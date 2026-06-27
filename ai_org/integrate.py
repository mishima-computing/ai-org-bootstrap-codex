"""Integration boundaries (thin placeholders — NOT built out yet; here only to complete the flow).

  - accept_into_subsystem(branch)    : layer 1. A reviewed contributor branch is taken into the
                                       subsystem tree (the maintainer pulls it).
  - pull_into_mainline(subsystem_refs): layer 2. Linus pulls reviewed subsystem trees into mainline.

All integration is plain git (merge / cherry-pick); git is the source of truth.

STUB: placeholders only.
"""
from __future__ import annotations


def accept_into_subsystem(branch: str) -> str:
    """Layer 1: take a reviewed branch into the subsystem tree; return the subsystem ref. STUB."""
    raise NotImplementedError("accept_into_subsystem placeholder")  # pragma: no cover


def pull_into_mainline(subsystem_refs: list) -> str:
    """Layer 2: Linus pulls reviewed subsystem trees into mainline; return mainline ref. STUB."""
    raise NotImplementedError("pull_into_mainline placeholder")  # pragma: no cover
