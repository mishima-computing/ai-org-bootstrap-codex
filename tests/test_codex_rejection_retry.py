from __future__ import annotations

import json
from pathlib import Path
import subprocess

import ai_org.codex_reset as codex_reset
import ai_org.log as org_log
from ai_org.patchwork_queue import receive as receive_module
import ai_org.patchwork_queue.codex_exec as codex_exec


def test_identical_rejection_fingerprints_abort_permanent(monkeypatch, tmp_path):
    attempts = []
    bad = _decision()
    bad["stack_axes"]["fidelity_precedent"] = {"evidence": "", "judgment": "Use it as evidence."}

    def fake_run_json(repo: Path, **kwargs):
        attempts.append(kwargs["prompt"])
        return {"ok": True, "raw": json.dumps(bad)}

    monkeypatch.setattr(receive_module.codex_exec, "run_json", fake_run_json)

    result = receive_module._select_approach(_candidates(), _evaluations(), _constraints(), {"repo": tmp_path}, {})

    assert result["ok"] is False
    assert result["failure_mode"] == "permanent_rejection"
    assert result["validator_id"] == "receive.select_approach"
    assert result["path"] == "/stack_axes/fidelity_precedent/evidence"
    assert result["json_pointer"] == "/stack_axes/fidelity_precedent/evidence"
    assert len(attempts) == 2


def test_differing_rejection_fingerprints_retry_with_error_injection(monkeypatch, tmp_path):
    first = _decision()
    first["stack_axes"]["fidelity_precedent"] = {"evidence": "", "judgment": "Use it as evidence."}
    second = _decision()
    second["stack_axes"]["fidelity_precedent"] = {"evidence": "Reference precedent exists.", "judgment": ""}
    attempts = [first, second, _decision()]
    prompts = []

    def fake_run_json(repo: Path, **kwargs):
        prompts.append(kwargs["prompt"])
        return {"ok": True, "raw": json.dumps(attempts.pop(0))}

    monkeypatch.setattr(receive_module.codex_exec, "run_json", fake_run_json)

    result = receive_module._select_approach(_candidates(), _evaluations(), _constraints(), {"repo": tmp_path}, {})

    assert result["selected_candidate_id"] == "repo_native"
    assert len(prompts) == 3
    assert "Previous deterministic validator rejection:" in prompts[1]
    assert "validator_id: receive.select_approach" in prompts[1]
    assert "path: /stack_axes/fidelity_precedent/evidence" in prompts[1]
    assert "path: /stack_axes/fidelity_precedent/judgment" in prompts[2]


def test_transient_codex_errors_keep_existing_failure_behavior(monkeypatch, tmp_path):
    def fake_logged_subprocess(*args, **kwargs):
        raise OSError("temporary subprocess failure")

    monkeypatch.setattr(org_log, "logged_subprocess", fake_logged_subprocess)

    result = codex_exec.run_json(
        tmp_path,
        schema={"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        prompt="return json",
        schema_filename="schema.json",
        output_filename="out.json",
        failure_label="Codex transient test",
        ctx=org_log.RunContext(repo=tmp_path, run_id="run-transient-test"),
    )

    assert result == {"ok": False, "error": "Codex transient test failed: temporary subprocess failure"}
    projections = org_log.rebuild_projections(tmp_path, "run-transient-test")
    attempts = projections["attempt_history"]["attempts"]
    assert attempts[-1]["classification"] == "transient"
    assert attempts[-1]["status"] == "failed"


def test_usage_limit_retries_are_bounded_and_preserve_failure(monkeypatch, tmp_path):
    calls = 0
    waits: list[str] = []

    def fake_logged_subprocess(cmd, **_kwargs):
        nonlocal calls
        calls += 1
        return subprocess.CompletedProcess(
            cmd,
            1,
            stdout="You've hit your usage limit; try again at 10:18 PM.\n",
            stderr="",
        )

    monkeypatch.setattr(org_log, "logged_subprocess", fake_logged_subprocess)
    monkeypatch.setattr(
        codex_reset,
        "wait_for_codex_reset",
        lambda _not_before, *, ctx, event_name: waits.append(event_name),
    )

    result = codex_exec.run_json(
        tmp_path,
        schema={"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        prompt="return json",
        schema_filename="schema.json",
        output_filename="out.json",
        failure_label="Codex bounded reset test",
        ctx=org_log.RunContext(repo=tmp_path, run_id="run-bounded-reset-test"),
    )

    assert calls == 5
    assert waits == ["patchwork_queue.codex_reset_wait"] * codex_reset.MAX_RESET_RETRIES
    assert result == {"ok": False, "error": "Codex bounded reset test failed: no output file"}


def test_capacity_transient_restarts_without_a_session_and_succeeds(monkeypatch, tmp_path):
    calls: list[list[str]] = []
    waits: list[str] = []

    def fake_logged_subprocess(cmd, **_kwargs):
        calls.append(list(cmd))
        if len(calls) == 1:
            return subprocess.CompletedProcess(
                cmd,
                1,
                stdout="",
                stderr="ERROR: Selected model is at capacity. Please try again later.\n",
            )
        out_file = Path(cmd[cmd.index("-o") + 1])
        out_file.write_text("{}", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(org_log, "logged_subprocess", fake_logged_subprocess)
    monkeypatch.setattr(
        codex_reset,
        "wait_for_transient",
        lambda _attempt, *, ctx, event_name: waits.append(event_name),
    )

    result = codex_exec.run_json(
        tmp_path,
        schema={"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        prompt="return json",
        schema_filename="schema.json",
        output_filename="out.json",
        failure_label="Codex capacity retry test",
        ctx=org_log.RunContext(repo=tmp_path, run_id="run-capacity-retry-test"),
    )

    assert result == {"ok": True, "raw": "{}"}
    assert len(calls) == 2
    assert calls[1] == calls[0]
    assert "resume" not in calls[1]
    assert waits == ["patchwork_queue.codex_reset_wait"]


def test_capacity_transient_exhaustion_returns_typed_failure_marker(monkeypatch, tmp_path):
    calls: list[list[str]] = []
    waits: list[str] = []

    def fake_logged_subprocess(cmd, **_kwargs):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(
            cmd,
            1,
            stdout="",
            stderr="ERROR: Selected model is at capacity. Please try again later.\n",
        )

    monkeypatch.setattr(org_log, "logged_subprocess", fake_logged_subprocess)
    monkeypatch.setattr(
        codex_reset,
        "wait_for_transient",
        lambda _attempt, *, ctx, event_name: waits.append(event_name),
    )

    result = codex_exec.run_json(
        tmp_path,
        schema={"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        prompt="return json",
        schema_filename="schema.json",
        output_filename="out.json",
        failure_label="Codex capacity exhaustion test",
        ctx=org_log.RunContext(repo=tmp_path, run_id="run-capacity-exhaustion-test"),
    )

    assert result["ok"] is False
    assert result["failure_mode"] == "transient-retries-exhausted"
    assert len(calls) == codex_reset.MAX_TRANSIENT_RETRIES + 1
    assert len(waits) == codex_reset.MAX_TRANSIENT_RETRIES
    assert all("resume" not in cmd for cmd in calls)


def test_rejection_fingerprint_stability():
    first = codex_exec.rejection_fingerprint(
        "validator", "empty_slot", "/stack_axes/fidelity_precedent/evidence", "required", "evidence"
    )
    second = codex_exec.rejection_fingerprint(
        "validator", "empty_slot", "stack_axes.fidelity_precedent.evidence", "required", "evidence"
    )
    other_path = codex_exec.rejection_fingerprint(
        "validator", "empty_slot", "/stack_axes/fidelity_precedent/judgment", "required", "evidence"
    )
    other_subject = codex_exec.rejection_fingerprint(
        "validator", "empty_slot", "/stack_axes/fidelity_precedent/evidence", "required", "judgment"
    )

    assert first == second
    assert first != other_path
    assert first != other_subject


def test_fingerprint_atomization_different_marker_atom_is_recoverable():
    state = codex_exec.RejectionRetryState()
    first = codex_exec.validation_rejection(
        "receive.grounding_contract",
        [
            "C1 faithfulness/specificity: grounded target uses 'generic'",
            "C2 full scope: grounded target uses 'minimal'",
        ],
    )
    second = codex_exec.validation_rejection(
        "receive.grounding_contract",
        "C1 faithfulness/specificity: grounded target uses '-style'",
    )

    first_decision = state.classify(first, attempt=1)
    second_decision = state.classify(second, attempt=2)

    assert first_decision.classification == "stochastic-recoverable"
    assert second_decision.classification == "stochastic-recoverable"
    assert not second_decision.permanent
    assert len(first.atom_fingerprints) == 2
    assert second.atom_fingerprints[0] not in set(first.atom_fingerprints)


def test_fingerprint_atomization_same_atom_repeating_is_permanent():
    state = codex_exec.RejectionRetryState()
    first = codex_exec.validation_rejection(
        "receive.grounding_contract",
        "C1 faithfulness/specificity: grounded target uses 'generic'",
    )
    second = codex_exec.validation_rejection(
        "receive.grounding_contract",
        "C1 faithfulness/specificity: grounded target uses 'generic'",
    )

    state.classify(first, attempt=1)
    decision = state.classify(second, attempt=2)

    assert decision.classification == "permanent"
    assert decision.permanent is True
    assert decision.repeated_fingerprints == first.atom_fingerprints


def test_fingerprint_atomization_same_marker_at_different_json_pointer_is_fresh():
    state = codex_exec.RejectionRetryState()
    first = codex_exec.validation_rejection(
        "receive.grounding_contract",
        "C1 faithfulness/specificity: working title uses 'generic'",
        json_pointer="/working_title",
        subject_atom="generic",
    )
    second = codex_exec.validation_rejection(
        "receive.grounding_contract",
        "C1 faithfulness/specificity: desired outcome uses 'generic'",
        json_pointer="/desired_outcomes_success",
        subject_atom="generic",
    )

    state.classify(first, attempt=1)
    decision = state.classify(second, attempt=2)

    assert decision.classification == "stochastic-recoverable"
    assert not decision.permanent
    assert first.atom_fingerprints != second.atom_fingerprints


def test_multi_error_attempt_stores_atom_set_in_projection(tmp_path):
    state = codex_exec.RejectionRetryState()
    rejection = codex_exec.validation_rejection(
        "receive.grounding_contract",
        [
            "C1 faithfulness/specificity: grounded target uses 'generic'",
            "C2 full scope: grounded target uses 'minimal'",
        ],
    )

    state.classify(rejection, attempt=1, ctx=org_log.RunContext(repo=tmp_path, run_id="run-atom-set-test"))

    projections = org_log.rebuild_projections(tmp_path, "run-atom-set-test")
    attempts = projections["attempt_history"]["attempts"]
    last = attempts[-1]
    assert last["fingerprint_set"] == list(rejection.atom_fingerprints)
    assert last["fingerprint"] == rejection.atom_fingerprints[0]
    assert len(last["defect_atoms"]) == 2


def _candidates() -> dict[str, object]:
    return {"candidates": [{"id": "minimal"}, {"id": "repo_native"}]}


def _evaluations() -> dict[str, object]:
    return {"evaluations": [{"candidate_id": "minimal"}, {"candidate_id": "repo_native"}]}


def _constraints() -> dict[str, object]:
    return {"hard_constraints": [], "soft_preferences": []}


def _decision() -> dict[str, object]:
    return {
        "selected_candidate_id": "repo_native",
        "arguments": [
            {
                "role": "support",
                "about_candidate_id": "repo_native",
                "claim": "Repo Native fits the requested loop.",
                "grounds": "It has high problem and repo fit.",
                "warrant": "The chosen approach should satisfy the goal while matching the repository.",
                "backing": "The prior-art map and constraints support it.",
                "rebuttal": "It costs more than the minimal patch.",
            },
            {
                "role": "objection",
                "about_candidate_id": "minimal",
                "claim": "Minimal leaves less room for repo-native ownership.",
                "grounds": "Its problem fit is weaker.",
                "warrant": "The first proof moment loop should establish the module path.",
                "backing": "Repository constraints prefer local ownership.",
                "rebuttal": "Minimal would be cheaper.",
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
        "rejected": [{"candidate_id": "minimal", "objection": "Lower problem fit."}],
        "open_questions": [],
    }
