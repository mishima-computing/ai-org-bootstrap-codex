from __future__ import annotations

import subprocess

import pytest

import ai_org.codex_reset as codex_reset
import ai_org.log as org_log


SESSION_ID = "019f4f12-c389-7abc-8def-0123456789ab"


@pytest.mark.parametrize(
    "text",
    [
        "ERROR: Selected model is at capacity, please try again later.\n",
        "Selected model is at capacity, please try again later.\n",
        "ERROR: HTTP 429 Too Many Requests\n",
        "ERROR: HTTP 503 Service Unavailable\n",
        "ERROR: stream disconnected before completion\n",
        "ERROR: stream reset\n",
        "ERROR: connection reset by peer\n",
        "ERROR: request failed with HTTP status 503 Service Unavailable\n",
        "ERROR: upstream returned HTTP/2 503 Service Unavailable\n",
        "ERROR: error sending request for url (https://example.test): connection reset by peer (os error 54)\n",
        "ERROR: Selected model is at capacity. Please try again later. Request ID: req-123\n",
    ],
)
def test_classify_transient_accepts_only_announced_provider_failures(text):
    assert codex_reset.classify_transient(text) == "transient"


def test_classify_transient_preserves_timed_reset_precedence():
    text = "You've hit your usage limit; try again at 10:18 PM.\n"

    assert codex_reset.classify_transient(text) == "timed_reset"


@pytest.mark.parametrize(
    "text",
    [
        'Review finding quotes ERROR: Selected model is at capacity, please try again later.\n',
        '> ERROR: HTTP 503 Service Unavailable\n',
        '{"message":"ERROR: stream disconnected before completion"}\n',
        '"ERROR: connection reset by peer"\n',
        "ERROR: invalid output schema\n",
        "ERROR: HTTP 400 Bad Request\n",
        "Selected model does not exist\n",
        "status 503 is the expected fixture value\n",
        "Status code: 500 is covered by the test\n",
        "ERROR: status 503 is the expected fixture value\n",
    ],
)
def test_classify_transient_rejects_quoted_and_ordinary_failures(text):
    assert codex_reset.classify_transient(text) is None


def test_wait_for_transient_uses_deterministic_exponential_backoff(monkeypatch, tmp_path):
    sleeps: list[float] = []
    events: list[tuple[str, dict]] = []
    ctx = org_log.RunContext(repo=tmp_path, run_id="transient-backoff")

    monkeypatch.setattr(codex_reset.time, "sleep", sleeps.append)
    monkeypatch.setattr(
        codex_reset.org_log,
        "emit",
        lambda event_name, payload=None, **_kwargs: events.append((event_name, dict(payload or {}))),
    )

    for attempt in range(1, 7):
        codex_reset.wait_for_transient(
            attempt,
            ctx=ctx,
            event_name="unit.codex_transient_wait",
        )

    assert sleeps == [30.0, 60.0, 120.0, 240.0, 300.0, 300.0]
    assert [event_name for event_name, _payload in events] == ["unit.codex_transient_wait"] * 18
    assert [payload["status"] for _event_name, payload in events] == [
        status
        for _attempt in range(1, 7)
        for status in ("started", "sleeping", "retrying")
    ]
    sleeping_payloads = [payload for _event_name, payload in events if payload["status"] == "sleeping"]
    assert [payload["attempt"] for payload in sleeping_payloads] == list(range(1, 7))
    assert [payload["sleep_seconds"] for payload in sleeping_payloads] == sleeps


def test_transient_retry_limit_reads_environment_override(monkeypatch):
    monkeypatch.setenv("AI_ORG_TRANSIENT_MAX_RETRIES", "3")

    assert codex_reset._configured_max_transient_retries() == 3


def test_codex_resume_command_uses_documented_flag_order_and_continuation_prompt():
    original = [
        "codex",
        "exec",
        "--sandbox",
        "workspace-write",
        "-C",
        "/tmp/worktree",
        "-o",
        "/tmp/last-message.txt",
        "--output-schema",
        "/tmp/result.schema.json",
        "Implement the requested change.",
    ]

    resumed = codex_reset.codex_resume_command(original, SESSION_ID)

    assert resumed == [
        "codex",
        "exec",
        "--sandbox",
        "workspace-write",
        "-C",
        "/tmp/worktree",
        "--output-schema",
        "/tmp/result.schema.json",
        "resume",
        "--json",
        SESSION_ID,
        "-o",
        "/tmp/last-message.txt",
        "Continue where you left off.",
    ]
    assert original[-1] == "Implement the requested change."


def test_session_extraction_rejects_payload_values_and_resume_flags():
    payload = '{"type":"item.completed","payload":{"session_id":"--last"}}\n'

    assert codex_reset.extract_session_ids(payload) == ()
    with pytest.raises(ValueError, match="session UUID"):
        codex_reset.codex_resume_command(["codex", "exec", "prompt"], "--last")


def test_transient_retry_resumes_when_thread_id_was_announced(monkeypatch, tmp_path):
    run_calls = 0
    resumed_sessions: list[str] = []
    waits: list[int] = []
    events: list[tuple[str, dict]] = []
    failed = _completed(
        1,
        stdout=f'{{"type":"thread.started","thread_id":"{SESSION_ID}"}}\n',
        stderr="ERROR: Selected model is at capacity, please try again later.\n",
    )
    succeeded = _completed(0, stdout="completed\n")

    def run_once():
        nonlocal run_calls
        run_calls += 1
        return failed

    def resume_once(session_id: str):
        resumed_sessions.append(session_id)
        return succeeded

    monkeypatch.setattr(
        codex_reset,
        "wait_for_transient",
        lambda attempt, *, ctx, event_name: waits.append(attempt),
    )
    monkeypatch.setattr(
        codex_reset.org_log,
        "emit",
        lambda event_name, payload=None, **_kwargs: events.append((event_name, dict(payload or {}))),
    )

    result = codex_reset.run_with_reset_retries(
        run_once,
        resume_once=resume_once,
        ctx=org_log.RunContext(repo=tmp_path, run_id="transient-resume"),
        event_name="unit.codex_transient_wait",
        max_transient_retries=2,
    )

    assert result.returncode == 0
    assert run_calls == 1
    assert resumed_sessions == [SESSION_ID]
    assert waits == [1]
    assert list(codex_reset.retry_session_ids(result)) == [SESSION_ID]
    assert codex_reset.transient_retries_exhausted(result) is False
    assert any(
        event_name == "codex.retry_chain.recorded"
        and payload["session_ids"] == [SESSION_ID]
        and payload["retry_modes"] == ["restart", "resume"]
        for event_name, payload in events
    )


def test_transient_retry_restarts_when_no_thread_id_was_announced(monkeypatch, tmp_path):
    attempts = [
        _completed(1, stderr="ERROR: HTTP 503 Service Unavailable\n"),
        _completed(0, stdout="completed\n"),
    ]
    run_calls = 0
    resumed_sessions: list[str] = []

    def run_once():
        nonlocal run_calls
        run_calls += 1
        return attempts.pop(0)

    def resume_once(session_id: str):
        resumed_sessions.append(session_id)
        raise AssertionError("a transient attempt without a thread id must restart")

    monkeypatch.setattr(codex_reset, "wait_for_transient", lambda *_args, **_kwargs: None)

    result = codex_reset.run_with_reset_retries(
        run_once,
        resume_once=resume_once,
        ctx=org_log.RunContext(repo=tmp_path, run_id="transient-restart"),
        event_name="unit.codex_transient_wait",
        max_transient_retries=2,
    )

    assert result.returncode == 0
    assert run_calls == 2
    assert resumed_sessions == []
    assert list(codex_reset.retry_session_ids(result)) == []


def test_transient_retry_exhaustion_is_typed_and_preserves_session_chain(monkeypatch, tmp_path):
    session_ids = [
        "019f4f12-c389-7abc-8def-0123456789a1",
        "019f4f12-c389-7abc-8def-0123456789a2",
        "019f4f12-c389-7abc-8def-0123456789a3",
    ]
    attempts = [
        _completed(
            1,
            stdout=f'{{"type":"thread.started","thread_id":"{session_id}"}}\n',
            stderr="ERROR: HTTP 429 Too Many Requests\n",
        )
        for session_id in session_ids
    ]
    waits: list[int] = []
    events: list[tuple[str, dict]] = []

    monkeypatch.setattr(
        codex_reset,
        "wait_for_transient",
        lambda attempt, *, ctx, event_name: waits.append(attempt),
    )
    monkeypatch.setattr(
        codex_reset.org_log,
        "emit",
        lambda event_name, payload=None, **_kwargs: events.append((event_name, dict(payload or {}))),
    )

    result = codex_reset.run_with_reset_retries(
        lambda: attempts.pop(0),
        ctx=org_log.RunContext(repo=tmp_path, run_id="transient-exhausted"),
        event_name="unit.codex_transient_wait",
        max_transient_retries=2,
    )

    assert result.returncode == 1
    assert waits == [1, 2]
    assert codex_reset.transient_retries_exhausted(result) is True
    assert list(codex_reset.retry_session_ids(result)) == session_ids
    assert any(payload.get("marker") == "transient-retries-exhausted" for _event_name, payload in events)


def test_timed_reset_still_uses_announced_boundary_wait_and_restart(monkeypatch, tmp_path):
    attempts = [
        _completed(1, stderr="You've hit your usage limit; try again at 10:18 PM.\n"),
        _completed(0, stdout="completed\n"),
    ]
    reset_waits: list[str] = []

    monkeypatch.setattr(
        codex_reset,
        "wait_for_codex_reset",
        lambda not_before, *, ctx, event_name: reset_waits.append(event_name),
    )
    monkeypatch.setattr(
        codex_reset,
        "wait_for_transient",
        lambda *_args, **_kwargs: pytest.fail("timed resets must not use transient backoff"),
    )

    result = codex_reset.run_with_reset_retries(
        lambda: attempts.pop(0),
        ctx=org_log.RunContext(repo=tmp_path, run_id="timed-reset-unchanged"),
        event_name="unit.codex_reset_wait",
        max_reset_retries=1,
        max_transient_retries=1,
    )

    assert result.returncode == 0
    assert reset_waits == ["unit.codex_reset_wait"]
    assert codex_reset.transient_retries_exhausted(result) is False


def _completed(returncode: int, *, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        ["codex", "exec"],
        returncode,
        stdout=stdout,
        stderr=stderr,
    )
