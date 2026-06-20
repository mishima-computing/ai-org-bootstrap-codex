#!/usr/bin/env python3
"""Smoke tests for aob_stream.py — the AI Org stream inspector CLI (dogfood deliverable, built by the org
and delivered with all ADR-0009 gates green; this pins its contract in the codex suite)."""
import os, subprocess, sys, tempfile
from pathlib import Path
AOB = Path(__file__).resolve().parent / "aob_stream.py"


def _run(args, stdin=None):
    p = subprocess.run([sys.executable, str(AOB), *args], input=stdin, text=True, capture_output=True)
    return p.returncode, p.stdout, p.stderr


def test_filter_and_exit_codes():
    d = tempfile.mkdtemp()
    f = Path(d) / "s.jsonl"
    f.write_text('{"source":"a","type":"x"}\n{"source":"b","type":"y"}\nBADLINE\n')
    rc, out, _ = _run([str(f), "--source", "a"])
    assert rc == 0 and '"source": "a"' in out.replace(" ", "") or '"source":"a"' in out, (rc, out)
    assert "b" not in out, out                                   # filtered
    assert _run(["--help"])[0] == 0                              # --help -> 0
    assert _run([])[0] == 2                                      # no path -> 2
    assert _run(["/no/such.jsonl"])[0] == 2                      # missing file -> 2
    rc, out, _ = _run(["-"], stdin='{"source":"a","type":"x"}\nbad\n')  # stdin via '-'
    assert rc == 0 and "a" in out, (rc, out)
    print("ok  aob_stream: filter + exit codes (0 ok / 2 no-arg / 2 missing) + stdin + skips malformed")


if __name__ == "__main__":
    test_filter_and_exit_codes()
    print("\n1 passed")
