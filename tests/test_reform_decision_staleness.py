"""Reform decision-step staleness fix (brief 15): coverage guard, not emptiness.

Proven disease (PULL 5, PULL 6, rescue-1 — identical failures, 2026-07-04):
committed evaluations are generation-stamped by their candidate ids; when reform
regenerated candidates, the old emptiness guard passed a non-empty previous-
generation matrix into _select_approach and the coverage contract failed
deterministically. The fix guards by the gate's total id comparison, prunes
dead-generation evaluations code-side, evaluates ONLY the missing candidates at
normal effort, merges without overwriting surviving evaluations, and stays
fail-closed when coverage is still bad after the delta.
"""
from __future__ import annotations

import copy
import inspect
from typing import Any

import ai_org.log as org_log
from ai_org.patchwork_queue import receive as receive_module


def _evaluation(candidate_id: str) -> dict[str, Any]:
    return {
        "candidate_id": candidate_id,
        "scores": {
            field: {"rating": "high", "reason": "Scored in this generation."}
            for field in receive_module.CANDIDATE_EVALUATION_SCORE_FIELDS
        },
    }


def _components(candidate_ids: list[str], evaluated_ids: list[str]) -> dict[str, Any]:
    # Real reform state: a previously committed approach with a prior decision;
    # the candidates/evaluations slots are then mutated by earlier reform steps.
    components = receive_module._approach_components({})
    components["selected"] = {"selected_candidate_id": evaluated_ids[0] if evaluated_ids else "previous"}
    components["candidates"] = {"candidates": [{"id": candidate_id} for candidate_id in candidate_ids]}
    components["evaluations"] = {"evaluations": [_evaluation(candidate_id) for candidate_id in evaluated_ids]}
    return components


def _install_fakes(monkeypatch, evaluate_calls: list[list[str]], select_seen: dict[str, Any]):
    def fake_evaluate_candidates(
        candidates, normalized_problem, constraints, context=None, accumulated_approach=None, *, log_ctx=None
    ):
        ids = [candidate["id"] for candidate in candidates["candidates"]]
        evaluate_calls.append(ids)
        return {"evaluations": [_evaluation(candidate_id) for candidate_id in ids]}

    def fake_select_approach(
        candidates, evaluations, constraints, context=None, accumulated_approach=None, *, log_ctx=None
    ):
        select_seen["evaluations"] = evaluations
        select_seen["log_ctx"] = log_ctx
        return {"selected_candidate_id": candidates["candidates"][0]["id"]}

    monkeypatch.setattr(receive_module, "_evaluate_candidates", fake_evaluate_candidates)
    monkeypatch.setattr(receive_module, "_select_approach", fake_select_approach)


def test_regenerated_candidates_prune_strays_and_evaluate_only_the_new_generation(monkeypatch, tmp_path):
    # The exact PULL5/6/rescue-1 shape: three fresh LLM-named candidate ids meet a
    # non-empty previous-generation matrix. The old trio must be pruned code-side
    # and the evaluation call must receive ONLY the new generation.
    components = _components(
        ["lit_svg_jrpg", "pixi_dom_jrpg", "react_canvas_jrpg"],
        ["old_minimal", "old_repo_native", "old_general"],
    )
    evaluate_calls: list[list[str]] = []
    select_seen: dict[str, Any] = {}
    _install_fakes(monkeypatch, evaluate_calls, select_seen)

    result = receive_module._run_reform_step("decision", tmp_path, {}, components, {"repo": tmp_path})

    assert result["selected"] == {"selected_candidate_id": "lit_svg_jrpg"}
    assert evaluate_calls == [["lit_svg_jrpg", "pixi_dom_jrpg", "react_canvas_jrpg"]]
    merged_ids = [item["candidate_id"] for item in select_seen["evaluations"]["evaluations"]]
    assert sorted(merged_ids) == ["lit_svg_jrpg", "pixi_dom_jrpg", "react_canvas_jrpg"]
    assert not any(candidate_id.startswith("old_") for candidate_id in merged_ids)
    # Brief 17: the exact matrix the selection consumed travels in the step result
    # so the reformed tree can persist the decision's auditable evaluation basis.
    assert result["evaluations"] is select_seen["evaluations"]


def test_partial_overlap_reuses_the_surviving_evaluation_byte_identical(monkeypatch, tmp_path):
    components = _components(["kept", "fresh_b", "fresh_c"], ["kept", "dead_x"])
    original_kept = copy.deepcopy(components["evaluations"]["evaluations"][0])
    evaluate_calls: list[list[str]] = []
    select_seen: dict[str, Any] = {}
    _install_fakes(monkeypatch, evaluate_calls, select_seen)

    result = receive_module._run_reform_step("decision", tmp_path, {}, components, {"repo": tmp_path})

    assert result["selected"]["selected_candidate_id"] == "kept"
    # Only the ids lacking an evaluation are evaluated; the survivor is never re-run.
    assert evaluate_calls == [["fresh_b", "fresh_c"]]
    merged = {item["candidate_id"]: item for item in select_seen["evaluations"]["evaluations"]}
    assert set(merged) == {"kept", "fresh_b", "fresh_c"}
    # The surviving evaluation is reused byte-identical, never overwritten.
    assert merged["kept"] == original_kept


def test_unchanged_candidate_generation_short_circuits_without_any_evaluation_call(monkeypatch, tmp_path):
    # Same id set (order-independent): byte-identical prior behavior, zero LLM work.
    components = _components(["alpha", "beta"], ["beta", "alpha"])
    select_seen: dict[str, Any] = {}

    def forbidden_evaluate(*args, **kwargs):
        raise AssertionError("evaluation must not run when coverage already matches")

    def fake_select_approach(
        candidates, evaluations, constraints, context=None, accumulated_approach=None, *, log_ctx=None
    ):
        select_seen["evaluations"] = evaluations
        return {"selected_candidate_id": "alpha"}

    monkeypatch.setattr(receive_module, "_evaluate_candidates", forbidden_evaluate)
    monkeypatch.setattr(receive_module, "_select_approach", fake_select_approach)

    result = receive_module._run_reform_step("decision", tmp_path, {}, components, {"repo": tmp_path})

    assert result["selected"] == {"selected_candidate_id": "alpha"}
    # Coverage matched: the committed matrix object passes through untouched, both
    # into the selection and out as the persisted step result.
    assert select_seen["evaluations"] is components["evaluations"]
    assert result["evaluations"] is components["evaluations"]


def test_coverage_still_bad_after_delta_is_typed_needs_work(monkeypatch, tmp_path):
    # The delta evaluation returns a wrong-generation id: fail-closed, no selection.
    components = _components(["alpha", "beta"], ["alpha"])
    evaluate_calls: list[list[str]] = []

    def fake_evaluate_candidates(
        candidates, normalized_problem, constraints, context=None, accumulated_approach=None, *, log_ctx=None
    ):
        evaluate_calls.append([candidate["id"] for candidate in candidates["candidates"]])
        return {"evaluations": [_evaluation("wrong_generation_id")]}

    def forbidden_select(*args, **kwargs):
        raise AssertionError("selection must not run on uncovered evaluations")

    monkeypatch.setattr(receive_module, "_evaluate_candidates", fake_evaluate_candidates)
    monkeypatch.setattr(receive_module, "_select_approach", forbidden_select)

    result = receive_module._run_reform_step("decision", tmp_path, {}, components, {"repo": tmp_path})

    assert result == {
        "ok": False,
        "error": "reform decision evaluations do not cover the current candidate generation",
        "failed_step": "decision",
    }
    assert evaluate_calls == [["beta"]]


def test_reform_decision_threads_the_pull_run_context(monkeypatch, tmp_path):
    # run-20260704T141927Z ran the repair round as an orphan run dir behind an
    # adapter_boundary warning: reform's codex calls must carry the caller's ctx.
    components = _components(["alpha"], ["alpha"])
    select_seen: dict[str, Any] = {}

    def fake_select_approach(
        candidates, evaluations, constraints, context=None, accumulated_approach=None, *, log_ctx=None
    ):
        select_seen["log_ctx"] = log_ctx
        return {"selected_candidate_id": "alpha"}

    monkeypatch.setattr(receive_module, "_select_approach", fake_select_approach)
    ctx = org_log.RunContext(repo=tmp_path, run_id="run-reform-ctx-test")

    receive_module._run_reform_step("decision", tmp_path, {}, components, {"repo": tmp_path}, log_ctx=ctx)

    assert select_seen["log_ctx"] is ctx
    # The reform entrypoint accepts the pull's ctx (wired via _call_with_optional_ctx).
    assert "ctx" in inspect.signature(receive_module.reform_patch_series).parameters
