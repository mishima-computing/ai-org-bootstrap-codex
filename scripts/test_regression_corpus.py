#!/usr/bin/env python3
"""Tests for ADR-0009 #4 finding->regression conversion. The corpus stores gate counterexamples and replays
them; the fuzz gate turns a one-time crash discovery into a permanent, deterministically-replayed regression
test (the deterministic gates self-regress, so fuzz is the one that needs this)."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import regression_corpus as rc
import fuzz_cli as fz
import conformance as conf

R = conf.RunResult
PROFILE = {"entrypoint": {"invocation": "tool"},
           "status_and_errors": {"success_codes": [0], "invalid_input_codes": [1, 2]}}


def _corpus():
    return str(Path(tempfile.mkdtemp(prefix="corpus-")) / "regressions.jsonl")


def test_record_dedupes_and_loads_by_gate():
    p = _corpus()
    n1 = rc.record(p, [{"gate": "cli-fuzz", "kind": "crash", "arg": "", "stdin": "X"},
                       {"gate": "other", "kind": "k", "arg": "", "stdin": "y"}])
    assert n1 == 2
    n2 = rc.record(p, [{"gate": "cli-fuzz", "kind": "crash", "arg": "", "stdin": "X"}])   # dup
    assert n2 == 0, "a duplicate counterexample is not re-recorded"
    fuzz_entries = rc.load(p, "cli-fuzz")
    assert len(fuzz_entries) == 1 and fuzz_entries[0]["stdin"] == "X", fuzz_entries
    assert rc.load(p, "other")[0]["kind"] == "k"
    print("ok  corpus record dedupes and load filters by gate")


def test_load_missing_is_empty_and_failsoft():
    assert rc.load(None) == [] and rc.load("/no/such/file.jsonl") == []
    print("ok  load of a missing/None corpus is empty (fail-soft)")


def test_fuzz_records_then_replays_a_counterexample():
    p = _corpus()
    # crash on the malformed-JSON payload '{bad', which is one of fuzz's DETERMINISTIC generated cases — so
    # the discovery (and thus the record) does not depend on random luck.
    def crashing(cmd, *, cwd=None, stdin=None):
        return R(1, "", "Traceback (most recent call last): boom") if (stdin and "bad" in stdin) else R(0, "", "")
    rep1 = fz.fuzz(PROFILE, crashing, iterations=5, corpus_path=p)
    assert not rep1["passed"] and rep1["recorded"] >= 1, rep1
    stored = rc.load(p, "cli-fuzz")
    assert any("bad" in e.get("stdin", "") for e in stored), stored

    # now REPLAY: with iterations=0 (no generated cases) the stored counterexample is still replayed, and the
    # still-broken CLI regresses deterministically — caught without the LLM and without random luck.
    rep2 = fz.fuzz(PROFILE, crashing, iterations=0, corpus_path=p)
    assert not rep2["passed"] and rep2["replayed"] >= 1 and rep2["regressed"] >= 1, rep2
    assert any(f.get("regressed") and "REGRESSION" in f["detail"] for f in rep2["findings"]), rep2["findings"]
    print("ok  fuzz records a counterexample, then replays it as a deterministic regression")


def test_replayed_counterexample_passes_once_fixed():
    p = _corpus()
    rc.record(p, [{"gate": "cli-fuzz", "kind": "crash", "arg": "", "stdin": "ZZZ"}])
    # the CLI is now robust (never crashes) -> the replayed counterexample passes, no findings.
    def robust(cmd, *, cwd=None, stdin=None):
        return R(0 if not stdin else 1, "", "")
    rep = fz.fuzz(PROFILE, robust, iterations=0, corpus_path=p)
    assert rep["passed"] and rep["replayed"] >= 1 and rep["regressed"] == 0, rep
    print("ok  a fixed counterexample replays green (kept in corpus, no longer a finding)")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
    print(f"\n{len(fns)} passed")
