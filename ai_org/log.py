"""Append-only AI Org event log and rebuildable projections.

The log is process history. Git stores durable results; this module stores the
off-git event stream used to rebuild progress, timing, status, and tail views.
It is intentionally low-level and must not import RFC, patch, merge, or
reference modules.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
import argparse
import contextvars
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import threading
import time
import uuid
import warnings
from typing import Any, Iterator, Mapping, Sequence


EVENT_VERSION = 1
TARGET_PAYLOAD_BYTES = 16 * 1024
HARD_PAYLOAD_BYTES = 64 * 1024
DEFAULT_HEARTBEAT_INTERVAL_SECONDS = 30.0
DEFAULT_DIED_AFTER_SECONDS = 300.0
_ARTIFACT_INLINE_BYTES = 4096

_HOST = socket.gethostname()
_PID = os.getpid()
_START_ID = f"{int(time.time_ns()):020d}-{uuid.uuid4().hex[:12]}"
_WRITER_SEQ = 0
_WRITER_LOCK = threading.Lock()
_LAST_RECORD_HASH: dict[str, str] = {}
_SPAN_STACK: contextvars.ContextVar[tuple[str, ...]] = contextvars.ContextVar("ai_org_log_span_stack", default=())


@dataclass(frozen=True)
class RunContext:
    """Correlation envelope for one log stream."""

    repo: str | Path
    run_id: str | None = None
    stream_id: str = "supervisor"
    trace_id: str | None = None
    span_id: str | None = None
    parent_span_id: str | None = None
    correlation_id: str | None = None
    request_id: str | None = None
    rfc_id: str | None = None
    rfc_serial: str | int | None = None
    lineage_id: str | None = None
    stage: str | None = None
    step: str | None = None
    attempt: int | None = None

    def __post_init__(self) -> None:
        trace_id = self.trace_id or uuid.uuid4().hex
        correlation_id = self.correlation_id or self.request_id or self.rfc_id or trace_id
        run_id = self.run_id or f"run-{_utc_now().strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
        object.__setattr__(self, "repo", str(Path(self.repo).resolve()))
        object.__setattr__(self, "run_id", run_id)
        object.__setattr__(self, "trace_id", trace_id)
        object.__setattr__(self, "correlation_id", str(correlation_id))

    def child(
        self,
        *,
        stream_id: str | None = None,
        span_id: str | None = None,
        parent_span_id: str | None = None,
        stage: str | None = None,
        step: str | None = None,
        attempt: int | None = None,
        **ids: Any,
    ) -> "RunContext":
        values = {
            "stream_id": stream_id if stream_id is not None else self.stream_id,
            "span_id": span_id if span_id is not None else self.span_id,
            "parent_span_id": parent_span_id if parent_span_id is not None else self.parent_span_id,
            "stage": stage if stage is not None else self.stage,
            "step": step if step is not None else self.step,
            "attempt": attempt if attempt is not None else self.attempt,
            **{key: value for key, value in ids.items() if hasattr(self, key)},
        }
        return replace(self, **values)


def emit(
    event_type: str,
    payload: Mapping[str, Any] | None = None,
    *,
    ctx: RunContext | Mapping[str, Any],
    severity: str = "info",
    causation_event_id: str | None = None,
    payload_ref: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Append one authoritative event record.

    Failure raises: callers should not continue state-bearing work when history
    cannot be recorded.
    """
    context = _coerce_context(ctx)
    occurred = _utc_now()
    event_id = _event_id(occurred)
    stream_path = _stream_path(context)
    run_dir = _run_dir(context)
    payload_obj: Mapping[str, Any] = dict(payload or {})
    normalized_payload, overflow_ref = _normalize_payload(run_dir, event_id, payload_obj)
    if payload_ref is not None:
        overflow_ref = {**dict(payload_ref), **(overflow_ref or {})}

    with _WRITER_LOCK:
        global _WRITER_SEQ
        _WRITER_SEQ += 1
        writer_seq = _WRITER_SEQ
        stream_seq = _next_stream_seq(stream_path)
        chain_key = f"{Path(context.repo).resolve()}:{context.stream_id}"
        prev_hash = _LAST_RECORD_HASH.get(chain_key)

        observed = _utc_now()
        record: dict[str, Any] = {
            "event_id": event_id,
            "event_type": str(event_type),
            "event_version": EVENT_VERSION,
            "occurred_at": _isoformat(occurred),
            "observed_at": _isoformat(observed),
            "severity": str(severity),
            "trace_id": context.trace_id,
            "span_id": context.span_id,
            "parent_span_id": context.parent_span_id,
            "correlation_id": context.correlation_id,
            "writer": {
                "host": _HOST,
                "pid": _PID,
                "start_id": _START_ID,
                "seq": writer_seq,
            },
            "stream": {
                "repo": str(Path(context.repo).resolve()),
                "run_id": context.run_id,
                "stream_id": context.stream_id,
                "seq": stream_seq,
            },
            "payload": normalized_payload,
            "prev_record_hash": prev_hash,
        }
        for key in ("request_id", "rfc_id", "rfc_serial", "lineage_id", "stage", "step", "attempt"):
            value = getattr(context, key)
            if value is not None:
                record[key] = value
        if causation_event_id is not None:
            record["causation_event_id"] = str(causation_event_id)
        if overflow_ref is not None:
            record["payload_ref"] = overflow_ref

        line = _json_dumps(record).encode("utf-8") + b"\n"
        stream_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(stream_path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o644)
        try:
            os.write(fd, line)
            _sync_fd(fd)
        finally:
            os.close(fd)
        record_hash = hashlib.sha256(line.rstrip(b"\n")).hexdigest()
        _LAST_RECORD_HASH[chain_key] = record_hash
        return record


def debug_emit(event_type: str, payload: Mapping[str, Any] | None = None, *, ctx: RunContext, severity: str = "debug") -> None:
    """Best-effort worker/debug event."""
    try:
        emit(event_type, payload, ctx=ctx, severity=severity)
    except Exception as exc:  # pragma: no cover - intentionally defensive
        warnings.warn(f"ai_org.log debug emit failed: {exc}", RuntimeWarning, stacklevel=2)


@contextmanager
def span(name: str, ctx: RunContext | Mapping[str, Any]) -> Iterator[RunContext]:
    """Emit ``<name>.started`` and terminal span events around a block."""
    parent = _coerce_context(ctx)
    stack = _SPAN_STACK.get()
    parent_span_id = parent.span_id or (stack[-1] if stack else parent.parent_span_id)
    span_id = uuid.uuid4().hex
    child = parent.child(span_id=span_id, parent_span_id=parent_span_id)
    token = _SPAN_STACK.set(stack + (span_id,))
    start = time.monotonic()
    started = emit(f"{name}.started", {"name": name}, ctx=child)
    stop_heartbeat = _start_heartbeat(name, child)
    try:
        yield child
    except Exception as exc:
        duration = time.monotonic() - start
        emit(
            f"{name}.failed",
            {"name": name, "duration_seconds": duration, "error": str(exc), "error_type": type(exc).__name__},
            ctx=child,
            severity="error",
            causation_event_id=started["event_id"],
        )
        raise
    else:
        duration = time.monotonic() - start
        emit(
            f"{name}.completed",
            {"name": name, "duration_seconds": duration},
            ctx=child,
            causation_event_id=started["event_id"],
        )
    finally:
        stop_heartbeat.set()
        _SPAN_STACK.reset(token)


def logged_subprocess(
    argv: Sequence[str | os.PathLike[str]],
    *,
    ctx: RunContext | Mapping[str, Any],
    capture_policy: str | Mapping[str, Any] | None = None,
    **kwargs: Any,
) -> subprocess.CompletedProcess:
    """Run a subprocess and log start/completion/failure boundaries."""
    context = _coerce_context(ctx)
    command = [os.fspath(arg) for arg in argv]
    policy = _capture_policy(capture_policy)
    check = bool(kwargs.pop("check", False))
    started_at = time.monotonic()
    started = emit(
        "subprocess.started",
        {"argv": command, "cwd": str(kwargs.get("cwd")) if kwargs.get("cwd") is not None else None},
        ctx=context,
    )
    try:
        completed = subprocess.run(command, check=False, **kwargs)
    except Exception as exc:
        emit(
            "subprocess.failed",
            {
                "argv": command,
                "duration_seconds": time.monotonic() - started_at,
                "error": str(exc),
                "error_type": type(exc).__name__,
            },
            ctx=context,
            severity="error",
            causation_event_id=started["event_id"],
        )
        raise

    payload, payload_ref = _subprocess_payload(context, command, completed, time.monotonic() - started_at, policy)
    terminal_type = "subprocess.completed" if completed.returncode == 0 else "subprocess.failed"
    emit(
        terminal_type,
        payload,
        ctx=context,
        severity="info" if completed.returncode == 0 else "error",
        causation_event_id=started["event_id"],
        payload_ref=payload_ref,
    )
    if check and completed.returncode:
        raise subprocess.CalledProcessError(
            completed.returncode,
            completed.args,
            output=completed.stdout,
            stderr=completed.stderr,
        )
    return completed


def rebuild_projections(repo: str | Path, run_id: str, *, stale_after_seconds: float | None = None) -> dict[str, Any]:
    """Rebuild V1 projections for a run from JSONL events alone."""
    repo_path = Path(repo).resolve()
    run_dir = _find_run_dir(repo_path, run_id)
    if run_dir is None:
        raise FileNotFoundError(f"Run log not found: {run_id}")
    events, skipped = _read_run_events(run_dir)
    projections = _project_events(repo_path, run_id, run_dir, events, skipped, stale_after_seconds=stale_after_seconds)
    _write_projections(run_dir, projections)
    return projections


def read_projection(repo: str | Path, run_id: str, name: str) -> dict[str, Any]:
    run_dir = _find_run_dir(Path(repo).resolve(), run_id)
    if run_dir is None:
        raise FileNotFoundError(f"Run log not found: {run_id}")
    path = run_dir / "projections" / f"{name}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def iter_jsonl(path: str | Path) -> Iterator[dict[str, Any]]:
    """Yield complete JSON object lines, tolerating an incomplete trailing line."""
    file_path = Path(path)
    if not file_path.exists():
        return
    with file_path.open("rb") as handle:
        for raw in handle:
            if not raw.endswith(b"\n"):
                break
            try:
                loaded = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(loaded, dict):
                yield loaded


def tail(repo: str | Path, run_id: str | None = None) -> list[str]:
    """Return canonical run lines for one run or all known runs."""
    repo_path = Path(repo).resolve()
    run_ids = [run_id] if run_id else _run_ids(repo_path)
    lines = []
    for current_run_id in run_ids:
        if current_run_id is None:
            continue
        projections = rebuild_projections(repo_path, current_run_id)
        line = str(projections["canonical_run_line"]["line"])
        lines.append(line)
    return lines


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m ai_org.log")
    subparsers = parser.add_subparsers(dest="command", required=True)
    tail_parser = subparsers.add_parser("tail")
    tail_parser.add_argument("repo")
    tail_parser.add_argument("run_id", nargs="?")
    args = parser.parse_args(argv)
    if args.command == "tail":
        for line in tail(args.repo, args.run_id):
            print(line)
        return 0
    return 2


def write_progress_projection(
    progress_path: str | Path,
    *,
    technical_approach: Mapping[str, Any],
    steps_completed: Sequence[Mapping[str, Any]],
    current_step: str | None,
) -> None:
    """Write the legacy progress-path shape from projection data."""
    safe_steps = [
        {"step": str(step.get("step", "")), "seconds": float(step.get("seconds", 0.0))}
        for step in steps_completed
    ]
    snapshot = {
        "technical_approach": dict(technical_approach),
        "steps_completed": safe_steps,
        "current_step": current_step,
        "progress": {
            "steps_done": [step["step"] for step in safe_steps],
            "steps_completed": safe_steps,
            "current_step": current_step,
        },
    }
    path = Path(progress_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snapshot, indent=2, default=str, sort_keys=True), encoding="utf-8")


def write_artifact(
    ctx: RunContext | Mapping[str, Any],
    relative_name: str,
    data: bytes,
    *,
    content_type: str = "application/octet-stream",
) -> dict[str, Any]:
    context = _coerce_context(ctx)
    run_dir = _run_dir(context)
    safe = _safe_artifact_name(relative_name)
    path = run_dir / "artifacts" / safe
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return {
        "path": str(path.relative_to(run_dir)),
        "content_type": content_type,
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def _project_events(
    repo: Path,
    run_id: str,
    run_dir: Path,
    events: list[dict[str, Any]],
    skipped: Mapping[str, int],
    *,
    stale_after_seconds: float | None,
) -> dict[str, Any]:
    events = sorted(events, key=lambda event: str(event.get("event_id", "")))
    now = _utc_now()
    last_event = events[-1] if events else None
    outcome = _latest_outcome(events)
    status = "running"
    if any(str(event.get("event_type", "")).endswith(".failed") for event in events):
        status = "failed"
    elif outcome is not None:
        status = "succeeded" if _outcome_is_success(outcome) else "completed"
    elif events and _has_success_terminal(events):
        status = "succeeded"
    elif last_event is not None:
        stale_after = stale_after_seconds if stale_after_seconds is not None else _env_float(
            "AI_ORG_LOG_DIED_AFTER", DEFAULT_DIED_AFTER_SECONDS
        )
        observed = _parse_time(str(last_event.get("observed_at") or last_event.get("occurred_at") or ""))
        if observed is not None and (now - observed).total_seconds() > stale_after:
            status = "died_inferred"

    step_events = [
        event for event in events if str(event.get("event_type")) == "approach.step.completed"
    ]
    steps_completed = []
    technical_approach: dict[str, Any] = {}
    current_step = None
    for event in step_events:
        payload = _payload(event)
        step = str(payload.get("step") or event.get("step") or "")
        seconds = float(payload.get("duration_seconds", payload.get("seconds", 0.0)) or 0.0)
        if step:
            steps_completed.append({"step": step, "seconds": seconds})
        if isinstance(payload.get("technical_approach"), Mapping):
            technical_approach = dict(payload["technical_approach"])
        if "current_step" in payload:
            current_step = payload.get("current_step")

    timing_rows = []
    attempt_history = []
    artifact_index = []
    for event in events:
        event_type = str(event.get("event_type", ""))
        payload = _payload(event)
        event_id = str(event.get("event_id", ""))
        if event_type.endswith((".completed", ".failed")):
            timing_rows.append(
                {
                    "event_type": event_type,
                    "span_id": event.get("span_id"),
                    "stage": event.get("stage"),
                    "step": event.get("step") or payload.get("step"),
                    "duration_seconds": float(payload.get("duration_seconds", 0.0) or 0.0),
                    "status": "failed" if event_type.endswith(".failed") else "completed",
                }
            )
        if event.get("attempt") is not None or payload.get("attempt") is not None:
            attempt_history.append(
                {
                    "event_type": event_type,
                    "attempt": event.get("attempt", payload.get("attempt")),
                    "status": payload.get("status"),
                    "step": event.get("step") or payload.get("step"),
                }
            )
        payload_ref = event.get("payload_ref")
        if isinstance(payload_ref, Mapping):
            artifact_index.append({"event_id": event_id, "event_type": event_type, "payload_ref": dict(payload_ref)})
        for key in ("stdout_ref", "stderr_ref"):
            if isinstance(payload.get(key), Mapping):
                artifact_index.append({"event_id": event_id, "event_type": event_type, key: payload[key]})

    progress_snapshot = {
        "technical_approach": technical_approach,
        "steps_completed": steps_completed,
        "current_step": current_step,
        "progress": {
            "steps_done": [step["step"] for step in steps_completed],
            "steps_completed": steps_completed,
            "current_step": current_step,
        },
        "skipped": dict(skipped),
    }
    run_status = {
        "repo": str(repo),
        "run_id": run_id,
        "status": status,
        "last_event_id": last_event.get("event_id") if last_event else None,
        "last_event_type": last_event.get("event_type") if last_event else None,
        "event_count": len(events),
        "skipped": dict(skipped),
    }
    if outcome is not None:
        run_status["outcome"] = outcome["status"]
        if outcome.get("failed_step"):
            run_status["failed_step"] = outcome["failed_step"]
    timing_table = {"rows": timing_rows, "skipped": dict(skipped)}
    attempt_projection = {"attempts": attempt_history, "skipped": dict(skipped)}
    artifact_projection = {"artifacts": artifact_index, "skipped": dict(skipped)}
    canonical = {"line": _canonical_line(run_status, progress_snapshot, events, timing_rows)}
    return {
        "run_status": run_status,
        "progress_snapshot": progress_snapshot,
        "timing_table": timing_table,
        "attempt_history": attempt_projection,
        "artifact_index": artifact_projection,
        "canonical_run_line": canonical,
    }


def _write_projections(run_dir: Path, projections: Mapping[str, Any]) -> None:
    projection_dir = run_dir / "projections"
    projection_dir.mkdir(parents=True, exist_ok=True)
    for name, projection in projections.items():
        (projection_dir / f"{name}.json").write_text(_json_dumps(projection) + "\n", encoding="utf-8")


def _canonical_line(
    run_status: Mapping[str, Any],
    progress: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
    timing_rows: Sequence[Mapping[str, Any]],
) -> str:
    steps = progress.get("progress", {}).get("steps_done", []) if isinstance(progress.get("progress"), Mapping) else []
    done = len(steps) if isinstance(steps, Sequence) else 0
    current = progress.get("current_step") or "-"
    duration = _headline_duration_seconds(events, timing_rows)
    outcome = f" outcome={run_status['outcome']}" if run_status.get("outcome") else ""
    failed_step = f" failed_step={run_status['failed_step']}" if run_status.get("failed_step") else ""
    return (
        f"{run_status.get('run_id')} {run_status.get('status')}{outcome}{failed_step} "
        f"events={run_status.get('event_count')} steps={done} current={current} seconds={duration:.3f}"
    )


def _read_run_events(run_dir: Path) -> tuple[list[dict[str, Any]], dict[str, int]]:
    events = []
    skipped = {"malformed": 0, "non_object": 0}
    for path in _stream_files(run_dir):
        if not path.exists():
            continue
        with path.open("rb") as handle:
            for raw in handle:
                if not raw.endswith(b"\n"):
                    break
                try:
                    loaded = json.loads(raw)
                except json.JSONDecodeError:
                    skipped["malformed"] += 1
                    continue
                if not isinstance(loaded, dict):
                    skipped["non_object"] += 1
                    continue
                events.append(loaded)
    return events, skipped


def _stream_files(run_dir: Path) -> list[Path]:
    files = [run_dir / "supervisor.jsonl"]
    worker_dir = run_dir / "workers"
    if worker_dir.exists():
        files.extend(sorted(worker_dir.glob("*.jsonl")))
    return files


def _has_success_terminal(events: Sequence[Mapping[str, Any]]) -> bool:
    terminal = [str(event.get("event_type", "")) for event in events]
    return any(event_type in {"run.completed", "rfc.produce.completed"} for event_type in terminal) or any(
        event_type.endswith(".completed") and event_type.count(".") == 1 for event_type in terminal
    )


def _latest_outcome(events: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    for event in reversed(events):
        if str(event.get("event_type", "")) != "rfc.pull.outcome":
            continue
        payload = _payload(event)
        status = payload.get("status")
        if not isinstance(status, str) or not status:
            continue
        outcome: dict[str, Any] = {"status": status}
        failed_step = payload.get("failed_step")
        if isinstance(failed_step, str) and failed_step:
            outcome["failed_step"] = failed_step
        if payload.get("ok") is True:
            outcome["ok"] = True
        return outcome
    return None


def _outcome_is_success(outcome: Mapping[str, Any]) -> bool:
    status = str(outcome.get("status") or "")
    return bool(outcome.get("ok")) or status in {
        "accepted",
        "elaborated",
        "integrated",
        "made",
        "merged",
        "ok",
        "promoted",
        "rebaselined",
        "refined",
        "reformed",
        "reviewed",
        "succeeded",
        "success",
    }


def _headline_duration_seconds(
    events: Sequence[Mapping[str, Any]], timing_rows: Sequence[Mapping[str, Any]]
) -> float:
    for row in reversed(timing_rows):
        if row.get("event_type") == "run.completed":
            return float(row.get("duration_seconds", 0.0) or 0.0)
    timestamps = [
        parsed
        for event in events
        for parsed in [_parse_time(str(event.get("occurred_at") or event.get("observed_at") or ""))]
        if parsed is not None
    ]
    if len(timestamps) < 2:
        return 0.0
    return max((max(timestamps) - min(timestamps)).total_seconds(), 0.0)


def _payload(event: Mapping[str, Any]) -> dict[str, Any]:
    payload = event.get("payload")
    return dict(payload) if isinstance(payload, Mapping) else {}


def _normalize_payload(run_dir: Path, event_id: str, payload: Mapping[str, Any]) -> tuple[Mapping[str, Any], dict[str, Any] | None]:
    encoded = _json_dumps(payload).encode("utf-8")
    if len(encoded) <= HARD_PAYLOAD_BYTES:
        return dict(payload), None
    ref = _write_payload_overflow(run_dir, event_id, encoded)
    truncated = encoded[:TARGET_PAYLOAD_BYTES].decode("utf-8", errors="replace")
    return {"truncated": truncated}, ref


def _write_payload_overflow(run_dir: Path, event_id: str, encoded: bytes) -> dict[str, Any]:
    path = run_dir / "artifacts" / "payloads" / f"{event_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encoded)
    return {
        "path": str(path.relative_to(run_dir)),
        "content_type": "application/json",
        "payload_bytes": len(encoded),
        "payload_sha256": hashlib.sha256(encoded).hexdigest(),
        "truncated": True,
    }


def _subprocess_payload(
    ctx: RunContext,
    command: Sequence[str],
    completed: subprocess.CompletedProcess,
    duration: float,
    policy: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    payload: dict[str, Any] = {
        "argv": list(command),
        "duration_seconds": duration,
        "exit_code": int(completed.returncode),
    }
    for name, value in (("stdout", completed.stdout), ("stderr", completed.stderr)):
        data = _completed_stream_bytes(value)
        limit = int(policy.get("inline_bytes", _ARTIFACT_INLINE_BYTES))
        payload[f"{name}_bytes"] = len(data)
        payload[f"{name}_head"] = data[:limit].decode("utf-8", errors="replace")
        payload[f"{name}_tail"] = data[-limit:].decode("utf-8", errors="replace") if len(data) > limit else payload[f"{name}_head"]
        if data:
            payload[f"{name}_ref"] = write_artifact(
                ctx,
                f"subprocess/{uuid.uuid4().hex}-{name}.txt",
                data,
                content_type="text/plain; charset=utf-8",
            )
    return payload, None


def _completed_stream_bytes(value: Any) -> bytes:
    if value is None:
        return b""
    if isinstance(value, bytes):
        return value
    return str(value).encode("utf-8", errors="replace")


def _capture_policy(policy: str | Mapping[str, Any] | None) -> Mapping[str, Any]:
    if isinstance(policy, Mapping):
        return dict(policy)
    if policy == "none":
        return {"inline_bytes": 0}
    return {"inline_bytes": _ARTIFACT_INLINE_BYTES}


def _coerce_context(ctx: RunContext | Mapping[str, Any]) -> RunContext:
    if isinstance(ctx, RunContext):
        return ctx
    return RunContext(**dict(ctx))


def _run_dir(ctx: RunContext) -> Path:
    assert ctx.run_id is not None
    date = _run_date_from_id(ctx.run_id) or _utc_now().strftime("%Y-%m-%d")
    return Path(ctx.repo).resolve() / ".ai-org" / "log" / "runs" / date / ctx.run_id


def _stream_path(ctx: RunContext) -> Path:
    run_dir = _run_dir(ctx)
    if ctx.stream_id == "supervisor":
        return run_dir / "supervisor.jsonl"
    return run_dir / "workers" / f"{_safe_artifact_name(ctx.stream_id)}.jsonl"


def _next_stream_seq(stream_path: Path) -> int:
    if not stream_path.exists():
        return 1
    seq = 0
    for record in iter_jsonl(stream_path):
        stream = record.get("stream")
        if isinstance(stream, Mapping):
            try:
                seq = max(seq, int(stream.get("seq") or 0))
            except (TypeError, ValueError):
                continue
    return seq + 1


def _find_run_dir(repo: Path, run_id: str) -> Path | None:
    runs = repo / ".ai-org" / "log" / "runs"
    if not runs.exists():
        return None
    for candidate in runs.glob(f"*/{run_id}"):
        if candidate.is_dir():
            return candidate
    return None


def _run_ids(repo: Path) -> list[str]:
    runs = repo / ".ai-org" / "log" / "runs"
    if not runs.exists():
        return []
    return sorted(path.name for path in runs.glob("*/*") if path.is_dir())


def _run_date_from_id(run_id: str) -> str | None:
    parts = run_id.split("-")
    for part in parts:
        if len(part) >= 8 and part[:8].isdigit():
            return f"{part[:4]}-{part[4:6]}-{part[6:8]}"
    return None


def _event_id(now: datetime) -> str:
    return f"{now.strftime('%Y%m%dT%H%M%S')}.{time.time_ns() % 1_000_000_000:09d}Z-{_PID}-{uuid.uuid4().hex[:8]}"


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _isoformat(value: datetime) -> str:
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_time(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str)


def _safe_artifact_name(value: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "._-/" else "_" for ch in str(value))
    parts = [part.strip(".") for part in safe.split("/") if part.strip(".")]
    return "/".join(parts) or "artifact"


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def _sync_fd(fd: int) -> None:
    sync = getattr(os, "fdatasync", os.fsync)
    sync(fd)


def _start_heartbeat(name: str, ctx: RunContext) -> threading.Event:
    stop = threading.Event()
    interval = _env_float("AI_ORG_LOG_HEARTBEAT_INTERVAL", DEFAULT_HEARTBEAT_INTERVAL_SECONDS)
    if interval <= 0:
        return stop

    def beat() -> None:
        while not stop.wait(interval):
            debug_emit("heartbeat", {"span": name}, ctx=ctx)

    thread = threading.Thread(target=beat, name="ai-org-log-heartbeat", daemon=True)
    thread.start()
    return stop


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
