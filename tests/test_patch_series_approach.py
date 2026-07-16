from __future__ import annotations

import concurrent.futures
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import threading
import time

import pytest

from ai_org import git_wrapper, patch_series_bodies
from ai_org.body_codec import BodyCodecClient
from ai_org.patchwork_queue import field_registry
from ai_org.patchwork_queue import receive as receive_module


ORIGINAL_START_BACKGROUND_BUILD = receive_module.engineering_precedent_store.start_background_build


@pytest.fixture(autouse=True)
def default_no_background_precedent_build(monkeypatch):
    future: concurrent.futures.Future[dict[str, object]] = concurrent.futures.Future()
    future.set_result({"terms": {}, "processed_terms": [], "expanded": [], "hits": [], "failed": {}})
    monkeypatch.setattr(receive_module.engineering_precedent_store, "start_background_build", lambda *args, **kwargs: future)


def test_form_technical_approach_builds_derivation_tree(monkeypatch, tmp_path):
    calls = []
    approaches = {}

    monkeypatch.setattr(
        receive_module,
        "_normalize_problem",
        lambda patch_series_view, context=None: calls.append("normalize_problem") or _normalized_problem(),
    )
    def fake_build_from_patch_series(patch_series_view, context=None, **kwargs):
        assert kwargs == {"kinds": ("design",)}
        calls.append("build_from_patch_series")
        return {"terms": {}, "processed_terms": ["battle loop"], "expanded": [], "hits": [], "failed": {}}

    monkeypatch.setattr(receive_module.engineering_precedent_store, "build_from_patch_series", fake_build_from_patch_series)

    def fake_constraints(patch_series_view, repo, context=None, approach=None):
        calls.append(("extract_constraints", approach))
        approaches["extract_constraints"] = approach
        assert "problem" in approach
        assert "normalized_problem" not in approach
        return _constraints()

    def fake_prior_art(patch_series_view, repo, context=None, approach=None, reference_terms=None):
        calls.append(("build_prior_art_map", approach))
        approaches["build_prior_art_map"] = approach
        assert reference_terms == ["battle loop"]
        assert approach["problem"]["constraints"]["hard"][0]["id"] == "constraint:hard:1"
        return _prior_art_map()

    def fake_generate_candidates(normalized, constraints, prior_art, context=None, accumulated_approach=None):
        calls.append("generate_candidates")
        approaches["generate_candidates"] = accumulated_approach
        return _candidates()

    def fake_evaluate_candidates(candidates, normalized, constraints, context=None, accumulated_approach=None):
        calls.append("evaluate_candidates")
        approaches["evaluate_candidates"] = accumulated_approach
        return _evaluations()

    def fake_select_approach(candidates, evaluations, constraints, context=None, accumulated_approach=None):
        calls.append("select_approach")
        approaches["select_approach"] = accumulated_approach
        return _decision()

    def fake_implementation_strategy(
        chosen,
        prior_art,
        constraints,
        patch_series_view,
        repo,
        context=None,
        accumulated_approach=None,
    ):
        calls.append("implementation_strategy")
        approaches["implementation_strategy"] = accumulated_approach
        return _implementation()

    def fake_right_size_patch_plan(
        chosen,
        implementation,
        constraints,
        context=None,
        accumulated_approach=None,
    ):
        calls.append("right_size_patch_plan")
        approaches["right_size_patch_plan"] = accumulated_approach
        return _patch_plan()

    def fake_domain_specification(
        profile_facets,
        reference_lookups,
        implementation,
        constraints,
        patch_series_view,
        context=None,
        accumulated_approach=None,
    ):
        calls.append("domain_specification")
        approaches["domain_specification"] = accumulated_approach
        return _domain_specification()

    def fake_surface_risks(
        chosen,
        implementation,
        patch_plan,
        constraints,
        context=None,
        accumulated_approach=None,
    ):
        calls.append("surface_risks")
        approaches["surface_risks"] = accumulated_approach
        return _risks()

    monkeypatch.setattr(receive_module, "_extract_constraints", fake_constraints)
    monkeypatch.setattr(receive_module, "_build_prior_art_map", fake_prior_art)
    monkeypatch.setattr(receive_module, "_generate_candidates", fake_generate_candidates)
    monkeypatch.setattr(receive_module, "_evaluate_candidates", fake_evaluate_candidates)
    monkeypatch.setattr(receive_module, "_select_approach", fake_select_approach)
    monkeypatch.setattr(receive_module, "_implementation_strategy", fake_implementation_strategy)
    monkeypatch.setattr(receive_module, "_domain_specification", fake_domain_specification)
    monkeypatch.setattr(receive_module, "_right_size_patch_plan", fake_right_size_patch_plan)
    monkeypatch.setattr(receive_module, "_surface_risks", fake_surface_risks)

    result = receive_module.form_technical_approach(_patch_series_view(), tmp_path)

    assert result["ok"] is True
    assert "approach" not in result
    assert calls[0] == "normalize_problem"
    assert calls[2] == "build_from_patch_series"
    assert calls[3][0] == "build_prior_art_map"
    assert calls[-1] == "surface_risks"
    for step in (
        "extract_constraints",
        "build_prior_art_map",
        "generate_candidates",
        "evaluate_candidates",
        "select_approach",
        "implementation_strategy",
        "domain_specification",
        "right_size_patch_plan",
        "surface_risks",
    ):
        assert approaches[step]["problem"]["goals"][0]["verification"]["check"] == (
            "Assert Spark defeats Slime and sets meadow_gate_open true."
        )
    assert approaches["generate_candidates"]["problem"]["prior_art"][0]["id"] == (
        "prior_art:reference-first-prior-art-synthesis"
    )
    assert approaches["evaluate_candidates"]["problem"]["question"]["candidates"][0]["id"] == "minimal"
    assert approaches["select_approach"]["problem"]["question"]["candidates"][1]["evaluation"]["id"] == (
        "evaluation:repo_native"
    )
    assert approaches["implementation_strategy"]["problem"]["question"]["decision"]["id"] == "decision:repo_native"
    assert approaches["domain_specification"]["problem"]["question"]["decision"]["implementation"]["systems"][0][
        "system_name"
    ] == "Battle loop"
    assert approaches["right_size_patch_plan"]["problem"]["question"]["decision"]["implementation"]["systems"][0][
        "system_name"
    ] == "Battle loop"
    assert approaches["surface_risks"]["problem"]["question"]["decision"]["implementation"]["patch_plan"][
        "first_proof_moment"
    ]["how_verified"] == "Run the battle-loop test and assert meadow_gate_open."

    tree = result["technical_approach"]
    assert set(tree) == {"problem", "cross_links"}
    problem = tree["problem"]
    assert problem["id"] == "problem"
    assert problem["goals"][0]["id"] == "goal:1"
    assert problem["constraints"]["hard"][0]["id"] == "constraint:hard:1"
    assert problem["constraints"]["soft"][0]["id"] == "constraint:soft:1"
    assert problem["prior_art"][0]["id"] == "prior_art:reference-first-prior-art-synthesis"

    question = problem["question"]
    assert question["id"] == "question:approach"
    assert [candidate["id"] for candidate in question["candidates"]] == ["minimal", "repo_native"]
    repo_candidate = question["candidates"][1]
    assert repo_candidate["evaluation"]["id"] == "evaluation:repo_native"
    assert repo_candidate["evaluation"]["scores"]["problem_fit"]["rating"] == "high"
    assert repo_candidate["evaluation"]["arguments"][0]["role"] == "support"
    assert repo_candidate["risks"][0]["id"] == "risk:candidate"

    decision = question["decision"]
    assert decision["id"] == "decision:repo_native"
    assert decision["selected_candidate_id"] == "repo_native"
    assert decision["rejected"] == [{"candidate_id": "minimal", "objection": "Lower problem fit."}]
    assert decision["implementation"]["id"] == "implementation:repo_native"
    assert decision["implementation"]["patch_plan"]["id"] == "patch_plan:repo_native"
    assert decision["implementation"]["risks"][0]["id"] == "risk:implementation"

    link_types = {link["type"] for link in tree["cross_links"]}
    assert link_types <= set(receive_module.CROSS_LINK_TYPES)
    assert {"from": "repo_native", "to": "question:approach", "type": "depends_on"} in tree["cross_links"]
    assert {"from": "implementation:repo_native", "to": "decision:repo_native", "type": "implements"} in tree[
        "cross_links"
    ]
    assert decision["implementation"]["domain_specification"]["aspects"][0]["id"] == "domain_specification:battle-numbers"
    assert {"from": "risk:implementation", "to": "implementation:repo_native", "type": "mitigates"} in tree[
        "cross_links"
    ]


def test_form_technical_approach_preserves_step_open_questions(monkeypatch, tmp_path):
    normalized_question = "Confirm whether the first battle should tune Spark damage before broader balance work."
    decision_question = "Validate later whether repo-native rendering remains preferable if art scope expands."
    patch_question = "Decide after the first slice whether the full campaign map belongs in this patch series."

    normalized = _normalized_problem()
    normalized["open_questions"] = [normalized_question]
    decision = _decision()
    decision["open_questions"] = [decision_question, normalized_question]
    patch_plan = _patch_plan()
    patch_plan["open_questions"] = [patch_question]

    monkeypatch.setattr(receive_module, "_normalize_problem", lambda patch_series_view, context=None: normalized)
    monkeypatch.setattr(receive_module.engineering_precedent_store, "build_from_patch_series", lambda *args, **kwargs: {"processed_terms": []})
    monkeypatch.setattr(receive_module.engineering_precedent_store, "start_background_build", lambda *args, **kwargs: object())
    monkeypatch.setattr(receive_module, "_extract_constraints", lambda *args, **kwargs: _constraints())
    monkeypatch.setattr(receive_module, "_build_prior_art_map", lambda *args, **kwargs: _prior_art_map())
    monkeypatch.setattr(receive_module, "_generate_candidates", lambda *args, **kwargs: _candidates())
    monkeypatch.setattr(receive_module, "_evaluate_candidates", lambda *args, **kwargs: _evaluations())
    monkeypatch.setattr(receive_module, "_select_approach", lambda *args, **kwargs: decision)
    monkeypatch.setattr(receive_module, "_implementation_strategy", lambda *args, **kwargs: _implementation())
    monkeypatch.setattr(receive_module, "_domain_specification", lambda *args, **kwargs: _domain_specification())
    monkeypatch.setattr(receive_module, "_right_size_patch_plan", lambda *args, **kwargs: patch_plan)
    monkeypatch.setattr(receive_module, "_surface_risks", lambda *args, **kwargs: _risks())

    result = receive_module.form_technical_approach(_patch_series_view(), tmp_path)

    assert result["ok"] is True
    assert result["technical_approach"]["problem"]["open_questions"] == [
        normalized_question,
        decision_question,
        patch_question,
    ]


def test_tech_stack_provenance_controls_candidate_generation(monkeypatch, tmp_path):
    patch_series = _patch_series_view()
    patch_series["tech_stack"] = {
        **patch_series["tech_stack"],
        "build_strategy": "framework_based",
        "framework": "repo-native Python modules",
        "language": "Python",
        "platform": "CLI",
        "rationale": "The requester specified the repository-native Python stack.",
        "provenance": "requester_specified",
    }
    calls = []

    monkeypatch.setattr(receive_module, "_normalize_problem", lambda *args, **kwargs: _normalized_problem())
    monkeypatch.setattr(receive_module, "_extract_constraints", lambda *args, **kwargs: _constraints())
    monkeypatch.setattr(receive_module.engineering_precedent_store, "build_from_patch_series", lambda *args, **kwargs: {"processed_terms": []})
    monkeypatch.setattr(receive_module, "_build_prior_art_map", lambda *args, **kwargs: _prior_art_map())
    monkeypatch.setattr(
        receive_module,
        "_generate_candidates",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("requester stack should skip candidates")),
    )
    monkeypatch.setattr(receive_module, "_implementation_strategy", lambda *args, **kwargs: _implementation())
    monkeypatch.setattr(receive_module, "_domain_specification", lambda *args, **kwargs: _domain_specification())
    monkeypatch.setattr(receive_module, "_right_size_patch_plan", lambda *args, **kwargs: _patch_plan())
    monkeypatch.setattr(receive_module, "_surface_risks", lambda *args, **kwargs: {"risks": []})

    def fake_select(candidates, evaluations, constraints, context=None, accumulated_approach=None):
        calls.append((candidates, evaluations))
        return _decision()

    monkeypatch.setattr(receive_module, "_select_approach", fake_select)

    result = receive_module.form_technical_approach(patch_series, tmp_path)

    assert result["ok"] is True
    assert "provided_approach" in result["steps"]
    assert calls == []


def test_unspecified_tech_stack_is_ai_deliberated_after_generated_selection(monkeypatch, tmp_path):
    patch_series = _patch_series_view()
    _patch_successful_approach_steps(monkeypatch)

    result = receive_module.form_technical_approach(patch_series, tmp_path, skip_precedent_build=True, reference_terms=[])

    assert result["ok"] is True
    decision = result["technical_approach"]["problem"]["question"]["decision"]
    assert set(decision["stack_axes"]) == set(receive_module.STACK_DECISION_AXIS_FIELDS)
    for axis in receive_module.STACK_DECISION_AXIS_FIELDS:
        assert decision["stack_axes"][axis]["evidence"]
        assert decision["stack_axes"][axis]["judgment"]
    assert patch_series["tech_stack"]["build_strategy"] == "framework_based"
    assert patch_series["tech_stack"]["framework"] == "Repo Native"
    assert patch_series["tech_stack"]["engine"] == ""
    assert patch_series["tech_stack"]["platform"] == "browser"
    assert patch_series["tech_stack"]["provenance"] == "ai_deliberated"
    assert patch_series["tech_stack"]["rationale"]


def test_stack_decision_empty_axis_retries(monkeypatch, tmp_path):
    invalid_axes = _stack_axes()
    invalid_axes["fidelity_precedent"] = {"evidence": "", "judgment": ""}
    attempts = [
        {**_decision(), "stack_axes": invalid_axes},
        _decision(),
    ]
    prompts = []

    def fake_run_json(repo: Path, **kwargs):
        prompts.append(kwargs["prompt"])
        return {"ok": True, "raw": json.dumps(attempts.pop(0))}

    monkeypatch.setattr(receive_module.codex_exec, "run_json", fake_run_json)

    result = receive_module._select_approach(
        _candidates(),
        _evaluations(),
        _constraints(),
        {"repo": tmp_path},
        _accumulated_approach_through_patch_plan(),
    )

    assert result == _decision()
    assert len(prompts) == 2
    assert "stack_axes.fidelity_precedent.evidence is empty" in prompts[1]


def test_candidate_precedent_fields_are_schema_enums(monkeypatch, tmp_path):
    evaluation_schemas = []

    def fake_evaluate_run_json(repo: Path, **kwargs):
        schema = kwargs["schema"]
        evaluation_schemas.append(schema)
        candidate_id = schema["properties"]["candidate_id"]["enum"][0]
        return {"ok": True, "raw": json.dumps({"candidate_id": candidate_id, "scores": _scores()})}

    monkeypatch.setattr(receive_module.codex_exec, "run_json", fake_evaluate_run_json)

    evaluations = receive_module._evaluate_candidates(
        _candidates(),
        _normalized_problem(),
        _constraints(),
        {"repo": tmp_path},
        _accumulated_approach_through_patch_plan(),
    )

    assert [item["candidate_id"] for item in evaluations["evaluations"]] == ["minimal", "repo_native"]
    assert [schema["properties"]["candidate_id"]["enum"] for schema in evaluation_schemas] == [["minimal"], ["repo_native"]]

    select_schemas = []

    def fake_select_run_json(repo: Path, **kwargs):
        schema = kwargs["schema"]
        select_schemas.append(schema)
        return {"ok": True, "raw": json.dumps(_decision())}

    monkeypatch.setattr(receive_module.codex_exec, "run_json", fake_select_run_json)

    selected = receive_module._select_approach(
        _candidates(),
        _evaluations(),
        _constraints(),
        {"repo": tmp_path},
        _accumulated_approach_through_patch_plan(),
    )

    candidate_ids = ["minimal", "repo_native"]
    schema = select_schemas[0]
    assert selected["selected_candidate_id"] == "repo_native"
    assert schema["properties"]["selected_candidate_id"]["enum"] == candidate_ids
    assert schema["properties"]["arguments"]["items"]["properties"]["about_candidate_id"]["enum"] == candidate_ids
    assert schema["properties"]["rejected"]["items"]["properties"]["candidate_id"]["enum"] == candidate_ids


def test_surface_risks_schema_enums_real_tree_node_ids(monkeypatch, tmp_path):
    schemas = []

    def fake_run_json(repo: Path, **kwargs):
        schemas.append(kwargs["schema"])
        return {
            "ok": True,
            "raw": json.dumps(
                {
                    "risks": [
                        {
                            "id": "risk:patch-plan",
                            "risk": "The first proof moment may overrun the safe slice.",
                            "mitigation": "Keep follow-up content out of the first patch.",
                            "attaches_to": "patch_plan",
                            "target_id": "patch_plan:repo_native",
                        }
                    ]
                }
            ),
        }

    monkeypatch.setattr(receive_module.codex_exec, "run_json", fake_run_json)

    result = receive_module._surface_risks(
        _decision(),
        _implementation(),
        _patch_plan(),
        _constraints(),
        {"repo": tmp_path},
        _accumulated_approach_through_patch_plan(),
    )

    target_schema = schemas[0]["properties"]["risks"]["items"]["properties"]["target_id"]
    assert result["risks"][0]["target_id"] == "patch_plan:repo_native"
    assert target_schema["enum"] == [
        "decision:repo_native",
        "implementation:repo_native",
        "minimal",
        "patch_plan:repo_native",
        "repo_native",
    ]
    assert "patch_plan:vanilla_web_repo_adventure" not in target_schema["enum"]


def test_surface_risks_over_bound_fallback_prompts_ids_and_lint_catches_dangling(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(receive_module, "MAX_SCHEMA_ENUM_REFERENCES", 1)
    monkeypatch.setattr(receive_module, "_normalize_problem", lambda patch_series_view, context=None: _normalized_problem())
    monkeypatch.setattr(receive_module, "_extract_constraints", lambda *args, **kwargs: _constraints())
    monkeypatch.setattr(
        receive_module.engineering_precedent_store,
        "build_from_patch_series",
        lambda *args, **kwargs: {"terms": {}, "processed_terms": ["battle loop"], "expanded": [], "hits": [], "failed": {}},
    )
    monkeypatch.setattr(receive_module, "_build_prior_art_map", lambda *args, **kwargs: _prior_art_map())
    monkeypatch.setattr(receive_module, "_generate_candidates", lambda *args, **kwargs: _candidates())
    monkeypatch.setattr(receive_module, "_evaluate_candidates", lambda *args, **kwargs: _evaluations())
    monkeypatch.setattr(receive_module, "_select_approach", lambda *args, **kwargs: _decision())
    monkeypatch.setattr(receive_module, "_implementation_strategy", lambda *args, **kwargs: _implementation())
    monkeypatch.setattr(receive_module, "_domain_specification", lambda *args, **kwargs: _domain_specification())
    monkeypatch.setattr(receive_module, "_right_size_patch_plan", lambda *args, **kwargs: _patch_plan())

    prompts = []
    schemas = []

    def fake_run_json(repo: Path, **kwargs):
        prompts.append(kwargs["prompt"])
        schemas.append(kwargs["schema"])
        return {
            "ok": True,
            "raw": json.dumps(
                {
                    "risks": [
                        {
                            "id": "risk:lost",
                            "risk": "Detached risk.",
                            "mitigation": "Attach to a known parent.",
                            "attaches_to": "implementation",
                            "target_id": "implementation:invented",
                        }
                    ]
                }
            ),
        }

    monkeypatch.setattr(receive_module.codex_exec, "run_json", fake_run_json)

    result = receive_module.form_technical_approach(_patch_series_view(), tmp_path)

    target_schema = schemas[0]["properties"]["risks"]["items"]["properties"]["target_id"]
    assert "enum" not in target_schema
    assert "Valid risk target_id values are a closed list" in prompts[0]
    assert '"implementation:repo_native"' in prompts[0]
    assert result["ok"] is False
    assert result["failed_step"] == "surface_risks"
    assert "targets unknown implementation node implementation:invented" in result["error"]


def test_requester_specified_stack_skips_alternatives(monkeypatch, tmp_path):
    patch_series = _patch_series_view()
    patch_series["raw_request"] = "Make the battle slice in Unreal Engine 5."
    patch_series["proposal_hint"] = "Use Unreal Engine 5."
    patch_series["tech_stack"] = {
        "build_strategy": "engine_based",
        "engine": "Unreal Engine 5",
        "framework": "",
        "language": "Blueprint and C++",
        "platform": "desktop",
        "rationale": "The requester explicitly specified Unreal Engine 5.",
        "provenance": "requester_specified",
    }

    monkeypatch.setattr(receive_module, "_normalize_problem", lambda *args, **kwargs: _normalized_problem())
    monkeypatch.setattr(receive_module, "_extract_constraints", lambda *args, **kwargs: _constraints())
    monkeypatch.setattr(receive_module.engineering_precedent_store, "build_from_patch_series", lambda *args, **kwargs: {"processed_terms": []})
    monkeypatch.setattr(receive_module, "_build_prior_art_map", lambda *args, **kwargs: _prior_art_map())
    monkeypatch.setattr(
        receive_module,
        "_generate_candidates",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("requester stack should skip alternatives")),
    )
    monkeypatch.setattr(receive_module, "_implementation_strategy", lambda *args, **kwargs: _implementation())
    monkeypatch.setattr(receive_module, "_domain_specification", lambda *args, **kwargs: _domain_specification())
    monkeypatch.setattr(receive_module, "_right_size_patch_plan", lambda *args, **kwargs: _patch_plan())
    monkeypatch.setattr(receive_module, "_surface_risks", lambda *args, **kwargs: {"risks": []})

    result = receive_module.form_technical_approach(patch_series, tmp_path)

    assert result["ok"] is True
    assert "generate_candidates" not in result["steps"]
    decision = result["technical_approach"]["problem"]["question"]["decision"]
    assert decision["selected_candidate_id"] == "provided_approach"
    assert [candidate["id"] for candidate in result["technical_approach"]["problem"]["question"]["candidates"]] == [
        "provided_approach"
    ]
    assert all(
        risk.get("id") != "risk:requester_stack_org_profile_conflict"
        for risk in decision.get("risks", [])
    )


def test_candidate_platform_rejects_org_internal_verifier_vocabulary(monkeypatch, tmp_path):
    invalid = _candidate("bad_platform", "minimal_local", "Bad Platform")
    invalid["stack_requirement"] = {
        **invalid["stack_requirement"],
        "platform": "headless functional_check target",
    }
    fixed = _candidate("browser_platform", "minimal_local", "Browser Platform")
    outputs = [
        invalid,
        fixed,
        _candidate("feasible_b", "repo_native", "Feasible B"),
        _candidate("feasible_c", "general_architectural", "Feasible C"),
    ]
    prompts = []

    def fake_run_json(repo: Path, **kwargs):
        prompts.append(kwargs["prompt"])
        return {"ok": True, "raw": json.dumps(outputs.pop(0))}

    monkeypatch.setattr(receive_module.codex_exec, "run_json", fake_run_json)

    result = receive_module._generate_candidates(
        _normalized_problem(),
        _constraints(),
        _prior_art_map(),
        {"repo": tmp_path},
    )

    assert [candidate["id"] for candidate in result["candidates"]] == [
        "browser_platform",
        "feasible_b",
        "feasible_c",
    ]
    assert result["candidates"][0]["stack_requirement"]["platform"] == "browser"
    assert len(prompts) == 4
    assert "platform must name the user-facing runtime target" in prompts[1]


# ---------------------------------------------------------------------------
# Brief 34: the org is a general artifact engine — deliverable-kind assumptions
# live in DATA (UX applicability), never in validators. Live kill: the
# body-format spec run (request_type=research, not_user_facing) honestly wrote
# platform "git worktree text files" and the product-app assumption rejected it
# permanently (fingerprint c34426f8ea41ffc8, 2026-07-05).
# ---------------------------------------------------------------------------


def _spec_candidate(candidate_id: str, kind: str, name: str) -> dict[str, object]:
    candidate = _candidate(candidate_id, kind, name)
    candidate["stack_requirement"] = {
        "build_strategy": "from_scratch",
        "engine": "",
        "framework": "",
        "language": "Markdown with JSON examples",
        # The LIVE honest platform string the validator killed.
        "platform": "git worktree text files",
        "authoring_model": "text_first",
        "verification_model": "headless_ci",
    }
    return candidate


def test_not_user_facing_series_accepts_the_live_spec_platform(monkeypatch, tmp_path):
    outputs = [
        _spec_candidate("body_grammar_spec", "minimal_local", "Body Grammar Spec"),
        _spec_candidate("projection_spec", "repo_native", "Projection Spec"),
        _spec_candidate("hybrid_spec", "general_architectural", "Hybrid Spec"),
    ]
    prompts = []

    def fake_run_json(repo: Path, **kwargs):
        prompts.append(kwargs["prompt"])
        return {"ok": True, "raw": json.dumps(outputs.pop(0))}

    monkeypatch.setattr(receive_module.codex_exec, "run_json", fake_run_json)

    result = receive_module._generate_candidates(
        _normalized_problem(),
        _constraints(),
        _prior_art_map(),
        {"repo": tmp_path, "deliverable_user_facing": False},
    )

    # The live string passes first try: no retry, no pruning, no permanent kill.
    assert [candidate["id"] for candidate in result["candidates"]] == [
        "body_grammar_spec",
        "projection_spec",
        "hybrid_spec",
    ]
    assert "pruned_candidates" not in result
    assert len(prompts) == 3
    # The prompt guidance is mode-scoped too (probe-verified wording).
    assert "deliverable's own target surface, such as git-committed specification" in prompts[0]
    assert "user-facing runtime target such as browser or lightweight desktop" not in prompts[0]


def test_platform_org_vocabulary_scoping_both_modes_and_both_directions():
    def candidate_with(platform: str) -> dict[str, object]:
        candidate = _spec_candidate("probe", "minimal_local", "Probe")
        candidate["stack_requirement"] = {**candidate["stack_requirement"], "platform": platform}
        return candidate

    lint = receive_module._lint_candidate_stack_requirement
    # not_user_facing: the deliverable's own surface passes; the org's own gate
    # and carrier stay rejected (the guard's original purpose, narrower).
    assert lint(candidate_with("git worktree text files"), user_facing=False) == []
    assert lint(candidate_with("git-committed specification documents"), user_facing=False) == []
    assert lint(candidate_with("headless functional_check target"), user_facing=False) != []
    assert lint(candidate_with("codex-authored spec docs"), user_facing=False) != []
    # user_facing: original rule byte-identical in both directions.
    assert lint(candidate_with("browser"), user_facing=True) == []
    assert lint(candidate_with("git worktree text files"), user_facing=True) != []
    assert lint(candidate_with("headless functional_check target"), user_facing=True) != []
    # Message content per mode (probe-verified wording, brief-18 law).
    nu_errors = lint(candidate_with("headless functional_check target"), user_facing=False)
    assert any("deliverable's own target surface" in error for error in nu_errors)
    uf_errors = lint(candidate_with("git worktree text files"), user_facing=True)
    assert any("user-facing runtime target" in error for error in uf_errors)


def test_not_user_facing_platform_rejection_feedback_steers_to_spec_surface(monkeypatch, tmp_path):
    invalid = _spec_candidate("bad_platform_spec", "minimal_local", "Bad Platform Spec")
    invalid["stack_requirement"] = {
        **invalid["stack_requirement"],
        "platform": "headless functional_check target",
    }
    fixed = _spec_candidate("good_platform_spec", "minimal_local", "Good Platform Spec")
    fixed["stack_requirement"] = {
        **fixed["stack_requirement"],
        # The correction a blank model produced from the feedback message alone
        # (scratchpad/brief34_probes/out_nu.txt).
        "platform": "git-committed specification documents",
    }
    outputs = [
        invalid,
        fixed,
        _spec_candidate("projection_spec", "repo_native", "Projection Spec"),
        _spec_candidate("hybrid_spec", "general_architectural", "Hybrid Spec"),
    ]
    prompts = []

    def fake_run_json(repo: Path, **kwargs):
        prompts.append(kwargs["prompt"])
        return {"ok": True, "raw": json.dumps(outputs.pop(0))}

    monkeypatch.setattr(receive_module.codex_exec, "run_json", fake_run_json)

    result = receive_module._generate_candidates(
        _normalized_problem(),
        _constraints(),
        _prior_art_map(),
        {"repo": tmp_path, "deliverable_user_facing": False},
    )

    assert [candidate["id"] for candidate in result["candidates"]] == [
        "good_platform_spec",
        "projection_spec",
        "hybrid_spec",
    ]
    assert "deliverable's own target surface" in prompts[1]
    assert "functional_check, codex) are not a platform" in prompts[1]


def test_tech_stack_platform_rule_is_scoped_by_applicability():
    stack = {
        "build_strategy": "from_scratch",
        "engine": "",
        "framework": "",
        "language": "Markdown",
        "platform": "git worktree specification documents",
        "rationale": "Spec deliverable authored directly as repository documents.",
        "provenance": "ai_deliberated",
    }
    assert field_registry.explain_tech_stack_violation(dict(stack), user_facing=False) == ""
    assert "user-facing runtime target" in field_registry.explain_tech_stack_violation(dict(stack), user_facing=True)
    org_stack = {**stack, "platform": "headless functional_check target"}
    # The org's own mechanisms are rejected in BOTH modes.
    assert "not a platform" in field_registry.explain_tech_stack_violation(dict(org_stack), user_facing=False)
    assert field_registry.explain_tech_stack_violation(dict(org_stack), user_facing=True) != ""


def test_research_formation_reaches_select_approach_with_spec_candidates(monkeypatch, tmp_path):
    # E2E: the applicability travels from the VIEW through form_technical_approach's
    # context into the real candidate validator; a not_user_facing research series
    # with spec-shaped candidates reaches select_approach instead of dying at
    # generate_candidates (the body-format run's death).
    view = _patch_series_view()
    view["request_type"] = "research"
    view["user_experience_requirements"]["applicability"] = {
        "applicability": "not_user_facing",
        "not_user_facing_reason": "The deliverables are git-committed specification documents.",
    }
    calls: list[str] = []
    outputs = [
        _spec_candidate("body_grammar_spec", "minimal_local", "Body Grammar Spec"),
        _spec_candidate("projection_spec", "repo_native", "Projection Spec"),
        _spec_candidate("hybrid_spec", "general_architectural", "Hybrid Spec"),
    ]

    def fake_run_json(repo: Path, **kwargs):
        return {"ok": True, "raw": json.dumps(outputs.pop(0))}

    def fake_select(*args, **kwargs):
        calls.append("select_approach")
        decision = _decision()
        decision["selected_candidate_id"] = "body_grammar_spec"
        decision["rejected"] = [{"candidate_id": "projection_spec", "objection": "Lower problem fit."}]
        return decision

    monkeypatch.setattr(receive_module.codex_exec, "run_json", fake_run_json)
    monkeypatch.setattr(receive_module, "_normalize_problem", lambda *args, **kwargs: _normalized_problem())
    monkeypatch.setattr(receive_module.engineering_precedent_store, "build_from_patch_series", lambda *args, **kwargs: {"processed_terms": []})
    monkeypatch.setattr(receive_module.engineering_precedent_store, "start_background_build", lambda *args, **kwargs: object())
    monkeypatch.setattr(receive_module, "_extract_constraints", lambda *args, **kwargs: _constraints())
    monkeypatch.setattr(receive_module, "_build_prior_art_map", lambda *args, **kwargs: _prior_art_map())
    monkeypatch.setattr(receive_module, "_evaluate_candidates", lambda *args, **kwargs: _evaluations())
    monkeypatch.setattr(receive_module, "_select_approach", fake_select)
    monkeypatch.setattr(receive_module, "_implementation_strategy", lambda *args, **kwargs: _implementation())
    monkeypatch.setattr(receive_module, "_domain_specification", lambda *args, **kwargs: _domain_specification())
    monkeypatch.setattr(receive_module, "_right_size_patch_plan", lambda *args, **kwargs: _patch_plan())
    spec_risks = {
        "risks": [
            {
                "id": "risk:spec-scope",
                "risk": "The grammar spec could underdefine ordering rules.",
                "mitigation": "Pin ordering rules with worked examples from the specimens.",
                "attaches_to": "candidate",
                "target_id": "body_grammar_spec",
            }
        ]
    }
    monkeypatch.setattr(receive_module, "_surface_risks", lambda *args, **kwargs: spec_risks)

    result = receive_module.form_technical_approach(view, tmp_path)

    assert result["ok"] is True
    assert calls == ["select_approach"]
    candidates = result["technical_approach"]["problem"]["question"]["candidates"]
    assert [candidate["id"] for candidate in candidates] == [
        "body_grammar_spec",
        "projection_spec",
        "hybrid_spec",
    ]
    assert candidates[0]["stack_requirement"]["platform"] == "git worktree text files"


def test_candidate_engine_based_rejects_browser_standards_as_engine(monkeypatch, tmp_path):
    invalid = _candidate("browser_standards_engine", "minimal_local", "Browser Standards")
    invalid["stack_requirement"] = {
        **invalid["stack_requirement"],
        "build_strategy": "engine_based",
        "engine": "browser standards",
        "framework": "",
    }
    fixed = _candidate("browser_standards_from_scratch", "minimal_local", "Browser Standards")
    fixed["stack_requirement"] = {
        **fixed["stack_requirement"],
        "build_strategy": "from_scratch",
        "engine": "",
        "framework": "",
    }
    outputs = [
        invalid,
        fixed,
        _candidate("feasible_b", "repo_native", "Feasible B"),
        _candidate("feasible_c", "general_architectural", "Feasible C"),
    ]
    prompts = []

    def fake_run_json(repo: Path, **kwargs):
        prompts.append(kwargs["prompt"])
        return {"ok": True, "raw": json.dumps(outputs.pop(0))}

    monkeypatch.setattr(receive_module.codex_exec, "run_json", fake_run_json)

    result = receive_module._generate_candidates(
        _normalized_problem(),
        _constraints(),
        _prior_art_map(),
        {"repo": tmp_path},
    )

    assert result["candidates"][0]["stack_requirement"]["build_strategy"] == "from_scratch"
    assert result["candidates"][0]["stack_requirement"]["engine"] == ""
    assert "hand-rolled browser standards must use from_scratch" in prompts[1]


def test_form_technical_approach_writes_incremental_progress_snapshots(monkeypatch, tmp_path):
    _patch_successful_approach_steps(monkeypatch)
    ticks = iter(float(value) for value in range(100))
    monkeypatch.setattr(receive_module.time, "monotonic", lambda: next(ticks))

    progress_path = tmp_path / "progress" / "technical-approach-plan.json"
    original_writer = receive_module._write_technical_approach_progress
    snapshots = []

    def recording_writer(path, partial_tree, steps_completed, current_step):
        original_writer(path, partial_tree, steps_completed, current_step)
        snapshots.append(json.loads(Path(path).read_text(encoding="utf-8")))

    monkeypatch.setattr(receive_module, "_write_technical_approach_progress", recording_writer)

    result = receive_module.form_technical_approach(_patch_series_view(), tmp_path, progress_path=progress_path)

    expected_steps = [
        "normalize_problem",
        "extract_constraints",
        "build_prior_art_map",
        "generate_candidates",
        "evaluate_candidates",
        "select_approach",
        "implementation_strategy",
        "domain_specification",
        "right_size_patch_plan",
        "surface_risks",
    ]
    assert result["ok"] is True
    assert len(snapshots) == len(expected_steps)
    assert json.loads(progress_path.read_text(encoding="utf-8")) == snapshots[-1]

    for index, snapshot in enumerate(snapshots):
        completed = snapshot["steps_completed"]
        expected_completed = expected_steps[: index + 1]
        expected_current = expected_steps[index + 1] if index + 1 < len(expected_steps) else None
        assert [step["step"] for step in completed] == expected_completed
        assert [step["seconds"] for step in completed] == [1.0] * len(expected_completed)
        assert snapshot["current_step"] == expected_current
        assert snapshot["progress"]["steps_done"] == expected_completed
        assert snapshot["progress"]["steps_completed"] == completed
        assert snapshot["progress"]["current_step"] == expected_current

    assert snapshots[0]["technical_approach"]["problem"]["goals"][0]["id"] == "goal:1"
    assert snapshots[0]["technical_approach"]["problem"]["constraints"] == {"hard": [], "soft": []}
    assert snapshots[1]["technical_approach"]["problem"]["constraints"]["hard"][0]["id"] == "constraint:hard:1"
    assert snapshots[2]["technical_approach"]["problem"]["prior_art"][0]["id"] == (
        "prior_art:reference-first-prior-art-synthesis"
    )
    assert [candidate["id"] for candidate in snapshots[3]["technical_approach"]["problem"]["question"]["candidates"]] == [
        "minimal",
        "repo_native",
    ]
    assert "evaluation" not in snapshots[3]["technical_approach"]["problem"]["question"]["candidates"][0]
    assert snapshots[4]["technical_approach"]["problem"]["question"]["candidates"][1]["evaluation"]["id"] == (
        "evaluation:repo_native"
    )
    assert snapshots[5]["technical_approach"]["problem"]["question"]["decision"]["id"] == "decision:repo_native"
    implementation_after_step_7 = snapshots[6]["technical_approach"]["problem"]["question"]["decision"]["implementation"]
    assert implementation_after_step_7["id"] == "implementation:repo_native"
    assert "patch_plan" not in implementation_after_step_7
    assert snapshots[7]["technical_approach"]["problem"]["question"]["decision"]["implementation"]["domain_specification"][
        "aspects"
    ][0]["id"] == "domain_specification:battle-numbers"
    assert "patch_plan" not in snapshots[7]["technical_approach"]["problem"]["question"]["decision"]["implementation"]
    assert snapshots[8]["technical_approach"]["problem"]["question"]["decision"]["implementation"]["patch_plan"][
        "id"
    ] == "patch_plan:repo_native"
    final_question = snapshots[9]["technical_approach"]["problem"]["question"]
    assert final_question["candidates"][1]["risks"][0]["id"] == "risk:candidate"
    assert final_question["decision"]["implementation"]["risks"][0]["id"] == "risk:implementation"


def test_form_technical_approach_without_progress_path_does_not_persist_progress(monkeypatch, tmp_path):
    _patch_successful_approach_steps(monkeypatch)

    def fail_monotonic():
        raise AssertionError("progress timing should not run when progress_path is None")

    def fail_writer(*args):
        raise AssertionError("progress writer should not run when progress_path is None")

    monkeypatch.setattr(receive_module.time, "monotonic", fail_monotonic)
    monkeypatch.setattr(receive_module, "_write_technical_approach_progress", fail_writer)

    result = receive_module.form_technical_approach(_patch_series_view(), tmp_path, progress_path=None)

    assert result["ok"] is True
    assert "progress" not in result
    assert "steps_completed" not in result
    assert "current_step" not in result
    assert result["technical_approach"]["problem"]["question"]["decision"]["implementation"]["risks"][0]["id"] == (
        "risk:implementation"
    )


def test_empty_slot_regenerates_then_accepts(monkeypatch, tmp_path):
    attempts = [
        {
            "systems": [
                {
                    "system_name": "Battle loop",
                    "observable_behavior": "",
                    "observable_effect": "Visible battle feedback appears.",
                    "named_content": [{"name": "Slime", "kind": "enemy"}, {"name": "Spark spell", "kind": "spell"}],
                    "key_modules": ["game.battle"],
                }
            ],
            "persistence": {"saved_fields": ["battle_state"]},
        },
        _implementation(),
    ]
    prompts = []

    def fake_run_json(repo: Path, **kwargs):
        prompts.append(kwargs["prompt"])
        return {"ok": True, "raw": json.dumps(attempts.pop(0))}

    monkeypatch.setattr(receive_module.codex_exec, "run_json", fake_run_json)

    result = receive_module._implementation_strategy(
        _decision(),
        _prior_art_map(),
        _constraints(),
        _patch_series_view(),
        tmp_path,
        {"repo": tmp_path},
    )

    assert result == _implementation()
    assert len(prompts) == 2
    assert "observable_behavior is empty" in prompts[1]


def test_user_facing_success_criteria_accept_resolved_ux_trace():
    problem = _normalized_problem()
    criterion = problem["success_criteria"][0]
    criterion["verifiable_outcome"] = {
        "expected_state": "The Slime is defeated and meadow_gate_open is true.",
        "evidence": "Internal state records the gate flag.",
    }
    criterion["verification"] = {
        "method": "automated_test",
        "check": "Assert hidden meadow_gate_open is true.",
    }

    assert not receive_module._lint_normalized_problem(problem, _patch_series_view())


def test_user_facing_success_criteria_reject_unresolved_ux_trace():
    bad = _normalized_problem()
    bad["success_criteria"][0]["ux_trace"] = "user_experience_requirements.core_status_surfaces.missing"

    errors = receive_module._lint_normalized_problem(bad, _patch_series_view())

    assert any(
        "success_criteria[0].ux_trace does not resolve: user_experience_requirements.core_status_surfaces.missing"
        in error
        for error in errors
    )


def test_unresolved_ux_trace_feedback_retries_with_bad_path(monkeypatch, tmp_path):
    bad = _normalized_problem()
    bad["success_criteria"][0]["ux_trace"] = "user_experience_requirements.core_status_surfaces.missing"
    good = _normalized_problem()
    attempts = [bad, good]
    prompts = []

    def fake_run_json(repo: Path, **kwargs):
        prompts.append(kwargs["prompt"])
        return {"ok": True, "raw": json.dumps(attempts.pop(0))}

    monkeypatch.setattr(receive_module.codex_exec, "run_json", fake_run_json)

    result = receive_module._normalize_problem(_patch_series_view(), {"repo": tmp_path})

    assert result == good
    assert len(prompts) == 2
    assert "user_experience_requirements.core_status_surfaces.missing" in prompts[1]


def test_user_facing_success_criteria_reject_all_none_ux_trace():
    bad = _normalized_problem()
    bad["success_criteria"][0]["ux_trace"] = "none"

    errors = receive_module._lint_normalized_problem(bad, _patch_series_view())

    assert any("player/user criterion with a resolved ux_trace" in error for error in errors)


def test_non_user_facing_success_criteria_do_not_require_ux_trace_resolution():
    problem = _normalized_problem()
    problem["success_criteria"][0]["ux_trace"] = "none"
    patch_series_view = _patch_series_view()
    patch_series_view["user_experience_requirements"]["applicability"] = {
        "applicability": "not_user_facing",
        "not_user_facing_reason": "Internal refactor only.",
    }

    assert not receive_module._lint_normalized_problem(problem, patch_series_view)


def test_prior_art_term_extraction_sees_user_experience_requirements(monkeypatch, tmp_path):
    captured = {}

    def fake_extract(text, context):
        captured["text"] = text
        return ["persistent visible gate state"]

    monkeypatch.setattr(receive_module.engineering_precedent_store, "_extract_precedent_terms", fake_extract)

    terms = receive_module._prior_art_key_concepts(_patch_series_view(), {}, {"normalized_problem": _normalized_problem()})

    assert "persistent visible gate state" in terms
    assert "Gate locked and open states have persistent visible evidence" in captured["text"]


def test_observable_effect_empty_slot_retries(monkeypatch, tmp_path):
    invalid = _implementation()
    invalid["systems"][0]["observable_effect"] = ""
    prompts = []

    def fake_run_json(repo: Path, **kwargs):
        prompts.append(kwargs["prompt"])
        payload = invalid if len(prompts) == 1 else _implementation()
        return {"ok": True, "raw": json.dumps(payload)}

    monkeypatch.setattr(receive_module.codex_exec, "run_json", fake_run_json)

    result = receive_module._implementation_strategy(
        _decision(),
        _prior_art_map(),
        _constraints(),
        _patch_series_view(),
        tmp_path,
        {"repo": tmp_path},
    )

    assert result == _implementation()
    assert "observable_effect is empty" in prompts[1]


def test_first_proof_moment_requires_presentation_baseline(monkeypatch, tmp_path):
    invalid = _patch_plan()
    invalid["first_proof_moment"]["presentation_baseline"] = ""

    monkeypatch.setattr(
        receive_module.codex_exec,
        "run_json",
        lambda repo, **kwargs: {"ok": True, "raw": json.dumps(invalid)},
    )

    result = receive_module._right_size_patch_plan(
        _decision(),
        _implementation(),
        _constraints(),
        {"repo": tmp_path},
        _accumulated_approach_through_patch_plan(),
    )

    assert result["ok"] is False
    assert "presentation_baseline is empty" in result["error"]


def test_domain_specification_validates_applicable_blocks_and_tolerates_not_applicable_surplus():
    valid = {
        "aspects": [
            {
                "aspect_name": "battle numbers",
                "applicability": "applies",
                "specification_body": "Spark damage and Slime HP are contractual for the first battle.",
                "quantities": [{"name": "Spark damage", "value": "4", "unit": "hit points"}],
                "tables": [],
                "sources": ["Reference battle loop facet"],
            },
            {
                "aspect_name": "economy",
                "applicability": "not_applicable",
                "specification_body": "The first battle slice has no shop or currency exchange.",
                "quantities": [{"name": "", "value": "", "unit": ""}],
                "tables": [{"table_name": "", "columns": [""], "rows": [[""]]}],
                "sources": [""],
            },
        ]
    }

    parsed = receive_module._parse_domain_specification(
        json.dumps(valid),
        [{"aspect_name": "battle numbers"}, {"aspect_name": "economy"}],
    )

    assert parsed["aspects"][0]["applicability"] == "applies"
    assert receive_module._lint_domain_specification(parsed) == []
    tree = receive_module._externalize_domain_specification(parsed, {})
    not_applicable = tree["aspects"][1]
    assert not_applicable["quantities"] == []
    assert not_applicable["tables"] == []
    assert not_applicable["surplus_quantities_ignored"] == 1
    assert not_applicable["surplus_tables_ignored"] == 1


def test_domain_specification_rejects_applies_without_data_source_or_matching_row_width():
    missing_data = {
        "aspects": [
            {
                "aspect_name": "battle numbers",
                "applicability": "applies",
                "specification_body": "Spark damage is specified.",
                "quantities": [],
                "tables": [],
                "sources": [],
            }
        ]
    }
    parsed = receive_module._parse_domain_specification(json.dumps(missing_data), [{"aspect_name": "battle numbers"}])

    lint_errors = receive_module._lint_domain_specification(parsed)
    assert any("applies but has no quantity or table" in error for error in lint_errors)
    assert any("applies but has no source" in error for error in lint_errors)

    bad_width = {
        "aspects": [
            {
                "aspect_name": "battle table",
                "applicability": "applies",
                "specification_body": "Rows must match columns.",
                "quantities": [],
                "tables": [{"table_name": "stats", "columns": ["name", "hp"], "rows": [["Slime"]]}],
                "sources": ["Reference stats table"],
            }
        ]
    }

    width = receive_module._parse_domain_specification(json.dumps(bad_width), [{"aspect_name": "battle table"}])
    assert width["ok"] is False
    assert "rows width mismatch" in width["error"]


def test_domain_precedent_query_terms_derive_multiple_short_phrases_from_patch_series_view():
    patch_series_view = _patch_series_view()
    patch_series_view["raw_request"] = "Build a command-RPG battle slice inspired by Dragon Quest."
    patch_series_view["working_title"] = "Command RPG Slime Battle"
    patch_series_view["background_facts"] = "JRPG combat readability, level curve, and content budget matter."

    terms = receive_module._domain_precedent_query_terms(patch_series_view, _implementation())

    assert len(terms) >= 3
    assert "JRPG combat formula" in terms
    assert "JRPG content budget" in terms
    assert "JRPG level curve" in terms
    assert all(2 <= len(term.split()) <= 5 for term in terms)


def test_domain_precedent_store_hit_materializes_quantity_aspect(monkeypatch):
    calls = []

    def fake_lookup(term, context=None, kind=None):
        calls.append((term, kind))
        if term != "JRPG combat formula":
            return None
        return {
            "term": term,
            "candidates": [
                {
                    "aspect_name": "combat formula",
                    "structure": "Declare damage, HP, and turn budget for the first proof fight.",
                    "quantities": [{"name": "Spark damage", "value": "4", "unit": "hit points"}],
                    "tables": [],
                    "source_url": "reference://jrpg-combat-formula",
                }
            ],
        }

    monkeypatch.setattr(receive_module.engineering_precedent_store, "lookup", fake_lookup)
    patch_series_view = _patch_series_view()
    patch_series_view["background_facts"] = "JRPG battle with a Slime and Spark spell."

    facets, lookups = receive_module._domain_precedent_facets_from_patch_series(patch_series_view, _implementation(), {})

    assert calls
    assert lookups["JRPG combat formula"]["candidates"]
    # Brief 26 union order: store-derived facets keep priority, UX-surface facets
    # follow (they demand model-authored bodies, so the materialization shortcut
    # correctly stays off for the mixed set).
    assert facets[0]["aspect_name"] == "combat formula"
    assert any(facet.get("source") == "patch_series_view.user_experience_requirements" for facet in facets[1:])
    domain = receive_module._domain_specification(facets[:1], lookups, _implementation(), _constraints(), patch_series_view, {"repo": "."})
    assert domain["aspects"][0]["aspect_name"] == "combat formula"
    assert domain["aspects"][0]["quantities"] == [{"name": "Spark damage", "value": "4", "unit": "hit points"}]


def test_domain_precedent_store_miss_falls_back_to_committed_ux_surfaces(monkeypatch):
    # Brief 26 (live: pomodoro run 3, formation commit ccede62): this test used to
    # PIN the disease — store miss -> facets [] -> _domain_specification
    # short-circuits to {"aspects": []} with no model call -> empty subtree that
    # reform's aspect-derived facets could never repopulate (GIGO bootstrap).
    # Ratified fix: the committed view's populated UX sections ARE a facet source.
    queried = []

    def fake_lookup(term, context=None, kind=None):
        queried.append(term)
        return None

    monkeypatch.setattr(receive_module.engineering_precedent_store, "lookup", fake_lookup)
    view = _patch_series_view()

    facets, lookups = receive_module._domain_precedent_facets_from_patch_series(view, _implementation(), {})

    assert queried
    assert lookups == {}
    assert facets, "populated user_facing UX must yield facets on a store miss"
    assert all(facet["source"] == "patch_series_view.user_experience_requirements" for facet in facets)
    by_name = {facet["aspect_name"]: facet for facet in facets}
    # Structure from code, committed text VERBATIM, content authored by the model.
    assert by_name["experience identity"]["requirement_surface"] == view["user_experience_requirements"]["experience_identity"]

    # A not_user_facing view still yields nothing (the reason is the UX story).
    silent = _patch_series_view()
    silent["user_experience_requirements"] = dict(view["user_experience_requirements"])
    silent["user_experience_requirements"]["applicability"] = {
        "applicability": "not_user_facing",
        "not_user_facing_reason": "Internal machinery only.",
    }
    silent_facets, silent_lookups = receive_module._domain_precedent_facets_from_patch_series(silent, _implementation(), {})
    assert silent_facets == []
    assert silent_lookups == {}


def _reform_domain_components(aspects: list[dict[str, object]]) -> dict[str, object]:
    components = receive_module._approach_components({})
    components["selected"] = {"selected_candidate_id": "candidate:one"}
    components["candidates"] = {"candidates": [{"id": "candidate:one", "summary": "Approach."}]}
    components["domain_specification"] = {"aspects": aspects}
    return components


def test_reform_domain_specification_bootstraps_from_committed_ux_when_aspects_empty(monkeypatch, tmp_path):
    # Brief 26 reform half of the disease: an empty committed subtree fed
    # aspect-derived facets, so the step re-ran and regenerated empty (live:
    # pomodoro run 3, round-2 flagged empty -> v3 reform re-ran the step ->
    # round-3 flagged empty again, 3 objections). The reform window must carry
    # the formation-equivalent facets instead.
    monkeypatch.setattr(receive_module.engineering_precedent_store, "lookup", lambda *args, **kwargs: None)
    view = _patch_series_view()
    components = _reform_domain_components(aspects=[])
    captured: dict[str, object] = {}

    def fake_domain_specification(profile_facets, reference_lookups, *args, **kwargs):
        captured["facets"] = profile_facets
        captured["lookups"] = reference_lookups
        return {"aspects": [{"aspect_name": "experience identity", "applicability": "applies",
                              "specification_body": "Authored by the model.", "quantities": [], "tables": [],
                              "sources": ["patch_series_view.user_experience_requirements"]}]}

    monkeypatch.setattr(receive_module, "_domain_specification", fake_domain_specification)

    result = receive_module._run_reform_step("domain_specification", tmp_path, view, components, {"repo": tmp_path})

    assert result["aspects"], "the regenerated subtree is no longer empty"
    facets = captured["facets"]
    assert facets, "empty committed aspects must not yield an empty window"
    assert all(facet["source"] == "patch_series_view.user_experience_requirements" for facet in facets)
    # The window/prompt contract: the committed UX text reaches the model verbatim
    # (the contract is the input; the content is the model's).
    prompt = receive_module._domain_specification_prompt(
        facets, captured["lookups"], _implementation(), _constraints(), view, {"repo": str(tmp_path)}
    )
    assert view["user_experience_requirements"]["experience_identity"]["named_reference"] in prompt
    assert "presentation model" in prompt


def test_reform_domain_specification_unions_committed_aspects_with_formation_source(monkeypatch, tmp_path):
    # [PROVISIONAL union] committed aspects keep identity and come first; the
    # formation-equivalent source appends only what the committed generation
    # missed (deduped by aspect name).
    monkeypatch.setattr(receive_module.engineering_precedent_store, "lookup", lambda *args, **kwargs: None)
    view = _patch_series_view()
    committed_aspect = {"aspect_name": "experience identity", "applicability": "applies",
                        "specification_body": "Committed body.", "quantities": [], "tables": [], "sources": ["prior round"]}
    components = _reform_domain_components(aspects=[committed_aspect])
    captured: dict[str, object] = {}

    def fake_domain_specification(profile_facets, reference_lookups, *args, **kwargs):
        captured["facets"] = profile_facets
        return {"aspects": [dict(committed_aspect)]}

    monkeypatch.setattr(receive_module, "_domain_specification", fake_domain_specification)

    receive_module._run_reform_step("domain_specification", tmp_path, view, components, {"repo": tmp_path})

    facets = captured["facets"]
    assert facets[0]["aspect_name"] == "experience identity"
    assert facets[0].get("specification_body") == "Committed body."  # committed facet first, untouched
    names = [facet["aspect_name"] for facet in facets]
    assert names.count("experience identity") == 1  # deduped against the UX source
    assert "presentation model" in names  # missed surfaces appended


def test_domain_specification_externalizes_large_tables():
    large_rows = [[f"Slime {index}", str(index)] for index in range(13)]
    domain = {
        "aspects": [
            {
                "aspect_name": "encounter table",
                "applicability": "applies",
                "specification_body": "Encounter rows are contractual.",
                "quantities": [],
                "tables": [{"table_name": "encounters", "columns": ["name", "hp"], "rows": large_rows}],
                "sources": ["Reference encounter table"],
            }
        ]
    }
    external_files: dict[str, object] = {}

    tree = receive_module._externalize_domain_specification(domain, external_files)

    aspect = tree["aspects"][0]
    assert aspect["id"] == "domain_specification:encounter-table"
    assert aspect["tables"] == []
    assert aspect["externalized_tables"][0]["row_count"] == 13
    assert aspect["externalized_tables"][0]["file_ref"] == "domain-spec/encounter-table.json"
    assert external_files["domain-spec/encounter-table.json"]["tables"][0]["rows"] == large_rows


def test_reform_maps_domain_aspect_ids_to_domain_step():
    assert receive_module._step_for_node_id("domain_specification:battle-numbers") == "domain_specification"
    assert receive_module._affected_reform_steps(
        {"problem": {"question": {"decision": {"implementation": {"domain_specification": _domain_specification()}}}}},
        [{"anchor_node_ids": ["domain_specification:battle-numbers"], "axis": "approach"}],
    ) == ["domain_specification"]


def test_step_prompts_render_accumulated_goals_and_direct_ancestors(tmp_path):
    accumulated = _accumulated_approach_through_patch_plan()

    prompts = {
        "extract_constraints": receive_module._extract_constraints_prompt(
            _patch_series_view(),
            tmp_path,
            {"repo": tmp_path},
            accumulated,
        ),
        "build_prior_art_map": receive_module._prior_art_map_prompt(
            _patch_series_view(),
            tmp_path,
            ["battle loop"],
            [{"term": "battle loop", "design": [], "implementation": [], "status": "not_found"}],
            {"repo": tmp_path},
            accumulated,
        ),
        "generate_candidates": receive_module._generate_candidate_prompt(
            _normalized_problem(),
            _constraints(),
            _prior_art_map(),
            "repo_native",
            [],
            {"repo": tmp_path},
            accumulated,
        ),
        "evaluate_candidates": receive_module._evaluate_candidate_prompt(
            _candidate("repo_native", "repo_native", "Repo Native"),
            _candidates(),
            _normalized_problem(),
            _constraints(),
            {"repo": tmp_path},
            accumulated,
        ),
        "select_approach": receive_module._select_approach_prompt(
            _candidates(),
            _evaluations(),
            _constraints(),
            {"repo": tmp_path},
            accumulated,
        ),
        "implementation_strategy": receive_module._implementation_strategy_prompt(
            _decision(),
            _prior_art_map(),
            _constraints(),
            _patch_series_view(),
            tmp_path,
            {"repo": tmp_path},
            accumulated,
        ),
        "domain_specification": receive_module._domain_specification_prompt(
            [{"aspect_name": "battle numbers", "structure": "Declare quantities."}],
            {"battle numbers": {"candidates": [{"structure": "Damage values are explicit."}]}},
            _implementation(),
            _constraints(),
            _patch_series_view(),
            {"repo": tmp_path},
            accumulated,
        ),
        "right_size_patch_plan": receive_module._right_size_patch_plan_prompt(
            _decision(),
            _implementation(),
            _constraints(),
            {"repo": tmp_path},
            accumulated,
        ),
        "surface_risks": receive_module._surface_risks_prompt(
            _decision(),
            _implementation(),
            _patch_plan(),
            _constraints(),
            {"repo": tmp_path},
            accumulated,
        ),
    }

    for prompt in prompts.values():
        assert "Root success_criteria from step 1" in prompt
        assert "Assert Spark defeats Slime and sets meadow_gate_open true." in prompt
        assert "Accumulated approach so far" in prompt

    assert "Prior-art map" in prompts["generate_candidates"]
    assert "Reference-first prior-art synthesis" in prompts["generate_candidates"]
    assert "Candidate to evaluate" in prompts["evaluate_candidates"]
    assert "All candidate approaches" in prompts["evaluate_candidates"]
    assert "Evaluation matrix" in prompts["select_approach"]
    assert "evaluation:repo_native" in prompts["implementation_strategy"]
    assert "decision:repo_native" in prompts["implementation_strategy"]
    assert "Content-aware precedent facets" in prompts["domain_specification"]
    assert "battle numbers" in prompts["domain_specification"]
    assert "Battle loop" in prompts["right_size_patch_plan"]
    assert "Patch plan" in prompts["surface_risks"]
    assert "Run the battle-loop test and assert meadow_gate_open." in prompts["surface_risks"]


def test_empty_slot_fails_closed_after_bounded_regeneration(monkeypatch, tmp_path):
    invalid = {
        "systems": [
            {
                "system_name": "Battle loop",
                "observable_behavior": "",
                "observable_effect": "Visible battle feedback appears.",
                "named_content": [{"name": "Slime", "kind": "enemy"}, {"name": "Spark spell", "kind": "spell"}],
                "key_modules": ["game.battle"],
            }
        ],
        "persistence": {"saved_fields": ["battle_state"]},
    }

    monkeypatch.setattr(
        receive_module.codex_exec,
        "run_json",
        lambda repo, **kwargs: {"ok": True, "raw": json.dumps(invalid)},
    )

    result = receive_module._implementation_strategy(
        _decision(),
        _prior_art_map(),
        _constraints(),
        _patch_series_view(),
        tmp_path,
        {"repo": tmp_path},
    )

    assert result["ok"] is False
    assert "remained invalid" in result["error"]
    assert "observable_behavior is empty" in result["error"]


def test_risk_targets_fail_closed_when_parent_is_unknown(monkeypatch, tmp_path):
    monkeypatch.setattr(receive_module, "_normalize_problem", lambda patch_series_view, context=None: _normalized_problem())
    monkeypatch.setattr(receive_module, "_extract_constraints", lambda *args, **kwargs: _constraints())
    monkeypatch.setattr(
        receive_module.engineering_precedent_store,
        "build_from_patch_series",
        lambda *args, **kwargs: {"terms": {}, "processed_terms": ["battle loop"], "expanded": [], "hits": [], "failed": {}},
    )
    monkeypatch.setattr(receive_module, "_build_prior_art_map", lambda *args, **kwargs: _prior_art_map())
    monkeypatch.setattr(receive_module, "_generate_candidates", lambda *args, **kwargs: _candidates())
    monkeypatch.setattr(receive_module, "_evaluate_candidates", lambda *args, **kwargs: _evaluations())
    monkeypatch.setattr(receive_module, "_select_approach", lambda *args, **kwargs: _decision())
    monkeypatch.setattr(receive_module, "_implementation_strategy", lambda *args, **kwargs: _implementation())
    monkeypatch.setattr(receive_module, "_domain_specification", lambda *args, **kwargs: _domain_specification())
    monkeypatch.setattr(receive_module, "_right_size_patch_plan", lambda *args, **kwargs: _patch_plan())
    monkeypatch.setattr(
        receive_module,
        "_surface_risks",
        lambda *args, **kwargs: {
            "risks": [
                {
                    "id": "risk:lost",
                    "risk": "Detached risk.",
                    "mitigation": "Attach to a known parent.",
                    "attaches_to": "implementation",
                    "target_id": "implementation:unknown",
                }
            ]
        },
    )

    result = receive_module.form_technical_approach(_patch_series_view(), tmp_path)

    assert result["ok"] is False
    assert result["failed_step"] == "surface_risks"
    assert "targets unknown implementation node" in result["error"]


def test_form_technical_approach_builds_precedent_then_prior_art_uses_same_terms(monkeypatch, tmp_path):
    calls = []

    monkeypatch.setattr(receive_module, "_normalize_problem", lambda *args, **kwargs: _normalized_problem())
    monkeypatch.setattr(receive_module, "_extract_constraints", lambda *args, **kwargs: _constraints())
    monkeypatch.setattr(receive_module, "_generate_candidates", lambda *args, **kwargs: _candidates())
    monkeypatch.setattr(receive_module, "_evaluate_candidates", lambda *args, **kwargs: _evaluations())
    monkeypatch.setattr(receive_module, "_select_approach", lambda *args, **kwargs: _decision())
    monkeypatch.setattr(receive_module, "_implementation_strategy", lambda *args, **kwargs: _implementation())
    monkeypatch.setattr(receive_module, "_domain_specification", lambda *args, **kwargs: _domain_specification())
    monkeypatch.setattr(receive_module, "_right_size_patch_plan", lambda *args, **kwargs: _patch_plan())
    monkeypatch.setattr(receive_module, "_surface_risks", lambda *args, **kwargs: _risks())
    monkeypatch.setattr(
        receive_module,
        "_prior_art_key_concepts",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("prior art must not re-extract terms")),
    )

    def fake_build_from_patch_series(patch_series_view, context=None, **kwargs):
        assert kwargs == {"kinds": ("design",)}
        calls.append("build_from_patch_series")
        return {
            "terms": {"C1": {}, "C2": {}},
            "processed_terms": ["C1", "C2"],
            "expanded": ["C1", "C2"],
            "hits": [],
            "failed": {},
        }

    def fake_lookup(term, reference_context, kind=None):
        assert term in {"C1", "C2"}
        if kind == "design":
            return {"term": term, "candidates": [_precedent_candidate_for_term(term, "design", f"{term} design summary")]}
        if kind == "implementation":
            return {
                "term": term,
                "candidates": [_precedent_candidate_for_term(term, "implementation", f"{term} implementation summary")],
            }
        return {"term": term, "candidates": []}

    def fake_run_json(repo: Path, **kwargs):
        calls.append("build_prior_art_map")
        prompt = kwargs["prompt"]
        assert prompt.index('"term": "C1"') < prompt.index('"term": "C2"')
        assert "C1 design structure" in prompt
        assert "C2 implementation summary" in prompt
        return {"ok": True, "raw": json.dumps(_prior_art_map_for_terms(("C1", "design"), ("C2", "implementation")))}

    monkeypatch.setattr(receive_module.engineering_precedent_store, "build_from_patch_series", fake_build_from_patch_series)
    monkeypatch.setattr(receive_module.engineering_precedent_store, "lookup", fake_lookup)
    monkeypatch.setattr(receive_module.codex_exec, "run_json", fake_run_json)

    result = receive_module.form_technical_approach(_patch_series_view(), tmp_path)

    assert result["ok"] is True
    assert calls == ["build_from_patch_series", "build_prior_art_map"]
    patterns = result["steps"]["build_prior_art_map"]["patterns"]
    assert patterns[0]["source"]["facet_kind"] == "design"
    assert patterns[0]["reference_facets"]["design"][0]["structure"] == "C1 design structure"
    assert patterns[1]["source"]["facet_kind"] == "implementation"
    assert patterns[1]["reference_facets"]["design"][0]["rationale"] == "C2 design rationale"


def test_form_technical_approach_waits_for_design_build(monkeypatch, tmp_path):
    design_started = threading.Event()
    release_design = threading.Event()
    form_done = threading.Event()
    used_terms = []

    _patch_successful_approach_steps(monkeypatch)

    def fake_build_from_patch_series(patch_series_view, context=None, **kwargs):
        assert kwargs == {"kinds": ("design",)}
        design_started.set()
        assert release_design.wait(5)
        return {
            "terms": {"design concept": {}},
            "processed_terms": ["design concept"],
            "expanded": ["design concept"],
            "hits": [],
            "failed": {},
        }

    def fake_prior_art(patch_series_view, repo, context=None, approach=None, reference_terms=None):
        used_terms.extend(reference_terms)
        return _prior_art_map_with_facet("design concept", "design")

    monkeypatch.setattr(receive_module.engineering_precedent_store, "build_from_patch_series", fake_build_from_patch_series)
    monkeypatch.setattr(receive_module, "_build_prior_art_map", fake_prior_art)

    result_holder = {}

    def run_form():
        result_holder["result"] = receive_module.form_technical_approach(_patch_series_view(), tmp_path)
        form_done.set()

    worker = threading.Thread(target=run_form)
    worker.start()
    assert design_started.wait(1)
    assert not form_done.is_set()
    release_design.set()
    worker.join(5)

    assert form_done.is_set()
    assert result_holder["result"]["ok"] is True
    assert used_terms == ["design concept"]
    assert result_holder["result"]["steps"]["build_prior_art_map"]["patterns"][0]["source"]["facet_kind"] == "design"


def test_form_technical_approach_joins_external_precedent_front_after_constraints(
    monkeypatch, tmp_path
):
    constraints_finished = threading.Event()
    prior_art_called = threading.Event()
    used_terms = []
    front_future = concurrent.futures.Future()

    _patch_successful_approach_steps(monkeypatch)

    def fake_constraints(*args, **kwargs):
        constraints_finished.set()
        return _constraints()

    def fake_prior_art(
        patch_series_view,
        repo,
        context=None,
        approach=None,
        reference_terms=None,
    ):
        assert front_future.done()
        assert approach["problem"]["constraints"]["hard"][0]["id"] == (
            "constraint:hard:1"
        )
        used_terms.extend(reference_terms)
        prior_art_called.set()
        return _prior_art_map()

    monkeypatch.setattr(receive_module, "_extract_constraints", fake_constraints)
    monkeypatch.setattr(receive_module, "_build_prior_art_map", fake_prior_art)

    result_holder = {}

    def run_form():
        result_holder["result"] = receive_module.form_technical_approach(
            _patch_series_view(),
            tmp_path,
            precedent_front_future=front_future,
        )

    worker = threading.Thread(target=run_form)
    worker.start()
    assert constraints_finished.wait(1)
    assert not prior_art_called.is_set()
    assert worker.is_alive()

    front_future.set_result((["design concept"], object()))
    worker.join(5)

    assert not worker.is_alive()
    assert result_holder["result"]["ok"] is True
    assert used_terms == ["design concept"]
    assert "build_precedent_from_patch_series" not in result_holder["result"]["steps"]


def test_external_precedent_front_merge_is_deterministic(monkeypatch, tmp_path):
    _patch_successful_approach_steps(monkeypatch)

    serial = receive_module.form_technical_approach(
        _patch_series_view(),
        tmp_path,
        reference_terms=["design concept"],
    )
    parallel_results = []
    for _ in range(2):
        front_future = concurrent.futures.Future()
        front_future.set_result((["design concept"], object()))
        parallel_results.append(
            receive_module.form_technical_approach(
                _patch_series_view(),
                tmp_path,
                precedent_front_future=front_future,
            )
        )

    serial_body = receive_module._canonical_json(serial)
    assert [receive_module._canonical_json(result) for result in parallel_results] == [
        serial_body,
        serial_body,
    ]


def test_external_precedent_front_preserves_front_failure_semantics(
    monkeypatch, tmp_path
):
    _patch_successful_approach_steps(monkeypatch)
    monkeypatch.setattr(
        receive_module,
        "_extract_constraints",
        lambda *args, **kwargs: {
            "ok": False,
            "error": "constraint front failed",
        },
    )
    monkeypatch.setattr(
        receive_module,
        "_build_prior_art_map",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("prior art must not run after a constraint failure")
        ),
    )

    serial = receive_module.form_technical_approach(
        _patch_series_view(),
        tmp_path,
        reference_terms=["design concept"],
    )
    front_future = concurrent.futures.Future()
    front_future.set_result((["design concept"], object()))
    parallel = receive_module.form_technical_approach(
        _patch_series_view(),
        tmp_path,
        precedent_front_future=front_future,
    )

    assert parallel == serial == {
        "ok": False,
        "error": "constraint front failed",
        "failed_step": "extract_constraints",
    }


def test_form_technical_approach_starts_implementation_build_without_awaiting(monkeypatch, tmp_path):
    implementation_started = threading.Event()
    release_implementation = threading.Event()
    implementation_done = threading.Event()
    calls = []

    receive_module.engineering_precedent_store.await_background_builds(timeout=5)
    _patch_successful_approach_steps(monkeypatch)
    monkeypatch.setattr(receive_module.engineering_precedent_store, "start_background_build", ORIGINAL_START_BACKGROUND_BUILD)

    def fake_build_from_patch_series(patch_series_view, context=None, force=False, kinds=None):
        calls.append(kinds)
        if kinds == ("implementation",):
            implementation_started.set()
            assert release_implementation.wait(5)
            implementation_done.set()
        return {
            "terms": {"design concept": {}},
            "processed_terms": ["design concept"],
            "expanded": ["design concept"],
            "hits": [],
            "failed": {},
        }

    def fake_prior_art(patch_series_view, repo, context=None, approach=None, reference_terms=None):
        assert reference_terms == ["design concept"]
        return _prior_art_map_with_facet("design concept", "design")

    monkeypatch.setattr(receive_module.engineering_precedent_store, "build_from_patch_series", fake_build_from_patch_series)
    monkeypatch.setattr(receive_module, "_build_prior_art_map", fake_prior_art)

    result = receive_module.form_technical_approach(_patch_series_view(), tmp_path)

    assert result["ok"] is True
    assert implementation_started.wait(1)
    assert not implementation_done.is_set()
    assert calls[0] == ("design",)
    assert ("implementation",) in calls
    release_implementation.set()
    receive_module.engineering_precedent_store.await_background_builds(timeout=5)
    assert implementation_done.is_set()


def test_form_technical_approach_precedent_terms_skip_internal_build(monkeypatch, tmp_path):
    used_terms = []

    _patch_successful_approach_steps(monkeypatch)
    monkeypatch.setattr(
        receive_module.engineering_precedent_store,
        "build_from_patch_series",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("build_from_patch_series should be skipped")),
    )
    monkeypatch.setattr(
        receive_module.engineering_precedent_store,
        "start_background_build",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("background build should be skipped")),
    )

    def fake_prior_art(patch_series_view, repo, context=None, approach=None, reference_terms=None):
        used_terms.extend(reference_terms)
        return _prior_art_map()

    monkeypatch.setattr(receive_module, "_build_prior_art_map", fake_prior_art)

    result = receive_module.form_technical_approach(_patch_series_view(), tmp_path, reference_terms=["cached concept"])

    assert result["ok"] is True
    assert used_terms == ["cached concept"]


def test_prior_art_precedent_term_timeout_does_not_abort_other_concepts(monkeypatch, tmp_path):
    lookup_calls = []

    def fake_lookup(term, reference_context, kind=None):
        lookup_calls.append((term, kind))
        if term == "timed concept":
            raise receive_module.engineering_precedent_store.ReferenceCodexTimeout("search timed out")
        if kind == "design":
            return {
                "term": term,
                "candidates": [_precedent_candidate_for_term(term, "design", f"{term} design summary")],
            }
        return {"term": term, "candidates": []}

    def fake_run_json(repo: Path, **kwargs):
        prompt = kwargs["prompt"]
        assert '"status": "failed"' in prompt
        assert "search timed out" in prompt
        assert "good concept design summary" in prompt
        return {
            "ok": True,
            "raw": json.dumps(_prior_art_map_for_terms(("timed concept", "none"), ("good concept", "design"))),
        }

    monkeypatch.setattr(receive_module.engineering_precedent_store, "lookup", fake_lookup)
    monkeypatch.setattr(receive_module.codex_exec, "run_json", fake_run_json)

    result = receive_module._build_prior_art_map(
        _patch_series_view(),
        tmp_path,
        {},
        {"normalized_problem": _normalized_problem()},
        ["timed concept", "good concept"],
    )

    assert result["patterns"][0]["source"]["facet_kind"] == "none"
    assert result["patterns"][0]["reference_facets"]["status"] == "failed"
    assert result["patterns"][1]["source"]["facet_kind"] == "design"
    assert result["patterns"][1]["reference_facets"]["design"][0]["structure"] == "good concept design structure"
    assert ("good concept", "design") in lookup_calls


def test_precedent_codex_search_timeout_returns_empty_result(monkeypatch):
    observed_timeouts = []

    def fake_run(*args, **kwargs):
        observed_timeouts.append(kwargs.get("timeout"))
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=kwargs.get("timeout"))

    monkeypatch.setenv("AI_ORG_REFERENCE_SEARCH_TIMEOUT", "1")
    monkeypatch.setattr(receive_module.engineering_precedent_store.subprocess, "run", fake_run)

    context: dict[str, object] = {}
    result = receive_module.engineering_precedent_store._codex_search_keywords("battle loop", context)

    assert result == []
    assert observed_timeouts == [1.0]
    assert "search-keywords.json timed out after 1 seconds" in context["_precedent_codex_timeouts"][0]


def test_prior_art_expands_precedent_on_miss_and_uses_researched_facets(monkeypatch, tmp_path):
    expand_calls = []
    lookup_contexts = []
    prompts = []
    stack_context = {"language": "Python", "environment": "CLI", "version": "3.12"}
    context = {**stack_context, "repo": tmp_path, "repo_root": tmp_path}

    monkeypatch.setattr(receive_module, "_prior_art_key_concepts", lambda *args, **kwargs: ["battle loop"])

    def fake_lookup(term, reference_context, kind=None):
        lookup_contexts.append(dict(reference_context))
        assert reference_context == stack_context
        assert "repo" not in reference_context
        assert "repo_root" not in reference_context
        return {"term": term, "candidates": []}

    def fake_expand(term, reference_context):
        expand_calls.append((term, dict(reference_context)))
        return {
            "term": term,
            "candidates": [
                _precedent_candidate("design", "researched design summary"),
                _precedent_candidate("implementation", "researched implementation summary"),
            ],
        }

    def fake_run_json(repo: Path, **kwargs):
        prompt = kwargs["prompt"]
        prompts.append(prompt)
        assert "researched design summary" in prompt
        assert "researched implementation summary" in prompt
        assert '"status": "researched"' in prompt
        facet_kind = "none" if len(prompts) == 1 else "design"
        return {"ok": True, "raw": json.dumps(_prior_art_map_with_facet("battle loop", facet_kind))}

    monkeypatch.setattr(receive_module.engineering_precedent_store, "lookup", fake_lookup)
    monkeypatch.setattr(receive_module.engineering_precedent_store, "expand", fake_expand)
    monkeypatch.setattr(receive_module.codex_exec, "run_json", fake_run_json)

    result = receive_module._build_prior_art_map(_patch_series_view(), tmp_path, context, {"normalized_problem": _normalized_problem()})

    assert result["patterns"][0]["source"]["facet_kind"] == "design"
    assert expand_calls == [("battle loop", stack_context)]
    assert lookup_contexts
    assert len(prompts) == 2


def test_prior_art_precedent_hit_does_not_expand(monkeypatch):
    expand_calls = []
    stack_context = {"language": "Python", "environment": "CLI", "version": "3.12"}

    def fake_lookup(term, reference_context, kind=None):
        if kind == "design":
            return {"term": term, "candidates": [_precedent_candidate("design", "stored design summary")]}
        return {"term": term, "candidates": []}

    monkeypatch.setattr(receive_module.engineering_precedent_store, "lookup", fake_lookup)
    monkeypatch.setattr(receive_module.engineering_precedent_store, "expand", lambda *args, **kwargs: expand_calls.append(args))

    facets = receive_module._read_prior_art_precedent_facets(["battle loop"], {**stack_context, "repo": Path(".")})

    assert expand_calls == []
    assert facets[0]["status"] == "retrieved"
    assert facets[0]["design"][0]["summary"] == "stored design summary"


def test_prior_art_researches_missing_concepts_concurrently(monkeypatch):
    monkeypatch.setenv("AI_ORG_REFERENCE_PARALLEL", "3")
    concepts = ["alpha pattern", "beta pattern", "gamma pattern"]
    max_workers = []
    real_executor = concurrent.futures.ThreadPoolExecutor

    class RecordingExecutor(real_executor):
        def __init__(self, *args, **kwargs):
            max_workers.append(kwargs.get("max_workers"))
            super().__init__(*args, **kwargs)

    active = 0
    max_active = 0
    active_lock = threading.Lock()

    monkeypatch.setattr(
        receive_module.engineering_precedent_store,
        "lookup",
        lambda term, reference_context, kind=None: {"term": term, "candidates": []},
    )

    def fake_expand(term, reference_context):
        nonlocal active, max_active
        with active_lock:
            active += 1
            max_active = max(max_active, active)
        try:
            time.sleep(0.05)
            return {
                "term": term,
                "candidates": [
                    _precedent_candidate("design", f"{term} design summary"),
                    _precedent_candidate("implementation", f"{term} implementation summary"),
                ],
            }
        finally:
            with active_lock:
                active -= 1

    monkeypatch.setattr(receive_module.concurrent.futures, "ThreadPoolExecutor", RecordingExecutor)
    monkeypatch.setattr(receive_module.engineering_precedent_store, "expand", fake_expand)

    facets = receive_module._read_prior_art_precedent_facets(concepts, {})

    assert max_workers == [3, 3]
    assert max_active > 1
    assert [facet["term"] for facet in facets] == concepts
    assert [facet["status"] for facet in facets] == ["researched", "researched", "researched"]
    assert [facet["design"][0]["summary"] for facet in facets] == [
        "alpha pattern design summary",
        "beta pattern design summary",
        "gamma pattern design summary",
    ]


def test_prior_art_missing_concept_failure_does_not_abort_others(monkeypatch):
    monkeypatch.setenv("AI_ORG_REFERENCE_PARALLEL", "3")
    concepts = ["alpha pattern", "bad pattern", "gamma pattern"]

    monkeypatch.setattr(
        receive_module.engineering_precedent_store,
        "lookup",
        lambda term, reference_context, kind=None: {"term": term, "candidates": []},
    )

    def fake_expand(term, reference_context):
        if term == "bad pattern":
            raise RuntimeError("research failed")
        return {"term": term, "candidates": [_precedent_candidate("design", f"{term} design summary")]}

    monkeypatch.setattr(receive_module.engineering_precedent_store, "expand", fake_expand)

    facets = receive_module._read_prior_art_precedent_facets(concepts, {})

    assert [facet["term"] for facet in facets] == concepts
    assert facets[0]["status"] == "researched"
    assert facets[0]["design"][0]["summary"] == "alpha pattern design summary"
    assert facets[1]["status"] == "failed"
    assert facets[1]["error"] == "RuntimeError: research failed"
    assert facets[2]["status"] == "researched"
    assert facets[2]["design"][0]["summary"] == "gamma pattern design summary"


def test_prior_art_allows_none_only_when_expand_produces_no_facets(monkeypatch, tmp_path):
    expand_calls = []

    monkeypatch.setattr(receive_module, "_prior_art_key_concepts", lambda *args, **kwargs: ["unknown pattern"])
    monkeypatch.setattr(
        receive_module.engineering_precedent_store,
        "lookup",
        lambda term, reference_context, kind=None: {"term": term, "candidates": []},
    )

    def fake_expand(term, reference_context):
        expand_calls.append(term)
        return {"term": term, "candidates": []}

    monkeypatch.setattr(receive_module.engineering_precedent_store, "expand", fake_expand)
    monkeypatch.setattr(
        receive_module.codex_exec,
        "run_json",
        lambda repo, **kwargs: {"ok": True, "raw": json.dumps(_prior_art_map_with_facet("unknown pattern", "none"))},
    )

    result = receive_module._build_prior_art_map(_patch_series_view(), tmp_path, {}, {"normalized_problem": _normalized_problem()})

    assert expand_calls == ["unknown pattern"]
    assert result["patterns"][0]["source"]["facet_kind"] == "none"


def test_prior_art_precedent_expansion_is_capped(monkeypatch):
    concepts = [f"concept {index}" for index in range(receive_module.MAX_PRIOR_ART_REFERENCE_EXPANSIONS + 2)]
    expand_calls = []

    monkeypatch.setattr(
        receive_module.engineering_precedent_store,
        "lookup",
        lambda term, reference_context, kind=None: {"term": term, "candidates": []},
    )

    def fake_expand(term, reference_context):
        expand_calls.append(term)
        return {"term": term, "candidates": []}

    monkeypatch.setattr(receive_module.engineering_precedent_store, "expand", fake_expand)

    facets = receive_module._read_prior_art_precedent_facets(concepts, {})

    assert set(expand_calls) == set(concepts[: receive_module.MAX_PRIOR_ART_REFERENCE_EXPANSIONS])
    assert len(expand_calls) == receive_module.MAX_PRIOR_ART_REFERENCE_EXPANSIONS
    assert [facet["status"] for facet in facets[-2:]] == ["not_researched_cap", "not_researched_cap"]


def test_technical_approach_schemas_are_codex_valid():
    for schema in (
        receive_module.CANONICAL_ROOT_PREVIEW_SCHEMA,
        receive_module.NORMALIZE_PROBLEM_SCHEMA,
        receive_module.EXTRACT_CONSTRAINTS_SCHEMA,
        receive_module.PRIOR_ART_MAP_SCHEMA,
        receive_module.CANDIDATE_APPROACH_SCHEMA,
        receive_module.GENERATE_CANDIDATES_SCHEMA,
        receive_module.EVALUATE_CANDIDATE_SCHEMA,
        receive_module.EVALUATE_CANDIDATES_SCHEMA,
        receive_module.SELECT_APPROACH_SCHEMA,
        receive_module.IMPLEMENTATION_STRATEGY_SCHEMA,
        receive_module.DOMAIN_SPECIFICATION_SCHEMA,
        receive_module.RIGHT_SIZE_PATCH_PLAN_SCHEMA,
        receive_module.SURFACE_RISKS_SCHEMA,
    ):
        _assert_codex_valid_object_schema(schema)


def test_canonical_root_preview_mixed_pair_closure_and_normalization():
    root = _producer_aware_root()

    normalized = receive_module.validate_canonical_root_technical_approach(root)

    problem = normalized["problem"]
    assert [goal["requires_deliverable"] for goal in problem["goals"]] == [True, False]
    assert problem["deliverable_requirements"][0]["deliverable"] == "technical approach plan.cue"
    assert problem["production_obligations"][0]["deliverable"] == "technical approach plan.cue"
    assert problem["production_obligations"][0]["replacement_links"] == []
    assert problem["patch_plan"] == [
        {
            "item_id": "patch:preview",
            "production_obligation_ids": ["obligation:preview"],
        },
        {"item_id": "patch:diagnostics", "production_obligation_ids": []},
    ]
    assert root["problem"]["deliverable_requirements"][0]["deliverable"].startswith("  ")


def test_canonical_root_preview_partition_is_set_equal_across_item_order():
    root = _producer_aware_root()
    problem = root["problem"]
    problem["goals"][1]["requires_deliverable"] = True
    problem["deliverable_requirements"].append(
        {
            "id": "requirement:diagnostics",
            "referee_goal_id": "goal:diagnostics",
            "production_obligation_id": "obligation:diagnostics",
            "deliverable": "preview diagnostics.json",
        }
    )
    problem["production_obligations"].append(
        {
            "id": "obligation:diagnostics",
            "referee_goal_id": "goal:diagnostics",
            "deliverable_requirement_id": "requirement:diagnostics",
            "deliverable": "preview diagnostics.json",
            "eligibility_predicate": "Can produce field-specific diagnostics.",
            "replacement_links": [],
        }
    )
    # Assignment order is presentation only: the flattened assignment list is
    # intentionally the reverse of the obligation declaration list.
    problem["patch_plan"][0]["production_obligation_ids"] = [
        "obligation:diagnostics"
    ]
    problem["patch_plan"][1]["production_obligation_ids"] = [
        "obligation:preview"
    ]

    normalized = receive_module.validate_canonical_root_technical_approach(root)

    assert [
        obligation["id"]
        for obligation in normalized["problem"]["production_obligations"]
    ] == ["obligation:preview", "obligation:diagnostics"]
    assert [
        obligation_id
        for item in normalized["problem"]["patch_plan"]
        for obligation_id in item["production_obligation_ids"]
    ] == ["obligation:diagnostics", "obligation:preview"]


def test_canonical_root_preview_partition_rejects_cross_item_duplicate():
    root = _producer_aware_root()
    root["problem"]["patch_plan"][1]["production_obligation_ids"] = [
        "obligation:preview"
    ]

    with pytest.raises(receive_module.CanonicalRootPreviewError) as caught:
        receive_module.validate_canonical_root_technical_approach(root)

    assert caught.value.rule_id == "production-obligation-exactly-once"
    assert caught.value.obligation_id == "obligation:preview"
    assert caught.value.json_pointer == (
        "/problem/patch_plan/1/production_obligation_ids"
    )


def test_canonical_root_preview_parser_restores_depth_bounded_carrier():
    parsed = receive_module.parse_canonical_root_preview(_producer_preview_carrier())

    assert parsed["goals"][0]["capability"] == {
        "action": "Inspect the canonical producer-aware root preview.",
        "preconditions": ["A supplied candidate has explicit producer pairs."],
    }
    assert parsed["deliverable_requirements"][0]["production_obligation_id"] == (
        "obligation:preview"
    )
    assert parsed["production_obligations"][0]["replacement_links"] == []


@pytest.mark.parametrize("mutation", ["omit", "alias"])
def test_canonical_root_preview_parser_rejects_omitted_or_aliased_determination(
    mutation,
):
    carrier = _producer_preview_carrier()
    goal = carrier["goals"][0]
    if mutation == "omit":
        goal.pop("requires_deliverable")
        expected_rule = "requires-deliverable-required"
    else:
        goal["deliverable_required"] = goal.pop("requires_deliverable")
        expected_rule = "requires-deliverable-alias"

    with pytest.raises(receive_module.CanonicalRootPreviewError) as caught:
        receive_module.parse_canonical_root_preview(carrier)

    assert caught.value.rule_id == expected_rule
    assert caught.value.goal_id == "goal:preview"


def test_canonical_root_preview_parser_rejects_duplicate_determination_keys():
    carrier = json.dumps(_producer_preview_carrier(), separators=(",", ":"))
    carrier = carrier.replace(
        '"requires_deliverable":true',
        '"requires_deliverable":true,"requires_deliverable":false',
        1,
    )

    with pytest.raises(receive_module.CanonicalRootPreviewError) as caught:
        receive_module.parse_canonical_root_preview(carrier)

    assert caught.value.rule_id == "preview-json"
    assert caught.value.field == "preview"
    assert caught.value.cue_path == "root"
    assert "strict UTF-8 JSON" in caught.value.detail


def test_producer_aware_formation_rejects_duplicate_determination_keys(tmp_path):
    carrier = json.dumps(_producer_preview_carrier(), separators=(",", ":"))
    carrier = carrier.replace(
        '"requires_deliverable":true',
        '"requires_deliverable":true,"requires_deliverable":false',
        1,
    )

    with pytest.raises(receive_module.CanonicalRootPreviewError) as caught:
        receive_module.prepare_producer_aware_formation(
            _producer_aware_root(), tmp_path, carrier=carrier
        )

    assert caught.value.rule_id == "preview-json"
    assert caught.value.field == "preview"
    assert caught.value.cue_path == "root"


@pytest.mark.parametrize(
    ("mutation", "rule_id", "field"),
    [
        (
            lambda root: root["problem"]["goals"][0].pop("requires_deliverable"),
            "requires-deliverable-required",
            "requires_deliverable",
        ),
        (
            lambda root: root["problem"]["goals"][0].update({"deliverable_required": True}),
            "requires-deliverable-alias",
            "deliverable_required",
        ),
        (
            lambda root: root["problem"]["patch_plan"][0].update(
                {"production_obligation_ids": []}
            ),
            "production-obligation-exact-partition",
            "production_obligation_ids",
        ),
        (
            lambda root: root["problem"]["deliverable_requirements"][0].update(
                {"production_obligation_id": "obligation:fabricated"}
            ),
            "pair-cross-link",
            "production_obligation_id",
        ),
    ],
)
def test_canonical_root_preview_rejects_determination_and_partition_errors(
    mutation, rule_id, field
):
    root = _producer_aware_root()
    mutation(root)

    with pytest.raises(receive_module.CanonicalRootPreviewError) as caught:
        receive_module.validate_canonical_root_technical_approach(root)

    assert caught.value.rule_id == rule_id
    assert caught.value.field == field
    assert caught.value.cue_path.startswith("root.problem")


@pytest.mark.parametrize("transition", ["formation", "reform"])
def test_canonical_root_preview_rejects_surplus_patch_plan_assignment_fields(
    transition,
):
    previous = receive_module.validate_canonical_root_technical_approach(
        _producer_aware_root()
    )
    candidate = (
        _producer_aware_root()
        if transition == "formation"
        else json.loads(json.dumps(previous))
    )
    candidate["problem"]["patch_plan"][0]["assignment_note"] = (
        "surplus metadata cannot become a second assignment authority"
    )

    with pytest.raises(receive_module.CanonicalRootPreviewError) as caught:
        receive_module.validate_canonical_root_technical_approach(
            candidate,
            previous=previous if transition == "reform" else None,
        )

    assert caught.value.rule_id == "patch-plan-assignment-fields"
    assert caught.value.field == "assignment_note"
    assert caught.value.json_pointer == "/problem/patch_plan/0/assignment_note"
    assert caught.value.cue_path == "root.problem.patch_plan[0].assignment_note"


def test_canonical_root_preview_reform_retains_identity_for_same_semantics():
    previous = receive_module.validate_canonical_root_technical_approach(
        _producer_aware_root()
    )
    retained = json.loads(json.dumps(previous))
    retained["problem"]["deliverable_requirements"][0]["deliverable"] = (
        "technical\tapproach plan.cue"
    )
    retained["problem"]["production_obligations"][0]["deliverable"] = (
        "technical approach   plan.cue"
    )
    retained["problem"]["production_obligations"][0]["eligibility_predicate"] = (
        "Can produce, inspect, and independently test the canonical CUE body."
    )

    normalized = receive_module.validate_canonical_root_technical_approach(
        retained, previous=previous
    )

    assert normalized["problem"]["deliverable_requirements"][0]["id"] == "requirement:preview"
    assert normalized["problem"]["production_obligations"][0]["id"] == "obligation:preview"
    assert normalized["problem"]["production_obligations"][0]["replacement_links"] == []
    before_preview = receive_module.preview_canonical_root_technical_approach(previous)
    after_preview = receive_module.preview_canonical_root_technical_approach(
        retained, previous=previous
    )
    assert after_preview.body_sha256 != before_preview.body_sha256


def test_canonical_root_preview_reform_requires_one_closed_successor_link():
    previous = receive_module.validate_canonical_root_technical_approach(
        _producer_aware_root()
    )
    successor = json.loads(json.dumps(previous))
    successor["problem"]["deliverable_requirements"][0].update(
        {
            "id": "requirement:preview-v2",
            "production_obligation_id": "obligation:preview-v2",
            "deliverable": "producer-aware-plan.cue",
        }
    )
    successor["problem"]["production_obligations"][0].update(
        {
            "id": "obligation:preview-v2",
            "deliverable_requirement_id": "requirement:preview-v2",
            "deliverable": "producer-aware-plan.cue",
            "replacement_links": [
                {
                    "replaces_obligation_id": "obligation:preview",
                    "replacement_reason": "The required canonical artifact name changed.",
                }
            ],
        }
    )
    successor["problem"]["patch_plan"][0]["production_obligation_ids"] = [
        "obligation:preview-v2"
    ]

    normalized = receive_module.validate_canonical_root_technical_approach(
        successor, previous=previous
    )
    assert normalized["problem"]["production_obligations"][0]["replacement_links"] == [
        {
            "replaces_obligation_id": "obligation:preview",
            "replacement_reason": "The required canonical artifact name changed.",
        }
    ]

    fabricated = json.loads(json.dumps(successor))
    fabricated["problem"]["production_obligations"][0]["replacement_links"][0][
        "replaces_obligation_id"
    ] = "obligation:fabricated"
    with pytest.raises(receive_module.CanonicalRootPreviewError) as caught:
        receive_module.validate_canonical_root_technical_approach(
            fabricated, previous=previous
        )
    assert caught.value.rule_id == "successor-predecessor-link"
    assert caught.value.goal_id == "goal:preview"
    assert caught.value.obligation_id == "obligation:preview-v2"

    partial = json.loads(json.dumps(successor))
    partial["problem"]["production_obligations"][0]["replacement_links"] = []
    with pytest.raises(receive_module.CanonicalRootPreviewError) as caught:
        receive_module.validate_canonical_root_technical_approach(
            partial, previous=previous
        )
    assert caught.value.rule_id == "successor-predecessor-link"

    multiple = json.loads(json.dumps(successor))
    multiple["problem"]["production_obligations"][0]["replacement_links"].append(
        {
            "replaces_obligation_id": "obligation:another",
            "replacement_reason": "A fabricated second predecessor.",
        }
    )
    with pytest.raises(receive_module.CanonicalRootPreviewError) as caught:
        receive_module.validate_canonical_root_technical_approach(
            multiple, previous=previous
        )
    assert caught.value.rule_id == "replacement-links-cardinality"


def test_canonical_root_preview_mixed_successor_reform_is_identity_ordered():
    candidate = _producer_aware_root()
    problem = candidate["problem"]
    problem["goals"][1]["requires_deliverable"] = True
    problem["deliverable_requirements"].append(
        {
            "id": "requirement:diagnostics",
            "referee_goal_id": "goal:diagnostics",
            "production_obligation_id": "obligation:diagnostics",
            "deliverable": "diagnostics.cue",
        }
    )
    problem["production_obligations"].append(
        {
            "id": "obligation:diagnostics",
            "referee_goal_id": "goal:diagnostics",
            "deliverable_requirement_id": "requirement:diagnostics",
            "deliverable": "diagnostics.cue",
            "eligibility_predicate": "Can produce structured diagnostics.",
            "replacement_links": [],
        }
    )
    problem["patch_plan"][1]["production_obligation_ids"] = [
        "obligation:diagnostics"
    ]
    previous = receive_module.validate_canonical_root_technical_approach(candidate)

    successor = json.loads(json.dumps(previous))
    successor_problem = successor["problem"]
    preview_requirement = successor_problem["deliverable_requirements"][0]
    preview_obligation = successor_problem["production_obligations"][0]
    preview_requirement.update(
        {
            "id": "requirement:preview-v2",
            "production_obligation_id": "obligation:preview-v2",
            "deliverable": "producer-aware-plan.cue",
        }
    )
    preview_obligation.update(
        {
            "id": "obligation:preview-v2",
            "deliverable_requirement_id": "requirement:preview-v2",
            "deliverable": "producer-aware-plan.cue",
            "replacement_links": [
                {
                    "replaces_obligation_id": "obligation:preview",
                    "replacement_reason": "The canonical artifact name changed.",
                }
            ],
        }
    )
    successor_problem["patch_plan"][0]["production_obligation_ids"] = [
        "obligation:preview-v2"
    ]
    for field_name in (
        "goals",
        "deliverable_requirements",
        "production_obligations",
        "patch_plan",
    ):
        successor_problem[field_name].reverse()

    normalized = receive_module.validate_canonical_root_technical_approach(
        successor, previous=previous
    )
    obligations = {
        obligation["referee_goal_id"]: obligation
        for obligation in normalized["problem"]["production_obligations"]
    }

    assert obligations["goal:diagnostics"]["id"] == "obligation:diagnostics"
    assert obligations["goal:diagnostics"]["replacement_links"] == []
    assert obligations["goal:preview"]["id"] == "obligation:preview-v2"
    assert obligations["goal:preview"]["replacement_links"] == [
        {
            "replaces_obligation_id": "obligation:preview",
            "replacement_reason": "The canonical artifact name changed.",
        }
    ]


def test_canonical_root_preview_formation_rejects_predecessor_lineage():
    candidate = _producer_aware_root()
    candidate["problem"]["production_obligations"][0]["replacement_links"] = [
        {
            "replaces_obligation_id": "obligation:fabricated",
            "replacement_reason": "A formation candidate has no predecessor authority.",
        }
    ]

    with pytest.raises(receive_module.CanonicalRootPreviewError) as caught:
        receive_module.validate_canonical_root_technical_approach(candidate)

    assert caught.value.rule_id == "initial-obligation-lineage"
    assert caught.value.field == "replacement_links"
    assert caught.value.goal_id == "goal:preview"
    assert caught.value.obligation_id == "obligation:preview"
    assert caught.value.cue_path == "root.problem.production_obligations"


@pytest.mark.parametrize(
    ("stolen_field", "rule_id"),
    [
        ("obligation", "obligation-id-projection"),
        ("requirement", "requirement-id-projection"),
    ],
)
def test_canonical_root_preview_rejects_retired_identity_owned_by_another_goal(
    stolen_field, rule_id
):
    previous_candidate = _producer_aware_root()
    problem = previous_candidate["problem"]
    problem["goals"][1]["requires_deliverable"] = True
    problem["deliverable_requirements"].append(
        {
            "id": "requirement:diagnostics",
            "referee_goal_id": "goal:diagnostics",
            "production_obligation_id": "obligation:diagnostics",
            "deliverable": "diagnostics.cue",
        }
    )
    problem["production_obligations"].append(
        {
            "id": "obligation:diagnostics",
            "referee_goal_id": "goal:diagnostics",
            "deliverable_requirement_id": "requirement:diagnostics",
            "deliverable": "diagnostics.cue",
            "eligibility_predicate": "Can verify structured diagnostics.",
            "replacement_links": [],
        }
    )
    problem["patch_plan"][1]["production_obligation_ids"] = [
        "obligation:diagnostics"
    ]
    previous = receive_module.validate_canonical_root_technical_approach(
        previous_candidate
    )

    successor = json.loads(json.dumps(previous))
    successor_problem = successor["problem"]
    successor_problem["goals"] = successor_problem["goals"][:1]
    successor_problem["deliverable_requirements"] = successor_problem[
        "deliverable_requirements"
    ][:1]
    successor_problem["production_obligations"] = successor_problem[
        "production_obligations"
    ][:1]
    requirement = successor_problem["deliverable_requirements"][0]
    obligation = successor_problem["production_obligations"][0]
    requirement.update(
        {
            "id": (
                "requirement:diagnostics"
                if stolen_field == "requirement"
                else "requirement:preview-v2"
            ),
            "production_obligation_id": (
                "obligation:diagnostics"
                if stolen_field == "obligation"
                else "obligation:preview-v2"
            ),
            "deliverable": "changed.cue",
        }
    )
    obligation.update(
        {
            "id": requirement["production_obligation_id"],
            "deliverable_requirement_id": requirement["id"],
            "deliverable": "changed.cue",
            "replacement_links": [
                {
                    "replaces_obligation_id": "obligation:preview",
                    "replacement_reason": "The deliverable changed.",
                }
            ],
        }
    )
    successor_problem["patch_plan"][0]["production_obligation_ids"] = [
        obligation["id"]
    ]
    successor_problem["patch_plan"][1]["production_obligation_ids"] = []

    with pytest.raises(receive_module.CanonicalRootPreviewError) as caught:
        receive_module.validate_canonical_root_technical_approach(
            successor, previous=previous
        )

    assert caught.value.rule_id == rule_id


def test_canonical_root_preview_round_trip_does_not_mutate_git(tmp_path):
    subprocess.run(["git", "-C", str(tmp_path), "init", "-b", "main"], check=True, capture_output=True)
    (tmp_path / "sentinel.txt").write_text("unchanged\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "sentinel.txt"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            *git_wrapper.identity_config_args(),
            "commit",
            "-m",
            "base",
        ],
        check=True,
        capture_output=True,
    )

    def snapshot():
        return (
            subprocess.check_output(["git", "-C", str(tmp_path), "rev-parse", "HEAD"], text=True),
            subprocess.check_output(["git", "-C", str(tmp_path), "ls-tree", "-r", "HEAD"], text=True),
            subprocess.check_output(["git", "-C", str(tmp_path), "status", "--porcelain"], text=True),
        )

    carrier = _producer_preview_carrier()
    base_candidate = _producer_aware_root()
    for field_name in receive_module.CANONICAL_ROOT_PREVIEW_FIELDS:
        base_candidate["problem"].pop(field_name)
    before = snapshot()
    preview = receive_module.preview_canonical_root_technical_approach(
        base_candidate,
        carrier=carrier,
    )
    after = snapshot()

    assert after == before
    assert preview.lifecycle_status == "preview_only"
    assert preview.authorable is False
    assert preview.preview_artifact == "technical-approach-plan.cue"
    assert preview.body_sha256 == preview.body_digest
    assert set(preview.cohort_files) == {patch_series_bodies.ROOT_APPROACH_PATH}
    assert "goals" not in base_candidate["problem"]
    parsed = BodyCodecClient().parse(
        patch_series_bodies.ROOT_APPROACH_CONTEXT,
        preview.canonical_cue.encode("utf-8"),
        expected=patch_series_bodies.ROOT_APPROACH_CONTRACT,
    )
    assert parsed["problem"]["production_obligations"][0]["id"] == "obligation:preview"


def test_producer_aware_formation_preparation_reconstructs_final_root(tmp_path):
    candidate = _producer_aware_root()
    problem = candidate["problem"]
    problem.pop("deliverable_requirements")
    problem.pop("production_obligations")
    carrier = _producer_preview_carrier()
    carrier["deliverable_requirements"][0].update(
        {"id": "requirement:temporary", "production_obligation_id": "obligation:temporary"}
    )
    carrier["production_obligations"][0].update(
        {"id": "obligation:temporary", "deliverable_requirement_id": "requirement:temporary"}
    )
    carrier["patch_plan"][0]["production_obligation_ids"] = ["obligation:temporary"]
    candidate_before = json.loads(json.dumps(candidate))
    carrier_before = json.loads(json.dumps(carrier))

    preview = receive_module.prepare_producer_aware_formation(
        {
            "technical_approach": candidate,
            "steps": {
                "normalize_problem": _normalized_referee_source(candidate)
            },
        },
        tmp_path,
        carrier=carrier,
    )

    digest = receive_module.production_obligation_identity(
        "goal:preview", "technical approach plan.cue"
    )
    prepared_problem = preview.technical_approach["problem"]
    assert prepared_problem["deliverable_requirements"][0]["id"] == f"requirement:{digest}"
    assert prepared_problem["production_obligations"][0] == {
        "id": f"obligation:{digest}",
        "referee_goal_id": "goal:preview",
        "deliverable_requirement_id": f"requirement:{digest}",
        "deliverable": "technical approach plan.cue",
        "eligibility_predicate": "Can produce and test the canonical CUE body.",
        "replacement_links": [],
    }
    assert prepared_problem["patch_plan"][0]["production_obligation_ids"] == [
        f"obligation:{digest}"
    ]
    assert candidate == candidate_before
    assert carrier == carrier_before
    assert preview.lifecycle_status == "preview_only"
    assert preview.authorable is False
    assert preview.preview_artifact == "technical-approach-plan.cue"


@pytest.mark.parametrize(
    (
        "record_name",
        "expected_pointer",
        "expected_cue_path",
        "expected_obligation_id",
    ),
    [
        (
            "deliverable_requirements",
            "/deliverable_requirements/0/deliverable",
            "root.deliverable_requirements[0].deliverable",
            "",
        ),
        (
            "production_obligations",
            "/production_obligations/0/deliverable",
            "root.production_obligations[0].deliverable",
            "obligation:preview",
        ),
    ],
)
def test_producer_aware_preparation_locates_invalid_pair_deliverable(
    tmp_path,
    record_name,
    expected_pointer,
    expected_cue_path,
    expected_obligation_id,
):
    carrier = _producer_preview_carrier()
    carrier[record_name][0]["deliverable"] = 7

    with pytest.raises(receive_module.CanonicalRootPreviewError) as caught:
        receive_module.prepare_producer_aware_formation(
            _producer_aware_root(), tmp_path, carrier=carrier
        )

    assert caught.value.rule_id == "deliverable-normalization"
    assert caught.value.field == "deliverable"
    assert caught.value.json_pointer == expected_pointer
    assert caught.value.cue_path == expected_cue_path
    assert caught.value.goal_id == "goal:preview"
    assert caught.value.obligation_id == expected_obligation_id


def test_producer_aware_reform_preparation_closes_changed_projection(tmp_path):
    initial_candidate = _producer_aware_root()
    initial_carrier = _producer_preview_carrier()
    initial = receive_module.prepare_producer_aware_formation(
        initial_candidate,
        tmp_path,
        carrier=initial_carrier,
    ).technical_approach
    predecessor = initial["problem"]["production_obligations"][0]["id"]

    reform_candidate = json.loads(json.dumps(initial))
    reform_carrier = _producer_preview_carrier()
    reform_carrier["deliverable_requirements"][0].update(
        {
            "id": "requirement:temporary-v2",
            "production_obligation_id": "obligation:temporary-v2",
            "deliverable": "producer-aware-plan.cue",
        }
    )
    reform_carrier["production_obligations"][0].update(
        {
            "id": "obligation:temporary-v2",
            "deliverable_requirement_id": "requirement:temporary-v2",
            "deliverable": "producer-aware-plan.cue",
            "replaces_obligation_id": "temporary-predecessor-is-not-authority",
            "replacement_reason": "The canonical artifact name changed.",
        }
    )
    reform_carrier["patch_plan"][0]["production_obligation_ids"] = [
        "obligation:temporary-v2"
    ]

    preview = receive_module.prepare_producer_aware_reform(
        {"technical_approach": reform_candidate},
        initial,
        tmp_path,
        carrier=reform_carrier,
    )

    obligation = preview.technical_approach["problem"]["production_obligations"][0]
    assert obligation["id"] != predecessor
    assert obligation["replacement_links"] == [
        {
            "replaces_obligation_id": predecessor,
            "replacement_reason": "The canonical artifact name changed.",
        }
    ]
    assert preview.technical_approach["problem"]["patch_plan"][0][
        "production_obligation_ids"
    ] == [obligation["id"]]
    assert preview.preview_artifact == "technical-approach-plan.cue"


def test_producer_aware_reform_accepts_successor_as_next_predecessor(tmp_path):
    initial = receive_module.prepare_producer_aware_formation(
        _producer_aware_root(),
        tmp_path,
        carrier=_producer_preview_carrier(),
    ).technical_approach

    successor_carrier = _producer_preview_carrier()
    successor_carrier["deliverable_requirements"][0]["deliverable"] = (
        "producer-aware-plan.cue"
    )
    successor_carrier["production_obligations"][0].update(
        {
            "deliverable": "producer-aware-plan.cue",
            "replacement_reason": "The canonical artifact name changed.",
        }
    )
    successor = receive_module.prepare_producer_aware_reform(
        initial,
        initial,
        tmp_path,
        carrier=successor_carrier,
    ).technical_approach
    successor_obligation = successor["problem"]["production_obligations"][0]
    assert successor_obligation["replacement_links"] == [
        {
            "replaces_obligation_id": initial["problem"]["production_obligations"][0][
                "id"
            ],
            "replacement_reason": "The canonical artifact name changed.",
        }
    ]

    stable_candidate = json.loads(json.dumps(successor))
    stable_candidate["problem"]["goals"][0]["actor"] = "The independent referee"
    stable_carrier = json.loads(json.dumps(successor_carrier))
    stable_carrier["goals"][0]["actor"] = "The independent referee"
    stable_preview = receive_module.prepare_producer_aware_reform(
        stable_candidate,
        successor,
        tmp_path,
        carrier=stable_carrier,
    )
    stable = stable_preview.technical_approach
    stable_obligation = stable["problem"]["production_obligations"][0]

    assert stable_obligation["id"] == successor_obligation["id"]
    assert stable_obligation["deliverable_requirement_id"] == successor_obligation[
        "deliverable_requirement_id"
    ]
    assert stable_obligation["replacement_links"] == successor_obligation[
        "replacement_links"
    ]
    assert stable["problem"]["patch_plan"][0]["production_obligation_ids"] == [
        stable_obligation["id"]
    ]
    assert stable_preview.preview_artifact == "technical-approach-plan.cue"


def test_producer_aware_reform_preparation_requires_explicit_changed_goal_lineage(
    tmp_path,
):
    initial = receive_module.prepare_producer_aware_formation(
        _producer_aware_root(),
        tmp_path,
        carrier=_producer_preview_carrier(),
    ).technical_approach
    predecessor = initial["problem"]["production_obligations"][0]["id"]

    reform_candidate = json.loads(json.dumps(initial))
    reform_candidate["problem"]["goals"][0]["id"] = "goal:renamed"
    reform_carrier = _producer_preview_carrier()
    reform_carrier["goals"][0]["id"] = "goal:renamed"
    reform_carrier["deliverable_requirements"][0]["referee_goal_id"] = (
        "goal:renamed"
    )
    reform_carrier["production_obligations"][0].update(
        {
            "referee_goal_id": "goal:renamed",
            "replaces_obligation_id": predecessor,
            "replacement_reason": "The normalized goal identity changed.",
        }
    )

    preview = receive_module.prepare_producer_aware_reform(
        reform_candidate,
        initial,
        tmp_path,
        carrier=reform_carrier,
    )

    obligation = preview.technical_approach["problem"]["production_obligations"][0]
    digest = receive_module.production_obligation_identity(
        "goal:renamed", "technical approach plan.cue"
    )
    assert obligation["id"] == f"obligation:{digest}"
    assert obligation["replacement_links"] == [
        {
            "replaces_obligation_id": predecessor,
            "replacement_reason": "The normalized goal identity changed.",
        }
    ]

    fabricated = json.loads(json.dumps(reform_carrier))
    fabricated["production_obligations"][0]["replaces_obligation_id"] = (
        "obligation:fabricated"
    )
    with pytest.raises(receive_module.CanonicalRootPreviewError) as caught:
        receive_module.prepare_producer_aware_reform(
            reform_candidate,
            initial,
            tmp_path,
            carrier=fabricated,
        )
    assert caught.value.rule_id == "successor-predecessor-link"


def test_producer_aware_formation_uses_normalization_in_formal_prompt(
    monkeypatch, tmp_path
):
    candidate = _producer_aware_root()
    normalized = _normalized_referee_source(candidate)
    formation = {
        "technical_approach": candidate,
        "steps": {"normalize_problem": normalized},
    }
    carrier = _producer_preview_carrier()
    calls = []

    def fake_run_json(repo, **kwargs):
        calls.append((repo, kwargs))
        return {"ok": True, "raw": json.dumps(carrier)}

    monkeypatch.setattr(receive_module.codex_exec, "run_json", fake_run_json)

    preview = receive_module.prepare_producer_aware_formation(formation, tmp_path)

    assert preview.lifecycle_status == "preview_only"
    assert len(calls) == 1
    repo, kwargs = calls[0]
    assert repo == tmp_path.resolve()
    assert kwargs["schema"] == receive_module.build_canonical_root_preview_schema()
    assert '"problem": "The game lacks a verifiable first battle loop."' in kwargs["prompt"]
    assert "deliverable_required" not in kwargs["prompt"]
    assert kwargs["schema_filename"] == "patch_series-producer-aware-formation.schema.json"


def test_formation_preparation_rejects_referee_drift_from_normalization_before_model(
    monkeypatch, tmp_path
):
    candidate = _producer_aware_root()
    normalized = _normalized_referee_source(candidate)
    normalized["success_criteria"][0]["requires_deliverable"] = False
    monkeypatch.setattr(
        receive_module.codex_exec,
        "run_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("normalization drift must fail before model preparation")
        ),
    )

    with pytest.raises(receive_module.CanonicalRootPreviewError) as caught:
        receive_module.prepare_producer_aware_formation(
            {
                "technical_approach": candidate,
                "steps": {"normalize_problem": normalized},
            },
            tmp_path,
        )

    assert caught.value.rule_id == "normalized-referee-source"
    assert caught.value.field == "requires_deliverable"
    assert caught.value.json_pointer == "/problem/goals/0/requires_deliverable"


def test_reform_preparation_preserves_normalized_referee_source(tmp_path):
    previous = receive_module.prepare_producer_aware_formation(
        _producer_aware_root(),
        tmp_path,
        carrier=_producer_preview_carrier(),
    ).technical_approach
    candidate = json.loads(json.dumps(previous))
    normalized = _normalized_referee_source(candidate)

    preview = receive_module.prepare_producer_aware_reform(
        {
            "technical_approach": candidate,
            "steps": {"normalize_problem": normalized},
        },
        previous,
        tmp_path,
        carrier=_producer_preview_carrier(),
    )

    assert [
        goal["requires_deliverable"]
        for goal in preview.technical_approach["problem"]["goals"]
    ] == [
        criterion["requires_deliverable"]
        for criterion in normalized["success_criteria"]
    ]
    assert preview.lifecycle_status == "preview_only"
    assert preview.authorable is False


def test_explicit_reform_carries_problem_normalization_into_preparation(
    monkeypatch, tmp_path
):
    candidate = _producer_aware_root()
    normalized = _normalized_referee_source(candidate)
    monkeypatch.setattr(
        receive_module,
        "_run_reform_step",
        lambda step, *_args, **_kwargs: normalized if step == "problem" else {},
    )
    monkeypatch.setattr(
        receive_module,
        "_targeted_revision_plan",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        receive_module,
        "_changed_node_ids_for_step",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        receive_module,
        "_assemble_from_components",
        lambda _components: candidate,
    )

    reform = receive_module._reform_technical_approach(
        tmp_path,
        _patch_series_view(),
        candidate,
        [],
        ["problem"],
    )

    assert reform["ok"] is True
    assert reform["steps"] == {"normalize_problem": normalized}
    assert reform["steps"]["normalize_problem"] is not normalized


def test_reform_preparation_rejects_referee_drift_from_normalization_before_model(
    monkeypatch, tmp_path
):
    previous = receive_module.prepare_producer_aware_formation(
        _producer_aware_root(),
        tmp_path,
        carrier=_producer_preview_carrier(),
    ).technical_approach
    candidate = json.loads(json.dumps(previous))
    normalized = _normalized_referee_source(candidate)
    normalized["success_criteria"][0]["requires_deliverable"] = False
    monkeypatch.setattr(
        receive_module.codex_exec,
        "run_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("normalization drift must fail before reform preparation")
        ),
    )

    with pytest.raises(receive_module.CanonicalRootPreviewError) as caught:
        receive_module.prepare_producer_aware_reform(
            {
                "technical_approach": candidate,
                "steps": {"normalize_problem": normalized},
            },
            previous,
            tmp_path,
        )

    assert caught.value.rule_id == "normalized-referee-source"
    assert caught.value.field == "requires_deliverable"
    assert caught.value.json_pointer == "/problem/goals/0/requires_deliverable"


def test_normalization_owns_the_only_producer_eligibility_field():
    schema = receive_module.build_success_criterion_schema()
    prompt = receive_module._normalize_problem_prompt(_patch_series_view())
    criterion = dict(_normalized_problem()["success_criteria"][0])
    consumers = receive_module.PRODUCER_PREPARATION_CONSUMER_FIELDS

    assert receive_module.PRODUCER_DETERMINATION_FIELD == "requires_deliverable"
    assert set(consumers) == {
        "schema",
        "prompt",
        "parser",
        "lint",
        "reconstruction",
        "identity_continuity",
        "successor_lineage",
        "assignment_generation",
    }
    assert set(consumers.values()) == {"requires_deliverable"}
    with pytest.raises(TypeError):
        consumers["parser"] = "deliverable_required"

    assert receive_module.PRODUCER_DETERMINATION_FIELD in schema["required"]
    assert "deliverable_required" not in json.dumps(schema)
    assert "deliverable_required" not in prompt
    assert "Canonical Referee Criterion" in prompt
    assert "Producer Eligibility Predicate" in prompt
    assert receive_module._parse_success_criterion(criterion) == criterion

    aliased = dict(criterion)
    aliased["deliverable_required"] = aliased.pop("requires_deliverable")
    assert receive_module._parse_success_criterion(aliased) is None


def test_formation_preparation_cannot_redetermine_normalized_eligibility(tmp_path):
    candidate = _producer_aware_root()
    carrier = _producer_preview_carrier()
    carrier["goals"][0]["requires_deliverable"] = False
    carrier["deliverable_requirements"] = []
    carrier["production_obligations"] = []
    carrier["patch_plan"][0]["production_obligation_ids"] = []

    with pytest.raises(receive_module.CanonicalRootPreviewError) as caught:
        receive_module.prepare_producer_aware_formation(
            candidate, tmp_path, carrier=carrier
        )

    assert caught.value.rule_id == "canonical-referee-byte-stable"


@pytest.mark.parametrize(
    ("mutation", "rule_id"),
    [
        (
            lambda goals: goals[1].pop("requires_deliverable"),
            "requires-deliverable-required",
        ),
        (
            lambda goals: goals[1].update(
                {"deliverable_required": goals[1].pop("requires_deliverable")}
            ),
            "requires-deliverable-alias",
        ),
        (
            lambda goals: goals[1].update({"requires_deliverable": "false"}),
            "requires-deliverable-boolean",
        ),
    ],
)
def test_partial_producer_determination_cannot_bypass_preparation(
    mutation, rule_id
):
    candidate = _producer_aware_root()
    goals = candidate["problem"]["goals"]
    mutation(goals)

    with pytest.raises(receive_module.CanonicalRootPreviewError) as caught:
        receive_module._has_explicit_producer_determinations(candidate)

    assert caught.value.rule_id == rule_id

    for goal in goals:
        if isinstance(goal, dict):
            goal.pop("requires_deliverable", None)
            goal.pop("deliverable_required", None)
    assert receive_module._has_explicit_producer_determinations(candidate) is False


def test_default_formation_and_reform_cannot_select_historical_preparation():
    previous = _producer_aware_root()
    revised = json.loads(json.dumps(previous))
    for goal in revised["problem"]["goals"]:
        goal.pop("requires_deliverable")

    assert (
        receive_module._producer_preparation_transition(previous)
        == "formation"
    )
    assert (
        receive_module._producer_preparation_transition(
            previous, previous=previous
        )
        == "reform"
    )
    assert receive_module._producer_preparation_transition(revised) == "formation"
    assert (
        receive_module._producer_preparation_transition(
            revised, previous=revised
        )
        == "formation"
    )

    with pytest.raises(receive_module.CanonicalRootPreviewError) as caught:
        receive_module._producer_preparation_transition(
            revised, previous=previous
        )

    assert caught.value.rule_id == "requires-deliverable-required"
    assert caught.value.field == "requires_deliverable"
    assert caught.value.goal_id == "goal:preview"
    assert "historical writer fallback" in caught.value.detail


def test_formation_and_stable_reform_are_nonpublishing_transitions(tmp_path):
    subprocess.run(
        ["git", "-C", str(tmp_path), "init", "-b", "main"],
        check=True,
        capture_output=True,
    )
    (tmp_path / "sentinel.txt").write_text("unchanged\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "sentinel.txt"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            *git_wrapper.identity_config_args(),
            "commit",
            "-m",
            "base",
        ],
        check=True,
        capture_output=True,
    )

    def snapshot():
        return (
            subprocess.check_output(
                ["git", "-C", str(tmp_path), "rev-parse", "HEAD"], text=True
            ),
            subprocess.check_output(
                ["git", "-C", str(tmp_path), "status", "--porcelain"], text=True
            ),
        )

    before = snapshot()
    formed = receive_module.prepare_producer_aware_formation(
        _producer_aware_root(),
        tmp_path,
        carrier=_producer_preview_carrier(),
    )
    previous = formed.technical_approach
    predecessor = previous["problem"]["production_obligations"][0]

    candidate = json.loads(json.dumps(previous))
    carrier = _producer_preview_carrier()
    candidate["problem"]["goals"][0]["actor"] = "The independent referee"
    carrier["goals"][0]["actor"] = "The independent referee"
    carrier["deliverable_requirements"][0]["deliverable"] = (
        " technical\tapproach plan.cue "
    )
    carrier["production_obligations"][0].update(
        {
            "deliverable": "technical approach   plan.cue",
            "eligibility_predicate": (
                "Can produce, inspect, and test the canonical CUE body."
            ),
        }
    )

    reformed = receive_module.prepare_producer_aware_reform(
        candidate,
        previous,
        tmp_path,
        carrier=carrier,
    )
    obligation = reformed.technical_approach["problem"]["production_obligations"][0]

    assert obligation["id"] == predecessor["id"]
    assert obligation["deliverable_requirement_id"] == predecessor[
        "deliverable_requirement_id"
    ]
    assert obligation["replacement_links"] == predecessor["replacement_links"] == []
    assert reformed.body_sha256 != formed.body_sha256
    assert reformed.lifecycle_status == formed.lifecycle_status == "preview_only"
    assert reformed.authorable is formed.authorable is False
    assert snapshot() == before


def test_producer_aware_transition_sequence_matches_formal_frame(tmp_path):
    formed = receive_module.prepare_producer_aware_formation(
        _producer_aware_root(),
        tmp_path,
        carrier=_producer_preview_carrier(),
    )
    formed_obligation = formed.technical_approach["problem"][
        "production_obligations"
    ][0]

    retained_candidate = json.loads(json.dumps(formed.technical_approach))
    retained_carrier = _producer_preview_carrier()
    retained_carrier["production_obligations"][0]["eligibility_predicate"] = (
        "Can produce, inspect, and independently test the canonical CUE body."
    )
    retained = receive_module.prepare_producer_aware_reform(
        retained_candidate,
        formed.technical_approach,
        tmp_path,
        carrier=retained_carrier,
    )
    retained_obligation = retained.technical_approach["problem"][
        "production_obligations"
    ][0]

    successor_candidate = json.loads(json.dumps(retained.technical_approach))
    successor_carrier = _producer_preview_carrier()
    successor_carrier["deliverable_requirements"][0]["deliverable"] = (
        "producer-aware-plan.cue"
    )
    successor_carrier["production_obligations"][0].update(
        {
            "deliverable": "producer-aware-plan.cue",
            "replacement_reason": "The canonical artifact name changed.",
        }
    )
    successor = receive_module.prepare_producer_aware_reform(
        successor_candidate,
        retained.technical_approach,
        tmp_path,
        carrier=successor_carrier,
    )
    successor_obligation = successor.technical_approach["problem"][
        "production_obligations"
    ][0]

    assert formed_obligation["replacement_links"] == []
    assert retained_obligation["id"] == formed_obligation["id"]
    assert retained_obligation["deliverable_requirement_id"] == formed_obligation[
        "deliverable_requirement_id"
    ]
    assert retained_obligation["replacement_links"] == []
    assert retained.body_sha256 != formed.body_sha256
    assert successor_obligation["id"] != retained_obligation["id"]
    assert successor_obligation["deliverable_requirement_id"] != retained_obligation[
        "deliverable_requirement_id"
    ]
    assert successor_obligation["replacement_links"] == [
        {
            "replaces_obligation_id": retained_obligation["id"],
            "replacement_reason": "The canonical artifact name changed.",
        }
    ]
    assert all(
        preview.lifecycle_status == "preview_only"
        and preview.authorable is False
        and preview.preparation_publishes is False
        and preview.historical_writer_path == "technical-approach-plan.json"
        and dict(preview.consumer_fields)
        == {
            name: "requires_deliverable"
            for name in (
                "schema",
                "prompt",
                "parser",
                "lint",
                "reconstruction",
                "identity_continuity",
                "successor_lineage",
                "assignment_generation",
            )
        }
        and dict(preview.prepared_root_shape)
        == {
            "goal_determination_field": "requires_deliverable",
            "requirement_field": "deliverable_requirements",
            "obligation_field": "production_obligations",
            "assignment_field": "production_obligation_ids",
        }
        for preview in (formed, retained, successor)
    )


@pytest.mark.parametrize(
    "metadata",
    [
        {"lifecycle_status": "published"},
        {"authorable": True},
    ],
)
def test_preparation_preview_status_is_not_caller_selectable(metadata):
    with pytest.raises(TypeError):
        receive_module.CanonicalRootTechnicalApproachPreview(
            technical_approach={},
            canonical_cue="package preview\n",
            body_sha256="0" * 64,
            cohort_files={},
            **metadata,
        )


def test_receive_imports_precedent_and_codex_exec_without_later_phases():
    source = Path(receive_module.__file__).read_text(encoding="utf-8")

    assert "from ai_org import engineering_precedent_store" in source
    assert "import ai_org.patchwork_queue.codex_exec as codex_exec" in source
    assert "ai_org.patch_authoring" not in source
    assert "ai_org.maintainer_merge" not in source
    assert not any("\u3040" <= character <= "\u9fff" for character in source)


def _patch_successful_approach_steps(monkeypatch) -> None:
    monkeypatch.setattr(receive_module, "_normalize_problem", lambda patch_series_view, context=None: _normalized_problem())
    monkeypatch.setattr(receive_module, "_extract_constraints", lambda *args, **kwargs: _constraints())
    monkeypatch.setattr(
        receive_module.engineering_precedent_store,
        "build_from_patch_series",
        lambda *args, **kwargs: {"terms": {}, "processed_terms": ["battle loop"], "expanded": [], "hits": [], "failed": {}},
    )
    monkeypatch.setattr(receive_module, "_build_prior_art_map", lambda *args, **kwargs: _prior_art_map())
    monkeypatch.setattr(receive_module, "_generate_candidates", lambda *args, **kwargs: _candidates())
    monkeypatch.setattr(receive_module, "_evaluate_candidates", lambda *args, **kwargs: _evaluations())
    monkeypatch.setattr(receive_module, "_select_approach", lambda *args, **kwargs: _decision())
    monkeypatch.setattr(receive_module, "_implementation_strategy", lambda *args, **kwargs: _implementation())
    monkeypatch.setattr(receive_module, "_domain_specification", lambda *args, **kwargs: _domain_specification())
    monkeypatch.setattr(receive_module, "_right_size_patch_plan", lambda *args, **kwargs: _patch_plan())
    monkeypatch.setattr(receive_module, "_surface_risks", lambda *args, **kwargs: _risks())


def _assert_codex_valid_object_schema(schema: dict[str, object]) -> None:
    assert "allOf" not in schema
    assert "anyOf" not in schema
    assert "oneOf" not in schema
    if schema.get("type") == "object":
        assert schema["additionalProperties"] is False
        assert sorted(schema["required"]) == sorted(schema["properties"])
        for subschema in schema["properties"].values():
            _assert_codex_valid_object_schema(subschema)
    if schema.get("type") == "array":
        _assert_codex_valid_object_schema(schema["items"])


def _patch_series_view() -> dict[str, object]:
    return {
        "raw_request": "Add a playable battle slice.",
        "working_title": "Playable Battle Slice",
        "request_type": "game_app",
        "problem_or_motivation": "Players need a first battle loop.",
        "intended_users_or_jobs": "Players and game contributors need a verifiable combat job.",
        "desired_outcomes_success": "A named enemy can be defeated with a named spell and progress is recorded.",
        "affected_area_platform": "gameplay",
        "tech_stack": {
            "build_strategy": "",
            "engine": "",
            "framework": "",
            "language": "",
            "platform": "",
            "rationale": "",
            "provenance": "unspecified",
        },
        "user_experience_requirements": _ux_requirements(),
        "background_facts": "The first proof moment slice centers on a Slime and Spark spell.",
        "constraints_assumptions": ["Keep the battle loop small enough for automated tests."],
        "references": [],
        "grounding_provenance": "Test fixture grounding.",
        "open_questions": [],
        "non_goals_out_of_scope": ["Do not add a full campaign."],
        "proposal_hint": "Add a small battle loop with a named enemy and spell.",
        "alternatives_considered": ["Defer combat."],
    }


def _normalized_problem() -> dict[str, object]:
    return {
        "problem": "The game lacks a verifiable first battle loop.",
        "affected": "Players and game contributors.",
        "current_inadequacy": "There is no named enemy, action, or progress condition for combat.",
        "success_criteria": [
            {
                "actor": "player",
                "capability": {
                    "action": "Cast Spark at a Slime in the meadow encounter.",
                    "preconditions": ["The player is in Green Meadow with Spark learned."],
                },
                "verifiable_outcome": {
                    "expected_state": "The Slime is defeated and the meadow gate visibly opens according to user_experience_requirements.",
                    "evidence": "Battle log records Spark damage, visible feedback appears, and gate state changes to open.",
                },
                "verification": {
                    "method": "automated_test",
                    "check": "Assert Spark defeats Slime and sets meadow_gate_open true.",
                },
                "ux_trace": "user_experience_requirements.core_status_surfaces.player_status",
                "requires_deliverable": True,
            }
        ],
        "non_goals": ["Do not add a full campaign."],
        "open_questions": [],
    }


def _normalized_referee_source(
    root: dict[str, object],
) -> dict[str, object]:
    normalized = deepcopy(_normalized_problem())
    normalized["success_criteria"] = [
        {
            field_name: deepcopy(goal[field_name])
            for field_name in receive_module.SUCCESS_CRITERION_FIELDS
        }
        for goal in root["problem"]["goals"]
    ]
    return normalized


def _constraints() -> dict[str, object]:
    return {
        "hard_constraints": [
            {
                "statement": "Keep patch series receive isolated from patch and merge modules.",
                "derivation": {"from": "repo", "trace": "ai_org.patchwork_queue.receive imports only patch series helpers."},
                "implication": {
                    "must": "Reuse ai_org.patchwork_queue.codex_exec for Codex-backed approach nodes.",
                    "must_not": "Import patch or merge phase modules.",
                },
            }
        ],
        "soft_preferences": [
            {
                "statement": "Prefer repo-native gameplay modules.",
                "derivation": {"from": "repo", "trace": "Existing game modules own battle behavior."},
                "rationale": "Local ownership keeps the first slice easy to test.",
            }
        ],
    }


def _prior_art_map() -> dict[str, object]:
    return {
        "patterns": [
            {
                "name": "Reference-first prior-art synthesis",
                "source": {"reference_concept": "battle loop", "facet_kind": "design", "where": "Reference."},
                "when_applies": "When a first proof moment loop needs named content.",
                "tradeoffs": {"pros": ["Grounded design."], "cons": ["Requires explicit content slots."]},
                "disposition": {"choice": "adopt", "why": "It gives the candidate concrete content."},
                "traces_to": ["normalized_problem.success_criteria[0]"],
            }
        ]
    }


def _prior_art_map_with_facet(reference_concept: str, facet_kind: str) -> dict[str, object]:
    return {
        "patterns": [
            {
                "name": f"Prior-art pattern {index}",
                "source": {
                    "reference_concept": reference_concept,
                    "facet_kind": facet_kind,
                    "where": "Reference." if facet_kind != "none" else "patch series and repository.",
                },
                "when_applies": "When the patch series needs a grounded implementation direction.",
                "tradeoffs": {"pros": ["Grounded direction."], "cons": ["Requires validation."]},
                "disposition": {"choice": "adopt", "why": "It fits the patch series constraints."},
                "traces_to": ["normalized_problem.success_criteria[0]"],
            }
            for index in range(1, 4)
        ]
    }


def _prior_art_map_for_terms(*term_facets: tuple[str, str]) -> dict[str, object]:
    patterns = []
    expanded = list(term_facets)
    while len(expanded) < 3:
        expanded.append(term_facets[-1])
    for index, (reference_concept, facet_kind) in enumerate(expanded[:3], start=1):
        patterns.append(
            {
                "name": f"{reference_concept} prior-art pattern {index}",
                "source": {
                    "reference_concept": reference_concept,
                    "facet_kind": facet_kind,
                    "where": "Reference." if facet_kind != "none" else "patch series and repository.",
                },
                "when_applies": "When the patch series needs a grounded implementation direction.",
                "tradeoffs": {"pros": ["Grounded direction."], "cons": ["Requires validation."]},
                "disposition": {"choice": "adopt", "why": "It fits the patch series constraints."},
                "traces_to": ["normalized_problem.success_criteria[0]"],
            }
        )
    return {"patterns": patterns}


def _precedent_candidate(kind: str, summary: str) -> dict[str, str]:
    return {
        "kind": kind,
        "term": "battle loop",
        "summary": summary,
        "snippet": f"{kind} snippet",
        "pitfalls": f"{kind} pitfalls",
        "structure": f"{kind} structure",
        "rationale": f"{kind} rationale",
        "when_to_use": f"{kind} use",
        "tradeoffs": f"{kind} tradeoffs",
        "implementation_hooks": f"{kind} hooks",
        "quality_attributes": f"{kind} quality",
        "evidence": f"{kind} evidence",
        "delta_claim": f"{kind} delta",
        "lang_env_version": "Python 3.12",
        "author_level": "maintainer",
        "source_url": "https://example.test/reference",
        "found_via": "test",
    }


def _precedent_candidate_for_term(term: str, kind: str, summary: str) -> dict[str, str]:
    candidate = _precedent_candidate(kind, summary)
    candidate["term"] = term
    candidate["structure"] = f"{term} {kind} structure"
    candidate["rationale"] = f"{term} {kind} rationale"
    candidate["when_to_use"] = f"{term} {kind} use"
    candidate["tradeoffs"] = f"{term} {kind} tradeoffs"
    return candidate


def _candidate(candidate_id: str, kind: str, name: str) -> dict[str, object]:
    return {
        "id": candidate_id,
        "name": name,
        "kind": kind,
        "summary": f"{name} builds the battle slice.",
        "stack_requirement": {
            "build_strategy": "framework_based",
            "engine": "",
            "framework": name,
            "language": "JavaScript",
            "platform": "browser",
            "authoring_model": "text_first",
            "verification_model": "headless_ci",
        },
        "first_proof_moment": {
            "user_actions": ["Cast Spark at Slime."],
            "named_content": [
                {"name": "Green Meadow", "kind": "location"},
                {"name": "Slime", "kind": "enemy"},
                {"name": "Spark", "kind": "spell"},
            ],
            "win_or_progress_condition": "Slime defeated and meadow gate opens.",
        },
        "core_systems": ["battle resolution"],
        "draws_on": ["Reference-first prior-art synthesis"],
    }


def _candidates() -> dict[str, object]:
    return {
        "candidates": [
            _candidate("minimal", "minimal_local", "Minimal"),
            _candidate("repo_native", "repo_native", "Repo Native"),
        ]
    }


def _evaluations() -> dict[str, object]:
    return {
        "evaluations": [
            {"candidate_id": "minimal", "scores": _scores(problem_fit=("medium", "Narrow battle coverage."))},
            {"candidate_id": "repo_native", "scores": _scores(problem_fit=("high", "Covers the named loop."))},
        ]
    }


def _scores(**overrides: tuple[str, str]) -> dict[str, object]:
    defaults = {
        "problem_fit": ("high", "Matches the success criterion."),
        "repo_fit": ("high", "Fits module boundaries."),
        "complexity": ("medium", "Adds focused behavior."),
        "quality_attributes": ("high", "Preserves deterministic resolution."),
        "compat_migration": ("high", "No migration required."),
        "testability": ("high", "Can be verified with an automated test."),
        "operability": ("medium", "Battle log gives visibility."),
        "reversibility": ("high", "Local behavior can be replaced."),
        "risk": ("medium", "Balance may need tuning."),
    }
    defaults.update(overrides)
    return {field: {"rating": rating, "reason": reason} for field, (rating, reason) in defaults.items()}


def _decision() -> dict[str, object]:
    return {
        "selected_candidate_id": "repo_native",
        "arguments": [
            {
                "role": "support",
                "about_candidate_id": "repo_native",
                "claim": "Repo Native best implements the named battle loop.",
                "grounds": "Its evaluation has high problem and repo fit.",
                "warrant": "The selected approach should satisfy the goal while fitting existing modules.",
                "backing": "The prior-art map favors named content and repo inspection.",
                "rebuttal": "It costs more than the minimal local patch.",
            },
            {
                "role": "objection",
                "about_candidate_id": "minimal",
                "claim": "Minimal leaves less room for repo-native battle ownership.",
                "grounds": "Its problem fit is only medium.",
                "warrant": "A first proof moment loop should establish the module path.",
                "backing": "The constraint prefers repo-native gameplay modules.",
                "rebuttal": "Minimal would be cheaper.",
            },
        ],
        "rationale": {
            "because": ["It covers the named Slime and Spark loop."],
            "under_constraints": ["It keeps receive isolated and reuses codex_exec."],
            "accepting_tradeoffs": ["It adds more implementation detail than the minimal patch."],
        },
        "stack_axes": _stack_axes(),
        "rejected": [{"candidate_id": "minimal", "objection": "Lower problem fit."}],
        "open_questions": [],
    }


def _stack_axes() -> dict[str, object]:
    return {
        "fidelity_precedent": {
            "evidence": "The background facts mention turn-based RPG precedent.",
            "judgment": "Use precedent as evidence but keep the repo-native path feasible.",
        },
    }


def _implementation() -> dict[str, object]:
    return {
        "systems": [
            {
                "system_name": "Battle loop",
                "observable_behavior": "Player casts Spark, Slime takes damage, and the meadow gate opens on victory.",
                "observable_effect": "Spark damage appears in the battle log, Slime defeat is visible, and the meadow gate visibly opens.",
                "named_content": [
                    {"name": "Player", "kind": "actor"},
                    {"name": "Slime", "kind": "enemy"},
                    {"name": "Spark", "kind": "spell"},
                    {"name": "Meadow gate", "kind": "gate"},
                ],
                "key_modules": ["game.battle", "game.state"],
            }
        ],
        "persistence": {"saved_fields": ["player_spells", "slime_defeated", "meadow_gate_open"]},
    }


def _domain_specification() -> dict[str, object]:
    return {
        "aspects": [
            {
                "id": "domain_specification:battle-numbers",
                "aspect_name": "battle numbers",
                "applicability": "applies",
                "specification_body": "Spark must defeat the named Slime in the first proof moment battle.",
                "quantities": [{"name": "Spark damage", "value": "4", "unit": "hit points"}],
                "tables": [],
                "sources": ["Reference battle loop facet"],
            }
        ]
    }


def _patch_plan() -> dict[str, object]:
    return {
        "first_proof_moment": {
            "user_can": ["Enter Green Meadow.", "Cast Spark at Slime."],
            "named_content": [
                {"name": "Green Meadow", "kind": "location"},
                {"name": "Slime", "kind": "enemy"},
                {"name": "Spark", "kind": "spell"},
            ],
            "presentation_baseline": "The first proof moment shows HP, MP, Spark feedback, Slime defeat, and persistent visible gate state from user_experience_requirements.",
            "win_or_progress_condition": "Slime defeated and meadow gate opens.",
            "how_verified": "Run the battle-loop test and assert meadow_gate_open.",
        },
        "follow_ups": [
            {
                "adds": "Add a second meadow enemy.",
                "named_content": [
                    {"name": "Green Meadow", "kind": "location"},
                    {"name": "Bat", "kind": "enemy"},
                    {"name": "Spark", "kind": "spell"},
                ],
            }
        ],
        "deferred": [{"item": "Full campaign map.", "why_safe_to_defer": "The first battle loop is independent."}],
        "open_questions": [],
    }


def _producer_aware_root() -> dict[str, object]:
    deliverable_goal = {
        "id": "goal:preview",
        "requires_deliverable": True,
        "actor": "technical approach maintainer",
        "capability": {
            "action": "Inspect the canonical producer-aware root preview.",
            "preconditions": ["A supplied candidate has explicit producer pairs."],
        },
        "verifiable_outcome": {
            "expected_state": "The canonical CUE body is available in memory.",
            "evidence": "The body digest and paired identities are visible.",
        },
        "verification": {
            "method": "automated_test",
            "check": "Round-trip the root body through the pinned codec.",
        },
        "ux_trace": "none",
    }
    referee_only_goal = {
        "id": "goal:diagnostics",
        "requires_deliverable": False,
        "actor": "independent referee",
        "capability": {
            "action": "Reject an invalid producer pair with field coordinates.",
            "preconditions": ["A candidate violates pair closure."],
        },
        "verifiable_outcome": {
            "expected_state": "The candidate is rejected before publication.",
            "evidence": "The diagnostic names a goal, field, and rule.",
        },
        "verification": {
            "method": "automated_test",
            "check": "Assert the structured diagnostic and unchanged Git state.",
        },
        "ux_trace": "none",
    }
    return {
        "problem": {
            "id": "problem",
            "problem": "The durable root has no producer-aware proof preview.",
            "affected": "Technical approach maintainers and independent referees.",
            "current_inadequacy": "Producer obligations cannot yet be inspected canonically.",
            "goals": [deliverable_goal, referee_only_goal],
            "non_goals": ["Do not activate the durable v2 cutover."],
            "constraints": {},
            "prior_art": [],
            "question": {},
            "open_questions": [],
            "deliverable_requirements": [
                {
                    "id": "requirement:preview",
                    "referee_goal_id": "goal:preview",
                    "production_obligation_id": "obligation:preview",
                    "deliverable": "  technical\tapproach plan.cue  ",
                }
            ],
            "production_obligations": [
                {
                    "id": "obligation:preview",
                    "referee_goal_id": "goal:preview",
                    "deliverable_requirement_id": "requirement:preview",
                    "deliverable": "technical approach\nplan.cue",
                    "eligibility_predicate": "Can produce and test the canonical CUE body.",
                    "replacement_links": [],
                }
            ],
            "patch_plan": [
                {
                    "item_id": "patch:preview",
                    "production_obligation_ids": ["obligation:preview"],
                },
                {
                    "item_id": "patch:diagnostics",
                    "production_obligation_ids": [],
                },
            ],
        },
        "cross_links": [],
    }


def _producer_preview_carrier() -> dict[str, object]:
    problem = _producer_aware_root()["problem"]
    goals = []
    for goal in problem["goals"]:
        goals.append(
            {
                "id": goal["id"],
                "requires_deliverable": goal["requires_deliverable"],
                "actor": goal["actor"],
                "capability_action": goal["capability"]["action"],
                "capability_preconditions": goal["capability"]["preconditions"],
                "outcome_expected_state": goal["verifiable_outcome"]["expected_state"],
                "outcome_evidence": goal["verifiable_outcome"]["evidence"],
                "verification_method": goal["verification"]["method"],
                "verification_check": goal["verification"]["check"],
                "ux_trace": goal["ux_trace"],
            }
        )
    obligations = []
    for obligation in problem["production_obligations"]:
        links = obligation["replacement_links"]
        obligations.append(
            {
                "id": obligation["id"],
                "referee_goal_id": obligation["referee_goal_id"],
                "deliverable_requirement_id": obligation[
                    "deliverable_requirement_id"
                ],
                "deliverable": obligation["deliverable"],
                "eligibility_predicate": obligation["eligibility_predicate"],
                "replaces_obligation_id": (
                    links[0]["replaces_obligation_id"] if links else ""
                ),
                "replacement_reason": links[0]["replacement_reason"] if links else "",
            }
        )
    return {
        "goals": goals,
        "deliverable_requirements": problem["deliverable_requirements"],
        "production_obligations": obligations,
        "patch_plan": problem["patch_plan"],
    }


def _ux_requirements() -> dict[str, object]:
    return {
        "applicability": {"applicability": "user_facing", "not_user_facing_reason": ""},
        "experience_identity": {
            "named_reference": "Playable RPG battle slice with readable status surfaces.",
            "genre_conventions": "JRPG status, command, battle-log, and map-readability conventions.",
            "must_resemble": "Visible HP, MP, enemy, spell, gate, and objective state.",
            "must_not_resemble": "Mechanics that only change hidden variables.",
        },
        "presentation_model": {
            "camera_and_view": "Readable map and battle view.",
            "world_readability": "The meadow, Slime, Spark effect, and gate state are visible.",
            "ui_taxonomy_notes": "Non-diegetic HUD and battle log with spatial gate evidence.",
        },
        "core_status_surfaces": {
            "player_status": "Player HP, MP, and learned spells are visible.",
            "opposition_status": "Slime damage and defeat state are visible.",
            "inventory_resources": "Spell resources remain visible or inspectable.",
            "objective_progress": "Gate objective progress is visible.",
            "location_identity": "Green Meadow identity is visible.",
        },
        "entity_affordances": {
            "interactive_entities": "Player actions show valid targets.",
            "exits_and_transitions": "The meadow gate is a visible transition.",
            "gates_and_locks": "Gate locked and open states have persistent visible evidence.",
            "hazards_and_bosses": "Enemy threat is visible before action.",
            "collectibles": "Rewards visibly change after victory.",
            "decorative_elements": "Decorations never resemble gate or enemy state.",
        },
        "action_feedback_matrix": [
            {"action_verb": "cast Spark", "feedback_requirement": "Battle log and damage feedback appear."},
            {"action_verb": "open gate", "feedback_requirement": "Gate visibly changes and remains open."},
        ],
        "progression_legibility": {
            "current_goal_visibility": "Defeat Slime and open the gate is visible.",
            "locked_state_feedback": "A closed gate communicates blocked progress.",
            "unlocked_state_feedback": "An open gate communicates progress.",
            "flag_observability": "meadow_gate_open has persistent visible evidence.",
            "ending_state_consistency": "Victory state matches Slime defeated and gate open.",
        },
        "hud_and_ui_flow": {
            "primary_hud": "HP and MP are visible during battle.",
            "secondary_screens": "Status and spell details are inspectable.",
            "menu_flow": "Command choice supports casting Spark.",
            "dialog_flow": "Battle text is player-readable.",
            "failure_and_recovery": "Failure explains what happened and allows retry.",
        },
        "visual_language_constraints": {
            "contrast": "HUD, battle log, enemy, and gate state are readable.",
            "palette_role": "Color reinforces but does not solely encode state.",
            "silhouette_readability": "Slime and gate silhouettes are distinct.",
            "labels_and_markers": "Objective, enemy, and gate markers are readable.",
            "animation_minimums": "Spark, damage, defeat, and gate opening have visible feedback.",
        },
        "accessibility_baseline": {
            "controls": "Battle commands use simple inputs.",
            "text_readability": "HUD and log text are readable.",
            "color_independence": "Damage and gate state do not rely on color alone.",
            "audio_independence": "Audio feedback has visual text equivalents.",
            "pacing": "Battle log and dialog are player-paced.",
        },
        "acceptance_tests": {
            "screenshot_checks": ["When battle starts, visible HP, MP, Slime, and command surfaces appear."],
            "interaction_checks": ["When Spark is cast, visible damage feedback and battle log entries appear."],
            "playtest_checks": ["When Slime is defeated, persistent visible gate-open evidence appears."],
        },
    }


def _risks() -> dict[str, object]:
    return {
        "risks": [
            {
                "id": "risk:candidate",
                "risk": "The repo-native path may touch more files.",
                "mitigation": "Keep the first proof moment patch limited to battle and state modules.",
                "attaches_to": "candidate",
                "target_id": "repo_native",
            },
            {
                "id": "risk:implementation",
                "risk": "Persisted battle state could drift from runtime state.",
                "mitigation": "Save and reload player_spells, slime_defeated, and meadow_gate_open in tests.",
                "attaches_to": "implementation",
                "target_id": "implementation:repo_native",
            },
        ]
    }


def _accumulated_approach_through_patch_plan() -> dict[str, object]:
    problem = receive_module._problem_root_from_normalized(_normalized_problem())
    problem["constraints"] = receive_module._constraint_tree_nodes(_constraints())
    problem["prior_art"] = receive_module._prior_art_tree_nodes(_prior_art_map())
    problem["question"] = receive_module._partial_question_tree(
        candidates=_candidates(),
        evaluations=_evaluations(),
        selected=_decision(),
        implementation=_implementation(),
        domain_specification=_domain_specification(),
        patch_plan=_patch_plan(),
    )
    return {"problem": problem}
