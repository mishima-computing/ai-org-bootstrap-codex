"""Brief 25: targeted node revision — the author-side twin of brief19-D.

Two live proofs of the disease: ~725KB whole-step windows drowned a per-item
instruction (brief 22), and a one-fact reviewer demand ("define the Python
version floor and its verification") survived 5 generations of whole-step
rewording churn until two series died at the round cap (DQ round 6; pomodoro
round 6, python-version-support-window; round-0004 ledger: 72/81 nodes
changed). The cure: bounded revision windows per step and byte-preservation of
every non-target node, so the delta ledger and review delta scoping see true
deltas.
"""
from __future__ import annotations

import json
from typing import Any

from ai_org.patchwork_queue import receive as receive_module
from ai_org import review_bodies

import test_patch_series_receive as harness


def _constraint_node(index: int, statement: str) -> dict[str, Any]:
    return {
        "id": f"constraint:hard:{index}",
        "statement": statement,
        "derivation": {"from": "repo", "trace": "Repository evidence."},
        "implication": {"must": "Honor it.", "must_not": "Ignore it."},
    }


def _pattern_node(name: str, *, when_applies: str = "When revising a series.") -> dict[str, Any]:
    return {
        "id": f"prior_art:{receive_module._slug(name)}",
        "name": name,
        "source": {"reference_concept": "kernel review flow", "facet_kind": "none", "where": "kernel docs"},
        "when_applies": when_applies,
        "tradeoffs": {"pros": ["continuity"], "cons": ["discipline cost"]},
        "disposition": {"choice": "adopt", "why": "It fits revision continuity."},
        "traces_to": ["normalized_problem.success_criteria[0]"],
    }


def _pattern_payload(name: str, *, when_applies: str) -> dict[str, Any]:
    payload = _pattern_node(name, when_applies=when_applies)
    payload.pop("id")
    return payload


def _plan_tree() -> dict[str, Any]:
    # Built THROUGH the assembler so the committed fixture is assembly-normalized:
    # a reform round-trip then reproduces untouched nodes byte-identically, which
    # is exactly the invariant under test.
    components = receive_module._approach_components({})
    components["problem"] = {
        "id": "problem",
        "problem": "Version floor fixture.",
        "goals": [{"id": "goal:1", "criterion": "Ship with a defined support window."}],
    }
    components["selected"] = {"selected_candidate_id": "candidate:one"}
    components["candidates"] = {
        "candidates": [
            {"id": "candidate:one", "summary": "First approach."},
            {"id": "candidate:two", "summary": "Second approach."},
            {"id": "candidate:three", "summary": "Third approach."},
        ]
    }
    components["constraints"] = {
        "hard_constraints": [
            {key: value for key, value in _constraint_node(1, "Support the current Python line.").items() if key != "id"},
            {key: value for key, value in _constraint_node(2, "Keep the CLI dependency-free.").items() if key != "id"},
            {key: value for key, value in _constraint_node(3, "Verify headlessly.").items() if key != "id"},
        ],
        "soft_preferences": [],
    }
    components["prior_art"] = {
        "patterns": [
            _pattern_payload("Pattern A", when_applies="When revising a series."),
            _pattern_payload("Pattern B", when_applies="When revising a series."),
            _pattern_payload("Pattern C", when_applies="When revising a series."),
        ]
    }
    components["patch_plan"] = {"first_safe_slice": "slice"}
    return receive_module._assemble_from_components(components)


def _objection(objection_id: str, anchors: list[str], claim: str = "Define the Python version floor and its verification.") -> dict[str, Any]:
    return {
        "objection_id": objection_id,
        "anchor_node_ids": anchors,
        "axis": "compat",
        "type": "blocking",
        "claim": claim,
        "evidence": [],
        "impact": "The support window is undefined.",
        "requested_author_action": "revise_subtree",
        "resolution_authority": "author",
        "status": "open",
    }


# ---------------------------------------------------------------------------
# Plan derivation (mechanical)
# ---------------------------------------------------------------------------


def test_version_floor_demand_targets_exactly_the_anchored_constraint():
    tree = _plan_tree()
    objections = [_objection("compat:1", ["constraint:hard:1", "decision:candidate:one"])]

    plans = receive_module._targeted_revision_plan(tree, objections, ["constraints", "decision"])

    assert set(plans) == {"constraints"}  # decision stays whole-step by ratified rule
    plan = plans["constraints"]
    assert plan["target_ids"] == ["constraint:hard:1"]
    assert "constraint:hard:1" in plan["target_bodies"]
    # The cross-step anchored node arrives as context, never the whole tree.
    assert "decision:candidate:one" in plan["out_of_step_anchor_bodies"]
    assert set(plan["manifest_ids"]) == {"constraint:hard:2", "constraint:hard:3"}
    # Objection text travels verbatim.
    assert plan["objections"][0]["claim"] == "Define the Python version floor and its verification."


def test_whole_step_fallbacks_fire_on_majority_root_and_missing_generation():
    tree = _plan_tree()
    # Majority: 2 of 3 candidates anchored (>50%) -> whole step, no plan.
    majority = receive_module._targeted_revision_plan(
        tree, [_objection("a:1", ["candidate:one", "candidate:two"])], ["candidates"]
    )
    assert majority == {}
    # Minority: 1 of 3 -> targeted, and evaluation anchors normalize to their candidate.
    minority = receive_module._targeted_revision_plan(
        tree, [_objection("a:2", ["evaluation:candidate:two"])], ["candidates"]
    )
    assert minority["candidates"]["target_ids"] == ["candidate:two"]
    # Step container anchored -> root covered -> whole step.
    container = receive_module._targeted_revision_plan(
        tree, [_objection("a:3", ["question:approach", "candidate:one"])], ["candidates"]
    )
    assert container == {}
    # No committed generation -> whole step.
    bare = _plan_tree()
    bare["problem"]["question"]["candidates"] = []
    empty = receive_module._targeted_revision_plan(bare, [_objection("a:4", ["candidate:one"])], ["candidates"])
    assert empty == {}


# ---------------------------------------------------------------------------
# Bounded window + code-side merge
# ---------------------------------------------------------------------------


def test_constraint_revision_window_is_bounded_and_merge_byte_preserves(monkeypatch, tmp_path):
    tree = _plan_tree()
    objections = [_objection("compat:1", ["constraint:hard:1", "decision:candidate:one"])]
    plan = receive_module._targeted_revision_plan(tree, objections, ["constraints"])["constraints"]
    components = receive_module._approach_components(tree)
    committed = components["constraints"]
    untouched_before = committed["hard_constraints"][1]
    calls: list[dict[str, Any]] = []

    revised_item = {
        "id": "constraint:hard:1",
        "statement": "Support CPython 3.12+ as the version floor.",
        "derivation": {"from": "repo", "trace": "Repository evidence."},
        "implication": {"must": "Declare and verify 3.12+.", "must_not": "Ship untested floors."},
    }

    def fake_run_json(repo, **kwargs):
        calls.append(kwargs)
        return {"ok": True, "raw": json.dumps({"hard_constraints": [revised_item], "soft_preferences": []})}

    monkeypatch.setattr(receive_module.codex_exec, "run_json", fake_run_json)

    merged = receive_module._revise_step_nodes("constraints", tmp_path, components, plan)

    # The demanded fact landed in the demanded node...
    assert merged["hard_constraints"][0]["statement"] == "Support CPython 3.12+ as the version floor."
    assert "id" not in merged["hard_constraints"][0]
    # ...and every non-target item is the SAME object (byte-preservation).
    assert merged["hard_constraints"][1] is untouched_before
    assert merged["hard_constraints"][2] is committed["hard_constraints"][2]

    assert len(calls) == 1
    prompt = calls[0]["prompt"]
    # Window contents: objections verbatim, target body, out-of-step anchor body,
    # revision contract, manifest — and NOT the whole tree.
    assert "Define the Python version floor and its verification." in prompt
    assert "Support the current Python line." in prompt
    assert "decision:candidate:one" in prompt
    assert "REVISION CONTRACT" in prompt
    assert "constraint:hard:2" in prompt  # manifest id
    # Unrelated step bodies stay out (the anchored decision node's own subtree is
    # allowed by the brief: "that anchored node's body").
    assert "Pattern A" not in prompt
    assert "First approach." not in prompt
    # Fragment ids are enum-closed to the targets.
    schema = calls[0]["schema"]
    assert schema["properties"]["hard_constraints"]["items"]["properties"]["id"]["enum"] == ["constraint:hard:1"]


def test_fragment_addressing_a_non_target_fails_the_round(monkeypatch, tmp_path):
    tree = _plan_tree()
    plan = receive_module._targeted_revision_plan(
        tree, [_objection("a:1", ["candidate:two"])], ["candidates"]
    )["candidates"]
    components = receive_module._approach_components(tree)
    calls: list[dict[str, Any]] = []

    def fake_run_json(repo, **kwargs):
        calls.append(kwargs)
        return {"ok": True, "raw": json.dumps({"candidates": [
            {"id": "candidate:brand-new", "name": "New", "kind": "repo_native", "summary": "Not a target.",
             "stack_requirement": {"build_strategy": "framework_based", "engine": "", "framework": "X",
                                    "language": "Python", "platform": "browser",
                                    "authoring_model": "text_first", "verification_model": "headless_ci"},
             "first_proof_moment": {"user_actions": ["Run."], "named_content": [{"name": "N", "kind": "module"}],
                                     "win_or_progress_condition": "Works."},
             "core_systems": ["cli"], "draws_on": ["Pattern A"]}
        ]})}

    monkeypatch.setattr(receive_module.codex_exec, "run_json", fake_run_json)

    result = receive_module._revise_step_nodes("candidates", tmp_path, components, plan)

    assert result["ok"] is False
    assert "addresses no target node" in result["error"]
    assert len(calls) >= 2  # validation feedback retried within the bounded loop


# ---------------------------------------------------------------------------
# End-to-end: true deltas flow into the ledger
# ---------------------------------------------------------------------------


def test_reform_targeted_prior_art_revision_yields_true_delta(tmp_path, monkeypatch):
    harness._install_historical_reform_preparation(monkeypatch)
    repo = harness._init_repo(tmp_path)
    patch_series_view = harness._patch_series_view("Reviewable patch series")
    tree = _plan_tree()
    branch = "ai-org/patch-series/reviewable-patch_series"
    harness._git(repo, "checkout", "-B", branch, "main")
    (repo / "patch-series-cover-letter.json").write_text(json.dumps(patch_series_view) + "\n", encoding="utf-8")
    (repo / "technical-approach-plan.json").write_text(json.dumps(tree) + "\n", encoding="utf-8")
    harness._git(repo, "add", "patch-series-cover-letter.json", "technical-approach-plan.json")
    harness._git(repo, "commit", "-m", "initial patch_series")
    harness._git(repo, "commit", "--allow-empty", "-m", "patch_series: needs-revision round 1")
    harness._git(repo, "checkout", "main")
    objection = harness._blocking_objection("prior_art:pattern-b")
    harness._write_review_round(repo, "reviewable-patch_series", objection)
    calls: list[dict[str, Any]] = []

    def fake_run_json(repo_path, **kwargs):
        calls.append(kwargs)
        return {"ok": True, "raw": json.dumps({"patterns": [
            _pattern_payload("Pattern B", when_applies="Revised: applies to bounded revision windows.")
        ]})}

    monkeypatch.setattr(receive_module.codex_exec, "run_json", fake_run_json)

    result = receive_module.reform_patch_series(repo, "reviewable-patch_series")

    assert result["status"] == "reformed"
    assert [call["failure_label"] for call in calls] == ["Codex targeted prior_art revision"]
    before = tree
    revised_tree = json.loads(harness._git(repo, "show", f"{branch}:technical-approach-plan.json"))
    # Byte-preservation across the step: untouched patterns identical.
    revised_patterns = {item["id"]: item for item in revised_tree["problem"]["prior_art"]}
    before_patterns = {item["id"]: item for item in before["problem"]["prior_art"]}
    assert revised_patterns["prior_art:pattern-a"] == before_patterns["prior_art:pattern-a"]
    assert revised_patterns["prior_art:pattern-c"] == before_patterns["prior_art:pattern-c"]
    assert revised_patterns["prior_art:pattern-b"]["when_applies"] == "Revised: applies to bounded revision windows."
    # The ledger shows a TRUE delta: the target plus its ancestor container only —
    # not the 72/81-node churn that killed two series at the round cap.
    ledger = review_bodies.read_record(repo, branch, result["revision_delta_path"], review_bodies.REVISION_DELTA_RECIPE)
    assert set(ledger["changed_node_ids"]) == {"prior_art:pattern-b", "problem"}
    assert ledger["added_node_ids"] == []
    assert ledger["pruned_node_ids"] == []

# ---------------------------------------------------------------------------
# Brief 31: tree position outranks id-prefix guessing (run 4 rounds 4-5)
# ---------------------------------------------------------------------------
# Live disease: real candidate ids are unprefixed LLM slugs, but the node->step
# map let _step_for_node_id's prefix guess OVERRIDE the tree-derived step, so
# every real candidate node routed to "problem". The identical
# candidate-anchored blocking objection survived reform rounds 4 AND 5 with
# affected_steps [problem, constraints, risks] - the objected candidate text
# was never revised, and the run paused one round before the cap.


def _run4_tree() -> dict[str, Any]:
    components = receive_module._approach_components({})
    components["problem"] = {
        "id": "problem",
        "problem": "Run-4 shape: candidate ids are unprefixed slugs.",
        "goals": [{"id": f"goal:{index}", "criterion": f"Goal {index}."} for index in range(1, 5)],
    }
    components["selected"] = {"selected_candidate_id": "python_stdlib_contract_locked_cli"}
    components["candidates"] = {
        "candidates": [
            {"id": "python_stdlib_contract_locked_cli", "summary": "Stdlib-only contract-locked CLI."},
            {"id": "framework_backed_cli", "summary": "Framework-backed CLI."},
            {"id": "hybrid_split_cli", "summary": "Hybrid split CLI."},
        ]
    }
    components["constraints"] = {
        "hard_constraints": [
            {key: value for key, value in _constraint_node(index, f"Hard constraint {index}.").items() if key != "id"}
            for index in range(1, 7)
        ],
        "soft_preferences": [],
    }
    components["risks"] = {
        "risks": [
            {
                "id": "risk:final-break-semantics-ambiguity",
                "risk": "Final-break semantics are ambiguous.",
                "mitigation": "Define them explicitly.",
                "attaches_to": "candidate",
                "target_id": "python_stdlib_contract_locked_cli",
            }
        ]
    }
    components["patch_plan"] = {"first_safe_slice": "slice"}
    return receive_module._assemble_from_components(components)


def test_node_step_map_derives_steps_from_tree_position_not_id_prefix():
    step_map = receive_module._node_step_map(_run4_tree())

    # The run-4 fallacy: an unprefixed candidate slug is a CANDIDATES node
    # because it sits in the candidates subtree, whatever its name looks like.
    assert step_map["python_stdlib_contract_locked_cli"] == "candidates"
    assert step_map["framework_backed_cli"] == "candidates"
    # Prefixed families keep their steps - now via tree position.
    assert step_map["question:approach"] == "candidates"
    assert step_map["evaluation:python_stdlib_contract_locked_cli"] == "candidates"
    assert step_map["decision:python_stdlib_contract_locked_cli"] == "decision"
    assert step_map["implementation:python_stdlib_contract_locked_cli"] == "implementation"
    assert step_map["patch_plan:python_stdlib_contract_locked_cli"] == "patch_plan"
    assert step_map["domain_specification"] == "domain_specification"
    assert step_map["risk:final-break-semantics-ambiguity"] == "risks"
    assert step_map["constraint:hard:6"] == "constraints"
    assert step_map["goal:4"] == "problem"
    assert step_map["problem"] == "problem"


def test_run4_live_anchor_list_routes_candidates_into_affected_steps():
    tree = _run4_tree()
    # The EXACT anchor list from the identical blocking objection of run 4
    # rounds 4 and 5 (2026-07-05).
    objections = [
        _objection(
            "arch:1",
            [
                "python_stdlib_contract_locked_cli",
                "constraint:hard:6",
                "risk:final-break-semantics-ambiguity",
                "goal:4",
            ],
        )
    ]

    steps = receive_module._affected_reform_steps(tree, objections)

    assert "candidates" in steps  # the step that never fired in rounds 4-5
    assert steps == [
        step
        for step in receive_module.REFORM_STEP_ORDER
        if step in {"problem", "constraints", "candidates", "risks"}
    ]


def test_unprefixed_anchor_absent_from_tree_falls_back_to_candidates():
    # Fallback vocabulary: unprefixed ids are candidate slugs (consistent with
    # brief 21's reference-family routing), not "problem".
    assert receive_module._step_for_node_id("pruned_dead_generation_slug") == "candidates"

    steps = receive_module._affected_reform_steps(
        _plan_tree(), [_objection("arch:2", ["pruned_dead_generation_slug"])]
    )
    assert steps == ["candidates"]


def test_prefixed_id_families_keep_their_fallback_steps():
    for node_id, expected in {
        "problem": "problem",
        "goal:2": "problem",
        "constraint:hard:1": "constraints",
        "prior_art:pattern-a": "prior_art",
        "question:approach": "candidates",
        "candidate:one": "candidates",
        "evaluation:candidate:one": "candidates",
        "decision:candidate:one": "decision",
        "implementation:candidate:one": "implementation",
        "domain_specification": "domain_specification",
        "domain_specification:battle-numbers": "domain_specification",
        "patch_plan:candidate:one": "patch_plan",
        "risk:base": "risks",
    }.items():
        assert receive_module._step_for_node_id(node_id) == expected, node_id


def test_reform_candidate_anchored_objection_revises_candidate_and_answers(tmp_path, monkeypatch):
    harness._install_historical_reform_preparation(monkeypatch)
    repo = harness._init_repo(tmp_path)
    patch_series_view = harness._patch_series_view("Reviewable patch series")
    tree = _run4_tree()
    branch = "ai-org/patch-series/reviewable-patch_series"
    harness._git(repo, "checkout", "-B", branch, "main")
    (repo / "patch-series-cover-letter.json").write_text(json.dumps(patch_series_view) + "\n", encoding="utf-8")
    (repo / "technical-approach-plan.json").write_text(json.dumps(tree) + "\n", encoding="utf-8")
    harness._git(repo, "add", "patch-series-cover-letter.json", "technical-approach-plan.json")
    harness._git(repo, "commit", "-m", "initial patch_series")
    harness._git(repo, "commit", "--allow-empty", "-m", "patch_series: needs-revision round 1")
    harness._git(repo, "checkout", "main")
    objection = harness._blocking_objection("python_stdlib_contract_locked_cli")
    harness._write_review_round(repo, "reviewable-patch_series", objection)
    calls: list[dict[str, Any]] = []

    revised_candidate = {
        "id": "python_stdlib_contract_locked_cli",
        "name": "Stdlib contract-locked CLI",
        "kind": "repo_native",
        "summary": "Revised: final-break semantics are defined in the contract.",
        "stack_requirement": {
            "build_strategy": "framework_based",
            "engine": "",
            "framework": "stdlib",
            "language": "Python",
            "platform": "browser",
            "authoring_model": "text_first",
            "verification_model": "headless_ci",
        },
        "first_proof_moment": {
            "user_actions": ["Run the CLI."],
            "named_content": [{"name": "Contract", "kind": "module"}],
            "win_or_progress_condition": "Final break lands per the defined semantics.",
        },
        "core_systems": ["cli"],
        "draws_on": ["Requester notes on final-break semantics"],
    }

    def fake_run_json(repo_path, **kwargs):
        calls.append(kwargs)
        return {"ok": True, "raw": json.dumps({"candidates": [revised_candidate]})}

    monkeypatch.setattr(receive_module.codex_exec, "run_json", fake_run_json)

    result = receive_module.reform_patch_series(repo, "reviewable-patch_series")

    assert result["status"] == "reformed"
    # The candidates step fired (in rounds 4-5 it never did) and it was the
    # bounded targeted call, not a whole-step rewrite.
    assert [call["failure_label"] for call in calls] == ["Codex targeted candidates revision"]
    revised_tree = json.loads(harness._git(repo, "show", f"{branch}:technical-approach-plan.json"))
    revised_by_id = {
        node["id"]: node for node in revised_tree["problem"]["question"]["candidates"]
    }
    before_by_id = {node["id"]: node for node in tree["problem"]["question"]["candidates"]}
    assert (
        revised_by_id["python_stdlib_contract_locked_cli"]["summary"]
        == "Revised: final-break semantics are defined in the contract."
    )
    # Non-target candidates byte-preserved.
    assert revised_by_id["framework_backed_cli"] == before_by_id["framework_backed_cli"]
    assert revised_by_id["hybrid_split_cli"] == before_by_id["hybrid_split_cli"]
    # The objection is classified as ADDRESSED because its anchor is in the
    # true delta - not carried forward on problem-step churn as in rounds 4-5.
    ledger = review_bodies.read_record(repo, branch, result["revision_delta_path"], review_bodies.REVISION_DELTA_RECIPE)
    responses = {item["objection_id"]: item for item in ledger["objection_responses"]}
    answer = responses[str(objection["objection_id"])]
    assert answer["classification"] == "addressed_by_change"
    assert "python_stdlib_contract_locked_cli" in ledger["changed_node_ids"]
