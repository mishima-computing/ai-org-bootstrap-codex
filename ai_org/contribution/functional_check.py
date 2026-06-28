"""functional_check — the Contributor's own functional verification (動作確認) of its work.

Part of a Contribution (alongside implement's mechanical self-check). Here the Contributor runs its branch
the way a user would, to confirm the user can actually REACH THE GOAL — beyond "tests pass" ("all tests
passed but the user still couldn't play"). This is self-verification (a courtesy/quality step), NOT
approval; the independent judgment is downstream (the maintainers). On fail -> the Contributor fixes.

Substance = the Mona walkthrough: a two-agent static check — a stubborn USER persona that keeps trying
until truly blocked, and a code-grounded APP that traces the real source (file:line) and confesses gaps
and false successes, without launching the app. Output: a reachability verdict + where intent meets
broken reality.

STUB: runs through the carrier (not wired).
"""
from __future__ import annotations

from ..rfc.receive import RFC


def check(rfc: RFC, branch: str) -> str:
    """Mona walkthrough: can the user reach the RFC's goal with this branch? "ok" | "fail". STUB."""
    raise NotImplementedError("functional_check (Mona walkthrough) not wired (stub)")  # pragma: no cover
