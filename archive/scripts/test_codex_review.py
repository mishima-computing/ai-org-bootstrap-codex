#!/usr/bin/env python3
"""Tests for codex_review: parsing AND the hardened subprocess containment.

The containment tests install a FAKE `codex` on PATH whose `review` subcommand simulates the failure modes
the real launch must survive: a silent hang (no-output watchdog must kill it well before the wall clock),
a grandchild that outlives the direct child (process-group kill must reap it — no leaked pid), and a normal
quick review (its output must still parse). These are the un-hardened twin of the carrier hang the harness
already solved; here we prove codex_review.review kills the process GROUP, not just the direct child."""
from __future__ import annotations

import os
import signal
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import codex_review


def _write_fake_codex(dir_path: Path, body: str) -> Path:
    """Install an executable `codex` shim in `dir_path` whose `review` subcommand runs `body` (python)."""
    p = dir_path / "codex"
    p.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "if len(sys.argv) >= 2 and sys.argv[1] == 'review':\n"
        + "".join("    " + line + "\n" for line in body.splitlines())
        + "    sys.exit(0)\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )
    p.chmod(0o755)
    return p


class _PathWith:
    def __init__(self, dir_path: Path):
        self.dir_path = str(dir_path)

    def __enter__(self):
        self._old = os.environ.get("PATH", "")
        os.environ["PATH"] = self.dir_path + os.pathsep + self._old
        return self

    def __exit__(self, *exc):
        os.environ["PATH"] = self._old


def test_parse_self_test():
    assert codex_review.self_test() == 0


def test_normal_quick_review_parses():
    """A fast review that prints findings -> ok True and findings parsed (containment is transparent)."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        bindir = td / "bin"
        bindir.mkdir()
        repo = td / "repo"
        repo.mkdir()
        _write_fake_codex(bindir, (
            "print('codex')\n"
            "print('Review comment:')\n"
            "print('- [P1] Preserve the timeout parameter — lib.py:1-1')\n"
            "print('  Existing callers pass two arguments.')\n"
        ))
        with _PathWith(bindir):
            rv = codex_review.review(str(repo))
        assert rv["ok"] is True, rv
        assert len(rv["findings"]) == 1, rv
        assert rv["findings"][0]["file"] == "lib.py"
        assert rv["findings"][0]["priority"] == 1


def test_silent_hang_is_killed_by_watchdog_not_walled():
    """A review that produces NO output is killed by the no-output watchdog well before the wall clock, and
    returns a fail-closed (ok False, frozen) result — NOT a 600s block and NOT a fabricated pass."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        bindir = td / "bin"
        bindir.mkdir()
        repo = td / "repo"
        repo.mkdir()
        _write_fake_codex(bindir, (
            "import time\n"
            "time.sleep(600)\n"          # silent forever
        ))
        with _PathWith(bindir):
            t0 = time.monotonic()
            # tiny watchdog so the test is fast; wall clock stays high to prove the WATCHDOG fired, not the wall
            rv = codex_review.review(str(repo), timeout=600, no_output_timeout=0.6)
            elapsed = time.monotonic() - t0
        assert rv["ok"] is False, rv
        assert rv.get("frozen") is True, rv
        assert elapsed < 10, f"watchdog should kill quickly, took {elapsed:.1f}s"


def test_grandchild_outliving_child_is_reaped_by_killpg():
    """The direct child spawns a grandchild that holds stdout open and outlives it, then the child exits. The
    process-group kill must reap the grandchild (no leaked pid) instead of blocking forever on a pipe with no
    EOF — the exact failure the carrier harness records."""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        bindir = td / "bin"
        bindir.mkdir()
        repo = td / "repo"
        repo.mkdir()
        pidfile = td / "grandchild.pid"
        # The fake codex forks a grandchild that inherits stdout (holds the pipe), records its pid, then the
        # direct child exits immediately while the grandchild sleeps. No output is ever flushed -> the launcher
        # must reap via killpg (post-exit drain + watchdog), proving the GROUP is killed, not just the child.
        _write_fake_codex(bindir, (
            "import os, time, sys\n"
            f"pid = os.fork()\n"
            "if pid == 0:\n"
            "    # grandchild: keep stdout open, sleep long\n"
            f"    open({str(pidfile)!r}, 'w').write(str(os.getpid()))\n"
            "    time.sleep(600)\n"
            "    os._exit(0)\n"
            "# direct child exits immediately, leaving the grandchild holding stdout\n"
            "sys.exit(0)\n"
        ))
        with _PathWith(bindir):
            t0 = time.monotonic()
            rv = codex_review.review(str(repo), timeout=600, no_output_timeout=3.0)
            elapsed = time.monotonic() - t0
        # The direct child exited 0; the point is the launcher did NOT block on the grandchild-held pipe —
        # it drained, reaped the group, and returned fast (the un-hardened twin would have blocked ~600s here).
        assert elapsed < 15, f"post-exit drain + killpg should finish fast, took {elapsed:.1f}s"
        # the grandchild must be dead (killpg reaped the group); assert no leaked pid
        for _ in range(50):
            if pidfile.is_file():
                break
            time.sleep(0.05)
        assert pidfile.is_file(), "grandchild never recorded its pid"
        gpid = int(pidfile.read_text().strip())
        time.sleep(0.3)  # let SIGKILL settle
        alive = True
        try:
            os.kill(gpid, 0)
        except ProcessLookupError:
            alive = False
        except PermissionError:
            alive = True
        assert not alive, f"grandchild pid {gpid} leaked (process group not killed)"


def _run_all() -> int:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  ok  {name}")
    print("codex_review tests passed (parse + containment: watchdog, killpg grandchild reap, normal review).")
    return 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
