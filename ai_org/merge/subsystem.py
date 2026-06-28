"""Subsystem-tree maintainer stage: git-read -> Codex judgment -> git-write."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Any

SUBSYSTEM_BRANCH = "ai-org/subsystem"

# Proven Codex --output-schema constraints: no allOf/anyOf/oneOf/if-then,
# additionalProperties must be false, and required must list every property.
_VERDICT = {
    "type": "object",
    "additionalProperties": False,
    "required": ["accept", "reasons"],
    "properties": {
        "accept": {"type": "boolean"},
        "reasons": {"type": "array", "items": {"type": "string"}},
    },
}


def review_and_integrate(
    repo: str | Path,
    branch: str,
    subsystem: str = SUBSYSTEM_BRANCH,
    base: str = "master",
) -> dict[str, Any]:
    """Review a contribution branch and merge it into the subsystem tree on accept."""
    repo = Path(repo)
    subsystem_ref = f"refs/heads/{subsystem}"

    temp_dir = Path(tempfile.mkdtemp(prefix="ai-org-subsystem-"))
    read_worktree = temp_dir / "contribution"
    try:
        if not _add_read_worktree(repo, read_worktree, branch):
            return _reject("could not add contribution worktree")

        diff = _diff(repo, base, branch)
        verdict = _codex_verdict(read_worktree, branch, base, diff, temp_dir)
        if not verdict["accept"]:
            return {"accept": False, "ref": None, "reasons": verdict["reasons"]}

        if not _merge_contribution(repo, branch, subsystem, base, temp_dir / "subsystem"):
            return _reject("git merge failed")
        return {"accept": True, "ref": subsystem_ref, "reasons": verdict["reasons"]}
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
    schema.write_text(json.dumps(_VERDICT), encoding="utf-8")

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
    result = subprocess.run(
        [
            "git",
            "-C",
            str(worktree),
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
