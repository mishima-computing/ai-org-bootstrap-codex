"""Read-only git plumbing for vessel repos.

INVARIANT (Memento tattoo): only read commands are issued (`git log`,
`git show`, `git ls-tree`, `git rev-parse`, `git for-each-ref`). Nothing
here ever writes to a repository.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Optional

_FIELD_SEP = "\x1f"
_RECORD_SEP = "\x1e"


class GitReadError(RuntimeError):
    pass


def _git(repo: str, *args: str) -> str:
    """Run a read-only git command and return stdout as text."""
    proc = subprocess.run(
        ["git", "-C", repo, *args],
        capture_output=True,
        text=True,
        errors="replace",
    )
    if proc.returncode != 0:
        raise GitReadError(
            f"git {' '.join(args[:2])}... failed in {repo}: "
            + proc.stderr.strip()[:300]
        )
    return proc.stdout


def _git_bytes(repo: str, *args: str) -> Optional[bytes]:
    proc = subprocess.run(["git", "-C", repo, *args], capture_output=True)
    if proc.returncode != 0:
        return None
    return proc.stdout


@dataclass
class Commit:
    sha: str
    author_date: str  # ISO 8601 with offset
    committer_date: str  # ISO 8601 with offset
    subject: str


def branch_exists(repo: str, branch: str) -> bool:
    proc = subprocess.run(
        ["git", "-C", repo, "rev-parse", "--verify", "--quiet", branch],
        capture_output=True,
        text=True,
    )
    return proc.returncode == 0


def rev_parse_commit(repo: str, ref: str) -> Optional[str]:
    """Full commit sha of `ref`, or None when it is not a commit-ish."""
    proc = subprocess.run(
        ["git", "-C", repo, "rev-parse", "--verify", "--quiet",
         f"{ref}^{{commit}}"],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


def is_ancestor(repo: str, ancestor: str, descendant: str) -> bool:
    """True when `ancestor` is reachable from `descendant` (or equal).

    Read-only: `git merge-base --is-ancestor` only inspects the graph.
    """
    proc = subprocess.run(
        ["git", "-C", repo, "merge-base", "--is-ancestor",
         ancestor, descendant],
        capture_output=True,
        text=True,
    )
    return proc.returncode == 0


def log_for_path(repo: str, branch: str, path: str) -> list[Commit]:
    """Commits reachable from `branch` (any ref/sha) touching `path`,
    newest first."""
    fmt = _FIELD_SEP.join(["%H", "%aI", "%cI", "%s"]) + _RECORD_SEP
    out = _git(repo, "log", f"--format={fmt}", branch, "--", path)
    commits: list[Commit] = []
    for rec in out.split(_RECORD_SEP):
        rec = rec.strip("\n")
        if not rec:
            continue
        parts = rec.split(_FIELD_SEP)
        if len(parts) != 4:
            continue
        commits.append(Commit(*parts))
    return commits


def show_blob(repo: str, ref: str, path: str) -> Optional[str]:
    """Blob content of path at ref, or None when absent."""
    data = _git_bytes(repo, "show", f"{ref}:{path}")
    if data is None:
        return None
    return data.decode("utf-8", errors="replace")


def ls_tree(repo: str, ref: str, prefix: str) -> list[str]:
    """Recursive file listing under prefix at ref (sorted by git)."""
    try:
        out = _git(repo, "ls-tree", "-r", "--name-only", ref, "--", prefix)
    except GitReadError:
        return []
    return [line for line in out.splitlines() if line.strip()]

