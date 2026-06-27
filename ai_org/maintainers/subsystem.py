"""Subsystem_tree_maintainer (layer 1) — reviews a Contribution and, if accepted, integrates it into
the subsystem tree.

REVIEW ONLY: it does not fix code; a reject routes back to the Contributor (the sole code-fixer).
"review_and_integrate" is ONE act: review the PR diff + GitHub CI checks; on accept, take the branch
into the subsystem tree (git merge/cherry-pick) and return its ref; on reject, send back to the
Contributor (v2) and re-review -- a bounded loop (CAP).

STUB.
"""
from __future__ import annotations

CAP = 5


def review_and_integrate(branch: str) -> str:
    """Review the contribution; on accept integrate -> subsystem ref; reject -> Contributor. STUB."""
    raise NotImplementedError("subsystem_tree_maintainer.review_and_integrate placeholder")  # pragma: no cover
