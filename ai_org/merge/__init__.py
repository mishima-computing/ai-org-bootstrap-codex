"""merge — the integration boundary (subsystem maintainer, then mainline/Linus).

NOTE on git CONFLICTS (where they happen + how to handle): conflicts arise ONLY here, in the merge
stages, never in patch/implement. A Contributor works in an isolated worktree on its own branch off
HEAD, so it never merges and never conflicts. Conflicts appear when INDEPENDENT contributions touched
OVERLAPPING content and we integrate them into a shared tree:
  - subsystem.py : merging a 2nd overlapping contribution into ai-org/subsystem (1st already in)
  - mainline.py  : merging subsystem into ai-org/mainline when mainline moved underneath
Disjoint scopes (e.g. adding a new file) do NOT conflict. This mirrors Linux: conflicts surface at the
maintainer's/Linus's integration, not at the contributor's desk.

Handling (Linux "rebase and resend"): on a merge conflict, `git merge --abort` (leave NO half-merged
state), then REJECT and send the contribution back to the Contributor to redo on the updated base.

STATUS (be honest): subsystem.py / mainline.py currently do `git merge --no-ff` but do NOT yet handle
the conflict case (abort + reject + send-back). TODO: implement that.

Concurrency note (separate from conflicts): no self-written locks needed — git is the arbiter. A branch
can be checked out in only ONE worktree, so concurrent merges into the same shared branch serialize
naturally; just handle git's "already checked out" / "branch exists" failures gracefully (back off/retry).
"""
