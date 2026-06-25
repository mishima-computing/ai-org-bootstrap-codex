#!/usr/bin/env python3
"""Box-backed conformance runner — the engine's CLEAN boundary to an isolated host executor (ADR-0022).

The engine (controller_pipeline) verifies a built artifact by RUNNING commands against it. WHERE those
commands run is a HOST decision, not an engine one: the local single-host simulation runs them in a bounded
subprocess; a box host (e.g. Shagiri's box) runs them inside an isolated box. This factory lets the host inject
that choice WITHOUT the engine importing ANY host/box code — the host advertises a SHIM command via an env var
and the engine speaks a tiny, explicit wire protocol to it. The engine stays host-agnostic; the box stays the
containment boundary.

  AI_ORG_RUNNER_CMD                 a shell-tokenized command. When set, conformance runs THROUGH this shim
                                    (the box boundary). Unset -> the in-process bounded subprocess runner.
  AI_ORG_RUNNER_FALLBACK_SUBPROCESS opt-in (1/true/yes/on). If a shim TRANSPORT step fails, run the command
                                    LOCALLY via the subprocess runner instead of surfacing a could-not-run.
                                    For box-LESS dev/CI ONLY — it deliberately gives up the requested isolation.
  AI_ORG_RUNNER_TRANSPORT_SLACK     optional positive seconds beyond the command timeout for shim staging +
                                    response framing. Unset -> a generous box-staging default.

== Wire protocol (engine <-> shim), version AOBRUN1 ==
Binary-safe, length-prefixed, sentinel-framed, so a command's stdout/stderr may contain ANYTHING (newlines,
the sentinel text itself, non-UTF8 bytes) without corrupting the frame — mirroring how a box CLI wrapper hands
a captured result back over a pipe.

  REQUEST  (engine -> shim):
      argv  = [*AI_ORG_RUNNER_CMD, <cwd>]            # cwd to run the command in, as the LAST argv element
      stdin = AOBRUN1\n                              # magic sentinel + version
              <len(command_bytes)>\n <command_bytes> # the shell command to run, length-prefixed
              <len(stdin_bytes)>\n   <stdin_bytes>   # the command's own stdin payload, length-prefixed

  RESPONSE (shim -> engine), on the shim's STDOUT:
      AOBRUN1\n
      <returncode>\n                                # the COMMAND's exit code (NOT the shim's)
      <len(stdout_bytes)>\n <stdout_bytes>          # the command's captured stdout
      <len(stderr_bytes)>\n <stderr_bytes>          # the command's captured stderr

The shim's OWN process exit code is the TRANSPORT status. shim-exit 0 + a well-formed RESPONSE frame == the
command RAN: its result (including a nonzero command exit) is INSIDE the frame and is reported faithfully. A
nonzero shim exit, an unrunnable/missing shim, a shim timeout, or a malformed/absent frame is a TRANSPORT
FAILURE — the requested isolation did NOT happen. The engine surfaces that as a could-not-run RunResult
(returncode 127, with `executable file not found` in stderr), which conformance._failure_classification routes
to `infra` -> escalate / clean-retry / unverified. NEVER a fabricated pass, NEVER blamed on product code. We do
NOT silently fall back in a way that hides the lost isolation; the only fallback is the EXPLICIT opt-in above.
"""
from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Optional

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import conformance  # noqa: E402  — RunResult / subprocess_runner live here; the box boundary's data shape.

_MAGIC = b"AOBRUN1"
# Extra wall-clock the engine waits for the shim BEYOND the command's own timeout: the first per-cwd shim call
# may also cold-stage the artifact into the box before the command can run, and the shim still has to frame and
# return the result. A shim silent past this is itself a transport failure (a hung box).
_TRANSPORT_SLACK = 180.0
_TRANSPORT_SLACK_ENV = "AI_ORG_RUNNER_TRANSPORT_SLACK"
_TRUTHY = {"1", "true", "yes", "on"}


def get_conformance_runner(timeout: float = 60.0) -> conformance.Runner:
    """The injection point. Returns the runner the engine should hand the conformance gate:

      * AI_ORG_RUNNER_CMD set  -> a runner that executes EACH command THROUGH the host shim (box isolation),
                                  speaking the AOBRUN1 wire protocol above. Zero engine import of host code.
      * AI_ORG_RUNNER_CMD unset-> conformance.subprocess_runner(timeout) unchanged (local bounded subprocess).
    """
    shim = os.environ.get("AI_ORG_RUNNER_CMD")
    if not shim or not shim.strip():
        return conformance.subprocess_runner(timeout=timeout)
    return _shim_runner(shlex.split(shim), timeout)


def _is_truthy(value: Optional[str]) -> bool:
    return value is not None and value.strip().lower() in _TRUTHY


def _transport_slack() -> float:
    raw = os.environ.get(_TRANSPORT_SLACK_ENV)
    if raw is None or not raw.strip():
        return _TRANSPORT_SLACK
    try:
        value = float(raw)
    except ValueError:
        return _TRANSPORT_SLACK
    return value if value > 0 else _TRANSPORT_SLACK


class _TransportFailure(Exception):
    """The shim could not run the command and return a framed result — the requested isolation did NOT happen.
    Raised inside the shim runner; mapped to an infra/could-not-run RunResult (or the opt-in fallback)."""


def _shim_runner(argv_prefix: list, default_timeout: float) -> conformance.Runner:
    """A conformance.Runner that routes every command through the host shim. On a TRANSPORT failure it returns
    an infra/could-not-run RunResult (returncode 127) — never a pass, never a product blame — unless the
    explicit AI_ORG_RUNNER_FALLBACK_SUBPROCESS opt-in re-runs the command locally for a box-less environment."""
    fallback = conformance.subprocess_runner(timeout=default_timeout) \
        if _is_truthy(os.environ.get("AI_ORG_RUNNER_FALLBACK_SUBPROCESS")) else None

    def _run(cmd: str, *, cwd: Optional[str] = None, stdin: Optional[str] = None,
             timeout: Optional[float] = None) -> conformance.RunResult:
        try:
            return _invoke_shim(argv_prefix, cmd, cwd, stdin, timeout, default_timeout)
        except _TransportFailure as tf:
            if fallback is not None:                  # explicit opt-in: give up isolation, run locally instead
                return fallback(cmd, cwd=cwd, stdin=stdin, timeout=timeout)
            return _infra_result(str(tf))

    return _run


def _invoke_shim(argv_prefix: list, cmd: str, cwd, stdin, timeout, default_timeout) -> conformance.RunResult:
    request = _frame_request(cmd, stdin or "")
    argv = [*argv_prefix, "" if cwd is None else str(cwd)]
    effective = default_timeout if timeout is None else timeout
    transport_timeout = effective + _transport_slack()
    try:
        proc = subprocess.run(argv, input=request, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              timeout=transport_timeout)
    except subprocess.TimeoutExpired:
        raise _TransportFailure(f"shim did not respond within {transport_timeout:.0f}s (box hang)")
    except (FileNotFoundError, PermissionError, NotADirectoryError, OSError) as exc:
        raise _TransportFailure(f"shim not runnable: {exc!r}")
    if proc.returncode != 0:
        raise _TransportFailure(
            f"shim exited {proc.returncode} at the transport layer: {_tail(proc.stderr)}")
    result = _parse_response(proc.stdout)
    if result is None:
        raise _TransportFailure("shim returned a malformed/absent AOBRUN1 result frame")
    return result


def _frame_request(command: str, stdin: str) -> bytes:
    """Build the AOBRUN1 REQUEST frame (magic + length-prefixed command + length-prefixed command-stdin)."""
    cmd_bytes = command.encode("utf-8")
    in_bytes = stdin.encode("utf-8")
    return b"".join([
        _MAGIC, b"\n",
        str(len(cmd_bytes)).encode("ascii"), b"\n", cmd_bytes,
        str(len(in_bytes)).encode("ascii"), b"\n", in_bytes,
    ])


def _parse_response(buf: bytes) -> Optional[conformance.RunResult]:
    """Parse an AOBRUN1 RESPONSE frame -> RunResult, or None if the frame is malformed/absent (a transport
    failure). Length-prefixing makes this exact even when stdout/stderr contain newlines or the sentinel."""
    try:
        magic, rest = buf.split(b"\n", 1)
        if magic != _MAGIC:
            return None
        rc_line, rest = rest.split(b"\n", 1)
        returncode = int(rc_line)
        stdout, rest = _read_block(rest)
        stderr, _rest = _read_block(rest)
    except (ValueError, IndexError):
        return None
    return conformance.RunResult(returncode, stdout.decode("utf-8", "replace"),
                                 stderr.decode("utf-8", "replace"))


def _read_block(buf: bytes) -> tuple:
    """Read one `<len>\\n<bytes>` block; raise ValueError on any framing inconsistency."""
    len_line, rest = buf.split(b"\n", 1)
    n = int(len_line)
    if n < 0 or n > len(rest):
        raise ValueError("length-prefix overruns the frame")
    return rest[:n], rest[n:]


def _infra_result(detail: str) -> conformance.RunResult:
    """A could-not-run RunResult for a transport failure. returncode 127 AND the `executable file not found`
    phrasing both route to `infra` in conformance._failure_classification (belt-and-suspenders), so a checker
    that inspects only the code OR only the text still classifies this as escalate/unverified — never a pass,
    never product code."""
    return conformance.RunResult(
        127, "",
        f"<runner-shim transport failure: {detail}; executable file not found — "
        "requested box isolation did not happen>")


def _tail(data, limit: int = 400) -> str:
    text = data.decode("utf-8", "replace") if isinstance(data, (bytes, bytearray)) else (data or "")
    return text[-limit:]
