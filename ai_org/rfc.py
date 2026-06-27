"""RFC — the apex of the AI Org.

A raw requirement that arrives at the AI Org is NOT in Linux-RFC form. The AI Org must first
TRANSLATE it into an *implementable* RFC (problem + proposed change + interface sketch). That
translation step is assumed already done here; for now an RFC is inserted MANUALLY by a human.

An RFC mirrors a Linux mailing-list RFC: "here is the problem, here is the change I propose,
here is the approach/interface." It is the unit that enters RFC review (see ``rfc_review.py``).
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

    # NOTE: an RFC is the OUTPUT of the (assumed-done) "raw requirement -> implementable RFC"
    # translation. That translator is not built yet; for now we hand-write the RFC.
