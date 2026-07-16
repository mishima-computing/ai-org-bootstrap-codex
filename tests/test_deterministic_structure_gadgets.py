"""Unit tests for ai_org.deterministic_structure_gadgets.

The package contract under test: code owns STRUCTURE (ids, slots, placeholders),
the model owns JUDGMENT. Gadgets are pure and total: deterministic skeletons,
structural repair deltas, clobber-safe merges, typed errors instead of exceptions.
"""
from __future__ import annotations

import copy
from typing import Any

from ai_org import deterministic_structure_gadgets as gadgets

SCORE_FIELDS = ("problem_fit", "repo_fit", "risk")
LEAF_FIELDS = ("rating", "reason")


def _skeleton(candidate_ids: list[str]) -> dict[str, Any]:
    result = gadgets.evaluation_matrix_skeleton(
        candidate_ids, score_fields=SCORE_FIELDS, score_leaf_fields=LEAF_FIELDS
    )
    assert result["ok"] is True
    return result["skeleton"]


def _string_values(value: Any) -> set[str]:
    """Collect every string VALUE (not key) anywhere in a structure."""
    if isinstance(value, str):
        return {value}
    if isinstance(value, dict):
        found: set[str] = set()
        for child in value.values():
            found |= _string_values(child)
        return found
    if isinstance(value, list):
        found = set()
        for child in value:
            found |= _string_values(child)
        return found
    return set()


def _leaf_values(value: Any) -> list[Any]:
    if isinstance(value, dict):
        leaves: list[Any] = []
        for child in value.values():
            leaves.extend(_leaf_values(child))
        return leaves
    if isinstance(value, list):
        leaves = []
        for child in value:
            leaves.extend(_leaf_values(child))
        return leaves
    return [value]


# ---------------------------------------------------------------------------
# evaluation_matrix_skeleton
# ---------------------------------------------------------------------------


def test_evaluation_matrix_skeleton_exact_cardinality_and_ids():
    skeleton = _skeleton(["cand_alpha", "cand_beta", "cand_gamma"])
    evaluations = skeleton["evaluations"]
    assert len(evaluations) == 3
    assert [slot["candidate_id"] for slot in evaluations] == ["cand_alpha", "cand_beta", "cand_gamma"]
    for slot in evaluations:
        assert set(slot["scores"]) == set(SCORE_FIELDS)
        for score in slot["scores"].values():
            assert score == {"rating": None, "reason": None}


def test_evaluation_matrix_skeleton_is_deterministic():
    assert _skeleton(["a_id", "b_id"]) == _skeleton(["a_id", "b_id"])


def test_evaluation_matrix_skeleton_contains_no_content_beyond_ids_and_field_names():
    ids = ["cand_alpha", "cand_beta"]
    skeleton = _skeleton(ids)
    # Every string VALUE is an input id; every judgment leaf is a None placeholder.
    assert _string_values(skeleton) == set(ids)
    for leaf in _leaf_values(skeleton):
        assert leaf is None or leaf in ids


def test_evaluation_matrix_skeleton_accepts_step4_candidates_mapping():
    result = gadgets.evaluation_matrix_skeleton(
        {"candidates": [{"id": "cand_alpha", "summary": "prose"}, {"id": "cand_beta"}]},
        score_fields=SCORE_FIELDS,
    )
    assert result["ok"] is True
    slots = result["skeleton"]["evaluations"]
    assert [slot["candidate_id"] for slot in slots] == ["cand_alpha", "cand_beta"]
    # Upstream prose must never leak into the skeleton.
    assert "prose" not in _string_values(result["skeleton"])


def test_evaluation_matrix_skeleton_empty_input_yields_empty_matrix():
    result = gadgets.evaluation_matrix_skeleton([], score_fields=SCORE_FIELDS)
    assert result == {"ok": True, "skeleton": {"evaluations": []}}


def test_evaluation_matrix_skeleton_without_score_fields_uses_none_placeholder():
    result = gadgets.evaluation_matrix_skeleton(["only_id"])
    assert result["ok"] is True
    assert result["skeleton"]["evaluations"] == [{"candidate_id": "only_id", "scores": None}]


def test_evaluation_matrix_skeleton_rejects_duplicates_and_malformed_input():
    duplicate = gadgets.evaluation_matrix_skeleton(["same_id", "same_id"])
    assert duplicate["ok"] is False
    assert duplicate["error"]["type"] == "duplicate_id"

    for bad in (None, 7, "raw-string", [{"no_id": "x"}], [""]):
        result = gadgets.evaluation_matrix_skeleton(bad)
        assert result["ok"] is False
        assert result["error"]["type"] == "invalid_input"

    bad_fields = gadgets.evaluation_matrix_skeleton(["a_id"], score_fields=(1, 2))
    assert bad_fields["ok"] is False
    assert bad_fields["error"]["type"] == "invalid_input"


# ---------------------------------------------------------------------------
# coverage_ledger_skeleton
# ---------------------------------------------------------------------------


def test_coverage_ledger_skeleton_one_slot_per_scope_item_with_empty_assignment():
    result = gadgets.coverage_ledger_skeleton(["scope_1", "scope_2"])
    assert result["ok"] is True
    assert result["skeleton"] == {
        "coverage": [
            {"scope_item_id": "scope_1", "assigned_child_key": None},
            {"scope_item_id": "scope_2", "assigned_child_key": None},
        ]
    }
    assert result == gadgets.coverage_ledger_skeleton(["scope_1", "scope_2"])


def test_coverage_ledger_skeleton_edge_cases():
    assert gadgets.coverage_ledger_skeleton([]) == {"ok": True, "skeleton": {"coverage": []}}
    duplicate = gadgets.coverage_ledger_skeleton(["scope_1", "scope_1"])
    assert duplicate["ok"] is False
    assert duplicate["error"]["type"] == "duplicate_id"
    malformed = gadgets.coverage_ledger_skeleton({"scope": "not-a-sequence"})
    assert malformed["ok"] is False


# ---------------------------------------------------------------------------
# acknowledged_checks_skeleton
# ---------------------------------------------------------------------------


def test_acknowledged_checks_skeleton_keys_every_injected_check_with_placeholder():
    facts = {
        "checks": {
            "counter": {"declared_type": "counter", "current_value": 4, "last_event": None},
            "milestone": {"declared_type": "milestone", "current_value": "reached", "last_event": {}},
        },
        "blocking_edges": [],
    }
    result = gadgets.acknowledged_checks_skeleton(facts)
    assert result["ok"] is True
    # Placeholders stay empty: acknowledgement means the model echoes the computed
    # fact itself, so the gadget must not prefill current_value.
    assert result["skeleton"] == {"acknowledged_patchwork_checks": {"counter": None, "milestone": None}}
    assert result == gadgets.acknowledged_checks_skeleton(facts)


def test_acknowledged_checks_skeleton_rejects_failed_or_malformed_facts():
    failed = gadgets.acknowledged_checks_skeleton({"ok": False, "type": "gate_errors_present", "errors": []})
    assert failed["ok"] is False
    assert failed["error"]["type"] == "facts_report_failed"

    for bad in (None, [], {"checks": "nope"}, {}):
        result = gadgets.acknowledged_checks_skeleton(bad)
        assert result["ok"] is False


# ---------------------------------------------------------------------------
# repair_delta
# ---------------------------------------------------------------------------

REPORT = {
    "validator_id": "receive.select_approach",
    "errors": ["select_approach requires one evaluation per candidate from step 5"],
}


def _filled_evaluation(candidate_id: str) -> dict[str, Any]:
    return {
        "candidate_id": candidate_id,
        "scores": {field: {"rating": "high", "reason": "Judged."} for field in SCORE_FIELDS},
    }


def test_repair_delta_detects_the_missing_slot_from_a_realistic_report():
    skeleton = _skeleton(["cand_alpha", "cand_beta", "cand_gamma"])
    produced = {"evaluations": [_filled_evaluation("cand_alpha"), _filled_evaluation("cand_gamma")]}

    result = gadgets.repair_delta(REPORT, produced, skeleton)
    assert result["ok"] is True
    delta = result["delta"]
    assert delta["validator_id"] == "receive.select_approach"
    assert delta["errors"] == REPORT["errors"]
    assert delta["missing_slots"] == ["evaluations[candidate_id=cand_beta]"]
    assert delta["malformed_slots"] == []
    assert delta["overwritable_slots"] == []
    # The fragment request carries the untouched skeleton fragment for ONLY that slot.
    assert delta["fragment_request"] == {
        "evaluations": [slot for slot in skeleton["evaluations"] if slot["candidate_id"] == "cand_beta"]
    }


def test_repair_delta_reports_malformed_slots_as_overwritable():
    skeleton = _skeleton(["cand_alpha", "cand_beta"])
    broken = _filled_evaluation("cand_beta")
    del broken["scores"]["risk"]
    produced = {"evaluations": [_filled_evaluation("cand_alpha"), broken]}

    delta = gadgets.repair_delta(REPORT, produced, skeleton)["delta"]
    assert delta["missing_slots"] == []
    assert delta["malformed_slots"] == ["evaluations[candidate_id=cand_beta]"]
    assert delta["overwritable_slots"] == ["evaluations[candidate_id=cand_beta]"]
    assert [slot["candidate_id"] for slot in delta["fragment_request"]["evaluations"]] == ["cand_beta"]


def test_repair_delta_with_full_coverage_requests_nothing():
    skeleton = _skeleton(["cand_alpha", "cand_beta"])
    produced = {"evaluations": [_filled_evaluation("cand_alpha"), _filled_evaluation("cand_beta")]}
    delta = gadgets.repair_delta(REPORT, produced, skeleton)["delta"]
    assert delta["missing_slots"] == []
    assert delta["malformed_slots"] == []
    assert delta["fragment_request"] == {}


def test_repair_delta_handles_mapping_slots():
    skeleton = {"acknowledged_patchwork_checks": {"counter": None, "milestone": None}}
    produced = {"acknowledged_patchwork_checks": {"counter": 4}}
    delta = gadgets.repair_delta({}, produced, skeleton)["delta"]
    assert delta["missing_slots"] == ["acknowledged_patchwork_checks.milestone"]
    assert delta["fragment_request"] == {"acknowledged_patchwork_checks": {"milestone": None}}


def test_repair_delta_accepts_wrapped_skeleton_and_rejects_malformed_inputs():
    wrapped = gadgets.evaluation_matrix_skeleton(["cand_alpha"], score_fields=SCORE_FIELDS)
    result = gadgets.repair_delta(REPORT, {"evaluations": []}, wrapped)
    assert result["ok"] is True
    assert result["delta"]["missing_slots"] == ["evaluations[candidate_id=cand_alpha]"]

    for report, produced, skeleton in (
        ("not-a-mapping", {}, {}),
        ({}, "not-a-mapping", {}),
        ({}, {}, "not-a-mapping"),
        ({}, {}, None),
    ):
        result = gadgets.repair_delta(report, produced, skeleton)
        assert result["ok"] is False
        assert result["error"]["type"] == "invalid_input"


def test_repair_delta_does_not_mutate_inputs():
    skeleton = _skeleton(["cand_alpha", "cand_beta"])
    produced = {"evaluations": [_filled_evaluation("cand_alpha")]}
    skeleton_before = copy.deepcopy(skeleton)
    produced_before = copy.deepcopy(produced)
    gadgets.repair_delta(REPORT, produced, skeleton)
    assert skeleton == skeleton_before
    assert produced == produced_before


# ---------------------------------------------------------------------------
# merge_fragment
# ---------------------------------------------------------------------------


def test_merge_fragment_fills_missing_slot_without_touching_valid_ones():
    skeleton = _skeleton(["cand_alpha", "cand_beta"])
    produced = {"evaluations": [_filled_evaluation("cand_alpha")]}
    delta = gadgets.repair_delta(REPORT, produced, skeleton)["delta"]
    fragment = {"evaluations": [_filled_evaluation("cand_beta")]}
    produced_before = copy.deepcopy(produced)

    result = gadgets.merge_fragment(produced, fragment, delta)
    assert result["ok"] is True
    merged = result["merged"]
    assert [item["candidate_id"] for item in merged["evaluations"]] == ["cand_alpha", "cand_beta"]
    assert merged["evaluations"][0] == _filled_evaluation("cand_alpha")
    assert result["applied_slots"] == ["evaluations[candidate_id=cand_beta]"]
    assert result["skipped_slots"] == []
    # Pure: the original produced output is untouched.
    assert produced == produced_before


def test_merge_fragment_cannot_clobber_a_valid_slot_unless_listed_in_the_delta():
    produced = {"evaluations": [_filled_evaluation("cand_alpha")]}
    hijack = _filled_evaluation("cand_alpha")
    hijack["scores"]["risk"]["rating"] = "hijacked"

    unlisted = gadgets.merge_fragment(produced, {"evaluations": [hijack]})
    assert unlisted["ok"] is True
    assert unlisted["merged"] == produced
    assert unlisted["applied_slots"] == []
    assert unlisted["skipped_slots"] == ["evaluations[candidate_id=cand_alpha]"]

    listing = {"overwritable_slots": ["evaluations[candidate_id=cand_alpha]"]}
    listed = gadgets.merge_fragment(produced, {"evaluations": [hijack]}, listing)
    assert listed["ok"] is True
    assert listed["merged"]["evaluations"][0]["scores"]["risk"]["rating"] == "hijacked"
    assert listed["applied_slots"] == ["evaluations[candidate_id=cand_alpha]"]


def test_merge_fragment_mapping_slots_fill_only_missing_keys():
    produced = {"acknowledged_patchwork_checks": {"counter": 4}}
    fragment = {"acknowledged_patchwork_checks": {"counter": 999, "milestone": "reached"}}
    result = gadgets.merge_fragment(produced, fragment)
    assert result["merged"] == {"acknowledged_patchwork_checks": {"counter": 4, "milestone": "reached"}}
    assert result["skipped_slots"] == ["acknowledged_patchwork_checks.counter"]


def test_merge_fragment_rejects_malformed_inputs_with_typed_errors():
    for produced, fragment, delta in (
        ("not-a-mapping", {}, None),
        ({}, "not-a-mapping", None),
        ({}, {}, "not-a-mapping"),
        ({}, {}, {"overwritable_slots": "not-a-list"}),
    ):
        result = gadgets.merge_fragment(produced, fragment, delta)
        assert result["ok"] is False
        assert result["error"]["type"] == "invalid_input"
