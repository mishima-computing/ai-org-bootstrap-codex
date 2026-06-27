"""Top-level flow (flat baseline) — RFC -> RFC review -> decompose -> contributors -> PRs.

  1. RFC inserted (manual for now; it is the translated, implementable requirement).
  2. rfc_review.run_rfc_review -> "direction-ok" or "nak". NAK stops here, returning the review
     (which dimensions resolved, which objections remain).
  3. decompose.decompose(rfc) -> a flat list of contributor-sized Tasks (independent baseline;
     the RFC owns the split, this only materializes it).
  4. each Task -> contributor.contribute -> a PR. Independent, so contributors run in parallel
     on their own branches (no graph).
  5. [next, not built]: review each PR, then integrate the PRs upward to mainline.

STUB: orchestration is real; roles/git go through the carrier seam (not wired), so running this
raises at the first role call.
"""
from __future__ import annotations

from . import contributor, decompose as _decompose, rfc_review
from .rfc import RFC


def run(rfc: RFC) -> dict:
    review = rfc_review.run_rfc_review(rfc)
    if review.status != "direction-ok":
        return {"status": review.status, "review": review}   # NAK -> stop (resolved/unresolved on review)

    tasks = _decompose.decompose(rfc)                        # flat, contributor-sized, independent
    prs = [contributor.contribute(t) for t in tasks]        # each -> one PR (parallel, own branches)
    return {"status": "prs-open", "prs": prs}               # next: review + integrate (not built)
