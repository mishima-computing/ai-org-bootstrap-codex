"""End-to-end flow (skeleton; every node is a thin stub — placed to confirm the FLOW, not built out).

  1. RFC inserted (manual for now; the translated, implementable requirement).
  2. rfc_review.run_rfc_review            -> "direction-ok" | "nak". NAK stops here.
  3. decompose.decompose(rfc)             -> flat list of contributor-sized Tasks (RFC owns the split).
  4. contributor.contribute(task)         -> a git BRANCH ref per task (parallel; the "PR" is the branch).
  --- layer 1 (subsystem) ---
  5. review.subsystem_review(branch)      -> verdict (PR diff + GitHub CI checks; revise loop). [stub]
  6. integrate.accept_into_subsystem(...) -> subsystem tree ref. [stub]
  --- layer 2 (mainline / Linus) ---
  7. review.mainline_review(subsystem_ref)-> verdict (review the subsystem tree). [stub]
  8. integrate.pull_into_mainline([...])  -> mainline ref. [stub]

Two tiers only (subsystem maintainer + Linus); deeper nesting added only if a subsystem needs it.
Everything is a stub: orchestration shape is real; roles/git/GitHub go through the carrier seam.
"""
from __future__ import annotations

from . import contributor, decompose as _decompose, integrate, review, rfc_review
from .rfc import RFC


def run(rfc: RFC) -> dict:
    rev = rfc_review.run_rfc_review(rfc)
    if rev.status != "direction-ok":
        return {"status": rev.status, "review": rev}          # NAK -> stop

    tasks = _decompose.decompose(rfc)                          # flat, contributor-sized
    branches = [contributor.contribute(t) for t in tasks]     # each -> a git branch (parallel)

    # layer 1: review each PR + take it into the subsystem tree
    accepted = [b for b in branches if review.subsystem_review(b) == "accept"]
    subsystem_ref = integrate.accept_into_subsystem(accepted)  # (shape stub)

    # layer 2: Linus reviews the subsystem tree + pulls into mainline
    if review.mainline_review(subsystem_ref) != "accept":
        return {"status": "mainline-rejected", "subsystem": subsystem_ref}
    mainline = integrate.pull_into_mainline([subsystem_ref])
    return {"status": "merged", "mainline": mainline}
