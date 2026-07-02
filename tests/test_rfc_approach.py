from __future__ import annotations

import concurrent.futures
import json
from pathlib import Path
import subprocess
import threading
import time

import pytest

from ai_org.rfc import receive as receive_module


ORIGINAL_START_BACKGROUND_BUILD = receive_module.reference.start_background_build


@pytest.fixture(autouse=True)
def default_no_background_reference_build(monkeypatch):
    future: concurrent.futures.Future[dict[str, object]] = concurrent.futures.Future()
    future.set_result({"terms": {}, "processed_terms": [], "expanded": [], "hits": [], "failed": {}})
    monkeypatch.setattr(receive_module.reference, "start_background_build", lambda *args, **kwargs: future)


def test_form_technical_approach_builds_derivation_tree(monkeypatch, tmp_path):
    calls = []
    approaches = {}

    monkeypatch.setattr(
        receive_module,
        "_normalize_problem",
        lambda rfc_view, context=None: calls.append("normalize_problem") or _normalized_problem(),
    )
    def fake_build_from_rfc(rfc_view, context=None, **kwargs):
        assert kwargs == {"kinds": ("design",)}
        calls.append("build_from_rfc")
        return {"terms": {}, "processed_terms": ["battle loop"], "expanded": [], "hits": [], "failed": {}}

    monkeypatch.setattr(receive_module.reference, "build_from_rfc", fake_build_from_rfc)

    def fake_constraints(rfc_view, repo, context=None, approach=None):
        calls.append(("extract_constraints", approach))
        approaches["extract_constraints"] = approach
        assert "problem" in approach
        assert "normalized_problem" not in approach
        return _constraints()

    def fake_prior_art(rfc_view, repo, context=None, approach=None, reference_terms=None):
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
        rfc_view,
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
    monkeypatch.setattr(receive_module, "_right_size_patch_plan", fake_right_size_patch_plan)
    monkeypatch.setattr(receive_module, "_surface_risks", fake_surface_risks)

    result = receive_module.form_technical_approach(_rfc_view(), tmp_path)

    assert result["ok"] is True
    assert "approach" not in result
    assert calls[0] == "normalize_problem"
    assert calls[2] == "build_from_rfc"
    assert calls[3][0] == "build_prior_art_map"
    assert calls[-1] == "surface_risks"
    for step in (
        "extract_constraints",
        "build_prior_art_map",
        "generate_candidates",
        "evaluate_candidates",
        "select_approach",
        "implementation_strategy",
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
    assert approaches["right_size_patch_plan"]["problem"]["question"]["decision"]["implementation"]["systems"][0][
        "system_name"
    ] == "Battle loop"
    assert approaches["surface_risks"]["problem"]["question"]["decision"]["implementation"]["patch_plan"][
        "first_playable"
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
    assert {"from": "risk:implementation", "to": "implementation:repo_native", "type": "mitigates"} in tree[
        "cross_links"
    ]


def test_tech_stack_provenance_controls_candidate_generation(monkeypatch, tmp_path):
    rfc = _rfc_view()
    rfc["tech_stack"] = {
        **rfc["tech_stack"],
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
    monkeypatch.setattr(receive_module.reference, "build_from_rfc", lambda *args, **kwargs: {"processed_terms": []})
    monkeypatch.setattr(receive_module, "_build_prior_art_map", lambda *args, **kwargs: _prior_art_map())
    monkeypatch.setattr(
        receive_module,
        "_generate_candidates",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("requester stack should skip candidates")),
    )
    monkeypatch.setattr(receive_module, "_implementation_strategy", lambda *args, **kwargs: _implementation())
    monkeypatch.setattr(receive_module, "_right_size_patch_plan", lambda *args, **kwargs: _patch_plan())
    monkeypatch.setattr(receive_module, "_surface_risks", lambda *args, **kwargs: {"risks": []})

    def fake_select(candidates, evaluations, constraints, context=None, accumulated_approach=None):
        calls.append((candidates, evaluations))
        return _decision()

    monkeypatch.setattr(receive_module, "_select_approach", fake_select)

    result = receive_module.form_technical_approach(rfc, tmp_path)

    assert result["ok"] is True
    assert "provided_approach" in result["steps"]
    assert calls == []


def test_unspecified_tech_stack_is_ai_deliberated_after_generated_selection(monkeypatch, tmp_path):
    rfc = _rfc_view()
    _patch_successful_approach_steps(monkeypatch)

    result = receive_module.form_technical_approach(rfc, tmp_path, skip_reference_build=True, reference_terms=[])

    assert result["ok"] is True
    decision = result["technical_approach"]["problem"]["question"]["decision"]
    assert set(decision["stack_axes"]) == set(receive_module.STACK_DECISION_AXIS_FIELDS)
    for axis in receive_module.STACK_DECISION_AXIS_FIELDS:
        assert decision["stack_axes"][axis]["evidence"]
        assert decision["stack_axes"][axis]["judgment"]
    assert rfc["tech_stack"]["build_strategy"] == "framework_based"
    assert rfc["tech_stack"]["framework"] == "Repo Native"
    assert rfc["tech_stack"]["engine"] == ""
    assert rfc["tech_stack"]["platform"] == "browser"
    assert rfc["tech_stack"]["provenance"] == "ai_deliberated"
    assert rfc["tech_stack"]["rationale"]


def test_extract_constraints_merges_org_builder_profile_as_hard_constraints(monkeypatch, tmp_path):
    def fake_run_json(repo: Path, **kwargs):
        prompt = kwargs["prompt"]
        assert "ORG_BUILDER_PROFILE" in prompt
        assert "org_builder_profile" in prompt
        return {"ok": True, "raw": json.dumps(_constraints())}

    monkeypatch.setattr(receive_module.codex_exec, "run_json", fake_run_json)

    result = receive_module._extract_constraints(
        _rfc_view(),
        tmp_path,
        {"repo": tmp_path},
        {"normalized_problem": _normalized_problem()},
    )

    org_constraints = [
        item for item in result["hard_constraints"] if item["derivation"]["from"] == "org_builder_profile"
    ]
    assert len(org_constraints) == len(receive_module.ORG_BUILDER_PROFILE)
    assert org_constraints[0]["implication"]["must"]
    assert org_constraints[0]["implication"]["must_not"]


def test_stack_decision_empty_axis_retries(monkeypatch, tmp_path):
    invalid_axes = _stack_axes()
    invalid_axes["builder_buildability"] = {"evidence": "", "judgment": ""}
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
    assert "stack_axes.builder_buildability.evidence is empty" in prompts[1]


def test_requester_specified_stack_skips_alternatives_and_records_org_conflict_risk(monkeypatch, tmp_path):
    rfc = _rfc_view()
    rfc["raw_request"] = "Make the battle slice in Unreal Engine 5."
    rfc["proposal_hint"] = "Use Unreal Engine 5."
    rfc["tech_stack"] = {
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
    monkeypatch.setattr(receive_module.reference, "build_from_rfc", lambda *args, **kwargs: {"processed_terms": []})
    monkeypatch.setattr(receive_module, "_build_prior_art_map", lambda *args, **kwargs: _prior_art_map())
    monkeypatch.setattr(
        receive_module,
        "_generate_candidates",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("requester stack should skip alternatives")),
    )
    monkeypatch.setattr(receive_module, "_implementation_strategy", lambda *args, **kwargs: _implementation())
    monkeypatch.setattr(receive_module, "_right_size_patch_plan", lambda *args, **kwargs: _patch_plan())
    monkeypatch.setattr(receive_module, "_surface_risks", lambda *args, **kwargs: {"risks": []})

    result = receive_module.form_technical_approach(rfc, tmp_path)

    assert result["ok"] is True
    assert "generate_candidates" not in result["steps"]
    decision = result["technical_approach"]["problem"]["question"]["decision"]
    assert decision["selected_candidate_id"] == "provided_approach"
    assert [candidate["id"] for candidate in result["technical_approach"]["problem"]["question"]["candidates"]] == [
        "provided_approach"
    ]
    assert decision["risks"][0]["id"] == "risk:requester_stack_org_profile_conflict"
    assert "Requester sovereignty makes this non-blocking" in decision["risks"][0]["risk"]


def test_org_profile_conflicting_candidate_is_pruned_without_retry(monkeypatch, tmp_path):
    outputs = [
        _candidate("feasible_a", "minimal_local", "Feasible A"),
        _candidate("feasible_b", "repo_native", "Feasible B"),
        _unity_candidate("general_architectural"),
    ]
    prompts = []

    def fake_run_json(repo: Path, **kwargs):
        prompts.append(kwargs["prompt"])
        return {"ok": True, "raw": json.dumps(outputs.pop(0))}

    monkeypatch.setattr(receive_module.codex_exec, "run_json", fake_run_json)

    result = receive_module._generate_candidates(
        _normalized_problem(),
        _constraints_with_org_builder_profile(),
        _prior_art_map(),
        {"repo": tmp_path},
    )

    assert [candidate["id"] for candidate in result["candidates"]] == ["feasible_a", "feasible_b"]
    assert len(prompts) == 3
    assert result["pruned_candidates"][0]["candidate_id"] == "unity"
    assert "org-profile conflict" in result["pruned_candidates"][0]["objection"]
    assert "authoring_model=gui_editor" in result["pruned_candidates"][0]["objection"]
    assert "verification_model=gui_required" in result["pruned_candidates"][0]["objection"]
    assert "headless functional_check verification" in result["pruned_candidates"][0]["objection"]


def test_org_profile_allows_text_first_candidate_that_mentions_rival_engines(monkeypatch, tmp_path):
    phaser = _candidate("browser_phaser_campaign", "minimal_local", "Phaser Campaign")
    phaser["summary"] = (
        "Phaser builds a browser campaign unlike Unity or Unreal while keeping all scenes text-authored."
    )
    phaser["core_systems"] = ["Phaser scene modules", "browser campaign runtime"]
    phaser["stack_requirement"] = {
        "build_strategy": "framework_based",
        "engine": "",
        "framework": "Phaser",
        "language": "TypeScript",
        "platform": "browser",
        "authoring_model": "text_first",
        "verification_model": "headless_ci",
    }
    outputs = [
        phaser,
        _candidate("feasible_b", "repo_native", "Feasible B"),
        _candidate("feasible_c", "general_architectural", "Feasible C"),
    ]

    def fake_run_json(repo: Path, **kwargs):
        return {"ok": True, "raw": json.dumps(outputs.pop(0))}

    monkeypatch.setattr(receive_module.codex_exec, "run_json", fake_run_json)

    result = receive_module._generate_candidates(
        _normalized_problem(),
        _constraints_with_org_builder_profile(),
        _prior_art_map(),
        {"repo": tmp_path},
    )

    assert [candidate["id"] for candidate in result["candidates"]] == [
        "browser_phaser_campaign",
        "feasible_b",
        "feasible_c",
    ]
    assert "pruned_candidates" not in result


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
        _constraints_with_org_builder_profile(),
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
        _constraints_with_org_builder_profile(),
        _prior_art_map(),
        {"repo": tmp_path},
    )

    assert result["candidates"][0]["stack_requirement"]["build_strategy"] == "from_scratch"
    assert result["candidates"][0]["stack_requirement"]["engine"] == ""
    assert "hand-rolled browser standards must use from_scratch" in prompts[1]


def test_org_profile_pruning_retries_only_when_feasible_set_too_small(monkeypatch, tmp_path):
    outputs = [
        _unity_candidate("minimal_local"),
        _unreal_candidate("repo_native"),
        _candidate("feasible_a", "general_architectural", "Feasible A"),
        _candidate("feasible_b", "repo_native", "Feasible B"),
    ]
    prompts = []

    def fake_run_json(repo: Path, **kwargs):
        prompts.append(kwargs["prompt"])
        return {"ok": True, "raw": json.dumps(outputs.pop(0))}

    monkeypatch.setattr(receive_module.codex_exec, "run_json", fake_run_json)

    result = receive_module._generate_candidates(
        _normalized_problem(),
        _constraints_with_org_builder_profile(),
        _prior_art_map(),
        {"repo": tmp_path},
    )

    assert [candidate["id"] for candidate in result["candidates"]] == ["feasible_a", "feasible_b"]
    assert [item["candidate_id"] for item in result["pruned_candidates"]] == ["unity", "unreal"]
    assert "authoring_model=binary_assets" in result["pruned_candidates"][1]["objection"]
    assert len(prompts) == 4
    assert "Fewer than two feasible candidates remain after deterministic pruning" in prompts[3]
    assert "Pruned unity: org-profile conflict" in prompts[3]
    assert "author as text in the worktree and verify headlessly" in prompts[3]


def test_org_profile_all_conflicts_fail_only_after_candidate_retries(monkeypatch, tmp_path):
    outputs = [
        _unity_candidate("minimal_local", candidate_id="unity_minimal"),
        _unreal_candidate("repo_native", candidate_id="unreal_repo"),
        _unity_candidate("general_architectural", candidate_id="unity_general"),
        _unreal_candidate("minimal_local", candidate_id="unreal_retry_1"),
        _unity_candidate("repo_native", candidate_id="unity_retry_2"),
        _unreal_candidate("general_architectural", candidate_id="unreal_retry_3"),
    ]
    prompts = []

    def fake_run_json(repo: Path, **kwargs):
        prompts.append(kwargs["prompt"])
        return {"ok": True, "raw": json.dumps(outputs.pop(0))}

    monkeypatch.setattr(receive_module.codex_exec, "run_json", fake_run_json)

    result = receive_module._generate_candidates(
        _normalized_problem(),
        _constraints_with_org_builder_profile(),
        _prior_art_map(),
        {"repo": tmp_path},
    )

    assert result["ok"] is False
    assert "no feasible candidates remained after pruning ORG_BUILDER_PROFILE conflicts" in result["error"]
    assert len(prompts) == 6


def test_org_profile_single_feasible_after_retries_proceeds_degraded(monkeypatch, tmp_path):
    outputs = [
        _unity_candidate("minimal_local", candidate_id="unity_minimal"),
        _unreal_candidate("repo_native", candidate_id="unreal_repo"),
        _candidate("feasible_a", "general_architectural", "Feasible A"),
        _unreal_candidate("minimal_local", candidate_id="unreal_retry_1"),
        _unity_candidate("repo_native", candidate_id="unity_retry_2"),
        _unreal_candidate("general_architectural", candidate_id="unreal_retry_3"),
    ]

    def fake_run_json(repo: Path, **kwargs):
        return {"ok": True, "raw": json.dumps(outputs.pop(0))}

    monkeypatch.setattr(receive_module.codex_exec, "run_json", fake_run_json)

    result = receive_module._generate_candidates(
        _normalized_problem(),
        _constraints_with_org_builder_profile(),
        _prior_art_map(),
        {"repo": tmp_path},
    )

    assert [candidate["id"] for candidate in result["candidates"]] == ["feasible_a"]
    assert len(result["pruned_candidates"]) == 5
    assert "degradation_notes" in result
    assert "one feasible candidate" in result["degradation_notes"][0]


def test_pruned_candidates_land_in_final_decision_rejected_list(monkeypatch, tmp_path):
    candidates = {
        "candidates": [
            _candidate("feasible_a", "minimal_local", "Feasible A"),
            _candidate("feasible_b", "repo_native", "Feasible B"),
        ],
        "pruned_candidates": [
            {
                "candidate_id": "unity",
                "objection": (
                    "org-profile conflict: stack_requirement requires authoring_model=gui_editor; ORG_BUILDER_PROFILE requires Codex "
                    "text-first worktree authoring and headless functional_check verification."
                ),
            }
        ],
        "degradation_notes": ["Candidate generation proceeded with one feasible candidate after bounded retries."],
    }
    decision = {
        **_decision(),
        "selected_candidate_id": "feasible_b",
        "arguments": [
            {
                **_decision()["arguments"][0],
                "about_candidate_id": "feasible_b",
            },
            {
                **_decision()["arguments"][1],
                "about_candidate_id": "feasible_a",
            },
        ],
        "rejected": [{"candidate_id": "feasible_a", "objection": "Lower problem fit."}],
    }

    def fake_run_json(repo: Path, **kwargs):
        return {"ok": True, "raw": json.dumps(decision)}

    monkeypatch.setattr(receive_module.codex_exec, "run_json", fake_run_json)

    selected = receive_module._select_approach(
        candidates,
        {
            "evaluations": [
                {"candidate_id": "feasible_a", "scores": _scores()},
                {"candidate_id": "feasible_b", "scores": _scores()},
            ]
        },
        _constraints_with_org_builder_profile(),
        {"repo": tmp_path},
    )

    rejected = selected["rejected"]
    assert {"candidate_id": "feasible_a", "objection": "Lower problem fit."} in rejected
    assert rejected[-1]["candidate_id"] == "unity"
    assert "org-profile conflict" in rejected[-1]["objection"]
    assert "one feasible candidate" in selected["rationale"]["accepting_tradeoffs"][-1]


def test_form_technical_approach_writes_incremental_progress_snapshots(monkeypatch, tmp_path):
    _patch_successful_approach_steps(monkeypatch)
    ticks = iter(float(value) for value in range(100))
    monkeypatch.setattr(receive_module.time, "monotonic", lambda: next(ticks))

    progress_path = tmp_path / "progress" / "technical-approach.json"
    original_writer = receive_module._write_technical_approach_progress
    snapshots = []

    def recording_writer(path, partial_tree, steps_completed, current_step):
        original_writer(path, partial_tree, steps_completed, current_step)
        snapshots.append(json.loads(Path(path).read_text(encoding="utf-8")))

    monkeypatch.setattr(receive_module, "_write_technical_approach_progress", recording_writer)

    result = receive_module.form_technical_approach(_rfc_view(), tmp_path, progress_path=progress_path)

    expected_steps = [
        "normalize_problem",
        "extract_constraints",
        "build_prior_art_map",
        "generate_candidates",
        "evaluate_candidates",
        "select_approach",
        "implementation_strategy",
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
    assert snapshots[7]["technical_approach"]["problem"]["question"]["decision"]["implementation"]["patch_plan"][
        "id"
    ] == "patch_plan:repo_native"
    final_question = snapshots[8]["technical_approach"]["problem"]["question"]
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

    result = receive_module.form_technical_approach(_rfc_view(), tmp_path, progress_path=None)

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
                    "behavior_in_game": "",
                    "named_content": {"entities": ["Slime"], "content_items": ["Spark spell"]},
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
        _rfc_view(),
        tmp_path,
        {"repo": tmp_path},
    )

    assert result == _implementation()
    assert len(prompts) == 2
    assert "behavior_in_game is empty" in prompts[1]


def test_step_prompts_render_accumulated_goals_and_direct_ancestors(tmp_path):
    accumulated = _accumulated_approach_through_patch_plan()

    prompts = {
        "extract_constraints": receive_module._extract_constraints_prompt(
            _rfc_view(),
            tmp_path,
            {"repo": tmp_path},
            accumulated,
        ),
        "build_prior_art_map": receive_module._prior_art_map_prompt(
            _rfc_view(),
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
            _rfc_view(),
            tmp_path,
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
    assert "Battle loop" in prompts["right_size_patch_plan"]
    assert "Patch plan" in prompts["surface_risks"]
    assert "Run the battle-loop test and assert meadow_gate_open." in prompts["surface_risks"]


def test_empty_slot_fails_closed_after_bounded_regeneration(monkeypatch, tmp_path):
    invalid = {
        "systems": [
            {
                "system_name": "Battle loop",
                "behavior_in_game": "",
                "named_content": {"entities": ["Slime"], "content_items": ["Spark spell"]},
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
        _rfc_view(),
        tmp_path,
        {"repo": tmp_path},
    )

    assert result["ok"] is False
    assert "remained invalid" in result["error"]
    assert "behavior_in_game is empty" in result["error"]


def test_risk_targets_fail_closed_when_parent_is_unknown(monkeypatch, tmp_path):
    monkeypatch.setattr(receive_module, "_normalize_problem", lambda rfc_view, context=None: _normalized_problem())
    monkeypatch.setattr(receive_module, "_extract_constraints", lambda *args, **kwargs: _constraints())
    monkeypatch.setattr(
        receive_module.reference,
        "build_from_rfc",
        lambda *args, **kwargs: {"terms": {}, "processed_terms": ["battle loop"], "expanded": [], "hits": [], "failed": {}},
    )
    monkeypatch.setattr(receive_module, "_build_prior_art_map", lambda *args, **kwargs: _prior_art_map())
    monkeypatch.setattr(receive_module, "_generate_candidates", lambda *args, **kwargs: _candidates())
    monkeypatch.setattr(receive_module, "_evaluate_candidates", lambda *args, **kwargs: _evaluations())
    monkeypatch.setattr(receive_module, "_select_approach", lambda *args, **kwargs: _decision())
    monkeypatch.setattr(receive_module, "_implementation_strategy", lambda *args, **kwargs: _implementation())
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

    result = receive_module.form_technical_approach(_rfc_view(), tmp_path)

    assert result["ok"] is False
    assert result["failed_step"] == "surface_risks"
    assert "targets unknown implementation node" in result["error"]


def test_form_technical_approach_builds_reference_then_prior_art_uses_same_terms(monkeypatch, tmp_path):
    calls = []

    monkeypatch.setattr(receive_module, "_normalize_problem", lambda *args, **kwargs: _normalized_problem())
    monkeypatch.setattr(receive_module, "_extract_constraints", lambda *args, **kwargs: _constraints())
    monkeypatch.setattr(receive_module, "_generate_candidates", lambda *args, **kwargs: _candidates())
    monkeypatch.setattr(receive_module, "_evaluate_candidates", lambda *args, **kwargs: _evaluations())
    monkeypatch.setattr(receive_module, "_select_approach", lambda *args, **kwargs: _decision())
    monkeypatch.setattr(receive_module, "_implementation_strategy", lambda *args, **kwargs: _implementation())
    monkeypatch.setattr(receive_module, "_right_size_patch_plan", lambda *args, **kwargs: _patch_plan())
    monkeypatch.setattr(receive_module, "_surface_risks", lambda *args, **kwargs: _risks())
    monkeypatch.setattr(
        receive_module,
        "_prior_art_key_concepts",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("prior art must not re-extract terms")),
    )

    def fake_build_from_rfc(rfc_view, context=None, **kwargs):
        assert kwargs == {"kinds": ("design",)}
        calls.append("build_from_rfc")
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
            return {"term": term, "candidates": [_reference_candidate_for_term(term, "design", f"{term} design summary")]}
        if kind == "implementation":
            return {
                "term": term,
                "candidates": [_reference_candidate_for_term(term, "implementation", f"{term} implementation summary")],
            }
        return {"term": term, "candidates": []}

    def fake_run_json(repo: Path, **kwargs):
        calls.append("build_prior_art_map")
        prompt = kwargs["prompt"]
        assert prompt.index('"term": "C1"') < prompt.index('"term": "C2"')
        assert "C1 design structure" in prompt
        assert "C2 implementation summary" in prompt
        return {"ok": True, "raw": json.dumps(_prior_art_map_for_terms(("C1", "design"), ("C2", "implementation")))}

    monkeypatch.setattr(receive_module.reference, "build_from_rfc", fake_build_from_rfc)
    monkeypatch.setattr(receive_module.reference, "lookup", fake_lookup)
    monkeypatch.setattr(receive_module.codex_exec, "run_json", fake_run_json)

    result = receive_module.form_technical_approach(_rfc_view(), tmp_path)

    assert result["ok"] is True
    assert calls == ["build_from_rfc", "build_prior_art_map"]
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

    def fake_build_from_rfc(rfc_view, context=None, **kwargs):
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

    def fake_prior_art(rfc_view, repo, context=None, approach=None, reference_terms=None):
        used_terms.extend(reference_terms)
        return _prior_art_map_with_facet("design concept", "design")

    monkeypatch.setattr(receive_module.reference, "build_from_rfc", fake_build_from_rfc)
    monkeypatch.setattr(receive_module, "_build_prior_art_map", fake_prior_art)

    result_holder = {}

    def run_form():
        result_holder["result"] = receive_module.form_technical_approach(_rfc_view(), tmp_path)
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


def test_form_technical_approach_starts_implementation_build_without_awaiting(monkeypatch, tmp_path):
    implementation_started = threading.Event()
    release_implementation = threading.Event()
    implementation_done = threading.Event()
    calls = []

    receive_module.reference.await_background_builds(timeout=5)
    _patch_successful_approach_steps(monkeypatch)
    monkeypatch.setattr(receive_module.reference, "start_background_build", ORIGINAL_START_BACKGROUND_BUILD)

    def fake_build_from_rfc(rfc_view, context=None, force=False, kinds=None):
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

    def fake_prior_art(rfc_view, repo, context=None, approach=None, reference_terms=None):
        assert reference_terms == ["design concept"]
        return _prior_art_map_with_facet("design concept", "design")

    monkeypatch.setattr(receive_module.reference, "build_from_rfc", fake_build_from_rfc)
    monkeypatch.setattr(receive_module, "_build_prior_art_map", fake_prior_art)

    result = receive_module.form_technical_approach(_rfc_view(), tmp_path)

    assert result["ok"] is True
    assert implementation_started.wait(1)
    assert not implementation_done.is_set()
    assert calls[0] == ("design",)
    assert ("implementation",) in calls
    release_implementation.set()
    receive_module.reference.await_background_builds(timeout=5)
    assert implementation_done.is_set()


def test_form_technical_approach_reference_terms_skip_internal_build(monkeypatch, tmp_path):
    used_terms = []

    _patch_successful_approach_steps(monkeypatch)
    monkeypatch.setattr(
        receive_module.reference,
        "build_from_rfc",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("build_from_rfc should be skipped")),
    )
    monkeypatch.setattr(
        receive_module.reference,
        "start_background_build",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("background build should be skipped")),
    )

    def fake_prior_art(rfc_view, repo, context=None, approach=None, reference_terms=None):
        used_terms.extend(reference_terms)
        return _prior_art_map()

    monkeypatch.setattr(receive_module, "_build_prior_art_map", fake_prior_art)

    result = receive_module.form_technical_approach(_rfc_view(), tmp_path, reference_terms=["cached concept"])

    assert result["ok"] is True
    assert used_terms == ["cached concept"]


def test_prior_art_reference_term_timeout_does_not_abort_other_concepts(monkeypatch, tmp_path):
    lookup_calls = []

    def fake_lookup(term, reference_context, kind=None):
        lookup_calls.append((term, kind))
        if term == "timed concept":
            raise receive_module.reference.ReferenceCodexTimeout("search timed out")
        if kind == "design":
            return {
                "term": term,
                "candidates": [_reference_candidate_for_term(term, "design", f"{term} design summary")],
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

    monkeypatch.setattr(receive_module.reference, "lookup", fake_lookup)
    monkeypatch.setattr(receive_module.codex_exec, "run_json", fake_run_json)

    result = receive_module._build_prior_art_map(
        _rfc_view(),
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


def test_reference_codex_search_timeout_returns_empty_result(monkeypatch):
    observed_timeouts = []

    def fake_run(*args, **kwargs):
        observed_timeouts.append(kwargs.get("timeout"))
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=kwargs.get("timeout"))

    monkeypatch.setenv("AI_ORG_REFERENCE_SEARCH_TIMEOUT", "1")
    monkeypatch.setattr(receive_module.reference.subprocess, "run", fake_run)

    context: dict[str, object] = {}
    result = receive_module.reference._codex_search_keywords("battle loop", context)

    assert result == []
    assert observed_timeouts == [1.0]
    assert "search-keywords.json timed out after 1 seconds" in context["_reference_codex_timeouts"][0]


def test_prior_art_expands_reference_on_miss_and_uses_researched_facets(monkeypatch, tmp_path):
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
                _reference_candidate("design", "researched design summary"),
                _reference_candidate("implementation", "researched implementation summary"),
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

    monkeypatch.setattr(receive_module.reference, "lookup", fake_lookup)
    monkeypatch.setattr(receive_module.reference, "expand", fake_expand)
    monkeypatch.setattr(receive_module.codex_exec, "run_json", fake_run_json)

    result = receive_module._build_prior_art_map(_rfc_view(), tmp_path, context, {"normalized_problem": _normalized_problem()})

    assert result["patterns"][0]["source"]["facet_kind"] == "design"
    assert expand_calls == [("battle loop", stack_context)]
    assert lookup_contexts
    assert len(prompts) == 2


def test_prior_art_reference_hit_does_not_expand(monkeypatch):
    expand_calls = []
    stack_context = {"language": "Python", "environment": "CLI", "version": "3.12"}

    def fake_lookup(term, reference_context, kind=None):
        if kind == "design":
            return {"term": term, "candidates": [_reference_candidate("design", "stored design summary")]}
        return {"term": term, "candidates": []}

    monkeypatch.setattr(receive_module.reference, "lookup", fake_lookup)
    monkeypatch.setattr(receive_module.reference, "expand", lambda *args, **kwargs: expand_calls.append(args))

    facets = receive_module._read_prior_art_reference_facets(["battle loop"], {**stack_context, "repo": Path(".")})

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
        receive_module.reference,
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
                    _reference_candidate("design", f"{term} design summary"),
                    _reference_candidate("implementation", f"{term} implementation summary"),
                ],
            }
        finally:
            with active_lock:
                active -= 1

    monkeypatch.setattr(receive_module.concurrent.futures, "ThreadPoolExecutor", RecordingExecutor)
    monkeypatch.setattr(receive_module.reference, "expand", fake_expand)

    facets = receive_module._read_prior_art_reference_facets(concepts, {})

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
        receive_module.reference,
        "lookup",
        lambda term, reference_context, kind=None: {"term": term, "candidates": []},
    )

    def fake_expand(term, reference_context):
        if term == "bad pattern":
            raise RuntimeError("research failed")
        return {"term": term, "candidates": [_reference_candidate("design", f"{term} design summary")]}

    monkeypatch.setattr(receive_module.reference, "expand", fake_expand)

    facets = receive_module._read_prior_art_reference_facets(concepts, {})

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
        receive_module.reference,
        "lookup",
        lambda term, reference_context, kind=None: {"term": term, "candidates": []},
    )

    def fake_expand(term, reference_context):
        expand_calls.append(term)
        return {"term": term, "candidates": []}

    monkeypatch.setattr(receive_module.reference, "expand", fake_expand)
    monkeypatch.setattr(
        receive_module.codex_exec,
        "run_json",
        lambda repo, **kwargs: {"ok": True, "raw": json.dumps(_prior_art_map_with_facet("unknown pattern", "none"))},
    )

    result = receive_module._build_prior_art_map(_rfc_view(), tmp_path, {}, {"normalized_problem": _normalized_problem()})

    assert expand_calls == ["unknown pattern"]
    assert result["patterns"][0]["source"]["facet_kind"] == "none"


def test_prior_art_reference_expansion_is_capped(monkeypatch):
    concepts = [f"concept {index}" for index in range(receive_module.MAX_PRIOR_ART_REFERENCE_EXPANSIONS + 2)]
    expand_calls = []

    monkeypatch.setattr(
        receive_module.reference,
        "lookup",
        lambda term, reference_context, kind=None: {"term": term, "candidates": []},
    )

    def fake_expand(term, reference_context):
        expand_calls.append(term)
        return {"term": term, "candidates": []}

    monkeypatch.setattr(receive_module.reference, "expand", fake_expand)

    facets = receive_module._read_prior_art_reference_facets(concepts, {})

    assert set(expand_calls) == set(concepts[: receive_module.MAX_PRIOR_ART_REFERENCE_EXPANSIONS])
    assert len(expand_calls) == receive_module.MAX_PRIOR_ART_REFERENCE_EXPANSIONS
    assert [facet["status"] for facet in facets[-2:]] == ["not_researched_cap", "not_researched_cap"]


def test_technical_approach_schemas_are_codex_valid():
    for schema in (
        receive_module.NORMALIZE_PROBLEM_SCHEMA,
        receive_module.EXTRACT_CONSTRAINTS_SCHEMA,
        receive_module.PRIOR_ART_MAP_SCHEMA,
        receive_module.CANDIDATE_APPROACH_SCHEMA,
        receive_module.GENERATE_CANDIDATES_SCHEMA,
        receive_module.EVALUATE_CANDIDATE_SCHEMA,
        receive_module.EVALUATE_CANDIDATES_SCHEMA,
        receive_module.SELECT_APPROACH_SCHEMA,
        receive_module.IMPLEMENTATION_STRATEGY_SCHEMA,
        receive_module.RIGHT_SIZE_PATCH_PLAN_SCHEMA,
        receive_module.SURFACE_RISKS_SCHEMA,
    ):
        _assert_codex_valid_object_schema(schema)


def test_receive_imports_reference_and_codex_exec_without_later_phases():
    source = Path(receive_module.__file__).read_text(encoding="utf-8")

    assert "import ai_org.reference as reference" in source
    assert "import ai_org.rfc.codex_exec as codex_exec" in source
    assert "ai_org.patch" not in source
    assert "ai_org.merge" not in source
    assert not any("\u3040" <= character <= "\u9fff" for character in source)


def _patch_successful_approach_steps(monkeypatch) -> None:
    monkeypatch.setattr(receive_module, "_normalize_problem", lambda rfc_view, context=None: _normalized_problem())
    monkeypatch.setattr(receive_module, "_extract_constraints", lambda *args, **kwargs: _constraints())
    monkeypatch.setattr(
        receive_module.reference,
        "build_from_rfc",
        lambda *args, **kwargs: {"terms": {}, "processed_terms": ["battle loop"], "expanded": [], "hits": [], "failed": {}},
    )
    monkeypatch.setattr(receive_module, "_build_prior_art_map", lambda *args, **kwargs: _prior_art_map())
    monkeypatch.setattr(receive_module, "_generate_candidates", lambda *args, **kwargs: _candidates())
    monkeypatch.setattr(receive_module, "_evaluate_candidates", lambda *args, **kwargs: _evaluations())
    monkeypatch.setattr(receive_module, "_select_approach", lambda *args, **kwargs: _decision())
    monkeypatch.setattr(receive_module, "_implementation_strategy", lambda *args, **kwargs: _implementation())
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


def _rfc_view() -> dict[str, object]:
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
        "background_facts": "The first playable slice centers on a Slime and Spark spell.",
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
                    "expected_state": "The Slime is defeated and the meadow gate opens.",
                    "evidence": "Battle log records Spark damage and gate state changes to open.",
                },
                "verification": {
                    "method": "automated_test",
                    "check": "Assert Spark defeats Slime and sets meadow_gate_open true.",
                },
            }
        ],
        "non_goals": ["Do not add a full campaign."],
    }


def _constraints() -> dict[str, object]:
    return {
        "hard_constraints": [
            {
                "statement": "Keep RFC receive isolated from patch and merge modules.",
                "derivation": {"from": "repo", "trace": "ai_org.rfc.receive imports only RFC helpers."},
                "implication": {
                    "must": "Reuse ai_org.rfc.codex_exec for Codex-backed approach nodes.",
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


def _constraints_with_org_builder_profile() -> dict[str, object]:
    constraints = _constraints()
    constraints["hard_constraints"].append(
        {
            "statement": receive_module.ORG_BUILDER_PROFILE[0]["statement"],
            "derivation": {
                "from": "org_builder_profile",
                "trace": receive_module.ORG_BUILDER_PROFILE[0]["trace"],
            },
            "implication": {
                "must": receive_module.ORG_BUILDER_PROFILE[0]["must"],
                "must_not": receive_module.ORG_BUILDER_PROFILE[0]["must_not"],
            },
        }
    )
    return constraints


def _prior_art_map() -> dict[str, object]:
    return {
        "patterns": [
            {
                "name": "Reference-first prior-art synthesis",
                "source": {"reference_concept": "battle loop", "facet_kind": "design", "where": "Reference."},
                "when_applies": "When a first playable loop needs named content.",
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
                    "where": "Reference." if facet_kind != "none" else "RFC and repository.",
                },
                "when_applies": "When the RFC needs a grounded implementation direction.",
                "tradeoffs": {"pros": ["Grounded direction."], "cons": ["Requires validation."]},
                "disposition": {"choice": "adopt", "why": "It fits the RFC constraints."},
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
                    "where": "Reference." if facet_kind != "none" else "RFC and repository.",
                },
                "when_applies": "When the RFC needs a grounded implementation direction.",
                "tradeoffs": {"pros": ["Grounded direction."], "cons": ["Requires validation."]},
                "disposition": {"choice": "adopt", "why": "It fits the RFC constraints."},
                "traces_to": ["normalized_problem.success_criteria[0]"],
            }
        )
    return {"patterns": patterns}


def _reference_candidate(kind: str, summary: str) -> dict[str, str]:
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


def _reference_candidate_for_term(term: str, kind: str, summary: str) -> dict[str, str]:
    candidate = _reference_candidate(kind, summary)
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
        "first_playable_moment": {
            "player_actions": ["Cast Spark at Slime."],
            "named_content": {
                "locations": ["Green Meadow"],
                "enemies": ["Slime"],
                "items_or_spells": ["Spark"],
            },
            "win_or_progress_condition": "Slime defeated and meadow gate opens.",
        },
        "core_systems": ["battle resolution"],
        "draws_on": ["Reference-first prior-art synthesis"],
    }


def _unity_candidate(kind: str, candidate_id: str = "unity") -> dict[str, object]:
    candidate = _candidate(candidate_id, kind, "Unity Engine")
    candidate["summary"] = "Unity builds the battle slice with editor-authored scenes and imported assets."
    candidate["core_systems"] = ["Unity scene workflow", "binary asset import"]
    candidate["stack_requirement"] = {
        "build_strategy": "engine_based",
        "engine": "Unity",
        "framework": "",
        "language": "C#",
        "platform": "desktop",
        "authoring_model": "gui_editor",
        "verification_model": "gui_required",
    }
    return candidate


def _unreal_candidate(kind: str, candidate_id: str = "unreal") -> dict[str, object]:
    candidate = _candidate(candidate_id, kind, "Unreal Engine")
    candidate["summary"] = "Unreal builds the battle slice with editor-authored maps and native packaging."
    candidate["core_systems"] = ["Unreal editor workflow", "heavyweight native build"]
    candidate["stack_requirement"] = {
        "build_strategy": "engine_based",
        "engine": "Unreal Engine",
        "framework": "",
        "language": "C++",
        "platform": "desktop",
        "authoring_model": "binary_assets",
        "verification_model": "gui_required",
    }
    return candidate


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
                "warrant": "A first playable loop should establish the module path.",
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
    }


def _stack_axes() -> dict[str, object]:
    return {
        "fidelity_precedent": {
            "evidence": "The background facts mention turn-based RPG precedent.",
            "judgment": "Use precedent as evidence but keep the repo-native path feasible.",
        },
        "builder_buildability": {
            "evidence": "Codex can author the repo-native modules as text.",
            "judgment": "The selected stack is buildable in a worktree.",
        },
        "asset_supply": {
            "evidence": "The first slice uses named 2D content.",
            "judgment": "The SVG graphicist can supply the needed assets.",
        },
        "distribution_reachability": {
            "evidence": "The selected path runs through the repository test harness.",
            "judgment": "Users can reach the deliverable without a heavyweight engine install.",
        },
        "licensing_cost": {
            "evidence": "Repo-native code avoids commercial engine terms.",
            "judgment": "Licensing and CI costs stay within normal repository work.",
        },
    }


def _implementation() -> dict[str, object]:
    return {
        "systems": [
            {
                "system_name": "Battle loop",
                "behavior_in_game": "Player casts Spark, Slime takes damage, and the meadow gate opens on victory.",
                "named_content": {"entities": ["Player", "Slime"], "content_items": ["Spark", "Meadow gate"]},
                "key_modules": ["game.battle", "game.state"],
            }
        ],
        "persistence": {"saved_fields": ["player_spells", "slime_defeated", "meadow_gate_open"]},
    }


def _patch_plan() -> dict[str, object]:
    return {
        "first_playable": {
            "player_can": ["Enter Green Meadow.", "Cast Spark at Slime."],
            "named_content": {
                "locations": ["Green Meadow"],
                "enemies": ["Slime"],
                "items_or_spells": ["Spark"],
            },
            "win_or_progress_condition": "Slime defeated and meadow gate opens.",
            "how_verified": "Run the battle-loop test and assert meadow_gate_open.",
        },
        "follow_ups": [
            {
                "adds": "Add a second meadow enemy.",
                "named_content": {"locations": ["Green Meadow"], "enemies": ["Bat"], "items_or_spells": ["Spark"]},
            }
        ],
        "deferred": [{"item": "Full campaign map.", "why_safe_to_defer": "The first battle loop is independent."}],
    }


def _risks() -> dict[str, object]:
    return {
        "risks": [
            {
                "id": "risk:candidate",
                "risk": "The repo-native path may touch more files.",
                "mitigation": "Keep the first playable patch limited to battle and state modules.",
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
        patch_plan=_patch_plan(),
    )
    return {"problem": problem}
