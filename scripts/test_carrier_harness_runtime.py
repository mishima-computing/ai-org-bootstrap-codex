#!/usr/bin/env python3
"""Tests for the carrier streaming watchdog — it must always terminate (no hang), even when the carrier exits
while a GRANDCHILD holds its pipe open (the bug that ran a goal for an hour). Plain def test_* + a __main__
runner (the scripts/ idiom, no pytest)."""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import carrier_harness as ch  # noqa: E402

_HERE = Path(__file__).resolve().parent


def test_clean_run_captures_output_and_exit_code():
    out, err, code, timed_out, frozen, killed = ch._stream_carrier_process(
        [sys.executable, "-c", "import sys; print('hi'); sys.exit(3)"], _HERE,
        timeout=30.0, no_output_timeout=10.0)
    assert "hi" in out, out
    assert code == 3, code
    assert not (timed_out or frozen or killed), (timed_out, frozen, killed)
    print("ok  clean run: output + exit code captured, nothing killed")


def test_no_output_timeout_kills_a_silent_carrier():
    start = time.monotonic()
    out, err, code, timed_out, frozen, killed = ch._stream_carrier_process(
        [sys.executable, "-c", "import time; time.sleep(30)"], _HERE,
        timeout=30.0, no_output_timeout=1.0)
    elapsed = time.monotonic() - start
    assert frozen and killed, (frozen, killed)
    assert elapsed < 6, f"a silent carrier must be killed near the no-output timeout, took {elapsed:.1f}s"
    print(f"ok  silent carrier frozen-killed in ~{elapsed:.1f}s")


def test_does_not_hang_and_reaps_a_grandchild_that_holds_the_pipe():
    # the regression: the carrier prints, then forks a GRANDCHILD that keeps the inherited stdout open and
    # sleeps; the carrier itself exits immediately. The old watchdog (gated on the child's liveness) waited on
    # the never-closing pipe forever. Now: the loop terminates promptly AND the orphan grandchild is REAPED
    # (killpg on the captured pgid at exit detection) — an orphan that outlives verification could mutate state.
    import os
    import tempfile
    pidfile = os.path.join(tempfile.gettempdir(), f"carrier_gc_{os.getpid()}.pid")
    if os.path.exists(pidfile):
        os.unlink(pidfile)
    script = (
        "import os, sys, time\n"
        "os.write(1, b'hello\\n')\n"
        "if os.fork() == 0:\n"
        f"    fd = os.open({pidfile!r}, os.O_CREAT | os.O_WRONLY, 0o644)\n"
        "    os.write(fd, str(os.getpid()).encode()); os.close(fd)\n"
        "    time.sleep(40)\n"            # grandchild holds the inherited stdout open
        "    os._exit(0)\n"
        "os._exit(0)\n"                   # the carrier (parent) exits right away
    )
    start = time.monotonic()
    out, err, code, timed_out, frozen, killed = ch._stream_carrier_process(
        [sys.executable, "-c", script], _HERE, timeout=60.0, no_output_timeout=30.0)
    elapsed = time.monotonic() - start
    assert "hello" in out, out
    assert elapsed < ch.POST_EXIT_DRAIN_SECONDS + 5, \
        f"watchdog HUNG on a grandchild-held pipe: {elapsed:.1f}s (drain cap {ch.POST_EXIT_DRAIN_SECONDS}s)"
    for _ in range(30):
        if os.path.exists(pidfile):
            break
        time.sleep(0.1)
    gc_pid = int(open(pidfile).read())
    time.sleep(0.3)
    try:
        os.kill(gc_pid, 0)
        alive = True
    except ProcessLookupError:
        alive = False
    os.unlink(pidfile)
    assert not alive, f"the orphan grandchild {gc_pid} was NOT reaped — it can still mutate state"
    print(f"ok  no hang + grandchild reaped: stopped in ~{elapsed:.1f}s, orphan dead")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
    print(f"\n{len(fns)} passed")
