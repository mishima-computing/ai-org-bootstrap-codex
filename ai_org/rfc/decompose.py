"""decompose — materialize the converged RFC into Contributor-sized Tasks. STUB-level Codex wiring.

The RFC owns the split; decompose only INSTANTIATES it into concrete Tasks. This is a thin wiring of the
Codex call + a minimal parse so the flow runs end-to-end. Enrich later: fail-closed parsing, recurse on
oversize, depends_on validation/ordering.
"""
from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

from ..platform import carrier
from .receive import RFC
from .task import Task

_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": ["tasks"],
    "properties": {
        "tasks": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "objective"],
                "properties": {
                    "id": {"type": "string"},
                    "objective": {"type": "string"},
                    "contract": {"type": "string"},
                    "scope": {"type": "array", "items": {"type": "string"}},
                    "depends_on": {"type": "array", "items": {"type": "string"}},
                    "checks": {"type": "array", "items": {"type": "string"}},
                },
            },
        }
    },
}


def decompose(rfc: RFC, repo: str | Path) -> list[Task]:
    """Materialize the RFC's split into Tasks (flat baseline). Each Task branches off the repo HEAD."""
    repo = Path(repo)
    head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True
    ).stdout.strip()
    prompt = (
        "Materialize this APPROVED RFC's split into contributor-sized tasks. Follow the RFC; do not "
        "re-decide the split. Keep tasks independent unless the RFC states a real dependency. For each "
        "task give: id, objective, contract (interface to satisfy), scope (files/symbols it may touch), "
        "depends_on (sibling ids), checks (shell commands for self-check).\n"
        f"title: {rfc.title}\nproblem: {rfc.problem}\nproposed_change: {rfc.proposed_change}\n"
        f"interface_sketch: {rfc.interface_sketch}\n"
    )
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        schema = td / "decompose.schema.json"
        out = td / "tasks.json"
        schema.write_text(json.dumps(_SCHEMA), encoding="utf-8")
        result = carrier.run_codex(repo, prompt, "read-only", out_file=out, output_schema=schema)
        data = json.loads(result.get("last_message") or "{}")  # STUB-level: minimal parse
    return [
        Task(
            id=t["id"],
            objective=t["objective"],
            contract=t.get("contract", ""),
            base_sha=head,
            scope=t.get("scope", []),
            checks=t.get("checks", []),
            depends_on=t.get("depends_on", []),
        )
        for t in data.get("tasks", [])
    ]
