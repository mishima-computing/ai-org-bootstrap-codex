"""merge — the integration boundary (subsystem maintainer, then mainline/Linus).

NOTE on git CONFLICTS (where they happen + how to handle): conflicts arise ONLY here, in the merge
stages, never in patch/implement. A Contributor works in an isolated worktree on its own branch off
HEAD, so it never merges and never conflicts. Conflicts appear when INDEPENDENT contributions touched
OVERLAPPING content and we integrate them into a shared tree:
  - subsystem.py : merging a 2nd overlapping contribution into ai-org/subsystem (1st already in)
  - mainline.py  : merging subsystem into ai-org/mainline when mainline moved underneath
Disjoint scopes (e.g. adding a new file) do NOT conflict. This mirrors Linux: conflicts surface at the
maintainer's/Linus's integration, not at the contributor's desk.

Handling — it's a WORKTREE matter: the merge runs in a throwaway worktree, so on conflict we just
DISCARD the worktree. No `git merge --abort` is even needed: the merge commit is never made, the target
branch ref (ai-org/subsystem / ai-org/mainline) is never moved, and review_and_integrate's `finally`
force-removes the merge worktree — so the conflicted state vanishes and nothing is polluted. The stage
then returns reject ("git merge failed").

STATUS: conflict is handled cleanly THIS way (PROVEN: induced a real conflict -> reject, target ref
unchanged, zero leftover worktrees). What is NOT yet built is the "rebase and resend" LOOP: a conflicting
contribution stays accepted-but-unmerged, so merge.pull keeps re-selecting and re-rejecting it (no damage,
but no progress) instead of routing it back to the Contributor to rebase on the new base. TODO: that loop.

Concurrency note (separate from conflicts): no self-written locks needed — git is the arbiter. A branch
can be checked out in only ONE worktree, so concurrent merges into the same shared branch serialize
naturally; just handle git's "already checked out" / "branch exists" failures gracefully (back off/retry).
"""
from __future__ import annotations

from ai_org import git_wrapper
from ai_org.merge import mainline, subsystem


CONTRIB_PREFIX = "ai-org/contrib/"
SUBSYSTEM_BRANCH = "ai-org/subsystem"
MAINLINE_BRANCH = "ai-org/mainline"

# Memento: Log V1 leaves merge.subsystem.* and merge.mainline.* as follow-up
# wiring. The authoritative history boundary is ready in ai_org.log; add events
# here when merge projections exist.


def pull(repo):
    """Integrate one accepted contribution or one subsystem tree, if pending."""
    for branch in sorted(git_wrapper.branches(repo, f"{CONTRIB_PREFIX}*")):
        if not git_wrapper.has_subject(repo, branch, "acceptance: reachable"):
            continue
        if git_wrapper.is_ancestor(repo, branch, SUBSYSTEM_BRANCH):
            continue
        return subsystem.review_and_integrate(repo, branch)

    if git_wrapper.branch_exists(repo, SUBSYSTEM_BRANCH) and not git_wrapper.is_ancestor(
        repo, SUBSYSTEM_BRANCH, MAINLINE_BRANCH
    ):
        return mainline.review_and_integrate(repo)
    return None
