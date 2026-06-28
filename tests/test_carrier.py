from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import time

from ai_org.platform import carrier


def _write_fake_codex(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "codex"
    path.write_text(f"#!/usr/bin/env python3\n{body}")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def test_run_codex_builds_resume_argv_with_output_file(tmp_path, monkeypatch):
    argv_log = tmp_path / "argv.json"
    _write_fake_codex(
        tmp_path,
        """
import json
import os
import pathlib
import sys

pathlib.Path(os.environ["ARGV_LOG"]).write_text(json.dumps(sys.argv[1:]))
out_file = pathlib.Path(sys.argv[sys.argv.index("-o") + 1])
out_file.write_text("final message")
print(json.dumps({"type": "session_configured", "session_id": "sess-new"}), flush=True)
""",
    )
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("ARGV_LOG", str(argv_log))

    repo = tmp_path / "repo"
    repo.mkdir()
    schema = tmp_path / "schema.json"
    schema.write_text("{}")
    out_file = tmp_path / "out.txt"

    result = carrier.run_codex(
        repo,
        "fix it",
        "read-only",
        out_file=out_file,
        model="gpt-test",
        resume_session="sess-old",
        output_schema=schema,
    )

    assert result == {
        "ok": True,
        "session_id": "sess-new",
        "last_message": "final message",
        "events": 1,
    }
    assert json.loads(argv_log.read_text()) == [
        "exec",
        "--json",
        "--sandbox",
        "read-only",
        "-C",
        str(repo),
        "-m",
        "gpt-test",
        "--output-schema",
        str(schema),
        "resume",
        "--json",
        "sess-old",
        "-o",
        str(out_file),
        "fix it",
    ]


def test_run_codex_kills_stalled_process_group(tmp_path, monkeypatch):
    pid_file = tmp_path / "pid.txt"
    _write_fake_codex(
        tmp_path,
        """
import os
import pathlib
import time

pathlib.Path(os.environ["PID_FILE"]).write_text(str(os.getpid()))
time.sleep(30)
""",
    )
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("PID_FILE", str(pid_file))

    started = time.monotonic()
    result = carrier.run_codex(
        tmp_path,
        "stall",
        "read-only",
        out_file=tmp_path / "out.txt",
        no_output_timeout=0.5,
        wall_timeout=10.0,
    )

    assert time.monotonic() - started < 5.0
    assert result["ok"] is False
    assert result["events"] == 0
    pid = int(pid_file.read_text())
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        pass
    else:  # pragma: no cover - failure path
        raise AssertionError("stalled codex process was not killed")


def test_find_session_id_accepts_thread_id_and_session_id():
    # Real codex v0.142+ emits the conversation id as "thread_id" (thread.started); older codex used
    # "session_id". _find_session_id must accept both, including nested under "msg".
    assert carrier._find_session_id({"type": "thread.started", "thread_id": "uuid-new"}) == "uuid-new"
    assert carrier._find_session_id({"session_id": "uuid-old"}) == "uuid-old"
    assert carrier._find_session_id({"msg": {"thread_id": "uuid-nested"}}) == "uuid-nested"
    assert carrier._find_session_id({"type": "turn.completed"}) is None
