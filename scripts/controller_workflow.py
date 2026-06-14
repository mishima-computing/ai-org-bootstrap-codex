#!/usr/bin/env python3
"""Deterministic controller workflow runner (ADR-0004 Phase 2).

The Workflow side of the Workflow/Activity split. It advances a fixed phase sequence —
prepare → validate_contract → run_carrier → enforce_scope → run_verifiers → package_evidence →
await_semantic_judgment — wiring Phase-1 modules (models, scope, evidence) and the carrier harness.
It NEVER authors the contract or judges the deliverable: it executes mechanics, records an immutable
journal, and returns a ControllerRunReport for the semantic core to judge. The carrier runner is
injectable (the Activity boundary), so the wiring is testable offline without a real Codex carrier.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # sibling modules, any PYTHONPATH
import controller_models as models          # noqa: E402
import controller_scope as scope            # noqa: E402
import controller_verifiers as verifiers    # noqa: E402
from controller_evidence import RunJournal, sha256_text  # noqa: E402


def _default_carrier_runner(repo, prompt, sandbox, *, timeout, retries, out_dir):
    import carrier_harness
    return carrier_harness.run_carrier(repo, prompt, sandbox, timeout=timeout,
                                       retries=retries, out_dir=out_dir, prepend_discipline=True)


def run_contract(repo, contract, run_id, *, verifier_specs=None, include_builtin_gates=True,
                 declared=None, carrier_runner=None, clock=None) -> models.ControllerRunReport:
    """Execute one contract deterministically and return the report (await_semantic_judgment)."""
    repo = Path(repo)
    contract = contract if isinstance(contract, models.CarrierContract) else \
        models.CarrierContract.from_dict(contract)
    contract.validate()

    journal = RunJournal(repo, run_id, clock=clock)
    journal.append("validate_contract", {"role": contract.role, "sandbox": contract.sandbox,
                                         "prompt_sha256": sha256_text(contract.prompt),
                                         "files_allowed_to_change": contract.files_allowed_to_change})

    baseline = scope.baseline_of(repo)
    journal.append("baseline", {"touched": sorted(baseline)})

    runner = carrier_runner or _default_carrier_runner
    carrier = runner(repo, contract.prompt, contract.sandbox,
                     timeout=contract.timeout, retries=contract.retries, out_dir=journal.dir)
    carrier_ok = bool(carrier.get("ok"))
    attempts = carrier.get("attempts", [])
    journal.append("run_carrier", {"ok": carrier_ok, "attempts": attempts})

    forbidden = contract.forbidden_paths or scope.DEFAULT_FORBIDDEN
    scope_report = scope.enforce(repo, contract.files_allowed_to_change, baseline=baseline,
                                 forbidden=forbidden, declared=declared)
    journal.append("enforce_scope", scope_report.to_dict())

    specs = list(verifier_specs or [])
    if include_builtin_gates:
        specs = verifiers.builtin_gate_specs(repo) + specs
    verifier_runs = verifiers.run_all(specs, evidence_dir=journal.dir) if specs else []
    journal.append("run_verifiers", {"results": [v.to_dict() for v in verifier_runs]})

    unresolved = []
    if not carrier_ok:
        unresolved.append("carrier did not complete (timeout/hang/nonzero)")
    if not scope_report.scope_ok:
        unresolved.append(f"scope: deviations={scope_report.deviations} "
                          f"forbidden={scope_report.forbidden_hits} undeclared={scope_report.undeclared}")
    for v in verifier_runs:
        if v.status != "pass":
            unresolved.append(f"verifier {v.name}: {v.status} (exit {v.exit_code})")

    ok = carrier_ok and scope_report.scope_ok and verifiers.all_passed(verifier_runs)
    report = models.ControllerRunReport(
        contract_role=contract.role, ok=ok, sandbox=contract.sandbox, attempts=attempts,
        changed_files=scope_report.changed, scope=scope_report.to_dict(),
        verifier_results=[v.to_dict() for v in verifier_runs], unresolved_failures=unresolved,
    )
    journal.append("package_evidence", {"ok": ok, "report": report.to_dict()})
    # await_semantic_judgment: the report is returned; the LLM/owner decides. The workflow does not.
    return report


if __name__ == "__main__":
    import argparse
    import json
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo", required=True)
    p.add_argument("--contract", required=True, help="path to contract.json")
    p.add_argument("--run-id", required=True)
    args = p.parse_args()
    contract = json.loads(Path(args.contract).read_text(encoding="utf-8"))
    rep = run_contract(args.repo, contract, args.run_id)
    print(json.dumps(rep.to_dict(), indent=2, ensure_ascii=False))
    raise SystemExit(0 if rep.ok else 1)
