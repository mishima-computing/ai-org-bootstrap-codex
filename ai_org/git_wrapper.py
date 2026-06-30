"""The one sanctioned read-only git lens for shared repository state.

This super-module is allowed because git itself is the shared state: this
module stores nothing, decides nothing, and exposes only generic read
primitives. Public lens functions are branches, branch_exists, log_subjects,
has_subject, is_ancestor, and head_sha.
"""
from __future__ import annotations

from pathlib import Path
import subprocess


def branches(repo, pattern: str = "*") -> list[str]:
    """Return local branch names matching a git branch --list pattern."""
    result = _git(Path(repo), "branch", "--list", pattern, "--format=%(refname:short)")
    if result.returncode != 0:
        return []
    return [line for line in result.stdout.splitlines() if line]


def branch_exists(repo, name: str) -> bool:
    """Return whether a local branch exists."""
    result = _git(Path(repo), "show-ref", "--verify", "--quiet", f"refs/heads/{name}")
    return result.returncode == 0


def log_subjects(repo, ref: str) -> list[str]:
    """Return commit subjects reachable from ref, newest first."""
    result = _git(Path(repo), "log", ref, "--format=%s")
    if result.returncode != 0:
        return []
    return result.stdout.splitlines()


def has_subject(repo, ref: str, substring: str) -> bool:
    """Return whether any commit subject on ref contains substring."""
    return any(substring in subject for subject in log_subjects(repo, ref))


def is_ancestor(repo, a: str, b: str) -> bool:
    """Return whether commit/ref a is an ancestor of commit/ref b."""
    result = _git(Path(repo), "merge-base", "--is-ancestor", a, b)
    return result.returncode == 0


def head_sha(repo, ref: str) -> str | None:
    """Return the full object id for ref, or None when ref is missing."""
    result = _git(Path(repo), "rev-parse", "--verify", f"{ref}^{{commit}}")
    if result.returncode != 0:
        return None
    sha = result.stdout.strip()
    return sha or None


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", "-C", str(repo), *args],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except OSError as exc:
        return subprocess.CompletedProcess(["git", "-C", str(repo), *args], 127, "", str(exc))
