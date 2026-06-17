#!/usr/bin/env python3
"""Deterministic verifier normalization for the controller (ADR-0004 Phase 2).

The pack's gates speak different CLIs (residue scan, validate_pack, merge-gate, the linon packet
check, the stefan/measure instruments, profile-evidence). The workflow needs one shape. This
module runs each gate as a subprocess and returns a uniform `VerifierRun`
{name, status, exit_code, command, evidence_path}. status = pass | fail | error. Output is captured
to the run's evidence dir so the report is content-addressable, not a transient stream.
"""
from __future__ import annotations

import os
import subprocess
import sys
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
                 timeout: int = 600, env=None) -> VerifierRun:
    # The controller is responsible for handing the settled environment to its verifier subprocesses:
    # a gate that imports the pack package (validate_pack) needs the package on PYTHONPATH, otherwise it
    # fails with ModuleNotFoundError even though the pack is valid. `env` (merged over os.environ, never
    # replacing it) carries that. ADR-0005: a settled fact must travel to the subprocess, not be re-derived.
    proc_env = {**os.environ, **env} if env else None
    try:
        cp = subprocess.run(argv, cwd=str(cwd) if cwd else None, capture_output=True,
                            text=True, encoding="utf-8", errors="replace", timeout=timeout, stdin=subprocess.DEVNULL, env=proc_env)
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
    """specs: [{name, argv, cwd?, env?}]. Returns one VerifierRun each."""
    return [run_verifier(s["name"], s["argv"], cwd=s.get("cwd"), env=s.get("env"),
                         evidence_dir=evidence_dir, timeout=timeout) for s in specs]


def builtin_gate_specs(repo) -> list[dict]:
    """The input-free deterministic gates that apply to any pack change.

    These gates validate the ORG INSTALL's integrity (its scripts live there: the residue scan and
    validate_pack), so they resolve against AI_ORG_ROOT — the org install — not the workspace. When
    AI_ORG_ROOT is unset (self-hosted, org operates on itself) org_root == repo, so behaviour is
    identical; when the org runs on an EXTERNAL --repo (cross-repo), the gates still find their own
    scripts in the install instead of failing to open them in the workspace worktree."""
    repo = Path(repo)
    env = os.environ.get("AI_ORG_ROOT")
    base = Path(env).expanduser().resolve() if env else repo     # the org install (where the gate scripts are)
    pkg = "packages/codex-org-bootstrap/src"
    # the running interpreter, not an env-overridable name — an env var must not be able to point the
    # mandatory gates at /bin/true and turn them into no-ops (NN2/NN3).
    py = sys.executable or "python3"
    # hand the pack package to the gates on PYTHONPATH (absolute, so it holds regardless of cwd) — the
    # gates run as plain files, so `import ai_org_bootstrap` needs `src/` on the path, not the script dir.
    gate_env = {"PYTHONPATH": str((base / pkg).resolve())}
    return [
        {"name": "residue",
         "argv": [py, str(base / "scripts/check-codex-private-residue.py"), "--root", str(base)],
         "cwd": base, "env": gate_env},
        {"name": "validate_pack",
         "argv": [py, str(base / pkg / "ai_org_bootstrap/scripts/validate_pack.py"), "--root", str(base)],
         "cwd": base, "env": gate_env},
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
