#!/usr/bin/env python3
"""Targeted, deterministic repair of an aufheben contract — ADR-0009 #7, the incremental-repair half.

When contract_preflight raises a CONTRACT-LEVEL finding during repair, the org's default move is to re-run
aufheben and RE-SYNTHESIZE the whole contract. That is expensive (a full design re-run) and risky: the
re-synthesis can silently drift fields the finding never touched. For the subclass of findings whose fix
target is fully determined by the finding's structured detail, we apply a TARGETED, DETERMINISTIC patch to
just the implicated field instead — no LLM, no drift, auditable.

`patch_contract(contract, findings)` returns `(patched_contract, audit)`:
  - `patched_contract` is a NEW contract (the input is never mutated) with ONLY the implicated fields changed,
    or `None` when no finding was deterministically patchable — the signal to escalate to re-synthesis.
  - `audit` is a plain dict `{"applied": [<delta>, ...], "skipped": [{"check", "reason"}, ...]}`.

WHY the audit is RETURNED SEPARATELY (not attached to the contract): this module is the synthesis of a
two-carrier experiment — two independent carriers each implemented it, then exchanged code. Each found a
real flaw in the other's audit-storage choice:
  - an IN-BAND key (`contract["_contract_patch"]`) is JSON-durable but makes the contract SCHEMA-INVALID — the
    implementation-contract schema is `additionalProperties: false`, so the patched contract fails the very
    gates this system runs;
  - an OUT-OF-BAND dict-subclass attribute (`patched.patch_delta`) keeps the contract schema-pure but is LOST
    across `json.dumps`+reload and `dict()` — the audit silently vanishes the moment a contract is persisted
    or transmitted as JSON, which it always is here ("the log is the state source").
Returning the audit as its own value satisfies BOTH: the contract stays schema-valid AND the audit is an
ordinary JSON-serializable dict that depends on neither an in-band key nor a fragile attribute. The caller
persists it where "how the spec was made" belongs — the ADR-0009 spec_derivation / provenance, not the contract.

The patch is PURE: no side effects, and it does NOT re-run preflight itself. The contract is that the CALLER
re-runs contract_preflight on the patched contract to confirm the finding cleared (the verify-loop the test
demonstrates). The "did it work" judgment stays with the deterministic checker, never with the patcher
(ADR-0011: unproven never passes).
"""
from __future__ import annotations

import copy

# Deliverable kinds that owe a conformance profile. Mirrors contract_preflight._INTERFACE_KINDS; kept local so
# this module's patch surface is self-describing and does not silently follow a preflight change it has not
# been re-tested against.
_INTERFACE_KINDS = ("cli", "library", "http_service", "rpc_service", "batch_job", "json")

# Minimal-but-VALID conformance profile stubs, one per interface kind. Each satisfies the kind's `required`
# fields in schemas/implementation-contract.schema.json AND clears contract_preflight (_is_substantive_profile
# + the cli coverage checks), while staying an obvious placeholder the implementer/designer fills in. The
# verify-loop (caller re-runs preflight) is the safety net — a stub that failed to clear escalates.
_PROFILE_STUBS: dict[str, dict] = {
    "cli": {
        "entrypoint": {"invocation": "TODO: how the CLI is launched"},
        "status_and_errors": {"success_codes": [0], "invalid_input_codes": [2]},
        "examples": [
            {"invocation": "--help", "expected_status": 0},
            {"invocation": "TODO: a success invocation", "expected_status": 0},
            {"invocation": "TODO: an error invocation", "expected_status": 2},
        ],
    },
    "library": {"module": "TODO: importable module path", "exported_symbols": ["TODO_public_symbol"]},
    "http_service": {
        "start": {"command": "TODO: command to start the service"},
        "base_url": "http://127.0.0.1:8000",
        "readiness_timeout_seconds": 10,
        "examples": [{"method": "GET", "path": "/", "expected_status": 200}],
    },
    "rpc_service": {
        "start": {"command": "TODO: command to start the service"},
        "base_url": "http://127.0.0.1:8000",
        "transport": "json_rpc_http",
        "calls": [{"method": "TODO.method"}],
    },
    "batch_job": {"run": {"command": "TODO: command that runs the job once"}},
    "json": {"files": [{"path": "TODO/produced.json"}]},
}


def _delta(check: str, field: str, action: str, before, after, **extra) -> dict:
    """One audit record: which preflight check drove the patch, which contract field it touched, how, and the
    exact before/after of that field (deep-copied so later mutation can't rewrite history). The before/after
    pair makes the delta self-contained — a reviewer reconstructs the change without re-deriving it."""
    return {"check": check, "field": field, "action": action,
            "before": copy.deepcopy(before), "after": copy.deepcopy(after), **extra}


def _patch_self_overlapping_scope(contract: dict, finding: dict, applied: list) -> bool:
    """Narrow the forbidden set so the offending deliverable is no longer both allowed and forbidden. The
    finding carries the exact `allowed` and `forbidden` globs; we drop only that one over-broad forbidden
    entry and preserve every other rule. No structured `forbidden` field -> decline (can't act surgically)."""
    forbidden_glob = finding.get("forbidden")
    if not forbidden_glob:
        return False
    forbidden = contract.get("files_not_allowed_to_change")
    if not isinstance(forbidden, list) or forbidden_glob not in forbidden:
        return False
    new_forbidden = [f for f in forbidden if f != forbidden_glob]
    contract["files_not_allowed_to_change"] = new_forbidden
    applied.append(_delta("self_overlapping_scope", "files_not_allowed_to_change", "removed_forbidden_glob",
                          before=forbidden, after=new_forbidden,
                          removed=forbidden_glob, allowed=finding.get("allowed")))
    return True


def _patch_conformance_profile(contract: dict, finding: dict, applied: list) -> bool:
    """Insert a minimal valid conformance.<kind> stub for the declared deliverable_kind. Only for a kind we
    have a stub for, and only when no substantive profile is already there (idempotent — never clobbers a
    real profile, and a stale finding against an already-filled profile declines)."""
    kind = finding.get("kind")
    if kind not in _PROFILE_STUBS:
        return False
    conformance = contract.get("conformance")
    if not isinstance(conformance, dict):
        conformance = {}
        contract["conformance"] = conformance
    existing = conformance.get(kind)
    if isinstance(existing, dict) and existing:
        return False
    stub = copy.deepcopy(_PROFILE_STUBS[kind])
    conformance[kind] = stub
    applied.append(_delta("conformance_profile", f"conformance.{kind}", "inserted_stub_profile",
                          before=existing, after=stub, kind=kind))
    return True


def _patch_deliverable_kind(contract: dict, finding: dict, applied: list) -> bool:
    """Fill a missing deliverable_kind ONLY when it is deterministically derivable: the contract already
    carries EXACTLY ONE substantive conformance profile, so the declared interface unambiguously implies the
    kind. Zero or many profiles is a judgment call -> decline (escalate). Empty `{}` is non-substantive."""
    if contract.get("deliverable_kind") is not None:
        return False
    conformance = contract.get("conformance")
    if not isinstance(conformance, dict):
        return False
    present = [k for k in _INTERFACE_KINDS if isinstance(conformance.get(k), dict) and conformance.get(k)]
    if len(present) != 1:
        return False
    contract["deliverable_kind"] = present[0]
    applied.append(_delta("deliverable_kind", "deliverable_kind", "derived_from_sole_profile",
                          before=None, after=present[0], value=present[0]))
    return True


def _patch_acceptance_criteria(contract: dict, finding: dict, applied: list) -> bool:
    """Fill empty acceptance_criteria ONLY when the FINDING itself carries the criteria. What a build must
    satisfy is the heart of the contract's judgment — never FABRICATE a placeholder (clearing the check while
    asserting nothing true is worse than escalating). The real contract_preflight finding carries no criteria,
    so it always declines here; only a finding that explicitly supplies a non-blank string list is applied
    (deterministic — same "driven by the finding's structured detail" contract as the other patchers)."""
    if contract.get("acceptance_criteria"):
        return False
    supplied = finding.get("acceptance_criteria")
    if not (isinstance(supplied, list) and supplied
            and all(isinstance(s, str) and s.strip() for s in supplied)):
        return False
    cleaned = [s.strip() for s in supplied]
    contract["acceptance_criteria"] = cleaned
    applied.append(_delta("acceptance_criteria", "acceptance_criteria", "applied_finding_supplied_criteria",
                          before=None, after=cleaned))
    return True


# Dispatch: preflight check name -> its surgical patcher. A finding whose check is not here is, by
# construction, not deterministically patchable (it escalates).
_PATCHERS = {
    "self_overlapping_scope": _patch_self_overlapping_scope,
    "conformance_profile": _patch_conformance_profile,
    "deliverable_kind": _patch_deliverable_kind,
    "acceptance_criteria": _patch_acceptance_criteria,
}


def patch_contract(contract: dict, findings: list) -> tuple:
    """Apply deterministic, targeted patches for the deterministically-patchable contract-preflight findings.

    Returns `(patched_contract, audit)`:
      - `patched_contract`: a NEW contract (input never mutated) with ONLY the implicated fields changed and
        NOTHING attached to it (it stays schema-valid), or `None` when no finding was deterministically
        patchable (escalate to re-synthesis);
      - `audit`: `{"applied": [<delta>...], "skipped": [{"check", "reason"}...]}` — a plain JSON-serializable
        dict the caller persists into provenance (spec_derivation). Returned even when patched_contract is None,
        so the caller can log WHY it escalated.

    Pure: no I/O, no preflight re-run. The caller re-runs contract_preflight on `patched_contract` to confirm
    the findings cleared (the verify-loop)."""
    applied: list = []
    skipped: list = []
    if not isinstance(contract, dict) or not findings:
        return None, {"applied": applied, "skipped": skipped}

    patched = copy.deepcopy(contract)
    for finding in findings:
        if finding.get("source") != "contract-preflight":
            skipped.append({"check": finding.get("check"), "reason": "not a contract-preflight finding"})
            continue
        check = finding.get("check")
        patcher = _PATCHERS.get(check)
        if patcher is None:
            skipped.append({"check": check, "reason": "no deterministic patcher for this check"})
            continue
        if not patcher(patched, finding, applied):
            skipped.append({"check": check, "reason": "finding not deterministically patchable"})

    audit = {"applied": applied, "skipped": skipped}
    return (patched if applied else None), audit


__all__ = ["patch_contract"]
