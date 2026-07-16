from __future__ import annotations

from datetime import datetime, timedelta
import json
import os
import subprocess
import hashlib
from pathlib import Path
from types import SimpleNamespace

import jsonschema
import pytest

import ai_org.codex_reset as codex_reset
from ai_org import git_wrapper, mailing_list, patch_series_bodies, review_bodies
from ai_org.patch_author.code_worker import _is_common_8 as _implement_is_registry_patch_series
from ai_org.body_codec import BodyCodecClient
import ai_org.patchwork_queue.requester_assumptions as requester_assumptions
from ai_org.patchwork_queue import receive as receive_module
import ai_org.patchwork_queue.spine as spine_module
from ai_org.patchwork_queue.field_registry import FIELD_REGISTRY, LINT_SCOPES
from ai_org.patchwork_queue.receive import (
    COMMON_8_FIELDS,
    GROUNDING_SCHEMA,
    GROUNDING_VERDICT_SCHEMA,
    REQUEST_SCHEMA,
    GroundingResult,
    intake,
    produce_patch_series,
    receive,
)


def _install_deterministic_default_cutover_preparation(monkeypatch):
    """Isolate reform tests from the independent producer-preparation model call."""

    def prepare(transition, previous, repo_path, **kwargs):
        candidate = transition.get("technical_approach", transition)
        prepared = patch_series_bodies.prepare_root_technical_approach_preview(
            kwargs["cover_letter"],
            kwargs["request_provenance"],
            candidate,
        )
        return receive_module.CanonicalRootTechnicalApproachPreview(
            technical_approach=candidate,
            canonical_cue=prepared.canonical_cue.decode("utf-8"),
            body_sha256=prepared.body_sha256,
            cohort_files=prepared.files(),
        )

    monkeypatch.setattr(
        receive_module,
        "prepare_producer_aware_formation",
        lambda formation, repo_path, **kwargs: prepare(
            formation, None, repo_path, **kwargs
        ),
    )
    monkeypatch.setattr(
        receive_module,
        "prepare_producer_aware_reform",
        lambda reform, previous, repo_path, **kwargs: prepare(
            reform, previous, repo_path, **kwargs
        ),
    )


def _install_historical_reform_preparation(monkeypatch):
    """Keep legacy reform unit tests focused on their pre-cutover concern."""

    def prepare(transition, previous, repo_path, **kwargs):
        candidate = transition.get("technical_approach", transition)
        return SimpleNamespace(
            technical_approach=candidate,
            complete_cohort=True,
            cohort_files={
                patch_series_bodies.LEGACY_COVER_PATH: kwargs["cover_letter"],
                patch_series_bodies.LEGACY_ROOT_APPROACH_PATH: candidate,
            },
        )

    monkeypatch.setattr(
        receive_module,
        "prepare_producer_aware_formation",
        lambda formation, repo_path, **kwargs: prepare(
            formation, None, repo_path, **kwargs
        ),
    )
    monkeypatch.setattr(
        receive_module,
        "prepare_producer_aware_reform",
        lambda reform, previous, repo_path, **kwargs: prepare(
            reform, previous, repo_path, **kwargs
        ),
    )


@pytest.fixture(autouse=True)
def _deterministic_default_cutover_preparation(monkeypatch):
    """Keep receive tests at the prepublication seam, without a model call."""

    _install_deterministic_default_cutover_preparation(monkeypatch)


def _read_canonical_pending_member(repo, ref, path, context, contract):
    snapshot = patch_series_bodies.classify_root_generation(repo, ref)
    raw = snapshot.raw(path)
    assert raw is not None
    return BodyCodecClient().parse(context, raw, expected=contract)


def _read_canonical_pending_cover(repo, ref):
    return _read_canonical_pending_member(
        repo,
        ref,
        patch_series_bodies.COVER_PATH,
        patch_series_bodies.COVER_CONTEXT,
        patch_series_bodies.COVER_CONTRACT,
    )


def _read_canonical_pending_approach(repo, ref):
    return _read_canonical_pending_member(
        repo,
        ref,
        patch_series_bodies.ROOT_APPROACH_PATH,
        patch_series_bodies.ROOT_APPROACH_CONTEXT,
        patch_series_bodies.ROOT_APPROACH_CONTRACT,
    )


def test_receive_validates_raw_request_only_from_dict():
    request = {
        "raw_request": "Make Dragon Quest.",
    }

    assert receive(request) == request
    assert tuple(REQUEST_SCHEMA["recognized_fields"]) == COMMON_8_FIELDS
    assert REQUEST_SCHEMA["required"] == ["raw_request"]
    assert "grounding_provenance" in REQUEST_SCHEMA["field_registry"]


def test_grounding_retries_once_after_codex_usage_limit(tmp_path, monkeypatch):
    proposed = _patch_series_view("Usage Limit Recovery")
    payload = json.dumps(
        {
            "confident": True,
            "proposed_patch_series": proposed,
            "ascii_working_slug": "usage-limit-recovery",
            "assumptions": [],
            "questions": [],
            "grounding_notes": "Recovered after the usage-limit reset window.",
        }
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    counter = tmp_path / "codex-count"
    fake_codex = bin_dir / "codex"
    fake_codex.write_text(
        "#!/usr/bin/env python3\n"
        "from datetime import datetime\n"
        "import pathlib, sys\n"
        f"counter = pathlib.Path({str(counter)!r})\n"
        "calls = int(counter.read_text() or '0') if counter.exists() else 0\n"
        "counter.write_text(str(calls + 1))\n"
        "if calls == 0:\n"
        "    reset = datetime.now().astimezone().strftime('%I:%M %p').lstrip('0')\n"
        "    print(f\"You've hit your usage limit; try again at {reset}.\", file=sys.stderr)\n"
        "    raise SystemExit(1)\n"
        "out_file = pathlib.Path(sys.argv[sys.argv.index('-o') + 1])\n"
        f"out_file.write_text({payload!r})\n",
        encoding="utf-8",
    )
    fake_codex.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    wait_events: list[str] = []

    class AdvancingDateTime(datetime):
        current = datetime.now().astimezone()

        @classmethod
        def now(cls, tz=None):
            return cls.current if tz is None else cls.current.astimezone(tz)

    original_emit = codex_reset.org_log.emit

    def capture_emit(event_name, payload=None, **kwargs):
        if event_name == "patchwork_queue.codex_reset_wait":
            wait_events.append(event_name)
        return original_emit(event_name, payload, **kwargs)

    monkeypatch.setattr(codex_reset, "datetime", AdvancingDateTime)
    monkeypatch.setattr(codex_reset, "WAIT_INTERVAL_SECONDS", 120.0)
    monkeypatch.setattr(
        codex_reset.time,
        "sleep",
        lambda seconds: setattr(
            AdvancingDateTime,
            "current",
            AdvancingDateTime.current + timedelta(seconds=seconds),
        ),
    )
    monkeypatch.setattr(codex_reset.org_log, "emit", capture_emit)
    original = _patch_series_view("Usage Limit Request")
    ctx = codex_reset.org_log.RunContext(repo=tmp_path, run_id="receive-reset-retry")

    result = receive_module._ground_request(Path.cwd(), original, ctx=ctx)

    assert result.parse_failure == ""
    assert result.patch_series_view["working_title"] == "Usage Limit Recovery"
    assert counter.read_text(encoding="utf-8") == "2"
    assert wait_events == ["patchwork_queue.codex_reset_wait"]


def test_grounding_contract_preserves_exhausted_grounding_failure(monkeypatch):
    proposed = _patch_series_view("Transient Grounding Failure")
    calls = 0

    def fake_ground_request(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        reason = "Grounding failed [transient-retries-exhausted]"
        return GroundingResult(
            proposed,
            grounding_notes=reason,
            confident=False,
            failure_mode=codex_reset.TRANSIENT_RETRIES_EXHAUSTED,
            parse_failure=reason,
        )

    monkeypatch.setattr(receive_module, "_ground_request", fake_ground_request)

    result = receive_module._ground_with_contract(Path.cwd(), proposed)

    assert calls == 1
    assert result.failure_mode == codex_reset.TRANSIENT_RETRIES_EXHAUSTED
    assert result.parse_failure.endswith("[transient-retries-exhausted]")


def test_grounding_contract_preserves_exhausted_verifier_failure(monkeypatch):
    proposed = _patch_series_view("Transient Verifier Failure")
    grounding_calls = 0

    def fake_ground_request(*_args, **_kwargs):
        nonlocal grounding_calls
        grounding_calls += 1
        return GroundingResult(proposed, grounding_notes="Grounded candidate")

    monkeypatch.setattr(receive_module, "_ground_request", fake_ground_request)
    monkeypatch.setattr(
        receive_module,
        "_verify_grounding",
        lambda *_args, **_kwargs: {
            "ok": False,
            "violations": ["C0 verifier failed [transient-retries-exhausted]"],
            "failure_mode": codex_reset.TRANSIENT_RETRIES_EXHAUSTED,
        },
    )

    result = receive_module._ground_with_contract(Path.cwd(), proposed)

    assert grounding_calls == 1
    assert result.failure_mode == codex_reset.TRANSIENT_RETRIES_EXHAUSTED
    assert result.violations == ["C0 verifier failed [transient-retries-exhausted]"]


def test_receive_validates_request_from_json_file(tmp_path):
    path = tmp_path / "maintainer-series-request.json"
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


def test_patch_series_handoff_requires_full_registry_shape():
    complete = _patch_series_view("Complete Handoff")
    assert receive_module._is_patch_series_view(complete) is True

    missing = dict(complete)
    missing.pop("grounding_provenance")
    assert receive_module._is_patch_series_view(missing) is False


def test_tech_stack_structured_field_validates():
    patch_series = _patch_series_view("Structured Stack")
    assert receive_module._is_patch_series_view(patch_series) is True
    patch_series["tech_stack"] = {**patch_series["tech_stack"], "build_strategy": "invalid"}
    assert receive_module._is_patch_series_view(patch_series) is False


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


def _surface_pin_tech_stack() -> dict[str, str]:
    # The exact pomodoro-run shape (2026-07-05): requester pinned only the surface.
    return {
        "build_strategy": "",
        "engine": "",
        "framework": "",
        "language": "",
        "platform": "CLI/terminal",
        "rationale": "Requester asked for a CLI pomodoro timer.",
        "provenance": "requester_specified",
    }


def test_explain_tech_stack_violation_names_the_surface_pin_rule():
    message = receive_module.explain_tech_stack_violation(_surface_pin_tech_stack())
    assert "concrete build_strategy" in message
    assert "requester_specified" in message
    assert "affected_area_platform" in message
    assert "constraints_assumptions" in message


def test_validate_tech_stack_boolean_matches_explain_across_rule_matrix():
    unspecified = {
        "build_strategy": "",
        "engine": "",
        "framework": "",
        "language": "",
        "platform": "",
        "rationale": "",
        "provenance": "unspecified",
    }
    requester_framework = {
        "build_strategy": "framework_based",
        "engine": "",
        "framework": "React",
        "language": "TypeScript",
        "platform": "browser",
        "rationale": "Requester named React.",
        "provenance": "requester_specified",
    }
    cases = [
        unspecified,
        requester_framework,
        _surface_pin_tech_stack(),
        {**unspecified, "engine": "Unity"},
        {**unspecified, "provenance": "made_up"},
        {**requester_framework, "rationale": ""},
        {**requester_framework, "build_strategy": "from_scratch", "framework": "", "rationale": ""},
        {**requester_framework, "build_strategy": "engine_based", "engine": "browser standards"},
        {**requester_framework, "provenance": "ai_deliberated", "platform": "headless functional_check target"},
        {**requester_framework, "language": 7},
        {key: value for key, value in requester_framework.items() if key != "engine"},
        "not-a-dict",
        None,
    ]
    for case in cases:
        explanation = receive_module.explain_tech_stack_violation(case)
        assert receive_module.validate_tech_stack(case) == (explanation == ""), case


def test_grounding_candidate_view_violations_name_the_failing_component():
    valid = _patch_series_view("Pomodoro timer")
    assert receive_module._grounding_candidate_view_violations(valid) == []
    assert receive_module._is_grounding_candidate_view(valid)

    surface_pinned = {**valid, "tech_stack": _surface_pin_tech_stack()}
    violations = receive_module._grounding_candidate_view_violations(surface_pinned)
    assert len(violations) == 1
    assert violations[0].startswith("proposed_patch_series.tech_stack invalid: ")
    assert "concrete build_strategy" in violations[0]
    assert "C0" not in violations[0]
    assert not receive_module._is_grounding_candidate_view(surface_pinned)

    keyset = {key: value for key, value in valid.items() if key != "working_title"}
    keyset["bogus_field"] = "x"
    [keyset_message] = receive_module._grounding_candidate_view_violations(keyset)
    assert "missing fields: working_title" in keyset_message
    assert "unexpected fields: bogus_field" in keyset_message

    [string_message] = receive_module._grounding_candidate_view_violations({**valid, "working_title": 7})
    assert "working_title (got int)" in string_message

    [ux_message] = receive_module._grounding_candidate_view_violations(
        {**valid, "user_experience_requirements": {}}
    )
    assert "user_experience_requirements" in ux_message


def test_grounding_parse_failure_feeds_component_rule_not_scaffold_symptoms(tmp_path):
    # The full pomodoro chain: honest grounding output fails ONLY the tech_stack
    # rule -> the fail-closed result must carry the component+rule (no raw dump),
    # and _verify_grounding must feed back exactly that violation instead of the
    # fallback scaffold's C0 empty-string symptoms.
    original = _patch_series_view("Pomodoro timer")
    proposed = {**_patch_series_view("Pomodoro timer"), "tech_stack": _surface_pin_tech_stack()}
    raw = json.dumps(
        {
            "confident": True,
            "proposed_patch_series": proposed,
            "ascii_working_slug": "pomodoro-cli-timer",
            "assumptions": [],
            "grounding_notes": "Researched pomodoro conventions.",
            "questions": [],
        }
    )

    result = receive_module._parse_grounding_result(raw, original)

    assert result.confident is False
    assert result.parse_failure == result.grounding_notes
    assert "proposed_patch_series.tech_stack invalid" in result.parse_failure
    assert "concrete build_strategy" in result.parse_failure
    # The failure string is the component + rule, never the raw dump.
    assert "Researched pomodoro conventions." not in result.parse_failure

    verification = receive_module._verify_grounding(original, result.patch_series_view, result)
    assert verification == {"ok": False, "violations": [result.parse_failure]}

    # The retry prompt carries the component message verbatim.
    retry_prompt = receive_module._grounding_prompt(original, verification["violations"])
    assert result.parse_failure in retry_prompt


def test_grounding_prompt_states_surface_pin_provenance_semantics():
    prompt = receive_module._grounding_prompt(_patch_series_view("Pomodoro timer"), None)
    assert "requester_specified requires a concrete build_strategy" in prompt
    assert "A surface-only pin such as CLI, terminal, browser, or mobile is not a stack choice" in prompt
    assert "record that surface in affected_area_platform and constraints_assumptions" in prompt
    # The old contradictory example ("or a concrete platform" as requester_specified) is gone.
    assert "or a concrete platform" not in prompt


def test_user_experience_requirements_validator_enforces_applicability_completeness():
    user_facing = _ux_requirements()
    assert receive_module.validate_user_experience_requirements(user_facing)
    user_facing["applicability"]["not_user_facing_reason"] = "Not applicable: the request is to make a playable game."
    assert receive_module.validate_user_experience_requirements(user_facing)
    assert user_facing["applicability"]["not_user_facing_reason"] == ""

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


def test_not_user_facing_ux_boilerplate_is_accepted_and_normalized_empty():
    ux = _ux_requirements()
    ux["applicability"] = {
        "applicability": "not_user_facing",
        "not_user_facing_reason": "This patch series only changes internal patch series assembly and produces no human UI.",
    }

    assert receive_module.validate_user_experience_requirements(ux)
    assert ux["applicability"]["not_user_facing_reason"] == (
        "This patch series only changes internal patch series assembly and produces no human UI."
    )
    for section in (
        "experience_identity",
        "presentation_model",
        "core_status_surfaces",
        "entity_affordances",
        "progression_legibility",
        "hud_and_ui_flow",
        "visual_language_constraints",
        "accessibility_baseline",
    ):
        assert set(ux[section].values()) == {""}
    assert ux["action_feedback_matrix"] == []
    assert ux["acceptance_tests"] == {
        "screenshot_checks": [],
        "interaction_checks": [],
        "playtest_checks": [],
    }


def test_receive_no_longer_contains_terminal_open_questions_reset():
    source = Path(receive_module.__file__).read_text(encoding="utf-8")

    assert 'problem["open_questions"] = []' not in source


def test_receive_preserves_extra_keys():
    assert receive(
        {
            "title": "Extra data",
            "problem": "Unknown keys should not be rejected.",
            "custom_priority": "high",
        }
    )["custom_priority"] == "high"


def test_produce_patch_series_publishes_default_canonical_root_cohort(
    tmp_path, monkeypatch
):
    repo = _init_repo(tmp_path)
    monkeypatch.setenv("AI_ORG_REQUIRE_CONFIRMATION", "true")
    request = receive(
        {
            "raw_request": "Manual Intake: commit the validated registry patch series as patch-series-cover-letter.json.",
            "custom_priority": "high",
        }
    )
    grounded = _patch_series_view(
        "Manual Intake",
        raw_request=request["raw_request"],
        proposal_hint="Commit the validated registry patch series as patch-series-cover-letter.json.",
    )

    monkeypatch.setattr(receive_module, "_ground_with_contract", lambda repo, patch_series: GroundingResult(grounded, "identity"))
    approach_tree = _approach_tree()
    approach_tree["problem"].update(
        {
            "id": "problem",
            "problem": "The root needs a canonical producer cohort.",
            "affected": "Maintainers and patch authors.",
            "current_inadequacy": "The historical root cannot carry producer identity.",
            "non_goals": [],
            "constraints": {},
            "prior_art": [],
            "open_questions": [],
            "goals": [
                {
                    "id": "goal:formation",
                    "requires_deliverable": True,
                    "actor": "maintainer",
                    "capability": {
                        "action": "Inspect the canonical root.",
                        "preconditions": ["The candidate is frozen."],
                    },
                    "verifiable_outcome": {
                        "expected_state": "The producer pair is closed.",
                        "evidence": "The deterministic vet passes.",
                    },
                    "verification": {
                        "method": "automated_test",
                        "check": "Exercise the formation transition.",
                    },
                    "ux_trace": "none",
                }
            ],
            "deliverable_requirements": [
                {
                    "id": "requirement:formation",
                    "referee_goal_id": "goal:formation",
                    "production_obligation_id": "obligation:formation",
                    "deliverable": "technical-approach-plan.cue",
                }
            ],
            "production_obligations": [
                {
                    "id": "obligation:formation",
                    "referee_goal_id": "goal:formation",
                    "deliverable_requirement_id": "requirement:formation",
                    "deliverable": "technical-approach-plan.cue",
                    "eligibility_predicate": "Can produce and test the canonical body.",
                    "replacement_links": [],
                }
            ],
            "patch_plan": [
                {
                    "item_id": "patch_plan:formation#first-proof-moment",
                    "production_obligation_ids": ["obligation:formation"],
                }
            ],
        }
    )
    approach_tree["cross_links"] = []
    calls = []

    def fake_build_from_patch_series(patch_series_view, context=None, **kwargs):
        calls.append("build_from_patch_series")
        assert patch_series_view == grounded
        assert kwargs == {"kinds": ("design",)}
        assert context["repo"] == repo.resolve()
        assert context["repo_root"] == repo.resolve()
        assert "language" not in context
        assert "environment" not in context
        assert "version" not in context
        return {"terms": {}, "processed_terms": ["battle loop"], "expanded": [], "hits": [], "failed": {}}

    def fake_start_background_build(patch_series_view, context=None, **kwargs):
        calls.append("start_background_build")
        assert calls == ["build_from_patch_series", "start_background_build"]
        assert kwargs == {"kinds": ("implementation",)}
        assert context["repo"] == repo.resolve()
        return object()

    def fake_form_technical_approach(patch_series_view, repo_path, **kwargs):
        design_terms, _background_future = kwargs[
            "precedent_front_future"
        ].result()
        calls.append("form_technical_approach")
        assert calls == [
            "build_from_patch_series",
            "start_background_build",
            "form_technical_approach",
        ]
        assert repo_path == repo.resolve()
        assert kwargs["context"]["repo"] == repo.resolve()
        assert design_terms == ["battle loop"]
        assert "language" not in kwargs["context"]
        assert "environment" not in kwargs["context"]
        assert "version" not in kwargs["context"]
        return {
            "ok": True,
            "technical_approach": approach_tree,
            "external_files": {"domain-spec/battle-numbers.json": {"tables": []}},
        }

    def fake_prepare_producer_aware_formation(formation, repo_path, **kwargs):
        calls.append("prepare_producer_aware_formation")
        assert calls == [
            "build_from_patch_series",
            "start_background_build",
            "form_technical_approach",
            "prepare_producer_aware_formation",
        ]
        assert formation["technical_approach"] == approach_tree
        assert repo_path == repo.resolve()
        assert kwargs["cover_letter"] == grounded
        prepared = patch_series_bodies.prepare_root_technical_approach_preview(
            kwargs["cover_letter"],
            kwargs["request_provenance"],
            approach_tree,
        )
        return receive_module.CanonicalRootTechnicalApproachPreview(
            technical_approach=approach_tree,
            canonical_cue=prepared.canonical_cue.decode("utf-8"),
            body_sha256=prepared.body_sha256,
            cohort_files=prepared.files(),
        )

    monkeypatch.setattr(receive_module.engineering_precedent_store, "build_from_patch_series", fake_build_from_patch_series)
    monkeypatch.setattr(receive_module.engineering_precedent_store, "start_background_build", fake_start_background_build)
    monkeypatch.setattr(receive_module, "form_technical_approach", fake_form_technical_approach)
    monkeypatch.setattr(
        receive_module,
        "prepare_producer_aware_formation",
        fake_prepare_producer_aware_formation,
    )

    result = produce_patch_series(
        request,
        repo,
        request_provenance={"request_id": "request-123", "payload": request},
    )

    assert result["ok"] is True
    assert result["status"] == "promoted"
    assert result["id"] == "manual-intake"
    assert result["branch"] == "ai-org/patch-series/manual-intake"
    assert result["commit"] == _git(repo, "rev-parse", "refs/heads/ai-org/patch-series/manual-intake")
    assert result["technical_approach_path"] == "technical-approach-plan.cue"
    assert _git(repo, "rev-parse", "HEAD") == _git(repo, "rev-parse", "refs/heads/main")
    assert _git(repo, "show", "main:README.md") == "base"
    raw_cover = git_wrapper.show_file_bytes(
        repo,
        "ai-org/patch-series/manual-intake",
        patch_series_bodies.COVER_PATH,
    )
    assert raw_cover is not None
    produced = BodyCodecClient().parse(
        patch_series_bodies.COVER_CONTEXT,
        raw_cover,
        expected=patch_series_bodies.COVER_CONTRACT,
    )
    assert produced == grounded
    assert _implement_is_registry_patch_series(produced)
    assert "custom_priority" not in produced
    raw_provenance = git_wrapper.show_file_bytes(
        repo,
        "ai-org/patch-series/manual-intake",
        patch_series_bodies.PROVENANCE_PATH,
    )
    assert raw_provenance is not None
    provenance = BodyCodecClient().parse(
        patch_series_bodies.PROVENANCE_CONTEXT,
        raw_provenance,
        expected=patch_series_bodies.PROVENANCE_CONTRACT,
    )
    canonical_payload = json.dumps(request, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    assert provenance["request_id"] == "request-123"
    assert provenance["raw_request"] == request["raw_request"]
    assert provenance["payload_sha256"] == hashlib.sha256(canonical_payload.encode("utf-8")).hexdigest()
    assert provenance["request_payload"] == request
    root_snapshot = patch_series_bodies.classify_root_generation(
        repo, "ai-org/patch-series/manual-intake"
    )
    raw_approach = root_snapshot.raw(
        patch_series_bodies.ROOT_APPROACH_PATH
    )
    assert raw_approach is not None
    prepared_approach = BodyCodecClient().parse(
        patch_series_bodies.ROOT_APPROACH_CONTEXT,
        raw_approach,
        expected=patch_series_bodies.ROOT_APPROACH_CONTRACT,
    )
    assert prepared_approach == approach_tree
    assert prepared_approach["problem"]["goals"][0]["requires_deliverable"] is True
    assert "deliverable_required" not in json.dumps(prepared_approach)
    assert root_snapshot.generation == patch_series_bodies.ROOT_GENERATION_V2
    assert (
        root_snapshot.disposition
        == patch_series_bodies.ROOT_DISPOSITION_V2_NOT_READY
    )
    paths = set(git_wrapper.tree_files(repo, "ai-org/patch-series/manual-intake"))
    assert {
        patch_series_bodies.COVER_PATH,
        patch_series_bodies.PROVENANCE_PATH,
        patch_series_bodies.ROOT_APPROACH_PATH,
    } <= paths
    assert {
        patch_series_bodies.LEGACY_COVER_PATH,
        patch_series_bodies.LEGACY_PROVENANCE_PATH,
        patch_series_bodies.LEGACY_ROOT_APPROACH_PATH,
    }.isdisjoint(paths)
    assert json.loads(_git(repo, "show", "ai-org/patch-series/manual-intake:domain-spec/battle-numbers.json")) == {"tables": []}
    rfc_posts = mailing_list.read(repo, kinds={"rfc"})
    assert len(rfc_posts) == 1
    assert rfc_posts[0]["subject"] == "manual-intake"
    assert rfc_posts[0]["refs"] == {
        "commit": result["commit"],
        "series": result["branch"],
    }
    assert calls == [
        "build_from_patch_series",
        "start_background_build",
        "form_technical_approach",
        "prepare_producer_aware_formation",
    ]
    log_events = [
        json.loads(line)
        for log_path in (repo / ".ai-org" / "log" / "runs").glob("*/*/supervisor.jsonl")
        for line in log_path.read_text(encoding="utf-8").splitlines()
    ]
    event_types = [event["event_type"] for event in log_events]
    assert "patch_series.produce.started" in event_types
    assert "engineering_precedent_store.design_build.started" in event_types
    assert "patch_series.promoted" in event_types
    assert "patch_series.produce.completed" in event_types


def test_new_formation_never_falls_back_when_canonical_preflight_fails(
    tmp_path, monkeypatch
):
    repo = _init_repo(tmp_path)
    request = receive({"raw_request": "Create an asset commission for game assets."})
    grounded = _patch_series_view(
        "Asset Commission",
        raw_request=request["raw_request"],
        proposal_hint="Commission art, animation, audio, and text assets.",
    )
    grounded["request_type"] = "asset commission"

    monkeypatch.setattr(receive_module, "_ground_with_contract", lambda repo, patch_series: GroundingResult(grounded, "identity"))
    monkeypatch.setattr(
        receive_module.engineering_precedent_store,
        "build_from_patch_series",
        lambda *args, **kwargs: {"terms": {}, "processed_terms": [], "expanded": [], "hits": [], "failed": {}},
    )
    monkeypatch.setattr(receive_module.engineering_precedent_store, "start_background_build", lambda *args, **kwargs: object())
    monkeypatch.setattr(
        receive_module,
        "form_technical_approach",
        lambda *args, **kwargs: {"ok": True, "technical_approach": _approach_tree(), "external_files": {}},
    )
    monkeypatch.setattr(
        receive_module,
        "prepare_producer_aware_formation",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ValueError("canonical cohort preflight failed")
        ),
    )

    result = produce_patch_series(request, repo)

    assert result["ok"] is False
    assert result["status"] == "needs_work"
    assert result["failed_step"] == "producer_aware_formation"
    assert result["error"] == "canonical cohort preflight failed"
    assert git_wrapper.branches(repo, "ai-org/patch-series/asset-commission") == []


def test_produce_patch_series_emits_spine_for_asset_bearing_kind_and_not_machinery(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    monkeypatch.setattr(receive_module.engineering_precedent_store, "lookup", _fake_spine_lookup)
    monkeypatch.setattr(
        receive_module.engineering_precedent_store,
        "build_from_patch_series",
        lambda *args, **kwargs: {"terms": {}, "processed_terms": ["visual identity"], "expanded": [], "hits": [], "failed": {}},
    )
    monkeypatch.setattr(receive_module.engineering_precedent_store, "start_background_build", lambda *args, **kwargs: object())
    monkeypatch.setattr(
        receive_module,
        "form_technical_approach",
        lambda *args, **kwargs: {"ok": True, "technical_approach": _approach_tree()},
    )

    asset_patch_series = _patch_series_view(
        "Playable Visual Surface",
        raw_request="Build a user-facing visual surface with authored assets.",
        affected_area_platform="browser runtime",
    )
    monkeypatch.setattr(receive_module, "_ground_with_contract", lambda repo, patch_series: GroundingResult(asset_patch_series, "identity"))

    asset_result = produce_patch_series(receive({"raw_request": asset_patch_series["raw_request"]}), repo)

    assert asset_result["status"] == "promoted"
    assert asset_result["spine_artifact_paths"] == [
        "spine/art-bible.json",
        "spine/asset-manifest.schema.json",
    ]
    art_bible = json.loads(_git(repo, "show", "ai-org/patch-series/playable-visual-surface:spine/art-bible.json"))
    assert set(art_bible) >= {"judgment_layer", "machine_layer", "reference_citations"}
    assert art_bible["machine_layer"]["proportion_systems_table"][0]["head_count"] == "3.5"
    manifest_schema = json.loads(
        _git(repo, "show", "ai-org/patch-series/playable-visual-surface:spine/asset-manifest.schema.json")
    )
    jsonschema.Draft202012Validator.check_schema(manifest_schema)

    machinery_patch_series = _patch_series_view(
        "Internal Machinery Refactor",
        raw_request="Refactor internal patch series machinery.",
        affected_area_platform="ai_org.patchwork_queue",
    )
    machinery_patch_series["user_experience_requirements"] = _not_user_facing_ux()
    monkeypatch.setattr(receive_module, "_ground_with_contract", lambda repo, patch_series: GroundingResult(machinery_patch_series, "identity"))

    machinery_result = produce_patch_series(receive({"raw_request": machinery_patch_series["raw_request"]}), repo)

    assert machinery_result["status"] == "promoted"
    assert machinery_result["spine_artifact_paths"] == []
    missing = subprocess.run(
        ["git", "-C", str(repo), "show", "ai-org/patch-series/internal-machinery-refactor:spine/art-bible.json"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert missing.returncode != 0


def test_spine_art_bible_layers_and_manifest_schema_validation(monkeypatch):
    monkeypatch.setattr(spine_module.engineering_precedent_store, "lookup", _fake_spine_lookup)
    artifacts = spine_module.derive_artifacts(
        _patch_series_view("Visual Asset Contract"),
        _approach_tree(),
        context={"repo": "test"},
    )

    art_bible = artifacts["spine/art-bible.json"]
    assert art_bible["judgment_layer"]["visual_pillars"]
    assert art_bible["judgment_layer"]["negative_examples"]
    assert art_bible["machine_layer"]["palette"]["hue_budget"]["dominant_hues"] == 3
    assert [row["system"] for row in art_bible["machine_layer"]["proportion_systems_table"]] == ["portrait", "field"]
    assert {"enlarge", "compress", "preserve", "elide"} == set(
        art_bible["machine_layer"]["deformation_redistribution_rules"]
    )
    assert [citation["term"] for citation in art_bible["reference_citations"]] == list(spine_module.REFERENCE_TERMS)

    schema = artifacts["spine/asset-manifest.schema.json"]
    compliant = {
        "asset_id": "hero.walk",
        "category": "character",
        "kind": "sprite_sheet",
        "dimensions": {"width": 128, "height": 128, "unit": "px"},
        "sheet_layout": {"frame_width": 32, "frame_height": 32, "columns": 4, "rows": 4, "spacing": 0},
        "state_direction_matrix": [{"state": "walk", "directions": ["north", "south"]}],
        "pivot": {"x": 16, "y": 28, "origin": "bottom_center"},
        "palette_family_ref": "identity",
        "license_provenance": {"author": "AI Org", "license": "CC-BY-4.0", "source": "worktree"},
        "acceptance_checks": ["renders at field scale", "matches art bible palette family"],
    }
    jsonschema.validate(compliant, schema)

    license_less = dict(compliant)
    license_less["license_provenance"] = {"author": "AI Org", "source": "worktree"}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(license_less, schema)


def test_produce_patch_series_forwards_progress_path(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    request = receive({"raw_request": "Manual Intake: commit with progress snapshots."})
    grounded = _patch_series_view("Manual Intake", raw_request=request["raw_request"])
    progress_path = tmp_path / "progress" / "technical-approach-plan.json"

    monkeypatch.setattr(receive_module, "_ground_with_contract", lambda repo, patch_series: GroundingResult(grounded, "identity"))
    monkeypatch.setattr(
        receive_module.engineering_precedent_store,
        "build_from_patch_series",
        lambda *args, **kwargs: {"terms": {}, "processed_terms": ["battle loop"], "expanded": [], "hits": [], "failed": {}},
    )
    monkeypatch.setattr(receive_module.engineering_precedent_store, "start_background_build", lambda *args, **kwargs: object())

    def fake_form_technical_approach(patch_series_view, repo_path, **kwargs):
        assert kwargs["progress_path"] == progress_path
        Path(kwargs["progress_path"]).parent.mkdir(parents=True, exist_ok=True)
        Path(kwargs["progress_path"]).write_text(json.dumps({"current_step": None}), encoding="utf-8")
        return {"ok": True, "technical_approach": _approach_tree()}

    monkeypatch.setattr(receive_module, "form_technical_approach", fake_form_technical_approach)

    result = produce_patch_series(request, repo, progress_path=progress_path)

    assert result["status"] == "promoted"
    assert json.loads(progress_path.read_text(encoding="utf-8")) == {"current_step": None}


def test_intake_grounding_confident_writes_grounded_branch(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    request = receive(
        {
            "raw_request": "Make a maze arcade game like kumo. Build a spider labyrinth.",
        }
    )
    grounded = _patch_series_view(
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
            "proposed_patch_series": grounded,
            "assumptions": [],
            "questions": [],
            "grounding_notes": notes,
        }

    _install_codex_fake(monkeypatch, handler)
    _install_successful_approach_pipeline(monkeypatch)

    result = intake(request, repo)

    assert result["status"] == "promoted"
    assert result["id"] == "auto-battle-party-dungeon-rpg"
    assert result["branch"] == "ai-org/patch-series/auto-battle-party-dungeon-rpg"
    assert result["grounding_notes"] == notes
    assert _read_canonical_pending_cover(
        repo, "ai-org/patch-series/auto-battle-party-dungeon-rpg"
    ) == grounded
    assert _read_canonical_pending_approach(
        repo, "ai-org/patch-series/auto-battle-party-dungeon-rpg"
    ) == _approach_tree()
    assert result["technical_approach_path"] == "technical-approach-plan.cue"
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
    proposed_patch_series = _patch_series_view(
        "Conversation-Inferred Dungeon Automation Game",
        raw_request=request["raw_request"],
        problem_or_motivation="The requester likely wants the previously discussed automation game, but the exact reference is not fully recoverable from the request alone.",
        intended_users_or_jobs="Players who want a lightweight automated dungeon progression game.",
        desired_outcomes_success="The requester can confirm or correct a concrete dungeon automation interpretation.",
        affected_area_platform="game",
        background_facts="The available wording points at a dungeon automation loop.",
        grounding_provenance="Grounding inferred a likely game request from the available wording and repository game context.",
        proposal_hint="Build a small dungeon automation loop with party setup, automated runs, rewards, and progression.",
        alternatives_considered=["Wait for a named reference before shaping the patch series."],
    )
    assumptions = [
        "I assumed 'that thing we discussed' refers to a dungeon automation game because the repository context points at game work and the request asks for a rough game.",
        "I assumed the first patch series should cover core loop and progression rather than art polish because the problem does not name a visual style.",
    ]
    questions = ["Can you name the exact prior reference if this inferred game is wrong?"]
    notes = "The reference is ambiguous, but grounding inferred a likely patch series from repo context."
    calls = []

    monkeypatch.setattr(
        receive_module,
        "_ground_with_contract",
        lambda repo, patch_series: GroundingResult(proposed_patch_series, notes, False, assumptions, questions),
    )

    def fake_build_from_patch_series(patch_series_view, context=None, **kwargs):
        calls.append("build_from_patch_series")
        assert patch_series_view["open_questions"] == questions
        assert patch_series_view["constraints_assumptions"] == assumptions
        assert "Grounding was not fully confident" in patch_series_view["grounding_provenance"]
        assert assumptions[0] in patch_series_view["grounding_provenance"]
        assert kwargs == {"kinds": ("design",)}
        return {"terms": {}, "processed_terms": ["dungeon automation"], "expanded": [], "hits": [], "failed": {}}

    def fake_start_background_build(patch_series_view, context=None, **kwargs):
        calls.append("start_background_build")
        assert patch_series_view["open_questions"] == questions
        assert kwargs == {"kinds": ("implementation",)}
        return object()

    def fake_form_technical_approach(patch_series_view, repo_path, **kwargs):
        design_terms, _background_future = kwargs[
            "precedent_front_future"
        ].result()
        calls.append("form_technical_approach")
        assert patch_series_view["open_questions"] == questions
        assert patch_series_view["constraints_assumptions"] == assumptions
        assert design_terms == ["dungeon automation"]
        return {"ok": True, "technical_approach": _approach_tree()}

    monkeypatch.setattr(receive_module.engineering_precedent_store, "build_from_patch_series", fake_build_from_patch_series)
    monkeypatch.setattr(receive_module.engineering_precedent_store, "start_background_build", fake_start_background_build)
    monkeypatch.setattr(receive_module, "form_technical_approach", fake_form_technical_approach)

    result = intake(request, repo)

    assert result["status"] == "promoted"
    assert result["id"] == "conversation-inferred-dungeon-automation-game"
    assert result["grounding_notes"] == notes
    produced = _read_canonical_pending_cover(
        repo, "ai-org/patch-series/conversation-inferred-dungeon-automation-game"
    )
    assert produced["open_questions"] == questions
    assert produced["constraints_assumptions"] == assumptions
    assert "Grounding was not fully confident" in produced["grounding_provenance"]
    assert assumptions[0] in produced["grounding_provenance"]
    assert calls == ["build_from_patch_series", "start_background_build", "form_technical_approach"]
    assert _git(repo, "rev-parse", "HEAD") == _git(repo, "rev-parse", "refs/heads/main")


def test_intake_grounding_not_confident_requires_confirmation_when_toggle_on(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    monkeypatch.setenv("AI_ORG_REQUIRE_CONFIRMATION", "yes")
    request = receive(
        {
            "raw_request": "Make it like that thing we discussed.",
        }
    )
    proposed_patch_series = _patch_series_view(
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
        lambda repo, patch_series: GroundingResult(proposed_patch_series, notes, False, assumptions, questions),
    )
    monkeypatch.setattr(
        receive_module.engineering_precedent_store,
        "build_from_patch_series",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("design build should not run")),
    )
    monkeypatch.setattr(
        receive_module.engineering_precedent_store,
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
        "proposed_patch_series": proposed_patch_series,
        "assumptions": assumptions,
        "questions": questions,
        "grounding_notes": notes,
    }
    assert "branch" not in result
    missing_branch = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--verify", "refs/heads/ai-org/patch-series/conversation-inferred-dungeon-automation-game"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert missing_branch.returncode != 0
    assert _git(repo, "rev-parse", "HEAD") == _git(repo, "rev-parse", "refs/heads/main")


def test_produce_patch_series_approach_failure_does_not_promote_hollow_patch_series(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    request = receive(
        {
            "raw_request": "Manual Intake: commit the validated registry patch series as patch-series-cover-letter.json.",
        }
    )
    grounded = _patch_series_view("Manual Intake", raw_request=request["raw_request"])
    monkeypatch.setattr(receive_module, "_ground_with_contract", lambda repo, patch_series: GroundingResult(grounded, "identity"))
    monkeypatch.setattr(
        receive_module.engineering_precedent_store,
        "build_from_patch_series",
        lambda *args, **kwargs: {"terms": {}, "processed_terms": ["battle loop"], "expanded": [], "hits": [], "failed": {}},
    )
    monkeypatch.setattr(receive_module.engineering_precedent_store, "start_background_build", lambda *args, **kwargs: object())
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
        "_write_patch_series_branch",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("hollow patch series should not be written")),
    )

    result = produce_patch_series(request, repo)

    assert result == {
        "ok": False,
        "status": "needs_work",
        "error": "Could not select a coherent approach.",
        "failed_step": "select_approach",
        "proposed_patch_series": grounded,
        "grounding_notes": "identity",
    }
    missing_branch = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--verify", "refs/heads/ai-org/patch-series/manual-intake"],
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
    bad_grounding = _patch_series_view(
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
            "proposed_patch_series": bad_grounding,
            "assumptions": [],
            "questions": [],
            "grounding_notes": "Grounded as a generic retro Famicom prototype with trademark, copyright, IP, legal, licensing, and material usage concerns.",
        }

    _install_codex_fake(monkeypatch, handler)

    result = intake(request, repo)

    assert result["ok"] is False
    assert result["status"] == "needs_work"
    assert result["failed_step"] == "grounding"
    assert result["proposed_patch_series"] == bad_grounding
    assert "violations" in result
    assert any("C1 faithfulness/specificity" in violation for violation in result["violations"])
    assert any("C2 full scope" in violation for violation in result["violations"])
    assert any("C3 non-legal" in violation for violation in result["violations"])
    assert any("C4 latest-default" in violation for violation in result["violations"])
    assert result["failure_mode"] == "permanent_rejection"
    assert len(grounding_prompts) == 2
    assert verifier_calls == 2
    assert "Your previous grounding violated" in grounding_prompts[1]
    assert "branch" not in result


def test_marker_lints_ignore_retro_exclusions_in_context_fields(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    request = receive_module._entrance_request(receive({"raw_request": "Make Dragon Quest as the current experience."}))
    grounded = _patch_series_view(
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
            "proposed_patch_series": grounded,
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
    grounded = _patch_series_view(
        "Current Named Game",
        raw_request=request["raw_request"],
        problem_or_motivation="The franchise began in 1986 and still has a classic command battle feel.",
        desired_outcomes_success="Deliver the current game with its classic command battle feel.",
    )

    violations = _grounding_lint_violations(request, grounded)

    assert not any("C4 latest-default" in violation for violation in violations)


def test_latest_default_c4_follows_verifier_verdict(monkeypatch):
    request = receive_module._entrance_request(receive({"raw_request": "Make the current named game experience."}))
    grounded = _patch_series_view(
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
    grounded = _patch_series_view(
        "Past Version Named Game",
        raw_request=request["raw_request"],
        desired_outcomes_success="Target the named game's classic past-version conventions.",
    )

    assert not any("C4 latest-default" in violation for violation in _grounding_lint_violations(request, grounded))
    passed = _verify_with_latest_default(monkeypatch, request, grounded, latest_default=True)
    assert passed == {"ok": True, "violations": []}


@pytest.mark.parametrize(
    ("case_name", "mutate"),
    [
        (
            "not_generic_prohibition",
            lambda grounded: grounded.update(
                {
                    "problem_or_motivation": (
                        "The validator failures must not be generic; each failure must cite the exact rule, "
                        "path, and requester-facing consequence."
                    )
                }
            ),
        ),
        (
            "minimal_touchpoints_boundary",
            lambda grounded: grounded["constraints_assumptions"].append(
                "Keep unrelated modules to minimal touchpoints while preserving the full requested receive behavior."
            ),
        ),
        (
            "patch_series_style_workflow",
            lambda grounded: grounded.update(
                {
                    "proposal_hint": (
                        "Use patch-series-style workflow vocabulary for the review handoff, without shrinking "
                        "the requested deliverable."
                    )
                }
            ),
        ),
        (
            "gerrit_style_must_not_resemble",
            lambda grounded: grounded["user_experience_requirements"]["experience_identity"].update(
                {"must_not_resemble": "Gerrit-style hidden mutable state that makes review network invisible."}
            ),
        ),
    ],
)
def test_c1_c2_legitimate_vocabulary_is_accepted_by_verifier_and_has_no_lint_rejection(
    monkeypatch, case_name, mutate
):
    request = receive_module._entrance_request(receive({"raw_request": "Build the patch series receive grounding gate fix."}))
    grounded = _patch_series_view(
        "patch series Receive Grounding Gate Fix",
        raw_request=request["raw_request"],
        desired_outcomes_success="Implement the full grounding gate fix ratified by the requester.",
    )
    mutate(grounded)

    assert not any(
        violation.startswith(("C1 faithfulness/specificity lint", "C2 full scope lint"))
        for violation in _grounding_lint_violations(request, grounded)
    )

    result = _verify_with_grounding_verdict(
        monkeypatch,
        request,
        grounded,
        {
            "faithful_specific": True,
            "full_scope": True,
            "non_legal": True,
            "latest_default": True,
            "reasons": [],
        },
    )

    assert result == {"ok": True, "violations": []}


@pytest.mark.parametrize(
    ("case_name", "raw_request", "grounded_factory", "verdict", "expected_label"),
    [
        (
            "dragon_quest_generic_style",
            "Make Dragon Quest. Build Dragon Quest.",
            lambda: _patch_series_view(
                "Generic RPG-Style Game",
                problem_or_motivation="Build a generic RPG-style game inspired by Dragon Quest.",
                desired_outcomes_success="A broad-category RPG experience with Dragon Quest references.",
            ),
            {
                "faithful_specific": False,
                "full_scope": True,
                "non_legal": True,
                "latest_default": True,
                "reasons": ["The named target was watered down to a generic RPG-style category."],
            },
            "C1 faithfulness/specificity",
        ),
        (
            "complete_game_shrunk_to_mvp",
            "Build the complete named game experience.",
            lambda: _patch_series_view(
                "Named Game MVP",
                desired_outcomes_success="Ship a minimal MVP vertical slice / one town demo.",
            ),
            {
                "faithful_specific": True,
                "full_scope": False,
                "non_legal": True,
                "latest_default": True,
                "reasons": ["The full game request was shrunk to a one-town MVP demo."],
            },
            "C2 full scope",
        ),
        (
            "named_product_broad_category",
            "Build Acme Ledger Pro exactly.",
            lambda: _patch_series_view(
                "Generic Accounting Tool",
                problem_or_motivation="Build a broad accounting tool category instead of Acme Ledger Pro.",
                desired_outcomes_success="Users get a generic finance dashboard.",
            ),
            {
                "faithful_specific": False,
                "full_scope": True,
                "non_legal": True,
                "latest_default": True,
                "reasons": ["The named product was renamed to a broad category in title and target fields."],
            },
            "C1 faithfulness/specificity",
        ),
        (
            "latest_request_retargeted_to_retro",
            "Implement the latest behavior of ExampleDB.",
            lambda: _patch_series_view(
                "ExampleDB Retro Compatibility",
                desired_outcomes_success="Recreate ExampleDB 1.0 behavior and older conventions.",
            ),
            {
                "faithful_specific": True,
                "full_scope": True,
                "non_legal": True,
                "latest_default": False,
                "reasons": ["The latest-behavior request was silently retargeted to a retro version."],
            },
            "C4 latest-default",
        ),
    ],
)
def test_grounding_semantic_verifier_rejects_real_downgrades(
    monkeypatch, case_name, raw_request, grounded_factory, verdict, expected_label
):
    request = receive_module._entrance_request(receive({"raw_request": raw_request}))
    grounded = grounded_factory()
    grounded["raw_request"] = request["raw_request"]

    result = _verify_with_grounding_verdict(monkeypatch, request, grounded, verdict)

    assert result["ok"] is False
    assert any(expected_label in violation for violation in result["violations"])


@pytest.mark.skipif(
    os.environ.get("AI_ORG_LIVE_GROUNDING_VERIFIER_SMOKE", "").strip().lower() not in {"1", "true", "yes"},
    reason="optional live codex verifier smoke is opt-in",
)
def test_live_grounding_verifier_smoke_accepts_legitimate_marker_vocabulary():
    request = receive_module._entrance_request(
        receive({"raw_request": "Build the patch series receive grounding gate fix without shrinking scope."})
    )
    grounded = _patch_series_view(
        "patch series Receive Grounding Gate Fix",
        raw_request=request["raw_request"],
        desired_outcomes_success=(
            "Implement the full fix; failures must not be generic and unrelated modules should have minimal touchpoints."
        ),
    )

    result = receive_module._verify_grounding(request, grounded, GroundingResult(grounded, "Live smoke fixture."))

    assert result == {"ok": True, "violations": []}


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
    bad_grounding = _patch_series_view(
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
            "proposed_patch_series": bad_grounding,
            "assumptions": [],
            "questions": [],
            "grounding_notes": "Grounding incorrectly deliberated Unreal from franchise precedent.",
        }

    _install_codex_fake(monkeypatch, handler)

    result = intake(request, repo)

    assert result["ok"] is False
    assert result["failed_step"] == "grounding"
    assert any("grounding may not set provenance=ai_deliberated" in item for item in result["violations"])
    assert result["failure_mode"] == "permanent_rejection"
    assert len(grounding_prompts) == 2
    assert "Your previous grounding violated" in grounding_prompts[1]


def test_grounding_empty_user_facing_ux_regrounds_and_fails_closed(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    request = receive({"raw_request": "Make Dragon Quest."})
    bad_grounding = _patch_series_view("Dragon Quest", raw_request=request["raw_request"])
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
            "proposed_patch_series": bad_grounding,
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
    forged_grounding = _patch_series_view("Dragon Quest", raw_request=request["raw_request"])
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
            "proposed_patch_series": forged_grounding,
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
    grounded = _patch_series_view("Dragon Quest", raw_request=request["raw_request"])
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
            "proposed_patch_series": grounded,
            "assumptions": [],
            "questions": [],
            "grounding_notes": "Grounding preserved the requester-specified Unreal stack.",
        }

    _install_codex_fake(monkeypatch, handler)
    _install_successful_approach_pipeline(monkeypatch)

    result = intake(request, repo)

    assert result["status"] == "promoted"
    produced = _read_canonical_pending_cover(repo, "ai-org/patch-series/dragon-quest")
    assert produced["tech_stack"]["provenance"] == "requester_specified"


def test_grounding_empty_working_title_gets_deterministic_fallback_before_promotion(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    request = receive(
        {
            "raw_request": "Add a dashboard for patch series intake health.",
        }
    )
    incomplete = _patch_series_view(
        "",
        raw_request=request["raw_request"],
        problem_or_motivation="patch series intake health dashboard is needed.",
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
            "proposed_patch_series": incomplete,
            "assumptions": [],
            "questions": [],
            "grounding_notes": "Grounded patch series intake health dashboard.",
        }

    _install_codex_fake(monkeypatch, handler)
    _install_successful_approach_pipeline(monkeypatch)

    result = intake(request, repo)

    assert result["status"] == "promoted"
    assert result["id"] == "patch-series-intake-health-dashboard"
    assert grounding_calls == 1
    assert "working_title" in grounding_prompts[0]
    produced = _read_canonical_pending_cover(
        repo, "ai-org/patch-series/patch-series-intake-health-dashboard"
    )
    assert produced == {**incomplete, "working_title": "Patch Series Intake Health Dashboard"}


def test_ascii_working_slug_names_non_ascii_title_and_collision_suffix(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    request = receive({"raw_request": "ドラゴンクエスト本編再現ゲームを作る。"})
    grounded = _patch_series_view(
        "ドラゴンクエスト本編再現ゲーム",
        raw_request=request["raw_request"],
    )
    monkeypatch.setattr(
        receive_module,
        "_ground_with_contract",
        lambda repo, patch_series: GroundingResult(
            grounded,
            "Grounding translated the Japanese working title for branch naming.",
            ascii_working_slug="dragon-quest-mainline",
        ),
    )
    _install_successful_approach_pipeline(monkeypatch)

    first = produce_patch_series(request, repo)
    second = produce_patch_series(request, repo)

    assert first["branch"] == "ai-org/patch-series/dragon-quest-mainline"
    assert first["id"] == "dragon-quest-mainline"
    assert second["branch"] == "ai-org/patch-series/dragon-quest-mainline-2"
    assert second["id"] == "dragon-quest-mainline-2"
    produced = _read_canonical_pending_cover(repo, first["branch"])
    assert produced["working_title"] == "ドラゴンクエスト本編再現ゲーム"
    assert _git(repo, "rev-parse", f"{first['branch']}^{{tree}}") == _git(
        repo, "rev-parse", f"{second['branch']}^{{tree}}"
    )


def test_grounding_other_empty_required_field_regrounds_and_fails_closed(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    request = receive(
        {
            "raw_request": "Add a dashboard for patch series intake health.",
        }
    )
    incomplete = _patch_series_view(
        "Patch Series Intake Health Dashboard",
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
            "proposed_patch_series": incomplete,
            "assumptions": [],
            "questions": [],
            "grounding_notes": "Grounded patch series intake health dashboard.",
        }

    _install_codex_fake(monkeypatch, handler)
    _install_successful_approach_pipeline(monkeypatch)

    result = intake(request, repo)

    assert result["ok"] is False
    assert result["status"] == "needs_work"
    assert result["failed_step"] == "grounding"
    assert result["failure_mode"] == "permanent_rejection"
    assert grounding_calls == 2
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

    schema_patch_series = GROUNDING_SCHEMA["properties"]["proposed_patch_series"]
    assert schema_patch_series["additionalProperties"] is False
    assert tuple(schema_patch_series["required"]) == COMMON_8_FIELDS
    assert sorted(schema_patch_series["required"]) == sorted(schema_patch_series["properties"])
    assert schema_patch_series["properties"]["tech_stack"]["required"] == list(receive_module.TECH_STACK_FIELDS)
    assert schema_patch_series["properties"]["user_experience_requirements"]["required"] == list(
        receive_module.USER_EXPERIENCE_REQUIREMENTS_FIELDS
    )
    # The registry semantics reach the schema as a STRING description (codex/OpenAI Structured
    # Outputs reject a non-string `description` with HTTP 400 "... is not of type 'string'").
    # The structured dict form lives only in the prompt (REQUEST_SCHEMA/_field_registry_prompt).
    provenance_desc = schema_patch_series["properties"]["grounding_provenance"]["description"]
    assert isinstance(provenance_desc, str)
    assert "must_not=content consumed downstream as product requirement nouns" in provenance_desc


class _FakeBuildFuture:
    def __init__(self, finished: bool) -> None:
        self._finished = finished

    def done(self) -> bool:
        return self._finished


def _drain_harness(monkeypatch, tmp_path, *, form_result, future):
    repo = _init_repo(tmp_path)
    request = receive({"raw_request": "Wire the background drain."})
    grounded = _patch_series_view("Drain wiring", raw_request=request["raw_request"])
    monkeypatch.setattr(receive_module, "_ground_with_contract", lambda repo, patch_series: GroundingResult(grounded, "identity"))
    monkeypatch.setattr(
        receive_module.engineering_precedent_store,
        "build_from_patch_series",
        lambda *args, **kwargs: {"terms": {}, "processed_terms": [], "expanded": [], "hits": [], "failed": {}},
    )
    monkeypatch.setattr(
        receive_module.engineering_precedent_store, "start_background_build", lambda *args, **kwargs: future
    )
    monkeypatch.setattr(receive_module, "form_technical_approach", lambda *args, **kwargs: form_result)
    drain_calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        receive_module.engineering_precedent_store,
        "await_background_builds",
        lambda timeout=None: drain_calls.append({"timeout": timeout}),
    )
    return repo, request, drain_calls


def test_promotion_path_drains_background_builds_with_bounded_timeout(tmp_path, monkeypatch):
    repo, request, drain_calls = _drain_harness(
        monkeypatch,
        tmp_path,
        form_result={"ok": True, "technical_approach": _approach_tree(), "external_files": {}},
        future=_FakeBuildFuture(finished=True),
    )

    result = produce_patch_series(request, repo)

    assert result["status"] == "promoted"
    # Drained AFTER the promotion commit, before returning, with the bounded budget.
    assert drain_calls == [{"timeout": receive_module.BACKGROUND_BUILD_DRAIN_TIMEOUT_SECONDS}]
    assert receive_module.BACKGROUND_BUILD_DRAIN_TIMEOUT_SECONDS == 240.0


def test_promotion_continues_and_warns_when_rfc_post_fails(tmp_path, monkeypatch):
    repo, request, _drain_calls = _drain_harness(
        monkeypatch,
        tmp_path,
        form_result={"ok": True, "technical_approach": _approach_tree(), "external_files": {}},
        future=_FakeBuildFuture(finished=True),
    )
    warnings: list[tuple[str, dict[str, object], str]] = []
    monkeypatch.setattr(
        receive_module.mailing_list,
        "post",
        lambda *args, **kwargs: {"ok": False, "error": "injected RFC failure"},
    )
    monkeypatch.setattr(
        receive_module.org_log,
        "debug_emit",
        lambda event_type, payload, *, ctx, severity="debug": warnings.append(
            (event_type, payload, severity)
        ),
    )

    result = produce_patch_series(request, repo)

    assert result["ok"] is True
    assert result["status"] == "promoted"
    assert result["commit"] == _git(repo, "rev-parse", result["branch"])
    assert warnings == [
        (
            "mailing_list.rfc.failed",
            {
                "series": result["branch"],
                "commit": result["commit"],
                "error": "injected RFC failure",
            },
            "warning",
        )
    ]


def test_needs_work_after_background_start_still_drains(tmp_path, monkeypatch):
    repo, request, drain_calls = _drain_harness(
        monkeypatch,
        tmp_path,
        form_result={"ok": False, "error": "approach formation failed", "failed_step": "decision"},
        future=_FakeBuildFuture(finished=True),
    )

    result = produce_patch_series(request, repo)

    assert result["ok"] is False
    assert result["status"] == "needs_work"
    assert drain_calls == [{"timeout": receive_module.BACKGROUND_BUILD_DRAIN_TIMEOUT_SECONDS}]


def test_drain_timeout_records_undrained_build_and_never_raises(tmp_path, monkeypatch):
    events: list[tuple[str, dict[str, object]]] = []
    real_emit = receive_module.org_log.emit
    monkeypatch.setattr(
        receive_module.org_log,
        "emit",
        lambda event_type, payload=None, **kwargs: events.append((event_type, dict(payload or {}))) or real_emit(event_type, payload, **kwargs),
    )
    monkeypatch.setattr(
        receive_module.engineering_precedent_store, "await_background_builds", lambda timeout=None: None
    )
    view = _patch_series_view("Drain wiring")
    ctx = receive_module.org_log.RunContext(repo=tmp_path, run_id="run-drain-test")

    # Timeout expiry: the future is still running after the bounded wait.
    receive_module._drain_background_precedent_builds(_FakeBuildFuture(finished=False), view, ctx)
    timeout_events = [payload for event_type, payload in events if event_type == "engineering_precedent_store.background_drain.timeout"]
    assert timeout_events == [
        {
            "timeout_seconds": 240.0,
            "undrained_build": {"working_title": "Drain wiring", "kinds": ["implementation"]},
        }
    ]

    # A raising drain is recorded, never propagated.
    def exploding(timeout=None):
        raise RuntimeError("executor wedged")

    monkeypatch.setattr(receive_module.engineering_precedent_store, "await_background_builds", exploding)
    receive_module._drain_background_precedent_builds(_FakeBuildFuture(finished=False), view, ctx)
    failed_events = [payload for event_type, payload in events if event_type == "engineering_precedent_store.background_drain.failed"]
    assert failed_events == [{"error": "executor wedged"}]


def test_explicit_reform_replaces_legacy_root_with_canonical_cohort(
    tmp_path, monkeypatch
):
    repo = _init_repo(tmp_path)
    patch_series_view = _patch_series_view("Reviewable patch series")
    approach = _reform_approach_tree("old slice")
    approach["problem"]["goals"] = [
        {
            "id": "goal:1",
            "requires_deliverable": True,
            "actor": "maintainer",
            "capability": {
                "action": "Inspect the revised patch plan.",
                "preconditions": ["The reform candidate is frozen."],
            },
            "verifiable_outcome": {
                "expected_state": "The first slice is reviewable.",
                "evidence": "The reform transition test passes.",
            },
            "verification": {
                "method": "automated_test",
                "check": "Read the canonical successor root.",
            },
            "ux_trace": "none",
        }
    ]
    approach["problem"]["deliverable_requirements"] = [
        {
            "id": "requirement:1",
            "referee_goal_id": "goal:1",
            "production_obligation_id": "obligation:1",
            "deliverable": "technical-approach-plan.cue",
        }
    ]
    approach["problem"]["production_obligations"] = [
        {
            "id": "obligation:1",
            "referee_goal_id": "goal:1",
            "deliverable_requirement_id": "requirement:1",
            "deliverable": "technical-approach-plan.cue",
            "eligibility_predicate": "Can produce and test the canonical body.",
            "replacement_links": [],
        }
    ]
    approach["problem"]["patch_plan"] = [
        {
            "item_id": "patch_plan:candidate:one#first-proof-moment",
            "production_obligation_ids": ["obligation:1"],
        }
    ]
    branch = "ai-org/patch-series/reviewable-patch_series"
    _git(repo, "checkout", "-B", branch, "main")
    (repo / "patch-series-cover-letter.json").write_text(json.dumps(patch_series_view) + "\n", encoding="utf-8")
    provenance = {
        "request_id": "reviewable-request",
        "payload_sha256": "a" * 64,
        "raw_request": patch_series_view["raw_request"],
        "request_payload": {"raw_request": patch_series_view["raw_request"]},
        "memento": "historical provenance remains migration input only",
    }
    (repo / "patch-series-request-provenance.json").write_text(
        json.dumps(provenance) + "\n", encoding="utf-8"
    )
    (repo / "technical-approach-plan.json").write_text(json.dumps(approach) + "\n", encoding="utf-8")
    _git(
        repo,
        "add",
        "patch-series-cover-letter.json",
        "patch-series-request-provenance.json",
        "technical-approach-plan.json",
    )
    _git(repo, "commit", "-m", "initial patch_series")
    _git(repo, "commit", "--allow-empty", "-m", "patch_series: needs-revision round 1")
    _git(repo, "checkout", "main")
    _write_review_round(repo, "reviewable-patch_series", _blocking_objection("patch_plan:candidate:one"))
    calls: list[str] = []
    producer_calls: list[tuple[dict[str, object], dict[str, object]]] = []

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

    def prepare_reform_preview(reform, previous, repo_path, **kwargs):
        producer_calls.append((reform, previous))
        assert repo_path == repo.resolve()
        prepared = patch_series_bodies.prepare_root_technical_approach_preview(
            kwargs["cover_letter"],
            kwargs["request_provenance"],
            reform["technical_approach"],
        )
        return receive_module.CanonicalRootTechnicalApproachPreview(
            technical_approach=reform["technical_approach"],
            canonical_cue=prepared.canonical_cue.decode("utf-8"),
            body_sha256=prepared.body_sha256,
            cohort_files=prepared.files(),
        )

    monkeypatch.setattr(
        receive_module,
        "prepare_producer_aware_reform",
        prepare_reform_preview,
    )

    result = receive_module.reform_patch_series(repo, "reviewable-patch_series")

    assert result["status"] == "reformed"
    assert calls == ["patch_plan"]
    assert len(producer_calls) == 1
    assert result["branch"] == branch
    latest_message = _git(repo, "log", "-1", "--pretty=%B", branch)
    assert "patch_series v2: Reviewable patch series" in latest_message
    assert "Changes since v1:" in latest_message
    assert "approach:1" in latest_message
    raw_revised = git_wrapper.show_file_bytes(
        repo, branch, patch_series_bodies.ROOT_APPROACH_PATH
    )
    assert raw_revised is not None
    revised = BodyCodecClient().parse(
        patch_series_bodies.ROOT_APPROACH_CONTEXT,
        raw_revised,
        expected=patch_series_bodies.ROOT_APPROACH_CONTRACT,
    )
    assert revised["problem"]["constraints"] == approach["problem"]["constraints"]
    assert revised["problem"]["question"]["decision"]["implementation"]["patch_plan"]["first_safe_slice"] == "new slice"
    paths = set(git_wrapper.tree_files(repo, branch))
    assert {
        patch_series_bodies.COVER_PATH,
        patch_series_bodies.PROVENANCE_PATH,
        patch_series_bodies.ROOT_APPROACH_PATH,
    } <= paths
    assert {
        patch_series_bodies.LEGACY_COVER_PATH,
        patch_series_bodies.LEGACY_PROVENANCE_PATH,
        patch_series_bodies.LEGACY_ROOT_APPROACH_PATH,
    }.isdisjoint(paths)
    snapshot = patch_series_bodies.classify_root_generation(repo, branch)
    assert snapshot.generation == patch_series_bodies.ROOT_GENERATION_V2
    assert snapshot.disposition == patch_series_bodies.ROOT_DISPOSITION_V2_NOT_READY
    changed_files = _git(repo, "show", "--name-only", "--pretty=format:", branch).splitlines()
    assert patch_series_bodies.ROOT_APPROACH_PATH in changed_files
    response = review_bodies.read_record(repo, branch, result["author_response_path"], review_bodies.AUTHOR_RESPONSE_RECIPE)
    assert response["objections"][0]["status"] == "answered"
    original = json.loads((repo / ".ai-org/review/reviewable-patch_series/round-1.json").read_text(encoding="utf-8"))
    assert original["objections"][0]["status"] == "open"


def test_reform_author_paper_prints_committed_self_history_oldest_first(tmp_path):
    repo = _init_repo(tmp_path)
    branch = "ai-org/patch-series/reviewable-patch_series"
    _git(repo, "checkout", "-B", branch, "main")
    (repo / "patch-series-cover-letter.json").write_text(
        json.dumps(_patch_series_view("Reviewable patch series")) + "\n", encoding="utf-8"
    )

    history_dir = repo / "patch-series-review-rounds"
    history_dir.mkdir()

    def commit_author_round(
        round_number: int,
        *,
        framework: str,
        response: str,
    ) -> None:
        approach = _reform_approach_tree(f"round {round_number} slice")
        candidate = approach["problem"]["question"]["candidates"][0]
        candidate["stack_requirement"] = {
            "build_strategy": "framework_based",
            "engine": "",
            "framework": framework,
            "language": "Python",
            "platform": "server",
        }
        decision = approach["problem"]["question"]["decision"]
        decision["rationale"]["because"] = [f"Choose {framework} in round {round_number}."]
        (repo / "technical-approach-plan.json").write_text(
            json.dumps(approach) + "\n", encoding="utf-8"
        )
        delta = {
            "patch_series_id": "reviewable-patch_series",
            "review_round": round_number,
            "author_version": round_number + 1,
            "added_node_ids": [],
            "changed_node_ids": ["candidate:one"],
            "pruned_node_ids": [],
            "node_replacements": [],
            "objection_responses": [
                {
                    "objection_id": f"approach:{round_number}",
                    "classification": "defended_with_evidence",
                    "anchor_node_ids": ["candidate:one"],
                    "successor_node_ids": [],
                    "defense": response,
                    "evidence_citations": ["candidate:one"],
                    }
                ],
                "memento": ["Committed author revision response."],
            }
        delta_path = history_dir / f"round-{round_number:04d}-revision-delta.json"
        delta_path.write_text(json.dumps(delta) + "\n", encoding="utf-8")
        direction_record = {
            "patch_series_id": "reviewable-patch_series",
            "branch": branch,
            "round": round_number,
            "inputs": {},
            "axis_reviews": [],
            "objections": [],
            "resolved_objections": [],
            "per_axis_verdicts": {},
            "consolidation": {},
            "verdict": "nak",
            "git_result_marker": "patch_series: nak",
        }
        direction_path = history_dir / f"round-{round_number:04d}-direction-review-record.json"
        direction_path.write_text(json.dumps(direction_record) + "\n", encoding="utf-8")
        _git(
            repo,
            "add",
            "patch-series-cover-letter.json",
            "technical-approach-plan.json",
            str(delta_path.relative_to(repo)),
            str(direction_path.relative_to(repo)),
        )
        _git(repo, "commit", "-m", f"patch_series v{round_number + 1}: author round {round_number}")

    round_one_response = "Round one response stays verbatim: retain Framework One."
    round_two_response = "Round two response stays verbatim: retain Framework Two."
    commit_author_round(1, framework="Framework One", response=round_one_response)
    commit_author_round(2, framework="Framework Two", response=round_two_response)

    history = receive_module._author_self_history(repo, branch)

    assert [entry["review_round"] for entry in history["rounds"]] == [1, 2]
    assert history["rounds"][0]["revision_responses"][0]["defense"] == round_one_response
    assert history["rounds"][1]["revision_responses"][0]["defense"] == round_two_response
    assert history["rounds"][0]["candidate_framework_decisions"]["candidates"][0][
        "stack_requirement"
    ]["framework"] == "Framework One"
    assert history["rounds"][1]["candidate_framework_decisions"]["candidates"][0][
        "stack_requirement"
    ]["framework"] == "Framework Two"

    plan = {
        "target_bodies": {"candidate:one": {"id": "candidate:one", "summary": "Current candidate."}},
        "objections": [],
        "neighborhood_bodies": {},
        "out_of_step_anchor_bodies": {},
        "manifest_ids": [],
        "open_questions": [],
    }
    prompt = receive_module._targeted_revision_prompt("candidates", plan, history)
    assert "AUTHOR SELF-HISTORY" in prompt
    assert round_one_response in prompt
    assert round_two_response in prompt
    assert prompt.index(round_one_response) < prompt.index(round_two_response)
    assert prompt.index("Framework One") < prompt.index("Framework Two")


def _ai_deliberated_phaser_stack() -> dict[str, object]:
    # Committed formation-era state of the v3e loop: the org deliberated Phaser 3.
    return {
        "build_strategy": "framework_based",
        "engine": "",
        "framework": "Phaser 3",
        "language": "TypeScript",
        "platform": "browser",
        "rationale": "Phaser was selected at formation.",
        "provenance": "ai_deliberated",
    }


def test_reform_changing_selected_candidate_rederives_cover_letter_tech_stack(tmp_path, monkeypatch):
    # Brief 16 disease (v3e rounds 3->4): reform regenerated candidates and the
    # decision drifted Phaser->React->Svelte, but the committed cover letter kept
    # the Phaser 3 stack contract, so the reviewer deterministically re-objected.
    repo = _init_repo(tmp_path)
    patch_series_view = _patch_series_view("Reviewable patch series")
    patch_series_view["tech_stack"] = _ai_deliberated_phaser_stack()
    approach = _reform_approach_tree("old slice")
    branch = "ai-org/patch-series/reviewable-patch_series"
    _git(repo, "checkout", "-B", branch, "main")
    (repo / "patch-series-cover-letter.json").write_text(json.dumps(patch_series_view) + "\n", encoding="utf-8")
    (repo / "technical-approach-plan.json").write_text(json.dumps(approach) + "\n", encoding="utf-8")
    _git(repo, "add", "patch-series-cover-letter.json", "technical-approach-plan.json")
    _git(repo, "commit", "-m", "initial patch_series")
    _git(repo, "commit", "--allow-empty", "-m", "patch_series: needs-revision round 1")
    _git(repo, "checkout", "main")
    objection = _blocking_objection("decision:candidate:one")
    objection["anchor_node_ids"] = ["candidate:one", "decision:candidate:one"]
    _write_review_round(repo, "reviewable-patch_series", objection)

    svelte_candidate = {
        "id": "candidate:svelte",
        "name": "Svelte compiled DOM",
        "kind": "framework",
        "summary": "Compile the JRPG UI to lean DOM updates.",
        "stack_requirement": {
            "build_strategy": "framework_based",
            "engine": "",
            "framework": "Svelte",
            "language": "TypeScript",
            "platform": "browser",
        },
        # Brief 19 node identity contract: replacing the previous candidate must be
        # declared; an undeclared prune fails the revision continuity gate.
        "replaces": "candidate:one",
        "replacement_reason": "Review objections invalidated the previous approach direction.",
    }

    def fake_generate_candidates(*_args, **_kwargs):
        return {"candidates": [dict(svelte_candidate)]}

    def fake_evaluate_candidates(candidates, *_args, log_ctx=None, **_kwargs):
        return {
            "evaluations": [
                {"candidate_id": candidate["id"], "scores": {}} for candidate in candidates["candidates"]
            ]
        }

    def fake_select_approach(candidates, evaluations, *_args, log_ctx=None, **_kwargs):
        assert [item["candidate_id"] for item in evaluations["evaluations"]] == ["candidate:svelte"]
        return {
            "selected_candidate_id": "candidate:svelte",
            "arguments": [],
            "rationale": {"because": ["Svelte compiles to lean DOM."], "under_constraints": [], "accepting_tradeoffs": []},
            "stack_axes": {},
            "rejected": [],
        }

    def unexpected(*_args, **_kwargs):
        raise AssertionError("unanchored step should not rerun")

    monkeypatch.setattr(receive_module, "_generate_candidates", fake_generate_candidates)
    monkeypatch.setattr(receive_module, "_evaluate_candidates", fake_evaluate_candidates)
    monkeypatch.setattr(receive_module, "_select_approach", fake_select_approach)
    monkeypatch.setattr(receive_module, "_normalize_problem", unexpected)
    monkeypatch.setattr(receive_module, "_extract_constraints", unexpected)
    monkeypatch.setattr(receive_module, "_build_prior_art_map", unexpected)
    monkeypatch.setattr(receive_module, "_implementation_strategy", unexpected)
    monkeypatch.setattr(receive_module, "_right_size_patch_plan", unexpected)
    monkeypatch.setattr(receive_module, "_surface_risks", unexpected)

    result = receive_module.reform_patch_series(repo, "reviewable-patch_series")

    assert result["status"] == "reformed"
    cover = _read_canonical_pending_cover(repo, branch)
    tech_stack = cover["tech_stack"]
    assert tech_stack["framework"] == "Svelte"
    assert tech_stack["language"] == "TypeScript"
    assert tech_stack["build_strategy"] == "framework_based"
    assert tech_stack["provenance"] == "ai_deliberated"
    assert "Svelte compiles to lean DOM." in tech_stack["rationale"]
    assert "Phaser" not in tech_stack["rationale"]
    changed_files = _git(repo, "show", "--name-only", "--pretty=format:", branch).splitlines()
    assert patch_series_bodies.COVER_PATH in changed_files
    assert patch_series_bodies.COVER_PATH in result["changed_nodes"]
    # Brief 17: the committed tree carries the refreshed evaluation matrix in
    # formation's exact shape — evaluations cover exactly the new candidate ids,
    # the dead generation is gone, and the mutation is accounted in changed_nodes
    # (live: round-3 objected "the comparative evaluation is not auditable"
    # against a decision whose refreshed matrix was never persisted).
    revised_tree = _read_canonical_pending_approach(repo, branch)
    tree_candidates = revised_tree["problem"]["question"]["candidates"]
    assert [candidate["id"] for candidate in tree_candidates] == ["candidate:svelte"]
    evaluation_node = tree_candidates[0]["evaluation"]
    assert evaluation_node["id"] == "evaluation:candidate:svelte"
    assert evaluation_node["candidate_id"] == "candidate:svelte"
    assert "evaluation:candidate:one" not in json.dumps(revised_tree)
    assert "evaluation:candidate:svelte" in result["changed_nodes"]
    assert "evaluation:candidate:one" in result["changed_nodes"]
    # Brief 19 component B: the machine-readable revision delta ledger is committed
    # with the reform and classifies each prior objection mechanically.
    assert result["revision_delta_path"] == "patch-series-review-rounds/round-0001-revision-delta.cue"
    ledger = review_bodies.read_record(repo, branch, result["revision_delta_path"], review_bodies.REVISION_DELTA_RECIPE)
    assert "candidate:one" in ledger["pruned_node_ids"]
    assert "candidate:svelte" in ledger["added_node_ids"]
    replacement_pairs = {
        (item["replaced_node_id"], item["successor_node_id"]) for item in ledger["node_replacements"]
    }
    assert ("candidate:one", "candidate:svelte") in replacement_pairs
    assert ("decision:candidate:one", "decision:candidate:svelte") in replacement_pairs
    responses = {item["objection_id"]: item for item in ledger["objection_responses"]}
    assert responses["approach:1"]["classification"] == "superseded_by_replacement"
    # Both anchors were replaced: the declared candidate successor and the derived
    # decision singleton successor.
    assert responses["approach:1"]["successor_node_ids"] == ["candidate:svelte", "decision:candidate:svelte"]


def test_reform_undeclared_candidate_prune_fails_closed_with_exact_references(tmp_path, monkeypatch):
    # Brief 19 lint e2e: regenerating candidates WITHOUT declaring the replacement
    # is a contract violation surfaced as typed needs_work at reform commit time,
    # naming the exact dangling anchors; nothing is committed.
    repo = _init_repo(tmp_path)
    patch_series_view = _patch_series_view("Reviewable patch series")
    patch_series_view["tech_stack"] = _ai_deliberated_phaser_stack()
    approach = _reform_approach_tree("old slice")
    branch = "ai-org/patch-series/reviewable-patch_series"
    _git(repo, "checkout", "-B", branch, "main")
    (repo / "patch-series-cover-letter.json").write_text(json.dumps(patch_series_view) + "\n", encoding="utf-8")
    (repo / "technical-approach-plan.json").write_text(json.dumps(approach) + "\n", encoding="utf-8")
    _git(repo, "add", "patch-series-cover-letter.json", "technical-approach-plan.json")
    _git(repo, "commit", "-m", "initial patch_series")
    _git(repo, "commit", "--allow-empty", "-m", "patch_series: needs-revision round 1")
    _git(repo, "checkout", "main")
    objection = _blocking_objection("decision:candidate:one")
    objection["anchor_node_ids"] = ["candidate:one", "decision:candidate:one"]
    _write_review_round(repo, "reviewable-patch_series", objection)

    def fake_generate_candidates(*_args, **_kwargs):
        # No replaces declaration: silent rename, the exact disease.
        return {"candidates": [{"id": "candidate:svelte", "name": "Svelte compiled DOM", "kind": "framework",
                                "summary": "Compile the JRPG UI to lean DOM updates."}]}

    def fake_evaluate_candidates(candidates, *_args, log_ctx=None, **_kwargs):
        return {"evaluations": [{"candidate_id": item["id"], "scores": {}} for item in candidates["candidates"]]}

    def fake_select_approach(candidates, evaluations, *_args, log_ctx=None, **_kwargs):
        return {"selected_candidate_id": "candidate:svelte", "arguments": [],
                "rationale": {"because": ["Svelte."], "under_constraints": [], "accepting_tradeoffs": []},
                "stack_axes": {}, "rejected": []}

    monkeypatch.setattr(receive_module, "_generate_candidates", fake_generate_candidates)
    monkeypatch.setattr(receive_module, "_evaluate_candidates", fake_evaluate_candidates)
    monkeypatch.setattr(receive_module, "_select_approach", fake_select_approach)
    # Brief 22: the gate failure now triggers the bounded reconciliation round
    # first; pin it to a failed codex run so this test keeps proving the
    # fail-closed path deterministically (an unpinned run here reached a REAL
    # codex once - and the round healed the tree, which is the firing tests'
    # job to assert, not this one's).
    reconciliation_calls: list[dict[str, object]] = []

    def failed_reconciliation(repo_path, **kwargs):
        reconciliation_calls.append(kwargs)
        return {"ok": False, "error": "codex unavailable in this test"}

    monkeypatch.setattr(receive_module.codex_exec, "run_json", failed_reconciliation)

    result = receive_module.reform_patch_series(repo, "reviewable-patch_series")

    assert result["ok"] is False
    assert result["status"] == "needs_work"
    assert result["failed_step"] == "revision_continuity"
    assert any("undeclared prune" in item and "'candidate:one'" in item for item in result["violations"])
    assert len(reconciliation_calls) == 1, "one bounded round, no retry"
    # Fail-closed BEFORE commit: the branch head is still the needs-revision marker.
    assert _git(repo, "log", "-1", "--format=%s", branch).strip() == "patch_series: needs-revision round 1"


def _stale_root_link_tree() -> dict[str, object]:
    # The live pomodoro v4 shape (2026-07-05): the candidate's draws_on carries a
    # pure prose provenance note ("Repository inspection: ...") that v1 assembly
    # FABRICATED into a prior_art cross_link, plus a real pattern name that must
    # keep linking. The committed tree still holds the v1-era fabricated link.
    tree = _reform_approach_tree("old slice")
    candidate = tree["problem"]["question"]["candidates"][0]
    candidate["draws_on"] = [
        "Repository inspection: HEAD has no tracked files beyond scaffolding",
        "Phase State Machine",
    ]
    tree["problem"]["prior_art"] = [{"id": "prior_art:phase-state-machine", "name": "Phase State Machine"}]
    tree["cross_links"] = [
        {
            "from": "candidate:one",
            "to": "prior_art:repository-inspection-head-has-no-tracked-files-beyond-scaffolding",
            "type": "derived_from",
        },
    ]
    return tree


def _init_stale_link_repo(tmp_path, patch_series_view, *, anchor: str = "patch_plan:candidate:one") -> object:
    repo = _init_repo(tmp_path)
    branch = "ai-org/patch-series/reviewable-patch_series"
    _git(repo, "checkout", "-B", branch, "main")
    (repo / "patch-series-cover-letter.json").write_text(json.dumps(patch_series_view) + "\n", encoding="utf-8")
    (repo / "technical-approach-plan.json").write_text(json.dumps(_stale_root_link_tree()) + "\n", encoding="utf-8")
    _git(repo, "add", "patch-series-cover-letter.json", "technical-approach-plan.json")
    _git(repo, "commit", "-m", "initial patch_series")
    _git(repo, "commit", "--allow-empty", "-m", "patch_series: needs-revision round 1")
    _git(repo, "checkout", "main")
    _write_review_round(repo, "reviewable-patch_series", _blocking_objection(anchor))
    return repo


def _reconciliation_payload(mapping: dict[str, str], no_successor: dict[str, str] | None = None) -> str:
    entries = [
        {"dangling_id": dangling, "successor_id": successor, "no_successor": False,
         "reason": "Same logical concern; the node was renamed across generations."}
        for dangling, successor in mapping.items()
    ]
    for dangling, reason in (no_successor or {}).items():
        entries.append({"dangling_id": dangling, "successor_id": "", "no_successor": True, "reason": reason})
    return json.dumps({"reconciliations": entries})


def test_reconciliation_round_heals_legacy_root_links_from_the_live_shape(tmp_path, monkeypatch):
    # Live shape after brief 24: the prior_art step renames without declaring
    # (replaces empty, the pomodoro stdout behavior) -> the objection ANCHOR
    # dangles and reconciliation heals it. The prose provenance slug is never
    # fabricated into a link any more, so the reconciliation window must NOT
    # even ask about it (brief 24 item 5: only ids with lineage are asked).
    patch_series_view = _patch_series_view("Reviewable patch series")
    repo = _init_stale_link_repo(tmp_path, patch_series_view, anchor="prior_art:phase-state-machine")
    branch = "ai-org/patch-series/reviewable-patch_series"
    codex_calls: list[dict[str, object]] = []

    def fake_prior_art(*_args, revision=None, **_kwargs):
        # Live behavior: successor-shaped node, replaces left empty.
        return {"patterns": [{"name": "Event Driven Phase Lifecycle"}]}

    def fake_run_json(repo_path, **kwargs):
        codex_calls.append(kwargs)
        return {
            "ok": True,
            "raw": _reconciliation_payload(
                {"prior_art:phase-state-machine": "prior_art:event-driven-phase-lifecycle"}
            ),
        }

    monkeypatch.setattr(receive_module, "_build_prior_art_map", fake_prior_art)
    monkeypatch.setattr(receive_module.codex_exec, "run_json", fake_run_json)

    result = receive_module.reform_patch_series(repo, "reviewable-patch_series")

    assert result["status"] == "reformed"
    assert len(codex_calls) == 1
    assert "reasoning_effort" not in codex_calls[0]
    prompt = codex_calls[0]["prompt"]
    # The window lists the dangling ANCHOR and the just-created successor node...
    assert "prior_art:phase-state-machine" in prompt
    assert "Event Driven Phase Lifecycle" in prompt
    # ...and never asks about the never-existed prose slug (item 5) or bodies.
    assert "repository-inspection-head-has-no-tracked-files" not in prompt
    assert "stack_requirement" not in prompt
    revised_tree = _read_canonical_pending_approach(repo, branch)
    links_text = json.dumps(revised_tree["cross_links"])
    # No fabricated link; the real pattern link survives via lineage remap: the
    # renamed pattern has no link (its name changed), and the prose note has none.
    assert "repository-inspection-head-has-no-tracked-files" not in links_text
    ledger = review_bodies.read_record(repo, branch, result["revision_delta_path"], review_bodies.REVISION_DELTA_RECIPE)
    pairs = {(item["replaced_node_id"], item["successor_node_id"]) for item in ledger["node_replacements"]}
    assert ("prior_art:phase-state-machine", "prior_art:event-driven-phase-lifecycle") in pairs
    # The refusal to fabricate is recorded, never silent.
    assert (
        "prior_art:repository-inspection-head-has-no-tracked-files-beyond-scaffolding"
        in ledger["prose_provenance_not_linked"]
    )
    # The prose provenance itself is retained verbatim on the candidate.
    tree_candidate = revised_tree["problem"]["question"]["candidates"][0]
    assert "Repository inspection: HEAD has no tracked files beyond scaffolding" in tree_candidate["draws_on"]


def test_prose_provenance_is_not_linked_and_real_patterns_still_link(tmp_path, monkeypatch):
    # Brief 24 mixed e2e (pomodoro shape): with fabrication stopped, a series
    # whose only dead ids were prose provenance heals WITHOUT any reconciliation
    # call — the gate is green because the references were never real. The real
    # pattern-name draws_on entry still links exactly as before.
    patch_series_view = _patch_series_view("Reviewable patch series")
    repo = _init_stale_link_repo(tmp_path, patch_series_view)
    branch = "ai-org/patch-series/reviewable-patch_series"
    codex_calls: list[dict[str, object]] = []

    def forbidden_run_json(repo_path, **kwargs):
        codex_calls.append(kwargs)
        raise AssertionError("no reconciliation is needed when nothing real dangles")

    def revised_patch_plan(*_args, **_kwargs):
        return {"first_safe_slice": "new slice", "follow_ups": [], "deferred_work": []}

    monkeypatch.setattr(receive_module, "_right_size_patch_plan", revised_patch_plan)
    monkeypatch.setattr(receive_module.codex_exec, "run_json", forbidden_run_json)

    result = receive_module.reform_patch_series(repo, "reviewable-patch_series")

    assert result["status"] == "reformed"
    assert codex_calls == []
    revised_tree = _read_canonical_pending_approach(repo, branch)
    links = revised_tree["cross_links"]
    # Real pattern-name draws_on -> link emitted exactly as today.
    assert {"from": "candidate:one", "to": "prior_art:phase-state-machine", "type": "derived_from"} in links
    # Prose provenance -> no link, prose retained verbatim, ledger records it.
    assert "repository-inspection-head-has-no-tracked-files" not in json.dumps(links)
    tree_candidate = revised_tree["problem"]["question"]["candidates"][0]
    assert "Repository inspection: HEAD has no tracked files beyond scaffolding" in tree_candidate["draws_on"]
    ledger = review_bodies.read_record(repo, branch, result["revision_delta_path"], review_bodies.REVISION_DELTA_RECIPE)
    assert ledger["prose_provenance_not_linked"] == [
        "prior_art:repository-inspection-head-has-no-tracked-files-beyond-scaffolding"
    ]


def test_reconciliation_no_successor_stays_fail_closed_with_exact_refs(tmp_path, monkeypatch):
    # Brief 22 refusal case on a REAL dangling (an objection anchor whose node
    # was renamed without declaration): the model judges no current node
    # corresponds; the judgment is recorded and the gate stays fail-closed.
    patch_series_view = _patch_series_view("Reviewable patch series")
    repo = _init_stale_link_repo(tmp_path, patch_series_view, anchor="prior_art:phase-state-machine")
    branch = "ai-org/patch-series/reviewable-patch_series"
    codex_calls: list[dict[str, object]] = []

    def fake_prior_art(*_args, revision=None, **_kwargs):
        return {"patterns": [{"name": "Event Driven Phase Lifecycle"}]}

    def fake_run_json(repo_path, **kwargs):
        codex_calls.append(kwargs)
        return {
            "ok": True,
            "raw": _reconciliation_payload(
                {},
                no_successor={"prior_art:phase-state-machine": "No current pattern covers that concern."},
            ),
        }

    monkeypatch.setattr(receive_module, "_build_prior_art_map", fake_prior_art)
    monkeypatch.setattr(receive_module.codex_exec, "run_json", fake_run_json)

    result = receive_module.reform_patch_series(repo, "reviewable-patch_series")

    assert result["ok"] is False
    assert result["status"] == "needs_work"
    assert result["failed_step"] == "revision_continuity"
    assert any(
        "undeclared prune" in item and "'prior_art:phase-state-machine'" in item for item in result["violations"]
    )
    assert result["reconciliation_no_successor"] == [
        {"dangling_id": "prior_art:phase-state-machine",
         "reason": "No current pattern covers that concern."}
    ]
    # ONE round only; fail-closed BEFORE commit.
    assert len(codex_calls) == 1
    assert _git(repo, "log", "-1", "--format=%s", branch).strip() == "patch_series: needs-revision round 1"


def test_reform_without_decision_change_keeps_tech_stack_byte_identical(tmp_path, monkeypatch):
    # A patch-plan-only reform never touches the decision: the cover letter stack
    # contract must stay byte-identical (the re-derivation is gated on the
    # candidates/decision steps actually re-running).
    repo = _init_repo(tmp_path)
    patch_series_view = _patch_series_view("Reviewable patch series")
    patch_series_view["tech_stack"] = _ai_deliberated_phaser_stack()
    approach = _reform_approach_tree("old slice")
    branch = "ai-org/patch-series/reviewable-patch_series"
    _git(repo, "checkout", "-B", branch, "main")
    (repo / "patch-series-cover-letter.json").write_text(json.dumps(patch_series_view) + "\n", encoding="utf-8")
    (repo / "technical-approach-plan.json").write_text(json.dumps(approach) + "\n", encoding="utf-8")
    _git(repo, "add", "patch-series-cover-letter.json", "technical-approach-plan.json")
    _git(repo, "commit", "-m", "initial patch_series")
    _git(repo, "commit", "--allow-empty", "-m", "patch_series: needs-revision round 1")
    _git(repo, "checkout", "main")
    _write_review_round(repo, "reviewable-patch_series", _blocking_objection("patch_plan:candidate:one"))

    def revised_patch_plan(*_args, **_kwargs):
        return {"first_safe_slice": "new slice", "follow_ups": [], "deferred_work": []}

    monkeypatch.setattr(receive_module, "_right_size_patch_plan", revised_patch_plan)

    result = receive_module.reform_patch_series(repo, "reviewable-patch_series")

    assert result["status"] == "reformed"
    changed_files = _git(repo, "show", "--name-only", "--pretty=format:", branch).splitlines()
    assert patch_series_bodies.COVER_PATH in changed_files
    cover = _read_canonical_pending_cover(repo, branch)
    assert cover["tech_stack"] == _ai_deliberated_phaser_stack()
    # Brief 17: no decision re-run -> the committed evaluation content is
    # byte-identical (modulo the assembler's pre-existing normalization, which
    # stamps arguments: [] on every evaluation node in BOTH paths) and no
    # evaluation node enters changed_nodes.
    revised_tree = _read_canonical_pending_approach(repo, branch)
    assert revised_tree["problem"]["question"]["candidates"][0]["evaluation"] == {
        **approach["problem"]["question"]["candidates"][0]["evaluation"],
        "arguments": [],
    }
    assert not any(node.startswith("evaluation:") for node in result["changed_nodes"])


def test_fill_ai_deliberated_tech_stack_never_rewrites_requester_specified():
    view = _patch_series_view("Requester stack")
    view["tech_stack"] = {
        "build_strategy": "engine_based",
        "engine": "Unity",
        "framework": "",
        "language": "C#",
        "platform": "desktop",
        "rationale": "Requester mandated Unity.",
        "provenance": "requester_specified",
    }
    before = json.loads(json.dumps(view["tech_stack"]))

    receive_module._fill_ai_deliberated_tech_stack(
        view,
        {"selected_candidate_id": "candidate:svelte"},
        {"candidates": [{"id": "candidate:svelte", "name": "Svelte"}]},
    )

    assert view["tech_stack"] == before


def test_reform_risks_step_passes_surfaced_risks_through(tmp_path, monkeypatch):
    # The reformed risks step returns the surfaced risks unchanged; no standing
    # org-profile conflict risk is derived (that mechanism was abolished).
    view = _patch_series_view("Requester stack")
    view["tech_stack"] = {
        "build_strategy": "engine_based",
        "engine": "Unity",
        "framework": "",
        "language": "C#",
        "platform": "desktop",
        "rationale": "Requester mandated Unity.",
        "provenance": "requester_specified",
    }
    components = receive_module._approach_components(_reform_approach_tree("old slice"))

    def fake_surface_risks(*_args, log_ctx=None, **_kwargs):
        return {"risks": [{"id": "risk:base", "risk": "Base risk.", "mitigation": "Handle it.", "attaches_to": "decision", "target_id": "decision:candidate:one"}]}

    monkeypatch.setattr(receive_module, "_surface_risks", fake_surface_risks)

    result = receive_module._run_reform_step("risks", tmp_path, view, components, {"repo": tmp_path})

    risk_ids = [risk["id"] for risk in result["risks"]]
    assert risk_ids == ["risk:base"]

    # Typed step failures pass through untouched.
    monkeypatch.setattr(
        receive_module, "_surface_risks", lambda *args, **kwargs: {"ok": False, "error": "risk surface failed"}
    )
    failure = receive_module._run_reform_step("risks", tmp_path, view, components, {"repo": tmp_path})
    assert failure == {"ok": False, "error": "risk surface failed"}


def test_reform_records_requester_authority_assumption_and_revises_author_blocker(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    patch_series_view = _patch_series_view("Reviewable patch series")
    approach = _reform_approach_tree("old slice")
    branch = "ai-org/patch-series/reviewable-patch_series"
    _git(repo, "checkout", "-B", branch, "main")
    (repo / "patch-series-cover-letter.json").write_text(json.dumps(patch_series_view) + "\n", encoding="utf-8")
    (repo / "technical-approach-plan.json").write_text(json.dumps(approach) + "\n", encoding="utf-8")
    _git(repo, "add", "patch-series-cover-letter.json", "technical-approach-plan.json")
    _git(repo, "commit", "-m", "initial patch_series")
    _git(repo, "commit", "--allow-empty", "-m", "patch_series: needs-revision round 1")
    _git(repo, "checkout", "main")
    requester = {
        **_blocking_objection("decision:candidate:one"),
        "objection_id": "scope:rights-boundary",
        "claim": "The rights/content-use boundary needs requester policy.",
        "impact": "The author cannot decide the product identity risk tolerance.",
        "requested_author_action": "re_explain",
        "resolution_authority": "requester",
    }
    author = _blocking_objection("patch_plan:candidate:one")
    _write_review_round(repo, "reviewable-patch_series", [requester, author])
    calls: list[str] = []

    def unexpected(*_args, **_kwargs):
        raise AssertionError("requester-authority objection should not trigger technical reformation")

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

    result = receive_module.reform_patch_series(repo, "reviewable-patch_series")

    assert result["status"] == "reformed"
    assert calls == ["patch_plan"]
    cover = _read_canonical_pending_cover(repo, branch)
    entries = [
        json.loads(item)
        for item in cover["constraints_assumptions"]
        if item.startswith('{"adopted_default"')
    ]
    assert entries == [
        {
            "adopted_default": (
                "continue with the current explanation for selected approach candidate:one: "
                "The rights/content-use boundary needs requester policy. "
                "Monitor requester impact: The author cannot decide the product identity risk tolerance. "
                "Requester may override this default."
            ),
            "default_provenance": "deterministic",
            "objection_id": "scope:rights-boundary",
            "question": (
                "The rights/content-use boundary needs requester policy. "
                "Impact if wrong: The author cannot decide the product identity risk tolerance."
            ),
            "rationale": (
                "The objection is classified as requester-authority, so the author cannot settle it by technical revision. "
                "Requested author action was re_explain. Recording an explicit default preserves progress while keeping the requester question visible."
            ),
        }
    ]
    assert cover["open_questions"] == [
        (
            "scope:rights-boundary: The rights/content-use boundary needs requester policy. "
            "Impact if wrong: The author cannot decide the product identity risk tolerance."
        )
    ]
    response = review_bodies.read_record(repo, branch, result["author_response_path"], review_bodies.AUTHOR_RESPONSE_RECIPE)
    statuses = {item["objection_id"]: item["status"] for item in response["objections"]}
    assert statuses == {"scope:rights-boundary": "assumption_recorded", "approach:1": "answered"}
    assert response["requester_assumption_notes"] == entries


def test_reform_ignores_requester_authority_clarifications_and_suggestions(tmp_path):
    repo = _init_repo(tmp_path)
    patch_series_view = _patch_series_view("Reviewable patch series")
    approach = _reform_approach_tree("old slice")
    branch = "ai-org/patch-series/reviewable-patch_series"
    _git(repo, "checkout", "-B", branch, "main")
    (repo / "patch-series-cover-letter.json").write_text(json.dumps(patch_series_view) + "\n", encoding="utf-8")
    (repo / "technical-approach-plan.json").write_text(json.dumps(approach) + "\n", encoding="utf-8")
    _git(repo, "add", "patch-series-cover-letter.json", "technical-approach-plan.json")
    _git(repo, "commit", "-m", "initial patch_series")
    _git(repo, "commit", "--allow-empty", "-m", "patch_series: needs-revision round 1")
    _git(repo, "checkout", "main")
    clarification = {
        **_blocking_objection("decision:candidate:one"),
        "objection_id": "scope:clarify-policy",
        "axis": "scope",
        "type": "clarification",
        "claim": "Clarify requester policy.",
        "resolution_authority": "requester",
    }
    suggestion = {
        **_blocking_objection("decision:candidate:one"),
        "objection_id": "maintenance:budget-note",
        "axis": "maintenance",
        "type": "nonblocking_suggestion",
        "claim": "Requester budget preference could be documented.",
        "resolution_authority": "requester",
    }
    _write_review_round(repo, "reviewable-patch_series", [clarification, suggestion])

    result = receive_module.reform_patch_series(repo, "reviewable-patch_series")

    assert result["status"] == "no_open_blocking_objections"
    cover = json.loads(
        _git(repo, "show", f"{branch}:patch-series-cover-letter.json")
    )
    assert cover["constraints_assumptions"] == []
    assert cover["open_questions"] == []


def test_reform_preseeded_requester_assumption_marks_repeated_blocker_without_cover_commit(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    patch_series_view = _patch_series_view("Reviewable patch series")
    approach = _reform_approach_tree("old slice")
    requester = {
        **_blocking_objection("decision:candidate:one"),
        "objection_id": "scope:rights-boundary",
        "claim": "The rights/content-use boundary needs requester policy.",
        "impact": "The author cannot decide the product identity risk tolerance.",
        "requested_author_action": "re_explain",
        "resolution_authority": "requester",
    }
    patch_series_view, entries, changed = requester_assumptions.record_requester_authority_assumptions(
        patch_series_view,
        approach,
        [requester],
    )
    assert changed is True
    author = _blocking_objection("patch_plan:candidate:one")
    branch = "ai-org/patch-series/reviewable-patch_series"
    _git(repo, "checkout", "-B", branch, "main")
    (repo / "patch-series-cover-letter.json").write_text(json.dumps(patch_series_view) + "\n", encoding="utf-8")
    (repo / "technical-approach-plan.json").write_text(json.dumps(approach) + "\n", encoding="utf-8")
    _git(repo, "add", "patch-series-cover-letter.json", "technical-approach-plan.json")
    _git(repo, "commit", "-m", "initial patch_series")
    _git(repo, "commit", "--allow-empty", "-m", "patch_series: needs-revision round 1")
    _git(repo, "checkout", "main")
    _write_review_round(repo, "reviewable-patch_series", [requester, author])

    def unexpected(*_args, **_kwargs):
        raise AssertionError("requester-authority objection should not trigger technical reformation")

    monkeypatch.setattr(receive_module, "_normalize_problem", unexpected)
    monkeypatch.setattr(receive_module, "_extract_constraints", unexpected)
    monkeypatch.setattr(receive_module, "_build_prior_art_map", unexpected)
    monkeypatch.setattr(receive_module, "_generate_candidates", unexpected)
    monkeypatch.setattr(receive_module, "_select_approach", unexpected)
    monkeypatch.setattr(receive_module, "_implementation_strategy", unexpected)
    monkeypatch.setattr(receive_module, "_surface_risks", unexpected)

    def revised_patch_plan(*_args, **_kwargs):
        return {"first_safe_slice": "new slice", "follow_ups": [], "deferred_work": []}

    monkeypatch.setattr(receive_module, "_right_size_patch_plan", revised_patch_plan)

    result = receive_module.reform_patch_series(repo, "reviewable-patch_series")

    assert result["status"] == "reformed"
    cover = _read_canonical_pending_cover(repo, branch)
    assert [json.loads(item)["objection_id"] for item in cover["constraints_assumptions"]] == [
        "scope:rights-boundary"
    ]
    changed_files = _git(repo, "show", "--name-only", "--pretty=format:", branch).splitlines()
    assert patch_series_bodies.COVER_PATH in changed_files
    response = review_bodies.read_record(repo, branch, result["author_response_path"], review_bodies.AUTHOR_RESPONSE_RECIPE)
    statuses = {item["objection_id"]: item["status"] for item in response["objections"]}
    assert statuses == {"scope:rights-boundary": "assumption_recorded", "approach:1": "answered"}
    assert response["requester_assumption_notes"] == entries


def test_reform_reads_committed_round_record_after_transplant_without_ai_org_state(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    patch_series_view = _patch_series_view("Reviewable patch series")
    approach = _reform_approach_tree("old slice")
    branch = "ai-org/patch-series/reviewable-patch_series"
    _git(repo, "checkout", "-B", branch, "main")
    (repo / "patch-series-cover-letter.json").write_text(json.dumps(patch_series_view) + "\n", encoding="utf-8")
    (repo / "technical-approach-plan.json").write_text(json.dumps(approach) + "\n", encoding="utf-8")
    record_path = "patch-series-review-rounds/round-0001-direction-review-record.json"
    (repo / record_path).parent.mkdir(parents=True, exist_ok=True)
    (repo / record_path).write_text(
        json.dumps(
            {
                "patch_series_id": "reviewable-patch_series",
                "branch": branch,
                "round": 1,
                "reviewed_commit": "",
                "base_commit": "",
                "objections": [_blocking_objection("patch_plan:candidate:one")],
                "verdict": "needs_revision",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    _git(repo, "add", "patch-series-cover-letter.json", "technical-approach-plan.json", record_path)
    _git(repo, "commit", "-m", "patch_series: needs-revision round 1")
    _git(repo, "checkout", "main")

    clone = tmp_path / "fresh-clone"
    subprocess.run(["git", "clone", str(repo), str(clone)], check=True, capture_output=True, text=True)
    _git(clone, "checkout", "-b", branch, f"origin/{branch}")
    assert not (clone / ".ai-org").exists()

    monkeypatch.setattr(receive_module, "_normalize_problem", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("unexpected")))
    monkeypatch.setattr(receive_module, "_extract_constraints", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("unexpected")))
    monkeypatch.setattr(receive_module, "_build_prior_art_map", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("unexpected")))
    monkeypatch.setattr(receive_module, "_generate_candidates", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("unexpected")))
    monkeypatch.setattr(receive_module, "_select_approach", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("unexpected")))
    monkeypatch.setattr(receive_module, "_implementation_strategy", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("unexpected")))
    monkeypatch.setattr(receive_module, "_surface_risks", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("unexpected")))
    monkeypatch.setattr(
        receive_module,
        "_right_size_patch_plan",
        lambda *_args, **_kwargs: {"first_safe_slice": "new slice", "follow_ups": [], "deferred_work": []},
    )

    result = receive_module.reform_patch_series(clone, "reviewable-patch_series")

    assert result["status"] == "reformed"
    assert result["review_round"] == 1
    assert review_bodies.read_record(clone, branch, result["author_response_path"], review_bodies.AUTHOR_RESPONSE_RECIPE)["objections"][0]["status"] == "answered"


def test_write_patch_series_branch_commits_contributor_contract_schema(tmp_path):
    from ai_org import contributor_handoff
    from ai_org.patch_author import contract as contributor_contract

    repo = _init_repo(tmp_path)
    branch = "ai-org/patch-series/contract-series"

    receive_module._write_patch_series_branch(repo, branch, "main", _patch_series_view("Contract series"))

    committed = contributor_handoff.parse_result_contract(
        _git(repo, "show", f"{branch}:{contributor_contract.IMPLEMENTATION_RESULT_SCHEMA_PATH}") + "\n"
    )
    assert committed == contributor_handoff.result_contract_body()
    # The contract lives on the series branch, not only in implement.py's prompt.
    assert committed["required_fields"] == ["patch_series_branch", "node_key", "acknowledged_patchwork_checks"]


def test_write_patch_series_branch_prepares_handoff_members_before_publication(tmp_path):
    from ai_org import contributor_handoff

    repo = _init_repo(tmp_path)
    branch = "ai-org/patch-series/handoff-members"
    questions = {
        "questions_the_patch_must_answer": [
            {"objection_id": "approach:atomic", "executable_check": "true"}
        ]
    }
    experience = {
        "implementation_experience_reports": [{
            "objection_id": "approach:atomic",
            "claim": "preflight precedes publication",
            "executable_check": "true",
            "check_failure_evidence": "",
            "invalidated_node_ids": [],
            "contingency_plan": "preserve the failed worktree",
            "rewind_class": "requires_direction_change",
            "reported_from_node_key": "follow-up-08",
        }]
    }

    receive_module._write_patch_series_branch(
        repo,
        branch,
        "main",
        _patch_series_view("Handoff members"),
        extra_files={
            contributor_handoff.QUESTIONS_PATH: questions,
            contributor_handoff.EXPERIENCE_PATH: experience,
        },
    )

    assert contributor_handoff.parse_questions(
        _git(repo, "show", f"{branch}:{contributor_handoff.QUESTIONS_PATH}") + "\n"
    ) == questions
    assert contributor_handoff.parse_experience(
        _git(repo, "show", f"{branch}:{contributor_handoff.EXPERIENCE_PATH}") + "\n"
    ) == experience
    assert int(_git(repo, "rev-list", "--count", f"main..{branch}")) == 1


def test_handoff_preflight_failure_does_not_create_target_branch(tmp_path, monkeypatch):
    from ai_org import contributor_handoff

    repo = _init_repo(tmp_path)
    branch = "ai-org/patch-series/rejected-handoff"

    def reject_experience(_value):
        raise ValueError("injected experience codec rejection")

    monkeypatch.setattr(contributor_handoff, "prepare_experience", reject_experience)
    with pytest.raises(ValueError, match="injected experience codec rejection"):
        receive_module._write_patch_series_branch(
            repo,
            branch,
            "main",
            _patch_series_view("Rejected handoff"),
            extra_files={
                contributor_handoff.EXPERIENCE_PATH: {
                    "implementation_experience_reports": []
                }
            },
        )

    assert not git_wrapper.branch_exists(repo, branch)
    assert _git(repo, "symbolic-ref", "--short", "HEAD") == "main"
    assert _git(repo, "status", "--porcelain") == ""


def test_handoff_preflight_rejects_contract_override_before_publication(tmp_path):
    from ai_org import contributor_handoff

    repo = _init_repo(tmp_path)
    branch = "ai-org/patch-series/overridden-handoff"

    with pytest.raises(ValueError, match="written only by the promotion harness"):
        receive_module._write_patch_series_branch(
            repo,
            branch,
            "main",
            _patch_series_view("Overridden handoff"),
            extra_files={contributor_handoff.RESULT_CONTRACT_PATH: "not canonical"},
        )

    assert not git_wrapper.branch_exists(repo, branch)
    assert _git(repo, "status", "--porcelain") == ""


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


def _write_review_round(
    repo: Path,
    patch_series_id: str,
    objection: dict[str, object] | list[dict[str, object]],
    *,
    round_number: int = 1,
) -> None:
    directory = repo / ".ai-org" / "review" / patch_series_id
    directory.mkdir(parents=True, exist_ok=True)
    objections = objection if isinstance(objection, list) else [objection]
    record = {
        "patch_series_id": patch_series_id,
        "branch": f"ai-org/patch-series/{patch_series_id}",
        "round": round_number,
        "objections": objections,
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
        "resolution_authority": "author",
        "status": "open",
    }


def _reform_approach_tree(first_slice: str) -> dict[str, object]:
    return {
        "problem": {
            "id": "problem",
            "problem": "Review needs a concrete patch plan.",
            "affected": "patch series authors.",
            "current_inadequacy": "The plan is vague.",
            "goals": [
                {
                    "id": "goal:1",
                    "requires_deliverable": True,
                    "actor": "maintainer",
                    "capability": {
                        "action": "Inspect the revised patch plan.",
                        "preconditions": ["The reform candidate is frozen."],
                    },
                    "verifiable_outcome": {
                        "expected_state": "The first slice is reviewable.",
                        "evidence": "The reform transition test passes.",
                    },
                    "verification": {
                        "method": "automated_test",
                        "check": "Read the canonical successor root.",
                    },
                    "ux_trace": "none",
                }
            ],
            "deliverable_requirements": [
                {
                    "id": "requirement:1",
                    "referee_goal_id": "goal:1",
                    "production_obligation_id": "obligation:1",
                    "deliverable": "technical-approach-plan.cue",
                }
            ],
            "production_obligations": [
                {
                    "id": "obligation:1",
                    "referee_goal_id": "goal:1",
                    "deliverable_requirement_id": "requirement:1",
                    "deliverable": "technical-approach-plan.cue",
                    "eligibility_predicate": "Can produce and test the canonical body.",
                    "replacement_links": [],
                }
            ],
            "patch_plan": [
                {
                    "item_id": "patch_plan:candidate:one#first-proof-moment",
                    "production_obligation_ids": ["obligation:1"],
                }
            ],
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
                        "main_changes": ["Update patch series files."],
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


def _patch_series_view(
    working_title: str,
    *,
    raw_request: str | None = None,
    problem_or_motivation: str = "Requests need a grounded entrance form.",
    intended_users_or_jobs: str = "Contributors opening a request.",
    desired_outcomes_success: str = "patch series formation starts from grounded registry data.",
    affected_area_platform: str = "ai_org.patchwork_queue",
    background_facts: str = "The request targets the patch series receive flow.",
    grounding_provenance: str = "Test fixture grounding.",
    proposal_hint: str = "Commit the validated registry patch series as patch-series-cover-letter.json.",
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


def _not_user_facing_ux() -> dict[str, object]:
    ux = _ux_requirements()
    ux["applicability"] = {
        "applicability": "not_user_facing",
        "not_user_facing_reason": "This patch series only changes internal machinery and has no user-facing surface.",
    }
    for section in (
        "experience_identity",
        "presentation_model",
        "core_status_surfaces",
        "entity_affordances",
        "progression_legibility",
        "hud_and_ui_flow",
        "visual_language_constraints",
        "accessibility_baseline",
    ):
        ux[section] = {key: "" for key in ux[section]}
    ux["action_feedback_matrix"] = []
    ux["acceptance_tests"] = {"screenshot_checks": [], "interaction_checks": [], "playtest_checks": []}
    return ux


def _fake_spine_lookup(term, context=None, kind=None):
    return {
        "term": term,
        "candidates": [
            {
                "source_url": f"reference://{str(term).replace(' ', '-')}",
                "delta_claim": f"Facet citation for {term}.",
            }
        ],
    }


def _grounding_lint_violations(request: dict[str, object], patch_series_view: dict[str, object]) -> list[str]:
    return receive_module._lint_grounding(request, patch_series_view, GroundingResult(patch_series_view, ""))


def _verify_with_latest_default(
    monkeypatch: pytest.MonkeyPatch,
    request: dict[str, object],
    patch_series_view: dict[str, object],
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
            "reasons": [] if latest_default else ["The grounded patch series targets an outdated version without requester intent."],
        }

    _install_codex_fake(monkeypatch, handler)
    return receive_module._verify_grounding(request, patch_series_view, GroundingResult(patch_series_view, "Test verifier notes."))


def _verify_with_grounding_verdict(
    monkeypatch: pytest.MonkeyPatch,
    request: dict[str, object],
    patch_series_view: dict[str, object],
    verdict: dict[str, object],
) -> dict[str, object]:
    def handler(cmd):
        assert _schema_kind(cmd[cmd.index("--output-schema") + 1]) == "verifier"
        return verdict

    _install_codex_fake(monkeypatch, handler)
    return receive_module._verify_grounding(request, patch_series_view, GroundingResult(patch_series_view, "Test verifier notes."))


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
        receive_module.engineering_precedent_store,
        "build_from_patch_series",
        lambda *args, **kwargs: {"terms": {}, "processed_terms": ["battle loop"], "expanded": [], "hits": [], "failed": {}},
    )
    monkeypatch.setattr(receive_module.engineering_precedent_store, "start_background_build", lambda *args, **kwargs: object())
    monkeypatch.setattr(
        receive_module,
        "form_technical_approach",
        lambda *args, **kwargs: {"ok": True, "technical_approach": _approach_tree()},
    )


def _approach_tree() -> dict[str, object]:
    approach = _reform_approach_tree(
        "Write technical-approach-plan.cue beside the canonical root cohort."
    )
    implementation = approach["problem"]["question"]["decision"]["implementation"]
    implementation["domain_specification"] = {
        "id": "domain_specification",
        "aspects": [
            {
                "id": "domain_specification:battle-numbers",
                "aspect_name": "battle numbers",
                "applicability": "applies",
                "specification_body": "Battle numbers are declared for checkpoint tests.",
                "quantities": [{"name": "damage", "value": "4", "unit": "hp"}],
                "tables": [],
                "sources": ["test fixture"],
            }
        ],
    }
    return approach


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
    assert "required_at=patch_series_handoff" in prompt
    assert "working_title" in prompt
    assert "short noun phrase naming the deliverable" in prompt
