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
import conformance as conf

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


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
    print(f"\n{len(fns)} passed")
