"""Mainline maintainer stage: git-read -> Codex judgment -> git-write."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Any

SUBSYSTEM_BRANCH = "ai-org/subsystem"
MAINLINE_BRANCH = "ai-org/mainline"

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
    subsystem: str = SUBSYSTEM_BRANCH,
    mainline: str = MAINLINE_BRANCH,
    base: str = "master",
) -> dict[str, Any]:
    """Review a subsystem branch and merge it into the mainline tree on accept."""
    repo = Path(repo)
    mainline_ref = f"refs/heads/{mainline}"

    temp_dir = Path(tempfile.mkdtemp(prefix="ai-org-mainline-"))
    read_worktree = temp_dir / "subsystem"
    merge_worktree = temp_dir / "mainline"
    try:
        if not _add_read_worktree(repo, read_worktree, subsystem):
            return _reject("could not add subsystem worktree")

        diff = _diff(repo, base, subsystem)
        verdict = _codex_verdict(read_worktree, subsystem, base, diff, temp_dir)
        if not verdict["accept"]:
            return {"accept": False, "ref": None, "reasons": verdict["reasons"]}

        if not _merge_subsystem(repo, subsystem, mainline, base, merge_worktree):
            return _reject("git merge failed")
        return {"accept": True, "ref": mainline_ref, "reasons": verdict["reasons"]}
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
    schema.write_text(json.dumps(_VERDICT), encoding="utf-8")

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
) -> bool:
    mainline_ref = f"refs/heads/{mainline}"
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
    result = subprocess.run(
        [
            "git",
            "-C",
            str(worktree),
            "merge",
            "--no-ff",
            "-m",
            f"mainline: merge {subsystem}",
            subsystem,
        ],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return result.returncode == 0


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
