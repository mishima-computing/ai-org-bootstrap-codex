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

from . import acceptance, contributor
from ..rfc.receive import RFC
from ..rfc.task import Task

CAP = 5


def make(rfc: RFC, task: Task) -> str:
    """Produce an accepted branch for one task: implement, then independent acceptance, bounded loop."""
    branch = contributor.implement(task)                 # Contributor writes
    for _ in range(CAP):
        if acceptance.check(rfc, branch) == "ok":        # independent goal-reachability
            return branch
        branch = contributor.implement(task)             # fail -> Contributor re-implements (v2)
    raise RuntimeError("contribution not accepted within CAP")  # terminal -> escalate
