#!/usr/bin/env python3
"""Tests for contract_patch — the deterministic contract-patch repair (ADR-0009 #7). Plain def test_* + a
__main__ runner (scripts/ idiom, no pytest). The verify-loop (re-run contract_preflight after patching) is
exercised directly, since that is the contract: the patcher proposes, the deterministic checker confirms."""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import contract_patch as cp  # noqa: E402
import contract_preflight as pf  # noqa: E402


def _findings(contract: dict) -> list:
    return pf.preflight(contract)["findings"]


def _base(**over) -> dict:
    c = {"role_id": "aufheben-designer", "acceptance_criteria": ["the tool does the thing"],
         "deliverable_kind": "none"}
    c.update(over)
    return c


# --- the converged core: each patcher clears its finding, confirmed by re-running preflight ---------------

def test_conformance_profile_inserted_and_preflight_clears():
    c = _base(deliverable_kind="cli", conformance={})            # declares cli but no cli profile
    findings = _findings(c)
    assert any(f["check"] == "conformance_profile" for f in findings), findings
    patched, audit = cp.patch_contract(c, findings)
    assert patched is not None, audit
    assert isinstance(patched["conformance"]["cli"], dict) and patched["conformance"]["cli"]
    # VERIFY-LOOP: the conformance_profile finding is gone after the patch
    assert not any(f["check"] == "conformance_profile" for f in _findings(patched)), _findings(patched)
    assert audit["applied"][0]["check"] == "conformance_profile"
    assert audit["applied"][0]["field"] == "conformance.cli"
    print("ok  conformance_profile: stub inserted, preflight re-run clears the finding")


def test_self_overlapping_scope_narrowed_and_preflight_clears():
    c = _base(files_allowed_to_change=["engagement/wallet.py"],
              files_not_allowed_to_change=["engagement/*", "secrets/**"])
    findings = _findings(c)
    assert any(f["check"] == "self_overlapping_scope" for f in findings), findings
    patched, audit = cp.patch_contract(c, findings)
    assert patched is not None
    assert "engagement/*" not in patched["files_not_allowed_to_change"]
    assert "secrets/**" in patched["files_not_allowed_to_change"], "only the offending glob is removed"
    assert not any(f["check"] == "self_overlapping_scope" for f in _findings(patched))
    print("ok  self_overlapping_scope: offending forbidden glob removed, preflight clears")


def test_deliverable_kind_derived_only_from_a_sole_profile():
    one = {"role_id": "aufheben-designer", "acceptance_criteria": ["x"],
           "conformance": {"library": {"module": "m", "exported_symbols": ["f"]}}}
    patched, audit = cp.patch_contract(one, _findings(one))
    assert patched is not None and patched["deliverable_kind"] == "library", audit
    # two profiles -> ambiguous -> decline (escalate)
    two = {"role_id": "aufheben-designer", "acceptance_criteria": ["x"],
           "conformance": {"library": {"module": "m", "exported_symbols": ["f"]},
                           "json": {"files": [{"path": "a.json"}]}}}
    patched2, audit2 = cp.patch_contract(two, _findings(two))
    assert patched2 is None, "ambiguous kind must escalate, not guess"
    print("ok  deliverable_kind: derived from a sole profile, escalates when ambiguous")


def test_acceptance_criteria_always_escalates_for_the_real_finding():
    c = _base(acceptance_criteria=[])                            # the real preflight finding carries no criteria
    findings = _findings(c)
    assert any(f["check"] == "acceptance_criteria" for f in findings), findings
    patched, audit = cp.patch_contract(c, findings)
    assert patched is None, "acceptance_criteria is judgment, never fabricated -> escalate"
    assert any(s["check"] == "acceptance_criteria" for s in audit["skipped"]), audit
    print("ok  acceptance_criteria: declined (judgment), recorded in skipped, escalates")


def test_acceptance_criteria_applied_when_finding_supplies_them():
    c = _base(acceptance_criteria=[])
    finding = {"source": "contract-preflight", "check": "acceptance_criteria",
               "acceptance_criteria": ["  greets the user  ", "rejects bad input"]}
    patched, audit = cp.patch_contract(c, [finding])
    assert patched is not None and patched["acceptance_criteria"] == ["greets the user", "rejects bad input"]
    print("ok  acceptance_criteria: applied (trimmed) only when the finding supplies them")


# --- escalation: nothing deterministically patchable -> None ---------------------------------------------

def test_non_patchable_findings_return_none():
    c = _base()
    assert cp.patch_contract(c, [{"source": "conformance", "check": "exit_status"}])[0] is None  # foreign source
    assert cp.patch_contract(c, [{"source": "contract-preflight", "check": "mystery"}])[0] is None  # unknown
    assert cp.patch_contract(c, [])[0] is None                  # no findings
    assert cp.patch_contract("not a dict", [{"check": "x"}])[0] is None
    print("ok  non-patchable / foreign / empty -> None (escalate), audit still returned")


# --- the synthesis: contract stays SCHEMA-PURE, audit is RETURNED SEPARATELY and JSON-durable -------------

def test_patched_contract_is_schema_pure_and_audit_is_separate_and_serializable():
    c = _base(deliverable_kind="cli", conformance={})
    patched, audit = cp.patch_contract(c, _findings(c))
    # the audit is NOT attached to the contract (no in-band key) and the contract is a plain dict (no fragile
    # subclass attribute) -> the contract stays schema-valid AND both survive a JSON round-trip.
    assert "_contract_patch" not in patched, "audit must not be in-band on the contract (schema-invalidity)"
    assert type(patched) is dict, "the contract must be a plain dict (no out-of-band attribute to lose)"
    assert json.loads(json.dumps(patched)) == patched, "contract survives JSON serialization"
    assert json.loads(json.dumps(audit)) == audit, "audit survives JSON serialization (durable for provenance)"
    assert audit["applied"], "audit carries the applied deltas the caller persists into spec_derivation"
    print("ok  synthesis: contract schema-pure, audit separate + JSON-durable (resolves both carriers' flaws)")


def test_patch_does_not_drift_unrelated_fields():
    c = _base(deliverable_kind="cli", conformance={}, files_allowed_to_change=["a.py"],
              extensions={"note": "keep me"})
    before = copy.deepcopy(c)
    patched, _ = cp.patch_contract(c, _findings(c))
    assert c == before, "the input contract must never be mutated"
    # only `conformance` changed; every other field is byte-identical
    assert {k: v for k, v in patched.items() if k != "conformance"} == \
           {k: v for k, v in before.items() if k != "conformance"}, "no drift in unrelated fields"
    print("ok  no drift: input unmutated, only the implicated field changed")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
    print(f"\n{len(fns)} passed")
