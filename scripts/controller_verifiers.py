#!/usr/bin/env python3
"""Deterministic verifier normalization for the controller (ADR-0004 Phase 2).

The pack's gates speak different CLIs (residue scan, validate_pack, merge-gate, the linon packet
check, the stefan/measure instruments, profile-evidence). The workflow needs one shape. This
module runs each gate as a subprocess and returns a uniform `VerifierRun`
{name, status, exit_code, command, evidence_path}. status = pass | fail | error. Output is captured
to the run's evidence dir so the report is content-addressable, not a transient stream.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass, asdict
from pathlib import Path


@dataclass
class VerifierRun:
    name: str
    status: str          # pass | fail | error
    exit_code: int | None
    command: str
    evidence_path: str | None

    def to_dict(self) -> dict:
        return asdict(self)


def run_verifier(name: str, argv: list[str], *, cwd=None, evidence_dir=None,
                 timeout: int = 600) -> VerifierRun:
    try:
        cp = subprocess.run(argv, cwd=str(cwd) if cwd else None, capture_output=True,
                            text=True, timeout=timeout, stdin=subprocess.DEVNULL)
        out, code = (cp.stdout or "") + (cp.stderr or ""), cp.returncode
        status = "pass" if code == 0 else "fail"
    except subprocess.TimeoutExpired:
        out, code, status = "timeout", None, "error"
    except OSError as exc:
        out, code, status = f"launch error: {exc}", None, "error"
    evidence_path = None
    if evidence_dir:
        evidence_dir = Path(evidence_dir)
        evidence_dir.mkdir(parents=True, exist_ok=True)
        evidence_path = str(evidence_dir / f"verifier-{name}.log")
        Path(evidence_path).write_text(out, encoding="utf-8")
    return VerifierRun(name=name, status=status, exit_code=code,
                       command=" ".join(argv), evidence_path=evidence_path)


def run_all(specs: list[dict], *, evidence_dir=None, timeout: int = 600) -> list[VerifierRun]:
    """specs: [{name, argv, cwd?}]. Returns one VerifierRun each."""
    return [run_verifier(s["name"], s["argv"], cwd=s.get("cwd"),
                         evidence_dir=evidence_dir, timeout=timeout) for s in specs]


def builtin_gate_specs(repo) -> list[dict]:
    """The input-free deterministic gates that apply to any pack change."""
    repo = Path(repo)
    pkg = "packages/codex-org-bootstrap/src"
    import os
    env_py = os.environ.get("CONTROLLER_PYTHON", "python3")
    return [
        {"name": "residue", "argv": [env_py, "scripts/check-codex-private-residue.py", "--root", "."],
         "cwd": repo},
        {"name": "validate_pack",
         "argv": [env_py, f"{pkg}/ai_org_bootstrap/scripts/validate_pack.py", "--root", "."],
         "cwd": repo},
    ]


def all_passed(runs: list[VerifierRun]) -> bool:
    return all(r.status == "pass" for r in runs)


if __name__ == "__main__":
    import json
    with __import__("tempfile").TemporaryDirectory() as d:
        runs = run_all([
            {"name": "ok", "argv": ["python3", "-c", "import sys; sys.exit(0)"]},
            {"name": "bad", "argv": ["python3", "-c", "import sys; sys.exit(3)"]},
        ], evidence_dir=d)
        assert [r.status for r in runs] == ["pass", "fail"], runs
        assert runs[1].exit_code == 3
        assert not all_passed(runs)
        print("controller_verifiers smoke ok:", json.dumps([r.to_dict() for r in runs]))
