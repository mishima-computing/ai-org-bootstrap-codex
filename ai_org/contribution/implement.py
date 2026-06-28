"""Contributor implementation role.

The Contributor is the only writer. It runs Codex in an isolated git worktree,
on a contribution branch, through ``ai_org.carrier.run_codex``.
"""
from __future__ import annotations

from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from uuid import uuid4

from .. import carrier
from ..rfc.task import Task


def run(
    task: Task,
    *,
    feedback: str | None = None,
    resume_session: str | None = None,
    repo: str | Path | None = None,
    branch_ref: str | None = None,
) -> dict:
    """Write or revise one Task in its own worktree and return branch metadata."""
    repo_path = Path(repo or Path.cwd()).resolve()
    branch_name = _branch_name(task, branch_ref, resume_session, repo_path)
    branch_ref = f"refs/heads/{branch_name}"
    base = task.base_sha or "HEAD"

    temp_dir = Path(tempfile.mkdtemp(prefix="ai-org-contrib-"))
    worktree = temp_dir / "worktree"
    try:
        if _branch_exists(repo_path, branch_ref):
            _git(repo_path, "worktree", "add", str(worktree), branch_name)
        else:
            _git(repo_path, "worktree", "add", "-b", branch_name, str(worktree), base)

        out_file = temp_dir / "codex-last-message.txt"
        result = carrier.run_codex(
            worktree,
            _prompt(task, feedback=feedback, resume_session=resume_session),
            "workspace-write",
            out_file=out_file,
            resume_session=resume_session,
        )
        return {
            "branch": branch_ref,
            "session_id": result.get("session_id"),
            "ok": bool(result.get("ok")),
        }
    finally:
        if worktree.exists():
            subprocess.run(
                ["git", "-C", str(repo_path), "worktree", "remove", "--force", str(worktree)],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        shutil.rmtree(temp_dir, ignore_errors=True)


def _prompt(task: Task, *, feedback: str | None, resume_session: str | None) -> str:
    scope = "\n".join(f"- {item}" for item in task.scope) if task.scope else "- (no scope listed)"
    if resume_session:
        delta = feedback or "Acceptance failed without additional detail. Inspect the branch and fix the gap."
        return (
            "Revise the existing implementation in this same Codex session.\n"
            "Use only this feedback delta; do not restart or re-derive the original task.\n\n"
            f"feedback:\n{delta}\n\n"
            "Continue to respect the original exact allowed files/symbols scope.\n"
            "Make focused, one-logical-change commits with good messages."
        )

    return (
        "Implement this task and nothing else.\n\n"
        f"objective:\n{task.objective}\n\n"
        f"contract to satisfy:\n{task.contract}\n\n"
        "exact files/symbols you may touch:\n"
        f"{scope}\n\n"
        "Do not touch files or symbols outside that scope.\n"
        "Make focused, one-logical-change commits with good messages."
    )


def _branch_name(
    task: Task,
    branch_ref: str | None,
    resume_session: str | None,
    repo: Path,
) -> str:
    if branch_ref:
        return _short_branch_name(branch_ref)

    base_name = f"contrib/{_slug(task.id) or uuid4().hex}"
    base_ref = f"refs/heads/{base_name}"
    if resume_session or not _branch_exists(repo, base_ref):
        return base_name
    return f"{base_name}-{uuid4().hex[:8]}"


def _short_branch_name(ref: str) -> str:
    return ref.removeprefix("refs/heads/")


def _slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._/-]+", "-", value.strip()).strip("-/")
    return slug[:80]


def _branch_exists(repo: Path, branch_ref: str) -> bool:
    result = subprocess.run(
        ["git", "-C", str(repo), "show-ref", "--verify", "--quiet", branch_ref],
        check=False,
    )
    return result.returncode == 0


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return result.stdout
