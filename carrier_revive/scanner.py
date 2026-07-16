"""Read-only scanner for Codex rollout session files.

Verified layout (2026-07-11, codex-cli 0.144.1):
    ~/.codex/sessions/YYYY/MM/DD/rollout-<local-ts>-<uuid>.jsonl

The first line of each file is a JSON object:
    {"timestamp": "...Z", "type": "session_meta",
     "payload": {"id": "<uuid>", "cwd": "/abs/path", ...}}

Subsequent lines record the conversation: "response_item" (payload.type in
message / function_call / custom_tool_call / reasoning ...), "event_msg"
(payload.type in user_message / agent_message / token_count ...), etc.

INVARIANT (Memento tattoo): this module opens rollout files READ-ONLY and
never creates, modifies, or deletes anything under ~/.codex.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, Iterator, Optional

DEFAULT_SESSIONS_ROOT = Path.home() / ".codex" / "sessions"

# rollout-2026-07-11T05-49-31-019f4dca-ea50-7db1-b05c-bf74b551a5d1.jsonl
ROLLOUT_RE = re.compile(
    r"^rollout-(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2})-"
    r"(?P<uuid>[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\.jsonl$"
)

# Preserved implementer worktrees look like $TMPDIR/ai-org-patch-author-*/worktree
PRESERVED_WORKTREE_MARKER = "ai-org-patch-author-"

# How many leading lines to scan when looking for the first real user prompt.
_FIRST_PROMPT_SCAN_LIMIT = 300


@dataclass
class Session:
    """One rollout file = one recorded Codex session."""

    uuid: str
    path: Path
    start: Optional[datetime]  # from the rollout filename (local time)
    cwd: str
    last_activity: Optional[datetime]  # file mtime (local time)
    first_prompt: str = ""
    meta: dict = field(default_factory=dict)

    @property
    def short_uuid(self) -> str:
        return self.uuid[:8]

    @property
    def cwd_exists(self) -> bool:
        return bool(self.cwd) and os.path.isdir(self.cwd)

    @property
    def is_preserved_worktree(self) -> bool:
        return is_preserved_worktree(self.cwd)


def is_preserved_worktree(cwd: str) -> bool:
    """True when cwd looks like an engine-preserved implementer worktree."""
    return PRESERVED_WORKTREE_MARKER in cwd


def parse_rollout_filename(name: str) -> Optional[tuple[datetime, str]]:
    """Return (start_time, uuid) parsed from a rollout filename, or None."""
    m = ROLLOUT_RE.match(name)
    if not m:
        return None
    try:
        start = datetime.strptime(m.group("ts"), "%Y-%m-%dT%H-%M-%S")
    except ValueError:
        return None
    return start, m.group("uuid").lower()


def _iter_json_lines(path: Path, limit: Optional[int] = None) -> Iterator[dict]:
    """Yield parsed JSON objects from a rollout file, skipping bad lines.

    A killed session may leave a truncated final line; we skip undecodable
    lines instead of failing.
    """
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f):
                if limit is not None and i >= limit:
                    return
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict):
                    yield obj
    except OSError:
        return


def read_meta(path: Path) -> dict:
    """Return the session_meta payload (first line) of a rollout file."""
    for obj in _iter_json_lines(path, limit=5):
        if obj.get("type") == "session_meta":
            payload = obj.get("payload")
            return payload if isinstance(payload, dict) else {}
        break
    return {}


def _texts_of_message(payload: dict) -> str:
    """Flatten a response_item message payload's content into one string."""
    parts = []
    for item in payload.get("content") or []:
        if isinstance(item, dict):
            text = item.get("text") or item.get("input_text") or ""
            if isinstance(text, str):
                parts.append(text)
    return "\n".join(parts)


def _looks_like_preamble(text: str) -> bool:
    """Harness-injected messages start with tags like <permissions instructions>."""
    return text.lstrip().startswith("<")


def extract_first_prompt(objs: Iterable[dict]) -> str:
    """Return the first real user prompt from rollout objects.

    Preference order (verified against live rollouts):
      1. event_msg / user_message payload.message not starting with '<'
      2. response_item message with role == "user" not starting with '<'
    """
    fallback = ""
    for obj in objs:
        payload = obj.get("payload") or {}
        ptype = payload.get("type")
        if obj.get("type") == "event_msg" and ptype == "user_message":
            msg = payload.get("message") or ""
            if isinstance(msg, str) and msg and not _looks_like_preamble(msg):
                return msg
        elif obj.get("type") == "response_item" and ptype == "message":
            if payload.get("role") == "user" and not fallback:
                text = _texts_of_message(payload)
                if text and not _looks_like_preamble(text):
                    fallback = text
    return fallback


def load_session(path: Path) -> Optional[Session]:
    """Build a Session from a rollout file (reads only the head of the file)."""
    parsed = parse_rollout_filename(path.name)
    if parsed is None:
        return None
    start, uuid = parsed
    meta = read_meta(path)
    try:
        mtime = datetime.fromtimestamp(path.stat().st_mtime)
    except OSError:
        mtime = None
    first_prompt = extract_first_prompt(
        _iter_json_lines(path, limit=_FIRST_PROMPT_SCAN_LIMIT)
    )
    return Session(
        uuid=uuid,
        path=path,
        start=start,
        cwd=str(meta.get("cwd") or ""),
        last_activity=mtime,
        first_prompt=first_prompt,
        meta=meta,
    )


def _date_dirs(root: Path, days: int, now: Optional[datetime] = None) -> list[Path]:
    """Return existing YYYY/MM/DD directories covering the last `days` days."""
    now = now or datetime.now()
    dirs = []
    for delta in range(days + 1):  # include today
        d = now - timedelta(days=delta)
        p = root / f"{d.year:04d}" / f"{d.month:02d}" / f"{d.day:02d}"
        if p.is_dir():
            dirs.append(p)
    return dirs


def scan_sessions(
    root: Path = DEFAULT_SESSIONS_ROOT,
    days: int = 2,
    now: Optional[datetime] = None,
) -> list[Session]:
    """Scan rollout files from the last `days` days, newest first."""
    sessions: list[Session] = []
    for d in _date_dirs(root, days, now):
        for path in sorted(d.iterdir()):
            if not path.name.endswith(".jsonl"):
                continue
            s = load_session(path)
            if s is not None:
                sessions.append(s)
    sessions.sort(key=lambda s: (s.start or datetime.min), reverse=True)
    return sessions


def resolve_session(sessions: list[Session], prefix: str) -> tuple[Optional[Session], list[Session]]:
    """Resolve a uuid prefix to exactly one session.

    Returns (session, matches). session is None unless exactly one match.
    """
    prefix = prefix.lower().strip()
    matches = [s for s in sessions if s.uuid.startswith(prefix)]
    if len(matches) == 1:
        return matches[0], matches
    return None, matches


# ---------------------------------------------------------------------------
# Detail view (used by `show`)
# ---------------------------------------------------------------------------

def _is_apply_patch_call(payload: dict) -> bool:
    """True when a response_item payload is an apply_patch invocation.

    apply_patch may appear as a function_call named apply_patch, or embedded
    in a custom_tool_call / shell call input.
    """
    ptype = payload.get("type")
    if ptype not in ("function_call", "custom_tool_call", "local_shell_call"):
        return False
    name = payload.get("name") or ""
    if "apply_patch" in name:
        return True
    args = payload.get("arguments") or payload.get("input") or ""
    return isinstance(args, str) and "apply_patch" in args


def analyze_session(path: Path, tail_events: int = 10) -> dict:
    """Full-file pass: apply_patch count, last event types, last agent message."""
    apply_patch_count = 0
    event_types: list[str] = []
    last_agent_message = ""
    for obj in _iter_json_lines(path):
        payload = obj.get("payload") or {}
        ptype = payload.get("type") or obj.get("type") or "?"
        event_types.append(ptype)
        if _is_apply_patch_call(payload):
            apply_patch_count += 1
        if obj.get("type") == "event_msg" and payload.get("type") == "agent_message":
            msg = payload.get("message")
            if isinstance(msg, str) and msg:
                last_agent_message = msg
        elif (
            obj.get("type") == "response_item"
            and payload.get("type") == "message"
            and payload.get("role") == "assistant"
        ):
            text = _texts_of_message(payload)
            if text:
                last_agent_message = text
    return {
        "apply_patch_count": apply_patch_count,
        "total_events": len(event_types),
        "last_event_types": event_types[-tail_events:],
        "last_agent_message": last_agent_message,
    }

