"""Merge-stage subsystem maintainer (layer 1) — review a contribution + integrate into the subsystem tree.
STUB-level Codex wiring.

REVIEW ONLY (a reject routes back to the Contributor — the sole code-fixer). Thin wiring of the Codex
review call + a minimal integrate so the flow runs end-to-end. Enrich later: the reject->Contributor
revise loop (CAP), GitHub CI checks.
"""
from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

CAP = 5

_VERDICT = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": ["accept", "reasons"],
    "properties": {
        "accept": {"type": "boolean"},
        "reasons": {"type": "array", "items": {"type": "string"}},
    },
}


def review_and_integrate(branch: str, repo: str | Path) -> str:
    """Review the branch; on accept integrate -> subsystem ref; reject/garbled -> "reject" (fail closed)."""
    repo = Path(repo)
    prompt = (
        f"You are the subsystem maintainer. Review the contribution on branch {branch} "
        f"(read `git diff <base>..{branch}`). Accept only if it is correct and stays in scope. "
        "Return JSON {accept: bool, reasons: [..]}."
    )
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        schema = td / "verdict.schema.json"
        out = td / "verdict.json"
        schema.write_text(json.dumps(_VERDICT), encoding="utf-8")
        cmd = ["codex", "exec", "--sandbox", "read-only", "-C", str(repo), "-o", str(out)]
        if schema is not None:
            cmd += ["--output-schema", str(schema)]
        cmd.append(prompt)
        completed = subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
        )
        result_text = Path(out).read_text(encoding="utf-8")
        try:
            verdict = json.loads(result_text or "{}")
        except json.JSONDecodeError:
            return "reject"
    if completed.returncode != 0 or not verdict.get("accept"):
        return "reject"  # fail closed
    # STUB-level integrate: point a subsystem ref at the accepted branch (enrich -> real cherry-pick/merge).
    ref = f"refs/heads/ai-org/subsystem/{branch.rstrip('/').split('/')[-1]}"
    subprocess.run(["git", "-C", str(repo), "update-ref", ref, branch], check=False)
    return ref
