"""Mainline_maintainer (layer 2, the Linus role) — review subsystem refs and integrate."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import tempfile

from .. import carrier

CAP = 5
MAINLINE_REF = "refs/heads/ai-org/mainline"

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


def review_and_integrate(subsystem_refs: list[str], repo: str | Path) -> str:
    """Review subsystem tree(s); on accept merge to mainline ref; reject/garbled -> "reject"."""
    repo = Path(repo)
    refs = [str(ref) for ref in subsystem_refs]
    if not refs:
        return "reject"

    prompt = (
        "You are the mainline maintainer. Review these subsystem refs as an integrated patch series:\n"
        + "\n".join(f"- {ref}" for ref in refs)
        + "\nAccept only if the series is coherent, correct, and ready for mainline. "
        "Return JSON {accept: bool, reasons: [..]}."
    )
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        schema = td_path / "verdict.schema.json"
        out = td_path / "verdict.json"
        schema.write_text(json.dumps(_VERDICT), encoding="utf-8")
        result = carrier.run_codex(repo, prompt, "read-only", out_file=out, output_schema=schema)
        try:
            verdict = json.loads(result.get("last_message") or "{}")
        except json.JSONDecodeError:
            return "reject"

    if not result.get("ok") or not verdict.get("accept"):
        return "reject"
    return MAINLINE_REF if _merge_refs(repo, refs) else "reject"


def _merge_refs(repo: Path, refs: list[str]) -> bool:
    temp_dir = Path(tempfile.mkdtemp(prefix="ai-org-mainline-"))
    worktree = temp_dir / "worktree"
    try:
        base = MAINLINE_REF if _ref_exists(repo, MAINLINE_REF) else "HEAD"
        _git(repo, "worktree", "add", "--detach", str(worktree), base)
        for ref in refs:
            result = subprocess.run(
                [
                    "git",
                    "-C",
                    str(worktree),
                    "-c",
                    "user.name=AI Org",
                    "-c",
                    "user.email=ai-org@example.invalid",
                    "merge",
                    "--no-edit",
                    ref,
                ],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            if result.returncode != 0:
                return False
        head = _git(worktree, "rev-parse", "HEAD").stdout.strip()
        _git(repo, "update-ref", MAINLINE_REF, head)
        return True
    finally:
        if worktree.exists():
            subprocess.run(
                ["git", "-C", str(repo), "worktree", "remove", "--force", str(worktree)],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        shutil.rmtree(temp_dir, ignore_errors=True)


def _ref_exists(repo: Path, ref: str) -> bool:
    result = subprocess.run(
        ["git", "-C", str(repo), "show-ref", "--verify", "--quiet", ref],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return result.returncode == 0


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
