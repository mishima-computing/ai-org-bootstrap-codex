"""Chain links 4-5: carrier call -> Codex session -> recorded thinking.

Reuses carrier_revive.scanner for the rollout layout knowledge (filename
schema, session_meta, line iteration) instead of re-deriving it.

Evidence tiers for the call->session match, strongest first:
  * "session-id+content"  codex exec prints "session id: <uuid>" on stderr;
                          the run log captured that stderr, AND the call's
                          output text is found inside that rollout.
  * "session-id"          the recorded id resolved to a rollout, but the
                          content cross-check found no overlap.
  * "content"             no recorded id, but exactly one candidate rollout
                          contains the call's distinctive output text.
  * "cwd+time"            only the working directory + time window match
                          (weak; reported as such, never upgraded).
  * "none"                no defensible match.

Recorded thinking is extracted MECHANICALLY: the rollout items whose text
contains the target, the reasoning items around them (quoted verbatim when
plaintext; reported as encrypted when the rollout only carries
encrypted_content), and the file-read tool calls immediately preceding.
No summarization, no LLM.

READ-ONLY over ~/.codex/sessions (inherited invariant from carrier_revive).
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from carrier_revive import scanner

from .model import CarrierCall, SessionLink, ThinkingItem, ThinkingLink
from .runlog import contains_any_form, escape_forms, parse_ts

DEFAULT_SESSIONS_ROOT = scanner.DEFAULT_SESSIONS_ROOT

_SESSION_ID_RE = re.compile(
    r"session id: ([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
)

# codex exec prints its header (including the session id) at the top of
# stderr; reading the head is enough and keeps huge stderr files cheap.
_STDERR_HEAD_BYTES = 16384

# Content cross-check reads the head and tail of a rollout: the final
# output of a call lands at the end of its rollout.
_ROLLOUT_HEAD_BYTES = 256 * 1024
_ROLLOUT_TAIL_BYTES = 4 * 1024 * 1024

_MIN_PROBE_LEN = 40
_MAX_PROBES = 3

_EXCERPT_CHARS = 500
_CONTEXT_ITEMS_BEFORE = 40
_MAX_THINKING_ITEMS = 40
_MAX_PRECEDING_READS = 5

_READ_MARKERS = (
    "cat ", "sed -n", "rg ", "grep ", "head ", "tail ", "git show",
    "read_file", "view_file", "open(", "ls ", "find ", "nl ",
)


# ---------------------------------------------------------------------------
# call -> session
# ---------------------------------------------------------------------------

def stderr_session_id(run_dir: str, call: CarrierCall) -> Optional[str]:
    """The session uuid codex printed on the call's captured stderr."""
    if not call.stderr_path:
        return None
    path = os.path.join(run_dir, call.stderr_path)
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            head = f.read(_STDERR_HEAD_BYTES)
    except OSError:
        return None
    m = _SESSION_ID_RE.search(head)
    return m.group(1).lower() if m else None


def _day_dirs_between(root: Path, t0: Optional[datetime],
                      t1: Optional[datetime]) -> list:
    """Existing YYYY/MM/DD dirs covering [t0-1d, t1+1d] in LOCAL time.

    Rollout filenames carry local timestamps while run logs are UTC, so the
    window is padded by a day on each side.
    """
    if t0 is None or t1 is None:
        return []
    lo = (t0.astimezone() - timedelta(days=1)).date()
    hi = (t1.astimezone() + timedelta(days=1)).date()
    out = []
    day = lo
    while day <= hi:
        p = root / f"{day.year:04d}" / f"{day.month:02d}" / f"{day.day:02d}"
        if p.is_dir():
            out.append(p)
        day = day + timedelta(days=1)
    return out


def find_rollout_by_uuid(uuid: str, t0: Optional[datetime],
                         t1: Optional[datetime],
                         sessions_root: Path = DEFAULT_SESSIONS_ROOT
                         ) -> Optional[Path]:
    """Locate rollout-*-<uuid>.jsonl within the (padded) time window."""
    uuid = uuid.lower()
    for day_dir in _day_dirs_between(Path(sessions_root), t0, t1):
        for path in sorted(day_dir.iterdir()):
            parsed = scanner.parse_rollout_filename(path.name)
            if parsed is not None and parsed[1] == uuid:
                return path
    return None


def content_probes(run_dir: str, call: CarrierCall) -> list:
    """Distinctive strings from the call's stdout, in document order.

    Prefers decoded JSON string scalars (>= _MIN_PROBE_LEN chars); falls
    back to the longest raw lines for non-JSON stdout.
    """
    if not call.stdout_path:
        return []
    path = os.path.join(run_dir, call.stdout_path)
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return []
    probes: list = []
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = None
    if data is not None:
        stack = [data]
        while stack and len(probes) < _MAX_PROBES:
            node = stack.pop(0)
            if isinstance(node, str):
                if len(node) >= _MIN_PROBE_LEN:
                    probes.append(node)
            elif isinstance(node, dict):
                stack = list(node.values()) + stack
            elif isinstance(node, list):
                stack = list(node) + stack
        return probes
    lines = [ln.strip() for ln in text.splitlines() if len(ln.strip()) >= _MIN_PROBE_LEN]
    lines.sort(key=len, reverse=True)
    return lines[:_MAX_PROBES]


def rollout_contains(rollout_path: Path, probes: list) -> bool:
    """Head+tail raw scan of the rollout for any probe (escaped-aware)."""
    if not probes:
        return False
    try:
        size = os.path.getsize(rollout_path)
        with open(rollout_path, "r", encoding="utf-8", errors="replace") as f:
            if size <= _ROLLOUT_HEAD_BYTES + _ROLLOUT_TAIL_BYTES:
                text = f.read()
            else:
                head = f.read(_ROLLOUT_HEAD_BYTES)
                f.seek(size - _ROLLOUT_TAIL_BYTES)
                text = head + "\n" + f.read()
    except OSError:
        return False
    return contains_any_form(text, probes)


def match_session(run_dir: str, call: CarrierCall,
                  vessel: str,
                  sessions_root: Path = DEFAULT_SESSIONS_ROOT,
                  probes: Optional[list] = None,
                  fallback_days: int = 3) -> SessionLink:
    """Resolve a carrier call to its Codex rollout, with evidence tier."""
    link = SessionLink()
    t0 = parse_ts(call.started_at) or parse_ts(call.completed_at)
    t1 = parse_ts(call.completed_at) or t0
    if probes is None:
        probes = content_probes(run_dir, call)

    # Tier 1/2: the session id recorded by codex itself on stderr.
    uuid = stderr_session_id(run_dir, call)
    if uuid:
        path = find_rollout_by_uuid(uuid, t0, t1, sessions_root)
        if path is None:
            link.uuid = uuid
            link.reason = (
                f"stderr records session id {uuid} but no rollout file with "
                f"that uuid exists under {sessions_root} in the call's time "
                "window")
            return link
        link.uuid = uuid
        link.rollout_path = str(path)
        link.cwd = scanner.read_meta(path).get("cwd", "")
        if rollout_contains(path, probes):
            link.confidence = "session-id+content"
        else:
            link.confidence = "session-id"
        link.ok = True
        return link

    # Tier 3/4: no recorded id -- fall back to cwd+time, then content.
    if t0 is None or t1 is None:
        link.reason = (
            "no session id on stderr and the call has no usable timestamps")
        return link
    vessel_real = os.path.realpath(vessel)
    candidates = []
    for session in scanner.scan_sessions(
            root=Path(sessions_root), days=fallback_days,
            now=t1.astimezone().replace(tzinfo=None)):
        if session.start is None:
            continue
        start_utc = session.start.astimezone().astimezone(timezone.utc)
        if not (t0 - timedelta(hours=1) <= start_utc <= t1):
            continue
        cwd_real = os.path.realpath(session.cwd) if session.cwd else ""
        if cwd_real == vessel_real or scanner.is_preserved_worktree(
                session.cwd):
            candidates.append(session)
    content_hits = [s for s in candidates if rollout_contains(s.path, probes)]
    if len(content_hits) == 1:
        s = content_hits[0]
        link.ok = True
        link.uuid = s.uuid
        link.rollout_path = str(s.path)
        link.cwd = s.cwd
        link.confidence = "content"
        return link
    if len(candidates) == 1:
        s = candidates[0]
        link.ok = True
        link.uuid = s.uuid
        link.rollout_path = str(s.path)
        link.cwd = s.cwd
        link.confidence = "cwd+time"
        return link
    link.reason = (
        f"no recorded session id; {len(candidates)} cwd+time candidates and "
        f"{len(content_hits)} content matches -- not exactly one, so no "
        "defensible session match")
    return link


# ---------------------------------------------------------------------------
# session -> recorded thinking
# ---------------------------------------------------------------------------

def _decoded_strings(obj) -> list:
    out = []
    stack = [obj]
    while stack:
        node = stack.pop(0)
        if isinstance(node, str):
            out.append(node)
        elif isinstance(node, dict):
            stack = list(node.values()) + stack
        elif isinstance(node, list):
            stack = list(node) + stack
    return out


def _item_text(payload: dict) -> str:
    """Human text of a rollout item payload (messages)."""
    if isinstance(payload.get("message"), str):
        return payload["message"]
    parts = []
    for item in payload.get("content") or []:
        if isinstance(item, dict):
            text = item.get("text") or item.get("input_text") or ""
            if isinstance(text, str) and text:
                parts.append(text)
    return "\n".join(parts)


def _excerpt(text: str, needles: list) -> str:
    """Deterministic clip of `text` centered on the first needle hit."""
    for needle in needles:
        pos = text.find(needle)
        if pos >= 0:
            lo = max(0, pos - _EXCERPT_CHARS // 2)
            hi = min(len(text), pos + len(needle) + _EXCERPT_CHARS // 2)
            prefix = "..." if lo > 0 else ""
            suffix = "..." if hi < len(text) else ""
            return prefix + text[lo:hi] + suffix
    if len(text) <= _EXCERPT_CHARS:
        return text
    return text[:_EXCERPT_CHARS] + "..."


def _looks_like_read(payload: dict) -> bool:
    blob = ""
    for key in ("input", "arguments"):
        val = payload.get(key)
        if isinstance(val, str):
            blob += val
    if not blob:
        return False
    return any(marker in blob for marker in _READ_MARKERS)


def extract_thinking(rollout_path: str, target_texts: list,
                     term: Optional[str] = None) -> ThinkingLink:
    """Mechanically extract the recorded deliberation around the target.

    target_texts: ordered strongest-first (full scalar, then bare term).
    Anchors = agent-side items containing a target text. The extraction
    window is the anchors plus up to _CONTEXT_ITEMS_BEFORE preceding
    response items (reasoning, messages, file reads).
    """
    link = ThinkingLink()
    needles: list = []
    for t in target_texts:
        if t and t not in needles:
            needles.append(t)
    if term and term not in needles:
        needles.append(term)
    if not needles:
        link.reason = "no target text to search for in the rollout"
        return link

    rows = []  # (line_no, obj_type, payload)
    for line_no, obj in enumerate(
            scanner._iter_json_lines(Path(rollout_path)), 1):
        payload = obj.get("payload")
        rows.append((line_no, obj.get("type"),
                     payload if isinstance(payload, dict) else {}))
    if not rows:
        link.reason = f"rollout {rollout_path} is empty or unreadable"
        return link

    def is_agent_side(obj_type, payload):
        ptype = payload.get("type")
        if obj_type == "event_msg" and ptype == "agent_message":
            return True
        if obj_type == "response_item" and ptype == "agent_message":
            return True
        if (obj_type == "response_item" and ptype == "message"
                and payload.get("role") == "assistant"):
            return True
        return False

    # Find anchors: try each needle from strongest to weakest, keep the
    # strongest needle that anchors anywhere.
    anchors: list = []
    matched_on = ""
    for needle in needles:
        forms = escape_forms(needle)
        for line_no, obj_type, payload in rows:
            if not is_agent_side(obj_type, payload):
                continue
            text = _item_text(payload)
            if any(f in text for f in forms) or needle in text:
                anchors.append(line_no)
        if anchors:
            matched_on = "scalar" if needle != term else "term"
            break
    if not anchors:
        link.reason = (
            "target text does not appear in any agent-side item of the "
            f"rollout {rollout_path}")
        return link
    link.anchor_lines = anchors
    link.matched_on = matched_on

    window_lo = max(1, anchors[0] - _CONTEXT_ITEMS_BEFORE)
    window_hi = anchors[-1]
    items: list = []
    encrypted_count = 0

    # File reads "immediately preceding" the anchor: keep the last few
    # regardless of the context window, so what the author had just read is
    # always on the record.
    preceding_reads: list = []
    for line_no, obj_type, payload in rows:
        if line_no >= anchors[0]:
            break
        ptype = payload.get("type")
        if (obj_type == "response_item"
                and ptype in ("custom_tool_call", "function_call",
                              "local_shell_call")
                and _looks_like_read(payload)):
            blob = payload.get("input") or payload.get("arguments") or ""
            preceding_reads.append(ThinkingItem(
                line_no=line_no, kind="file_read",
                text=_excerpt(str(blob), needles)))
    items.extend(preceding_reads[-_MAX_PRECEDING_READS:])
    read_lines = {it.line_no for it in items}

    for line_no, obj_type, payload in rows:
        if not (window_lo <= line_no <= window_hi):
            continue
        ptype = payload.get("type")
        if obj_type == "response_item" and ptype == "reasoning":
            summary_texts = [
                s.get("text") for s in (payload.get("summary") or [])
                if isinstance(s, dict) and isinstance(s.get("text"), str)
            ]
            content_texts = [
                c.get("text") for c in (payload.get("content") or [])
                if isinstance(c, dict) and isinstance(c.get("text"), str)
            ]
            plain = "\n".join(summary_texts + content_texts).strip()
            if plain:
                items.append(ThinkingItem(
                    line_no=line_no, kind="reasoning",
                    text=_excerpt(plain, needles)))
            elif payload.get("encrypted_content"):
                encrypted_count += 1
                items.append(ThinkingItem(
                    line_no=line_no, kind="reasoning", text="",
                    encrypted=True))
        elif is_agent_side(obj_type, payload):
            text = _item_text(payload)
            if not text:
                continue
            kind = ("assistant_message"
                    if payload.get("role") == "assistant"
                    else "agent_message")
            items.append(ThinkingItem(
                line_no=line_no, kind=kind,
                text=_excerpt(text, needles),
                author=str(payload.get("author") or "")))
        elif (obj_type == "response_item"
              and ptype in ("custom_tool_call", "function_call",
                            "local_shell_call")
              and line_no < anchors[0]
              and line_no not in read_lines
              and _looks_like_read(payload)):
            blob = payload.get("input") or payload.get("arguments") or ""
            items.append(ThinkingItem(
                line_no=line_no, kind="file_read",
                text=_excerpt(str(blob), needles)))

    # Deterministic cap: keep the items closest to the first anchor.
    if len(items) > _MAX_THINKING_ITEMS:
        items.sort(key=lambda it: (abs(it.line_no - anchors[0]), it.line_no))
        items = items[:_MAX_THINKING_ITEMS]
    items.sort(key=lambda it: it.line_no)

    link.items = items
    link.encrypted_reasoning_count = encrypted_count
    link.ok = True
    if encrypted_count and not any(
            it.kind == "reasoning" and not it.encrypted for it in items):
        link.reason = (
            f"{encrypted_count} reasoning item(s) in the window are stored "
            "as encrypted_content only (codex ran with reasoning summaries "
            "off); the plaintext record is the agent/sub-agent messages and "
            "file reads quoted here")
    return link

