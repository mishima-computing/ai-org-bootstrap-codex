from __future__ import annotations

import json
from pathlib import Path
import subprocess
from typing import Any

import pytest

from ai_org import git_wrapper, rfc
from ai_org.rfc import lineage


def test_refine_maps_approved_patch_plan_to_subnumbered_child_branches(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    _write_direction_ok_rfc(repo, "feature", _rfc(), _approach())
    git_wrapper.ensure_serial(repo, "ai-org/rfc/feature")
    prompts: list[str] = []

    def handler(prompt: str) -> dict[str, Any]:
        prompts.append(prompt)
        assert "Approved Technical Approach" in prompt
        assert "first_playable" in prompt
        assert "Battle state" in prompt
        assert "Parent prose only" not in prompt
        return _split(
            [
                _child(
                    "prep",
                    "Prep battle state",
                    "leafed",
                    [
                        "success_criteria:0",
                        "patch_plan:first_playable",
                        "acceptance_tests:screenshot_checks:0",
                    ],
                    ["Battle state is testable."],
                    ["Battle state"],
                ),
                _child(
                    "behavior",
                    "Add visible battle behavior",
                    "leafed",
                    [
                        "patch_plan:follow_ups:0",
                        "acceptance_tests:interaction_checks:0",
                        "must_address_risks:risk-ui",
                    ],
                    ["Spark interaction is visible."],
                    ["Battle UI"],
                ),
                _child(
                    "docs",
                    "Document acceptance evidence",
                    "leafed",
                    ["acceptance_tests:playtest_checks:0"],
                    ["Reviewers can inspect acceptance evidence."],
                    ["Acceptance notes"],
                ),
            ],
            depends_on=[{"from_child_key": "prep", "to_child_key": "behavior", "reason": "Behavior uses state."}],
        )

    _install_codex_fake(monkeypatch, handler)

    result = lineage.refine(repo, "feature")

    assert result["ok"] is True
    assert result["status"] == "split"
    assert result["root_serial"] == "0001"
    assert {child["id"] for child in result["children"]} == {"0001-1", "0001-2", "0001-3"}
    assert git_wrapper.is_ancestor(repo, "ai-org/rfc/0001-1", "ai-org/rfc/0001-2") is True
    assert git_wrapper.is_ancestor(repo, "ai-org/rfc/0001-1", "ai-org/rfc/0001-3") is False
    assert git_wrapper.is_ancestor(repo, "ai-org/rfc/feature", "ai-org/rfc/0001-3") is True

    child_rfc = json.loads(_git(repo, "show", "ai-org/rfc/0001-1:rfc.json"))
    assert set(child_rfc) == set(lineage.RFC_FIELDS)
    assert child_rfc["tech_stack"] == _rfc()["tech_stack"]
    assert child_rfc["user_experience_requirements"] == _rfc()["user_experience_requirements"]
    metadata = json.loads(_git(repo, "show", "ai-org/rfc/0001-2:rfc-metadata.json"))
    assert metadata["lineage"]["depends_on"] == ["0001-1"]
    assert metadata["lineage"]["primary_dependency"] == "0001-1"
    assert git_wrapper.has_subject(repo, "ai-org/rfc/0001-2", "rfc: direction-ok") is True

    ledger = json.loads(_git(repo, "show", "ai-org/rfc/feature:lineage-ledger.json"))
    assert ledger["relation"] == "split-into[AND]"
    assert ledger["coverage"]["patch_plan:follow_ups:0"] == "0001-2"
    assert ledger["depends_on"] == [{"from": "0001-1", "to": "0001-2", "reason": "Behavior uses state."}]
    assert '"order"' not in json.dumps(ledger)
    assert prompts


def test_lineage_coverage_validation_retries_then_fails_closed(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    _write_direction_ok_rfc(repo, "feature", _rfc(), _approach())
    git_wrapper.ensure_serial(repo, "ai-org/rfc/feature")
    calls = 0

    def handler(_prompt: str) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return _split(
            [
                _child(
                    "only",
                    "Only child",
                    "leafed",
                    ["success_criteria:0"],
                    ["One item is covered."],
                    ["Battle state"],
                )
            ]
        )

    _install_codex_fake(monkeypatch, handler)

    result = lineage.refine(repo, "feature")

    assert result["ok"] is False
    assert result["status"] == "failed-closed"
    assert calls == 2
    assert any("patch_plan:first_playable is unmapped" == error for error in result["validation_errors"])
    assert git_wrapper.file_exists(repo, "ai-org/rfc/feature", "lineage-ledger.json") is False


def test_refine_accepts_right_sized_codex_answer_with_surplus_children(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    _write_direction_ok_rfc(repo, "feature", _rfc(), _approach())
    git_wrapper.ensure_serial(repo, "ai-org/rfc/feature")

    def handler(_prompt: str) -> dict[str, Any]:
        return {
            "right_sized": True,
            "summary_sentence": "The approved plan is one reviewable contribution.",
            "sizing_reason": "The implementation can be reviewed as a single bounded change.",
            "children": [
                _child(
                    "surplus",
                    "Surplus child",
                    "leafed",
                    ["success_criteria:0"],
                    ["This child should be ignored."],
                    ["Battle state"],
                )
            ],
            "parent_gate_scope_item_ids": ["patch_plan:first_playable"],
            "depends_on": [
                {"from_child_key": "surplus", "to_child_key": "surplus", "reason": "Schema-forced surplus."}
            ],
            "elaboration_notes": ["Schema-forced surplus."],
        }

    _install_codex_fake(monkeypatch, handler)

    result = lineage.refine(repo, "feature")

    assert result["ok"] is True
    assert result["status"] == "right-sized"
    assert result["surplus_children_ignored"] == 1
    assert git_wrapper.file_exists(repo, "ai-org/rfc/feature", "lineage-ledger.json") is False
    assert git_wrapper.branch_exists(repo, "ai-org/rfc/0001-1") is False


def test_double_mapped_scope_item_is_rejected():
    scope_items = [{"id": "success_criteria:0", "source": "test", "text": "criterion"}]
    split = _split(
        [
            _child("one", "One", "leafed", ["success_criteria:0"], ["ok"], ["system"]),
            _child("two", "Two", "leafed", ["success_criteria:0"], ["ok"], ["system"]),
        ]
    )

    result = lineage.validate_ledger_contract(split, scope_items)

    assert result["ok"] is False
    assert result["errors"] == ["success_criteria:0 is mapped more than once: one, two"]


def test_rolling_wave_keeps_later_stage_coarse_with_exit_criteria(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    _write_direction_ok_rfc(repo, "feature", _rfc(), _approach())
    git_wrapper.ensure_serial(repo, "ai-org/rfc/feature")

    _install_codex_fake(
        monkeypatch,
        lambda _prompt: _split(
            [
                _child(
                    "near",
                    "Near horizon playable",
                    "leafed",
                    [
                        "success_criteria:0",
                        "patch_plan:first_playable",
                        "acceptance_tests:screenshot_checks:0",
                        "acceptance_tests:interaction_checks:0",
                        "must_address_risks:risk-ui",
                    ],
                    ["The first playable passes functional_check."],
                    ["Battle state"],
                ),
                _child(
                    "later",
                    "Later evidence pass",
                    "coarse",
                    ["patch_plan:follow_ups:0", "acceptance_tests:playtest_checks:0"],
                    ["Dependencies are resolved.", "The playtest check is executable."],
                    ["Battle UI"],
                ),
            ],
            depends_on=[{"from_child_key": "near", "to_child_key": "later", "reason": "Later pass builds on first playable."}],
            notes=["Re-elaborate later at the horizon boundary."],
        ),
    )

    result = lineage.refine(repo, "feature", horizon=1)

    assert result["ok"] is True
    coarse = json.loads(_git(repo, "show", "ai-org/rfc/0001-2:rfc-metadata.json"))
    assert coarse["lineage"]["horizon_status"] == "coarse"
    assert coarse["lineage"]["acceptance_criteria"] == [
        "Dependencies are resolved.",
        "The playtest check is executable.",
    ]
    ledger = json.loads(_git(repo, "show", "ai-org/rfc/feature:lineage-ledger.json"))
    assert ledger["children"][1]["horizon_status"] == "coarse"


def test_resolved_rolls_up_leaf_parent_and_integration_gate(tmp_path):
    repo = _init_repo(tmp_path)
    _write_direction_ok_rfc(repo, "feature", _rfc(), _approach())
    git_wrapper.ensure_serial(repo, "ai-org/rfc/feature")
    _write_child_rfc(repo, "0001-1", "ai-org/rfc/feature")
    _write_child_rfc(repo, "0001-2", "ai-org/rfc/feature")
    git_wrapper.commit_files(
        repo,
        "ai-org/rfc/feature",
        {
            "lineage-ledger.json": {
                "children": [
                    {"branch": "ai-org/rfc/0001-1"},
                    {"branch": "ai-org/rfc/0001-2"},
                ]
            }
        },
        subject="rfc: lineage split",
    )
    _accepted_and_merged(repo, "0001-1")
    _accepted_and_merged(repo, "0001-2")

    assert lineage.resolved(repo, "ai-org/rfc/0001-1") is True
    assert lineage.resolved(repo, "ai-org/rfc/feature") is False
    git_wrapper.commit_empty(repo, "ai-org/rfc/feature", "rfc: integration-gate passed")
    assert lineage.resolved(repo, "ai-org/rfc/feature") is True


def test_escalate_and_stale_markers(tmp_path):
    repo = _init_repo(tmp_path)
    _write_child_rfc(repo, "0001-1", "main")
    _write_child_rfc(repo, "0001-2", "main")

    lineage.escalate(repo, "ai-org/rfc/0001-1", {"evidence": "Parent plan omits required migration."})
    lineage.mark_stale(repo, ["ai-org/rfc/0001-2"], "parent-rebaselined v2")

    assert git_wrapper.has_subject(repo, "ai-org/rfc/0001-1", "rfc: blocked:parent-invalidated") is True
    assert git_wrapper.has_subject(repo, "ai-org/rfc/0001-2", "rfc: stale parent-rebaselined v2") is True


def test_elaborate_refines_coarse_child_once_dependencies_resolve(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    _write_child_rfc(repo, "0001-1", "main")
    _write_child_rfc(repo, "0001-2", "ai-org/rfc/0001-1")
    git_wrapper.commit_files(
        repo,
        "ai-org/rfc/0001-2",
        {
            "rfc-metadata.json": {
                "lineage": {
                    "node_id": "0001-2",
                    "horizon_status": "coarse",
                    "depends_on": ["0001-1"],
                }
            },
            "technical-approach.json": _approach(),
        },
        subject="rfc: metadata",
    )
    _accepted_and_merged(repo, "0001-1")
    calls: list[str] = []

    def fake_refine(repo_arg, branch, **kwargs):
        calls.append(branch)
        return {"ok": True, "status": "split", "branch": branch, "kwargs": kwargs}

    monkeypatch.setattr(lineage, "refine", fake_refine)

    assert lineage.coarse_ready(repo, "ai-org/rfc/0001-2") is True
    result = lineage.elaborate(repo, "ai-org/rfc/0001-2", horizon=2)

    assert result["ok"] is True
    assert calls == ["ai-org/rfc/0001-2"]
    assert result["kwargs"] == {"horizon": 2}


def test_rfc_pull_runs_lineage_after_reviewable_and_before_coarse_elaboration(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    _commit_on_branch(repo, "ai-org/rfc/reviewable", "propose rfc")
    _write_direction_ok_rfc(repo, "feature", _rfc(), _approach())
    git_wrapper.ensure_serial(repo, "ai-org/rfc/feature")
    calls: list[str] = []

    monkeypatch.setattr(rfc.review, "run_rfc_review", lambda _repo, rfc_id: calls.append(f"review:{rfc_id}") or {"status": "reviewed"})
    monkeypatch.setattr(rfc.lineage, "refine", lambda _repo, branch: calls.append(f"refine:{branch}") or {"status": "split"})
    monkeypatch.setattr(rfc.lineage, "elaborate", lambda _repo, branch: calls.append(f"elaborate:{branch}") or {"status": "elaborated"})

    assert rfc.pull(repo)["status"] == "reviewed"
    _git(repo, "branch", "-D", "ai-org/rfc/reviewable")
    assert rfc.pull(repo)["status"] == "split"
    assert calls == ["review:reviewable", "refine:ai-org/rfc/feature"]


def test_lineage_schema_is_codex_safe_subset():
    serialized = json.dumps(lineage.LINEAGE_SPLIT_SCHEMA)
    assert "allOf" not in serialized
    assert "anyOf" not in serialized
    assert "oneOf" not in serialized
    _assert_required_is_all_properties(lineage.LINEAGE_SPLIT_SCHEMA)
    _assert_required_is_all_properties(lineage.LINEAGE_SPLIT_SCHEMA["properties"]["children"]["items"])
    _assert_required_is_all_properties(lineage.LINEAGE_SPLIT_SCHEMA["properties"]["depends_on"]["items"])


def _assert_required_is_all_properties(schema: dict[str, Any]) -> None:
    assert schema["additionalProperties"] is False
    assert sorted(schema["required"]) == sorted(schema["properties"])


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.name", "Lineage Test")
    _git(repo, "config", "user.email", "lineage-test@example.invalid")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "base")
    _git(repo, "branch", "-M", "main")
    return repo


def _write_direction_ok_rfc(repo: Path, rfc_id: str, rfc_view: dict[str, Any], approach: dict[str, Any]) -> None:
    git_wrapper.create_branch_with_files(
        repo,
        f"ai-org/rfc/{rfc_id}",
        "main",
        {"rfc.json": rfc_view, "technical-approach.json": approach},
        commit_message="rfc: promoted",
    )
    git_wrapper.commit_empty(repo, f"ai-org/rfc/{rfc_id}", "rfc: direction-ok")


def _write_child_rfc(repo: Path, rfc_id: str, base: str) -> None:
    git_wrapper.create_branch_with_files(
        repo,
        f"ai-org/rfc/{rfc_id}",
        base,
        {"rfc.json": _rfc(), "technical-approach.json": _approach()},
        commit_message=f"rfc: child {rfc_id}",
    )
    git_wrapper.commit_empty(repo, f"ai-org/rfc/{rfc_id}", "rfc: direction-ok inherited")


def _accepted_and_merged(repo: Path, rfc_id: str) -> None:
    git_wrapper.create_branch_with_files(
        repo,
        f"ai-org/contrib/{rfc_id}",
        f"ai-org/rfc/{rfc_id}",
        {f"{rfc_id}.txt": "accepted\n"},
        commit_message="patch",
    )
    git_wrapper.commit_empty(repo, f"ai-org/contrib/{rfc_id}", "acceptance: reachable")
    if not git_wrapper.branch_exists(repo, "ai-org/subsystem"):
        _git(repo, "branch", "ai-org/subsystem", f"ai-org/contrib/{rfc_id}")
    else:
        _git(repo, "checkout", "ai-org/subsystem")
        _git(repo, "merge", "--no-ff", f"ai-org/contrib/{rfc_id}", "-m", f"subsystem: merge ai-org/contrib/{rfc_id}")
        _git(repo, "checkout", "main")


def _rfc() -> dict[str, Any]:
    return {
        "raw_request": "Build a verifiable battle loop.",
        "working_title": "Battle Loop",
        "request_type": "feature",
        "problem_or_motivation": "Parent prose only.",
        "intended_users_or_jobs": "Players need visible battle progress.",
        "desired_outcomes_success": "Spark defeats Slime and visible progress is recorded.",
        "affected_area_platform": "gameplay",
        "tech_stack": {
            "build_strategy": "framework_based",
            "engine": "",
            "framework": "repo-native Python modules",
            "language": "Python",
            "platform": "CLI",
            "rationale": "Use the repository's existing Python modules.",
            "provenance": "requester_specified",
        },
        "user_experience_requirements": {
            "applicability": {"applicability": "user_facing", "not_user_facing_reason": ""},
            "experience_identity": {
                "named_reference": "Readable battle loop.",
                "genre_conventions": "JRPG status and battle-log conventions.",
                "must_resemble": "Visible HP, MP, enemy, spell, and objective state.",
                "must_not_resemble": "Hidden-only mechanics.",
            },
            "presentation_model": {
                "camera_and_view": "Readable battle view.",
                "world_readability": "Slime and Spark feedback are visible.",
                "ui_taxonomy_notes": "HUD and battle log are visible.",
            },
            "core_status_surfaces": {
                "player_status": "HP and MP are visible.",
                "opposition_status": "Slime damage is visible.",
                "inventory_resources": "Spell resources are visible.",
                "objective_progress": "Gate progress is visible.",
                "location_identity": "Meadow identity is visible.",
            },
            "entity_affordances": {
                "interactive_entities": "Targets are visible.",
                "exits_and_transitions": "Gate is visible.",
                "gates_and_locks": "Gate state is visible.",
                "hazards_and_bosses": "Enemy threat is visible.",
                "collectibles": "Rewards are visible.",
                "decorative_elements": "Decorations do not look interactive.",
            },
            "action_feedback_matrix": [
                {"action_verb": "cast Spark", "feedback_requirement": "Damage feedback appears."}
            ],
            "progression_legibility": {
                "current_goal_visibility": "Defeat Slime is visible.",
                "locked_state_feedback": "Closed gate is visible.",
                "unlocked_state_feedback": "Open gate is visible.",
                "flag_observability": "Gate flag has visible evidence.",
                "ending_state_consistency": "Victory matches visible state.",
            },
            "hud_and_ui_flow": {
                "primary_hud": "HP and MP are visible.",
                "secondary_screens": "Status is inspectable.",
                "menu_flow": "Command choice supports Spark.",
                "dialog_flow": "Battle text is readable.",
                "failure_and_recovery": "Failure explains what happened.",
            },
            "visual_language_constraints": {
                "contrast": "Text is readable.",
                "palette_role": "Color is not the only state cue.",
                "silhouette_readability": "Slime and gate are distinct.",
                "labels_and_markers": "Objective markers are readable.",
                "animation_minimums": "Spark feedback appears.",
            },
            "accessibility_baseline": {
                "controls": "Simple inputs.",
                "text_readability": "Text is readable.",
                "color_independence": "State does not rely on color alone.",
                "audio_independence": "Audio has visual equivalents.",
                "pacing": "Battle log is player-paced.",
            },
            "acceptance_tests": {
                "screenshot_checks": ["Battle start shows HP, MP, Slime, and command surfaces."],
                "interaction_checks": ["Spark shows visible damage feedback."],
                "playtest_checks": ["Slime defeat leaves persistent visible gate-open evidence."],
            },
        },
        "background_facts": "Test fixture.",
        "constraints_assumptions": ["Keep the working state after each child."],
        "references": [],
        "grounding_provenance": "Test fixture grounding.",
        "open_questions": [],
        "non_goals_out_of_scope": ["Do not add a full campaign."],
        "proposal_hint": "Add a small battle loop.",
        "alternatives_considered": ["Defer combat."],
    }


def _approach() -> dict[str, Any]:
    return {
        "problem": {
            "id": "problem",
            "goals": [
                {
                    "id": "goal:1",
                    "capability": {"action": "Cast Spark at Slime.", "preconditions": ["Spark learned."]},
                    "verifiable_outcome": {"expected_state": "Slime defeated.", "evidence": "Visible damage."},
                    "verification": {"method": "automated_test", "check": "Assert victory."},
                }
            ],
            "question": {
                "decision": {
                    "id": "decision:repo_native",
                    "implementation": {
                        "id": "implementation:repo_native",
                        "systems": [
                            {
                                "system_name": "Battle state",
                                "behavior_in_game": "Track Slime damage.",
                                "observable_effect": "Damage is visible.",
                                "named_content": {"entities": ["Slime"], "content_items": ["Spark"]},
                                "key_modules": ["game.state"],
                            },
                            {
                                "system_name": "Battle UI",
                                "behavior_in_game": "Render commands.",
                                "observable_effect": "Commands are visible.",
                                "named_content": {"entities": ["Player"], "content_items": ["Command menu"]},
                                "key_modules": ["game.ui"],
                            },
                        ],
                        "persistence": {"saved_fields": ["slime_defeated"]},
                        "patch_plan": {
                            "id": "patch_plan:repo_native",
                            "first_playable": {
                                "player_can": ["Enter meadow.", "Cast Spark."],
                                "named_content": {
                                    "locations": ["Meadow"],
                                    "enemies": ["Slime"],
                                    "items_or_spells": ["Spark"],
                                },
                                "presentation_baseline": "HUD and Spark feedback are visible.",
                                "win_or_progress_condition": "Slime defeated.",
                                "how_verified": "Run battle-loop functional check.",
                            },
                            "follow_ups": [
                                {
                                    "adds": "Add visible gate-open evidence.",
                                    "named_content": {
                                        "locations": ["Meadow"],
                                        "enemies": [],
                                        "items_or_spells": ["Gate"],
                                    },
                                }
                            ],
                            "deferred": [],
                        },
                        "risks": [
                            {
                                "id": "risk-ui",
                                "risk": "UI state may not reflect saved state.",
                                "mitigation": "Functional check must inspect visible state.",
                                "attaches_to": "implementation",
                                "target_id": "implementation:repo_native",
                            }
                        ],
                    },
                }
            },
        }
    }


def _split(
    children: list[dict[str, Any]],
    *,
    depends_on: list[dict[str, str]] | None = None,
    parent_gate: list[str] | None = None,
    notes: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "right_sized": False,
        "summary_sentence": "Split the approved plan into children.",
        "sizing_reason": "The patch plan has multiple stages.",
        "children": children,
        "parent_gate_scope_item_ids": parent_gate or [],
        "depends_on": depends_on or [],
        "elaboration_notes": notes or [],
    }


def _child(
    key: str,
    title: str,
    horizon_status: str,
    scope_item_ids: list[str],
    acceptance_criteria: list[str],
    relevant_systems: list[str],
) -> dict[str, Any]:
    return {
        "child_key": key,
        "working_title": title,
        "summary": f"{title} summary.",
        "horizon_status": horizon_status,
        "scope_item_ids": scope_item_ids,
        "acceptance_criteria": acceptance_criteria,
        "relevant_systems": relevant_systems,
        "patch_plan_slice": f"Implement {title}.",
    }


def _install_codex_fake(monkeypatch: pytest.MonkeyPatch, handler) -> None:
    def fake_run_json(repo: Path, **kwargs):
        assert repo.exists()
        assert kwargs["schema"] == lineage.LINEAGE_SPLIT_SCHEMA
        assert kwargs["schema_filename"] == "rfc-lineage-split.schema.json"
        assert kwargs["output_filename"] == "rfc-lineage-split.json"
        return {"ok": True, "raw": json.dumps(handler(kwargs["prompt"]))}

    monkeypatch.setattr(lineage.codex_exec, "run_json", fake_run_json)


def _commit_on_branch(repo: Path, branch: str, subject: str) -> None:
    _git(repo, "checkout", "-B", branch, "main")
    _git(repo, "commit", "--allow-empty", "-m", subject)
    _git(repo, "checkout", "main")


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return result.stdout.strip()
