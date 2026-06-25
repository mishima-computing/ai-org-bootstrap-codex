#!/usr/bin/env python3
"""Tests for controller_pipeline registry gating."""
from __future__ import annotations

import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "codex-org-bootstrap" / "src"))
import controller_pipeline as cp

REPO = str(Path(__file__).resolve().parents[1])


@contextmanager
def patched_env(**updates):
    old = {name: os.environ.get(name) for name in updates}
    try:
        for name, value in updates.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        yield
    finally:
        for name, value in old.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def test_ci_writers_are_gated_out_of_active_entries_by_default():
    with patched_env(STEFAN_ENABLED=None, CI_WRITERS_ENABLED=None):
        entries = cp._active_entries(cp._entries(REPO))
    assert not (cp.CI_WRITER_ROLES & set(entries)), ("CI writers default OFF", sorted(entries))
    assert cp.STEFAN_ROLE not in entries, "Stefan remains default OFF"
    assert cp._predecessors(entries)[cp.AUFHEBEN_ROLE] == [
        "aggressive-designer",
        "conservative-designer",
        "genius",
    ], "aufheben should depend only on the three designers by default"

    with patched_env(STEFAN_ENABLED=None, CI_WRITERS_ENABLED="1"):
        enabled = cp._active_entries(cp._entries(REPO))
    assert cp.CI_WRITER_ROLES <= set(enabled), ("CI writers opt back in", sorted(enabled))
    assert cp.STEFAN_ROLE not in enabled, "the CI-writer gate must not alter Stefan's gate"
    print("ok  CI writers default OFF in active entries; CI_WRITERS_ENABLED opts them back in")


def test_pipeline_runs_designer_dialectic_without_ci_writers():
    calls = []
    tmp = Path(tempfile.mkdtemp(prefix="cp-no-ci-"))

    def fake_execute(repo, role, entry, objective, inputs, stage_run_id, cache, **kwargs):
        calls.append((role, sorted(inputs)))
        result = {"role_id": role}
        if role == cp.AUFHEBEN_ROLE:
            assert sorted(inputs) == ["aggressive-designer", "conservative-designer", "genius"], inputs
            result = {
                "role_id": cp.AUFHEBEN_ROLE,
                "files_allowed_to_change": ["demo.txt"],
                "files_not_allowed_to_change": [],
            }
        elif role == "linon":
            result = {"role_id": "linon", "findings": []}
        return True, result, {"ok": True, "attempts": [], "changed_files": []}, {
            "role": role,
            "run_id": stage_run_id,
            "ok": True,
        }

    old_execute = cp._execute_stage
    old_preflight = cp._preflight_gate
    old_cheap = cp._cheap_gate_findings
    try:
        cp._execute_stage = fake_execute
        cp._preflight_gate = lambda *args, **kwargs: (None, False)
        cp._cheap_gate_findings = lambda *args, **kwargs: ([], {}, None)
        with patched_env(AI_ORG_ROOT=REPO, STREAM_LOG=None, STEFAN_ENABLED=None, CI_WRITERS_ENABLED=None):
            result = cp.run_pipeline(tmp, "demo objective", "r-no-ci", cache=False, max_parallel=1)
    finally:
        cp._execute_stage = old_execute
        cp._preflight_gate = old_preflight
        cp._cheap_gate_findings = old_cheap

    called_roles = [role for role, _inputs in calls]
    assert not (cp.CI_WRITER_ROLES & set(called_roles)), ("CI writers must not run by default", called_roles)
    assert called_roles == [
        "aggressive-designer",
        "conservative-designer",
        "genius",
        cp.AUFHEBEN_ROLE,
        "implementer",
        "linon",
    ], called_roles
    assert result["converged"], result
    print("ok  default pipeline runs designers -> aufheben -> implementer -> linon without CI writers")


def test_infra_gate_result_is_unverified_not_clean_green():
    calls = []
    tmp = Path(tempfile.mkdtemp(prefix="cp-infra-unverified-"))
    infra_finding = {
        "source": "regression",
        "check": "regression_suite",
        "severity": "major",
        "passed": False,
        "detail": "pytest runner could not start",
        "failure_classification": "infra",
    }

    def fake_execute(repo, role, entry, objective, inputs, stage_run_id, cache, **kwargs):
        calls.append(role)
        result = {"role_id": role}
        if role == cp.AUFHEBEN_ROLE:
            result = {
                "role_id": cp.AUFHEBEN_ROLE,
                "files_allowed_to_change": ["demo.txt"],
                "files_not_allowed_to_change": [],
            }
        elif role == "linon":
            result = {"role_id": "linon", "findings": []}
        return True, result, {"ok": True, "attempts": [], "changed_files": []}, {
            "role": role,
            "run_id": stage_run_id,
            "ok": True,
        }

    old_execute = cp._execute_stage
    old_preflight = cp._preflight_gate
    old_cheap = cp._cheap_gate_findings
    try:
        cp._execute_stage = fake_execute
        cp._preflight_gate = lambda *args, **kwargs: (None, False)
        cp._cheap_gate_findings = lambda *args, **kwargs: (
            [], {"conformance": [infra_finding]}, [])
        with patched_env(AI_ORG_ROOT=REPO, STREAM_LOG=None, STEFAN_ENABLED=None, CI_WRITERS_ENABLED=None):
            result = cp.run_pipeline(tmp, "demo objective", "r-infra-unverified", cache=False, max_parallel=1)
    finally:
        cp._execute_stage = old_execute
        cp._preflight_gate = old_preflight
        cp._cheap_gate_findings = old_cheap

    assert result["converged"], result
    assert result["verification_status"] == "unverified", result
    assert result["unverified_gate_findings"] == {"conformance": [infra_finding]}, result
    print("ok  infra gate finding leaves pipeline result unverified, not clean green")


def test_infra_conformance_finding_streams_but_does_not_block_gate_behind():
    infra = {
        "applicable": True,
        "passed": False,
        "findings": [{
            "source": "regression",
            "check": "regression_suite",
            "severity": "major",
            "passed": False,
            "detail": "python3 -m pytest could not run",
            "failure_classification": "infra",
        }],
        "checks_run": 1,
    }
    code = {
        "applicable": True,
        "passed": False,
        "findings": [{
            "source": "regression",
            "check": "regression_suite",
            "severity": "major",
            "passed": False,
            "detail": "real test failed",
            "failure_classification": "code",
        }],
        "checks_run": 1,
    }

    assert cp._apply_conformance_gate([], infra, "block") == [], "infra must not fold into repair findings"
    assert cp._apply_conformance_gate([], code, "block") == code["findings"], "code defects still block"

    events = []
    saved = (cp._shadow_conformance, cp._secret_scan, cp._fuzz_cli, cp._stream_append, cp.CONFORMANCE_GATE_MODE)
    cp._shadow_conformance = lambda repo, results, run_id: infra
    cp._secret_scan = lambda repo, run_id: None
    cp._fuzz_cli = lambda repo, results, run_id: None
    cp._stream_append = lambda repo, event: events.append(event)
    try:
        cp.CONFORMANCE_GATE_MODE = "block"
        findings, gate_ctx, blocked_by = cp._cheap_gate_findings("/tmp/leaf", {"linon": {"findings": []}}, "run-infra", None)
    finally:
        cp._shadow_conformance, cp._secret_scan, cp._fuzz_cli, cp._stream_append, cp.CONFORMANCE_GATE_MODE = saved

    assert findings == [] and blocked_by == [], (findings, blocked_by)
    assert gate_ctx["conformance"] == infra["findings"], gate_ctx
    print("ok  infra conformance findings are advisory in gate-behind; code findings still block")


def test_shadow_conformance_streams_infra_finding_event():
    tmp = Path(tempfile.mkdtemp(prefix="cp-infra-stream-"))
    cmd = "python3 -m pytest -q"
    results = {"aufheben-designer": {
        "role_id": "aufheben-designer",
        "deliverable_kind": "none",
        "regression_suite": {"command": cmd},
    }}
    runner = lambda c, *, cwd=None, timeout=None: cp.conformance.RunResult(127, "", "No module named pytest\n")

    events = []
    saved = (cp._stream_append, cp.CONFORMANCE_GATE_MODE)
    cp._stream_append = lambda repo, event: events.append(event)
    try:
        cp.CONFORMANCE_GATE_MODE = "block"
        report = cp._shadow_conformance(tmp, results, "run-stream", runner=runner)
    finally:
        cp._stream_append, cp.CONFORMANCE_GATE_MODE = saved

    assert report and report["findings"][0]["failure_classification"] == "infra", report
    infra_events = [e for e in events if e.get("type") == "infra_finding"]
    assert infra_events and infra_events[0]["failure_classification"] == "infra", events
    assert cp._apply_conformance_gate([], report, "block") == [], report
    print("ok  conformance streams infra_finding events and keeps them out of block-mode repair")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
    print(f"\n{len(fns)} passed")
