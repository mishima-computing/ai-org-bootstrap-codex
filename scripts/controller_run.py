#!/usr/bin/env python3
"""Carrier run entrypoint — routes a carrier through the deterministic controller with the gates its
role CLASS needs (dogfood: this is the real launch path, replacing bare `carrier_harness.py run`).

Carrier classes (from the registry's write_scope + schema):
  * write carriers (write_scope non-empty) — implementer, CI writers — get scope enforcement;
    implementer additionally gets the quality gate (lint / debug-tag).
  * producing / verifying carriers (write_scope []) — designers, genius, aufheben, linon, stefan —
    emit schema-valid JSON (captured via `-o result.json`), gated by the role's schema in Python.

All classes get: the universal launch core (stdin-closed / discipline / timeout / retry), the
content-addressed cache (skip the carrier on an identical contract + state), and a content-addressed
journal. The LLM only authors the contract; orchestration spends zero LLM tokens.

  controller_run.py --repo R --contract contract.json --run-id ID [--no-cache]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, str(Path(HERE).parent / "packages" / "codex-org-bootstrap" / "src"))
import controller_workflow as workflow  # noqa: E402

OUTPUT_FILE = "result.json"  # where producing/verifying carriers emit their JSON deliverable


def _registry_role(repo: Path, role: str) -> dict:
    """Return {schema, write_scope} for a role, from the canonical registry."""
    from ai_org_bootstrap.registry import load_runtime_registry
    for e in load_runtime_registry(repo / "registry" / "runtime-registry.yaml"):
        if e.agent_id == role:
            return {"schema": e.schema, "write_scope": list(e.write_scope or [])}
    raise SystemExit(f"unknown role '{role}' (not in registry)")


def run(repo, contract: dict, run_id: str, *, cache=True) -> workflow.models.ControllerRunReport:
    repo = Path(repo).resolve()
    role = contract.get("role")
    info = _registry_role(repo, role)
    is_implementer = role == "implementer"
    is_producing = not info["write_scope"]  # read-only producers / verifiers emit JSON, write no files

    kwargs = {"cache_enabled": cache, "quality_gate_enabled": is_implementer}
    if is_producing:
        kwargs["output_schema"] = str(repo / info["schema"])
        kwargs["output_path"] = OUTPUT_FILE
    return workflow.run_contract(repo, contract, run_id, **kwargs)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo", required=True)
    p.add_argument("--contract", required=True, help="path to contract.json")
    p.add_argument("--run-id", required=True)
    p.add_argument("--no-cache", action="store_true")
    a = p.parse_args(argv)
    contract = json.loads(Path(a.contract).read_text(encoding="utf-8"))
    rep = run(a.repo, contract, a.run_id, cache=not a.no_cache)
    print(json.dumps(rep.to_dict(), indent=2, ensure_ascii=False))
    return 0 if rep.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
