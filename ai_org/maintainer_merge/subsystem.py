"""Subsystem-tree maintainer stage: git-read -> Codex judgment -> git-write."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Any

from ai_org.schema_lifecycle import generated_schema_attr
from ai_org import git_wrapper
import ai_org.maintainer_merge.evidence as evidence
SUBSYSTEM_BRANCH = "ai-org/subsystem"

# Proven Codex --output-schema constraints: no allOf/anyOf/oneOf/if-then,
# additionalProperties must be false, and required must list every property.
def build_verdict_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["accept", "reasons"],
        "properties": {
            "accept": {"type": "boolean"},
            "reasons": {"type": "array", "items": {"type": "string"}},
        },
    }


_SCHEMA_BUILDERS = {
    "_VERDICT": build_verdict_schema,
}
_CODEX_OUTPUT_SCHEMA_BUILDERS = dict(_SCHEMA_BUILDERS)


def __getattr__(name: str) -> Any:
    return generated_schema_attr(name, _SCHEMA_BUILDERS)

def review_and_integrate(
    repo: str | Path,
    branch: str,
    subsystem: str = SUBSYSTEM_BRANCH,
    base: str | None = None,
) -> dict[str, Any]:
    """Review a contribution branch and merge it into the subsystem tree on accept."""
    repo = Path(repo)
    base = base or _default_branch(repo)
    subsystem_ref = f"refs/heads/{subsystem}"
    try:
        migrated = evidence.is_migrated_tree(repo, branch)
    except evidence.EvidenceError as exc:
        return _reject(str(exc))
    candidate: evidence.SealedContribution | None = None
    if migrated:
        try:
            candidate = evidence.sealed_contribution(repo, branch)
        except evidence.EvidenceError as exc:
            return _reject(str(exc))
    expected_subsystem_oid = git_wrapper.head_sha(repo, subsystem_ref) or ""
    base_oid = git_wrapper.head_sha(repo, base) or ""
    if candidate is not None and not base_oid:
        return _reject("could not resolve subsystem integration base")

    temp_dir = Path(tempfile.mkdtemp(prefix="ai-org-subsystem-"))
    read_worktree = temp_dir / "contribution"
    try:
        read_ref = candidate.implementation_oid if candidate is not None else branch
        if not _add_read_worktree(repo, read_worktree, read_ref):
            return _reject("could not add contribution worktree")

        diff = (
            _code_diff(repo, candidate)
            if candidate is not None
            else _diff(repo, base, branch)
        )
        verdict = _codex_verdict(read_worktree, branch, base, diff, temp_dir)
        if not verdict["accept"]:
            return {"accept": False, "ref": None, "reasons": verdict["reasons"]}

        if candidate is not None:
            publication = _integrate_sealed_contribution(
                repo,
                candidate,
                subsystem,
                base,
                base_oid,
                expected_subsystem_oid,
                temp_dir / "subsystem",
            )
            integrated = publication is not None and publication.ok
        else:
            publication = None
            integrated = _merge_contribution(
                repo, branch, subsystem, base, temp_dir / "subsystem"
            )
        if not integrated:
            if publication is not None and publication.failure is not None:
                return {
                    **_reject(str(publication.failure)),
                    "expected_ref_oids": dict(publication.expected_ref_oids),
                    "resulting_ref_oids": dict(publication.resulting_ref_oids),
                }
            return _reject("git merge failed")
        result = {
            "accept": True,
            "ref": subsystem_ref,
            "reasons": verdict["reasons"],
        }
        if publication is not None:
            result.update(
                expected_ref_oids=dict(publication.expected_ref_oids),
                resulting_ref_oids=dict(publication.resulting_ref_oids),
            )
        return result
    finally:
        _remove_worktree(repo, read_worktree)
        _remove_worktree(repo, temp_dir / "subsystem")
        shutil.rmtree(temp_dir, ignore_errors=True)


def _add_read_worktree(repo: Path, worktree: Path, branch: str) -> bool:
    # Git read stage: Codex inspects a detached, read-only contribution worktree.
    result = subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", "--detach", str(worktree), branch],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return result.returncode == 0


def _codex_verdict(
    worktree: Path,
    branch: str,
    base: str,
    diff: str,
    temp_dir: Path,
) -> dict[str, Any]:
    schema = temp_dir / "verdict.schema.json"
    out_file = temp_dir / "verdict.json"
    schema.write_text(json.dumps(build_verdict_schema()), encoding="utf-8")

    prompt = (
        "You are the subsystem-tree maintainer. Decide whether to accept this contribution "
        "into the subsystem tree.\n"
        f"Contribution branch: {branch}\n"
        f"Base branch: {base}\n"
        "Judge code fit, quality, maintainability, and whether it avoids breaking userspace. "
        "Inspect the worktree and the diff. Return only the schema-shaped JSON verdict.\n\n"
        f"Diff versus base:\n{diff}"
    )
    cmd = [
        "codex",
        "exec",
        "--sandbox",
        "read-only",
        "-C",
        str(worktree),
        "-o",
        str(out_file),
        "--output-schema",
        str(schema),
        prompt,
    ]
    completed = subprocess.run(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    # Proven Codex behavior: it may exit nonzero or write no -o file. Check both
    # before reading and fail closed on invalid JSON or schema shape.
    if completed.returncode != 0 or not out_file.exists():
        return _reject("codex did not produce a valid verdict")

    try:
        verdict = json.loads(out_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return _reject("codex verdict was not valid JSON")

    if not _valid_verdict(verdict):
        return _reject("codex verdict did not match schema")
    return verdict


def _valid_verdict(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == {"accept", "reasons"}
        and isinstance(value["accept"], bool)
        and isinstance(value["reasons"], list)
        and all(isinstance(reason, str) for reason in value["reasons"])
    )


def _merge_contribution(repo: Path, branch: str, subsystem: str, base: str, worktree: Path) -> bool:
    subsystem_ref = f"refs/heads/{subsystem}"
    if not _ref_exists(repo, subsystem_ref):
        if not _git_ok(repo, "branch", subsystem, base):
            return False

    result = subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", str(worktree), subsystem],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode != 0:
        return False

    # Git write stage: Python performs the real merge and records state in git.
    # Merge commits are engine-authored; explicit engine identity, never ambient config.
    result = subprocess.run(
        [
            "git",
            "-C",
            str(worktree),
            *git_wrapper.identity_config_args(),
            "merge",
            "--no-ff",
            "-m",
            f"subsystem: merge {branch}",
            branch,
        ],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return result.returncode == 0


def _integrate_sealed_contribution(
    repo: Path,
    candidate: evidence.SealedContribution,
    subsystem: str,
    base: str,
    base_oid: str,
    expected_subsystem_oid: str,
    worktree: Path,
) -> git_wrapper.GitPublicationResult | None:
    """Prepare the sealed code-only tree, then publish it through ref CAS."""

    subsystem_ref = f"refs/heads/{subsystem}"
    parent_oid = expected_subsystem_oid or base_oid
    added = subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "worktree",
            "add",
            "--detach",
            str(worktree),
            parent_oid,
        ],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if added.returncode != 0:
        return None
    # An empty code projection is a valid result when the sealed implementation
    # changed only lifecycle or handoff records.  Never invoke ``git diff --``
    # with an empty pathspec: Git would widen that to the complete implementation
    # delta and defeat the exclusion policy.  The empty integration commit still
    # records the resolvable producer evidence links.
    already_applied = not candidate.code_paths
    if candidate.code_paths:
        patch = subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "diff",
                "--binary",
                "--full-index",
                candidate.binding_commit_oid,
                candidate.implementation_oid,
                "--",
                *candidate.code_paths,
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if patch.returncode != 0 or not patch.stdout:
            return None
        applicable = subprocess.run(
            ["git", "-C", str(worktree), "apply", "--index", "--3way", "--check"],
            input=patch.stdout,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if applicable.returncode != 0:
            reverse_check = subprocess.run(
                ["git", "-C", str(worktree), "apply", "--reverse", "--check"],
                input=patch.stdout,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            if reverse_check.returncode != 0:
                return None
            already_applied = True
        else:
            applied = subprocess.run(
                ["git", "-C", str(worktree), "apply", "--index", "--3way"],
                input=patch.stdout,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            if applied.returncode != 0:
                return None
    staged = subprocess.run(
        ["git", "-C", str(worktree), "diff", "--cached", "--name-only", "-z"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    staged_paths = [
        path.decode("utf-8") for path in staged.stdout.split(b"\0") if path
    ] if staged.returncode == 0 else []
    if not staged_paths and not already_applied:
        reverse_check = subprocess.run(
            ["git", "-C", str(worktree), "apply", "--reverse", "--check"],
            input=patch.stdout,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        already_applied = reverse_check.returncode == 0
    if (
        (not staged_paths and not already_applied)
        or any(evidence.is_excluded_path(path) for path in staged_paths)
    ):
        return None
    message = evidence.integration_message(
        evidence.SUBSYSTEM_INTEGRATION_SUBJECT, candidate.links
    )
    committed = subprocess.run(
        [
            "git",
            "-C",
            str(worktree),
            *git_wrapper.identity_config_args(),
            "commit",
            "--allow-empty",
            "-m",
            message,
        ],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if committed.returncode != 0:
        return None
    prepared = subprocess.run(
        ["git", "-C", str(worktree), "rev-parse", "HEAD"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    prepared_oid = prepared.stdout.strip() if prepared.returncode == 0 else ""
    if not prepared_oid:
        return None
    return git_wrapper.publish_prepared_integration_commit(
        repo,
        subsystem_ref,
        prepared_oid,
        expected_subsystem_oid,
        source_ref_oids={
            (
                "refs/heads/"
                + candidate.contribution_branch.removeprefix("refs/heads/")
            ): candidate.verdict_oid,
            **(
                {evidence.AUTHORITY_NOTES_REF: candidate.authority_notes_oid}
                if candidate.authority_notes_oid
                else {}
            ),
            **(
                {f"refs/heads/{base}": base_oid}
                if not expected_subsystem_oid
                and git_wrapper.branch_exists(repo, base)
                else {}
            ),
        },
    )


def _code_diff(repo: Path, candidate: evidence.SealedContribution) -> str:
    if not candidate.code_paths:
        return ""
    result = subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "diff",
            "--no-ext-diff",
            f"{candidate.binding_commit_oid}..{candidate.implementation_oid}",
            "--",
            *candidate.code_paths,
        ],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return result.stdout if result.returncode == 0 else ""


def _diff(repo: Path, base: str, branch: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), "diff", "--no-ext-diff", f"{base}..{branch}"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return result.stdout if result.returncode == 0 else ""


def _ref_exists(repo: Path, ref: str) -> bool:
    result = subprocess.run(
        ["git", "-C", str(repo), "show-ref", "--verify", "--quiet", ref],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return result.returncode == 0


def _git_ok(repo: Path, *args: str) -> bool:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return result.returncode == 0


def _default_branch(repo: Path) -> str:
    origin_head = subprocess.run(
        ["git", "-C", str(repo), "symbolic-ref", "--short", "refs/remotes/origin/HEAD"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if origin_head.returncode == 0:
        ref = origin_head.stdout.strip()
        if ref.startswith("origin/"):
            return ref

    current = subprocess.run(
        ["git", "-C", str(repo), "symbolic-ref", "--short", "HEAD"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if current.returncode == 0 and current.stdout.strip():
        return current.stdout.strip()

    raise RuntimeError("could not determine repository default branch")


def _remove_worktree(repo: Path, worktree: Path) -> None:
    if not worktree.exists():
        return
    subprocess.run(
        ["git", "-C", str(repo), "worktree", "remove", "--force", str(worktree)],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _reject(reason: str) -> dict[str, Any]:
    return {"accept": False, "ref": None, "reasons": [reason]}
