"""Tests for the gadget hint registry and its review-return / gate attachments.

Ratified contract under test (rustc fix-it pattern): the reviewer's response is
read mechanically AFTER parsing; hints match ONLY typed surfaces (objection
enums, code-emitted error type strings); prose never matches; the reviewer is
never touched; hints are non-binding structure info that cannot alter verdicts,
rounds, or gate decisions.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ai_org import deterministic_structure_gadgets as gadgets
from ai_org.deterministic_structure_gadgets import registry as gadget_registry
from ai_org.patch_author import functional_check
from ai_org.patchwork_queue import receive as receive_module
from ai_org.patchwork_queue import review as review_module
from ai_org.patchwork_queue.field_registry import WORK_ORDER_VIEW_FIELDS

EVALUATION_COVERAGE_ERROR = "Codex candidate evaluation returned incomplete candidate coverage"
PILOT_COVERAGE_ERROR = "select_approach requires one evaluation per candidate from step 5"


def _objection(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "objection_id": "scope-1",
        "anchor_node_ids": ["patch_plan:slices"],
        "axis": "scope",
        "type": "blocking",
        "claim": "The slice bundles two concerns and should be split.",
        "evidence": [],
        "impact": "One slice cannot verify coverage of all scope items.",
        "requested_author_action": "split_scope",
        "resolution_authority": "author",
        "status": "open",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# match_gadget_hints
# ---------------------------------------------------------------------------


def test_match_is_deterministic_and_typed_shape_only():
    surface = _objection()
    first = gadgets.match_gadget_hints(surface)
    second = gadgets.match_gadget_hints(surface)
    assert first == second
    assert first["ok"] is True
    assert len(first["hints"]) == 1
    hint = first["hints"][0]
    assert set(hint) == {"gadget_id", "produces", "matched_on"}
    assert hint["gadget_id"] == "coverage_ledger_skeleton"
    assert hint["matched_on"] == {"field": "requested_author_action", "value": "split_scope"}


def test_prose_only_objection_never_matches():
    # The prose names gadgets and typed values verbatim; the typed fields do not.
    # Mechanical reading means silence here, never a guess.
    surface = _objection(
        requested_author_action="re_explain",
        claim=(
            "Please split_scope this work and use coverage_ledger_skeleton, "
            "acknowledged_patchwork_checks, and the evaluation matrix."
        ),
        impact="patchwork_check_acknowledgement_mismatch prose in impact text.",
    )
    result = gadgets.match_gadget_hints(surface)
    assert result == {"ok": True, "hints": []}


def test_gate_and_validator_typed_error_strings_match():
    gate = gadgets.match_gadget_hints({"gate_error_type": "patchwork_check_acknowledgement_mismatch"})
    assert [hint["gadget_id"] for hint in gate["hints"]] == ["acknowledged_checks_skeleton"]
    validator = gadgets.match_gadget_hints({"validator_error": EVALUATION_COVERAGE_ERROR})
    assert [hint["gadget_id"] for hint in validator["hints"]] == ["evaluation_matrix_skeleton"]
    # The piloted auto-repaired failure is deliberately absent from the registry.
    pilot = gadgets.match_gadget_hints({"validator_error": PILOT_COVERAGE_ERROR})
    assert pilot == {"ok": True, "hints": []}


def test_registry_covers_only_the_three_skeleton_gadgets():
    gadget_ids = {entry["gadget_id"] for entry in gadgets.GADGET_HINT_REGISTRY}
    assert gadget_ids == {
        "coverage_ledger_skeleton",
        "acknowledged_checks_skeleton",
        "evaluation_matrix_skeleton",
    }
    # Every declared match key is a typed surface field; prose fields cannot be declared.
    for entry in gadgets.GADGET_HINT_REGISTRY:
        assert entry["match"]["field"] in gadgets.MATCHABLE_SURFACE_FIELDS


def test_match_and_render_reject_malformed_input_with_typed_errors():
    for bad in (None, 7, "surface", ["list"]):
        result = gadgets.match_gadget_hints(bad)
        assert result["ok"] is False
        assert result["error"]["type"] == "invalid_input"
    for bad_hint in (None, {}, {"gadget_id": "x"}, {"gadget_id": "x", "produces": "y", "matched_on": "z"}):
        rendered = gadgets.render_gadget_hint_line(bad_hint)
        assert rendered["ok"] is False
        assert rendered["error"]["type"] == "invalid_input"


def test_render_line_names_tool_and_structure_only():
    hint = gadgets.match_gadget_hints(_objection())["hints"][0]
    rendered = gadgets.render_gadget_hint_line(hint)
    assert rendered["ok"] is True
    line = rendered["line"]
    assert rendered == gadgets.render_gadget_hint_line(hint)
    assert "coverage_ledger_skeleton" in line
    assert "one coverage slot per scope item" in line
    assert "requested_author_action=split_scope" in line
    assert "\n" not in line


# ---------------------------------------------------------------------------
# Reviewer untouched (constraint 1)
# ---------------------------------------------------------------------------


def test_reviewer_module_and_prompts_are_untouched_by_the_hint_wave():
    # The reviewer side must stay byte-identical: review.py neither imports the
    # gadget package nor mentions hints, and its prompt builders emit no gadget
    # or skeleton framing.
    source = Path(review_module.__file__).read_text(encoding="utf-8")
    assert "deterministic_structure_gadgets" not in source
    assert "gadget" not in source.lower()

    view = {field: "" for field in WORK_ORDER_VIEW_FIELDS}
    prompt = review_module._review_prompt(
        review_module.DIMENSIONS[0],
        view,
        {"problem": {"id": "problem"}},
        {"problem"},
        [],
        "",
    )
    assert "gadget" not in prompt.lower()
    assert "skeleton" not in prompt.lower()


# ---------------------------------------------------------------------------
# Non-binding (constraint 4)
# ---------------------------------------------------------------------------


def test_same_review_response_derives_identical_verdicts_with_and_without_registry(monkeypatch):
    objections = [
        review_module.Objection(
            objection_id="scope-1",
            anchor_node_ids=["patch_plan:slices"],
            axis="scope",
            type="blocking",
            claim="The slice bundles two concerns and should be split.",
            evidence=[],
            impact="One slice cannot verify coverage of all scope items.",
            requested_author_action="split_scope",
        )
    ]
    consolidation = review_module.Consolidation("needs_revision", "summary", objections, [], "", [])

    verdict_with_registry = review_module._derive_verdict(consolidation, objections)
    annotated_with = receive_module._objections_with_gadget_hints([_objection()])

    monkeypatch.setattr(gadget_registry, "GADGET_HINT_REGISTRY", ())

    verdict_without_registry = review_module._derive_verdict(consolidation, objections)
    annotated_without = receive_module._objections_with_gadget_hints([_objection()])

    assert verdict_with_registry == verdict_without_registry == "needs_revision"
    # The optional gadget_hint key is the ONLY delta hints introduce anywhere.
    assert annotated_with[0]["gadget_hint"]["gadget_id"] == "coverage_ledger_skeleton"
    stripped = [{key: value for key, value in item.items() if key != "gadget_hint"} for item in annotated_with]
    assert stripped == annotated_without


# ---------------------------------------------------------------------------
# Attachment point A + rendering in the reform brief
# ---------------------------------------------------------------------------


def test_matched_hint_renders_as_one_structural_line_in_the_reform_brief():
    annotated = receive_module._objections_with_gadget_hints([_objection(), _objection(objection_id="scope-2")])
    context = {
        "review_feedback": annotated,
        **receive_module._review_gadget_hint_lines(annotated),
    }
    # Duplicate hints collapse to one line; the line names the tool and what it
    # generates, nothing about what the answer should be.
    assert len(context["review_gadget_hints"]) == 1
    line = context["review_gadget_hints"][0]
    assert "coverage_ledger_skeleton" in line
    assert "generates" in line

    prompt = receive_module._select_approach_prompt(
        {"candidates": [{"id": "cand_alpha"}]},
        {"evaluations": [{"candidate_id": "cand_alpha"}]},
        {"hard_constraints": [], "soft_preferences": []},
        context,
        {},
    )
    assert line in prompt
    assert '"gadget_hint"' in prompt


def test_unmatched_objections_leave_the_reform_context_shape_unchanged():
    prose_only = [_objection(requested_author_action="re_explain")]
    annotated = receive_module._objections_with_gadget_hints(prose_only)
    assert annotated == prose_only
    assert receive_module._review_gadget_hint_lines(annotated) == {}


# ---------------------------------------------------------------------------
# Attachment point B + no double-hint at the piloted step
# ---------------------------------------------------------------------------


def test_unrepaired_evaluation_coverage_needs_work_carries_the_matrix_hint():
    failure = receive_module._technical_approach_step_failure(
        "candidates", {"ok": False, "error": EVALUATION_COVERAGE_ERROR}
    )
    assert failure["failed_step"] == "candidates"
    assert failure["gadget_hint"]["gadget_id"] == "evaluation_matrix_skeleton"
    assert failure["gadget_hint"]["matched_on"] == {
        "field": "validator_error",
        "value": EVALUATION_COVERAGE_ERROR,
    }


def test_piloted_decision_step_failure_is_never_double_hinted():
    # The decision step already auto-repairs this exact failure; after an
    # exhausted repair round it must surface as plain typed needs_work.
    failure = receive_module._technical_approach_step_failure(
        "decision", {"ok": False, "error": PILOT_COVERAGE_ERROR}
    )
    assert failure == {"ok": False, "error": PILOT_COVERAGE_ERROR, "failed_step": "decision"}


def test_other_step_failures_stay_hint_free():
    failure = receive_module._technical_approach_step_failure(
        "problem", {"ok": False, "error": "Codex problem normalization returned invalid JSON"}
    )
    assert "gadget_hint" not in failure


def test_acknowledgement_blocker_carries_the_acknowledged_checks_hint():
    blocker = functional_check._patchwork_check_acknowledgement_blocker(
        "acknowledged_patchwork_checks is missing"
    )
    assert blocker["where"] == "implementation-result.json"
    assert blocker["why"] == "patchwork_check_acknowledgement_mismatch: acknowledged_patchwork_checks is missing"
    assert blocker["gadget_hint"]["gadget_id"] == "acknowledged_checks_skeleton"
    assert blocker["gadget_hint"]["matched_on"] == {
        "field": "gate_error_type",
        "value": "patchwork_check_acknowledgement_mismatch",
    }
    # The hint is JSON-serializable alongside the blocker (travels in verdicts).
    json.dumps(blocker)
