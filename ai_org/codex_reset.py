"""Shared Codex interruption handling."""
from __future__ import annotations

from datetime import datetime, timedelta
import os
import re
import time
from typing import Any, Callable, Literal, Sequence, TypeVar

import ai_org.log as org_log


RESET_RE = re.compile(r"try again at ([0-9]{1,2}:[0-9]{2} [AP]M)\.", re.IGNORECASE)

# These provider failures are intentionally a line-oriented whitelist. Codex
# emits them at column zero, optionally behind its ERROR prefix; prose, JSON
# payloads, Markdown quotes, and review findings that quote them do not match.
_CAPACITY_RE = re.compile(
    r"^(?:ERROR(?::|\s+)\s*)?(?:the\s+)?selected model is (?:currently\s+)?at capacity"
    r"(?:[.,;:]?\s*(?:please\s+)?try again(?:\s+later)?)?[.!]?"
    r"(?:\s+(?:request|trace)[ _-]?id\s*[:=]\s*[^\r\n]+)?\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_HTTP_STATUS_RE = re.compile(
    r"^(?:"
    r"(?:ERROR(?::|\s+)\s*)?(?:(?:request failed with|upstream returned)\s+)?"
    r"HTTP(?:/\d(?:\.\d)?)?(?:\s+(?:status|error))?\s*[:=]?\s*(?:429|5\d{2})"
    r"|ERROR(?::|\s+)\s*(?:(?:request failed with|upstream returned)\s+)?"
    r"(?:unexpected\s+)?status(?:\s+code)?\s*[:=]?\s*(?:429|5\d{2})"
    r")\b"
    r"(?:\s+(?:too many requests|internal server error|bad gateway|service unavailable|gateway timeout|server error)"
    r"[.!]?(?::[^\r\n]*)?|\s*[:;\-]\s*[^\r\n]*|\s+\((?:request|trace)[ _-]?id[^\r\n]*\))?\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_STREAM_CONNECTION_RE = re.compile(
    r"^(?:"
    r"(?:ERROR(?::|\s+)\s*)?(?:"
    r"stream\s+(?:disconnected(?:\s+before\s+completion)?|reset|closed\s+unexpectedly|ended\s+unexpectedly)"
    r"|connection\s+(?:reset(?:\s+by\s+peer)?|closed\s+unexpectedly|aborted|dropped))"
    r"|ERROR(?::|\s+)\s*error sending request for url\s+\([^\r\n)]+\):\s*"
    r"(?:stream\s+(?:disconnected(?:\s+before\s+completion)?|reset|closed\s+unexpectedly|ended\s+unexpectedly)"
    r"|connection\s+(?:reset(?:\s+by\s+peer)?|closed\s+unexpectedly|aborted|dropped))"
    r")"
    r"(?:\s*[:;,.(\-]\s*[^\r\n]*)?\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_TRANSIENT_PATTERNS = (_CAPACITY_RE, _HTTP_STATUS_RE, _STREAM_CONNECTION_RE)

_SESSION_UUID_PATTERN = (
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
_SESSION_UUID_RE = re.compile(rf"^{_SESSION_UUID_PATTERN}$")
_SESSION_HEADER_RE = re.compile(
    rf"^session id: ({_SESSION_UUID_PATTERN})\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_SESSION_EVENT_RES = (
    re.compile(
        rf'^\s*\{{\s*"type"\s*:\s*"thread\.started"\s*,[^\r\n]*'
        rf'"thread_id"\s*:\s*"({_SESSION_UUID_PATTERN})"[^\r\n]*\}}\s*$'
    ),
    re.compile(
        rf'^\s*\{{\s*"type"\s*:\s*"session_configured"\s*,[^\r\n]*'
        rf'"session_id"\s*:\s*"({_SESSION_UUID_PATTERN})"[^\r\n]*\}}\s*$'
    ),
)

BOUNDARY_GRACE = timedelta(minutes=10)
SHORT_RETRY = timedelta(seconds=120)
WAIT_INTERVAL_SECONDS = 30.0
TRANSIENT_BACKOFF_SECONDS = 30.0
MAX_TRANSIENT_BACKOFF_SECONDS = 300.0
MAX_RESET_RETRIES = 4
DEFAULT_MAX_TRANSIENT_RETRIES = 6
TRANSIENT_RETRIES_EXHAUSTED = "transient-retries-exhausted"
RESUME_PROMPT = "Continue where you left off."

_SESSION_IDS_ATTR = "_ai_org_codex_session_ids"
_RETRY_MODES_ATTR = "_ai_org_codex_retry_modes"
_TRANSIENT_EXHAUSTED_ATTR = "_ai_org_transient_retries_exhausted"

_Completed = TypeVar("_Completed")
TransientClassification = Literal["timed_reset", "transient"]


def _configured_max_transient_retries() -> int:
    raw = os.environ.get("AI_ORG_TRANSIENT_MAX_RETRIES")
    if raw is None:
        return DEFAULT_MAX_TRANSIENT_RETRIES
    try:
        configured = int(raw)
    except ValueError:
        return DEFAULT_MAX_TRANSIENT_RETRIES
    return configured if configured >= 0 else DEFAULT_MAX_TRANSIENT_RETRIES


MAX_TRANSIENT_RETRIES = _configured_max_transient_retries()


def classify_transient(stderr_or_stdout_text: str) -> TransientClassification | None:
    """Classify only announced resets and whitelisted provider interruptions."""
    text = str(stderr_or_stdout_text or "")
    if RESET_RE.search(text) is not None:
        return "timed_reset"
    if any(pattern.search(text) is not None for pattern in _TRANSIENT_PATTERNS):
        return "transient"
    return None


def codex_not_before(reset_time_text: str, started_at: datetime) -> str:
    """Resolve a time-only Codex reset message against the local start date."""
    parsed = datetime.strptime(reset_time_text.upper(), "%I:%M %p")
    local_started = started_at.astimezone()
    candidate = local_started.replace(hour=parsed.hour, minute=parsed.minute, second=0, microsecond=0)
    if candidate <= local_started:
        # Memento: a just-passed HH:MM is refill-lag echo from the window that
        # is opening now, not a quota reset one day away (2026-07-12 incident).
        if local_started - candidate <= BOUNDARY_GRACE:
            return (local_started + SHORT_RETRY).isoformat()
        candidate += timedelta(days=1)
    return candidate.isoformat()


def wait_for_codex_reset(not_before: Any, *, ctx: org_log.RunContext, event_name: str) -> None:
    """Emit progress and wait until a computed Codex reset boundary."""
    if not not_before:
        return
    target = datetime.fromisoformat(str(not_before))
    while True:
        remaining = (target - datetime.now(target.tzinfo)).total_seconds()
        if remaining <= 0:
            return
        interval = min(WAIT_INTERVAL_SECONDS, remaining)
        org_log.emit(
            event_name,
            {"not_before": str(not_before), "remaining_seconds": remaining, "sleep_seconds": interval},
            ctx=ctx,
        )
        time.sleep(interval)


def transient_backoff_seconds(attempt: int) -> float:
    """Return the deterministic delay for a one-based transient retry."""
    normalized_attempt = max(int(attempt), 1)
    return min(
        TRANSIENT_BACKOFF_SECONDS * (2 ** (normalized_attempt - 1)),
        MAX_TRANSIENT_BACKOFF_SECONDS,
    )


def wait_for_transient(attempt: int, *, ctx: org_log.RunContext, event_name: str) -> None:
    """Emit retry progress and wait once using the bounded backoff schedule."""
    sleep_seconds = transient_backoff_seconds(attempt)
    common = {
        "attempt": int(attempt),
        "classification": "transient",
        "sleep_seconds": sleep_seconds,
    }
    org_log.emit(event_name, {**common, "status": "started"}, ctx=ctx)
    org_log.emit(event_name, {**common, "status": "sleeping"}, ctx=ctx)
    time.sleep(sleep_seconds)
    org_log.emit(event_name, {**common, "status": "retrying"}, ctx=ctx)


def _stream_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _combined_output(completed: Any) -> str:
    return f"{_stream_text(getattr(completed, 'stdout', ''))}\n{_stream_text(getattr(completed, 'stderr', ''))}"


def extract_session_ids(stderr_or_stdout_text: str) -> tuple[str, ...]:
    """Extract Codex session IDs from its header and JSONL event stream."""
    text = str(stderr_or_stdout_text or "")
    candidates = [match.group(1) for match in _SESSION_HEADER_RE.finditer(text)]
    for line in text.splitlines():
        for event_re in _SESSION_EVENT_RES:
            match = event_re.fullmatch(line)
            if match is not None:
                candidates.append(match.group(1))
                break
    return tuple(dict.fromkeys(candidates))


def codex_resume_command(
    argv: Sequence[str | os.PathLike[str]],
    session_id: str,
    *,
    prompt: str = RESUME_PROMPT,
) -> list[str]:
    """Build the documented ``codex exec ... resume`` form from an exec argv."""
    command = [os.fspath(part) for part in argv]
    if len(command) < 3 or command[0] != "codex" or command[1] != "exec":
        raise ValueError("resume requires a codex exec command")
    if _SESSION_UUID_RE.fullmatch(session_id) is None:
        raise ValueError("resume requires a Codex session UUID")

    prefix = command[:-1]
    output_args: list[str] = []
    for output_flag in ("-o", "--output-last-message"):
        if output_flag not in prefix:
            continue
        output_index = prefix.index(output_flag)
        if output_index + 1 >= len(prefix):
            raise ValueError(f"{output_flag} requires a path")
        output_args = ["-o", prefix[output_index + 1]]
        del prefix[output_index : output_index + 2]
        break

    return [*prefix, "resume", "--json", session_id, *output_args, prompt]


def _record_retry_metadata(
    completed: Any,
    *,
    session_ids: Sequence[str],
    retry_modes: Sequence[str],
    transient_exhausted: bool,
) -> None:
    try:
        setattr(completed, _SESSION_IDS_ATTR, tuple(session_ids))
        setattr(completed, _RETRY_MODES_ATTR, tuple(retry_modes))
        setattr(completed, _TRANSIENT_EXHAUSTED_ATTR, bool(transient_exhausted))
    except (AttributeError, TypeError):
        return


def retry_session_ids(completed: Any) -> tuple[str, ...]:
    """Return the ordered, de-duplicated session chain captured across attempts."""
    value = getattr(completed, _SESSION_IDS_ATTR, ())
    return tuple(value) if isinstance(value, (list, tuple)) else ()


def retry_modes(completed: Any) -> tuple[str, ...]:
    """Return whether each attempt was started or resumed."""
    value = getattr(completed, _RETRY_MODES_ATTR, ())
    return tuple(value) if isinstance(value, (list, tuple)) else ()


def transient_retries_exhausted(completed: Any) -> bool:
    """Return whether the final result exhausted the untimed retry budget."""
    return bool(getattr(completed, _TRANSIENT_EXHAUSTED_ATTR, False))


def _emit_retry_chain(
    *,
    ctx: org_log.RunContext,
    event_name: str,
    session_ids: Sequence[str],
    retry_modes: Sequence[str],
    returncode: Any,
) -> None:
    if len(retry_modes) <= 1:
        return
    org_log.emit(
        "codex.retry_chain.recorded",
        {
            "wait_event_name": event_name,
            "session_ids": list(session_ids),
            "retry_modes": list(retry_modes),
            "attempts": len(retry_modes),
            "returncode": int(returncode),
        },
        ctx=ctx,
    )


def run_with_reset_retries(
    run_once: Callable[[], _Completed],
    *,
    ctx: org_log.RunContext,
    event_name: str,
    resume_once: Callable[[str], _Completed] | None = None,
    max_reset_retries: int = MAX_RESET_RETRIES,
    max_transient_retries: int | None = None,
) -> _Completed:
    """Retry a Codex call across announced resets and bounded transients."""
    if max_transient_retries is None:
        max_transient_retries = MAX_TRANSIENT_RETRIES
    reset_retries = 0
    transient_retries = 0
    session_ids: list[str] = []
    invocation_modes: list[str] = []
    resume_session: str | None = None

    # Memento: interruption is an environmental property, not an exception --
    # timed waits for announced resets, bounded backoff for unannounced
    # transients, and resume over restart when the session survives.
    while True:
        started_at = datetime.now().astimezone()
        if resume_session is not None and resume_once is not None:
            completed = resume_once(resume_session)
            invocation_modes.append("resume")
        else:
            completed = run_once()
            invocation_modes.append("restart")

        attempt_session_ids = extract_session_ids(_combined_output(completed))
        for session_id in attempt_session_ids:
            if session_id not in session_ids:
                session_ids.append(session_id)
        if attempt_session_ids:
            resume_session = attempt_session_ids[-1]

        if getattr(completed, "returncode", 0) == 0:
            _record_retry_metadata(
                completed,
                session_ids=session_ids,
                retry_modes=invocation_modes,
                transient_exhausted=False,
            )
            _emit_retry_chain(
                ctx=ctx,
                event_name=event_name,
                session_ids=session_ids,
                retry_modes=invocation_modes,
                returncode=getattr(completed, "returncode", 0),
            )
            return completed

        combined_output = _combined_output(completed)
        classification = classify_transient(combined_output)
        if classification == "timed_reset" and reset_retries < max_reset_retries:
            reset_match = RESET_RE.search(combined_output)
            assert reset_match is not None
            not_before = codex_not_before(reset_match.group(1), started_at)
            wait_for_codex_reset(not_before, ctx=ctx, event_name=event_name)
            reset_retries += 1
            continue

        if classification == "transient" and transient_retries < max_transient_retries:
            wait_for_transient(transient_retries + 1, ctx=ctx, event_name=event_name)
            transient_retries += 1
            continue

        transient_exhausted = classification == "transient" and transient_retries >= max_transient_retries
        _record_retry_metadata(
            completed,
            session_ids=session_ids,
            retry_modes=invocation_modes,
            transient_exhausted=transient_exhausted,
        )
        _emit_retry_chain(
            ctx=ctx,
            event_name=event_name,
            session_ids=session_ids,
            retry_modes=invocation_modes,
            returncode=getattr(completed, "returncode", 0),
        )
        if transient_exhausted:
            org_log.emit(
                event_name,
                {
                    "status": "failed",
                    "classification": "transient",
                    "marker": TRANSIENT_RETRIES_EXHAUSTED,
                    "attempt": transient_retries + 1,
                    "session_ids": list(session_ids),
                },
                ctx=ctx,
                severity="error",
            )
        return completed
