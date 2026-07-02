from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from ai_org import git_wrapper, rfc
from ai_org.rfc import lineage
from ai_org.rfc.field_registry import empty_user_experience_requirements


def test_refine_consumes_patch_plan_and_creates_rolling_wave_lineage(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _write_parent(repo, "root")
    _install_codex_fake(monkeypatch, [_split_all_scope()])

    result = lineage.refine(repo, "root", horizon=1)

    assert result["ok"] is True
    assert result["status"] == "refined"
    assert [child["id"] for child in result["children"]] == ["0001-1", "0001-2", "0001-3"]
    assert [child["node_kind"] for child in result["children"]] == ["leaf", "coarse", "coarse"]

    parent_head = git_wrapper.head_sha(repo, "ai-org/rfc/root")
    ledger = json.loads(_git(repo, "show", "ai-org/rfc/root:lineage-ledger.json"))
    assert ledger["relation"] == "split-into"
    assert ledger["split_operator"] == "AND"
    assert set(_scope_ids(ledger)) == set(_expected_scope_ids())
    assert all(item["owner"] != "" for item in ledger["coverage"])
    assert result["ledger_commit"] == parent_head

    prep = "ai-org/rfc/0001-1"
    battle = "ai-org/rfc/0001-2"
    campaign = "ai-org/rfc/0001-3"
    assert git_wrapper.is_ancestor(repo, prep, battle) is True
    assert git_wrapper.is_ancestor(repo, battle, campaign) is False
    assert git_wrapper.is_ancestor(repo, "ai-org/rfc/root", campaign) is True

    battle_meta = json.loads(_git(repo, "show", f"{battle}:rfc-metadata.json"))
    campaign_meta = json.loads(_git(repo, "show", f"{campaign}:rfc-metadata.json"))
    assert battle_meta["branching_mode"] == "serial_after_child"
    assert battle_meta["depends_on_branches"] == [prep]
    assert campaign_meta["branching_mode"] == "parallel_from_parent"
    assert campaign_meta["depends_on_branches"] == [battle]

    prompt = _install_codex_fake.last_prompt
    assert "patch_plan" in prompt
    assert "First playable" in prompt
    assert "Do not derive new scope from prose" in prompt


def test_domain_specification_scope_items_and_split_summary_are_aspect_level():
    approach = _approach()

    scope_items = lineage._scope_items(_rfc(), approach)
    split_view = lineage._approach_split_view(approach)

    assert [item for item in scope_items if item["id"] == "domain_specification:battle-numbers"]
    domain_summary = split_view["domain_specification"][0]
    assert domain_summary["id"] == "domain_specification:battle-numbers"
    assert domain_summary["tables"] == [{"table_name": "stats", "columns": ["name", "hp"], "row_count": 1}]
    assert domain_summary["externalized_tables"][0]["row_count"] == 20
    assert "Slime" not in json.dumps(domain_summary["externalized_tables"])


def test_validate_ledger_contract_fails_closed():
    cases = [
        (_contract_split(children=[_child("a", [])], retained=[]), "unmapped_scope_ids"),
        (_contract_split(children=[_child("a", ["scope:one", "scope:missing"])], retained=["scope:two"]), "unknown_scope_ids"),
        (_contract_split(children=[_child("a", ["scope:one"]), _child("b", ["scope:one"])], retained=["scope:two"]), "double_mapped_scope_ids"),
        (
            _contract_split(
                children=[
                    _child("a", ["scope:one"], depends_on=["b"]),
                    _child("b", ["scope:two"], depends_on=["a"]),
                ],
                retained=[],
            ),
            "cycle",
        ),
    ]
    for split, expected_key in cases:
        result = lineage.validate_ledger_contract(split, _contract_scope())

        assert result["ok"] is False
        assert result["status"] == "feedback-retry"
        assert result[expected_key]


def test_refine_retries_feedback_and_does_not_write_invalid_ledger(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _write_parent(repo, "root")
    _install_codex_fake(monkeypatch, [_split_missing_scope(), _split_missing_scope()])

    result = lineage.refine(repo, "root")

    assert result["ok"] is False
    assert result["status"] == "feedback-retry"
    assert _install_codex_fake.calls == 2
    assert git_wrapper.file_exists(repo, "ai-org/rfc/root", "lineage-ledger.json") is False
    assert git_wrapper.branches(repo, "ai-org/rfc/0001-*") == []


def test_right_sized_with_surplus_children_retries_once_then_honors_verdict(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _write_parent(repo, "root")
    surplus = _right_sized_split()
    surplus["children"] = [_proposal_child("surplus", ["goal:rfc.desired_outcomes_success"])]
    _install_codex_fake(monkeypatch, [surplus, surplus])

    result = lineage.refine(repo, "root")

    assert result["ok"] is True
    assert result["status"] == "right-sized"
    assert result["surplus_children_ignored"] == 1
    assert _install_codex_fake.calls == 2
    assert "deterministic pre-check says this parent is not already one right-sized leaf" in _install_codex_fake.last_prompt
    assert git_wrapper.file_exists(repo, "ai-org/rfc/root", "lineage-ledger.json") is False
    assert git_wrapper.branches(repo, "ai-org/rfc/0001-*") == []


def test_right_sized_with_surplus_children_retries_and_accepts_corrected_split(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _write_parent(repo, "root")
    surplus = _right_sized_split()
    surplus["children"] = [_proposal_child("surplus", ["goal:rfc.desired_outcomes_success"])]
    _install_codex_fake(monkeypatch, [surplus, _split_all_scope()])

    result = lineage.refine(repo, "root")

    assert result["ok"] is True
    assert result["status"] == "refined"
    assert _install_codex_fake.calls == 2
    assert git_wrapper.file_exists(repo, "ai-org/rfc/root", "lineage-ledger.json") is True


def test_first_playable_cannot_depend_on_declared_coarse_child_and_retries(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _write_parent(repo, "root")
    first_playable = _proposal_child(
        "playable",
        [
            "goal:rfc.desired_outcomes_success",
            "patch_plan:first_playable",
            "ux:technical_approach:1:screenshot_checks:1",
            "domain_specification:battle-numbers",
        ],
        depends_on=["later"],
    )
    later = _proposal_child(
        "later",
        [
            "patch_plan:follow_up:1",
            "patch_plan:deferred:1",
            "ux:technical_approach:1:interaction_checks:1",
            "ux:technical_approach:1:playtest_checks:1",
            "risk:state-drift",
        ],
    )
    later["node_kind"] = "coarse"
    invalid = {
        "split_mode": "split_into_children",
        "rationale": "Inverted dependency.",
        "parent_retained_scope_ids": [],
        "children": [first_playable, later],
    }
    _install_codex_fake(monkeypatch, [invalid, _split_all_scope()])

    result = lineage.refine(repo, "root", horizon=1)

    assert result["ok"] is True
    assert _install_codex_fake.calls == 2
    assert "patch_plan:first_playable child cannot depend on a coarse child" in _install_codex_fake.last_prompt


def test_resolved_rolls_up_children_and_parent_integration_gate(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _write_parent(repo, "root")
    _install_codex_fake(monkeypatch, [_single_child_split()])
    result = lineage.refine(repo, "root")
    child = result["children"][0]["branch"]
    contrib = _write_contrib_branch(repo, child, "ai-org/rfc/root")

    assert lineage.resolved(repo, child) is False
    assert lineage.resolved(repo, "ai-org/rfc/root") is False

    git_wrapper.commit_empty(repo, contrib, "acceptance: passed")
    assert lineage.resolved(repo, child) is False

    _merge(repo, "ai-org/rfc/root", contrib)
    assert lineage.resolved(repo, child) is True
    assert lineage.resolved(repo, "ai-org/rfc/root") is False

    git_wrapper.commit_empty(repo, "ai-org/rfc/root", "lineage: integration-gate")
    assert lineage.resolved(repo, "ai-org/rfc/root") is True


def test_resolved_never_requires_child_doc_branch_ancestry_for_sibling_leaves(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _write_parent(repo, "root")
    split = {
        "split_mode": "split_into_children",
        "rationale": "Two independent implementation leaves.",
        "parent_retained_scope_ids": [],
        "children": [
                _proposal_child(
                    "prep",
                    [
                        "goal:rfc.desired_outcomes_success",
                        "patch_plan:first_playable",
                        "ux:technical_approach:1:screenshot_checks:1",
                        "domain_specification:battle-numbers",
                    ],
                ),
            _proposal_child(
                "battle",
                [
                    "patch_plan:follow_up:1",
                    "patch_plan:deferred:1",
                    "ux:technical_approach:1:interaction_checks:1",
                    "ux:technical_approach:1:playtest_checks:1",
                    "risk:state-drift",
                ],
            ),
        ],
    }
    _install_codex_fake(monkeypatch, [split])
    result = lineage.refine(repo, "root")
    children = [child["branch"] for child in result["children"]]
    contribs = [_write_contrib_branch(repo, child, "ai-org/rfc/root") for child in children]

    for child, contrib in zip(children, contribs):
        git_wrapper.commit_empty(repo, child, "acceptance: reachable")
        _merge(repo, "ai-org/rfc/root", contrib)

    assert all(git_wrapper.is_ancestor(repo, child, "ai-org/rfc/root") is False for child in children)
    assert all(lineage.resolved(repo, child) is True for child in children)
    git_wrapper.commit_empty(repo, "ai-org/rfc/root", "lineage: integration-gate")
    assert lineage.resolved(repo, "ai-org/rfc/root") is True


def test_resolved_treats_foreign_inherited_ledger_as_absent_leaf(tmp_path):
    repo = _repo(tmp_path)
    _write_child_branch(repo, "0001-1", "main")
    git_wrapper.commit_files(
        repo,
        "ai-org/rfc/0001-1",
        {"lineage-ledger.json": {"parent_branch": "ai-org/rfc/root", "children": [{"branch": "ai-org/rfc/0001-1"}]}},
        subject="lineage: inherited foreign ledger fixture",
    )
    git_wrapper.commit_empty(repo, "ai-org/rfc/0001-1", "acceptance: passed")
    _merge(repo, "main", "ai-org/rfc/0001-1")

    assert lineage.resolved(repo, "ai-org/rfc/0001-1") is True


def test_resolved_depth_guard_reports_lineage_cycle(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _write_child_branch(repo, "0001-1", "main")
    _write_child_branch(repo, "0001-2", "main")
    git_wrapper.commit_files(
        repo,
        "ai-org/rfc/0001-1",
        {"lineage-ledger.json": {"parent_branch": "ai-org/rfc/0001-1", "children": [{"branch": "ai-org/rfc/0001-2"}]}},
        subject="lineage: cycle fixture",
    )
    git_wrapper.commit_files(
        repo,
        "ai-org/rfc/0001-2",
        {"lineage-ledger.json": {"parent_branch": "ai-org/rfc/0001-2", "children": [{"branch": "ai-org/rfc/0001-1"}]}},
        subject="lineage: cycle fixture",
    )
    monkeypatch.setattr(lineage, "MAX_RESOLUTION_DEPTH", 3)

    with pytest.raises(RuntimeError, match="lineage resolution exceeded maximum depth 3"):
        lineage.resolved(repo, "ai-org/rfc/0001-1")


def test_escalate_blocks_child_rebaselines_parent_and_marks_dependents_stale(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _write_parent(repo, "root")
    _install_codex_fake(monkeypatch, [_split_all_scope()])
    result = lineage.refine(repo, "root")
    prep = result["children"][0]["branch"]
    battle = result["children"][1]["branch"]

    escalation = lineage.escalate(repo, prep, {"evidence": "parent acceptance no longer matches"})

    assert escalation["ok"] is True
    prep_meta = json.loads(_git(repo, "show", f"{prep}:rfc-metadata.json"))
    battle_meta = json.loads(_git(repo, "show", f"{battle}:rfc-metadata.json"))
    assert prep_meta["lifecycle_status"] == "blocked:parent-invalidated"
    assert prep_meta["escalation_evidence"]["evidence"] == "parent acceptance no longer matches"
    assert battle_meta["lifecycle_status"] == "stale"
    assert git_wrapper.has_subject(repo, "ai-org/rfc/root", "rfc: needs-revision lineage parent invalidated")


def test_leaf_child_is_not_split_pending_and_is_right_sized(tmp_path):
    repo = _repo(tmp_path)
    _write_child_branch(repo, "0001-1", "main")
    child = _proposal_child("leaf", ["goal:rfc.desired_outcomes_success"])
    child["id"] = "0001-1"
    child["node_kind"] = "leaf"
    git_wrapper.commit_files(
        repo,
        "ai-org/rfc/0001-1",
        {
            "rfc-metadata.json": {
                "schema": "rfc-lineage-node-v1",
                "id": "0001-1",
                "parent_branch": "ai-org/rfc/root",
                "node_kind": "leaf",
            },
            "technical-approach.json": lineage._child_approach(child),
        },
        subject="lineage: leaf metadata",
    )

    assert lineage.right_sized(lineage._child_approach(child)) is True
    assert lineage.split_pending(repo, "ai-org/rfc/0001-1") is False


def test_elaborate_waits_for_resolved_dependencies(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _write_parent(repo, "root")
    _install_codex_fake(monkeypatch, [_split_all_scope(), _right_sized_split()])
    result = lineage.refine(repo, "root", horizon=1)
    prep = result["children"][0]["branch"]
    battle = result["children"][1]["branch"]

    assert lineage.coarse_ready(repo, battle) is False
    assert lineage.elaborate(repo, battle)["status"] == "blocked-by-dependencies"

    contrib = _write_contrib_branch(repo, prep, "ai-org/rfc/root")
    git_wrapper.commit_empty(repo, prep, "acceptance: passed")
    _merge(repo, "ai-org/rfc/root", contrib)
    assert lineage.coarse_ready(repo, battle) is True

    elaborated = lineage.elaborate(repo, battle)
    assert elaborated["ok"] is True
    assert elaborated["status"] == "right-sized"


def test_lineage_schema_is_codex_safe_subset_and_uses_unambiguous_modes():
    schema = lineage.LINEAGE_SPLIT_SCHEMA
    forbidden = {"$schema", "allOf", "anyOf", "oneOf", "not", "if", "then", "else", "const", "pattern", "format"}
    assert _forbidden_schema_keys(schema, forbidden) == []
    _assert_required_is_all_properties(schema)
    _assert_required_is_all_properties(schema["properties"]["children"]["items"])
    assert schema["properties"]["split_mode"]["enum"] == ["right_sized", "split_into_children"]
    child_props = schema["properties"]["children"]["items"]["properties"]
    assert child_props["branching_mode"]["enum"] == ["parallel_from_parent", "serial_after_child"]
    assert "right_sized" not in child_props


def test_lineage_ledger_uses_unambiguous_dependency_endpoint_names(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _write_parent(repo, "root")
    _install_codex_fake(monkeypatch, [_split_all_scope()])

    lineage.refine(repo, "root", horizon=1)

    ledger = json.loads(_git(repo, "show", "ai-org/rfc/root:lineage-ledger.json"))
    assert ledger["dependency_graph"]
    assert set(ledger["dependency_graph"][0]) == {"prerequisite_branch", "dependent_branch"}
    serialized = json.dumps(ledger)
    assert '"from"' not in serialized
    assert '"to"' not in serialized


def test_rfc_pull_runs_lineage_after_reviewable_and_before_coarse_elaboration(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    git_wrapper.create_branch_with_files(
        repo,
        "ai-org/rfc/reviewable",
        "main",
        {"rfc.json": _rfc(), "technical-approach.json": _approach()},
        commit_message="rfc: draft",
    )
    _write_parent(repo, "root")
    calls: list[str] = []

    monkeypatch.setattr(rfc.review, "run_rfc_review", lambda _repo, rfc_id: calls.append(f"review:{rfc_id}") or {"status": "reviewed"})
    monkeypatch.setattr(rfc.lineage, "refine", lambda _repo, branch: calls.append(f"refine:{branch}") or {"status": "refined"})
    monkeypatch.setattr(rfc.lineage, "elaborate", lambda _repo, branch: calls.append(f"elaborate:{branch}") or {"status": "elaborated"})

    assert rfc.pull(repo)["status"] == "reviewed"
    _git(repo, "branch", "-D", "ai-org/rfc/reviewable")
    assert rfc.pull(repo)["status"] == "refined"
    assert calls == ["review:reviewable", "refine:ai-org/rfc/root"]


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "lineage-b-test@example.invalid")
    _git(repo, "config", "user.name", "Lineage B Test")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "base")
    _git(repo, "branch", "-M", "main")
    return repo


def _write_parent(repo: Path, rfc_id: str) -> None:
    git_wrapper.create_branch_with_files(
        repo,
        f"ai-org/rfc/{rfc_id}",
        "main",
        {
            "rfc.json": _rfc(),
            "technical-approach.json": _approach(),
        },
        commit_message="rfc: direction-ok",
    )


def _write_child_branch(repo: Path, rfc_id: str, base: str) -> None:
    git_wrapper.create_branch_with_files(
        repo,
        f"ai-org/rfc/{rfc_id}",
        base,
        {
            "rfc.json": _rfc(),
            "technical-approach.json": _approach(),
        },
        commit_message="rfc: direction-ok",
    )


def _write_contrib_branch(repo: Path, child_branch: str, base: str) -> str:
    child_id = child_branch.removeprefix("ai-org/rfc/")
    branch = f"ai-org/contrib/{child_id}"
    git_wrapper.create_branch_with_files(
        repo,
        branch,
        base,
        {f"implementation/{child_id}.txt": f"implementation for {child_id}\n"},
        commit_message=f"implement: {child_id}",
    )
    return branch


def _rfc() -> dict[str, object]:
    return {
        "raw_request": "Build the battle slice.",
        "working_title": "Battle Slice",
        "request_type": "feature",
        "problem_or_motivation": "Players need a visible first battle.",
        "intended_users_or_jobs": "Players can complete a tiny battle loop.",
        "desired_outcomes_success": "The battle slice is playable and reviewable.",
        "affected_area_platform": "ai_org.rfc",
        "tech_stack": {
            "build_strategy": "framework_based",
            "engine": "",
            "framework": "repo-native Python modules",
            "language": "Python",
            "platform": "CLI",
            "rationale": "Use existing repository modules.",
            "provenance": "requester_specified",
        },
        "user_experience_requirements": empty_user_experience_requirements(),
        "background_facts": "Test fixture.",
        "constraints_assumptions": [],
        "references": [],
        "grounding_provenance": "Test fixture.",
        "open_questions": [],
        "non_goals_out_of_scope": [],
        "proposal_hint": "Implement the first battle slice.",
        "alternatives_considered": [],
    }


def _approach() -> dict[str, object]:
    return {
        "problem": {
            "question": {
                "decision": {
                    "implementation": {
                        "systems": [{"name": "battle", "key_modules": ["game.battle"]}],
                        "domain_specification": {
                            "id": "domain_specification",
                            "aspects": [
                                {
                                    "id": "domain_specification:battle-numbers",
                                    "aspect_name": "battle numbers",
                                    "applicability": "applies",
                                    "specification_body": "Spark damage and enemy HP are contractual.",
                                    "quantities": [{"name": "Spark damage", "value": "4", "unit": "hit points"}],
                                    "tables": [{"table_name": "stats", "columns": ["name", "hp"], "rows": [["Slime", "4"]]}],
                                    "sources": ["Reference battle loop"],
                                    "externalized_tables": [
                                        {
                                            "table_name": "encounters",
                                            "columns": ["name", "hp"],
                                            "row_count": 20,
                                            "file_ref": "domain-spec/battle-numbers.json",
                                        }
                                    ],
                                }
                            ],
                        },
                        "user_experience_requirements": {
                            **empty_user_experience_requirements(),
                            "acceptance_tests": {
                                "screenshot_checks": ["Screenshot shows HP, MP, enemy, and command surfaces."],
                                "interaction_checks": ["Casting Spark shows damage feedback."],
                                "playtest_checks": ["Defeating the enemy opens the gate."],
                            },
                        },
                        "patch_plan": {
                            "first_playable": {
                                "summary": "First playable battle loop.",
                                "how_verified": "Run functional_check for the battle loop.",
                            },
                            "follow_ups": [{"adds": "Add the battle log."}],
                            "deferred": [{"item": "Campaign map.", "why_safe_to_defer": "Battle loop stands alone."}],
                        },
                        "risks": [
                            {
                                "id": "state-drift",
                                "risk": "Saved battle state can drift from runtime state.",
                                "mitigation": "Verify save and reload.",
                            }
                        ],
                    }
                }
            }
        }
    }


def _split_all_scope() -> dict[str, object]:
    return {
        "split_mode": "split_into_children",
        "rationale": "Prep, behavior, and later campaign work are separate concerns.",
        "parent_retained_scope_ids": [],
        "children": [
            _proposal_child(
                "prep",
                [
                    "goal:rfc.desired_outcomes_success",
                    "patch_plan:first_playable",
                    "ux:technical_approach:1:screenshot_checks:1",
                    "domain_specification:battle-numbers",
                ],
            ),
            _proposal_child(
                "battle",
                ["patch_plan:follow_up:1", "ux:technical_approach:1:interaction_checks:1", "risk:state-drift"],
                branching_mode="serial_after_child",
                serial_after_child_key="prep",
                depends_on=["prep"],
            ),
            _proposal_child(
                "campaign",
                ["patch_plan:deferred:1", "ux:technical_approach:1:playtest_checks:1"],
                depends_on=["battle"],
            ),
        ],
    }


def _single_child_split() -> dict[str, object]:
    return {
        "split_mode": "split_into_children",
        "rationale": "One child covers all executable work.",
        "parent_retained_scope_ids": [],
        "children": [_proposal_child("only", _expected_scope_ids())],
    }


def _split_missing_scope() -> dict[str, object]:
    split = _single_child_split()
    split["children"][0]["scope_item_ids"] = ["goal:rfc.desired_outcomes_success"]
    return split


def _right_sized_split() -> dict[str, object]:
    return {
        "split_mode": "right_sized",
        "rationale": "The coarse node is now a bounded leaf.",
        "parent_retained_scope_ids": [],
        "children": [],
    }


def _proposal_child(
    key: str,
    scope_ids: list[str],
    *,
    branching_mode: str = "parallel_from_parent",
    serial_after_child_key: str = "",
    depends_on: list[str] | None = None,
) -> dict[str, object]:
    return {
        "child_key": key,
        "title": f"{key.title()} Child",
        "stage_name": key,
        "node_kind": "leaf",
        "branching_mode": branching_mode,
        "serial_after_child_key": serial_after_child_key,
        "depends_on_child_keys": depends_on or [],
        "summary": f"Implement {key}.",
        "acceptance_criteria": [f"{key} acceptance passes."],
        "functional_check": f"functional_check verifies {key}.",
        "scope_item_ids": scope_ids,
        "systems": ["game.battle"],
        "ux_acceptance_tests": [],
        "risks": [],
    }


def _contract_scope() -> list[dict[str, str]]:
    return [
        {"id": "scope:one", "kind": "goal", "text": "First scope."},
        {"id": "scope:two", "kind": "goal", "text": "Second scope."},
    ]


def _contract_split(children: list[dict[str, object]], retained: list[str]) -> dict[str, object]:
    return {"split_mode": "split_into_children", "rationale": "test", "children": children, "parent_retained_scope_ids": retained}


def _child(key: str, scope_ids: list[str], *, depends_on: list[str] | None = None) -> dict[str, object]:
    return {
        "child_key": key,
        "scope_item_ids": scope_ids,
        "depends_on_child_keys": depends_on or [],
        "branching_mode": "parallel_from_parent",
        "serial_after_child_key": "",
    }


def _expected_scope_ids() -> list[str]:
    return [
        "goal:rfc.desired_outcomes_success",
        "ux:technical_approach:1:interaction_checks:1",
        "ux:technical_approach:1:playtest_checks:1",
        "ux:technical_approach:1:screenshot_checks:1",
        "patch_plan:first_playable",
        "patch_plan:follow_up:1",
        "patch_plan:deferred:1",
        "domain_specification:battle-numbers",
        "risk:state-drift",
    ]


def _scope_ids(ledger: dict[str, object]) -> list[str]:
    return [item["id"] for item in ledger["scope_items"]]


def _install_codex_fake(monkeypatch: pytest.MonkeyPatch, responses: list[dict[str, object]]) -> None:
    calls = {"count": 0}

    def fake_run_json(repo: Path, **kwargs):
        assert kwargs["schema"] == lineage.LINEAGE_SPLIT_SCHEMA
        assert kwargs["schema_filename"] == "rfc-lineage-b.schema.json"
        assert kwargs["output_filename"] == "rfc-lineage-b.json"
        _install_codex_fake.last_prompt = kwargs["prompt"]
        calls["count"] += 1
        index = min(calls["count"] - 1, len(responses) - 1)
        return {"ok": True, "raw": json.dumps(responses[index])}

    _install_codex_fake.calls = 0
    _install_codex_fake.last_prompt = ""

    def counting_fake(repo: Path, **kwargs):
        result = fake_run_json(repo, **kwargs)
        _install_codex_fake.calls = calls["count"]
        return result

    monkeypatch.setattr(lineage.codex_exec, "run_json", counting_fake)


def _assert_required_is_all_properties(schema: dict) -> None:
    assert schema["additionalProperties"] is False
    assert sorted(schema["required"]) == sorted(schema["properties"])


def _forbidden_schema_keys(value, forbidden: set[str], path: str = "$") -> list[str]:
    if isinstance(value, dict):
        found = [f"{path}.{key}" for key in value if key in forbidden]
        for key, child in value.items():
            found.extend(_forbidden_schema_keys(child, forbidden, f"{path}.{key}"))
        return found
    if isinstance(value, list):
        found = []
        for index, child in enumerate(value):
            found.extend(_forbidden_schema_keys(child, forbidden, f"{path}[{index}]"))
        return found
    return []


def _merge(repo: Path, target: str, source: str) -> None:
    original = _git(repo, "branch", "--show-current")
    try:
        _git(repo, "checkout", target)
        _git(repo, "merge", "--no-ff", "--no-edit", source)
    finally:
        if original:
            _git(repo, "checkout", original)


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return result.stdout.strip()
