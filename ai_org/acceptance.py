"""Goal acceptance — close the loop back to the RFC (thin placeholder; not built out).

"merged" is not "goal achieved". After the work is in mainline, verify the merged result actually
satisfies the RFC's intent end-to-end (the RFC's acceptance criteria), then the RFC is done.
Without this the loop never closes back to the requirement.

STUB: placeholder only.
"""
from __future__ import annotations

from .rfc import RFC


def goal_met(rfc: RFC, mainline_ref: str) -> bool:
    """Does the merged mainline actually satisfy the RFC's intent? STUB."""
    raise NotImplementedError("goal_met placeholder")  # pragma: no cover
