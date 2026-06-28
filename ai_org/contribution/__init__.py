"""Contribution — the unit handed up to a maintainer: an implemented AND accepted branch.

A Contribution contains two parts with DISTINCT actors:
  - Implement  : the Contributor writes the code (the only code author/fixer).
  - Acceptance : an INDEPENDENT goal-reachability check.

The implement<->acceptance revise loop is INTERNAL: acceptance fail -> the Contributor re-implements
-> re-check, bounded by CAP. Only an ACCEPTED branch leaves this unit, so a maintainer always receives
a contribution that already reaches the goal.

STUB: the loop shape is real; implement/check go through the carrier (not wired).
"""
from __future__ import annotations

from . import functional_check, implement
from ..rfc.receive import RFC
from ..rfc.task import Task

CAP = 5


def make(rfc: RFC, task: Task) -> str:
    """Produce an accepted branch for one task: implement, then independent acceptance, bounded loop."""
    result = implement.run(task)                 # Contributor writes
    branch = result["branch"]
    session_id = result.get("session_id")
    for _ in range(CAP):
        verdict = functional_check.check(rfc, branch)          # independent goal-reachability
        if verdict["ok"]:
            return branch
        result = implement.run(
            task,
            feedback=verdict,
            resume_session=session_id,
            branch_ref=branch,
        )                                        # fail -> Contributor re-implements (v2)
        branch = result["branch"]
        session_id = result.get("session_id") or session_id
    raise RuntimeError("contribution not accepted within CAP")  # terminal -> escalate
