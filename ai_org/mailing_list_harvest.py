"""Subscriber-side harvesting of recorded contributor QA evidence."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import shlex
from typing import Any, Mapping

from ai_org import git_wrapper
from ai_org.body_codec import strict_json_loads
from judge import rollout, runlog


_ITEM_SUBJECT_RE = re.compile(r"\[([^\[\]\r\n]+#[^\[\]\r\n]+)\]\Z")
_IMPLEMENTATION_RESULT_PREFIX = "patch: implementation result for "
_ACCEPTANCE_SUBJECTS = {"acceptance: reachable", "acceptance: blocked"}
_CELL_RUNNING_RE = re.compile(
    r"^Script running with cell ID `?(?P<cell>[A-Za-z0-9._:-]+)`?$"
)
_CMD_LITERAL_RE = re.compile(r"(?:\bcmd|\"cmd\")\s*:\s*(\"(?:\\.|[^\"\\])*\")")
_CELL_LITERAL_RE = re.compile(
    r"\bcell_id\s*:\s*(\"(?:\\.|[^\"\\])*\")"
)
_EXIT_LINE_RE = re.compile(
    r"(?:Process|Command|Script) (?:exited|failed)(?: with)? (?:code|exit code)[: ]+[-+]?\d+\Z",
    re.IGNORECASE,
)
_PYTEST_SUMMARY_RE = re.compile(
    r"(?:^|\s)\d+\s+(?:failed|passed|error|errors|skipped|xfailed|xpassed)(?:[,\s]|\Z)",
    re.IGNORECASE,
)
_FRAMEWORK_OUTCOME_RES = (
    _PYTEST_SUMMARY_RE,
    re.compile(r"^(?:OK|FAILED(?: \(.+\))?|PASS|FAIL)\Z"),
    re.compile(r"^(?:ok|FAIL)\s+\S+"),
    re.compile(r"^test result: ", re.IGNORECASE),
    re.compile(r"^(?:Tests|Test Suites):\s", re.IGNORECASE),
    re.compile(r"^# (?:pass|fail)\s", re.IGNORECASE),
    re.compile(r"^All checks passed!\Z"),
    re.compile(r"^make: \*\*\* .+ Error"),
    re.compile(r"^no tests ran in \S+", re.IGNORECASE),
    re.compile(r"^Success: no issues found in ", re.IGNORECASE),
)


@dataclass(frozen=True)
class _ItemCommit:
    sha: str
    committed_at: str
    item_id: str


@dataclass(frozen=True)
class _RecordedCheck:
    sequence: int
    command: str
    outcome_lines: tuple[str, ...]


@dataclass(frozen=True)
class _ItemEvidence:
    sessions: tuple[str, ...]
    run_id: str
    checks: tuple[_RecordedCheck, ...]
    narration: tuple[str, ...]


def _harvest_and_post_qa(
    repo,
    contrib_branch,
    *,
    ctx=None,
    read_posts,
    post_message,
) -> dict:
    """Read carrier archives and append at most one QA post per patch item."""
    # Memento: harvesting is a subscriber-side read of recorded execution.
    # Contributor commit machinery remains untouched; only the list is appended.
    repo_path = Path(repo).resolve()
    branch = str(contrib_branch)
    existing = {
        item_ref
        for entry in read_posts(repo_path, kinds={"qa"})
        if isinstance((item_ref := entry.get("refs", {}).get("item")), str)
    }
    result: dict[str, Any] = {
        "ok": True,
        "posted": [],
        "skipped_existing": [],
        "unavailable": [],
    }

    for item in _item_commits(repo_path, branch):
        if item.sha in existing:
            result["skipped_existing"].append(item.item_id)
            continue
        evidence = _resolve_item_evidence(repo_path, branch, item)
        if evidence is None:
            result["unavailable"].append(item.item_id)
            continue
        refs: dict[str, Any] = {
            "item": item.sha,
            "contrib": branch,
            "session": list(evidence.sessions),
            "run": evidence.run_id,
        }
        posted = post_message(
            repo_path,
            kind="qa",
            subject=item.item_id,
            body=_qa_body(evidence),
            refs=refs,
            ctx=ctx,
        )
        if not posted.get("ok"):
            result["ok"] = False
            break
        if posted.get("existing"):
            result["skipped_existing"].append(item.item_id)
        else:
            result["posted"].append(item.item_id)
        existing.add(item.sha)
    return result


def _item_commits(repo: Path, branch: str) -> list[_ItemCommit]:
    history = git_wrapper._git_bytes(  # noqa: SLF001 - delimiter-safe read through the Git gateway.
        repo,
        "log",
        "--first-parent",
        "-z",
        "--format=%H%x00%cI%x00%s",
        branch,
    )
    if history.returncode != 0:
        return []
    fields = history.stdout.split(b"\0")
    if fields and fields[-1] == b"":
        fields.pop()
    if len(fields) % 3:
        return []

    newest_first: list[_ItemCommit] = []
    for index in range(0, len(fields), 3):
        sha = fields[index].decode("ascii", errors="strict")
        committed_at = fields[index + 1].decode("utf-8", errors="strict")
        subject = fields[index + 2].decode("utf-8", errors="strict")
        if subject in _ACCEPTANCE_SUBJECTS or subject.startswith(_IMPLEMENTATION_RESULT_PREFIX):
            continue
        match = _ITEM_SUBJECT_RE.search(subject)
        if match is not None:
            newest_first.append(_ItemCommit(sha, committed_at, match.group(1)))
            continue
        break
    return list(reversed(newest_first))


def _resolve_item_evidence(repo: Path, branch: str, item: _ItemCommit) -> _ItemEvidence | None:
    run_link, selected_run = runlog.select_run(
        str(repo), branch, item.committed_at, [item.item_id]
    )
    if not run_link.ok or selected_run is None:
        return None
    _first_call, calls = runlog.select_call(selected_run, [item.item_id])
    if not calls:
        return None

    session_rows: list[tuple[str, Path]] = []
    seen_sessions: set[str] = set()
    sessions_root = Path(rollout.DEFAULT_SESSIONS_ROOT)
    for call in calls:
        link = rollout.match_session(
            selected_run.run_dir,
            call,
            str(repo),
            sessions_root=sessions_root,
            probes=[item.item_id],
        )
        if (
            not link.ok
            or link.confidence not in {"session-id", "session-id+content"}
            or not link.uuid
            or not link.rollout_path
        ):
            return None
        if link.uuid in seen_sessions:
            continue
        path = Path(link.rollout_path)
        if not path.is_file():
            return None
        seen_sessions.add(link.uuid)
        session_rows.append((link.uuid, path))

    checks: list[_RecordedCheck] = []
    narration: list[str] = []
    for session_index, (_uuid, path) in enumerate(session_rows):
        extracted = _extract_rollout(path)
        if extracted is None:
            return None
        session_checks, session_narration = extracted
        checks.extend(
            _RecordedCheck(
                sequence=(session_index << 32) + check.sequence,
                command=check.command,
                outcome_lines=check.outcome_lines,
            )
            for check in session_checks
        )
        narration.extend(session_narration)
    checks.sort(key=lambda check: check.sequence)
    return _ItemEvidence(
        sessions=tuple(uuid for uuid, _path in session_rows),
        run_id=Path(selected_run.run_dir).name,
        checks=tuple(checks),
        narration=tuple(narration),
    )


def _extract_rollout(path: Path) -> tuple[list[_RecordedCheck], list[str]] | None:
    try:
        rows = list(_jsonl_objects(path))
    except (OSError, UnicodeDecodeError):
        return None
    if not rows:
        return None

    narration: list[str] = []
    pending_calls: dict[str, dict[str, Any]] = {}
    ambiguous_call_ids: set[str] = set()
    anonymous_calls: dict[str, list[dict[str, Any]]] = {"custom": [], "function": []}
    active_cells: dict[str, dict[str, Any]] = {}
    ambiguous_cells: set[str] = set()
    checks: list[_RecordedCheck] = []

    for sequence, obj in enumerate(rows):
        payload = obj.get("payload")
        if not isinstance(payload, Mapping):
            continue
        payload_type = payload.get("type")
        if (
            obj.get("type") in {"event_msg", "response_item"}
            and payload_type == "agent_message"
        ):
            text = _agent_message_text(payload)
            if text:
                narration.append(text)
        if obj.get("type") != "response_item":
            continue
        if payload_type in {"custom_tool_call", "function_call"}:
            family = "custom" if payload_type == "custom_tool_call" else "function"
            call = {
                "sequence": sequence,
                "name": str(payload.get("name") or ""),
                "input": payload.get("input", payload.get("arguments", "")),
                "family": family,
            }
            call_id = _call_id(payload)
            if call_id:
                if call_id in pending_calls or call_id in ambiguous_call_ids:
                    pending_calls.pop(call_id, None)
                    ambiguous_call_ids.add(call_id)
                else:
                    pending_calls[call_id] = call
            else:
                anonymous_calls[family].append(call)
            continue
        if payload_type not in {"custom_tool_call_output", "function_call_output"}:
            continue
        family = "custom" if payload_type == "custom_tool_call_output" else "function"
        call_id = _call_id(payload)
        call = pending_calls.pop(call_id, None) if call_id and call_id not in ambiguous_call_ids else None
        if call is None and not call_id and anonymous_calls[family]:
            call = anonymous_calls[family].pop(0)
        if call is None or call.get("family") != family:
            continue
        output = _output_text(payload.get("output"))
        _consume_tool_pair(call, output, active_cells, ambiguous_cells, checks)

    checks.sort(key=lambda check: check.sequence)
    return checks, narration


def _jsonl_objects(path: Path):
    with path.open("r", encoding="utf-8", errors="strict") as handle:
        for raw_line in handle:
            try:
                value = strict_json_loads(raw_line)
            except (TypeError, ValueError):
                continue
            if isinstance(value, dict):
                yield value


def _consume_tool_pair(
    call: Mapping[str, Any],
    output: str,
    active_cells: dict[str, dict[str, Any]],
    ambiguous_cells: set[str],
    checks: list[_RecordedCheck],
) -> None:
    name = str(call.get("name") or "").rsplit(".", 1)[-1]
    if name in {"wait", "write_stdin"}:
        cell_id = _cell_id_from_input(call.get("input"))
        active = active_cells.get(cell_id) if cell_id else None
        if active is None:
            return
        active["outputs"].append(output)
        running_cell = _running_cell_id(output)
        if running_cell == cell_id:
            return
        if running_cell:
            active_cells.pop(cell_id, None)
            ambiguous_cells.add(cell_id)
            return
        active_cells.pop(cell_id, None)
        _finish_check(active, checks)
        return

    command = _command_from_call(name, call.get("input"))
    if command is None or not _is_check_command(command):
        return
    record = {
        "sequence": int(call["sequence"]),
        "command": command,
        "outputs": [output],
    }
    cell_id = _running_cell_id(output)
    if cell_id:
        if cell_id in active_cells or cell_id in ambiguous_cells:
            active_cells.pop(cell_id, None)
            ambiguous_cells.add(cell_id)
        else:
            active_cells[cell_id] = record
        return
    _finish_check(record, checks)


def _finish_check(record: Mapping[str, Any], checks: list[_RecordedCheck]) -> None:
    outcome_lines = tuple(
        line
        for part in record["outputs"]
        for line in _outcome_lines(str(part))
    )
    if not outcome_lines:
        return
    checks.append(
        _RecordedCheck(
            sequence=int(record["sequence"]),
            command=str(record["command"]),
            outcome_lines=outcome_lines,
        )
    )


def _call_id(payload: Mapping[str, Any]) -> str:
    value = payload.get("call_id") or payload.get("id")
    return str(value) if value else ""


def _agent_message_text(payload: Mapping[str, Any]) -> str:
    message = payload.get("message")
    if isinstance(message, str):
        return message
    parts: list[str] = []
    for item in payload.get("content") or ():
        if not isinstance(item, Mapping):
            continue
        value = item.get("text") or item.get("input_text")
        if isinstance(value, str) and value:
            parts.append(value)
    return "\n".join(parts)


def _output_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        for key in ("text", "input_text", "output_text", "output", "content"):
            if key in value:
                return _output_text(value[key])
        return ""
    if isinstance(value, (list, tuple)):
        parts = [_output_text(item) for item in value]
        return "\n".join(part for part in parts if part)
    return ""


def _command_from_call(name: str, raw_input: Any) -> str | None:
    if name not in {"exec", "exec_command"}:
        return None
    if isinstance(raw_input, Mapping):
        command = raw_input.get("cmd")
        return command if isinstance(command, str) else None
    if not isinstance(raw_input, str):
        return None
    try:
        decoded = strict_json_loads(raw_input)
    except (TypeError, ValueError):
        decoded = None
    if isinstance(decoded, Mapping) and isinstance(decoded.get("cmd"), str):
        return str(decoded["cmd"])
    source = raw_input if name == "exec_command" else _single_exec_command_argument(raw_input)
    if source is None:
        return None
    literals = _CMD_LITERAL_RE.findall(source)
    if len(literals) != 1:
        return None
    try:
        command = strict_json_loads(literals[0])
    except (TypeError, ValueError):
        return None
    return command if isinstance(command, str) and "\n" not in command else None


def _single_exec_command_argument(source: str) -> str | None:
    matches = list(re.finditer(r"\btools\.exec_command\s*\(", source))
    if len(matches) != 1:
        return None
    start = matches[0].end()
    depth = 1
    quote = ""
    escaped = False
    for index in range(start, len(source)):
        char = source[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
            continue
        if char in {"\"", "'"}:
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return source[start:index]
    return None


def _cell_id_from_input(raw_input: Any) -> str:
    if isinstance(raw_input, Mapping):
        value = raw_input.get("cell_id", raw_input.get("session_id"))
        return str(value) if value else ""
    if not isinstance(raw_input, str):
        return ""
    try:
        decoded = strict_json_loads(raw_input)
    except (TypeError, ValueError):
        decoded = None
    if isinstance(decoded, Mapping):
        value = decoded.get("cell_id", decoded.get("session_id"))
        return str(value) if value else ""
    match = _CELL_LITERAL_RE.search(raw_input)
    if match is None:
        return ""
    try:
        value = strict_json_loads(match.group(1))
    except (TypeError, ValueError):
        return ""
    return str(value)


def _running_cell_id(output: str) -> str:
    for line in output.splitlines():
        if not line.strip():
            continue
        match = _CELL_RUNNING_RE.fullmatch(line.strip())
        return match.group("cell") if match is not None else ""
    return ""


def _is_check_command(command: str) -> bool:
    if not command or "\n" in command or any(token in command for token in ("|", ";", ">", "<")):
        return False
    segments = [segment.strip() for segment in command.split("&&")]
    if not all(segments):
        return False
    if segments and segments[0].startswith("cd "):
        segments.pop(0)
    return bool(segments) and all(_is_check_segment(segment) for segment in segments)


def _is_check_segment(segment: str) -> bool:
    try:
        words = shlex.split(segment)
    except ValueError:
        return False
    while words and ("=" in words[0] and not words[0].startswith(("=", "-"))):
        words.pop(0)
    if words and words[0] == "env":
        words.pop(0)
        while words and "=" in words[0]:
            words.pop(0)
    if not words:
        return False
    executable = Path(words[0]).name
    args = words[1:]
    if any(arg in {"--fix", "--update", "-u", "--accept"} or arg.startswith("--fix=") for arg in args):
        return False
    if executable in {"pytest", "py.test", "tox", "nox", "mypy", "pyright", "flake8", "pylint", "shellcheck"}:
        return True
    if executable.startswith("python"):
        return len(args) >= 2 and args[0] == "-m" and args[1] in {"pytest", "unittest", "compileall"}
    if executable in {"uv", "poetry", "pipenv"} and args[:1] == ["run"]:
        return _is_check_segment(shlex.join(args[1:]))
    if executable == "coverage" and args[:2] == ["run", "-m"]:
        return len(args) >= 3 and args[2] == "pytest"
    if executable == "ruff":
        return args[:1] == ["check"] or (args[:1] == ["format"] and "--check" in args)
    if executable == "black":
        return "--check" in args
    if executable == "go":
        return args[:1] in (["test"], ["vet"])
    if executable == "cargo":
        return args[:1] in (["test"], ["check"], ["clippy"]) or (args[:1] == ["fmt"] and "--check" in args)
    if executable in {"npm", "pnpm", "yarn", "bun"}:
        return bool(args) and (args[0] == "test" or (args[0] == "run" and len(args) > 1 and args[1] in {"test", "check", "lint", "typecheck"}))
    if executable in {"make", "just"}:
        targets = [arg for arg in args if not arg.startswith("-") and "=" not in arg]
        return bool(targets) and all(target in {"test", "check", "lint", "verify"} for target in targets)
    if executable == "cue":
        return args[:1] == ["vet"]
    if executable == "git":
        return args[:2] == ["diff", "--check"]
    return executable in {"test", "["}


def _outcome_lines(output: str) -> tuple[str, ...]:
    selected: list[str] = []
    for raw_line in output.splitlines():
        line = raw_line.rstrip("\r")
        candidate = line.strip()
        if _EXIT_LINE_RE.fullmatch(candidate):
            selected.append(line)
        elif any(pattern.search(candidate) for pattern in _FRAMEWORK_OUTCOME_RES):
            selected.append(line)
    return tuple(selected)


def _qa_body(evidence: _ItemEvidence) -> str:
    lines = ["Checks:"]
    lines.extend(
        f"- {check.command} — {' | '.join(check.outcome_lines)}"
        for check in evidence.checks
    )
    lines.extend(["", "Recorded-Narration (recorded self-narration, verbatim):"])
    if evidence.narration:
        lines.append("\n\n".join(evidence.narration))
    return "\n".join(lines)
