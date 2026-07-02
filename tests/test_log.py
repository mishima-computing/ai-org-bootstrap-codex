from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

import ai_org.log as org_log


def test_emit_record_shape_hash_chain_and_sequences(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_ORG_LOG_HEARTBEAT_INTERVAL", "0")
    ctx = org_log.RunContext(repo=tmp_path, run_id="run-20260703T010203Z-test")

    first = org_log.emit("unit.started", {"value": 1}, ctx=ctx)
    second = org_log.emit("unit.completed", {"value": 2}, ctx=ctx)

    lines = _supervisor_lines(tmp_path, ctx.run_id)
    assert len(lines) == 2
    raw_first = lines[0].rstrip(b"\n")
    assert second["prev_record_hash"] == hashlib.sha256(raw_first).hexdigest()
    assert first["event_id"] < second["event_id"]
    assert first["writer"]["seq"] < second["writer"]["seq"]
    assert first["stream"]["seq"] == 1
    assert second["stream"]["seq"] == 2
    assert first["payload"] == {"value": 1}
    assert first["stream"]["repo"] == str(tmp_path.resolve())


def test_payload_overflow_writes_artifact_ref(tmp_path):
    ctx = org_log.RunContext(repo=tmp_path, run_id="run-20260703T010204Z-overflow")
    record = org_log.emit("large.payload", {"body": "x" * (org_log.HARD_PAYLOAD_BYTES + 1)}, ctx=ctx)

    payload_ref = record["payload_ref"]
    assert payload_ref["truncated"] is True
    assert payload_ref["payload_bytes"] > org_log.HARD_PAYLOAD_BYTES
    artifact = _run_dir(tmp_path, ctx.run_id) / payload_ref["path"]
    assert artifact.exists()
    assert hashlib.sha256(artifact.read_bytes()).hexdigest() == payload_ref["payload_sha256"]


def test_jsonl_reader_tolerates_partial_trailing_line(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_bytes(b'{"event_type":"ok"}\n{"event_type":')

    assert list(org_log.iter_jsonl(path)) == [{"event_type": "ok"}]


def test_span_nesting_and_failure_status(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_ORG_LOG_HEARTBEAT_INTERVAL", "0")
    ctx = org_log.RunContext(repo=tmp_path, run_id="run-20260703T010205Z-span")

    with pytest.raises(RuntimeError):
        with org_log.span("outer", ctx) as outer:
            with org_log.span("inner", outer):
                raise RuntimeError("boom")

    events = [json.loads(line) for line in _supervisor_lines(tmp_path, ctx.run_id)]
    types = [event["event_type"] for event in events]
    assert types == ["outer.started", "inner.started", "inner.failed", "outer.failed"]
    assert events[1]["parent_span_id"] == events[0]["span_id"]
    assert events[-1]["payload"]["error_type"] == "RuntimeError"


def test_logged_subprocess_records_boundaries_and_artifacts(tmp_path):
    ctx = org_log.RunContext(repo=tmp_path, run_id="run-20260703T010206Z-subprocess")
    completed = org_log.logged_subprocess(
        [sys.executable, "-c", "import sys; print('out'); print('err', file=sys.stderr)"],
        ctx=ctx,
        capture_policy={"inline_bytes": 8},
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    events = [json.loads(line) for line in _supervisor_lines(tmp_path, ctx.run_id)]
    assert [event["event_type"] for event in events] == ["subprocess.started", "subprocess.completed"]
    payload = events[-1]["payload"]
    assert payload["stdout_bytes"] == 4
    assert payload["stderr_bytes"] == 4
    assert payload["stdout_head"] == "out\n"
    assert (_run_dir(tmp_path, ctx.run_id) / payload["stdout_ref"]["path"]).read_text(encoding="utf-8") == "out\n"


def test_run_status_died_inferred_from_stale_heartbeat(tmp_path):
    ctx = org_log.RunContext(repo=tmp_path, run_id="run-20260703T010207Z-stale")
    org_log.emit("heartbeat", {"span": "work"}, ctx=ctx)
    path = _supervisor_path(tmp_path, ctx.run_id)
    record = json.loads(path.read_text(encoding="utf-8"))
    old = (datetime.now(UTC) - timedelta(seconds=10)).isoformat().replace("+00:00", "Z")
    record["observed_at"] = old
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")

    projections = org_log.rebuild_projections(tmp_path, ctx.run_id, stale_after_seconds=1)

    assert projections["run_status"]["status"] == "died_inferred"


def test_projections_rebuild_from_log_alone_and_tail_cli(tmp_path):
    ctx = org_log.RunContext(repo=tmp_path, run_id="run-20260703T010208Z-projection")
    org_log.emit(
        "approach.step.completed",
        {"step": "normalize_problem", "duration_seconds": 1.25, "technical_approach": {"problem": {}}, "current_step": None},
        ctx=ctx.child(stage="approach", step="normalize_problem"),
    )
    org_log.emit("rfc.produce.completed", {"duration_seconds": 1.5}, ctx=ctx)

    projections = org_log.rebuild_projections(tmp_path, ctx.run_id)

    assert projections["run_status"]["status"] == "succeeded"
    assert projections["progress_snapshot"]["progress"]["steps_done"] == ["normalize_problem"]
    assert any(row["duration_seconds"] == 1.5 for row in projections["timing_table"]["rows"])
    assert "run-20260703T010208Z-projection succeeded" in projections["canonical_run_line"]["line"]

    result = subprocess.run(
        [sys.executable, "-m", "ai_org.log", "tail", str(tmp_path), ctx.run_id],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "run-20260703T010208Z-projection succeeded" in result.stdout


def _run_dir(repo: Path, run_id: str) -> Path:
    return repo / ".ai-org" / "log" / "runs" / "2026-07-03" / run_id


def _supervisor_path(repo: Path, run_id: str) -> Path:
    return _run_dir(repo, run_id) / "supervisor.jsonl"


def _supervisor_lines(repo: Path, run_id: str) -> list[bytes]:
    return _supervisor_path(repo, run_id).read_bytes().splitlines(keepends=True)
