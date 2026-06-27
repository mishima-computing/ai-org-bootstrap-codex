#!/usr/bin/env python3
"""Tests for the isolated implementer probe harness."""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import implementer_probe  # noqa: E402


def _git(repo: Path, *args: str) -> None:
    cp = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if cp.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {cp.stderr}")


def _make_repo(tmp: Path) -> Path:
    repo = tmp / "repo"
    repo.mkdir()
    (repo / "target.py").write_text(
        "def old_name():\n"
        "    return 'old'\n"
        "\n"
        "VALUE = old_name()\n",
        encoding="utf-8",
    )
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


def _contract() -> dict:
    return {
        "role_id": "aufheben-designer",
        "contract_id": "probe-synthetic",
        "objective": "rename old_name to new_name in target.py",
        "acceptance_criteria": ["target.py no longer contains old_name"],
        "files_allowed_to_change": ["target.py"],
        "files_not_allowed_to_change": [],
        "required_checks": [],
        "deliverable_kind": "none",
        "forbidden_patterns": [{
            "pattern": "old_name",
            "scope": "leaf",
            "max_occurrences": 0,
            "reason": "synthetic rename straggler",
        }],
    }


def test_probe_runs_end_to_end_with_stub_carrier_and_reports_structure():
    with tempfile.TemporaryDirectory() as d:
        repo = _make_repo(Path(d))

        def carrier(rp, prompt, sandbox, *, timeout=600, retries=0, out_dir=None, resume_session=None):
            path = Path(rp) / "target.py"
            path.write_text(path.read_text(encoding="utf-8").replace("old_name", "new_name"), encoding="utf-8")
            return {"ok": True, "attempts": [{"attempt": 0, "ok": True}], "session_id": "stub-session"}

        report = implementer_probe.run_probe(repo, _contract(), carrier=carrier)
        rendered = implementer_probe.render_report(report)

    assert report["verdict"] == "carrier COMPLETED", report
    assert report["converged"] is True, report
    assert report["remaining_findings"] == [], report
    assert report["diff_summary"]["changed_files"] == ["target.py"], report["diff_summary"]
    assert report["forbidden_pattern_progress"] == [{
        "pattern": "old_name",
        "scope": "leaf",
        "max_occurrences": 0,
        "before_count": 2,
        "after_count": 0,
        "delta": -2,
    }]
    assert "IMPLEMENTER PROBE REPORT" in rendered
    assert "carrier COMPLETED" in rendered
    assert "before=2 after=0" in rendered


def _make_repo_with_out_of_scope_token(tmp: Path) -> Path:
    """Repo where the forbidden token lives BOTH in-scope (target.py) and out-of-scope (other.py)."""
    repo = tmp / "repo"
    repo.mkdir()
    (repo / "target.py").write_text("STRAGGLER = 1\n", encoding="utf-8")          # in scope
    (repo / "other.py").write_text("STRAGGLER = 2\n", encoding="utf-8")           # out of scope, kept
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


def _scoped_contract() -> dict:
    return {
        "role_id": "aufheben-designer",
        "contract_id": "probe-out-of-scope",
        "objective": "remove STRAGGLER from target.py",
        "acceptance_criteria": ["target.py no longer contains STRAGGLER"],
        "files_allowed_to_change": ["target.py"],   # only target.py is in scope
        "files_not_allowed_to_change": [],
        "required_checks": [],
        "deliverable_kind": "none",
        # a regression suite that always fails -> after_conformance.passed is False, which is the precondition
        # for the verdict's STUCK branch. Without the fix, an out-of-scope (advisory) occurrence inflates
        # after_count and the leaf is mislabeled "STUCK (zero progress)".
        "regression_suite": {"command": "false"},
        "forbidden_patterns": [{
            "pattern": "STRAGGLER",
            "scope": "leaf",
            "max_occurrences": 0,
            "reason": "synthetic straggler",
        }],
    }


def test_after_count_reflects_carrier_edit_not_out_of_scope_advisory():
    """Regression: the probe's AFTER count + verdict must match the gate's BLOCKING forbidden state on the
    carrier's actual output, not the advisory (out-of-scope) tally.

    The carrier removes the in-scope occurrence; an out-of-scope occurrence (in other.py) survives. A direct
    run_forbidden_patterns on the carrier's output reports ZERO blocking findings (passed=True). Before the fix,
    _count_by_pattern summed advisory findings, so after_count stayed at the before value (delta=0) and the
    verdict was a FALSE "carrier STUCK (zero progress)".
    """
    import conformance  # noqa: PLC0415 - local import keeps the module list at the top minimal

    with tempfile.TemporaryDirectory() as d:
        repo = _make_repo_with_out_of_scope_token(Path(d))
        contract = _scoped_contract()

        def carrier(rp, prompt, sandbox, *, timeout=600, retries=0, out_dir=None, resume_session=None):
            path = Path(rp) / "target.py"
            path.write_text(path.read_text(encoding="utf-8").replace("STRAGGLER", "RENAMED"), encoding="utf-8")
            return {"ok": True, "attempts": [{"attempt": 0, "ok": True}], "session_id": "stub-session"}

        report = implementer_probe.run_probe(repo, contract, carrier=carrier, keep_scratch=True)
        scratch = report["scratch_repo"]
        # The probe's measurement must equal a direct gate run on the carrier's preserved output.
        direct = conformance.run_forbidden_patterns(contract, cwd=scratch)

    assert direct["passed"] is True, direct
    assert [f for f in direct["findings"] if not f.get("passed")] == [], direct

    rows = report["forbidden_pattern_progress"]
    assert rows == [{
        "pattern": "STRAGGLER",
        "scope": "leaf",
        "max_occurrences": 0,
        "before_count": 1,   # the in-scope occurrence the carrier was asked to remove
        "after_count": 0,    # MUST reflect the carrier's edit, not the surviving out-of-scope advisory hit
        "delta": -1,
    }], rows
    # The forbidden check passed on the carrier's output, so the verdict must NOT be the zero-progress STUCK.
    assert report["verdict"] != "carrier STUCK (zero progress)", report
