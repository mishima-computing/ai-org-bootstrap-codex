"""Deterministic structure gadgets: the org's army knife of form and repair.

MEMENTO — DIVISION OF LABOR (ratified; do not re-derive):
    CODE OWNS STRUCTURE. THE MODEL OWNS JUDGMENT.
Carrier LLMs used to produce entire step JSONs, and deterministic contracts that
codex output schemas cannot express (cardinality, cross-references; the codex
output-schema safe subset forbids const/if/minLength/...) were checked only AFTER
generation, so a late contract failure discarded a whole multi-minute derivation.
Live accident: the approach decision step failed "select_approach requires one
evaluation per candidate from step 5" and threw away a 38-minute revision. These
gadgets close that hole from the code side: they GENERATE structure (ids, slots,
placeholders) before the model runs, and they compute minimal REPAIR deltas after
a contract failure so one bounded round can ask the model for only the missing
fragments instead of regenerating everything.

MEMENTO — NO-CONTENT INVARIANT (hard rule, zero conclusion-steering):
Gadgets must NEVER generate content. A skeleton may contain only structure: ids
that already exist upstream, field names, and empty placeholders (None). No prose
in slots, no defaults a model could mistake for a judgment, no prefilled ratings,
reasons, or assignments. If a change to this package puts words into a slot, it
is a bug even if every test stays green.

Contract for every public function: pure, total, no I/O, no model calls, no
mutation of inputs. Malformed input yields a typed error dict
{"ok": False, "error": {"type": ..., "message": ...}}; exceptions never escape.
Success yields {"ok": True, <payload>}: "skeleton" for the skeleton generators,
"delta" for repair_delta, "merged" (plus applied/skipped slot lists) for
merge_fragment.

Slot addressing used by repair deltas and merges:
  - keyed-list slot:  "<key>[<id_field>=<id>]"   e.g. evaluations[candidate_id=c2]
  - mapping slot:     "<key>.<name>"             e.g. acknowledged_patchwork_checks.counter
  - scalar slot:      "<key>"
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping, Sequence

from ._core import _gadget_error, _total
from .registry import (
    GADGET_HINT_REGISTRY,
    MATCHABLE_SURFACE_FIELDS,
    match_gadget_hints,
    render_gadget_hint_line,
)

__all__ = [
    "evaluation_matrix_skeleton",
    "coverage_ledger_skeleton",
    "acknowledged_checks_skeleton",
    "repair_delta",
    "merge_fragment",
    "GADGET_HINT_REGISTRY",
    "MATCHABLE_SURFACE_FIELDS",
    "match_gadget_hints",
    "render_gadget_hint_line",
]


def _string_id_list(value: Any, label: str) -> dict[str, Any] | list[str]:
    """Normalize an id input into a validated list of unique non-empty strings."""
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        return _gadget_error("invalid_input", f"{label} must be a sequence of ids")
    ids: list[str] = []
    for item in value:
        if isinstance(item, Mapping):
            item = item.get("id")
        if not isinstance(item, str) or not item:
            return _gadget_error("invalid_input", f"{label} entries must be non-empty strings")
        ids.append(item)
    if len(ids) != len(set(ids)):
        return _gadget_error("duplicate_id", f"{label} entries must be unique")
    return ids


def _field_name_list(value: Any, label: str) -> dict[str, Any] | list[str]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        return _gadget_error("invalid_input", f"{label} must be a sequence of field names")
    names = list(value)
    if not all(isinstance(name, str) and name for name in names):
        return _gadget_error("invalid_input", f"{label} entries must be non-empty strings")
    if len(names) != len(set(names)):
        return _gadget_error("duplicate_id", f"{label} entries must be unique")
    return names


@_total
def evaluation_matrix_skeleton(
    candidates: Any,
    *,
    score_fields: Sequence[str] = (),
    score_leaf_fields: Sequence[str] = ("rating", "reason"),
) -> dict[str, Any]:
    """Build an evaluation-matrix skeleton with exactly one slot per candidate.

    `candidates` accepts a sequence of candidate id strings, a sequence of
    candidate mappings carrying "id", or a step-4 style {"candidates": [...]}
    mapping. Every judgment position is a None placeholder; the only prefilled
    values are the candidate ids themselves.
    """
    if isinstance(candidates, Mapping):
        candidates = candidates.get("candidates")
    ids = _string_id_list(candidates, "candidates")
    if isinstance(ids, dict):
        return ids
    fields = _field_name_list(score_fields, "score_fields")
    if isinstance(fields, dict):
        return fields
    leaves = _field_name_list(score_leaf_fields, "score_leaf_fields")
    if isinstance(leaves, dict):
        return leaves

    def slot(candidate_id: str) -> dict[str, Any]:
        scores: Any = None
        if fields:
            scores = {field: {leaf: None for leaf in leaves} for field in fields}
        return {"candidate_id": candidate_id, "scores": scores}

    return {"ok": True, "skeleton": {"evaluations": [slot(candidate_id) for candidate_id in ids]}}


@_total
def coverage_ledger_skeleton(scope_item_ids: Any) -> dict[str, Any]:
    """Build a coverage-ledger skeleton with one slot per scope item.

    The child assignment of every slot is a None placeholder: which child covers
    which scope item is a judgment, never something this gadget decides.
    """
    ids = _string_id_list(scope_item_ids, "scope_item_ids")
    if isinstance(ids, dict):
        return ids
    return {
        "ok": True,
        "skeleton": {
            "coverage": [
                {"scope_item_id": scope_item_id, "assigned_child_key": None} for scope_item_id in ids
            ]
        },
    }


@_total
def acknowledged_checks_skeleton(computed_check_facts: Any) -> dict[str, Any]:
    """Build the acknowledged_patchwork_checks skeleton from computed check facts.

    Every injected check name becomes a key with a None placeholder. The values
    stay empty on purpose: acknowledgement means the model echoes the computed
    fact itself, so prefilling would defeat the acknowledgement.
    """
    if not isinstance(computed_check_facts, Mapping):
        return _gadget_error("invalid_input", "computed_check_facts must be a mapping")
    if computed_check_facts.get("ok") is False:
        return _gadget_error("facts_report_failed", "computed_check_facts reports ok=False")
    checks = computed_check_facts.get("checks")
    if not isinstance(checks, Mapping) or not all(isinstance(name, str) and name for name in checks):
        return _gadget_error("invalid_input", "computed_check_facts.checks must map non-empty string names")
    return {
        "ok": True,
        "skeleton": {"acknowledged_patchwork_checks": {name: None for name in sorted(checks)}},
    }


def _keyed_list_id_field(value: Any) -> str | None:
    """Return the id field of a keyed list of mappings, or None.

    A keyed list is a non-empty list whose items are all mappings sharing an id
    field ("id" or "*_id") holding unique non-empty strings. Preference order is
    deterministic: exact "id" first, then the alphabetically first "*_id" field.
    """
    if not isinstance(value, list) or not value:
        return None
    if not all(isinstance(item, Mapping) for item in value):
        return None
    candidate_fields = [
        field
        for field in value[0]
        if isinstance(field, str) and (field == "id" or field.endswith("_id"))
    ]
    ordered = (["id"] if "id" in candidate_fields else []) + sorted(
        field for field in candidate_fields if field != "id"
    )
    for field in ordered:
        values = [item.get(field) for item in value]
        if all(isinstance(item, str) and item for item in values) and len(values) == len(set(values)):
            return field
    return None


def _slot_satisfied(skeleton_value: Any, produced_value: Any) -> bool:
    """Structural coverage check: does the produced value fill the skeleton shape?

    Rules (structure only, never content quality — emptiness lint stays with the
    step validators): None placeholder -> present and not None; mapping -> mapping
    covering every skeleton key recursively; keyed list -> list covering every
    skeleton id with satisfied items; plain list -> list; prefilled scalar (ids
    echoed into the skeleton) -> exact equality.
    """
    if skeleton_value is None:
        return produced_value is not None
    if isinstance(skeleton_value, Mapping):
        if not isinstance(produced_value, Mapping):
            return False
        return all(
            key in produced_value and _slot_satisfied(child, produced_value[key])
            for key, child in skeleton_value.items()
        )
    if isinstance(skeleton_value, list):
        id_field = _keyed_list_id_field(skeleton_value)
        if id_field is None:
            return isinstance(produced_value, list)
        if not isinstance(produced_value, list):
            return False
        produced_by_id = _index_keyed_items(produced_value, id_field)
        return all(
            item[id_field] in produced_by_id and _slot_satisfied(item, produced_by_id[item[id_field]])
            for item in skeleton_value
        )
    return produced_value == skeleton_value


def _index_keyed_items(items: list[Any], id_field: str) -> dict[str, Mapping[str, Any]]:
    indexed: dict[str, Mapping[str, Any]] = {}
    for item in items:
        if isinstance(item, Mapping) and isinstance(item.get(id_field), str):
            indexed.setdefault(item[id_field], item)
    return indexed


def _unwrap(value: Any, payload_key: str) -> Any:
    """Accept either a raw payload mapping or a gadget {"ok": True, payload} result."""
    if isinstance(value, Mapping) and value.get("ok") is True and payload_key in value:
        return value[payload_key]
    return value


@_total
def repair_delta(validation_error_report: Any, produced_json: Any, skeleton: Any) -> dict[str, Any]:
    """Compute the minimal fragment request that would repair a produced output.

    Compares the produced output against the skeleton structurally (never by
    parsing error prose) and reports which required slots are missing or
    malformed. The fragment_request carries the untouched skeleton fragments for
    exactly those slots; overwritable_slots lists the malformed slots that a
    later merge is allowed to replace (missing slots need no permission — they
    do not exist yet).
    """
    if not isinstance(validation_error_report, Mapping):
        return _gadget_error("invalid_input", "validation_error_report must be a mapping")
    if not isinstance(produced_json, Mapping):
        return _gadget_error("invalid_input", "produced_json must be a mapping")
    skeleton = _unwrap(skeleton, "skeleton")
    if not isinstance(skeleton, Mapping):
        return _gadget_error("invalid_input", "skeleton must be a mapping")

    errors = validation_error_report.get("errors")
    if isinstance(errors, str):
        errors = [errors]
    if not isinstance(errors, list) or not all(isinstance(item, str) for item in errors):
        errors = []
    validator_id = validation_error_report.get("validator_id")
    if not isinstance(validator_id, str):
        validator_id = ""

    missing_slots: list[str] = []
    malformed_slots: list[str] = []
    fragment_request: dict[str, Any] = {}

    for key, skeleton_value in skeleton.items():
        produced_value = produced_json.get(key)
        id_field = _keyed_list_id_field(skeleton_value)
        if id_field is not None:
            produced_by_id = (
                _index_keyed_items(produced_value, id_field) if isinstance(produced_value, list) else {}
            )
            for skeleton_item in skeleton_value:
                item_id = skeleton_item[id_field]
                slot = f"{key}[{id_field}={item_id}]"
                if item_id not in produced_by_id:
                    missing_slots.append(slot)
                elif _slot_satisfied(skeleton_item, produced_by_id[item_id]):
                    continue
                else:
                    malformed_slots.append(slot)
                fragment_request.setdefault(key, []).append(deepcopy(skeleton_item))
        elif isinstance(skeleton_value, Mapping):
            produced_map = produced_value if isinstance(produced_value, Mapping) else {}
            for name, placeholder in skeleton_value.items():
                slot = f"{key}.{name}"
                if name not in produced_map:
                    missing_slots.append(slot)
                elif _slot_satisfied(placeholder, produced_map[name]):
                    continue
                else:
                    malformed_slots.append(slot)
                fragment_request.setdefault(key, {})[name] = deepcopy(placeholder)
        else:
            if key not in produced_json:
                missing_slots.append(key)
            elif _slot_satisfied(skeleton_value, produced_json[key]):
                continue
            else:
                malformed_slots.append(key)
            fragment_request[key] = deepcopy(skeleton_value)

    return {
        "ok": True,
        "delta": {
            "validator_id": validator_id,
            "errors": list(errors),
            "missing_slots": missing_slots,
            "malformed_slots": malformed_slots,
            "overwritable_slots": list(malformed_slots),
            "fragment_request": fragment_request,
        },
    }


@_total
def merge_fragment(produced_json: Any, fragment: Any, delta: Any = None) -> dict[str, Any]:
    """Merge a model-produced fragment into the produced output, code-side.

    Deterministic fill-only merge: a fragment value may fill a slot that is
    absent, and may overwrite an existing slot only when that slot is listed in
    the delta's overwritable_slots (i.e. it was reported malformed). Already
    present slots outside that list are never clobbered; those attempts are
    reported in skipped_slots. Inputs are never mutated.
    """
    if not isinstance(produced_json, Mapping):
        return _gadget_error("invalid_input", "produced_json must be a mapping")
    if not isinstance(fragment, Mapping):
        return _gadget_error("invalid_input", "fragment must be a mapping")
    delta = _unwrap(delta, "delta")
    overwritable: set[str] = set()
    if delta is not None:
        if not isinstance(delta, Mapping):
            return _gadget_error("invalid_input", "delta must be a mapping when provided")
        listed = delta.get("overwritable_slots", [])
        if not isinstance(listed, list) or not all(isinstance(item, str) for item in listed):
            return _gadget_error("invalid_input", "delta.overwritable_slots must be a list of slot names")
        overwritable = set(listed)

    merged: dict[str, Any] = deepcopy(dict(produced_json))
    applied_slots: list[str] = []
    skipped_slots: list[str] = []

    for key, fragment_value in fragment.items():
        id_field = _keyed_list_id_field(fragment_value)
        if id_field is not None:
            if key not in merged:
                merged[key] = []
            target = merged[key]
            if not isinstance(target, list):
                skipped_slots.extend(f"{key}[{id_field}={item[id_field]}]" for item in fragment_value)
                continue
            for item in fragment_value:
                item_id = item[id_field]
                slot = f"{key}[{id_field}={item_id}]"
                existing_index = next(
                    (
                        index
                        for index, existing in enumerate(target)
                        if isinstance(existing, Mapping) and existing.get(id_field) == item_id
                    ),
                    None,
                )
                if existing_index is None:
                    target.append(deepcopy(item))
                    applied_slots.append(slot)
                elif slot in overwritable:
                    target[existing_index] = deepcopy(item)
                    applied_slots.append(slot)
                else:
                    skipped_slots.append(slot)
        elif isinstance(fragment_value, Mapping):
            if key not in merged:
                merged[key] = {}
            target = merged[key]
            if not isinstance(target, dict):
                skipped_slots.extend(f"{key}.{name}" for name in fragment_value)
                continue
            for name, value in fragment_value.items():
                slot = f"{key}.{name}"
                if name not in target:
                    target[name] = deepcopy(value)
                    applied_slots.append(slot)
                elif slot in overwritable:
                    target[name] = deepcopy(value)
                    applied_slots.append(slot)
                else:
                    skipped_slots.append(slot)
        else:
            if key not in merged:
                merged[key] = deepcopy(fragment_value)
                applied_slots.append(key)
            elif key in overwritable:
                merged[key] = deepcopy(fragment_value)
                applied_slots.append(key)
            else:
                skipped_slots.append(key)

    return {"ok": True, "merged": merged, "applied_slots": applied_slots, "skipped_slots": skipped_slots}
