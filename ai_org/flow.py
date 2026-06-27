"""End-to-end flow (skeleton; thin stubs). Three phases; only the Contributor writes/fixes code.

  rfc/         RFC phase:        receive -> review (5 + Aufheben) -> decompose  => Tasks
  contribution/ Contribution phase (per Task): { implement (Contributor) + acceptance (independent) }
                                  internal revise loop => an accepted branch
  maintainers/  Maintainers phase: subsystem (L1) -> mainline (L2 / Linus)      => mainline

Every fail/reject routes BACK to the Contributor (the sole code-fixer). A maintainer's act is
"review and, if accepted, integrate" (one act). Stubs only; the carrier is not wired.
"""
from __future__ import annotations

from . import contribution
from .maintainers import mainline, subsystem
from .rfc import decompose, review
from .rfc.receive import RFC


def run(rfc: RFC) -> dict:
    rev = review.run_rfc_review(rfc)
    if rev.status != "direction-ok":
        return {"status": rev.status, "review": rev}              # NAK -> stop

    tasks = decompose.decompose(rfc)                              # flat, Contributor-sized
    subsystem_refs = []
    for t in tasks:
        branch = contribution.make(rfc, t)                       # Implement + Acceptance -> accepted branch
        subsystem_refs.append(subsystem.review_and_integrate(branch))   # L1; reject -> Contributor

    mainline_ref = mainline.review_and_integrate(subsystem_refs)  # L2 / Linus; reject -> Contributor
    return {"status": "done", "mainline": mainline_ref}
