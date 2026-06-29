# receive.py — the INTAKE GATE: judges whether an incoming REQUEST may become an RFC.
# This is NOT a dumb loader/translator. A request is discussed and can be SENT BACK (差し戻し):
#     request --[receive gate]--> promote to RFC | send back for revision | reject.
# Only requests that PASS this gate become an RFC (ai-org/rfc/<id>), which then goes to rfc/review
# (the direction debate). So the RFC phase has TWO gates, in order:
#     1) receive : can this REQUEST become an RFC?   (this file)
#     2) review  : is the RFC's DIRECTION ok?        (review.py)
# THE RFC PHASE'S JOB = take a raw REQUEST and make it CONTRIBUTOR-TAKEABLE. That promotion is real
# work, not a load. It mirrors the Linux early-stage process (kernel.org process/3.Early-stage) — the
# "5 過程" that turn a request into a proposable RFC:
#     1) Specify the problem   — what must be solved, who is affected, where the system falls short
#     2) Early discussion      — surface objections / alternatives BEFORE implementation
#     3) Who do you talk to    — route to the right reviewers/maintainers (the right subsystem)
#     4) When to post          — the problem + intended approach are stated well enough to act on
#     5) Get buy-in            — go / no-go approval to proceed
# Only after these is the RFC ready for a Contributor to TAKE and implement. (This whole front-end was
# being IGNORED — receive was treated as a loader. It is not: it does the promotion work + the gate.)
#
# DECISION: these 5 過程 happen INSIDE the RFC formation — one codex-driven stage, like review's internal
# 5-reviewer + Aufheben loop — NOT as 5 separate git stages/branches/commits. Git stores ONLY the result:
# the promoted, contributor-takeable RFC (ai-org/rfc/<id>: rfc.json) or a send-back/reject marker. Doing
# the 5 過程 inside the RFC (not in git) keeps the git state from exploding.
#
# INPUT/OUTPUT field contract (grounded in REAL templates — Rust RFC 0000-template, PEP 12, Fuchsia RFC,
# Google design doc, GitLab feature proposal, kernel submitting-patches; not abstraction):
#   入り口 REQUEST (rough) carries the COMMON-8 through-line fields:
#       title, problem/motivation, proposal, alternatives, intended_users, affected_area, impact, context/links
#   出口 RFC (contributor-takeable) = the COMMON-8 (now REFINED) + the EXIT-ONLY fields the formation crafts:
#       goals & non-goals, reference-level design/spec, API/interface, backwards-compat, security, privacy,
#       testing, drawbacks, open-questions, future-possibilities, + meta (status, reviewers, resolution)
#   Formation's job = refine the common-8 AND craft the exit-only -> a contributor-takeable RFC (or send-back).
#   (A good request template is already a mini-RFC; the RFC adds the design/decision/meta the request lacks.)
#
# Shape (to match the other stages): git-read the request -> codex judges (gate) -> git-write either
# the promoted RFC (ai-org/rfc/<id>: rfc.json) or a send-back/reject marker.
#
# STATUS (be honest): the code BELOW is still only the manual loader/translator — the GATE (codex
# judgment + send-back, git read/write) is NOT built yet. TODO: build the gate in this form.
"""RFC receive — step 1 of the RFC phase: take in the RFC (the apex).

A raw requirement arriving at the AI Org is NOT in Linux-RFC form. The AI Org must first TRANSLATE it
into an implementable RFC (problem + proposed change + interface sketch). That translation is assumed
done here; for now the RFC is received MANUALLY (hand-written). An RFC mirrors a Linux mailing-list RFC.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


@dataclass
class RFC:
    title: str
    problem: str               # what is wrong / what is needed, stated concretely (the goal)
    proposed_change: str       # the intended change / approach
    interface_sketch: str = "" # the API/contract this would introduce or touch
    notes: str = ""


def receive(source: str | Path | Mapping[str, Any]) -> RFC:
    """Load a hand-written RFC from a dict or a JSON file path."""
    if isinstance(source, Mapping):
        data = source
    elif isinstance(source, (str, Path)):
        path = Path(source)
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise ValueError(f"Could not read RFC JSON file {path}: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise ValueError(f"RFC JSON file {path} is invalid JSON: {exc}") from exc
        if not isinstance(loaded, dict):
            raise ValueError(f"RFC JSON file {path} must contain a JSON object.")
        data = loaded
    else:
        raise TypeError("receive(source) expects a dict or a path to a JSON file.")

    missing = [field for field in ("title", "problem", "proposed_change") if data.get(field) is None]
    if missing:
        raise ValueError(f"RFC is missing required field(s): {', '.join(missing)}")

    return RFC(
        title=_string_field(data, "title"),
        problem=_string_field(data, "problem"),
        proposed_change=_string_field(data, "proposed_change"),
        interface_sketch=_string_field(data, "interface_sketch", default=""),
        notes=_string_field(data, "notes", default=""),
    )


def _string_field(data: Mapping[str, Any], field: str, *, default: str | None = None) -> str:
    value = data.get(field, default)
    if not isinstance(value, str):
        raise ValueError(f"RFC field {field!r} must be a string.")
    return value
