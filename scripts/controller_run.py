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


def org_root(repo) -> Path:
    """The ORG install — where this org's registry/, schemas/, and bootstrap/ live — as distinct from
    the WORKSPACE (the repo being changed). Defaults to the workspace (self-hosted: the org operates on
    itself, so behaviour is unchanged). Set AI_ORG_ROOT (or pass --org-root, which exports it) to
    operate the org on an EXTERNAL --repo: the org definition then comes from the install, while the
    carrier still runs in --repo."""
    env = os.environ.get("AI_ORG_ROOT")
    return Path(env).expanduser().resolve() if env else Path(repo).resolve()


def _registry_role(repo: Path, role: str) -> dict:
    """Return {schema, write_scope, role_file} for a role, from the canonical registry in the ORG install."""
    from ai_org_bootstrap.registry import load_runtime_registry
    for e in load_runtime_registry(org_root(repo) / "registry" / "runtime-registry.yaml"):
        if e.agent_id == role:
            return {"schema": e.schema, "write_scope": list(e.write_scope or []), "role_file": e.role}
    raise SystemExit(f"unknown role '{role}' (not in registry)")


def _inject_role_instructions(repo: Path, contract: dict, role_file: str) -> dict:
    """Prepend the role's `.md` (its job description: authority, forbidden actions, output schema,
    hand-off rules) to the carrier prompt. The carrier prompt otherwise carries only the role NAME plus
    the objective, so a role sees no constraints and behaves like a generic do-the-task agent — e.g. a
    CI-action writer (whose authority is `.github/workflows/**` only) implements the feature instead,
    or a designer implements the objective instead of emitting a schema-valid JSON proposal. The
    `.codex/agents/*.toml` adapter is NOT loaded by `codex exec`, so the role.md must be injected here.
    Returns a copy — never mutates the caller's contract."""
    md = org_root(repo) / role_file
    if not md.is_file():
        return contract
    contract = dict(contract)
    contract["prompt"] = (md.read_text(encoding="utf-8").rstrip()
                          + "\n\n---\n\n## Contract\n" + contract.get("prompt", ""))
    return contract


def _inject_output_schema(repo: Path, contract: dict, schema_file: str) -> dict:
    """Append a producing role's output JSON Schema to the prompt. The role.md says 'conform to schema X'
    but the carrier never SEES X, so it violates constraints it cannot know (maxLength, enum, required)
    and the gate then rejects it. Showing the schema lets the carrier produce conformant JSON the first
    time. Returns a copy."""
    sf = org_root(repo) / schema_file
    if not sf.is_file():
        return contract
    contract = dict(contract)
    contract["prompt"] = (contract.get("prompt", "")
                          + "\n\n---\n\n## Output JSON Schema (your result MUST validate against this — "
                          "respect every maxLength, enum, required, and type; output raw JSON only)\n"
                          + sf.read_text(encoding="utf-8"))
    return contract


def run(repo, contract: dict, run_id: str, *, cache=True,
        resume_session=None) -> workflow.models.ControllerRunReport:
    repo = Path(repo).resolve()
    role = contract.get("role")
    info = _registry_role(repo, role)
    is_implementer = role == "implementer"
    is_producing = not info["write_scope"]  # read-only producers / verifiers emit JSON, write no files

    contract = _inject_role_instructions(repo, contract, info["role_file"])   # the carrier must SEE its role
    kwargs = {"cache_enabled": cache, "quality_gate_enabled": is_implementer,
              "resume_session": resume_session}
    if is_producing:
        contract = _inject_output_schema(repo, contract, info["schema"])
        kwargs["output_schema"] = str(org_root(repo) / info["schema"])   # schema lives in the org install
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
