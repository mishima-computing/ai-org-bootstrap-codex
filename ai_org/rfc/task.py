"""Task — one contributor-sized unit of work, materialized from the converged RFC.

The RFC already owns the split (approach + how to break it). A Task is one concrete,
contributor-executable slice of that: enough for ONE contributor to implement and open ONE PR.

Flat baseline: Tasks are INDEPENDENT (``depends_on`` empty) -> each runs on its own branch in
parallel. Structure (ordering/base-from-predecessor) is added to ``depends_on`` ONLY when a real
serial dependency actually exists; a Task is split further ONLY if it is too big. No graph is
assumed.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Task:
    id: str
    objective: str                                   # what this contributor must do (from the RFC)
    contract: str = ""                               # interface/contract to satisfy (RFC approach)
    base_sha: str = ""                               # immutable commit to branch from
    scope: list = field(default_factory=list)        # files/symbols it is allowed to touch
    depends_on: list = field(default_factory=list)   # empty in the flat case; set only for serial deps
