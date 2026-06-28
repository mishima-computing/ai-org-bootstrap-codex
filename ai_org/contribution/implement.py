"""Contributor implementation role.

The Contributor is the only writer. It runs Codex in an isolated git worktree,
on a contribution branch, through ``ai_org.carrier.run_codex``.
"""
from __future__ import annotations

import fnmatch
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from uuid import uuid4

from .. import carrier
from ..rfc.task import Task

SELF_CHECK_CAP = 3


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
    base = task.base_sha or _git(repo_path, "rev-parse", "HEAD").strip()

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
            _prompt(
                task,
                feedback=feedback,
                resume_session=resume_session,
                localization=_pre_localize(worktree, task),
            ),
            "workspace-write",
            out_file=out_file,
            resume_session=resume_session,
        )
        session_id = result.get("session_id") or resume_session
        failures = _deterministic_failures(worktree, task, base)
        if not failures:
            return {
                "branch": branch_ref,
                "session_id": session_id,
                "ok": True,
            }

        for _ in range(SELF_CHECK_CAP):
            if not session_id:
                break
            result = carrier.run_codex(
                worktree,
                _prompt(
                    task,
                    feedback=_self_check_feedback(failures),
                    resume_session=session_id,
                    localization=None,
                ),
                "workspace-write",
                out_file=out_file,
                resume_session=session_id,
            )
            session_id = result.get("session_id") or session_id
            failures = _deterministic_failures(worktree, task, base)
            if not failures:
                return {
                    "branch": branch_ref,
                    "session_id": session_id,
                    "ok": True,
                }

        return {
            "branch": branch_ref,
            "session_id": session_id,
            "ok": False,
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


def _prompt(
    task: Task,
    *,
    feedback: str | None,
    resume_session: str | None,
    localization: list[dict] | None,
) -> str:
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

    grounding = _localization_block(localization or [])
    return (
        "Implement this task and nothing else.\n\n"
        f"objective:\n{task.objective}\n\n"
        f"contract to satisfy:\n{task.contract}\n\n"
        "exact files/symbols you may touch:\n"
        f"{scope}\n\n"
        f"{grounding}\n\n"
        "Do not touch files or symbols outside that scope.\n"
        "Make focused, one-logical-change commits with good messages."
    )


def _deterministic_failures(worktree: Path, task: Task, base: str) -> list[dict]:
    failures: list[dict] = []
    deviations = _scope_deviations(_changed_files_since_base(worktree, base), task.scope)
    if deviations:
        failures.append(
            {
                "kind": "scope_deviation",
                "out_of_scope": deviations,
                "scope": list(task.scope),
            }
        )
    failures.extend(_run_checks(worktree, task.checks))
    return failures


def _run_checks(worktree: Path, checks: list[str]) -> list[dict]:
    failures = []
    for command in checks:
        result = subprocess.run(
            command,
            cwd=worktree,
            shell=True,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        if result.returncode != 0:
            failures.append(
                {
                    "command": command,
                    "returncode": result.returncode,
                    "output": result.stdout,
                }
            )
    return failures


def _self_check_feedback(failures: list[dict]) -> str:
    blocks = []
    for failure in failures:
        if failure.get("kind") == "scope_deviation":
            scope = failure.get("scope") or []
            scope_lines = (
                "\n".join(f"- {item}" for item in scope)
                if scope
                else "- (empty scope; deviation check skipped)"
            )
            files = "\n".join(f"- {item}" for item in failure.get("out_of_scope", []))
            blocks.append(
                "scope deviation:\n"
                "The implementation changed files outside task.scope. This is a hard deterministic failure.\n\n"
                "out-of-scope files:\n"
                f"{files}\n\n"
                "allowed task.scope:\n"
                f"{scope_lines}\n\n"
                "Revise in this same session. Keep changes within task.scope only and revert the "
                "out-of-scope edits before stopping."
            )
            continue
        output = failure["output"].rstrip() or "(no output)"
        blocks.append(
            "command:\n"
            f"{failure['command']}\n"
            f"exit code: {failure['returncode']}\n"
            "combined output:\n"
            f"{output}"
        )
    return (
        "Deterministic self-check failed in the contribution worktree. "
        "Fix only the failures below, then stop.\n\n"
        + "\n\n---\n\n".join(blocks)
    )


def _changed_files_since_base(worktree: Path, base: str) -> list[str]:
    files = set()
    diff = subprocess.run(
        ["git", "-C", str(worktree), "diff", "--name-only", f"{base}..HEAD"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if diff.returncode == 0:
        files.update(line.strip() for line in diff.stdout.splitlines() if line.strip())

    status = subprocess.run(
        ["git", "-C", str(worktree), "status", "--porcelain", "-uall"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if status.returncode == 0:
        files.update(_status_paths(status.stdout))

    return sorted(path for path in files if not _is_scratch_path(path))


def _status_paths(porcelain: str) -> list[str]:
    files = []
    for line in porcelain.splitlines():
        if not line.strip():
            continue
        rel = line[3:].strip()
        if " -> " in rel:
            files.extend(part.strip() for part in rel.split(" -> ", 1))
        elif rel:
            files.append(rel)
    return files


def _scope_deviations(changed: list[str], scope: list) -> list[str]:
    if not scope:
        return []
    return [path for path in changed if not _path_in_scope(path, scope)]


def _path_in_scope(path: str, scope: list) -> bool:
    rel = _safe_rel(path)
    if rel is None:
        return False
    for raw in scope:
        pattern = _safe_rel(str(raw).strip())
        if pattern is None:
            continue
        if _has_glob(pattern):
            if fnmatch.fnmatchcase(rel, pattern):
                return True
            continue
        if rel == pattern:
            return True
    return False


def _safe_rel(path: str) -> str | None:
    rel = str(path or "").strip().replace("\\", "/")
    if not rel or rel.startswith("/") or any(part == ".." for part in rel.split("/")):
        return None
    return rel.lstrip("./")


def _has_glob(pattern: str) -> bool:
    return any(char in pattern for char in "*?[")


def _is_scratch_path(path: str) -> bool:
    return path == ".agent-runs" or path.startswith(".agent-runs/")


_STOPWORDS = {
    "add",
    "and",
    "are",
    "but",
    "for",
    "from",
    "into",
    "make",
    "the",
    "this",
    "that",
    "with",
}
_TOKEN_SPLIT_RE = re.compile(r"[^A-Za-z0-9]+")
_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def _pre_localize(worktree: Path, task: Task) -> list[dict]:
    # Pre-localization complements rfc/decompose: decompose decides task.scope (WHAT may be touched);
    # this deterministic grep grounds the Contributor in the relevant existing code so it understands
    # the area before editing. It is advisory context, never a write-scope expander.
    terms = _objective_terms(task.objective)
    if not terms:
        return []

    files = _candidate_existing_files(worktree, task.scope)
    matches = []
    for rel in files:
        path = worktree / rel
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        lower_rel = rel.lower()
        path_score = sum(1 for term in terms if term in lower_rel)
        file_matches = 0
        for lineno, line in enumerate(text.splitlines(), start=1):
            lower = line.lower()
            hits = [term for term in terms if term in lower]
            if not hits and not path_score:
                continue
            score = len(hits) * 10 + path_score
            matches.append(
                {
                    "path": rel,
                    "line": lineno,
                    "score": score,
                    "text": line.strip()[:160],
                    "terms": sorted(set(hits)),
                }
            )
            file_matches += 1
            if file_matches >= 3:
                break

    return sorted(matches, key=lambda item: (-item["score"], item["path"], item["line"]))[:12]


def _objective_terms(objective: str) -> list[str]:
    terms = set()
    for raw in _TOKEN_SPLIT_RE.split(objective or ""):
        for piece in _CAMEL_RE.split(raw):
            term = piece.lower()
            if len(term) >= 3 and term not in _STOPWORDS:
                terms.add(term)
    return sorted(terms)


def _candidate_existing_files(worktree: Path, scope: list) -> list[str]:
    tracked = _tracked_files(worktree)
    if not scope:
        return tracked
    scoped = [path for path in tracked if _path_matches_scope_for_context(path, scope)]
    return scoped or tracked


def _path_matches_scope_for_context(path: str, scope: list) -> bool:
    if _path_in_scope(path, scope):
        return True
    rel = _safe_rel(path)
    if rel is None:
        return False
    for raw in scope:
        pattern = _safe_rel(str(raw).strip())
        if pattern and not _has_glob(pattern) and rel.startswith(pattern.rstrip("/") + "/"):
            return True
    return False


def _tracked_files(worktree: Path) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(worktree), "ls-files"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode != 0:
        return []
    return sorted(path for path in result.stdout.splitlines() if path and not _is_scratch_path(path))


def _localization_block(localization: list[dict]) -> str:
    if not localization:
        return "relevant existing code:\n- (no deterministic grep hits found)"
    lines = [
        "relevant existing code (read for context before editing; advisory, not extra scope):"
    ]
    for item in localization:
        text = item["text"] or "(blank line)"
        lines.append(f"- {item['path']}:{item['line']} {text}")
    return "\n".join(lines)


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
