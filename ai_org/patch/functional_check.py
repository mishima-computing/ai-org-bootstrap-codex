"""Patch acceptance: git-read -> codex judgment -> git-write verdict."""
from __future__ import annotations

import json
from pathlib import Path
import os
import shutil
import stat
import subprocess
import tempfile
from typing import Any


# PROVEN against codex v0.142.0 --output-schema:
# - no allOf / anyOf / oneOf / if-then
# - every object must set additionalProperties=false
# - required must list every key in properties; use empty strings/null unions for absent values
VERDICT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["reachable", "blockers", "notes"],
    "properties": {
        "reachable": {"type": "boolean"},
        "blockers": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["where", "why"],
                "properties": {
                    "where": {"type": "string"},
                    "why": {"type": "string"},
                },
            },
        },
        "notes": {"type": "string"},
    },
}


def check(repo, branch: str) -> dict:
    """Judge whether a real user can reach the RFC goal with the contribution branch."""
    repo_path = Path(repo).resolve()
    temp_dir = Path(tempfile.mkdtemp(prefix="ai-org-functional-check-"))
    worktree = temp_dir / "worktree"
    schema_file = temp_dir / "verdict.schema.json"
    out_file = temp_dir / "verdict.json"
    branch_ref = _branch_ref(branch)
    original_sha = ""

    try:
        original_sha = _git(repo_path, "rev-parse", branch).strip()
        # Git read is done by python. Codex receives only this detached checkout.
        _git(repo_path, "worktree", "add", "--detach", str(worktree), branch)
        _make_read_only(worktree)

        schema_file.write_text(json.dumps(VERDICT_SCHEMA, indent=2), encoding="utf-8")
        completed = _run_codex(worktree, _prompt(), out_file, schema_file)
        verdict = _read_verdict(completed, out_file)

        # Git write is done by python. The detached commit is then installed onto the branch.
        _make_writable(worktree)
        _commit_verdict(worktree, verdict)
        verdict_sha = _git(worktree, "rev-parse", "HEAD").strip()
        _git(repo_path, "update-ref", branch_ref, verdict_sha, original_sha)

        return verdict
    except Exception as exc:
        verdict = _verdict(
            reachable=False,
            blockers=[{"where": "functional_check", "why": f"acceptance failed: {exc}"}],
            notes="",
        )
        if original_sha and worktree.exists():
            try:
                _make_writable(worktree)
                _commit_verdict(worktree, verdict)
                verdict_sha = _git(worktree, "rev-parse", "HEAD").strip()
                _git(repo_path, "update-ref", branch_ref, verdict_sha, original_sha)
            except Exception:
                pass
        return verdict
    finally:
        if worktree.exists():
            _make_writable(worktree)
            _git_run(repo_path, "worktree", "remove", "--force", str(worktree))
        shutil.rmtree(temp_dir, ignore_errors=True)


def _run_codex(
    worktree: Path,
    prompt: str,
    out_file: Path,
    schema_file: Path,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "codex",
            "exec",
            "--sandbox",
            "read-only",
            "-C",
            str(worktree),
            "-o",
            str(out_file),
            "--output-schema",
            str(schema_file),
            prompt,
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
    )


def _prompt() -> str:
    return (
        "You are the patch acceptance judge for this contribution branch.\n"
        "Decide whether a real USER can reach the RFC goal with the code in this checkout.\n\n"
        "Hard rules:\n"
        "- Read files only; do not edit, commit, launch the app, run tests, install packages, or run build tools.\n"
        "- Infer the RFC goal from committed project/RFC/task files in this checkout.\n"
        "- Use the Mona two-agent walkthrough idea: USER keeps trying to reach the goal; APP answers only "
        "from source-grounded facts.\n"
        "- Passing tests alone is not enough. Reject false success where the code claims success but a real "
        "user cannot complete the goal.\n"
        "- If blocked, list concrete blockers with where as file:line when possible and why as the user-visible "
        "reason.\n\n"
        "Return only JSON matching the provided schema."
    )


def _read_verdict(completed: subprocess.CompletedProcess[str], out_file: Path) -> dict:
    # PROVEN: codex can exit non-zero or write no -o file. Check both before read_text; fail closed.
    if completed.returncode != 0:
        return _verdict(
            reachable=False,
            blockers=[{"where": "functional_check", "why": "codex acceptance judge exited non-zero"}],
            notes=(completed.stderr or completed.stdout or "").strip(),
        )
    if not out_file.exists():
        return _verdict(
            reachable=False,
            blockers=[{"where": "functional_check", "why": "codex acceptance judge wrote no output file"}],
            notes=(completed.stderr or completed.stdout or "").strip(),
        )

    raw = out_file.read_text(encoding="utf-8")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return _verdict(
            reachable=False,
            blockers=[{"where": "functional_check", "why": "codex acceptance judge returned invalid JSON"}],
            notes=raw,
        )
    return _normalize_verdict(parsed, raw)


def _normalize_verdict(parsed: Any, raw: str) -> dict:
    if not isinstance(parsed, dict):
        return _invalid_shape(raw)
    if set(parsed) != {"reachable", "blockers", "notes"}:
        return _invalid_shape(raw)
    if not isinstance(parsed["reachable"], bool):
        return _invalid_shape(raw)
    if not isinstance(parsed["blockers"], list) or not isinstance(parsed["notes"], str):
        return _invalid_shape(raw)

    blockers = []
    for item in parsed["blockers"]:
        if not isinstance(item, dict) or set(item) != {"where", "why"}:
            return _invalid_shape(raw)
        if not isinstance(item["where"], str) or not isinstance(item["why"], str):
            return _invalid_shape(raw)
        blockers.append({"where": item["where"], "why": item["why"]})

    return _verdict(reachable=parsed["reachable"], blockers=blockers, notes=parsed["notes"])


def _invalid_shape(raw: str) -> dict:
    return _verdict(
        reachable=False,
        blockers=[{"where": "functional_check", "why": "codex verdict did not match the schema"}],
        notes=raw,
    )


def _verdict(*, reachable: bool, blockers: list[dict], notes: str) -> dict:
    return {"ok": reachable, "reachable": reachable, "blockers": blockers, "notes": notes}


def _commit_verdict(worktree: Path, verdict: dict) -> None:
    if verdict["reachable"]:
        _git(worktree, "commit", "--allow-empty", "-m", "acceptance: reachable")
        return

    body = _blockers_body(verdict["blockers"])
    _git(worktree, "commit", "--allow-empty", "-m", "acceptance: blocked", "-m", body)


def _blockers_body(blockers: list[dict]) -> str:
    if not blockers:
        return "functional_check: acceptance blocked without a specific blocker"
    return "\n".join(f"{item['where']}: {item['why']}" for item in blockers)


def _branch_ref(branch: str) -> str:
    if branch.startswith("refs/"):
        return branch
    return f"refs/heads/{branch}"


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


def _make_read_only(path: Path) -> None:
    for root, dirs, files in os.walk(path):
        for name in files:
            _chmod_read_only(Path(root) / name)
        for name in dirs:
            _chmod_read_only(Path(root) / name)
    _chmod_read_only(path)


def _make_writable(path: Path) -> None:
    for root, dirs, files in os.walk(path):
        for name in dirs:
            _chmod_writable(Path(root) / name)
        for name in files:
            _chmod_writable(Path(root) / name)
    _chmod_writable(path)


def _chmod_read_only(path: Path) -> None:
    if path.is_symlink():
        return
    mode = path.stat().st_mode
    if path.is_dir():
        path.chmod((mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH) & ~0o222)
    else:
        path.chmod(mode & ~0o222)


def _chmod_writable(path: Path) -> None:
    if path.is_symlink():
        return
    path.chmod(path.stat().st_mode | stat.S_IWUSR)
