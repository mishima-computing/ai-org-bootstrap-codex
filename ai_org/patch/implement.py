"""Patch/implement stage.

This stage is intentionally shaped as git-read -> codex -> git-write.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile


RFC_FIELDS = ("title", "problem", "proposed_change", "interface_sketch", "notes")


def run(repo, rfc_path: str = "rfc.json", branch: str | None = None) -> dict:
    """Implement the RFC committed at HEAD and return contribution branch metadata."""
    repo_path = Path(repo).resolve()
    rfc = _read_rfc_from_head(repo_path, rfc_path)
    if not rfc["ok"]:
        return rfc

    rfc_data = rfc["rfc"]
    branch_name = branch or f"ai-org/contrib/{_slug(rfc_data['title'])}"
    if _branch_exists(repo_path, branch_name):
        return _failure(f"branch already exists: {branch_name}", branch=branch_name)

    temp_dir = Path(tempfile.mkdtemp(prefix="ai-org-patch-"))
    worktree = temp_dir / "worktree"
    out_file = temp_dir / "codex-output.txt"
    try:
        # git is done in python; codex --sandbox workspace-write edits the worktree but
        # CANNOT write .git (PROVEN: .git/index.lock permission denied).
        worktree_result = _git_run(
            repo_path,
            "worktree",
            "add",
            "-b",
            branch_name,
            str(worktree),
            "HEAD",
        )
        if worktree_result.returncode != 0:
            return _failure(
                "git worktree add failed",
                branch=branch_name,
                stderr=worktree_result.stderr,
                stdout=worktree_result.stdout,
            )

        prompt = _prompt(rfc_data)
        cmd = [
            "codex",
            "exec",
            "--sandbox",
            "workspace-write",
            "-C",
            str(worktree),
            "-o",
            str(out_file),
            prompt,
        ]
        completed = subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
        )
        # codex can exit non-zero / write no -o file -> check returncode AND out_file
        # existence before reading (fail closed).
        if completed.returncode != 0 or not out_file.exists():
            return _failure(
                "codex implementation failed",
                branch=branch_name,
                returncode=completed.returncode,
                stderr=completed.stderr,
                stdout=completed.stdout,
                output_exists=out_file.exists(),
            )

        status = _git_run(worktree, "status", "--porcelain", "-uall")
        if status.returncode != 0:
            return _failure(
                "git status failed after codex",
                branch=branch_name,
                stderr=status.stderr,
                stdout=status.stdout,
            )
        if not status.stdout.strip():
            return _failure("codex produced no edits", branch=branch_name)

        add = _git_run(worktree, "add", "-A")
        if add.returncode != 0:
            return _failure(
                "git add failed",
                branch=branch_name,
                stderr=add.stderr,
                stdout=add.stdout,
            )

        commit = _git_run(worktree, "commit", "-m", f"patch: {rfc_data['title']}")
        if commit.returncode != 0:
            return _failure(
                "git commit failed",
                branch=branch_name,
                stderr=commit.stderr,
                stdout=commit.stdout,
            )

        sha = _git(worktree, "rev-parse", "HEAD").strip()
        return {
            "ok": True,
            "branch": branch_name,
            "commit": sha,
        }
    finally:
        if worktree.exists():
            _git_run(repo_path, "worktree", "remove", "--force", str(worktree))
        shutil.rmtree(temp_dir, ignore_errors=True)


def _read_rfc_from_head(repo: Path, rfc_path: str) -> dict:
    result = _git_run(repo, "show", f"HEAD:{rfc_path}")
    if result.returncode != 0:
        return _failure(
            f"{rfc_path} missing at HEAD",
            stderr=result.stderr,
            stdout=result.stdout,
        )

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return _failure(f"{rfc_path} at HEAD is not parseable JSON: {exc}")

    missing = [field for field in RFC_FIELDS if field not in data]
    if missing:
        return _failure(f"{rfc_path} at HEAD is missing required fields: {', '.join(missing)}")

    return {"ok": True, "rfc": {field: str(data[field]) for field in RFC_FIELDS}}


def _prompt(rfc: dict) -> str:
    return (
        "Implement the RFC in this repository.\n"
        "Edit the working tree only. Do not commit.\n\n"
        f"title:\n{rfc['title']}\n\n"
        f"problem:\n{rfc['problem']}\n\n"
        f"proposed_change:\n{rfc['proposed_change']}\n\n"
        f"interface_sketch:\n{rfc['interface_sketch']}\n\n"
        f"notes:\n{rfc['notes']}\n"
    )


def _slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._/-]+", "-", value.strip().lower()).strip("-/")
    return slug[:80] or "patch"


def _branch_exists(repo: Path, branch: str) -> bool:
    result = _git_run(repo, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}")
    return result.returncode == 0


def _git(repo: Path, *args: str) -> str:
    result = _git_run(repo, *args)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "git command failed")
    return result.stdout


def _git_run(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _failure(message: str, **extra) -> dict:
    result = {"ok": False, "error": message}
    result.update({key: value for key, value in extra.items() if value not in (None, "")})
    return result
