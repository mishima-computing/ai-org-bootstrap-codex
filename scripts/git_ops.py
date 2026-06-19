#!/usr/bin/env python3
"""Git-state procedures for the autonomous builder — the ONE place the per-leaf-commit git operations
live, so their guards are written correctly once and called everywhere instead of being re-implemented
inline at each site (where reviewers kept finding the same class of bug independently).

Every guard a leaf commit needs is here, once:
  * `-uall` so an untracked directory collapsed to `?? dir/` is expanded to individual files (else the
    files are neither merged back nor committed);
  * LITERAL pathspecs (`:(literal)<path>`) so a name with glob metacharacters (`pages/[id].tsx`) can't
    match an unrelated dirty `pages/i.tsx`;
  * scratch exclusion (`.agent-runs/`, `result.json`);
  * staging ONLY this leaf's files (never `git add -A`, which sweeps unrelated worktree changes);
  * a fallback git identity for fresh/CI repos;
  * add/commit-failure handling: the commit IS the handoff to dependent leaves (the next worktree is cut
    from HEAD), so a failure must ROLL the leaf paths back to HEAD and fail, never silently "converge".
"""
from __future__ import annotations

import subprocess
from pathlib import Path

_SCRATCH = (".agent-runs",)          # path prefixes that are never part of a deliverable
_SCRATCH_EXACT = ("result.json",)


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)


def _is_scratch(rel: str) -> bool:
    return (not rel) or rel in _SCRATCH_EXACT or any(rel == p or rel.startswith(p + "/") for p in _SCRATCH)


def leaf_changed_files(worktree) -> list[str]:
    """Files a leaf worktree changed, scratch excluded. `-uall` expands untracked directories to
    individual files (a collapsed `?? dir/` would otherwise hide — and fail to copy/stage — its files)."""
    out = _git(worktree, "status", "--porcelain", "-uall").stdout
    files: list[str] = []
    for line in out.splitlines():
        rel = line[3:].strip()
        if rel and not _is_scratch(rel):
            files.append(rel)
    return files


def ensure_identity(repo) -> None:
    """A fresh / CI repo may lack user.name/user.email, which makes `git commit` fail. Set a fallback
    (idempotent: only when unset) so the per-leaf commit handoff doesn't break on a bare repo."""
    for key, val in (("user.email", "ai-org@localhost"), ("user.name", "AI Org")):
        got = _git(repo, "config", key)
        if got.returncode != 0 or not got.stdout.strip():
            _git(repo, "config", key, val)


def _rollback(repo, rels: list[str]) -> None:
    """Undo copied+staged leaf paths back to HEAD — per file, since one new path (absent at HEAD) would
    otherwise fail a combined checkout and strand the tracked ones."""
    specs = [f":(literal){r}" for r in rels]
    _git(repo, "reset", "-q", "HEAD", "--", *specs)
    for rel in rels:
        if _git(repo, "cat-file", "-e", f"HEAD:{rel}").returncode == 0:
            _git(repo, "checkout", "-q", "HEAD", "--", f":(literal){rel}")
        else:
            p = Path(repo) / rel
            if p.exists():
                p.unlink()


def merge_and_commit_leaf(goal_repo, leaf_worktree, leaf_id, objective):
    """Merge a converged leaf's files from its worktree into the goal worktree and commit them as ONE
    commit — the handoff to dependent leaves (the next leaf worktree is cut from the goal HEAD), so leaves
    accumulate as commits and the goal's single PR reads as a series of sub-task commits.

    Returns the new commit SHA (so the caller can flow it into the rich log) — or "" when there was nothing
    to commit, or None when the handoff FAILED (the leaf paths are rolled back to HEAD and the caller must
    fail the leaf, mechanical). Note: "" is success; only None is failure."""
    import shutil
    goal_repo, leaf_worktree = Path(goal_repo), Path(leaf_worktree)
    leaf_files = leaf_changed_files(leaf_worktree)
    for rel in leaf_files:                                  # merge the leaf's files into the goal worktree
        src, dst = leaf_worktree / rel, goal_repo / rel
        if src.is_file():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        elif not src.exists() and dst.is_file():
            dst.unlink()
    if not leaf_files:
        return ""
    specs = [f":(literal){r}" for r in leaf_files]
    if _git(goal_repo, "add", "--", *specs).returncode != 0:
        _rollback(goal_repo, leaf_files)
        return None
    if _git(goal_repo, "diff", "--cached", "--quiet", "--", *specs).returncode == 0:
        return ""                                          # nothing staged (already committed / no change)
    ensure_identity(goal_repo)
    obj = (objective or leaf_id or "leaf").strip()
    subject = obj.splitlines()[0][:72] if obj else "leaf"
    if _git(goal_repo, "commit", "-q", "-m", subject, "-m", f"leaf: {leaf_id}", "--", *specs).returncode != 0:
        _rollback(goal_repo, leaf_files)
        return None
    return _git(goal_repo, "rev-parse", "HEAD").stdout.strip()


def self_test() -> int:
    import tempfile
    def run(r, *a):
        return _git(r, *a)
    with tempfile.TemporaryDirectory() as d:
        repo = Path(d) / "r"; repo.mkdir()
        run(repo, "init", "-q", "-b", "main"); run(repo, "config", "user.email", "t@t"); run(repo, "config", "user.name", "t")
        (repo / "app.py").write_text("x=1\n"); run(repo, "add", "-A"); run(repo, "commit", "-q", "-m", "base")
        wt = Path(d) / "wt"; run(repo, "worktree", "add", "-q", "--detach", str(wt), "HEAD")
        # leaf creates an untracked DIRECTORY (porcelain collapses to `?? pages/`) + a metacharacter name
        (wt / "pages").mkdir(); (wt / "pages" / "[id].tsx").write_text("route\n"); (wt / "app.py").write_text("x=2\n")
        (repo / "pages").mkdir(exist_ok=True); (repo / "pages" / "i.tsx").write_text("UNRELATED dirty\n")  # glob bait
        sha = merge_and_commit_leaf(repo, wt, "leaf-a", "add route + edit app")
        assert sha and len(sha) == 40, ("leaf should commit and return its sha", sha)
        committed = run(repo, "show", "--name-only", "--format=", "HEAD").stdout.split()
        assert "pages/[id].tsx" in committed and "app.py" in committed, committed
        assert "pages/i.tsx" not in committed, ("glob swept unrelated", committed)        # literal pathspec held
        assert run(repo, "rev-list", "--count", "HEAD").stdout.strip() == "2", "one leaf commit on top of base"
        # commit-failure path: a failing pre-commit hook -> rollback + False, worktree clean of leaf work
        (Path(d) / "r" / ".git" / "hooks").mkdir(exist_ok=True)
        # (hooks live in the common dir; set a failing one)
        hook = Path(run(repo, "rev-parse", "--git-path", "hooks/pre-commit").stdout.strip())
        if not hook.is_absolute():
            hook = repo / hook
        hook.parent.mkdir(parents=True, exist_ok=True); hook.write_text("#!/bin/sh\nexit 1\n"); hook.chmod(0o755)
        (wt / "b.py").write_text("new\n")
        failed = merge_and_commit_leaf(repo, wt, "leaf-b", "add b")
        assert failed is None, "a failed commit must return None (fail the leaf)"
        assert not (repo / "b.py").exists(), "the failed leaf's new file must be rolled back"
        dirty = run(repo, "status", "--porcelain").stdout
        assert "b.py" not in dirty, ("failed leaf work must not linger", dirty)   # the i.tsx bait may remain
        print("git_ops self-test passed (dir expansion, literal pathspec, rollback on hook failure).")
    return 0


if __name__ == "__main__":
    raise SystemExit(self_test())
