"""Patch stage: produce an implemented AND accepted contribution branch.

The patch stage contains two parts with DISTINCT actors:
  - Implement  : the Contributor writes the code (the only code author/fixer).
  - Acceptance : an INDEPENDENT goal-reachability check.

The implement<->acceptance revise loop is INTERNAL: acceptance fail -> the
Contributor re-implements -> re-check, bounded by cap.
"""
from __future__ import annotations

from . import functional_check, implement


def make(repo, rfc_id_or_branch: str, rfc_path: str = "rfc.json", cap: int = 3) -> dict:
    """Produce an accepted contribution branch, retrying rejected attempts."""
    feedback = None
    branch = None
    verdict = None
    for attempt in range(1, cap + 1):
        result = implement.run(repo, rfc_id_or_branch, rfc_path=rfc_path, feedback=feedback, attempt=attempt)
        branch = result["branch"]
        verdict = functional_check.check(repo, branch)
        if verdict["ok"]:
            return {"ok": True, "branch": branch, "verdict": verdict, "attempts": attempt}
        feedback = verdict["blockers"]
    return {"ok": False, "branch": branch, "verdict": verdict, "attempts": cap}
