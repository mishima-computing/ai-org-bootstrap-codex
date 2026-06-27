#!/usr/bin/env python3
"""Tests for ADR-0009 #3 black-box CLI fuzzing. The invariant logic + minimization are pinned with a fake
runner; a LIVE test fuzzes a real planted CLI that crashes on malformed input (and a robust one that does
not), through the bounded subprocess runner — so the gate is shown to actually catch a robustness bug."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "codex-org-bootstrap" / "src"))
import fuzz_cli as fz
import conformance as conf
import controller_pipeline as cp

R = conf.RunResult
PROFILE = {"entrypoint": {"invocation": "tool"},
           "status_and_errors": {"success_codes": [0], "invalid_input_codes": [1, 2]}}


def test_robust_cli_passes():
    # every input exits cleanly in policy (0/1/2) -> no findings.
    def runner(cmd, *, cwd=None, stdin=None):
        return R(0 if not stdin else 2, "ok", "")
    rep = fz.fuzz(PROFILE, runner, iterations=15)
    assert rep["applicable"] and rep["passed"] and rep["findings"] == [], rep
    print("ok  a robust CLI (always exits in policy) passes the fuzzer")


def test_crash_via_traceback_is_critical():
    # the CLI dumps a Python traceback on any non-empty stdin -> a CRASH finding (critical).
    def runner(cmd, *, cwd=None, stdin=None):
        if stdin:
            return R(1, "", "Traceback (most recent call last):\n  ...\nValueError: boom")
        return R(0, "", "")
    rep = fz.fuzz(PROFILE, runner, iterations=15)
    crashes = [f for f in rep["findings"] if f["check"] == "crash"]
    assert crashes and crashes[0]["severity"] == "critical", rep["findings"]
    print("ok  uncaught-traceback on input -> critical crash finding")


def test_exit_out_of_policy_flagged():
    # declared {0,1,2}; the CLI returns 5 on some input -> exit_out_of_policy (major).
    def runner(cmd, *, cwd=None, stdin=None):
        return R(5 if stdin == "{bad" else 0, "", "")
    rep = fz.fuzz(PROFILE, runner, iterations=5)
    oop = [f for f in rep["findings"] if f["check"] == "exit_out_of_policy"]
    assert oop and oop[0]["returncode"] == 5, rep["findings"]
    print("ok  an exit code outside the declared policy -> exit_out_of_policy finding")


def test_hang_flagged():
    def runner(cmd, *, cwd=None, stdin=None):
        return R(124, "", "")   # the bounded runner's timeout marker
    rep = fz.fuzz(PROFILE, runner, iterations=3)
    assert any(f["check"] == "hang" for f in rep["findings"]), rep["findings"]
    print("ok  a timeout (124) -> hang finding")


def test_minimization_shrinks_the_counterexample():
    # crash whenever stdin contains the marker; the reported stdin should be minimized (short), not the
    # original 200k-char payload.
    def runner(cmd, *, cwd=None, stdin=None):
        if stdin and "A" in stdin:
            return R(1, "", "Traceback (most recent call last): KaBoom")
        return R(0, "", "")
    rep = fz.fuzz(PROFILE, runner, iterations=20)
    crash = next(f for f in rep["findings"] if f["check"] == "crash")
    assert len(crash["stdin"]) < 200, ("counterexample must be minimized", crash)
    print("ok  counterexample is minimized (not the full 200k input)")


def test_live_fuzz_catches_a_real_crashing_cli():
    # plant a real CLI that does json.load WITHOUT handling errors -> crashes (traceback) on malformed stdin.
    d = tempfile.mkdtemp(prefix="fuzz-live-")
    (Path(d) / "crashy.py").write_text(
        "import sys, json\n"
        "if '--help' in sys.argv: print('usage'); sys.exit(0)\n"
        "json.load(sys.stdin)\n"            # NO try/except -> uncaught on malformed JSON
        "sys.exit(0)\n")
    profile = {"entrypoint": {"invocation": f"python3 {Path(d)/'crashy.py'}"},
               "status_and_errors": {"success_codes": [0], "invalid_input_codes": [1, 2]}}
    rep = fz.fuzz(profile, conf.subprocess_runner(timeout=5), iterations=12)
    assert not rep["passed"], "the fuzzer must catch the unhandled json.load crash"
    assert any(f["check"] == "crash" for f in rep["findings"]), rep["findings"]

    # the robust version handles it and exits in policy -> passes.
    (Path(d) / "robust.py").write_text(
        "import sys, json\n"
        "if '--help' in sys.argv: print('usage'); sys.exit(0)\n"
        "try:\n  json.load(sys.stdin)\nexcept Exception:\n  sys.stderr.write('bad json\\n'); sys.exit(1)\n"
        "sys.exit(0)\n")
    profile2 = {"entrypoint": {"invocation": f"python3 {Path(d)/'robust.py'}"},
                "status_and_errors": {"success_codes": [0], "invalid_input_codes": [1, 2]}}
    rep2 = fz.fuzz(profile2, conf.subprocess_runner(timeout=5), iterations=12)
    assert rep2["passed"], ("the robust CLI must pass the fuzzer", rep2["findings"])
    print("ok  LIVE: fuzzer catches the unhandled-json crash; the robust CLI passes")


def test_wired_fuzz_streams_and_block_folds():
    # the gate runs only for a CLI contract; streams shadow_findings; shadow never blocks, block folds.
    contract = {"role_id": "aufheben-designer", "deliverable_kind": "cli",
                "conformance": {"cli": dict(PROFILE)}}
    def crashing(cmd, *, cwd=None, stdin=None):
        return R(1, "", "Traceback (most recent call last): boom") if stdin else R(0, "", "")
    events = []
    orig = cp._stream_append
    cp._stream_append = lambda repo, ev: events.append(ev)
    try:
        rep = cp._fuzz_cli("/tmp/leaf", {"aufheben-designer": contract}, "run-fz", runner=crashing)
    finally:
        cp._stream_append = orig
    assert rep and rep["applicable"] and rep["passed"] is False, rep
    assert any(e.get("source") == "cli-fuzz" and e["type"] == "shadow_findings" for e in events), events
    assert cp._apply_conformance_gate([], rep, "shadow") == [], "shadow never blocks"
    assert cp._apply_conformance_gate([], rep, "block"), "block folds the crash finding"
    # a non-CLI contract is a no-op
    assert cp._fuzz_cli("/tmp/leaf", {"aufheben-designer": {"role_id": "aufheben-designer"}}, "r",
                        runner=crashing) is None
    assert cp.FUZZ_CLI_MODE == "shadow", "fuzz defaults to shadow"
    print("ok  wired fuzz: streams shadow_findings; shadow never blocks, block folds; no-op for non-CLI")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
    print(f"\n{len(fns)} passed")
