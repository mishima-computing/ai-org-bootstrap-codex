"""Mainline maintainer stage: git-read -> Codex judgment -> git-write."""
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
MAINLINE_BRANCH = "ai-org/mainline"

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
    subsystem: str = SUBSYSTEM_BRANCH,
    mainline: str = MAINLINE_BRANCH,
    base: str | None = None,
) -> dict[str, Any]:
    """Review a subsystem branch and merge it into the mainline tree on accept."""
    repo = Path(repo)
    base = base or _default_branch(repo)
    mainline_ref = f"refs/heads/{mainline}"
    subsystem_oid = git_wrapper.head_sha(repo, subsystem) or ""
    expected_mainline_oid = git_wrapper.head_sha(repo, mainline_ref) or ""
    base_oid = git_wrapper.head_sha(repo, base) or ""
    authority_notes_oid = git_wrapper.head_sha(repo, evidence.AUTHORITY_NOTES_REF) or ""
    pending = _pending_subsystem_commits(
        repo, subsystem_oid or subsystem, expected_mainline_oid or base_oid
    )
    try:
        current_required = evidence.current_integration_required(
            repo,
            tuple(
                ref for ref in (subsystem_oid, expected_mainline_oid) if ref
            ),
        )
        links = evidence.verified_links(
            repo,
            pending,
            require_current=current_required,
            frozen_authority_notes_oid=authority_notes_oid,
        )
    except evidence.EvidenceError as exc:
        return _reject(str(exc))

    temp_dir = Path(tempfile.mkdtemp(prefix="ai-org-mainline-"))
    read_worktree = temp_dir / "subsystem"
    merge_worktree = temp_dir / "mainline"
    try:
        if not _add_read_worktree(repo, read_worktree, subsystem_oid or subsystem):
            return _reject("could not add subsystem worktree")

        diff = _diff(
            repo,
            expected_mainline_oid or base_oid or base,
            subsystem_oid or subsystem,
        )
        verdict = _codex_verdict(read_worktree, subsystem, base, diff, temp_dir)
        if not verdict["accept"]:
            return {"accept": False, "ref": None, "reasons": verdict["reasons"]}

        publication = _merge_subsystem(
            repo,
            subsystem,
            mainline,
            base,
            merge_worktree,
            links=links,
            subsystem_oid=subsystem_oid,
            expected_mainline_oid=expected_mainline_oid,
            base_oid=base_oid,
            authority_notes_oid=authority_notes_oid,
        )
        integrated = (
            publication.ok
            if isinstance(publication, git_wrapper.GitPublicationResult)
            else publication
        )
        if not integrated:
            if (
                isinstance(publication, git_wrapper.GitPublicationResult)
                and publication.failure is not None
            ):
                return {
                    **_reject(str(publication.failure)),
                    "expected_ref_oids": dict(publication.expected_ref_oids),
                    "resulting_ref_oids": dict(publication.resulting_ref_oids),
                }
            return _reject("git merge failed")
        result = {
            "accept": True,
            "ref": mainline_ref,
            "reasons": verdict["reasons"],
        }
        if isinstance(publication, git_wrapper.GitPublicationResult):
            result.update(
                expected_ref_oids=dict(publication.expected_ref_oids),
                resulting_ref_oids=dict(publication.resulting_ref_oids),
            )
        return result
    finally:
        _remove_worktree(repo, read_worktree)
        _remove_worktree(repo, merge_worktree)
        shutil.rmtree(temp_dir, ignore_errors=True)


def _add_read_worktree(repo: Path, worktree: Path, subsystem: str) -> bool:
    # Git read stage: Python exposes a detached subsystem worktree for Codex review.
    result = subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", "--detach", str(worktree), subsystem],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return result.returncode == 0


def _codex_verdict(
    worktree: Path,
    subsystem: str,
    base: str,
    diff: str,
    temp_dir: Path,
) -> dict[str, Any]:
    schema = temp_dir / "verdict.schema.json"
    out_file = temp_dir / "verdict.json"
    schema.write_text(json.dumps(build_verdict_schema()), encoding="utf-8")

    prompt = (
        "You are the mainline maintainer, the Linus role, doing the final review "
        "before mainline integration.\n"
        f"Subsystem branch: {subsystem}\n"
        f"Base branch: {base}\n"
        "Accept only if the subsystem tree is coherent with the whole project, "
        "has no regressions, and is ready to ship. Inspect the read-only worktree "
        "and the diff. Return only the schema-shaped JSON verdict.\n\n"
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


def _merge_subsystem(
    repo: Path,
    subsystem: str,
    mainline: str,
    base: str,
    worktree: Path,
    *,
    links: tuple[evidence.ProducerEvidenceLink, ...] = (),
    subsystem_oid: str = "",
    expected_mainline_oid: str = "",
    base_oid: str = "",
    authority_notes_oid: str = "",
) -> bool | git_wrapper.GitPublicationResult:
    mainline_ref = f"refs/heads/{mainline}"
    message = (
        evidence.integration_message(evidence.MAINLINE_INTEGRATION_SUBJECT, links)
        if links
        else f"mainline: merge {subsystem}"
    )
    if links:
        parent_oid = expected_mainline_oid or base_oid
        if not parent_oid or not subsystem_oid:
            return False
        result = subprocess.run(
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
            text=True,
        )
        if result.returncode != 0:
            return False
        result = subprocess.run(
            [
                "git",
                "-C",
                str(worktree),
                *git_wrapper.identity_config_args(),
                "merge",
                "--no-ff",
                "-m",
                message,
                subsystem_oid,
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if result.returncode != 0:
            return False
        prepared = subprocess.run(
            ["git", "-C", str(worktree), "rev-parse", "HEAD"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        prepared_oid = prepared.stdout.strip() if prepared.returncode == 0 else ""
        if not prepared_oid:
            return False
        return git_wrapper.publish_prepared_integration_commit(
            repo,
            mainline_ref,
            prepared_oid,
            expected_mainline_oid,
            source_ref_oids={
                f"refs/heads/{subsystem.removeprefix('refs/heads/')}": subsystem_oid,
                **(
                    {evidence.AUTHORITY_NOTES_REF: authority_notes_oid}
                    if authority_notes_oid
                    else {}
                ),
                **(
                    {f"refs/heads/{base}": base_oid}
                    if not expected_mainline_oid
                    and git_wrapper.branch_exists(repo, base)
                    else {}
                ),
            },
        )

    if not _ref_exists(repo, mainline_ref):
        if not _git_ok(repo, "branch", mainline, base):
            return False

    result = subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", str(worktree), mainline],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode != 0:
        return False

    # Git write stage: Python performs the real merge and records state in git refs.
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
            message,
            subsystem,
        ],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return result.returncode == 0


def _pending_subsystem_commits(
    repo: Path, subsystem_ref: str, anchor_ref: str
) -> tuple[str, ...]:
    result = subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "rev-list",
            "--reverse",
            f"{anchor_ref}..{subsystem_ref}",
        ],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return tuple(result.stdout.splitlines()) if result.returncode == 0 else ()


def _diff(repo: Path, base: str, subsystem: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), "diff", "--no-ext-diff", f"{base}..{subsystem}"],
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
