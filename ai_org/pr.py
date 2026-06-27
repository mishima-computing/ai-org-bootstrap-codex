"""PR — a contributor's submission; the unit handed UP for review.

Mirrors a Linux patch / pull request: a branch of focused commits off a base, with a cover
letter (what & why) and the contributor's own self-test result. This is the single interface a
contributor exposes to the reviewer/integrator above it.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PR:
    task_id: str
    branch: str                                   # the contributor's branch (work in isolation)
    base_sha: str                                 # what it branched from
    commits: list = field(default_factory=list)   # focused, one-logical-change commits
    cover_letter: str = ""                        # what & why, sent with the patch
    self_test: str = ""                           # contributor's own build/test result
    # NOTE: self_test is a COURTESY before review, never approval. Approval comes from the
    # reviewer/integrator above (the contributor is an untrusted LLM).
