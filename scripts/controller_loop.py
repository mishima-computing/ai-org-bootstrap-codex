#!/usr/bin/env python3
"""Semantic-decision loop for the deterministic controller (ADR-0004 Phase 3).

Closes the Workflow/Activity loop. Each round: Python runs the contract (controller_workflow →
ControllerRunReport), then hands the report to the semantic core (the LLM `decider` — the Activity)
which returns a SemanticDecision; Python validates its shape (fail-closed) and ADVANCES on it. The
decision is the LLM's; the advancing is Python's. The loop is bounded by max_rounds (deterministic
safety) and journals every round.

  decider(report_dict, round_index) -> dict   # {"decision": ..., "rationale": ..., "next_contract": {...}?}

Terminal decisions: accept, merge_ready, block. Looping: revise_contract, run_next_carrier — these
MUST supply `next_contract` or the loop fails closed to block.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import controller_models as models          # noqa: E402
import controller_workflow as workflow      # noqa: E402
from controller_evidence import RunJournal  # noqa: E402

TERMINAL = {"accept", "merge_ready", "block"}
LOOPING = {"revise_contract", "run_next_carrier"}


def run_loop(repo, contract, run_id, *, decider, carrier_runner=None, verifier_specs=None,
             include_builtin_gates=True, declared=None, max_rounds=3, clock=None,
             quality_gate_enabled=False, cache_enabled=False) -> dict:
    """Run contract → report → semantic decision → advance, until terminal or max_rounds."""
    repo = Path(repo)
    current = contract if isinstance(contract, models.CarrierContract) else \
        models.CarrierContract.from_dict(contract)
    journal = RunJournal(repo, run_id, clock=clock)
    rounds = []

    for r in range(max_rounds):
        report = workflow.run_contract(
            repo, current, f"{run_id}-r{r}", verifier_specs=verifier_specs,
            include_builtin_gates=include_builtin_gates, declared=declared,
            carrier_runner=carrier_runner, clock=clock,
            quality_gate_enabled=quality_gate_enabled, cache_enabled=cache_enabled)
        raw = decider(report.to_dict(), r)                 # the Activity (LLM)
        decision = models.SemanticDecision.from_dict(raw)  # fail-closed shape validation
        # Mechanical failure overrides semantic acceptance: the LLM may not accept/merge a run whose
        # deterministic report is not ok (carrier failure / scope violation / failed verifier).
        overridden = None
        if decision.decision in ("accept", "merge_ready") and not report.ok:
            overridden = decision.decision
            decision = models.SemanticDecision(
                decision="block",
                rationale=f"mechanical override: report not ok, '{overridden}' rejected — {report.unresolved_failures}")
        journal.append("semantic_decision", {"round": r, "report_ok": report.ok,
                                              "decision": decision.decision,
                                              "overridden_decision": overridden,
                                              "rationale": decision.rationale})
        rounds.append({"round": r, "report": report.to_dict(),
                       "decision": decision.decision, "rationale": decision.rationale})

        if decision.decision in TERMINAL:
            return {"final": decision.decision, "rounds": rounds, "round_count": r + 1}

        # looping decision: must carry a next_contract, else fail closed
        nxt = raw.get("next_contract")
        if not nxt:
            journal.append("loop_block", {"reason": "looping decision without next_contract"})
            return {"final": "block", "reason": "looping decision without next_contract",
                    "rounds": rounds, "round_count": r + 1}
        current = models.CarrierContract.from_dict(nxt)

    journal.append("loop_block", {"reason": "max_rounds exhausted"})
    return {"final": "block", "reason": "max_rounds exhausted",
            "rounds": rounds, "round_count": max_rounds}


if __name__ == "__main__":
    print("controller_loop is a library; the decider (semantic core) is injected by the controller.")
