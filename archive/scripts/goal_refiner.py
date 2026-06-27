"""Refine a RAW goal into a candidate STRUCTURED goal at intake (ADR-0016 D1/D1b).

The engine receives a raw goal (free text). Before any decomposition it must refine that into a
*structured* goal — the WHY made checkable — and gate on SUFFICIENCY: a candidate structured goal is
sufficient only when a *falsifiable acceptance can be named*. Per ADR-0016 D1b that means naming all of:
  (a) outcome           — the observable, consumer-visible outcome
  (b) success_condition — a falsifiable success condition over that outcome
  (c) negative_control  — what the acceptance MUST reject (the D2 precondition: what makes the check go red)
  (d) owner             — the owner/oracle of correctness

The carrier (an LLM, like the splitter) PROPOSES the four fields; it is untrusted. The deterministic kernel
here DECIDES sufficiency by checking each field is actually named (non-empty) — ADR-0011's small trusted
kernel over an untrusted generator. If any field is unnameable the carrier leaves it empty (it must NOT
fabricate the WHY — choosing ends, forbidden by ADR-0016 D5); the kernel then reports the goal
UNDERDETERMINED so the engine can REQUEST/HOLD rather than decompose.

This proves CHECKABILITY, never intent-correctness: a structured goal that names (a)-(d) may still encode a
wrong WHY — that is the goal-setter's residual (ADR-0016 D5), not something this gate can catch.
"""
from __future__ import annotations

__all__ = ["refine", "REQUIRED_FIELDS"]

import json


# the four things that must be NAMEABLE for a falsifiable acceptance to exist (ADR-0016 D1b a-d)
REQUIRED_FIELDS = ("outcome", "success_condition", "negative_control", "owner")


def _default_carrier(_prompt):
    # no carrier => nothing named => underdetermined (fail closed, ADR-0011 "unproven never passes")
    return "{}"


def _build_prompt(goal, context):
    return (
        "Refine the raw goal below into a CANDIDATE STRUCTURED GOAL: the WHY made checkable. Name the four "
        "fields that a falsifiable acceptance of the OUTCOME requires. Name only what the goal genuinely "
        "DETERMINES — if the goal does not determine a field, leave it an EMPTY STRING. Do NOT invent or "
        "guess the intent to fill a blank: a fabricated WHY is worse than an admitted gap.\n\n"
        "Fields:\n"
        "- outcome: the observable, consumer-visible outcome the goal must produce (not the implementation).\n"
        "- success_condition: a FALSIFIABLE condition over that outcome — what, concretely, would show it is "
        "achieved.\n"
        "- negative_control: a concrete counter-example the acceptance MUST reject — what would make the "
        "check go RED (if you cannot state this, there is no real check).\n"
        "- owner: who owns/judges correctness of this outcome (the oracle).\n"
        "- intent: one sentence restating the WHY in the goal-setter's terms (optional context).\n\n"
        "Return ONLY a JSON object with exactly these string keys: outcome, success_condition, "
        "negative_control, owner, intent. No prose.\n\n"
        f"Raw goal:\n{goal}\n\n"
        f"Codebase context:\n{context}\n"
    )


def _coerce_str(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def _normalize(data) -> dict:
    """Deterministic kernel: decide sufficiency from the (untrusted) carrier output.

    Sufficient IFF every REQUIRED_FIELDS entry is named (non-empty). Returns
    {sufficient, structured, missing}: `structured` is the candidate WHY (always returned so a consumer can
    see what WAS nameable and pre-complete the rest, ADR-0016 D1b); `missing` lists the unnameable required
    fields (the engine's ASK)."""
    if not isinstance(data, dict):
        data = {}
    structured = {k: _coerce_str(data.get(k)) for k in REQUIRED_FIELDS}
    structured["intent"] = _coerce_str(data.get("intent"))
    missing = [k for k in REQUIRED_FIELDS if not structured[k]]
    return {"sufficient": not missing, "structured": structured, "missing": missing}


def refine(goal, context, carrier=_default_carrier) -> dict:
    """Refine a raw goal into a candidate structured goal and gate on sufficiency (ADR-0016 D1b).

    Returns {sufficient: bool, structured: {outcome, success_condition, negative_control, owner, intent},
    missing: [unnameable required fields]}. FAILS CLOSED: any carrier/parse error => not sufficient (a
    broken refiner must never wave a goal through — ADR-0011)."""
    try:
        out = carrier(_build_prompt(goal, context))
        return _normalize(json.loads(out))
    except Exception:
        return {
            "sufficient": False,
            "structured": {k: "" for k in (*REQUIRED_FIELDS, "intent")},
            "missing": list(REQUIRED_FIELDS),
            "error": "refiner_failed",
        }
