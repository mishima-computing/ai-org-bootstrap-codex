"""RFC receive — step 1 of the RFC phase: take in the RFC (the apex).

A raw requirement arriving at the AI Org is NOT in Linux-RFC form. The AI Org must first TRANSLATE it
into an implementable RFC (problem + proposed change + interface sketch). That translation is assumed
done here; for now the RFC is received MANUALLY (hand-written). An RFC mirrors a Linux mailing-list RFC.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RFC:
    title: str
    problem: str               # what is wrong / what is needed, stated concretely (the goal)
    proposed_change: str       # the intended change / approach
    interface_sketch: str = "" # the API/contract this would introduce or touch
    notes: str = ""


def receive() -> RFC:
    """Intake an RFC (manual for now -> later, the raw-requirement -> RFC translator). STUB."""
    raise NotImplementedError("rfc.receive intake not wired (stub)")  # pragma: no cover
