"""Contributor — the ONLY actor that writes or fixes code.

Implements one Task on its own branch (its own git worktree off the base), focused one-logical-change
commits, scoped to ``task.scope``. Every fail/reject anywhere downstream routes back here, because no
one else can fix non-working code. Returns the branch ref.

STUB: the implementation runs through the carrier (in an isolated worktree); git steps are in comments.
"""
from __future__ import annotations

from .. import carrier
from ..rfc.task import Task


def run(task: Task) -> str:
    """Write/fix one Task in its own branch; return the branch ref. STUB — carrier/git not wired."""
    # 1. git worktree add --detach <wt> <task.base_sha>   -> isolated checkout off the base
    # 2. run the implementation through the carrier, inside <wt>, allowed to touch task.scope only:
    prompt = (
        f"Implement this task and nothing else.\nobjective: {task.objective}\n"
        f"contract to satisfy: {task.contract}\nyou may only touch: {task.scope}\n"
        "Make focused, one-logical-change commits with good messages (the messages are the cover letter)."
    )
    resp = carrier.invoke(carrier.CarrierRequest(role="contributor", prompt=prompt))
    # 3. commit focused change(s) on the branch; record self-test in git (note), not Python state.
    # 4. return the branch ref, e.g. "refs/heads/contrib/<task.id>".
    raise NotImplementedError("contributor.implement carrier/git not wired (stub)")  # pragma: no cover
