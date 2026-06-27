"""End-to-end flow (skeleton; thin stubs). Only the Contributor writes/fixes code; all else reviews.

  1. RFC Creation                  (manual for now; the translated, implementable requirement).
  2. RFC review                    RFC reviewers (5) + Aufheben (consolidates); direction-ok | nak. NAK stops.
  3. decompose                     -> flat Contributor-sized Tasks (RFC owns the split).
  4. Contribution { Implement + Acceptance }
                                   Contributor implements a branch per Task; Acceptance independently
                                   checks goal-reachability; internal revise loop (fail -> Contributor);
                                   only an accepted branch leaves. Tasks are independent -> parallel.
  5. Subsystem_tree_maintainer     review + (on accept) integrate into the subsystem tree. reject -> Contributor.
  6. Mainline_maintainer (Linus)   review the subsystem tree (incl. RFC met?) + (on accept) pull to mainline.
                                   reject -> Contributor.

Every fail/reject routes BACK to the Contributor (the sole code-fixer), revise, re-check (bounded CAP).
A maintainer's act is "review and, if accepted, integrate" (one act). Stubs only.
"""
from __future__ import annotations

from . import (
    contribution,
    decompose as _decompose,
    mainline_maintainer,
    rfc_review,
    subsystem_tree_maintainer,
)
from .rfc import RFC


def run(rfc: RFC) -> dict:
    rev = rfc_review.run_rfc_review(rfc)
    if rev.status != "direction-ok":
        return {"status": rev.status, "review": rev}              # NAK -> stop

    tasks = _decompose.decompose(rfc)                             # flat, Contributor-sized
    subsystem_refs = []
    for t in tasks:
        branch = contribution.make(rfc, t)                       # Implement + Acceptance -> accepted branch
        subsystem_refs.append(subsystem_tree_maintainer.review_and_integrate(branch))  # reject -> Contributor

    mainline = mainline_maintainer.review_and_integrate(subsystem_refs)  # Linus; reject -> Contributor
    return {"status": "done", "mainline": mainline}
