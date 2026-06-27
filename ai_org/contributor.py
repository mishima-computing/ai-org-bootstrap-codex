"""Contributor — implements ONE Task on its own branch and pushes it for review.

Mirrors a Linux contributor: branch off the base, do the work in isolation (own git worktree),
make focused one-logical-change commits, self-test (build/test/lint), and submit. The contributor
is an UNTRUSTED LLM, so its self-test is a courtesy before review, never approval.

The "PR" is just the git BRANCH it pushes — there is no PR object. Everything a reviewer needs
lives in git: the diff is ``git diff base..branch``, the commit messages ARE the cover letter,
and the self-test is recorded in git (e.g. ``git notes``) or as a check. Nothing is duplicated
into Python state (git is the source of truth).

Independent tasks run in parallel because each contributor works in its OWN branch/worktree, and
only on the files/symbols in ``task.scope``.

STUB: the implementation step runs through the carrier (in an isolated worktree); the git steps
are described in comments. Returns the branch ref.
"""
from __future__ import annotations

from . import llm
from .task import Task


def contribute(task: Task) -> str:
    """Do one Task in isolation and return the branch ref it pushed. STUB — carrier/git not wired."""
    # 1. git worktree add --detach <wt> <task.base_sha>     -> isolated checkout off the base
    # 2. run the implementation role via the carrier, inside <wt>, allowed to touch task.scope only:
    prompt = (
        f"Implement this task and nothing else.\nobjective: {task.objective}\n"
        f"contract to satisfy: {task.contract}\nyou may only touch: {task.scope}\n"
        "Make focused, one-logical-change commits with good messages (the messages are the cover letter)."
    )
    resp = llm.invoke(llm.CarrierRequest(role="contributor", prompt=prompt))
    # 3. commit the focused change(s) on the branch; self-test (build/test/lint); record it in git
    #    (e.g. git notes) — not in a Python object.
    # 4. return the branch ref, e.g. "refs/heads/contrib/<task.id>". The reviewer reads git from there:
    #    diff base..branch, the commit messages (cover letter), and the self-test note.
    raise NotImplementedError("contributor carrier/git not wired (stub)")  # pragma: no cover
