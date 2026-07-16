"""Pilot integration tests for the deterministic-structure repair round.

Wiring under test (receive._select_approach): the first attempt is free-form and
unchanged — no skeleton scaffolding in any normal step prompt. Only after the
typed contract failure "select_approach requires one evaluation per candidate
from step 5" does ONE bounded repair round run: the code computes the missing
evaluation slots, the model is asked for ONLY those fragments, the merge happens
code-side, and the contract is re-validated. A second failure falls back to
today's typed needs_work error.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import ai_org.log as org_log
from ai_org.patchwork_queue import codex_exec
from ai_org.patchwork_queue import receive as receive_module

REPAIR_LABEL = "Codex evaluation matrix repair"


def _candidates() -> dict[str, Any]:
    return {"candidates": [{"id": "cand_alpha"}, {"id": "cand_beta"}]}


def _constraints() -> dict[str, Any]:
    return {"hard_constraints": [], "soft_preferences": []}


def _evaluation(candidate_id: str) -> dict[str, Any]:
    return {
        "candidate_id": candidate_id,
        "scores": {
            field: {"rating": "high", "reason": "Scored from the evaluation matrix."}
            for field in receive_module.CANDIDATE_EVALUATION_SCORE_FIELDS
        },
    }


def _decision() -> dict[str, Any]:
    return {
        "selected_candidate_id": "cand_alpha",
        "arguments": [
            {
                "role": "support",
                "about_candidate_id": "cand_alpha",
                "claim": "Alpha fits the requested loop.",
                "grounds": "It has high problem and repo fit.",
                "warrant": "The chosen approach should satisfy the goal while matching the repository.",
                "backing": "The prior-art map and constraints support it.",
                "rebuttal": "It costs more than the other patch.",
            },
            {
                "role": "objection",
                "about_candidate_id": "cand_beta",
                "claim": "Beta leaves less room for repo-native ownership.",
                "grounds": "Its problem fit is weaker.",
                "warrant": "The first proof moment loop should establish the module path.",
                "backing": "Repository constraints prefer local ownership.",
                "rebuttal": "Beta would be cheaper.",
            },
        ],
        "rationale": {
            "because": ["It covers the named loop."],
            "under_constraints": ["It keeps the work repo-native."],
            "accepting_tradeoffs": ["It adds more implementation detail."],
        },
        "stack_axes": {
            "fidelity_precedent": {"evidence": "Reference precedent exists.", "judgment": "Use it as evidence."},
        },
        "rejected": [{"candidate_id": "cand_beta", "objection": "Lower problem fit."}],
        "open_questions": [],
    }


def _capture_run_json(monkeypatch, repair_raw: str, decision_raw: str | None = None):
    calls: list[dict[str, Any]] = []

    def fake_run_json(repo, **kwargs):
        calls.append(kwargs)
        if kwargs["failure_label"] == REPAIR_LABEL:
            return {"ok": True, "raw": repair_raw}
        return {"ok": True, "raw": decision_raw if decision_raw is not None else json.dumps(_decision())}

    monkeypatch.setattr(receive_module.codex_exec, "run_json", fake_run_json)
    return calls


def test_missing_evaluation_triggers_one_repair_round_that_requests_only_the_missing_fragment(
    monkeypatch, tmp_path
):
    calls = _capture_run_json(monkeypatch, json.dumps({"evaluations": [_evaluation("cand_beta")]}))

    result = receive_module._select_approach(
        _candidates(),
        {"evaluations": [_evaluation("cand_alpha")]},
        _constraints(),
        {"repo": tmp_path},
        {},
    )

    assert result["selected_candidate_id"] == "cand_alpha"
    assert [call["failure_label"] for call in calls] == [REPAIR_LABEL, "Codex approach selection"]

    # Ratified: repair runs at LOW reasoning effort — the fragment is bounded,
    # skeleton-guided work; full-effort re-derivation is waste. The selection
    # call keeps the configured default (no override).
    assert calls[0]["reasoning_effort"] == "low"
    assert "reasoning_effort" not in calls[1]

    repair_prompt = calls[0]["prompt"]
    # The repair round references ONLY the missing candidate id, never the ones
    # whose evaluations already passed validation.
    assert "cand_beta" in repair_prompt
    assert "cand_alpha" not in repair_prompt
    # It carries the code-generated skeleton fragment for the missing slot: the id,
    # every score field name, and null placeholders — no prefilled judgment.
    assert '"candidate_id": "cand_beta"' in repair_prompt
    for field in receive_module.CANDIDATE_EVALUATION_SCORE_FIELDS:
        assert field in repair_prompt
    assert '"rating": null' in repair_prompt
    # The repair schema closes candidate_id over the missing ids only.
    repair_schema = calls[0]["schema"]
    assert repair_schema["properties"]["evaluations"]["items"]["properties"]["candidate_id"]["enum"] == [
        "cand_beta"
    ]

    # The merged matrix (existing slot + repaired slot) reaches the unchanged
    # selection prompt; no skeleton scaffolding leaks into normal step prompts.
    selection_prompt = calls[1]["prompt"]
    assert '"candidate_id": "cand_alpha"' in selection_prompt
    assert '"candidate_id": "cand_beta"' in selection_prompt
    assert "skeleton" not in selection_prompt


def test_first_attempt_prompt_is_unchanged_free_form_without_skeleton_content():
    # Gadgets deploy reactively by error type, never as always-on scaffolding: the
    # normal select_approach prompt must not carry skeleton framing or placeholders.
    prompt = receive_module._select_approach_prompt(
        _candidates(),
        {"evaluations": [_evaluation("cand_alpha"), _evaluation("cand_beta")]},
        _constraints(),
        {},
        {},
    )
    assert "skeleton" not in prompt
    assert '"rating": null' not in prompt


def test_full_coverage_never_invokes_the_repair_round(monkeypatch, tmp_path):
    calls = _capture_run_json(monkeypatch, json.dumps({"evaluations": []}))

    result = receive_module._select_approach(
        _candidates(),
        {"evaluations": [_evaluation("cand_alpha"), _evaluation("cand_beta")]},
        _constraints(),
        {"repo": tmp_path},
        {},
    )

    assert result["selected_candidate_id"] == "cand_alpha"
    assert [call["failure_label"] for call in calls] == ["Codex approach selection"]


def test_repair_round_that_still_misses_coverage_falls_back_to_typed_needs_work(monkeypatch, tmp_path):
    # The repair model returns an empty fragment: coverage is still incomplete, and
    # exactly one repair round is allowed — no selection call, no second repair.
    calls = _capture_run_json(monkeypatch, json.dumps({"evaluations": []}))

    result = receive_module._select_approach(
        _candidates(),
        {"evaluations": [_evaluation("cand_alpha")]},
        _constraints(),
        {"repo": tmp_path},
        {},
    )

    assert result == {
        "ok": False,
        "error": "select_approach requires one evaluation per candidate from step 5",
    }
    assert [call["failure_label"] for call in calls] == [REPAIR_LABEL]


def _capture_codex_cmd(monkeypatch) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    def fake_logged_subprocess(cmd, **kwargs):
        captured["cmd"] = list(cmd)
        out_file = Path(cmd[cmd.index("-o") + 1])
        out_file.write_text("{}", encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(org_log, "logged_subprocess", fake_logged_subprocess)
    return captured


def _run_json(tmp_path, **overrides) -> dict[str, Any]:
    return codex_exec.run_json(
        tmp_path,
        schema={"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        prompt="return json",
        schema_filename="schema.json",
        output_filename="out.json",
        failure_label="Codex reasoning effort test",
        ctx=org_log.RunContext(repo=tmp_path, run_id="run-reasoning-effort-test"),
        **overrides,
    )


def test_run_json_reasoning_effort_appends_config_override_before_the_prompt(monkeypatch, tmp_path):
    captured = _capture_codex_cmd(monkeypatch)

    result = _run_json(tmp_path, reasoning_effort="low")

    assert result == {"ok": True, "raw": "{}"}
    cmd = captured["cmd"]
    index = cmd.index("-c")
    assert cmd[index + 1] == 'model_reasoning_effort="low"'
    # The override precedes the prompt positional.
    assert index + 1 < cmd.index("return json")
    assert cmd[-1] == "return json"


def test_run_json_without_reasoning_effort_leaves_the_cmd_unchanged(monkeypatch, tmp_path):
    captured = _capture_codex_cmd(monkeypatch)

    result = _run_json(tmp_path)

    assert result == {"ok": True, "raw": "{}"}
    cmd = captured["cmd"]
    assert "-c" not in cmd
    assert not any("model_reasoning_effort" in part for part in cmd)


def test_repair_round_with_invalid_fragment_falls_back_to_typed_needs_work(monkeypatch, tmp_path):
    # The fragment covers the missing id but violates the step-5 slot contract
    # (empty reason strings): the repaired slot is rejected, and the round falls back.
    broken = _evaluation("cand_beta")
    broken["scores"]["risk"]["reason"] = ""
    calls = _capture_run_json(monkeypatch, json.dumps({"evaluations": [broken]}))

    result = receive_module._select_approach(
        _candidates(),
        {"evaluations": [_evaluation("cand_alpha")]},
        _constraints(),
        {"repo": tmp_path},
        {},
    )

    assert result == {
        "ok": False,
        "error": "select_approach requires one evaluation per candidate from step 5",
    }
    assert [call["failure_label"] for call in calls] == [REPAIR_LABEL]
