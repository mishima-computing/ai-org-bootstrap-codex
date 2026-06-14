#!/usr/bin/env python3
"""Typed handoff models for the deterministic controller (ADR-0004 Phase 1).

These are the shapes that cross the boundary between the LLM semantic core and the Python
mechanical harness. The LLM emits a CarrierContract; Python executes it and emits a
ControllerRunReport; the LLM consumes the report and emits a SemanticDecision. Python validates
the SHAPE of each (fail-closed) but never makes the semantic decision.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict

SANDBOXES = {"read-only", "workspace-write", "danger-full-access"}
DECISIONS = {"accept", "revise_contract", "run_next_carrier", "block", "merge_ready"}


class ContractError(ValueError):
    pass


@dataclass
class CarrierContract:
    role: str
    prompt: str
    sandbox: str = "workspace-write"
    timeout: int = 600
    retries: int = 1
    files_allowed_to_change: list[str] = field(default_factory=list)
    forbidden_paths: list[str] = field(default_factory=list)
    expected_artifacts: list[str] = field(default_factory=list)
    expected_verifiers: list[str] = field(default_factory=list)

    def validate(self) -> "CarrierContract":
        if not self.role or not isinstance(self.role, str):
            raise ContractError("role is required")
        if not self.prompt or not isinstance(self.prompt, str):
            raise ContractError("prompt is required")
        if self.sandbox not in SANDBOXES:
            raise ContractError(f"sandbox must be one of {sorted(SANDBOXES)}")
        if int(self.timeout) <= 0:
            raise ContractError("timeout must be positive")
        if int(self.retries) < 0:
            raise ContractError("retries must be >= 0")
        if self.sandbox == "workspace-write" and not self.files_allowed_to_change:
            raise ContractError("workspace-write requires a non-empty files_allowed_to_change")
        return self

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "CarrierContract":
        known = {f for f in cls.__dataclass_fields__}
        unknown = set(d) - known
        if unknown:
            raise ContractError(f"unknown contract fields: {sorted(unknown)}")
        return cls(**d).validate()


@dataclass
class ControllerRunReport:
    contract_role: str
    ok: bool
    sandbox: str
    attempts: list[dict] = field(default_factory=list)
    changed_files: list[str] = field(default_factory=list)
    scope: dict = field(default_factory=dict)
    diff_artifact: dict | None = None
    verifier_results: list[dict] = field(default_factory=list)
    unresolved_failures: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SemanticDecision:
    decision: str
    rationale: str = ""

    def validate(self) -> "SemanticDecision":
        if self.decision not in DECISIONS:
            raise ContractError(f"decision must be one of {sorted(DECISIONS)}")
        return self

    @classmethod
    def from_dict(cls, d: dict) -> "SemanticDecision":
        if "decision" not in d:
            raise ContractError("decision is required")
        return cls(decision=d["decision"], rationale=d.get("rationale", "")).validate()


if __name__ == "__main__":
    import sys
    # quick smoke
    c = CarrierContract(role="implementer", prompt="do x", files_allowed_to_change=["a/**"])
    c.validate()
    assert CarrierContract.from_dict(c.to_dict()).role == "implementer"
    assert SemanticDecision.from_dict({"decision": "accept"}).decision == "accept"
    try:
        SemanticDecision.from_dict({"decision": "yolo"}); sys.exit("should have raised")
    except ContractError:
        pass
    print("controller_models smoke ok")
