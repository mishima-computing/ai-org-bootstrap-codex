"""Hang-safe Codex CLI carrier seam shared by all LLM-backed roles."""
from __future__ import annotations

import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import time
from typing import Any


def run_codex(
    repo: str | os.PathLike[str],
    prompt: str,
    sandbox: str,
    *,
    out_file: str | os.PathLike[str],
    model: str | None = None,
    resume_session: str | None = None,
    output_schema: str | os.PathLike[str] | None = None,
    no_output_timeout: float = 180.0,
    wall_timeout: float = 1800.0,
) -> dict:
    """Run ``codex exec`` and return final-message and session metadata.

    The prompt is always passed as an argv element and stdin is always DEVNULL;
    this avoids Codex waiting forever for additional stdin.
    """
    repo_path = Path(repo)
    out_path = Path(out_file)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    argv = [
        "codex",
        "exec",
        "--json",
        "--sandbox",
        sandbox,
        "-C",
        str(repo_path),
    ]
    if model:
        argv.extend(["-m", model])
    if output_schema:
        argv.extend(["--output-schema", str(output_schema)])
    if resume_session:
        argv.extend(["resume", "--json", resume_session])
    argv.extend(["-o", str(out_path), prompt])

    proc = subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
        bufsize=1,
    )
    pgid = os.getpgid(proc.pid)

    session_id: str | None = None
    events = 0
    killed = False
    started_at = time.monotonic()
    last_event_at = started_at

    sel = selectors.DefaultSelector()
    assert proc.stdout is not None
    sel.register(proc.stdout, selectors.EVENT_READ)

    try:
        while True:
            now = time.monotonic()
            if now - started_at >= wall_timeout:
                killed = True
                _kill_process_group(pgid)
                break
            if now - last_event_at >= no_output_timeout:
                killed = True
                _kill_process_group(pgid)
                break

            wait_for = min(
                0.1,
                max(0.0, wall_timeout - (now - started_at)),
                max(0.0, no_output_timeout - (now - last_event_at)),
            )
            ready = sel.select(wait_for)
            if not ready:
                continue

            line = proc.stdout.readline()
            if line == "":
                break

            last_event_at = time.monotonic()
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            events += 1
            found = _find_session_id(event)
            if found:
                session_id = found
    finally:
        sel.close()

    try:
        returncode = proc.wait(timeout=5.0)
    except subprocess.TimeoutExpired:
        killed = True
        _kill_process_group(pgid)
        returncode = proc.wait(timeout=5.0)

    last_message = out_path.read_text() if out_path.exists() else ""
    return {
        "ok": (returncode == 0 and not killed),
        "session_id": session_id,
        "last_message": last_message,
        "events": events,
    }


def _kill_process_group(pgid: int) -> None:
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _find_session_id(value: Any) -> str | None:
    if isinstance(value, dict):
        candidate = value.get("session_id")
        if isinstance(candidate, str) and candidate:
            return candidate
        for child in value.values():
            found = _find_session_id(child)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_session_id(child)
            if found:
                return found
    return None
