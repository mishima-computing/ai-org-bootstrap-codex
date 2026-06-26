#!/usr/bin/env python3
"""Tests for the deterministic pre-implementation contract review (ADR-0009 #1).

preflight() checks the aufheben contract for completeness + self-consistency BEFORE the implementer runs, so
an under-specified or self-contradictory contract is caught at design time (one aufheben re-run) instead of
after a wasted build + review. These tests pin the deterministic checks; the last two cover the pipeline
wiring (streams before the implementer, shadow never blocks, block folds into the repair findings)."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "codex-org-bootstrap" / "src"))
import contract_preflight as pf
import controller_output
import controller_pipeline as cp

REPO = Path(__file__).resolve().parents[1]
IMPLEMENTATION_SCHEMA = REPO / "schemas" / "implementation-contract.schema.json"


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


def _schema_complete_none_contract():
    return {
        "role_id": "aufheben-designer",
        "contract_id": "c1",
        "objective": "Rename internal helper without behavior change.",
        "selected_direction": "Behavior-preserving rename with deterministic regression gates.",
        "rejected_parts": [],
        "implementation_summary": "Rename the internal helper without changing public behavior.",
        "acceptance_criteria": ["existing tests still pass"],
        "files_allowed_to_change": ["tool.py"],
        "files_not_allowed_to_change": [],
        "required_checks": ["python3 -m py_compile tool.py"],
        "security_requirements": [],
        "nonfunctional_requirements": [],
        "non_goals": ["no public interface change"],
        "risks": [],
        "fallback_plan": "revert the rename",
        "handoff_to_implementer": "Perform only the rename and run the stated check.",
        "deliverable_kind": "none",
        "regression_suite": {"command": "python3 -m py_compile tool.py",
                             "reason": "no-surface refactor must preserve importability"},
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


def test_self_overlapping_scope_is_flagged():
    # the exact contract that lost a deliverable live: allowed=["jsonpick.py"] AND a blanket forbidden "*"
    # that matches it. Preflight must flag the contradiction at design time.
    c = _complete_cli_contract()
    c["files_allowed_to_change"] = ["jsonpick.py"]
    c["files_not_allowed_to_change"] = ["*", ".agent-runs/**", "package manifests"]   # "*" matches the deliverable
    findings = pf.preflight(c)["findings"]
    overlap = [f for f in findings if f["check"] == "self_overlapping_scope"]
    assert overlap and overlap[0]["allowed"] == "jsonpick.py" and overlap[0]["forbidden"] == "*", findings
    # a NON-overlapping forbidden set (prose descriptions, scoped globs) does not false-positive
    c["files_not_allowed_to_change"] = [".agent-runs/**", "package manifests", "test files"]
    assert not [f for f in pf.preflight(c)["findings"] if f["check"] == "self_overlapping_scope"]
    print("ok  self-overlapping scope (allowed also matched by a blanket forbidden) -> flagged; clean set -> not")


def test_missing_deliverable_kind_is_flagged():
    # The escape hatch: a contract that simply omits deliverable_kind would skip every interface check. The
    # declaration is now required (silence is not a way out) — even an otherwise-complete contract is flagged.
    c = _complete_cli_contract()
    del c["deliverable_kind"]
    rep = pf.preflight(c)
    assert not rep["passed"]
    assert any(f["check"] == "deliverable_kind" and f["severity"] == "major" for f in rep["findings"]), rep
    print("ok  omitting deliverable_kind -> major (the interface obligation cannot be skipped by silence)")


def test_deliverable_kind_none_passes():
    # The explicit no-interface declaration: 'none' discharges the obligation, so no interface findings.
    c = _complete_cli_contract()
    c["deliverable_kind"] = "none"
    c.pop("conformance", None)
    rep = pf.preflight(c)
    checks = {f["check"] for f in rep["findings"]}
    assert "deliverable_kind" not in checks and "conformance_profile" not in checks, rep
    assert rep["passed"], rep
    print("ok  deliverable_kind 'none' explicitly discharges the interface obligation -> passes")


def test_change_intent_cannot_leak_into_implementation_contract_schema():
    c = _schema_complete_none_contract()
    ok = controller_output.gate_output(json.dumps(c), str(IMPLEMENTATION_SCHEMA))
    assert ok["output_ok"], ok
    c["change_intent"] = {"interface_delta": "no_surface_change"}
    leaked = controller_output.gate_output(json.dumps(c), str(IMPLEMENTATION_SCHEMA))
    assert not leaked["output_ok"], leaked
    assert any("additional" in e.lower() and "change_intent" in e for e in leaked["errors"]), leaked
    print("ok  implementation contract schema rejects leaked change_intent (advisory substrate only)")


def test_interface_kind_without_profile_is_flagged():
    # Declaring an interface kind but carrying no usable profile is as under-specified as declaring no kind:
    # there is nothing for the gate to check the built artifact against.
    c = _complete_cli_contract()
    c["deliverable_kind"] = "library"
    c.pop("conformance", None)
    rep = pf.preflight(c)
    prof = [f for f in rep["findings"] if f["check"] == "conformance_profile"]
    assert prof and prof[0]["kind"] == "library" and prof[0]["severity"] == "major", rep
    # an empty profile object is not substantive either
    c["conformance"] = {"library": {}}
    assert [f for f in pf.preflight(c)["findings"] if f["check"] == "conformance_profile"], "empty {} is not a profile"
    print("ok  an interface kind without a usable conformance profile -> conformance_profile major")


def _service_contract(kind: str, profile: dict) -> dict:
    c = _complete_cli_contract()
    c["objective"] = f"Expose a {kind} production boundary."
    c["acceptance_criteria"] = ["the service responds across its production boundary"]
    c["deliverable_kind"] = kind
    c["conformance"] = {kind: profile}
    return c


def _http_service_profile() -> dict:
    return {
        "start": {"command": "python3 app.py"},
        "base_url": "http://127.0.0.1:8000",
        "readiness_timeout_seconds": 3,
        "examples": [{"method": "GET", "path": "/widgets", "expected_status": 200,
                      "expected_body_contains": ["real-widget"]}],
    }


def _rpc_service_profile() -> dict:
    return {
        "start": {"command": "python3 rpc.py"},
        "base_url": "http://127.0.0.1:8001/rpc",
        "transport": "json_rpc_http",
        "calls": [{"method": "widgets.get", "expected_result_contains": ["real-widget"]}],
    }


def test_http_service_without_end_to_end_example_is_flagged():
    profile = _http_service_profile()
    profile["examples"] = [{"method": "GET", "path": "/widgets", "expected_status": 200}]
    rep = pf.preflight(_service_contract("http_service", profile))
    e2e = [f for f in rep["findings"] if f["check"] == "production_boundary_e2e"]
    assert not rep["passed"] and e2e and e2e[0]["kind"] == "http_service", rep
    print("ok  http_service with status-only examples -> production_boundary_e2e major")


def test_rpc_service_without_end_to_end_example_is_flagged():
    profile = _rpc_service_profile()
    profile["calls"] = [{"method": "widgets.get"}]
    rep = pf.preflight(_service_contract("rpc_service", profile))
    e2e = [f for f in rep["findings"] if f["check"] == "production_boundary_e2e"]
    assert not rep["passed"] and e2e and e2e[0]["kind"] == "rpc_service", rep
    print("ok  rpc_service without expected result -> production_boundary_e2e major")


def test_declared_service_kind_with_empty_or_absent_profile_is_flagged():
    # The obligation keys off the DECLARED deliverable_kind: a contract that declares a service kind but carries
    # no usable conformance profile still owes the production-boundary probe. (Anti-dodge by declaring
    # library/none instead is handled structurally — deliverable_kind is declared truthfully and conformance
    # boots the real artifact — NOT by inferring a boundary from contract text; see the FP-safety test below.)
    for kind, conformance in (
        ("http_service", {"http_service": {}}),
        ("rpc_service", None),
    ):
        c = _complete_cli_contract()
        c["objective"] = f"Expose a {kind} production boundary."
        c["acceptance_criteria"] = ["the service responds across its production boundary"]
        c["deliverable_kind"] = kind
        if conformance is None:
            c.pop("conformance", None)
        else:
            c["conformance"] = conformance
        rep = pf.preflight(c)
        e2e = [f for f in rep["findings"] if f["check"] == "production_boundary_e2e"]
        assert not rep["passed"] and e2e and e2e[0]["kind"] == kind, (kind, rep)
    print("ok  a declared service kind with an empty/absent profile -> production_boundary_e2e major")


def test_service_with_production_probe_passes_preflight():
    for kind, profile in (("http_service", _http_service_profile()), ("rpc_service", _rpc_service_profile())):
        rep = pf.preflight(_service_contract(kind, profile))
        assert rep["applicable"] and rep["passed"], (kind, rep)
        assert not [f for f in rep["findings"] if f["check"] == "production_boundary_e2e"], rep
    print("ok  service profiles with launch+endpoint/call+expected body/result pass preflight")


def test_non_service_kinds_are_not_forced_to_declare_service_probe():
    library = _complete_cli_contract()
    library["objective"] = "Add a reusable parser library that may call an HTTP API."
    library["acceptance_criteria"] = ["module exposes parse_config"]
    library["deliverable_kind"] = "library"
    library["conformance"] = {"library": {"module": "parserlib", "exported_symbols": ["parse_config"]}}

    none = _complete_cli_contract()
    none["objective"] = "Document the GET /health behavior without changing an executable interface."
    none["deliverable_kind"] = "none"
    none.pop("conformance", None)

    for c in (_complete_cli_contract(), library, none):
        rep = pf.preflight(c)
        assert rep["passed"], rep
        assert not [f for f in rep["findings"] if f["check"] == "production_boundary_e2e"], rep
    print("ok  cli/library/none contracts are not forced to declare service production-boundary probes")


def test_tree_scope_forbidden_pattern_out_of_scope_match_fails_preflight():
    c = _schema_complete_none_contract()
    c["files_allowed_to_change"] = ["src/in_scope.py"]
    c["forbidden_patterns"] = [{"pattern": "TREE_TOKEN", "scope": "tree"}]
    with tempfile.TemporaryDirectory(prefix="pf-tree-scope-") as d:
        root = Path(d)
        (root / "src").mkdir()
        (root / "docs").mkdir()
        (root / "src" / "in_scope.py").write_text("clean\n", encoding="utf-8")
        (root / "docs" / "legacy.md").write_text("TREE_TOKEN\n", encoding="utf-8")
        rep = pf.preflight(c, cwd=root)
    hits = [f for f in rep["findings"] if f["check"] == "tree_forbidden_pattern_scope"]
    assert not rep["passed"] and hits, rep
    assert hits[0]["source"] == "contract-preflight" and hits[0]["scope"] == "tree", hits
    assert hits[0]["hits"] == ["docs/legacy.md:1"], hits
    print("ok  tree-scope forbidden pattern with out-of-scope current match -> preflight failure")


def test_bare_word_forbidden_pattern_is_flagged_overbroad():
    # the incident: a bare word `\b[Ss]caffold\b` matches BOTH the demo references to rename AND the real
    # ADR-0008 mechanism the goal KEEPS -> the contract is unsatisfiable. The guard must flag it.
    c = _schema_complete_none_contract()
    c["forbidden_patterns"] = [{"pattern": r"\b[Ss]caffold\b", "scope": "leaf"}]
    rep = pf.preflight(c)
    ob = [f for f in rep["findings"] if f["check"] == "forbidden_pattern_overbroad"]
    assert not rep["passed"] and ob, rep
    assert ob[0]["severity"] == "major" and ob[0]["source"] == "contract-preflight", ob
    assert ob[0]["bare_word"].lower() == "scaffold" and ob[0]["pattern"] == r"\b[Ss]caffold\b", ob
    # routes to the designer (full re-synthesis), not implementer-only repair
    roles = cp._repair_roles_for([{"source": "contract-preflight"}], ["aufheben-designer", "implementer"])
    assert roles == ["aufheben-designer", "implementer"], roles
    print("ok  bare-word forbidden pattern -> forbidden_pattern_overbroad major, routes to designer")


def test_namespace_anchored_forbidden_patterns_not_flagged():
    # every namespace-precise authoring is backward compatible: NONE flagged overbroad.
    for pattern in (r"cockpit\.scaffold", "cockpit/scaffold", "import scaffold", "from cockpit import scaffold",
                    "SHAGIRI_SCAFFOLD", "def scaffold_runner", "class Scaffold", "_scaffold_seed_commit"):
        c = _schema_complete_none_contract()
        c["forbidden_patterns"] = [{"pattern": pattern, "scope": "leaf"}]
        rep = pf.preflight(c)
        assert not [f for f in rep["findings"] if f["check"] == "forbidden_pattern_overbroad"], (pattern, rep)
        assert rep["passed"], (pattern, rep)
    print("ok  namespace-anchored patterns (module/import/env/def/class/underscore-joined) -> not flagged")


def test_bare_word_overbroad_enriches_with_collision_hits_when_cwd_available():
    # advisory enrichment: when cwd is available, surface a few actual collision hits so re-author is mechanical
    # (the BLOCK decision is still the syntactic bare-word test).
    c = _schema_complete_none_contract()
    c["files_allowed_to_change"] = ["demo.py"]
    c["forbidden_patterns"] = [{"pattern": r"\b[Ss]caffold\b", "scope": "leaf"}]
    with tempfile.TemporaryDirectory(prefix="pf-overbroad-") as d:
        root = Path(d)
        (root / "demo.py").write_text("scaffold demo reference\n", encoding="utf-8")
        (root / "server.py").write_text("def _scaffold_seed_commit():\n    return scaffold\n", encoding="utf-8")
        rep = pf.preflight(c, cwd=root)
    ob = [f for f in rep["findings"] if f["check"] == "forbidden_pattern_overbroad"]
    assert ob and ob[0].get("collision_hits"), ob
    assert any("server.py" in h for h in ob[0]["collision_hits"]), ob
    print("ok  bare-word overbroad finding enriches with actual collision hits when cwd is available")


def test_wired_preflight_streams_before_block_folds():
    results = {"aufheben-designer": dict(_complete_cli_contract(), acceptance_criteria=[])}  # a flawed contract
    events = []
    orig = cp._stream_append
    orig_mode = cp.PREFLIGHT_MODE
    cp._stream_append = lambda repo, ev: events.append(ev)
    cp.PREFLIGHT_MODE = "shadow"                                  # this test asserts the SHADOW stream type
    try:
        rep = cp._contract_preflight("/tmp/leaf", results, "run-pf")
    finally:
        cp._stream_append = orig
        cp.PREFLIGHT_MODE = orig_mode
    assert rep and rep["applicable"] and rep["passed"] is False, rep
    streamed = [e for e in events if e.get("source") == "contract-preflight"]
    assert streamed and streamed[0]["type"] == "shadow_findings", streamed
    base = [{"severity": "minor"}]
    assert cp._apply_conformance_gate(base, rep, "shadow") == base, "shadow must not block"
    folded = cp._apply_conformance_gate(base, rep, "block")
    assert len(folded) > len(base), "block folds the failing preflight findings into the repair loop"
    print("ok  wired preflight streams shadow_findings; shadow never blocks, block folds in")


def test_default_mode_is_block():
    assert cp.PREFLIGHT_MODE == "block", "preflight promoted to block 2026-06-21 (FP-audit evidence, ADR-0009)"
    print("ok  default CONTRACT_PREFLIGHT mode is block (promoted; reversible via CONTRACT_PREFLIGHT=shadow)")


def _drive_preflight_gate(preflight_sequence, mode):
    """Run cp._preflight_gate with a scripted preflight() sequence and a fake aufheben stage; return
    (report, floored, aufheben_run_count, last_inputs_preflight)."""
    seq = iter(preflight_sequence)
    runs = []
    saved = (cp._contract_preflight, cp._execute_stage, cp._store_stage_output, cp.PREFLIGHT_MODE)
    cp._contract_preflight = lambda repo, results, run_id: next(seq, {"applicable": True, "passed": True,
                                                                       "findings": []})
    def fake_stage(repo, role, entry, objective, inputs, sid, cache, **kw):
        runs.append(inputs.get("preflight"))
        return True, {"role_id": role}, {"ok": True}, {"role": role, "sid": sid}
    cp._execute_stage = fake_stage
    cp._store_stage_output = lambda *a, **k: None
    try:
        cp.PREFLIGHT_MODE = mode
        rep, floored = cp._preflight_gate("/tmp/x", {"aufheben-designer": {}}, "r", "obj",
                                          {"aufheben-designer": object()}, {"aufheben-designer": []}, False, [])
    finally:
        cp._contract_preflight, cp._execute_stage, cp._store_stage_output, cp.PREFLIGHT_MODE = saved
    return rep, floored, runs


def test_preflight_gate_block_reruns_aufheben_then_proceeds():
    # block mode: preflight FAILS, then PASSES after one aufheben re-run -> proceed (not floored), aufheben
    # was re-run ONCE and fed the preflight findings. No implementer/verifier wave was consumed.
    rep, floored, runs = _drive_preflight_gate(
        [{"applicable": True, "passed": False, "findings": [{"check": "self_overlapping_scope"}]},
         {"applicable": True, "passed": True, "findings": []}], mode="block")
    assert floored is False and rep["passed"], (floored, rep)
    assert len(runs) == 1 and runs[0]["findings"], "one aufheben re-run, fed the preflight findings"
    print("ok  preflight gate (block): a contract defect re-runs aufheben only, then proceeds")


def test_preflight_gate_tree_scope_failure_routes_to_aufheben_not_implementer():
    rep, floored, runs = _drive_preflight_gate(
        [{"applicable": True, "passed": False,
          "findings": [{"source": "contract-preflight", "check": "tree_forbidden_pattern_scope"}]},
         {"applicable": True, "passed": True, "findings": []}], mode="block")
    roles = cp._repair_roles_for([{"source": "contract-preflight"}], ["aufheben-designer", "implementer"])
    assert floored is False and rep["passed"], (floored, rep)
    assert len(runs) == 1 and runs[0]["findings"][0]["check"] == "tree_forbidden_pattern_scope", runs
    assert roles == ["aufheben-designer", "implementer"], roles
    print("ok  tree-scope preflight failure feeds aufheben redesign path, not implementer-only repair")


def test_preflight_gate_block_fails_closed_after_cap():
    # block mode: preflight ALWAYS fails -> aufheben re-run up to the cap, then fail-closed (floored=True).
    always_fail = [{"applicable": True, "passed": False, "findings": [{"check": "x"}]}] * 10
    rep, floored, runs = _drive_preflight_gate(always_fail, mode="block")
    assert floored is True, "a persistent contract defect must fail closed (no implementer)"
    assert len(runs) == cp.PREFLIGHT_AUFHEBEN_CAP, ("aufheben re-run exactly cap times", len(runs))
    print("ok  preflight gate (block): a persistent defect fails closed after the cap")


def test_preflight_gate_shadow_never_reruns():
    # shadow (default): observe only — never re-run aufheben, never floor.
    rep, floored, runs = _drive_preflight_gate(
        [{"applicable": True, "passed": False, "findings": [{"check": "x"}]}], mode="shadow")
    assert floored is False and runs == [], "shadow mode is pure observation"
    print("ok  preflight gate (shadow): pure observation, no aufheben re-run, no floor")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
    print(f"\n{len(fns)} passed")
