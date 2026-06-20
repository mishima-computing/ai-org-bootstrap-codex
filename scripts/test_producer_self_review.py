#!/usr/bin/env python3
"""Tests for the producer self-adversary measurement (A, shadow-measure).

Before the reviewers run, `_producer_self_review` runs `codex review` on the implementer's own diff and
classifies the findings: a P1/P2 finding inside the implementer's allowed scope is one the implementer could
fix itself — i.e. a full-wave Linon rejection it could have pre-empted. These tests pin the classification and
the shadow contract: it measures, it never self-fixes, and a crash never sinks the build. `off` is a no-op."""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "codex-org-bootstrap" / "src"))
import controller_pipeline as cp
import codex_review

CONTRACT = {"role_id": "aufheben-designer", "files_allowed_to_change": ["box_runner.py", "tests/*.py"]}


def _run(mode, findings):
    """Run _producer_self_review with codex_review.review stubbed to `findings`, capturing streamed events and
    the repos codex review was invoked on."""
    events: list = []
    calls: list = []
    saved = (cp.PRODUCER_SELF_REVIEW, codex_review.review, cp._stream_append)
    cp.PRODUCER_SELF_REVIEW = mode
    codex_review.review = lambda repo: (calls.append(repo) or {"findings": findings, "ok": True, "raw": ""})
    cp._stream_append = lambda repo, ev: events.append(ev)
    try:
        measure = cp._producer_self_review("/tmp/leaf", {"aufheben-designer": CONTRACT}, "run-a")
    finally:
        cp.PRODUCER_SELF_REVIEW, codex_review.review, cp._stream_append = saved
    return measure, events, calls


def test_off_is_noop():
    m, ev, calls = _run("off", [{"file": "box_runner.py", "priority": 1}])
    assert m is None and calls == [] and ev == [], (m, calls, ev)
    print("ok  off -> no review, no stream, returns None")


def test_shadow_measures_in_scope_high_priority():
    findings = [
        {"file": "box_runner.py", "priority": 1, "title": "x"},      # in-scope P1 -> pre-emptable
        {"file": "tests/test_box.py", "priority": 2, "title": "y"},  # in-scope P2 -> pre-emptable
        {"file": "box_runner.py", "priority": 3, "title": "z"},      # in-scope but P3 -> not pre-emptable
        {"file": "inner_sandbox.py", "priority": 1, "title": "w"},   # out-of-scope -> design, left to Linon
    ]
    m, ev, calls = _run("shadow", findings)
    assert calls == ["/tmp/leaf"], "shadow runs codex review once on the leaf"
    assert m["total_findings"] == 4, m
    assert m["preemptable_in_scope"] == 2, m       # the two in-scope P1/P2
    assert m["out_of_scope"] == 1, m               # inner_sandbox.py (the in-scope P3 is neither bucket)
    assert m["would_preempt_full_wave"] is True, m
    streamed = [e for e in ev if e.get("type") == "self_review_measure"]
    assert streamed and streamed[0]["mode"] == "shadow", ev
    print("ok  shadow measures in-scope P1/P2 as pre-emptable; out-of-scope left for Linon; no self-fix")


def test_no_preemptable_signal_when_nothing_in_scope_high():
    findings = [{"file": "inner_sandbox.py", "priority": 1}, {"file": "box_runner.py", "priority": 3}]
    m, _ev, _calls = _run("shadow", findings)
    assert m["preemptable_in_scope"] == 0 and m["would_preempt_full_wave"] is False, m
    print("ok  no in-scope P1/P2 -> would_preempt_full_wave False (nothing to pre-empt)")


def test_self_review_error_streams_but_returns_none():
    events: list = []
    saved = (cp.PRODUCER_SELF_REVIEW, codex_review.review, cp._stream_append)
    cp.PRODUCER_SELF_REVIEW = "shadow"

    def boom(repo):
        raise RuntimeError("review crashed")

    codex_review.review = boom
    cp._stream_append = lambda repo, ev: events.append(ev)
    try:
        m = cp._producer_self_review("/tmp/leaf", {"aufheben-designer": CONTRACT}, "run-a")
    finally:
        cp.PRODUCER_SELF_REVIEW, codex_review.review, cp._stream_append = saved
    assert m is None, m
    assert any(e.get("type") == "self_review_error" for e in events), events
    print("ok  a crashed self-review streams an error and returns None (never sinks the build)")


def test_path_in_scope_matches_globs_and_exact():
    allowed = ("box_runner.py", "tests/*.py")
    assert cp._path_in_scope("box_runner.py", allowed)
    assert cp._path_in_scope("tests/test_box.py", allowed)
    assert not cp._path_in_scope("inner_sandbox.py", allowed)
    assert not cp._path_in_scope("", ())
    print("ok  _path_in_scope: exact + glob match, out-of-scope rejected")


def test_allowed_scope_reads_the_contract():
    assert cp._allowed_change_scope({"aufheben-designer": CONTRACT}) == ("box_runner.py", "tests/*.py")
    assert cp._allowed_change_scope({}) == ()
    print("ok  _allowed_change_scope reads files_allowed_to_change from the contract")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
    print(f"\n{len(fns)} passed")
