"""Small RFC-local helper for running Codex with a JSON output schema."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Any

import ai_org.log as org_log


def run_json(
    repo: Path,
    *,
    schema: dict[str, Any],
    prompt: str,
    schema_filename: str,
    output_filename: str,
    failure_label: str,
    ctx: org_log.RunContext | None = None,
) -> dict[str, Any]:
    """Run codex exec and return the raw JSON output text or a closed failure."""
    temp_dir = Path(tempfile.mkdtemp(prefix="ai-org-rfc-codex-"))
    schema_file = temp_dir / schema_filename
    out_file = temp_dir / output_filename
    try:
        schema_file.write_text(json.dumps(schema, indent=2), encoding="utf-8")
        cmd = [
            "codex",
            "exec",
            "--sandbox",
            "read-only",
            "-C",
            str(repo),
            "-o",
            str(out_file),
            "--output-schema",
            str(schema_file),
            prompt,
        ]
        log_ctx = ctx or org_log.RunContext(repo=repo, stage="codex_exec")
        try:
            completed = org_log.logged_subprocess(
                cmd,
                ctx=log_ctx,
                capture_policy="head_tail",
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
            )
        except OSError as exc:
            return {"ok": False, "error": f"{failure_label} failed: {exc}"}
        if completed.returncode != 0:
            detail = completed.stderr.strip() or (
                "no output file" if not out_file.exists() else f"{failure_label} did not complete successfully."
            )
            return {"ok": False, "error": f"{failure_label} failed: {detail}"}
        if not out_file.exists():
            return {"ok": False, "error": f"{failure_label} failed: no output file"}
        return {"ok": True, "raw": out_file.read_text(encoding="utf-8")}
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
