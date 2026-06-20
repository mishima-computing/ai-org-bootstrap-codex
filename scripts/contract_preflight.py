#!/usr/bin/env python3
"""Deterministic pre-implementation contract review — ADR-0009 #1, the deterministic half.

Before the implementer runs, check the aufheben contract for COMPLETENESS and SELF-CONSISTENCY. Catching an
under-specified or self-contradictory contract at DESIGN time costs one re-run of aufheben; catching it after
an implementer build + linon review costs the whole wave. Contract/interface precision is the largest
observed repair class, so this attacks the leak at its cheapest point.

The boundary (ADR-0009): this checks the contract the design agent CHOSE for internal completeness and
consistency — deterministic, low false-positive. It does NOT judge whether the contract is the RIGHT one for
the goal; that is the judgment half (an independent LLM review of contract-vs-goal), a separate follow-up.
Findings use the recovery-ladder shape (`severity`, `passed`, ...) so they route through the existing
severity budget / repair loop with no parallel path.
"""
from __future__ import annotations

import fnmatch


def _finding(check: str, severity: str, detail: str, **extra) -> dict:
    return {"source": "contract-preflight", "check": check, "severity": severity,
            "passed": False, "detail": detail, **extra}


def _declared_status_codes(profile: dict) -> set:
    se = profile.get("status_and_errors") or {}
    codes = set()
    for key in ("success_codes", "invalid_input_codes", "operational_failure_codes"):
        codes.update(se.get(key) or [])
    return codes


def _cli_findings(profile: dict) -> list[dict]:
    """Completeness + consistency of a cli conformance profile, before any code exists."""
    findings: list[dict] = []
    entry = (profile.get("entrypoint") or {}).get("invocation")
    if not entry:
        findings.append(_finding("entrypoint", "major",
                                 "cli contract has no entrypoint.invocation — the gate cannot launch it"))

    examples = profile.get("examples") or []
    if not examples:
        findings.append(_finding("examples", "major", "cli contract declares no examples to verify against"))
        return findings

    # Coverage: the three load-bearing example classes. A contract missing any of them under-specifies the
    # interface (the implementer is free to guess the unspecified behaviour, which the gate then can't catch).
    invs = [(e.get("invocation") or "").strip() for e in examples]
    statuses = [e.get("expected_status") for e in examples if "expected_status" in e]
    if not any(("--help" in i) or ("-h" in i.split()) for i in invs):
        findings.append(_finding("coverage_help", "major",
                                 "cli contract has no --help/-h example (usage path unspecified)"))
    if not any(s == 0 for s in statuses):
        findings.append(_finding("coverage_success", "major",
                                 "cli contract has no success example (expected_status 0)"))
    if not any(isinstance(s, int) and s != 0 for s in statuses):
        findings.append(_finding("coverage_error", "major",
                                 "cli contract has no error example (a non-zero expected_status)"))

    # Consistency: when an exit-code policy is declared, every example must honour it — otherwise the contract
    # contradicts itself and the implementer cannot satisfy both halves.
    declared = _declared_status_codes(profile)
    if declared:
        for i, e in enumerate(examples):
            s = e.get("expected_status")
            if isinstance(s, int) and s not in declared:
                findings.append(_finding(
                    "status_consistency", "major",
                    f"example {i} expects exit {s}, not in declared status_and_errors {sorted(declared)}",
                    example=i, expected=s, declared=sorted(declared)))
    return findings


def preflight(contract: dict) -> dict:
    """Check the aufheben contract before implementation. Returns {applicable, passed, findings, checks_run}.
    `applicable` is False for anything that is not a contract (so it is a no-op on non-aufheben outputs)."""
    if not isinstance(contract, dict) or contract.get("role_id") != "aufheben-designer":
        return {"applicable": False, "passed": True, "findings": [], "checks_run": 0}

    findings: list[dict] = []
    checks = 0

    # Generic: a contract with nothing to verify against is under-specified regardless of kind.
    checks += 1
    if not (contract.get("acceptance_criteria") or []):
        findings.append(_finding("acceptance_criteria", "major",
                                 "contract has no acceptance_criteria — nothing to verify the build against"))

    # Generic: a SELF-OVERLAPPING scope — a deliverable that is both allowed AND forbidden. A blanket
    # files_not_allowed entry like "*" matches the allowed deliverable, and the coordination strip would
    # revert it before the scope check (the deliverable is silently lost). Catch the contradiction at design
    # time so aufheben narrows the forbidden set instead of paying a wasted repair round.
    checks += 1
    allowed = contract.get("files_allowed_to_change") or []
    forbidden = contract.get("files_not_allowed_to_change") or []
    for a in allowed:
        for f in forbidden:
            if fnmatch.fnmatch(a, f):
                findings.append(_finding(
                    "self_overlapping_scope", "major",
                    f"files_allowed_to_change '{a}' is also matched by files_not_allowed_to_change '{f}' — "
                    f"the deliverable is both allowed and forbidden; narrow the forbidden set",
                    allowed=a, forbidden=f))

    kind = contract.get("deliverable_kind")
    profile = (contract.get("conformance") or {}).get("cli")
    if kind == "cli" and isinstance(profile, dict):
        before = len(findings)
        findings += _cli_findings(profile)
        checks += 4 + len(profile.get("examples") or [])
        _ = before

    return {"applicable": True, "passed": not findings, "findings": findings, "checks_run": checks}
