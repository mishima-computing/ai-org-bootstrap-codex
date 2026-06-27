#!/usr/bin/env python3
"""Tests for the gate false-positive audit harness. This harness is part of the trusted kernel (ADR-0011) —
it certifies the certifier — so its own logic is tested directly, not generated. Plain `def test_*` + assert,
run via the __main__ block (the scripts/ test idiom: no pytest dependency)."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gate_fp_audit as audit  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "gate_fixtures"


# --- pure classification logic -------------------------------------------------------------------

def test_gate_status_maps_report_to_pass_fail_na():
    assert audit._gate_status({"applicable": True, "passed": True}) == "pass"
    assert audit._gate_status({"applicable": True, "passed": False}) == "fail"
    assert audit._gate_status({"applicable": False, "passed": True}) == "na"   # vacuous pass is NOT a pass
    assert audit._gate_status(None) == "na"
    print("ok  _gate_status: applicable+passed -> pass, applicable+!passed -> fail, !applicable -> na")


def test_classify_all_five_verdicts():
    cases = [
        ("pass", "pass", "correct_accept"),
        ("pass", "fail", "false_positive"),   # the number that gates promotion
        ("fail", "fail", "true_positive"),
        ("fail", "pass", "false_negative"),   # gate has no teeth
        ("pass", "na", "misfire"),            # asserted gate did not run
        ("fail", "na", "misfire"),
    ]
    for expected, actual, verdict in cases:
        assert audit._classify(expected, actual) == verdict, (expected, actual)
    print("ok  _classify: correct_accept / false_positive / true_positive / false_negative / misfire")


# --- integration over the real fixtures ----------------------------------------------------------

def test_cli_good_passes_both_gates():
    r = audit.run_fixture(FIXTURES / "cli-good")
    assert r["gates"]["preflight"]["verdict"] == "correct_accept", r
    assert r["gates"]["conformance"]["verdict"] == "correct_accept", r
    print("ok  cli-good: well-formed contract + obedient artifact pass both gates")


def test_cli_bad_is_caught_by_conformance_not_preflight():
    r = audit.run_fixture(FIXTURES / "cli-bad")
    # the contract is well-formed (preflight passes); the ARTIFACT leaks exit 0 -> conformance catches it
    assert r["gates"]["preflight"]["verdict"] == "correct_accept", r
    assert r["gates"]["conformance"]["verdict"] == "true_positive", r
    print("ok  cli-bad: preflight passes the contract, conformance catches the leaked exit code")


def test_library_bad_missing_symbol_is_caught():
    r = audit.run_fixture(FIXTURES / "library-bad")
    assert r["gates"]["conformance"]["verdict"] == "true_positive", r
    print("ok  library-bad: the import-probe catches the missing exported symbol")


def test_json_bad_schema_violation_is_caught():
    r = audit.run_fixture(FIXTURES / "json-bad")
    assert r["gates"]["conformance"]["verdict"] == "true_positive", r
    print("ok  json-bad: the json gate catches the schema + required-path violation")


def test_preflight_bad_is_caught_by_preflight_and_conformance_is_na():
    r = audit.run_fixture(FIXTURES / "preflight-bad")
    assert r["gates"]["preflight"]["verdict"] == "true_positive", r
    assert "conformance" not in r["gates"], r   # not asserted: with no profile the gate is correctly na
    print("ok  preflight-bad: preflight catches the missing profile; conformance correctly does not fire")


def test_real_in_repo_artifacts_audited_in_place_via_workdir_pass():
    # REAL, in-repo artifacts of every kind, audited where they live (workdir) — across cli (process exec),
    # library (import-probe) and json (schema-validate), the gate must not false-positive on working code
    for name in ("real-cli-merge-gate", "real-cli-validate-pack",
                 "real-library-frontier", "real-json-schema"):
        r = audit.run_fixture(FIXTURES / name)
        assert r["gates"]["preflight"]["verdict"] == "correct_accept", (name, r)
        assert r["gates"]["conformance"]["verdict"] == "correct_accept", (name, r)
    print("ok  real in-repo artifacts (cli/library/json) pass in place — no false positive on working code")


# --- aggregation + the promotion verdict ---------------------------------------------------------

def test_audit_corpus_shows_zero_fp_and_full_catch():
    report = audit.audit(FIXTURES)
    for gate in ("preflight", "conformance"):
        s = report["summary"][gate]
        assert s["false_positive"] == 0, f"{gate} false positives: {s}"
        assert s["false_negative"] == 0, f"{gate} false negatives: {s}"
        assert s["misfire"] == 0, f"{gate} misfires: {s}"
        assert s["good_fixtures"] >= 1 and s["bad_fixtures"] >= 1, s
        assert s["promotable"] is True, s
    print("ok  corpus: both gates FP=0, catch=100%, promotable on the curated corpus")


def test_promotable_requires_both_a_good_and_a_bad_fixture():
    # a corpus with only good fixtures is NOT promotable: a gate that never fires on a defect is unproven
    with tempfile.TemporaryDirectory() as td:
        d = Path(td) / "only-good"
        d.mkdir()
        (d / "contract.json").write_text(
            (FIXTURES / "cli-good" / "contract.json").read_text(encoding="utf-8"), encoding="utf-8")
        (d / "expect.json").write_text('{"label": "only-good", "preflight": "pass"}', encoding="utf-8")
        report = audit.audit(Path(td))
        assert report["summary"]["preflight"]["promotable"] is False, report   # no bad fixture
    print("ok  promotable requires BOTH a good and a bad fixture (non-gameable)")


def test_require_promotable_exit_code_is_zero_on_the_real_corpus():
    assert audit.main(["--fixtures", str(FIXTURES), "--require-promotable"]) == 0
    print("ok  --require-promotable exits 0 on the real corpus")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
    print(f"\n{len(fns)} passed")
