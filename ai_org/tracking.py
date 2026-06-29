# tracking.py — the ONE sanctioned "super-module".
# Normally we forbid super-modules (a single thing everyone is forced through), because that became a
# state-exploding crossing layer (the deleted platform/). This module is the deliberate EXCEPTION, and only
# because git ITSELF is already a super-module: git is the single source of truth / the super-state. This module
# is a READ-ONLY LENS that DERIVES per-RFC status from git and STORES NOTHING — no status field, no enum, no DB,
# no parallel ledger. Git is the status; this is just how we read it. (PROVEN: codex --sandbox cannot write .git,
# so all writes happen in the python stages; this module only READS.)
#
# id convention: every artifact of an RFC is named by its slug <id>:
#   ai-org/rfc/<id>      branch  -> the RFC proposal (rfc.json) + its review history
#   ai-org/contrib/<id>  branch  -> the patch (implemented code) + acceptance commit
#   ai-org/subsystem , ai-org/mainline  -> integration trees (membership = progress)
# git fact -> stage (derived, in order, furthest wins):
#   ai-org/rfc/<id> exists                                        -> "proposed"
#   ...its log has "rfc: direction-ok"                            -> "direction-ok"
#   ...its log has "rfc: nak"                                     -> "rejected"
#   ai-org/contrib/<id> exists                                    -> "patch-in-progress"
#   ...contrib log has "acceptance: reachable"                    -> "accepted"
#   ai-org/subsystem contains contrib tip (is-ancestor)          -> "in-subsystem"
#   ai-org/mainline contains contrib tip (is-ancestor)           -> "merged"
from __future__ import annotations

from pathlib import Path
import subprocess


RFC_PREFIX = "ai-org/rfc/"
CONTRIB_PREFIX = "ai-org/contrib/"
SUBSYSTEM_BRANCH = "ai-org/subsystem"
MAINLINE_BRANCH = "ai-org/mainline"


def list_rfcs(repo) -> list[str]:
    """Return RFC ids from local ai-org/rfc/* branches."""
    branches = _branch_list(Path(repo), f"{RFC_PREFIX}*")
    return sorted(branch.removeprefix(RFC_PREFIX) for branch in branches if branch.startswith(RFC_PREFIX))


def stage_of(repo, id) -> str:
    """Derive the furthest known stage for an RFC id from git facts only."""
    repo = Path(repo)
    rfc_branch = f"{RFC_PREFIX}{id}"
    contrib_branch = f"{CONTRIB_PREFIX}{id}"
    stage = "none"

    if _branch_exists(repo, rfc_branch):
        stage = "proposed"
        rfc_subjects = _log_subjects(repo, rfc_branch)
        if _has_subject(rfc_subjects, "rfc: direction-ok"):
            stage = "direction-ok"
        if _has_subject(rfc_subjects, "rfc: nak"):
            stage = "rejected"

    if _branch_exists(repo, contrib_branch):
        stage = "patch-in-progress"
        contrib_subjects = _log_subjects(repo, contrib_branch)
        if _has_subject(contrib_subjects, "acceptance: reachable"):
            stage = "accepted"
        if _contains(repo, SUBSYSTEM_BRANCH, contrib_branch):
            stage = "in-subsystem"
        if _contains(repo, MAINLINE_BRANCH, contrib_branch):
            stage = "merged"

    return stage


def next_action(repo, id) -> str:
    """Return the next stage runner implied by the current git-derived stage."""
    return {
        "none": "none",
        "proposed": "review",
        "direction-ok": "make",
        "rejected": "none",
        "patch-in-progress": "make",
        "accepted": "subsystem",
        "in-subsystem": "mainline",
        "merged": "done",
    }[stage_of(repo, id)]


def _branch_list(repo: Path, pattern: str) -> list[str]:
    result = _git(repo, "branch", "--list", pattern)
    if result.returncode != 0:
        return []
    branches = []
    for line in result.stdout.splitlines():
        branch = line.strip()
        if branch.startswith("* "):
            branch = branch[2:].strip()
        if branch:
            branches.append(branch)
    return branches


def _branch_exists(repo: Path, branch: str) -> bool:
    return branch in _branch_list(repo, branch)


def _log_subjects(repo: Path, branch: str) -> list[str]:
    result = _git(repo, "log", branch, "--format=%s")
    if result.returncode != 0:
        return []
    return result.stdout.splitlines()


def _has_subject(subjects: list[str], needle: str) -> bool:
    return any(needle in subject for subject in subjects)


def _contains(repo: Path, branch: str, contrib_branch: str) -> bool:
    if not _branch_exists(repo, branch):
        return False
    result = _git(repo, "merge-base", "--is-ancestor", contrib_branch, branch)
    return result.returncode == 0


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
