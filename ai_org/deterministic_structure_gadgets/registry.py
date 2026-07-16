"""Gadget hint registry: mechanical fix-it hints on the review return path.

MEMENTO — HINT DISCIPLINE (ratified; rustc fix-it pattern):
Hints fire REACTIVELY by typed surface only. The reviewer's response is read
mechanically AFTER it is parsed: matching happens code-side on enum-typed
fields (objection axis / type / requested_author_action / resolution_authority)
and on code-emitted typed error strings (gate error types, closed validator
error constants). PROSE NEVER MATCHES: if identifying a gadget would require
interpreting claim/evidence/impact text, there is NO match — silence, never a
guess, and never an LLM classification call. Reviewer inputs are never
modified and author outputs are never steered (thinking-power preservation on
both sides: the reviewer is unconstrained on entry, the author unsteered on
return). A hint names the tool and what it generates — never what the answer
should be — and it is NON-BINDING: verdict derivation, round accounting, and
gate decisions must be identical with or without registry entries.

Registry hygiene: entries exist only for the hintable skeleton gadgets.
repair_delta/merge_fragment are internal primitives, not tools an author is
pointed at. A typed failure that already has auto-repair wiring (the decision
step's evaluation-coverage repair round) must NOT appear here — the repair
handles it, and double-hinting an auto-repaired failure is noise.
"""
from __future__ import annotations

from typing import Any, Mapping

from ._core import _gadget_error, _total

__all__ = [
    "GADGET_HINT_REGISTRY",
    "MATCHABLE_SURFACE_FIELDS",
    "match_gadget_hints",
    "render_gadget_hint_line",
]

# The ONLY fields matching may read. Everything else on a surface — claim,
# evidence, impact, why-text, any prose — is invisible by construction.
MATCHABLE_SURFACE_FIELDS = (
    "axis",
    "type",
    "requested_author_action",
    "resolution_authority",
    "gate_error_type",
    "validator_error",
)

# Pure data: typed key -> gadget descriptor. "produces" describes generated
# structure only (slots, ids, placeholders), never a conclusion.
GADGET_HINT_REGISTRY: tuple[dict[str, Any], ...] = (
    {
        "gadget_id": "coverage_ledger_skeleton",
        "produces": "one coverage slot per scope item with the child assignment left empty",
        "match": {"field": "requested_author_action", "value": "split_scope"},
    },
    {
        "gadget_id": "acknowledged_checks_skeleton",
        "produces": "an acknowledged_patchwork_checks mapping with one key per injected check and empty value placeholders",
        "match": {"field": "gate_error_type", "value": "patchwork_check_acknowledgement_mismatch"},
    },
    {
        "gadget_id": "evaluation_matrix_skeleton",
        "produces": "an evaluation matrix skeleton with exactly one evaluation slot per candidate",
        "match": {
            "field": "validator_error",
            "value": "Codex candidate evaluation returned incomplete candidate coverage",
        },
    },
)


@_total
def match_gadget_hints(typed_surface: Any) -> dict[str, Any]:
    """Match a parsed typed surface against the registry, mechanically.

    typed_surface is any mapping carrying typed fields (a parsed objection
    record, or {"gate_error_type"/"validator_error": ...} built by a caller
    that knows its own error type statically). Only MATCHABLE_SURFACE_FIELDS
    are read, only exact string equality fires, and hints return in registry
    order deduplicated by gadget id. No match -> empty list, never a guess.
    """
    if not isinstance(typed_surface, Mapping):
        return _gadget_error("invalid_input", "typed_surface must be a mapping")
    hints: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in GADGET_HINT_REGISTRY:
        rule = entry["match"]
        field = rule["field"]
        if field not in MATCHABLE_SURFACE_FIELDS or entry["gadget_id"] in seen:
            continue
        value = typed_surface.get(field)
        if isinstance(value, str) and value == rule["value"]:
            seen.add(entry["gadget_id"])
            hints.append(
                {
                    "gadget_id": entry["gadget_id"],
                    "produces": entry["produces"],
                    "matched_on": {"field": field, "value": rule["value"]},
                }
            )
    return {"ok": True, "hints": hints}


@_total
def render_gadget_hint_line(hint: Any) -> dict[str, Any]:
    """Render one hint as a single short structural line for an author brief.

    Names the tool and what it generates plus the typed field that fired —
    no advice, no prescription of what the answer should be.
    """
    if not isinstance(hint, Mapping):
        return _gadget_error("invalid_input", "hint must be a mapping")
    gadget_id = hint.get("gadget_id")
    produces = hint.get("produces")
    matched_on = hint.get("matched_on")
    if not isinstance(gadget_id, str) or not gadget_id or not isinstance(produces, str) or not produces:
        return _gadget_error("invalid_input", "hint must carry string gadget_id and produces")
    if (
        not isinstance(matched_on, Mapping)
        or not isinstance(matched_on.get("field"), str)
        or not isinstance(matched_on.get("value"), str)
    ):
        return _gadget_error("invalid_input", "hint.matched_on must carry string field and value")
    return {
        "ok": True,
        "line": (
            f"structural gadget available: {gadget_id} (generates {produces}; "
            f"matched on {matched_on['field']}={matched_on['value']})"
        ),
    }
