#!/usr/bin/env python3
"""Tests for the black-box CLI conformance checker (ADR-0009 investment #1).

The checker is the first *dynamic* gate: it re-runs the built artifact against the contract's declared
examples instead of trusting the implementer's self-report. These tests use a **fake runner** keyed by the
exact command string, so they verify the comparison logic deterministically without executing anything. The
last test validates the contract schema itself (the CLI profile is required exactly when the deliverable is
a CLI, and existing profile-less contracts still validate — backward compatible / shadow-first)."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "codex-org-bootstrap" / "src"))
import conformance as conf
import controller_pipeline as cp

REPO = Path(__file__).resolve().parents[1]
SCHEMA = REPO / "schemas" / "implementation-contract.schema.json"


def fake_runner(table):
    """A runner that returns a canned RunResult per command. Unknown commands surface as a loud failure so a
    test never silently passes against a command it did not stub."""
    def _run(cmd, *, cwd=None, stdin=None):
        if cmd not in table:
            raise AssertionError(f"fake_runner: unstubbed command {cmd!r} (stubbed: {sorted(table)})")
        return table[cmd]
    return _run


R = conf.RunResult


def _cli_contract(profile):
    return {"role_id": "aufheben-designer", "deliverable_kind": "cli", "conformance": {"cli": profile}}


def test_non_cli_is_not_applicable():
    # a contract without a cli profile must be a vacuous pass — the gate never fabricates a finding it
    # cannot ground, and is a no-op for library/service deliverables.
    rep = conf.run_cli_conformance({"role_id": "aufheben-designer"}, fake_runner({}))
    assert rep["applicable"] is False and rep["passed"] is True and rep["findings"] == [], rep
    print("ok  non-CLI contract -> not applicable, vacuous pass")


def test_all_examples_pass():
    profile = {
        "entrypoint": {"invocation": "mytool"},
        "examples": [
            {"invocation": "--help", "expected_status": 0, "expected_stdout_contains": ["usage:"]},
            {"invocation": "", "expected_status": 0, "expected_stdout_contains": ["usage:"]},
        ],
    }
    runner = fake_runner({
        "mytool --help": R(0, "usage: mytool [opts]\n", ""),
        "mytool": R(0, "usage: mytool [opts]\n", ""),
    })
    rep = conf.run_cli_conformance(_cli_contract(profile), runner)
    assert rep["applicable"] and rep["passed"] and rep["findings"] == [], rep
    assert rep["checks_run"] == 2, rep
    print("ok  all examples pass -> green report")


def test_exit_status_mismatch_is_major():
    # the named #1 leak: declared exit 2 on bad input, artifact returns 1. Must be a major, actionable finding.
    profile = {
        "entrypoint": {"invocation": "mytool"},
        "status_and_errors": {"invalid_input_codes": [2]},
        "examples": [{"invocation": "build bad", "expected_status": 2}],
    }
    runner = fake_runner({"mytool build bad": R(1, "", "error: bad input\n")})
    rep = conf.run_cli_conformance(_cli_contract(profile), runner)
    assert rep["passed"] is False, rep
    (f,) = rep["findings"]
    assert f["check"] == "exit_status" and f["severity"] == "major", f
    assert f["expected"] == 2 and f["actual"] == 1, f
    print("ok  exit-status mismatch -> major finding (expected/actual pinned)")


def test_stdout_and_stderr_substring_checks():
    profile = {
        "entrypoint": {"invocation": "mytool"},
        "examples": [{
            "invocation": "x", "expected_status": 0,
            "expected_stdout_contains": ["done"], "expected_stderr_contains": ["warn:"],
        }],
    }
    # stdout missing 'done', stderr missing 'warn:' -> two findings
    runner = fake_runner({"mytool x": R(0, "nothing here\n", "")})
    rep = conf.run_cli_conformance(_cli_contract(profile), runner)
    checks = {f["check"] for f in rep["findings"]}
    assert checks == {"stdout_contains", "stderr_contains"}, rep["findings"]
    assert rep["passed"] is False
    print("ok  stdout/stderr substring misses -> findings on both channels")


def test_build_failure_is_critical_and_skips_examples():
    # a broken build invalidates every example; the checker must report the build as critical and NOT pile on
    # derived example failures (which would be noise, against Tricorder discipline).
    profile = {
        "build_and_install": {"commands": ["pip install .", "make"]},
        "entrypoint": {"invocation": "mytool"},
        "examples": [{"invocation": "--help", "expected_status": 0}],
    }
    runner = fake_runner({
        "pip install .": R(1, "", "ERROR: could not build wheel\n"),
        # 'make' and the example are never reached
    })
    rep = conf.run_cli_conformance(_cli_contract(profile), runner)
    assert rep["passed"] is False
    (f,) = rep["findings"]
    assert f["check"] == "build_and_install" and f["severity"] == "critical", f
    assert "could not build wheel" in f["stderr_tail"], f
    print("ok  build failure -> single critical finding, examples skipped (no derived noise)")


def test_normalization_tolerates_volatile_output():
    # exact-stdout check must not flap on timestamps / temp paths / long hex ids — those are normalized.
    profile = {
        "entrypoint": {"invocation": "mytool"},
        "examples": [{
            "invocation": "report", "expected_status": 0,
            "expected_stdout": "built at <TS> in <TMP> (<HEX>)",
        }],
    }
    runner = fake_runner({
        "mytool report": R(0, "built at 2026-06-20T11:02:33Z in /tmp/build-xyz (deadbeefcafe123)\r\n", ""),
    })
    rep = conf.run_cli_conformance(_cli_contract(profile), runner)
    assert rep["passed"] is True, rep["findings"]
    print("ok  normalization tolerates timestamps/temp-paths/hex on exact stdout")


def test_dispatch_routes_cli_and_empty_slots():
    # the single entry point: cli routes to the real checker; the other four kinds route to their empty slot
    # (recognized, unchecked, never a silent pass); an unknown/absent kind is simply not applicable.
    profile = {"entrypoint": {"invocation": "t"}, "examples": [{"invocation": "", "expected_status": 0}]}
    cli = conf.run_conformance(_cli_contract(profile), fake_runner({"t": R(0, "", "")}))
    assert cli["applicable"] and cli["passed"], cli
    for kind in ("library", "http_service", "rpc_service", "batch_job"):
        rep = conf.run_conformance({"deliverable_kind": kind, "conformance": {kind: {}}}, fake_runner({}))
        assert rep["applicable"] is False and rep["passed"] is True, (kind, rep)
        assert rep["slot"] == kind and rep["status"] == "no-checker-yet", (kind, rep)
    assert conf.run_conformance({"role_id": "x"}, fake_runner({})) == {
        "applicable": False, "passed": True, "findings": [], "checks_run": 0}
    print("ok  dispatch: cli -> real checker, 4 kinds -> empty slot, none -> not applicable")


def test_slot_kind_is_streamed_not_silent():
    # a recognized-but-unchecked kind must emit a `slot_unchecked` stream event (no silent cap), while the
    # convergence findings stay untouched.
    results = {"aufheben-designer": {"deliverable_kind": "library", "conformance": {"library": {}}}}
    events = []
    orig = cp._stream_append
    cp._stream_append = lambda repo, ev: events.append(ev)
    try:
        rep = cp._shadow_conformance("/tmp/leaf", results, "run-lib", runner=fake_runner({}))
    finally:
        cp._stream_append = orig
    assert rep and rep.get("slot") == "library", rep
    slot_events = [e for e in events if e.get("type") == "slot_unchecked"]
    assert slot_events and slot_events[0]["slot"] == "library", events
    assert cp._apply_conformance_gate([], rep, "block") == [], "an empty slot folds no findings even in block"
    print("ok  empty slot streams slot_unchecked (visible, not silent); folds nothing")


def test_schema_other_kinds_have_optional_slots():
    try:
        import jsonschema
    except ImportError:
        print("skip  jsonschema not installed")
        return
    schema = json.loads(SCHEMA.read_text())
    v = jsonschema.Draft202012Validator(schema)
    base = {"role_id": "aufheben-designer", "contract_id": "c", "objective": "o", "selected_direction": "d",
            "rejected_parts": [], "implementation_summary": "s", "acceptance_criteria": [],
            "files_allowed_to_change": [], "files_not_allowed_to_change": [], "required_checks": [],
            "security_requirements": [], "nonfunctional_requirements": [], "non_goals": [], "risks": [],
            "fallback_plan": "f", "handoff_to_implementer": "h"}
    # the other kinds are valid deliverable_kinds and their conformance slot is OPTIONAL (empty slot — not
    # yet enforced), so declaring the kind without a profile still validates.
    for kind in ("library", "http_service", "rpc_service", "batch_job"):
        assert v.is_valid({**base, "deliverable_kind": kind}), (kind, "kind alone must validate")
        assert v.is_valid({**base, "deliverable_kind": kind, "conformance": {kind: {}}}), (kind, "empty profile ok")
    print("ok  schema: other-kind slots present and optional (empty slots, not enforced)")


def test_schema_cli_profile_required_iff_cli():
    try:
        import jsonschema
    except ImportError:
        print("skip  jsonschema not installed (schema conditional not checked here)")
        return
    schema = json.loads(SCHEMA.read_text())
    validator = jsonschema.Draft202012Validator(schema)

    base = {
        "role_id": "aufheben-designer", "contract_id": "c1", "objective": "o", "selected_direction": "d",
        "rejected_parts": [], "implementation_summary": "s", "acceptance_criteria": [],
        "files_allowed_to_change": [], "files_not_allowed_to_change": [], "required_checks": [],
        "security_requirements": [], "nonfunctional_requirements": [], "non_goals": [], "risks": [],
        "fallback_plan": "f", "handoff_to_implementer": "h",
    }
    # 1) existing-shape contract (no deliverable_kind) still validates — backward compatible / shadow-first
    assert validator.is_valid(base), list(validator.iter_errors(base))
    # 2) deliverable_kind=cli WITHOUT a conformance.cli profile must FAIL
    cli_missing = {**base, "deliverable_kind": "cli"}
    assert not validator.is_valid(cli_missing), "cli deliverable must require a conformance.cli profile"
    # 3) deliverable_kind=cli WITH a valid profile validates
    cli_ok = {**base, "deliverable_kind": "cli", "conformance": {"cli": {
        "entrypoint": {"invocation": "mytool"},
        "examples": [{"invocation": "--help", "expected_status": 0}],
    }}}
    assert validator.is_valid(cli_ok), list(validator.iter_errors(cli_ok))
    # 4) a library deliverable does NOT require a cli profile (the schema is not a universal checklist)
    assert validator.is_valid({**base, "deliverable_kind": "library"})
    print("ok  schema: cli profile required iff deliverable_kind==cli; non-cli & legacy still valid")


def test_shadow_gate_streams_but_does_not_block():
    # the wired gate: when the contract is a CLI and an example fails, the controller streams a
    # `shadow_findings` event for observation, but in SHADOW mode the failure must NOT enter the convergence
    # findings (so it cannot block the merge). Promotion to `block` is the only path that folds them in.
    profile = {"entrypoint": {"invocation": "mytool"},
               "examples": [{"invocation": "x", "expected_status": 0}]}
    results = {"aufheben-designer": _cli_contract(profile)}
    runner = fake_runner({"mytool x": R(3, "", "boom\n")})   # wrong exit -> a failing conformance finding

    events = []
    orig = cp._stream_append
    cp._stream_append = lambda repo, ev: events.append(ev)        # capture stream, touch no disk
    try:
        report = cp._shadow_conformance("/tmp/leaf", results, "run-1", runner=runner)
    finally:
        cp._stream_append = orig

    assert report and report["applicable"] and report["passed"] is False, report
    streamed = [e for e in events if e.get("source") == "cli-conformance"]
    assert streamed and streamed[0]["type"] == "shadow_findings", streamed
    # SHADOW: the convergence findings are untouched; BLOCK: the failing finding is folded in.
    base = [{"severity": "major", "file": "x.py"}]
    assert cp._apply_conformance_gate(base, report, "shadow") == base, "shadow must not block"
    folded = cp._apply_conformance_gate(base, report, "block")
    assert len(folded) == len(base) + 1 and folded[-1]["check"] == "exit_status", folded
    print("ok  wired gate streams shadow_findings; shadow never blocks, block folds the failure in")


def test_shadow_gate_dormant_for_non_cli_contract():
    # no contract declares a CLI today, so the gate must be a no-op: no event, returns a not-applicable
    # report (or None), and the runner is never touched.
    results = {"aufheben-designer": {"role_id": "aufheben-designer", "implementation_summary": "prose only"}}
    events = []
    orig = cp._stream_append
    cp._stream_append = lambda repo, ev: events.append(ev)
    try:
        report = cp._shadow_conformance("/tmp/leaf", results, "run-2",
                                        runner=fake_runner({}))   # empty table: any exec would raise
    finally:
        cp._stream_append = orig
    assert report is None or report.get("applicable") is False, report
    assert [e for e in events if e.get("source") == "cli-conformance"] == [], events
    assert cp.CONFORMANCE_SHADOW == "shadow", "default mode is shadow (observe, never block)"
    print("ok  gate dormant for a non-CLI contract (no event, no exec, default mode=shadow)")


def test_acceptance_bundle_withheld_from_implementer_only():
    # ADR-0009 #1 immutable acceptance bundle: the implementer sees the spec (entrypoint, status_and_errors,
    # acceptance_criteria) but NOT the golden examples; a verifier sees the full contract; results untouched.
    contract = {
        "role_id": "aufheben-designer", "acceptance_criteria": ["does X"],
        "deliverable_kind": "cli",
        "conformance": {"cli": {
            "entrypoint": {"invocation": "t"},
            "status_and_errors": {"success_codes": [0], "invalid_input_codes": [2]},
            "examples": [{"invocation": "--help", "expected_status": 0},
                         {"invocation": "", "expected_status": 2}],
        }},
    }
    inputs = {"aufheben-designer": contract}

    impl = cp._withhold_acceptance_bundle("implementer", inputs)
    icli = impl["aufheben-designer"]["conformance"]["cli"]
    assert icli["examples"] == [], "implementer must NOT see the golden examples"
    assert icli["_examples_withheld"] == 2, "the withholding is marked, not silent"
    assert icli["entrypoint"] == {"invocation": "t"}, "implementer keeps the entrypoint spec"
    assert icli["status_and_errors"]["invalid_input_codes"] == [2], "implementer keeps the exit-code policy"
    assert impl["aufheben-designer"]["acceptance_criteria"] == ["does X"], "implementer keeps acceptance_criteria"
    # the ORIGINAL contract (what the controller/gate uses) still has the examples
    assert len(contract["conformance"]["cli"]["examples"]) == 2, "the gate's contract is untouched"

    # a verifier / any non-implementer role sees the full contract unchanged
    same = cp._withhold_acceptance_bundle("linon", inputs)
    assert same["aufheben-designer"]["conformance"]["cli"]["examples"], "non-implementer sees full goldens"
    assert cp.WITHHOLD_BUNDLE == "on", "withholding is on by default"
    print("ok  acceptance bundle withheld from implementer only (spec kept, goldens hidden, gate intact)")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
    print(f"\n{len(fns)} passed")
