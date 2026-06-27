"""Contributor — implements ONE Task on its own branch and submits a PR.

Mirrors a Linux contributor: branch off the base, do the work in isolation (own git worktree),
make focused one-logical-change commits, self-test (build/test/lint), write a cover letter, and
submit the branch as a PR. The contributor is an UNTRUSTED LLM, so its self-test is a courtesy
before review, never approval.

Independent tasks run in parallel because each contributor works in its OWN branch/worktree
(Git is the parallelism boundary). A contributor never touches another's branch, and only the
files/symbols in ``task.scope``.

STUB: the implementation step runs through the carrier (in an isolated worktree); the git steps
are described in comments. Returns a PR.
"""
from __future__ import annotations

from . import llm
from .pr import PR
from .task import Task


def contribute(task: Task) -> PR:
    """Do one Task in isolation and return its PR. STUB — carrier/git not wired."""
    # 1. git worktree add --detach <wt> <task.base_sha>     -> isolated checkout off the base
    # 2. run the implementation role via the carrier, inside <wt>, allowed to touch task.scope only:
    prompt = (
        f"Implement this task and nothing else.\nobjective: {task.objective}\n"
        f"contract to satisfy: {task.contract}\nyou may only touch: {task.scope}\n"
        "Make focused, one-logical-change commits and write a short cover letter (what & why)."
    )
    resp = llm.invoke(llm.CarrierRequest(role="contributor", prompt=prompt))
    # 3. commit the focused change(s) on the contributor's branch; run self-test (build/test/lint)
    # 4. return PR(task_id=task.id, branch=..., base_sha=task.base_sha, commits=..., cover_letter=...,
    #              self_test=...)
    raise NotImplementedError("contributor carrier/git not wired (stub)")  # pragma: no cover
