"""Carrier seam — the single place an LLM-backed role is invoked.

By request: roles are NOT direct LLM API calls. An LLM-backed role runs through a *carrier*
(a coding-agent CLI process — Codex / Claude Code — launched as a subprocess, typically in an
isolated git worktree). Keeping that behind one function means every role goes through one seam
and the rest of the code never calls an LLM directly.

STUB: ``invoke`` is not wired. It documents the request/response shape and raises so callers
fail loudly until a real carrier is connected.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CarrierRequest:
    role: str            # who is speaking, e.g. "rfc-review:approach" or "aufheben"
    prompt: str          # full instruction + context for this role
    # (later, for roles that touch a tree:) repo, base_sha, allowed_scope


@dataclass
class CarrierResponse:
    text: str            # the role's raw output


def invoke(req: CarrierRequest) -> CarrierResponse:
    """Run one LLM-backed role through a carrier subprocess. STUB — not wired yet.

    Behavior when implemented: spawn the configured coding-agent CLI for ``req.role`` with
    ``req.prompt``, capture its output, return it as ``CarrierResponse``. No direct API call.
    """
    raise NotImplementedError("carrier invocation not wired yet (stub)")
