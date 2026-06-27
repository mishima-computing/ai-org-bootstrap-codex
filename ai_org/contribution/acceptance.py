"""Acceptance — independent goal-reachability check (part of a Contribution).

A human contributor would self-check; an LLM contributor cannot be trusted to (all tests green is NOT
the same as "the user reached the goal"). So Acceptance is a SEPARATE, INDEPENDENT actor inside the
Contribution unit: given the RFC (the user goal to reach) and the implemented branch, it verifies the
user can ACTUALLY reach the goal end-to-end -- not that endpoints return 200.

Method: a two-agent static walkthrough -- a stubborn user persona keeps trying until truly blocked,
while a code-grounded "app" traces the real source (file:line) and confesses gaps / false successes,
without launching the app. Produces a reachability verdict + where intent meets broken reality.

On fail -> back to the Contributor (the only one who can fix non-working code). STUB.
"""
from __future__ import annotations

from ..rfc.receive import RFC


def check(rfc: RFC, branch: str) -> str:
    """Independent goal-reachability check of a branch. "ok" | "fail" (-> Contributor). STUB."""
    raise NotImplementedError("acceptance.check placeholder")  # pragma: no cover
