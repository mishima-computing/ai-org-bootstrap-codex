"""Review boundaries (thin placeholders — NOT built out; here to complete the flow).

Both tiers are a BOUNDED REVISE LOOP (same shape as RFC review): review -> if not accepted, send
the work back DOWNSTREAM to be fixed -> re-review -> up to CAP rounds. A reject is NOT terminal
until the cap (or a fundamental NAK) is hit.

  - subsystem_review(branch)        : layer 1. Reviews the contributor PR diff (git diff base..branch)
                                      AND the GitHub CI checks. On reject -> contributor revises (v2)
                                      -> re-review. Terminal reject after CAP escalates up.
  - mainline_review(subsystem_ref)  : layer 2 (Linus). Reviews a subsystem tree before mainline pull.
                                      On reject -> sent back to the subsystem to fix (which may push
                                      down to contributors for v2) -> re-review. Terminal after CAP.

merge gate at each tier = "GitHub checks green" AND "reviewer: no unresolved objection".

STUB: placeholders only; the revise loop / verdict are not built out.
"""
from __future__ import annotations

CAP = 5  # same tentative cap as RFC review


def subsystem_review(branch: str) -> str:
    """Layer 1 bounded review+revise loop of a contributor PR. Returns "accept" | "reject". STUB."""
    raise NotImplementedError("subsystem_review placeholder")  # pragma: no cover


def mainline_review(subsystem_ref: str) -> str:
    """Layer 2 (Linus) bounded review+revise loop of a subsystem tree. "accept" | "reject". STUB."""
    raise NotImplementedError("mainline_review placeholder")  # pragma: no cover
