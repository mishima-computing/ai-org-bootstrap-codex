#!/usr/bin/env python3
"""Adapter: use Codex's native `codex review` as a reviewer, in the shape the pipeline expects.

`codex review` is diff-anchored (it starts from the change set) but NOT diff-limited — it reads the
full changed files, their HEAD base, and unchanged dependents to judge cross-file impact (verified:
flagged a break in an *unchanged* caller). Its findings stay on the deliverable, not on scratch the way
a free-reading role carrier did. So it is a stronger fit for the review role than a generic carrier.

Output is a transcript followed by a final review block of priority-tagged findings:

    - [P1] Preserve the timeout parameter — /repo/lib.py:1-1
      When `nums` is empty, `len(nums)` is 0, so ...

This module parses that into `{"findings": [{file, line_range, severity, priority, claim, title}], "ok"}`
so `_linon_findings` (and the `while findings` repair loop) consume it unchanged. Parsing is pure and
unit-tested; the subprocess wrapper is a thin shell.
"""
from __future__ import annotations

import os
import re
import selectors
import signal
import subprocess
import time
from pathlib import Path

# No-output liveness watchdog for `codex review`. `codex review` is NOT `--json`, so it has no per-item
# heartbeat; it streams a human transcript. A healthy review still has silent think-windows, but a review
# that produces ZERO output for this long is stuck (a grandchild — sandbox helper / MCP child / language
# server — is holding the pipe with the direct child gone, or the model wedged). Cut it off here, well before
# the 600s hard wall, instead of blocking the whole pipeline. Tunable for tests via the env var.
NO_OUTPUT_TIMEOUT_ENV = "CODEX_REVIEW_NO_OUTPUT_TIMEOUT_SECONDS"
DEFAULT_NO_OUTPUT_TIMEOUT_SECONDS = 180.0
# After the direct child exits, a grandchild may still hold stdout/stderr open (no EOF). Drain briefly, then
# process-group-kill the grandchild so the read loop cannot wait forever on a pipe that never reaches EOF.
POST_EXIT_DRAIN_SECONDS = 5.0


def _no_output_timeout_seconds() -> float:
    raw = os.environ.get(NO_OUTPUT_TIMEOUT_ENV)
    if raw is None or raw.strip() == "":
        return DEFAULT_NO_OUTPUT_TIMEOUT_SECONDS
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_NO_OUTPUT_TIMEOUT_SECONDS
    return value if value > 0 else DEFAULT_NO_OUTPUT_TIMEOUT_SECONDS


def _kill_process_group(pgid: int) -> None:
    """Kill `codex review` AND any grandchildren it spawned, by the CAPTURED process-group id. Reuses the
    carrier harness's proven helper (one source of truth for the killpg discipline); falls back to the same
    killpg pattern if the import is unavailable. The pgid is the child's pid captured right after Popen
    (== the group id under start_new_session) — NOT looked up via os.getpgid at kill time, because proc.poll()
    reaps the leader and the lookup would then ESRCH while the grandchildren are still alive."""
    try:
        import carrier_harness
        carrier_harness._kill_process_group(pgid)
        return
    except Exception:  # noqa: BLE001 — fall back to the identical killpg pattern below
        pass
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError, AttributeError):  # already gone / non-POSIX
        pass


def _run_review_subprocess(argv: list[str], repo: str, timeout: float,
                           no_output_timeout: float) -> tuple[str, str, int | None, bool, bool]:
    """Run `argv` (a `codex review` invocation) under the SAME containment the carrier path uses, instead of
    the un-hardened `subprocess.run`: own session (start_new_session=True) so killpg reaps grandchildren that
    hold the pipe open, a no-output watchdog that cuts off a silent review before the hard wall, and a
    process-group kill on timeout / silence / in `finally`. stdin is closed (the stdin-wait hang guard).

    Returns (stdout, stderr, returncode, timed_out, frozen). `timed_out` = hard wall; `frozen` = no-output
    watchdog. The caller treats EITHER as "could not review" (fail-closed), never as a clean pass."""
    stdout_chunks: list[bytes] = []
    stderr_chunks: list[bytes] = []
    started = time.monotonic()
    last_output = started
    timed_out = False
    frozen = False

    proc = subprocess.Popen(
        argv,
        cwd=str(repo),
        stdin=subprocess.DEVNULL,   # codex never waits on stdin
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,     # own process group, so killpg reaps grandchildren that hold the pipe open
    )
    pgid = proc.pid                 # capture NOW (== the group id under start_new_session); proc.poll() later
    #                                 reaps the leader, so os.getpgid(proc.pid) at kill time would ESRCH while
    #                                 grandchildren still live. The group persists, keyed by this pgid.
    selector = selectors.DefaultSelector()
    if proc.stdout is not None:
        selector.register(proc.stdout, selectors.EVENT_READ, "stdout")
    if proc.stderr is not None:
        selector.register(proc.stderr, selectors.EVENT_READ, "stderr")

    exited_at = None
    try:
        while selector.get_map() or proc.poll() is None:
            now = time.monotonic()
            alive = proc.poll() is None
            if not alive and exited_at is None:
                exited_at = now
                # leader exited — reap any orphaned grandchildren NOW; once the orphan dies the pipe EOFs.
                _kill_process_group(pgid)
            # HARD WALL — an ABSOLUTE ceiling; fires even after the child exits (a grandchild can hold the pipe).
            if timeout and now - started >= timeout:
                timed_out = True
                _kill_process_group(pgid)
                break
            if alive and no_output_timeout and now - last_output >= no_output_timeout:
                frozen = True
                _kill_process_group(pgid)
                break
            if not alive and now - exited_at >= POST_EXIT_DRAIN_SECONDS:
                _kill_process_group(pgid)
                break

            deadlines = [0.05]
            if alive:
                if timeout:
                    deadlines.append(timeout - (now - started))
                if no_output_timeout:
                    deadlines.append(no_output_timeout - (now - last_output))
            else:
                deadlines.append(POST_EXIT_DRAIN_SECONDS - (now - exited_at))
            events = selector.select(max(0.0, min(deadlines)))
            if not events and proc.poll() is not None and not selector.get_map():
                break
            for key, _mask in events:
                chunk = os.read(key.fileobj.fileno(), 4096)
                if chunk:
                    (stdout_chunks if key.data == "stdout" else stderr_chunks).append(chunk)
                    last_output = time.monotonic()
                else:
                    try:
                        selector.unregister(key.fileobj)
                    except KeyError:
                        pass
                    key.fileobj.close()
    finally:
        selector.close()
        if proc.poll() is None:
            _kill_process_group(pgid)
        code = proc.wait()

    stdout = b"".join(stdout_chunks).decode("utf-8", "replace")
    stderr = b"".join(stderr_chunks).decode("utf-8", "replace")
    return stdout, stderr, code, timed_out, frozen

# `- [P1] <title> — <file>:<start>[-<end>]`  (em-dash separator; file may be absolute; end optional)
_FINDING_RE = re.compile(
    r"^[-*]\s*\[P(?P<prio>\d+)\]\s+(?P<title>.*?)\s+[—-]\s+(?P<file>.+?):(?P<start>\d+)(?:-(?P<end>\d+))?\s*$"
)
# P1 = blocking/critical, P2 = major, P3+ = minor. The repair loop treats ANY finding as blocking, so
# severity is advisory here; priority is preserved for downstream filtering/ranking.
_PRIORITY_SEVERITY = {1: "critical", 2: "major", 3: "minor"}


def parse_review_output(text: str, repo: str | None = None) -> list[dict]:
    """Parse `codex review` output into findings. A finding's body is the indented lines that follow its
    header until the next header / dedent. File paths are made repo-relative when `repo` is given (so the
    verdict-scope filter and provenance see the same shape as the role-carrier reviewer produced)."""
    findings: list[dict] = []
    lines = (text or "").splitlines()
    i = 0
    while i < len(lines):
        m = _FINDING_RE.match(lines[i].rstrip())
        if not m:
            i += 1
            continue
        start = int(m.group("start"))
        end = int(m.group("end")) if m.group("end") else start
        file = m.group("file").strip()
        if repo:
            try:
                file = str(Path(file).resolve().relative_to(Path(repo).resolve()))
            except (ValueError, OSError):
                pass  # already relative, or outside repo — keep as-is
        # gather the indented body block
        body: list[str] = []
        j = i + 1
        while j < len(lines) and (lines[j].strip() == "" or lines[j].startswith((" ", "\t"))):
            if lines[j].strip():
                body.append(lines[j].strip())
            elif body:                                  # blank line after body started -> block ends
                break
            j += 1
        prio = int(m.group("prio"))
        findings.append({
            "file": file,
            "line_range": {"start": start, "end": end},
            "priority": prio,
            "severity": _PRIORITY_SEVERITY.get(prio, "minor"),
            "title": m.group("title").strip(),
            "claim": " ".join(body) if body else m.group("title").strip(),
            "source": "codex-review",
        })
        i = j
    # `codex review` prints its final review block more than once; collapse exact repeats
    # (same file + span + title) so a single issue is not counted as several findings.
    deduped: list[dict] = []
    seen: set = set()
    for f in findings:
        key = (f["file"], f["line_range"]["start"], f["line_range"]["end"], f["title"])
        if key not in seen:
            seen.add(key)
            deduped.append(f)
    return deduped


def review(repo: str, *, base: str | None = None, timeout: int = 600,
           no_output_timeout: float | None = None) -> dict:
    """Run `codex review` in `repo` and return `{"findings": [...], "ok": bool, "raw": str}`.

    Diff scope: `--base <branch>` if given, else `--uncommitted` (staged+unstaged+untracked — the leaf's
    accumulated work). `ok` is False on a non-zero/timed-out/frozen review (treat as "could not review", not
    "clean"). `codex review` has no `-C`, so it runs with cwd=repo and stdin closed (the hang guard).

    The launch is HARDENED the same way the carrier path is (start_new_session + process-group kill +
    no-output watchdog) so a grandchild (sandbox helper / MCP child / language server) that outlives the
    direct child or holds its pipes cannot turn this into a 600s silent block plus a leaked process — see
    `_run_review_subprocess`. On timeout/freeze we return `ok=False` (fail-closed), never a fabricated pass."""
    argv = ["codex", "review"]
    argv += (["--base", base] if base else ["--uncommitted"])
    no_out = _no_output_timeout_seconds() if no_output_timeout is None else no_output_timeout
    try:
        raw, _stderr, code, timed_out, frozen = _run_review_subprocess(
            argv, repo, float(timeout), float(no_out))
    except OSError as exc:                                  # codex not installed / not executable
        return {"findings": [], "ok": False, "raw": f"{type(exc).__name__}: {exc}", "timed_out": False}
    result = {"findings": parse_review_output(raw, repo), "ok": code == 0 and not (timed_out or frozen),
              "raw": raw}
    if timed_out:
        result["timed_out"] = True
    if frozen:
        result["frozen"] = True
    return result


def self_test() -> int:
    sample = (
        "codex\n"
        "The changed `fetch` signature breaks an existing caller.\n\n"
        "Review comment:\n\n"
        "- [P1] Preserve the timeout parameter — /repo/lib.py:1-1\n"
        "  Existing code still calls `fetch` with two arguments, e.g. `caller.py`\n"
        "  invokes fetch(\"http://x\", 30), so this signature change makes run() fail.\n"
        "- [P2] Handle empty input in average — /repo/calc.py:6-6\n"
        "  When nums is empty, len(nums) is 0, so average([]) raises ZeroDivisionError.\n"
    )
    fs = parse_review_output(sample, repo="/repo")
    assert len(fs) == 2, fs
    assert fs[0]["file"] == "lib.py" and fs[0]["line_range"] == {"start": 1, "end": 1}, fs[0]
    assert fs[0]["priority"] == 1 and fs[0]["severity"] == "critical", fs[0]
    assert "two arguments" in fs[0]["claim"], fs[0]
    assert fs[1]["file"] == "calc.py" and fs[1]["priority"] == 2, fs[1]
    # no findings -> empty (a clean review)
    assert parse_review_output("codex\nLooks good. No issues found.\n") == []
    print("codex_review self-test passed (parse priority/file/line-range/body, repo-relative).")
    return 0


if __name__ == "__main__":
    raise SystemExit(self_test())
