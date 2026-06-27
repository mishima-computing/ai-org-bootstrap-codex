"""Review boundaries (thin placeholders — NOT built out yet; here only to complete the flow).

Two review tiers, both at a merge boundary, both a revise-until-no-unresolved-objection loop
(same shape as RFC review; details deferred):

  - subsystem_review(branch)      : layer 1. Review a contributor's PR diff (git diff base..branch)
                                    PLUS the GitHub CI checks. -> accept / changes / reject.
  - mainline_review(subsystem_ref): layer 2 (Linus). Review a subsystem tree before pulling it
                                    into mainline. -> accept / changes / reject.

STUB: placeholders only.
"""
from __future__ import annotations


def subsystem_review(branch: str) -> str:
    """Layer 1 review of a contributor PR (branch). STUB — returns verdict later."""
    raise NotImplementedError("subsystem_review placeholder")  # pragma: no cover


def mainline_review(subsystem_ref: str) -> str:
    """Layer 2 (Linus) review of a subsystem tree before mainline pull. STUB."""
    raise NotImplementedError("mainline_review placeholder")  # pragma: no cover
