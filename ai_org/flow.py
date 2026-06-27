"""End-to-end flow (skeleton; every node is a thin stub — placed to confirm the FLOW, not built out).

  1. RFC inserted (manual for now; the translated, implementable requirement).
  2. rfc_review.run_rfc_review            -> "direction-ok" | "nak". NAK stops here.
  3. decompose.decompose(rfc)             -> flat list of contributor-sized Tasks (RFC owns the split).
  4. contributor.contribute(task)         -> a git BRANCH ref per task (parallel; the "PR" is the branch).
  --- layer 1 (subsystem) ---
  5. review.subsystem_review(branch)      -> bounded revise loop (PR diff + GitHub CI checks; reject ->
                                            contributor v2 -> re-review; terminal reject only after CAP). [stub]
  6. integrate.accept_into_subsystem(...) -> subsystem tree ref. [stub]
  --- layer 2 (mainline / Linus) ---
  7. review.mainline_review(subsystem_ref)-> bounded revise loop (reject -> subsystem fixes, maybe pushing
                                            down to contributors -> re-review; terminal reject only after CAP). [stub]
  8. integrate.pull_into_mainline([...])  -> mainline ref. [stub]

A reject at any tier is NOT terminal: it sends the work back downstream to be fixed and re-reviewed, up to
CAP. Only a cap-exhausted (or fundamental NAK) reject is terminal and surfaces here.

Two tiers only (subsystem maintainer + Linus); deeper nesting added only if a subsystem needs it.
Everything is a stub: orchestration shape is real; roles/git/GitHub go through the carrier seam.
"""
from __future__ import annotations

from . import acceptance, contributor, decompose as _decompose, integrate, review, rfc_review
from .rfc import RFC


def run(rfc: RFC) -> dict:
    rev = rfc_review.run_rfc_review(rfc)
    if rev.status != "direction-ok":
        return {"status": rev.status, "review": rev}          # NAK -> stop

    tasks = _decompose.decompose(rfc)                          # flat, contributor-sized
    branches = [contributor.contribute(t) for t in tasks]     # each -> a git branch (parallel)

    # layer 1: each review runs its OWN bounded revise loop; "reject" here = terminal after CAP
    verdicts = {b: review.subsystem_review(b) for b in branches}
    if any(v != "accept" for v in verdicts.values()):
        return {"status": "subsystem-rejected",
                "rejected": [b for b, v in verdicts.items() if v != "accept"]}
    subsystem_ref = integrate.accept_into_subsystem(list(verdicts))

    # layer 2 (Linus): bounded revise loop too; "reject" = terminal after CAP
    if review.mainline_review(subsystem_ref) != "accept":
        return {"status": "mainline-rejected", "subsystem": subsystem_ref}
    mainline = integrate.pull_into_mainline([subsystem_ref])

    # close the loop: "merged" is not "goal achieved" — verify the RFC's intent is actually met
    if not acceptance.goal_met(rfc, mainline):
        return {"status": "merged-but-goal-unmet", "mainline": mainline}
    return {"status": "done", "mainline": mainline}
