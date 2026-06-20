#!/usr/bin/env python3
"""Tests for the deterministic pre-implementation contract review (ADR-0009 #1).

preflight() checks the aufheben contract for completeness + self-consistency BEFORE the implementer runs, so
an under-specified or self-contradictory contract is caught at design time (one aufheben re-run) instead of
after a wasted build + review. These tests pin the deterministic checks; the last two cover the pipeline
wiring (streams before the implementer, shadow never blocks, block folds into the repair findings)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "codex-org-bootstrap" / "src"))
import contract_preflight as pf
import controller_pipeline as cp


def _complete_cli_contract():
    # mirrors the high-quality profile a real aufheben emitted: help + success + error examples, exit codes
    # consistent with the declared policy.
    return {
        "role_id": "aufheben-designer",
        "acceptance_criteria": ["jsonpick prints the value at the path", "honours the declared exit codes"],
        "deliverable_kind": "cli",
        "conformance": {"cli": {
            "entrypoint": {"invocation": "python3 jsonpick.py"},
            "status_and_errors": {"success_codes": [0], "invalid_input_codes": [1, 2]},
            "examples": [
                {"invocation": "--help", "expected_status": 0},
                {"invocation": "a.b", "expected_status": 0},
                {"invocation": "", "expected_status": 2},
            ],
        }},
    }


def test_non_contract_not_applicable():
    assert pf.preflight({"role_id": "implementer"})["applicable"] is False
    assert pf.preflight("not a dict")["applicable"] is False
    print("ok  preflight is a no-op on non-aufheben outputs")


def test_complete_contract_passes():
    rep = pf.preflight(_complete_cli_contract())
    assert rep["applicable"] and rep["passed"] and rep["findings"] == [], rep
    print("ok  a complete, consistent CLI contract passes preflight")


def test_missing_acceptance_criteria_is_flagged():
    c = _complete_cli_contract()
    c["acceptance_criteria"] = []
    rep = pf.preflight(c)
    assert not rep["passed"]
    assert any(f["check"] == "acceptance_criteria" and f["severity"] == "major" for f in rep["findings"]), rep
    print("ok  empty acceptance_criteria -> major (nothing to verify against)")


def test_cli_coverage_gaps_flagged():
    c = _complete_cli_contract()
    # drop the --help example and the error example -> two coverage findings
    c["conformance"]["cli"]["examples"] = [{"invocation": "a.b", "expected_status": 0}]
    checks = {f["check"] for f in pf.preflight(c)["findings"]}
    assert "coverage_help" in checks and "coverage_error" in checks, checks
    assert "coverage_success" not in checks, "a success example is present"
    print("ok  missing --help/error examples -> coverage_help + coverage_error")


def test_status_inconsistency_flagged():
    c = _complete_cli_contract()
    # an example expects exit 7, which the declared status_and_errors does not include -> contradiction
    c["conformance"]["cli"]["examples"].append({"invocation": "x", "expected_status": 7})
    findings = pf.preflight(c)["findings"]
    incon = [f for f in findings if f["check"] == "status_consistency"]
    assert incon and incon[0]["expected"] == 7, findings
    print("ok  an example exit code outside the declared policy -> status_consistency major")


def test_wired_preflight_streams_before_block_folds():
    results = {"aufheben-designer": dict(_complete_cli_contract(), acceptance_criteria=[])}  # a flawed contract
    events = []
    orig = cp._stream_append
    cp._stream_append = lambda repo, ev: events.append(ev)
    try:
        rep = cp._contract_preflight("/tmp/leaf", results, "run-pf")
    finally:
        cp._stream_append = orig
    assert rep and rep["applicable"] and rep["passed"] is False, rep
    streamed = [e for e in events if e.get("source") == "contract-preflight"]
    assert streamed and streamed[0]["type"] == "shadow_findings", streamed
    base = [{"severity": "minor"}]
    assert cp._apply_conformance_gate(base, rep, "shadow") == base, "shadow must not block"
    folded = cp._apply_conformance_gate(base, rep, "block")
    assert len(folded) > len(base), "block folds the failing preflight findings into the repair loop"
    print("ok  wired preflight streams shadow_findings; shadow never blocks, block folds in")


def test_default_mode_is_shadow():
    assert cp.PREFLIGHT_MODE == "shadow", "preflight defaults to shadow (observe, never block)"
    print("ok  default CONTRACT_PREFLIGHT mode is shadow")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
    print(f"\n{len(fns)} passed")
