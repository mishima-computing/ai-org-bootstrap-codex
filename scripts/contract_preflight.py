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
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import conformance  # noqa: E402

# Deliverable kinds that declare an executable interface (so a conformance profile is owed). "none" is the
# explicit no-interface declaration and is deliberately NOT in this set.
_INTERFACE_KINDS = ("cli", "library", "http_service", "rpc_service", "batch_job", "json")
_SERVICE_KINDS = ("http_service", "rpc_service")


def _is_substantive_profile(profile) -> bool:
    """A conformance profile that actually pins something. An absent profile or an empty object ({}) pins
    nothing, so a declared interface kind carrying one is as under-specified as declaring no kind at all."""
    return isinstance(profile, dict) and bool(profile)


def _finding(check: str, severity: str, detail: str, **extra) -> dict:
    return {"source": "contract-preflight", "check": check, "severity": severity,
            "passed": False, "detail": detail, "failure_classification": "code", **extra}


def _tree_forbidden_pattern_findings(contract: dict, cwd: str | None) -> tuple[list[dict], int]:
    """Tree-scope patterns must be satisfiable by the leaf before implementation starts."""
    if cwd is None:
        return [], 0
    patterns = conformance.tree_forbidden_patterns(contract)
    if not patterns:
        return [], 0
    probe_contract = {
        "role_id": "aufheben-designer",
        "deliverable_kind": contract.get("deliverable_kind", "none"),
        "files_allowed_to_change": contract.get("files_allowed_to_change"),
        "forbidden_patterns": patterns,
    }
    report = conformance.run_forbidden_patterns(probe_contract, cwd=cwd)
    findings: list[dict] = []
    for advisory in report.get("advisory_findings") or []:
        if not isinstance(advisory, dict):
            continue
        pattern = advisory.get("pattern")
        count = advisory.get("count", 0)
        hits = advisory.get("hits") or []
        detail = (f"tree-scoped forbidden pattern {pattern!r} already appears {count} time(s) outside "
                  "files_allowed_to_change; widen/split the contract or use scope:'leaf' if total repository "
                  "absence is not required")
        if hits:
            detail += "; e.g. " + ", ".join(hits)
        findings.append(_finding(
            "tree_forbidden_pattern_scope", "major", detail,
            pattern=pattern, count=count, hits=hits, scope="tree"))
    return findings, len(patterns)


def _http_has_e2e_example(profile) -> bool:
    if not isinstance(profile, dict):
        return False
    start = profile.get("start")
    if not isinstance(start, dict) or not start.get("command") or not profile.get("base_url"):
        return False
    for ex in profile.get("examples") or []:
        if not isinstance(ex, dict):
            continue
        body = ex.get("expected_body_contains")
        if ex.get("method") and ex.get("path") is not None and isinstance(ex.get("expected_status"), int) \
                and isinstance(body, list) and any(isinstance(x, str) and x for x in body):
            return True
    return False


def _rpc_has_e2e_example(profile) -> bool:
    if not isinstance(profile, dict):
        return False
    start = profile.get("start")
    if not isinstance(start, dict) or not start.get("command") or not profile.get("base_url") \
            or not profile.get("transport"):
        return False
    for call in profile.get("calls") or []:
        if not isinstance(call, dict) or not call.get("method"):
            continue
        result = call.get("expected_result_contains")
        if isinstance(result, list) and any(isinstance(x, str) and x for x in result):
            return True
        if isinstance(call.get("expected_error_code"), int):
            return True
    return False


def _has_production_boundary_e2e(kind: str, profile) -> bool:
    if kind == "http_service":
        return _http_has_e2e_example(profile)
    if kind == "rpc_service":
        return _rpc_has_e2e_example(profile)
    return False


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


def preflight(contract: dict, *, cwd=None) -> dict:
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

    # Deliverable-kind declaration: the conformance obligation is otherwise dodgeable by SILENCE — a contract
    # that simply omits deliverable_kind escapes every interface check below. Require the kind to be DECLARED
    # (including the explicit "none" for a no-interface deliverable), so a producer cannot opt out of the
    # interface contract by saying nothing. This is the structural half of "make the designer satisfy the
    # reviewer": the obligation cannot be skipped, only discharged or explicitly marked not-applicable.
    checks += 1
    kind = contract.get("deliverable_kind")
    if kind is None:
        findings.append(_finding(
            "deliverable_kind", "major",
            "contract does not declare deliverable_kind — declare it explicitly "
            "(cli/library/http_service/rpc_service/batch_job/json), or 'none' when there is no checkable "
            "interface; silence must not be a way to skip the interface contract"))
    elif kind in _INTERFACE_KINDS:
        # An interface deliverable must carry its conformance profile, or there is nothing to verify the built
        # artifact against — the interface-precision leak this gate exists to close.
        checks += 1
        profile = (contract.get("conformance") or {}).get(kind)
        if not _is_substantive_profile(profile):
            findings.append(_finding(
                "conformance_profile", "major",
                f"deliverable_kind '{kind}' declares an executable interface but carries no usable "
                f"conformance.{kind} profile — encode the interface so the gate can check the built artifact",
                kind=kind))
        elif kind == "cli":
            findings += _cli_findings(profile)
            checks += 4 + len(profile.get("examples") or [])

    # Service production-boundary obligation, keyed STRICTLY off the DECLARED deliverable_kind. A contract that
    # declares http_service/rpc_service owes a runnable end-to-end probe — launch command, externally
    # observable endpoint/call, and expected body/result — so conformance boots the REAL artifact instead of a
    # stub. The obligation is NEVER inferred from contract text: text inference is FP-prone and has no place in
    # a blocking gate. Anti-dodge (declaring `library`/`none` to escape the probe) is handled structurally —
    # deliverable_kind is declared truthfully and conformance boots the real artifact — not by a classifier.
    if kind in _SERVICE_KINDS:
        checks += 1
        service_profile = (contract.get("conformance") or {}).get(kind)
        if not _has_production_boundary_e2e(kind, service_profile):
            findings.append(_finding(
                "production_boundary_e2e", "major",
                f"deliverable_kind '{kind}' declares a production/integration boundary but does not provide a "
                f"runnable conformance.{kind} end-to-end example with launch command, externally observable "
                f"endpoint/call, and expected body/result — encode the probe so conformance boots the real "
                f"artifact",
                kind=kind))

    tree_findings, tree_checks = _tree_forbidden_pattern_findings(contract, str(cwd) if cwd is not None else None)
    findings += tree_findings
    checks += tree_checks

    return {"applicable": True, "passed": not findings, "findings": findings, "checks_run": checks}
