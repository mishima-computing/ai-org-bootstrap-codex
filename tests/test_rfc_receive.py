from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from ai_org.patch.implement import _is_common_8 as _implement_is_registry_rfc
from ai_org.rfc import receive as receive_module
from ai_org.rfc.field_registry import FIELD_REGISTRY, LINT_SCOPES
from ai_org.rfc.receive import (
    COMMON_8_FIELDS,
    GROUNDING_SCHEMA,
    GROUNDING_VERDICT_SCHEMA,
    REQUEST_SCHEMA,
    GroundingResult,
    intake,
    produce_rfc,
    receive,
)


@pytest.fixture(autouse=True)
def default_no_completeness_profile(monkeypatch):
    monkeypatch.setattr(
        receive_module.reference,
        "build_completeness_profile",
        lambda *args, **kwargs: {
            "status": "skipped_test",
            "term": "test specification completeness profile",
            "term_key": "test specification completeness profile",
            "kept": 0,
            "design_kept": 0,
        },
    )


def test_receive_validates_raw_request_only_from_dict():
    request = {
        "raw_request": "Make Dragon Quest.",
    }

    assert receive(request) == request
    assert tuple(REQUEST_SCHEMA["recognized_fields"]) == COMMON_8_FIELDS
    assert REQUEST_SCHEMA["required"] == ["raw_request"]
    assert "grounding_provenance" in REQUEST_SCHEMA["field_registry"]


def test_receive_validates_request_from_json_file(tmp_path):
    path = tmp_path / "request.json"
    request = {
        "raw_request": "Load JSON into a validated request dict.",
    }
    path.write_text(
        json.dumps(request),
        encoding="utf-8",
    )

    assert receive(path) == request


@pytest.mark.parametrize(
    ("request_data", "missing_field"),
    [
        ({}, "raw_request"),
        ({"raw_request": ""}, "raw_request"),
        ({"raw_request": "   "}, "raw_request"),
    ],
)
def test_receive_missing_raw_request_raises_clear_error(request_data, missing_field):
    with pytest.raises(ValueError, match=f"{missing_field!r} is required"):
        receive(request_data)


def test_receive_accepts_legacy_one_line_request_as_raw_request():
    assert receive({"title": "Minimal"}) == {
        "title": "Minimal",
        "raw_request": "Minimal",
    }


def test_rfc_handoff_requires_full_registry_shape():
    complete = _rfc_view("Complete Handoff")
    assert receive_module._is_rfc_view(complete) is True

    missing = dict(complete)
    missing.pop("grounding_provenance")
    assert receive_module._is_rfc_view(missing) is False


def test_tech_stack_structured_field_validates():
    rfc = _rfc_view("Structured Stack")
    assert receive_module._is_rfc_view(rfc) is True
    rfc["tech_stack"] = {**rfc["tech_stack"], "build_strategy": "invalid"}
    assert receive_module._is_rfc_view(rfc) is False


def test_validate_tech_stack_unspecified_requires_empty_choice_fields():
    unspecified = {
        "build_strategy": "",
        "engine": "",
        "framework": "",
        "language": "",
        "platform": "",
        "rationale": "",
        "provenance": "unspecified",
    }

    assert receive_module.validate_tech_stack(unspecified)
    assert not receive_module.validate_tech_stack({**unspecified, "build_strategy": "framework_based"})
    assert not receive_module.validate_tech_stack({**unspecified, "engine": "Unity"})
    assert not receive_module.validate_tech_stack({**unspecified, "rationale": "Grounding chose nothing."})


def test_ai_deliberated_tech_stack_platform_is_user_facing():
    tech_stack = {
        "build_strategy": "framework_based",
        "engine": "",
        "framework": "Phaser",
        "language": "TypeScript",
        "platform": "browser",
        "rationale": "Approach deliberation selected Phaser for a text-authored browser runtime.",
        "provenance": "ai_deliberated",
    }

    assert receive_module.validate_tech_stack(tech_stack)
    assert not receive_module.validate_tech_stack(
        {**tech_stack, "platform": "headless functional_check target"}
    )


def test_engine_based_tech_stack_requires_real_engine_product_name():
    engine_based = {
        "build_strategy": "engine_based",
        "engine": "Godot",
        "framework": "",
        "language": "GDScript",
        "platform": "browser",
        "rationale": "Approach deliberation selected Godot after comparing engine options.",
        "provenance": "ai_deliberated",
    }

    assert receive_module.validate_tech_stack(engine_based)
    assert not receive_module.validate_tech_stack({**engine_based, "engine": "browser standards"})


def test_user_experience_requirements_validator_enforces_applicability_completeness():
    user_facing = _ux_requirements()
    assert receive_module.validate_user_experience_requirements(user_facing)

    empty_identity = {
        **user_facing,
        "experience_identity": {**user_facing["experience_identity"], "named_reference": ""},
    }
    assert not receive_module.validate_user_experience_requirements(empty_identity)

    empty_acceptance = {
        **user_facing,
        "acceptance_tests": {**user_facing["acceptance_tests"], "playtest_checks": []},
    }
    assert not receive_module.validate_user_experience_requirements(empty_acceptance)

    not_user_facing = receive_module.entrance_defaults({"raw_request": "Refactor internals."})[
        "user_experience_requirements"
    ]
    assert receive_module.validate_user_experience_requirements(not_user_facing)
    assert not receive_module.validate_user_experience_requirements(
        {
            **not_user_facing,
            "applicability": {"applicability": "not_user_facing", "not_user_facing_reason": ""},
        }
    )


def test_receive_preserves_extra_keys():
    assert receive(
        {
            "title": "Extra data",
            "problem": "Unknown keys should not be rejected.",
            "custom_priority": "high",
        }
    )["custom_priority"] == "high"


def test_produce_rfc_forms_approach_and_writes_sibling_artifact(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    monkeypatch.setenv("AI_ORG_REQUIRE_CONFIRMATION", "true")
    request = receive(
        {
            "raw_request": "Manual Intake: commit the validated registry RFC as rfc.json.",
            "custom_priority": "high",
        }
    )
    grounded = _rfc_view(
        "Manual Intake",
        raw_request=request["raw_request"],
        proposal_hint="Commit the validated registry RFC as rfc.json.",
    )

    monkeypatch.setattr(receive_module, "_ground_with_contract", lambda repo, rfc: GroundingResult(grounded, "identity"))
    approach_tree = _approach_tree()
    calls = []

    def fake_build_from_rfc(rfc_view, context=None, **kwargs):
        calls.append("build_from_rfc")
        assert rfc_view == grounded
        assert kwargs == {"kinds": ("design",)}
        assert context["repo"] == repo.resolve()
        assert context["repo_root"] == repo.resolve()
        assert "language" not in context
        assert "environment" not in context
        assert "version" not in context
        return {"terms": {}, "processed_terms": ["battle loop"], "expanded": [], "hits": [], "failed": {}}

    def fake_build_completeness_profile(rfc_view, context=None, **kwargs):
        calls.append("build_completeness_profile")
        assert calls == ["build_from_rfc", "build_completeness_profile"]
        assert rfc_view == grounded
        assert context["repo"] == repo.resolve()
        assert kwargs == {}
        return {
            "status": "expanded",
            "deliverable_kind": "software deliverable",
            "term": "software deliverable specification completeness profile",
            "term_key": "software deliverable specification completeness profile",
            "kept": 2,
            "design_kept": 2,
            "facets": [{"aspect_name": "battle numbers", "structure": "Declare combat quantities."}],
            "aspects": [{"aspect_name": "battle numbers", "structure": "Declare combat quantities."}],
        }

    def fake_start_background_build(rfc_view, context=None, **kwargs):
        calls.append("start_background_build")
        assert calls == ["build_from_rfc", "build_completeness_profile", "start_background_build"]
        assert kwargs == {"kinds": ("implementation",)}
        assert context["repo"] == repo.resolve()
        return object()

    def fake_form_technical_approach(rfc_view, repo_path, **kwargs):
        calls.append("form_technical_approach")
        assert calls == [
            "build_from_rfc",
            "build_completeness_profile",
            "start_background_build",
            "form_technical_approach",
        ]
        assert repo_path == repo.resolve()
        assert kwargs["context"]["repo"] == repo.resolve()
        assert kwargs["reference_terms"] == ["battle loop"]
        assert kwargs["completeness_profile"]["facets"][0]["aspect_name"] == "battle numbers"
        assert "language" not in kwargs["context"]
        assert "environment" not in kwargs["context"]
        assert "version" not in kwargs["context"]
        return {
            "ok": True,
            "technical_approach": approach_tree,
            "external_files": {"domain-spec/battle-numbers.json": {"tables": []}},
        }

    monkeypatch.setattr(receive_module.reference, "build_from_rfc", fake_build_from_rfc)
    monkeypatch.setattr(receive_module.reference, "build_completeness_profile", fake_build_completeness_profile)
    monkeypatch.setattr(receive_module.reference, "start_background_build", fake_start_background_build)
    monkeypatch.setattr(receive_module, "form_technical_approach", fake_form_technical_approach)

    result = produce_rfc(request, repo)

    assert result["ok"] is True
    assert result["status"] == "promoted"
    assert result["id"] == "manual-intake"
    assert result["branch"] == "ai-org/rfc/manual-intake"
    assert result["commit"] == _git(repo, "rev-parse", "refs/heads/ai-org/rfc/manual-intake")
    assert result["technical_approach_path"] == "technical-approach.json"
    assert result["reference_completeness_profile"] == {
        "status": "expanded",
        "deliverable_kind": "software deliverable",
        "term": "software deliverable specification completeness profile",
        "term_key": "software deliverable specification completeness profile",
        "kept": 2,
        "design_kept": 2,
        "facets": [{"aspect_name": "battle numbers", "structure": "Declare combat quantities."}],
        "aspects": [{"aspect_name": "battle numbers", "structure": "Declare combat quantities."}],
    }
    assert _git(repo, "rev-parse", "HEAD") == _git(repo, "rev-parse", "refs/heads/main")
    assert _git(repo, "show", "main:README.md") == "base"
    produced = json.loads(_git(repo, "show", "ai-org/rfc/manual-intake:rfc.json"))
    assert produced == grounded
    assert _implement_is_registry_rfc(produced)
    assert "custom_priority" not in produced
    assert json.loads(_git(repo, "show", "ai-org/rfc/manual-intake:technical-approach.json")) == approach_tree
    assert json.loads(_git(repo, "show", "ai-org/rfc/manual-intake:domain-spec/battle-numbers.json")) == {"tables": []}
    assert calls == [
        "build_from_rfc",
        "build_completeness_profile",
        "start_background_build",
        "form_technical_approach",
    ]
    log_events = [
        json.loads(line)
        for log_path in (repo / ".ai-org" / "log" / "runs").glob("*/*/supervisor.jsonl")
        for line in log_path.read_text(encoding="utf-8").splitlines()
    ]
    event_types = [event["event_type"] for event in log_events]
    assert "rfc.produce.started" in event_types
    assert "reference.design_build.started" in event_types
    assert "reference.completeness_profile.started" in event_types
    assert "rfc.promoted" in event_types
    assert "rfc.produce.completed" in event_types


def test_produce_rfc_forwards_progress_path(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    request = receive({"raw_request": "Manual Intake: commit with progress snapshots."})
    grounded = _rfc_view("Manual Intake", raw_request=request["raw_request"])
    progress_path = tmp_path / "progress" / "technical-approach.json"

    monkeypatch.setattr(receive_module, "_ground_with_contract", lambda repo, rfc: GroundingResult(grounded, "identity"))
    monkeypatch.setattr(
        receive_module.reference,
        "build_from_rfc",
        lambda *args, **kwargs: {"terms": {}, "processed_terms": ["battle loop"], "expanded": [], "hits": [], "failed": {}},
    )
    monkeypatch.setattr(receive_module.reference, "start_background_build", lambda *args, **kwargs: object())

    def fake_form_technical_approach(rfc_view, repo_path, **kwargs):
        assert kwargs["progress_path"] == progress_path
        Path(kwargs["progress_path"]).parent.mkdir(parents=True, exist_ok=True)
        Path(kwargs["progress_path"]).write_text(json.dumps({"current_step": None}), encoding="utf-8")
        return {"ok": True, "technical_approach": _approach_tree()}

    monkeypatch.setattr(receive_module, "form_technical_approach", fake_form_technical_approach)

    result = produce_rfc(request, repo, progress_path=progress_path)

    assert result["status"] == "promoted"
    assert json.loads(progress_path.read_text(encoding="utf-8")) == {"current_step": None}


def test_intake_grounding_confident_writes_grounded_branch(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    request = receive(
        {
            "raw_request": "Make a maze arcade game like kumo. Build a spider labyrinth.",
        }
    )
    grounded = _rfc_view(
        "Auto-Battle Party Dungeon RPG",
        raw_request=request["raw_request"],
        problem_or_motivation="A rough request for a game like kumo needs the correct auto-battle dungeon RPG grounding.",
        intended_users_or_jobs="Players who want idle party-building dungeon RPG play.",
        desired_outcomes_success="The game has party setup, dungeon runs, loot, and progression.",
        affected_area_platform="game",
        background_facts="Kumo is treated here as an auto-battle party dungeon RPG reference.",
        grounding_provenance="Grounding corrected kumo from a maze arcade assumption to an auto-battle dungeon RPG reference.",
        proposal_hint="Build an auto-battle party dungeon RPG loop with party setup, dungeon runs, loot, and progression.",
        alternatives_considered=["Build a maze arcade game, but that is the wrong genre for the reference."],
    )
    notes = "Found kumo is an auto-battle party dungeon RPG; corrected wrong maze-arcade framing."

    calls = []

    def handler(cmd):
        assert cmd[:4] == ["codex", "exec", "--sandbox", "read-only"]
        kind = _schema_kind(cmd[cmd.index("--output-schema") + 1])
        calls.append(kind)
        if kind == "verifier":
            return {
                "faithful_specific": True,
                "full_scope": True,
                "non_legal": True,
                "latest_default": True,
                "reasons": [],
            }

        assert cmd[cmd.index("-C") + 1] == str(repo.resolve())
        assert cmd[cmd.index("--enable") + 1] == "web_search"
        _assert_prompt_preserves_named_thing_specificity(cmd[-1])
        return {
            "confident": True,
            "proposed_rfc": grounded,
            "assumptions": [],
            "questions": [],
            "grounding_notes": notes,
        }

    _install_codex_fake(monkeypatch, handler)
    _install_successful_approach_pipeline(monkeypatch)

    result = intake(request, repo)

    assert result["status"] == "promoted"
    assert result["id"] == "auto-battle-party-dungeon-rpg"
    assert result["branch"] == "ai-org/rfc/auto-battle-party-dungeon-rpg"
    assert result["grounding_notes"] == notes
    assert json.loads(_git(repo, "show", "ai-org/rfc/auto-battle-party-dungeon-rpg:rfc.json")) == grounded
    assert json.loads(
        _git(repo, "show", "ai-org/rfc/auto-battle-party-dungeon-rpg:technical-approach.json")
    ) == _approach_tree()
    assert result["technical_approach_path"] == "technical-approach.json"
    assert _git(repo, "rev-parse", "HEAD") == _git(repo, "rev-parse", "refs/heads/main")
    assert calls == ["grounding", "verifier"]


def test_intake_grounding_not_confident_promotes_by_default_with_uncertainty_preserved(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    monkeypatch.delenv("AI_ORG_REQUIRE_CONFIRMATION", raising=False)
    request = receive(
        {
            "raw_request": "Make it like that thing we discussed.",
        }
    )
    proposed_rfc = _rfc_view(
        "Conversation-Inferred Dungeon Automation Game",
        raw_request=request["raw_request"],
        problem_or_motivation="The requester likely wants the previously discussed automation game, but the exact reference is not fully recoverable from the request alone.",
        intended_users_or_jobs="Players who want a lightweight automated dungeon progression game.",
        desired_outcomes_success="The requester can confirm or correct a concrete dungeon automation interpretation.",
        affected_area_platform="game",
        background_facts="The available wording points at a dungeon automation loop.",
        grounding_provenance="Grounding inferred a likely game request from the available wording and repository game context.",
        proposal_hint="Build a small dungeon automation loop with party setup, automated runs, rewards, and progression.",
        alternatives_considered=["Wait for a named reference before shaping the RFC."],
    )
    assumptions = [
        "I assumed 'that thing we discussed' refers to a dungeon automation game because the repository context points at game work and the request asks for a rough game.",
        "I assumed the first RFC should cover core loop and progression rather than art polish because the problem does not name a visual style.",
    ]
    questions = ["Can you name the exact prior reference if this inferred game is wrong?"]
    notes = "The reference is ambiguous, but grounding inferred a likely RFC from repo context."
    calls = []

    monkeypatch.setattr(
        receive_module,
        "_ground_with_contract",
        lambda repo, rfc: GroundingResult(proposed_rfc, notes, False, assumptions, questions),
    )

    def fake_build_from_rfc(rfc_view, context=None, **kwargs):
        calls.append("build_from_rfc")
        assert rfc_view["open_questions"] == questions
        assert rfc_view["constraints_assumptions"] == assumptions
        assert "Grounding was not fully confident" in rfc_view["grounding_provenance"]
        assert assumptions[0] in rfc_view["grounding_provenance"]
        assert kwargs == {"kinds": ("design",)}
        return {"terms": {}, "processed_terms": ["dungeon automation"], "expanded": [], "hits": [], "failed": {}}

    def fake_start_background_build(rfc_view, context=None, **kwargs):
        calls.append("start_background_build")
        assert rfc_view["open_questions"] == questions
        assert kwargs == {"kinds": ("implementation",)}
        return object()

    def fake_form_technical_approach(rfc_view, repo_path, **kwargs):
        calls.append("form_technical_approach")
        assert rfc_view["open_questions"] == questions
        assert rfc_view["constraints_assumptions"] == assumptions
        assert kwargs["reference_terms"] == ["dungeon automation"]
        return {"ok": True, "technical_approach": _approach_tree()}

    monkeypatch.setattr(receive_module.reference, "build_from_rfc", fake_build_from_rfc)
    monkeypatch.setattr(receive_module.reference, "start_background_build", fake_start_background_build)
    monkeypatch.setattr(receive_module, "form_technical_approach", fake_form_technical_approach)

    result = intake(request, repo)

    assert result["status"] == "promoted"
    assert result["id"] == "conversation-inferred-dungeon-automation-game"
    assert result["grounding_notes"] == notes
    produced = json.loads(_git(repo, "show", "ai-org/rfc/conversation-inferred-dungeon-automation-game:rfc.json"))
    assert produced["open_questions"] == questions
    assert produced["constraints_assumptions"] == assumptions
    assert "Grounding was not fully confident" in produced["grounding_provenance"]
    assert assumptions[0] in produced["grounding_provenance"]
    assert calls == ["build_from_rfc", "start_background_build", "form_technical_approach"]
    assert _git(repo, "rev-parse", "HEAD") == _git(repo, "rev-parse", "refs/heads/main")


def test_intake_grounding_not_confident_requires_confirmation_when_toggle_on(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    monkeypatch.setenv("AI_ORG_REQUIRE_CONFIRMATION", "yes")
    request = receive(
        {
            "raw_request": "Make it like that thing we discussed.",
        }
    )
    proposed_rfc = _rfc_view(
        "Conversation-Inferred Dungeon Automation Game",
        raw_request=request["raw_request"],
        grounding_provenance="Grounding inferred a likely game request from the available wording and repository game context.",
    )
    assumptions = ["I assumed the request refers to the earlier dungeon automation game."]
    questions = ["Can you name the exact prior reference if this inferred game is wrong?"]
    notes = "The reference is ambiguous."

    monkeypatch.setattr(
        receive_module,
        "_ground_with_contract",
        lambda repo, rfc: GroundingResult(proposed_rfc, notes, False, assumptions, questions),
    )
    monkeypatch.setattr(
        receive_module.reference,
        "build_from_rfc",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("design build should not run")),
    )
    monkeypatch.setattr(
        receive_module.reference,
        "start_background_build",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("background build should not run")),
    )
    monkeypatch.setattr(
        receive_module,
        "form_technical_approach",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("approach should not be formed")),
    )

    result = intake(request, repo)

    assert result == {
        "status": "needs_confirmation",
        "proposed_rfc": proposed_rfc,
        "assumptions": assumptions,
        "questions": questions,
        "grounding_notes": notes,
    }
    assert "branch" not in result
    missing_branch = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--verify", "refs/heads/ai-org/rfc/conversation-inferred-dungeon-automation-game"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert missing_branch.returncode != 0
    assert _git(repo, "rev-parse", "HEAD") == _git(repo, "rev-parse", "refs/heads/main")


def test_produce_rfc_approach_failure_does_not_promote_hollow_rfc(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    request = receive(
        {
            "raw_request": "Manual Intake: commit the validated registry RFC as rfc.json.",
        }
    )
    grounded = _rfc_view("Manual Intake", raw_request=request["raw_request"])
    monkeypatch.setattr(receive_module, "_ground_with_contract", lambda repo, rfc: GroundingResult(grounded, "identity"))
    monkeypatch.setattr(
        receive_module.reference,
        "build_from_rfc",
        lambda *args, **kwargs: {"terms": {}, "processed_terms": ["battle loop"], "expanded": [], "hits": [], "failed": {}},
    )
    monkeypatch.setattr(receive_module.reference, "start_background_build", lambda *args, **kwargs: object())
    monkeypatch.setattr(
        receive_module,
        "form_technical_approach",
        lambda *args, **kwargs: {
            "ok": False,
            "error": "Could not select a coherent approach.",
            "failed_step": "select_approach",
        },
    )
    monkeypatch.setattr(
        receive_module,
        "_write_rfc_branch",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("hollow RFC should not be written")),
    )

    result = produce_rfc(request, repo)

    assert result == {
        "ok": False,
        "status": "needs_work",
        "error": "Could not select a coherent approach.",
        "failed_step": "select_approach",
        "proposed_rfc": grounded,
        "grounding_notes": "identity",
    }
    missing_branch = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--verify", "refs/heads/ai-org/rfc/manual-intake"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert missing_branch.returncode != 0
    assert _git(repo, "rev-parse", "HEAD") == _git(repo, "rev-parse", "refs/heads/main")


def test_grounding_contract_violations_reground_then_fail_closed(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    monkeypatch.setenv("AI_ORG_REQUIRE_CONFIRMATION", "true")
    request = receive(
        {
            "raw_request": "Make Dragon Quest. Build Dragon Quest.",
        }
    )
    bad_grounding = _rfc_view(
        "Generic Dragon Quest-Style Retro RPG Demo",
        raw_request=request["raw_request"],
        problem_or_motivation="Build a generic RPG inspired by the 1986 Famicom Dragon Quest instead of the current Dragon Quest experience.",
        intended_users_or_jobs="Players seeking a generic classic RPG.",
        desired_outcomes_success="A one town MVP prototype with a 10-minute vertical slice and short demo scope.",
        affected_area_platform="game",
        background_facts="This shrinks the named request into a dated demo.",
        grounding_provenance="Grounding chose retro Famicom constraints without a retro request.",
        alternatives_considered=["Avoid trademark, copyright, IP, legal, licensing, and material usage risk."],
    )
    grounding_prompts = []
    verifier_calls = 0

    def handler(cmd):
        nonlocal verifier_calls
        kind = _schema_kind(cmd[cmd.index("--output-schema") + 1])
        if kind == "verifier":
            verifier_calls += 1
            return {
                "faithful_specific": False,
                "full_scope": False,
                "non_legal": False,
                "latest_default": False,
                "reasons": ["Generalized, shrank scope, centered legal risk, and targeted a dated version."],
            }

        grounding_prompts.append(cmd[-1])
        return {
            "confident": True,
            "proposed_rfc": bad_grounding,
            "assumptions": [],
            "questions": [],
            "grounding_notes": "Grounded as a generic retro Famicom prototype with trademark, copyright, IP, legal, licensing, and material usage concerns.",
        }

    _install_codex_fake(monkeypatch, handler)

    result = intake(request, repo)

    assert result["ok"] is False
    assert result["status"] == "needs_work"
    assert result["failed_step"] == "grounding"
    assert result["proposed_rfc"] == bad_grounding
    assert "violations" in result
    assert any("C1 faithfulness/specificity" in violation for violation in result["violations"])
    assert any("C2 full scope" in violation for violation in result["violations"])
    assert any("C3 non-legal" in violation for violation in result["violations"])
    assert any("C4 latest-default" in violation for violation in result["violations"])
    assert len(grounding_prompts) == 3
    assert verifier_calls == 3
    assert "Your previous grounding violated" in grounding_prompts[1]
    assert "branch" not in result


def test_marker_lints_ignore_retro_exclusions_in_context_fields(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    request = receive_module._entrance_request(receive({"raw_request": "Make Dragon Quest as the current experience."}))
    grounded = _rfc_view(
        "Dragon Quest Current Experience",
        raw_request=request["raw_request"],
        desired_outcomes_success="Deliver the current Dragon Quest experience.",
        background_facts="The 1986 first-game release is historical context only.",
        grounding_provenance="Test fixture grounding.",
        alternatives_considered=["The original 1986 Dragon Quest was rejected."],
    )
    grounded["non_goals_out_of_scope"] = [
        "A retro-only Dragon Warrior or 1986 first-game target unless the requester asks for that older version."
    ]
    grounded["constraints_assumptions"] = ["I assumed the current experience, not classic or retro scope."]
    codex_calls: list[str] = []

    def handler(cmd):
        kind = _schema_kind(cmd[cmd.index("--output-schema") + 1])
        codex_calls.append(kind)
        if kind == "verifier":
            return {
                "faithful_specific": True,
                "full_scope": True,
                "non_legal": True,
                "latest_default": True,
                "reasons": [],
            }
        return {
            "confident": True,
            "proposed_rfc": grounded,
            "assumptions": [],
            "questions": [],
            "grounding_notes": "Grounded as the current experience.",
        }

    _install_codex_fake(monkeypatch, handler)

    result = receive_module._ground_with_contract(repo, request)

    assert result.confident is True
    assert result.violations == []
    assert codex_calls == ["grounding", "verifier"]


def test_latest_default_has_no_deterministic_marker_lint_for_classic_language_or_year():
    request = receive_module._entrance_request(receive({"raw_request": "Make the current named game experience."}))
    grounded = _rfc_view(
        "Current Named Game",
        raw_request=request["raw_request"],
        problem_or_motivation="The franchise began in 1986 and still has a classic command battle feel.",
        desired_outcomes_success="Deliver the current game with its classic command battle feel.",
    )

    violations = _grounding_lint_violations(request, grounded)

    assert not any("C4 latest-default" in violation for violation in violations)


def test_latest_default_c4_follows_verifier_verdict(monkeypatch):
    request = receive_module._entrance_request(receive({"raw_request": "Make the current named game experience."}))
    grounded = _rfc_view(
        "Current Named Game",
        raw_request=request["raw_request"],
        desired_outcomes_success="Deliver the current game with its classic command battle feel.",
    )

    failed = _verify_with_latest_default(monkeypatch, request, grounded, latest_default=False)
    assert failed["ok"] is False
    assert any("C4 latest-default: verifier marked latest_default=false" in violation for violation in failed["violations"])

    passed = _verify_with_latest_default(monkeypatch, request, grounded, latest_default=True)
    assert passed == {"ok": True, "violations": []}


def test_latest_default_retro_request_exemption_is_semantic_verifier_only(monkeypatch):
    request = receive_module._entrance_request(
        receive({"raw_request": "Make the named game as a specific past version."})
    )
    grounded = _rfc_view(
        "Past Version Named Game",
        raw_request=request["raw_request"],
        desired_outcomes_success="Target the named game's classic past-version conventions.",
    )

    assert not any("C4 latest-default" in violation for violation in _grounding_lint_violations(request, grounded))
    passed = _verify_with_latest_default(monkeypatch, request, grounded, latest_default=True)
    assert passed == {"ok": True, "violations": []}


def test_scope_shrink_markers_are_field_scoped_to_target_fields():
    request = receive_module._entrance_request(receive({"raw_request": "Make the full request."}))
    context_only = _rfc_view("Full Request")
    context_only["non_goals_out_of_scope"] = ["Not an MVP or short demo."]

    assert not any("C2 full scope" in violation for violation in _grounding_lint_violations(request, context_only))

    target = _rfc_view("Full Request", desired_outcomes_success="Ship an MVP vertical slice.")
    assert any("C2 full scope" in violation for violation in _grounding_lint_violations(request, target))


def test_generalizer_markers_are_field_scoped_to_target_fields():
    request = receive_module._entrance_request(receive({"raw_request": "Make the named product."}))
    context_only = _rfc_view("Named Product")
    context_only["alternatives_considered"] = ["A generic broad category substitute was rejected."]

    assert not any("C1 faithfulness/specificity" in violation for violation in _grounding_lint_violations(request, context_only))

    target = _rfc_view("Named Product", desired_outcomes_success="Build a generic broad category experience.")
    assert any("C1 faithfulness/specificity" in violation for violation in _grounding_lint_violations(request, target))


def test_registry_lint_scope_assignments_cover_every_field():
    expected_target = {
        "working_title",
        "problem_or_motivation",
        "intended_users_or_jobs",
        "desired_outcomes_success",
        "affected_area_platform",
        "tech_stack",
        "user_experience_requirements",
    }
    expected_context = {
        "raw_request",
        "request_type",
        "background_facts",
        "constraints_assumptions",
        "references",
        "grounding_provenance",
        "open_questions",
        "non_goals_out_of_scope",
        "proposal_hint",
        "alternatives_considered",
    }

    scopes = {entry.name: entry.lint_scope for entry in FIELD_REGISTRY}

    assert all(scope in LINT_SCOPES for scope in scopes.values())
    assert {name for name, scope in scopes.items() if scope == "target"} == expected_target
    assert {name for name, scope in scopes.items() if scope == "context"} == expected_context
    assert set(scopes) == expected_target | expected_context


def test_grounding_ai_deliberated_provenance_regrounds_and_fails_closed(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    request = receive({"raw_request": "Make Dragon Quest."})
    bad_grounding = _rfc_view(
        "Dragon Quest",
        raw_request=request["raw_request"],
        background_facts="Modern mainline Dragon Quest uses Unreal Engine as domain evidence.",
    )
    bad_grounding["tech_stack"] = {
        "build_strategy": "engine_based",
        "engine": "Unreal Engine 5",
        "framework": "",
        "language": "C++",
        "platform": "desktop",
        "rationale": "Modern Dragon Quest uses Unreal Engine.",
        "provenance": "ai_deliberated",
    }
    grounding_prompts = []

    def handler(cmd):
        kind = _schema_kind(cmd[cmd.index("--output-schema") + 1])
        if kind == "verifier":
            return {
                "faithful_specific": True,
                "full_scope": True,
                "non_legal": True,
                "latest_default": True,
                "reasons": [],
            }
        grounding_prompts.append(cmd[-1])
        return {
            "confident": True,
            "proposed_rfc": bad_grounding,
            "assumptions": [],
            "questions": [],
            "grounding_notes": "Grounding incorrectly deliberated Unreal from franchise precedent.",
        }

    _install_codex_fake(monkeypatch, handler)

    result = intake(request, repo)

    assert result["ok"] is False
    assert result["failed_step"] == "grounding"
    assert any("grounding may not set provenance=ai_deliberated" in item for item in result["violations"])
    assert len(grounding_prompts) == 3
    assert "Your previous grounding violated" in grounding_prompts[1]


def test_grounding_empty_user_facing_ux_regrounds_and_fails_closed(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    request = receive({"raw_request": "Make Dragon Quest."})
    bad_grounding = _rfc_view("Dragon Quest", raw_request=request["raw_request"])
    bad_grounding["user_experience_requirements"] = {
        **_ux_requirements(),
        "experience_identity": {field: "" for field in _ux_requirements()["experience_identity"]},
    }

    def handler(cmd):
        kind = _schema_kind(cmd[cmd.index("--output-schema") + 1])
        if kind == "verifier":
            return {
                "faithful_specific": True,
                "full_scope": True,
                "non_legal": True,
                "latest_default": True,
                "reasons": [],
            }
        return {
            "confident": True,
            "proposed_rfc": bad_grounding,
            "assumptions": [],
            "questions": [],
            "grounding_notes": "Grounding left user-facing UX blank.",
        }

    _install_codex_fake(monkeypatch, handler)

    result = intake(request, repo)

    assert result["ok"] is False
    assert result["failed_step"] == "grounding"
    assert any("user-experience completeness" in violation for violation in result["violations"])


def test_grounding_requester_specified_requires_original_stack_name(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    request = receive({"raw_request": "Make Dragon Quest."})
    forged_grounding = _rfc_view("Dragon Quest", raw_request=request["raw_request"])
    forged_grounding["tech_stack"] = {
        "build_strategy": "engine_based",
        "engine": "Unreal Engine 5",
        "framework": "",
        "language": "C++",
        "platform": "desktop",
        "rationale": "Grounding forged requester stack provenance.",
        "provenance": "requester_specified",
    }

    def handler(cmd):
        kind = _schema_kind(cmd[cmd.index("--output-schema") + 1])
        if kind == "verifier":
            return {
                "faithful_specific": True,
                "full_scope": True,
                "non_legal": True,
                "latest_default": True,
                "reasons": [],
            }
        return {
            "confident": True,
            "proposed_rfc": forged_grounding,
            "assumptions": [],
            "questions": [],
            "grounding_notes": "Grounding claimed the requester specified Unreal.",
        }

    _install_codex_fake(monkeypatch, handler)

    result = intake(request, repo)

    assert result["ok"] is False
    assert any("did not name that stack" in item for item in result["violations"])


def test_grounding_accepts_requester_named_stack(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    request = receive({"raw_request": "Make Dragon Quest in Unreal Engine 5."})
    grounded = _rfc_view("Dragon Quest", raw_request=request["raw_request"])
    grounded["tech_stack"] = {
        "build_strategy": "engine_based",
        "engine": "Unreal Engine 5",
        "framework": "",
        "language": "C++",
        "platform": "desktop",
        "rationale": "The requester explicitly named Unreal Engine 5.",
        "provenance": "requester_specified",
    }

    def handler(cmd):
        kind = _schema_kind(cmd[cmd.index("--output-schema") + 1])
        if kind == "verifier":
            return {
                "faithful_specific": True,
                "full_scope": True,
                "non_legal": True,
                "latest_default": True,
                "reasons": [],
            }
        return {
            "confident": True,
            "proposed_rfc": grounded,
            "assumptions": [],
            "questions": [],
            "grounding_notes": "Grounding preserved the requester-specified Unreal stack.",
        }

    _install_codex_fake(monkeypatch, handler)
    _install_successful_approach_pipeline(monkeypatch)

    result = intake(request, repo)

    assert result["status"] == "promoted"
    produced = json.loads(_git(repo, "show", "ai-org/rfc/dragon-quest:rfc.json"))
    assert produced["tech_stack"]["provenance"] == "requester_specified"


def test_grounding_empty_working_title_gets_deterministic_fallback_before_promotion(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    request = receive(
        {
            "raw_request": "Add a dashboard for RFC intake health.",
        }
    )
    incomplete = _rfc_view(
        "",
        raw_request=request["raw_request"],
        problem_or_motivation="RFC intake health dashboard is needed.",
        desired_outcomes_success="A dashboard summarizes intake health.",
    )
    grounding_calls = 0
    grounding_prompts = []

    def handler(cmd):
        nonlocal grounding_calls
        kind = _schema_kind(cmd[cmd.index("--output-schema") + 1])
        if kind == "verifier":
            return {
                "faithful_specific": True,
                "full_scope": True,
                "non_legal": True,
                "latest_default": True,
                "reasons": [],
            }

        grounding_calls += 1
        grounding_prompts.append(cmd[-1])
        return {
            "confident": True,
            "proposed_rfc": incomplete,
            "assumptions": [],
            "questions": [],
            "grounding_notes": "Grounded RFC intake health dashboard.",
        }

    _install_codex_fake(monkeypatch, handler)
    _install_successful_approach_pipeline(monkeypatch)

    result = intake(request, repo)

    assert result["status"] == "promoted"
    assert result["id"] == "rfc-intake-health-dashboard"
    assert grounding_calls == 1
    assert "working_title" in grounding_prompts[0]
    produced = json.loads(_git(repo, "show", "ai-org/rfc/rfc-intake-health-dashboard:rfc.json"))
    assert produced == {**incomplete, "working_title": "RFC Intake Health Dashboard"}


def test_grounding_other_empty_required_field_regrounds_and_fails_closed(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    request = receive(
        {
            "raw_request": "Add a dashboard for RFC intake health.",
        }
    )
    incomplete = _rfc_view(
        "RFC Intake Health Dashboard",
        raw_request=request["raw_request"],
        problem_or_motivation=" ",
    )
    grounding_calls = 0

    def handler(cmd):
        nonlocal grounding_calls
        kind = _schema_kind(cmd[cmd.index("--output-schema") + 1])
        if kind == "verifier":
            return {
                "faithful_specific": True,
                "full_scope": True,
                "non_legal": True,
                "latest_default": True,
                "reasons": [],
            }

        grounding_calls += 1
        return {
            "confident": True,
            "proposed_rfc": incomplete,
            "assumptions": [],
            "questions": [],
            "grounding_notes": "Grounded RFC intake health dashboard.",
        }

    _install_codex_fake(monkeypatch, handler)
    _install_successful_approach_pipeline(monkeypatch)

    result = intake(request, repo)

    assert result["ok"] is False
    assert result["status"] == "needs_work"
    assert result["failed_step"] == "grounding"
    assert grounding_calls == 3
    assert "branch" not in result
    assert any("C0 required-field completeness lint" in violation for violation in result["violations"])
    assert any("problem_or_motivation" in violation for violation in result["violations"])


def test_grounding_and_verifier_schemas_are_codex_valid_registry():
    for schema in (GROUNDING_SCHEMA, GROUNDING_VERDICT_SCHEMA):
        serialized = json.dumps(schema)
        assert "allOf" not in serialized
        assert "anyOf" not in serialized
        assert "oneOf" not in serialized
        assert _schema_key_paths(schema, {"minLength", "pattern", "format"}) == []
        assert schema["additionalProperties"] is False
        assert sorted(schema["required"]) == sorted(schema["properties"])

    schema_rfc = GROUNDING_SCHEMA["properties"]["proposed_rfc"]
    assert schema_rfc["additionalProperties"] is False
    assert tuple(schema_rfc["required"]) == COMMON_8_FIELDS
    assert sorted(schema_rfc["required"]) == sorted(schema_rfc["properties"])
    assert schema_rfc["properties"]["tech_stack"]["required"] == list(receive_module.TECH_STACK_FIELDS)
    assert schema_rfc["properties"]["user_experience_requirements"]["required"] == list(
        receive_module.USER_EXPERIENCE_REQUIREMENTS_FIELDS
    )
    # The registry semantics reach the schema as a STRING description (codex/OpenAI Structured
    # Outputs reject a non-string `description` with HTTP 400 "... is not of type 'string'").
    # The structured dict form lives only in the prompt (REQUEST_SCHEMA/_field_registry_prompt).
    provenance_desc = schema_rfc["properties"]["grounding_provenance"]["description"]
    assert isinstance(provenance_desc, str)
    assert "must_not=content consumed downstream as product requirement nouns" in provenance_desc


def test_reform_rfc_reruns_only_anchored_step_and_writes_v2_response(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    rfc_view = _rfc_view("Reviewable RFC")
    approach = _reform_approach_tree("old slice")
    branch = "ai-org/rfc/reviewable-rfc"
    _git(repo, "checkout", "-B", branch, "main")
    (repo / "rfc.json").write_text(json.dumps(rfc_view) + "\n", encoding="utf-8")
    (repo / "technical-approach.json").write_text(json.dumps(approach) + "\n", encoding="utf-8")
    _git(repo, "add", "rfc.json", "technical-approach.json")
    _git(repo, "commit", "-m", "initial rfc")
    _git(repo, "commit", "--allow-empty", "-m", "rfc: needs-revision round 1")
    _git(repo, "checkout", "main")
    _write_review_round(repo, "reviewable-rfc", _blocking_objection("patch_plan:candidate:one"))
    calls: list[str] = []

    def unexpected(*_args, **_kwargs):
        raise AssertionError("unanchored step should not rerun")

    monkeypatch.setattr(receive_module, "_normalize_problem", unexpected)
    monkeypatch.setattr(receive_module, "_extract_constraints", unexpected)
    monkeypatch.setattr(receive_module, "_build_prior_art_map", unexpected)
    monkeypatch.setattr(receive_module, "_generate_candidates", unexpected)
    monkeypatch.setattr(receive_module, "_select_approach", unexpected)
    monkeypatch.setattr(receive_module, "_implementation_strategy", unexpected)
    monkeypatch.setattr(receive_module, "_surface_risks", unexpected)

    def revised_patch_plan(*_args, **_kwargs):
        calls.append("patch_plan")
        return {"first_safe_slice": "new slice", "follow_ups": [], "deferred_work": []}

    monkeypatch.setattr(receive_module, "_right_size_patch_plan", revised_patch_plan)

    result = receive_module.reform_rfc(repo, "reviewable-rfc")

    assert result["status"] == "reformed"
    assert calls == ["patch_plan"]
    assert result["branch"] == branch
    latest_message = _git(repo, "log", "-1", "--pretty=%B", branch)
    assert "rfc v2: Reviewable RFC" in latest_message
    assert "Changes since v1:" in latest_message
    assert "approach:1" in latest_message
    revised = json.loads(_git(repo, "show", f"{branch}:technical-approach.json"))
    assert revised["problem"]["constraints"] == approach["problem"]["constraints"]
    assert revised["problem"]["question"]["decision"]["implementation"]["patch_plan"]["first_safe_slice"] == "new slice"
    response = json.loads((repo / ".ai-org/review/reviewable-rfc/round-1-author-v2.json").read_text(encoding="utf-8"))
    assert response["objections"][0]["status"] == "answered"
    original = json.loads((repo / ".ai-org/review/reviewable-rfc/round-1.json").read_text(encoding="utf-8"))
    assert original["objections"][0]["status"] == "open"


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "base")
    _git(repo, "branch", "-M", "main")
    return repo


def _write_review_round(repo: Path, rfc_id: str, objection: dict[str, object], *, round_number: int = 1) -> None:
    directory = repo / ".ai-org" / "review" / rfc_id
    directory.mkdir(parents=True, exist_ok=True)
    record = {
        "rfc_id": rfc_id,
        "branch": f"ai-org/rfc/{rfc_id}",
        "round": round_number,
        "objections": [objection],
        "verdict": "needs_revision",
    }
    (directory / f"round-{round_number}.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")


def _blocking_objection(anchor: str) -> dict[str, object]:
    return {
        "objection_id": "approach:1",
        "anchor_node_ids": [anchor],
        "axis": "approach",
        "type": "blocking",
        "claim": "The patch plan is not reviewable.",
        "evidence": [{"source_type": "tree_node", "citation": anchor, "consulted_terms": []}],
        "impact": "The author cannot repost a useful v2.",
        "requested_author_action": "revise_subtree",
        "status": "open",
    }


def _reform_approach_tree(first_slice: str) -> dict[str, object]:
    return {
        "problem": {
            "id": "problem",
            "problem": "Review needs a concrete patch plan.",
            "affected": "RFC authors.",
            "current_inadequacy": "The plan is vague.",
            "goals": [{"id": "goal:1", "criterion": "A reviewer can inspect the first slice."}],
            "non_goals": [],
            "constraints": {"hard": [{"id": "constraint:hard:1", "text": "Same branch v2."}], "soft": []},
            "prior_art": [{"id": "prior_art:lkml", "name": "LKML v2"}],
            "question": {
                "id": "question:approach",
                "candidates": [
                    {
                        "id": "candidate:one",
                        "summary": "Use the author reformation loop.",
                        "evaluation": {"id": "evaluation:candidate:one", "candidate_id": "candidate:one", "scores": {}},
                    }
                ],
                "decision": {
                    "id": "decision:candidate:one",
                    "selected_candidate_id": "candidate:one",
                    "arguments": [],
                    "rationale": {"because": ["It fits."], "under_constraints": [], "accepting_tradeoffs": []},
                    "stack_axes": {},
                    "rejected": [],
                    "implementation": {
                        "id": "implementation:candidate:one",
                        "main_changes": ["Update RFC files."],
                        "domain_specification": {"id": "domain_specification", "aspects": []},
                        "patch_plan": {"id": "patch_plan:candidate:one", "first_safe_slice": first_slice},
                        "risks": [],
                    },
                    "risks": [],
                },
            },
            "open_questions": [],
        },
        "cross_links": [],
    }


def _rfc_view(
    working_title: str,
    *,
    raw_request: str | None = None,
    problem_or_motivation: str = "Requests need a grounded entrance form.",
    intended_users_or_jobs: str = "Contributors opening a request.",
    desired_outcomes_success: str = "RFC formation starts from grounded registry data.",
    affected_area_platform: str = "ai_org.rfc",
    background_facts: str = "The request targets the RFC receive flow.",
    grounding_provenance: str = "Test fixture grounding.",
    proposal_hint: str = "Commit the validated registry RFC as rfc.json.",
    alternatives_considered: list[str] | None = None,
) -> dict[str, object]:
    return {
        "raw_request": raw_request or working_title,
        "working_title": working_title,
        "request_type": "feature",
        "problem_or_motivation": problem_or_motivation,
        "intended_users_or_jobs": intended_users_or_jobs,
        "desired_outcomes_success": desired_outcomes_success,
        "affected_area_platform": affected_area_platform,
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
        "background_facts": background_facts,
        "constraints_assumptions": [],
        "references": [],
        "grounding_provenance": grounding_provenance,
        "open_questions": [],
        "non_goals_out_of_scope": [],
        "proposal_hint": proposal_hint,
        "alternatives_considered": alternatives_considered or [],
    }


def _ux_requirements() -> dict[str, object]:
    return {
        "applicability": {"applicability": "user_facing", "not_user_facing_reason": ""},
        "experience_identity": {
            "named_reference": "Dragon Quest readable status windows and command menus.",
            "genre_conventions": "Classic JRPG exploration, dialog, battle, and inventory readability.",
            "must_resemble": "Status surfaces, NPC clues, doors, stairs, chests, and talk/search verbs.",
            "must_not_resemble": "An invisible mechanics-only simulation.",
        },
        "presentation_model": {
            "camera_and_view": "Top-down readable map with clear entrances and interactables.",
            "world_readability": "NPCs, exits, gates, hazards, bosses, and objectives are visibly represented.",
            "ui_taxonomy_notes": "Non-diegetic status windows plus spatial world markers.",
        },
        "core_status_surfaces": {
            "player_status": "LV, HP, MP, equipment state, and low-HP warning are visible.",
            "opposition_status": "Enemy name, threat, damage, and defeat state are visible.",
            "inventory_resources": "Gold, items, keys, and spell resources are visible in menus.",
            "objective_progress": "Current goal and completion flags are visible to the player.",
            "location_identity": "Town, dungeon, and room identity are readable on screen.",
        },
        "entity_affordances": {
            "interactive_entities": "NPCs and objects show talk/search/open affordances.",
            "exits_and_transitions": "Doors, stairs, and map exits are visibly distinct.",
            "gates_and_locks": "Locked and unlocked states have persistent visible evidence.",
            "hazards_and_bosses": "Hazards and bosses have distinct silhouettes and warnings.",
            "collectibles": "Chests and pickups visibly change after collection.",
            "decorative_elements": "Decorations never masquerade as interactive state.",
        },
        "action_feedback_matrix": [
            {"action_verb": "talk", "feedback_requirement": "Dialog window opens with player-paced text."},
            {"action_verb": "open", "feedback_requirement": "Door or chest visibly changes state."},
        ],
        "progression_legibility": {
            "current_goal_visibility": "The current goal is visible in HUD or menu.",
            "locked_state_feedback": "Locked gates explain the missing key or flag.",
            "unlocked_state_feedback": "Unlocked gates stay visibly open.",
            "flag_observability": "Quest flags leave persistent visible evidence.",
            "ending_state_consistency": "Ending and victory states match the visible world state.",
        },
        "hud_and_ui_flow": {
            "primary_hud": "HP, MP, and gold are readable without opening deep menus.",
            "secondary_screens": "Inventory, equipment, and status screens are readable.",
            "menu_flow": "Command menus expose fight, spell, item, talk, and search where applicable.",
            "dialog_flow": "Dialog is player-paced and identifies speaker or source.",
            "failure_and_recovery": "Defeat and retry states explain what happened and how to continue.",
        },
        "visual_language_constraints": {
            "contrast": "Text, status windows, and interactive markers meet readable contrast.",
            "palette_role": "Palette communicates state roles without being the only channel.",
            "silhouette_readability": "NPC, exit, gate, hazard, boss, and item silhouettes are distinct.",
            "labels_and_markers": "Labels and markers identify consequential entities and objectives.",
            "animation_minimums": "Core actions and state changes have visible animation or text feedback.",
        },
        "accessibility_baseline": {
            "controls": "Controls are simple or remappable and avoid mandatory chords.",
            "text_readability": "Text is large enough to read and never overlaps controls.",
            "color_independence": "State is not communicated by color alone.",
            "audio_independence": "Audio cues have visual or textual equivalents.",
            "pacing": "Dialog and progression prompts are player-paced.",
        },
        "acceptance_tests": {
            "screenshot_checks": [
                "When the player enters town, visible NPC, exit, objective, HP, MP, and gold surfaces appear."
            ],
            "interaction_checks": [
                "When the player opens a locked gate, visible locked-state feedback appears and persists."
            ],
            "playtest_checks": [
                "When a player follows NPC clues, observable objective feedback appears without hidden state."
            ],
        },
    }


def _grounding_lint_violations(request: dict[str, object], rfc_view: dict[str, object]) -> list[str]:
    return receive_module._lint_grounding(request, rfc_view, GroundingResult(rfc_view, ""))


def _verify_with_latest_default(
    monkeypatch: pytest.MonkeyPatch,
    request: dict[str, object],
    rfc_view: dict[str, object],
    *,
    latest_default: bool,
) -> dict[str, object]:
    def handler(cmd):
        assert _schema_kind(cmd[cmd.index("--output-schema") + 1]) == "verifier"
        return {
            "faithful_specific": True,
            "full_scope": True,
            "non_legal": True,
            "latest_default": latest_default,
            "reasons": [] if latest_default else ["The grounded RFC targets an outdated version without requester intent."],
        }

    _install_codex_fake(monkeypatch, handler)
    return receive_module._verify_grounding(request, rfc_view, GroundingResult(rfc_view, "Test verifier notes."))


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return result.stdout.strip()


def _install_codex_fake(monkeypatch: pytest.MonkeyPatch, handler) -> None:
    real_run = receive_module.subprocess.run

    def fake_run(cmd, *args, **kwargs):
        if cmd and cmd[0] == "codex":
            out_file = Path(cmd[cmd.index("-o") + 1])
            payload = handler(cmd)
            out_file.write_text(json.dumps(payload), encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(receive_module.subprocess, "run", fake_run)


def _install_successful_approach_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        receive_module.reference,
        "build_from_rfc",
        lambda *args, **kwargs: {"terms": {}, "processed_terms": ["battle loop"], "expanded": [], "hits": [], "failed": {}},
    )
    monkeypatch.setattr(receive_module.reference, "start_background_build", lambda *args, **kwargs: object())
    monkeypatch.setattr(
        receive_module,
        "form_technical_approach",
        lambda *args, **kwargs: {"ok": True, "technical_approach": _approach_tree()},
    )


def _approach_tree() -> dict[str, object]:
    return {
        "problem": {
            "summary": "Requests need a grounded implementation approach before review.",
            "question": {
                "decision": {
                    "chosen": "Build the receive-stage approach artifact.",
                    "implementation": {
                        "plan": ["Write technical-approach.json next to rfc.json."],
                    },
                }
            },
        }
    }


def _schema_kind(output_schema: str | Path) -> str:
    schema = json.loads(Path(output_schema).read_text(encoding="utf-8"))
    if schema == GROUNDING_SCHEMA:
        return "grounding"
    if schema == GROUNDING_VERDICT_SCHEMA:
        return "verifier"
    raise AssertionError(f"unexpected schema: {schema}")


def _schema_key_paths(value: object, forbidden: set[str], path: str = "$") -> list[str]:
    paths = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if key in forbidden:
                paths.append(child_path)
            paths.extend(_schema_key_paths(child, forbidden, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            paths.extend(_schema_key_paths(child, forbidden, f"{path}[{index}]"))
    return paths


def _assert_prompt_preserves_named_thing_specificity(prompt: str) -> None:
    assert "Faithfully render the request's specific identity" in prompt
    assert "concrete defining signatures" in prompt
    assert "ground down to that named thing" in prompt
    assert "never generalize up to a broad category" in prompt
    assert "faithfully reproduce <the specific named thing>" in prompt
    assert "generic genre entry" in prompt
    assert "Preserve the request's full scope" in prompt
    assert "vertical slice" in prompt
    assert "prototype, MVP, first iteration" in prompt
    assert "complete requested deliverable" in prompt
    assert "Grounding is not legal review" in prompt
    assert "Do not perform IP, trademark, copyright, or licensing risk analysis" in prompt
    assert "do not add legal disclaimers" in prompt
    assert "Do not avoid perceived IP risk by renaming, generalizing, or shrinking" in prompt
    assert "Default to the latest or current version" in prompt
    assert "unless the request explicitly asks for a retro, classic, old, vintage" in prompt
    assert "games should target the current experience, modern graphics, scope, and conventions" in prompt
    assert "required_at=rfc_handoff" in prompt
    assert "working_title" in prompt
    assert "short noun phrase naming the deliverable" in prompt
