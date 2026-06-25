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
    old_rerun = cp._rerun_dimension
    try:
        cp._execute_stage = fake_execute
        cp._preflight_gate = lambda *args, **kwargs: (None, False)
        cp._cheap_gate_findings = lambda *args, **kwargs: (
            [], {"conformance": [infra_finding]}, [])
        # incr #2: the clean-retry now runs before the verdict — make it REPRODUCE the infra (the could-not-run
        # is not a one-shot transient here), so the dimension stays terminally unverified as #1 intends.
        cp._rerun_dimension = lambda repo, dimension, results, run_id: {
            "applicable": True, "passed": False, "findings": [infra_finding], "checks_run": 1}
        with patched_env(AI_ORG_ROOT=REPO, STREAM_LOG=None, STEFAN_ENABLED=None, CI_WRITERS_ENABLED=None):
            result = cp.run_pipeline(tmp, "demo objective", "r-infra-unverified", cache=False, max_parallel=1)
    finally:
        cp._execute_stage = old_execute
        cp._preflight_gate = old_preflight
        cp._cheap_gate_findings = old_cheap
        cp._rerun_dimension = old_rerun

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


# ── incr #2: bounded autonomous clean-retry of an unverified dimension ──────────────────────────────────────

def _fake_clean_execute(repo, role, entry, objective, inputs, stage_run_id, cache, **kwargs):
    """A minimal happy-path stage runner: aufheben emits a write scope, linon is clean, everything else is OK.
    Convergence is then decided purely by the (patched) gate findings + clean-retry, which is what incr #2 tests."""
    result = {"role_id": role}
    if role == cp.AUFHEBEN_ROLE:
        result = {"role_id": cp.AUFHEBEN_ROLE, "files_allowed_to_change": ["demo.txt"],
                  "files_not_allowed_to_change": []}
    elif role == "linon":
        result = {"role_id": "linon", "findings": []}
    return True, result, {"ok": True, "attempts": [], "changed_files": []}, {
        "role": role, "run_id": stage_run_id, "ok": True}


def _infra_finding(detail="pytest runner could not start"):
    return {"source": "regression", "check": "regression_suite", "severity": "major", "passed": False,
            "detail": detail, "failure_classification": "infra", "returncode": 127,
            "stderr_tail": detail}


def _code_finding(detail="real test failed"):
    return {"source": "regression", "check": "regression_suite", "severity": "major", "passed": False,
            "detail": detail, "failure_classification": "code", "returncode": 1, "stderr_tail": detail}


def _clean_report():
    return {"applicable": True, "passed": True, "findings": [], "checks_run": 1}


def _infra_report(detail="pytest runner could not start"):
    return {"applicable": True, "passed": False, "findings": [_infra_finding(detail)], "checks_run": 1}


def _run_with_clean_retry(*, gate_ctx, rerun, max_repair_iterations=3, run_id="r-incr2"):
    """Drive run_pipeline with the stage runner + cheap gates faked, and the clean-retry's per-dimension re-run
    seam (cp._rerun_dimension) scripted. Returns (result, rerun_calls)."""
    tmp = Path(tempfile.mkdtemp(prefix="cp-clean-retry-"))
    calls = []

    def counted_rerun(repo, dimension, results, run_id):
        calls.append(dimension)
        return rerun(dimension, len([d for d in calls if d == dimension]))

    # cheap gates return NO blocking findings (so linon runs and the loop converges) but a non-clean gate_ctx,
    # i.e. exactly the "converged but a dimension could not be proven green" state the retry exists to resolve.
    blocking = [f for fs in gate_ctx.values() for f in fs if cp._finding_blocks_convergence(f)]
    blocked_by = sorted({f.get("source", "conformance") for f in blocking}) if blocking else []
    saved = (cp._execute_stage, cp._preflight_gate, cp._cheap_gate_findings, cp._rerun_dimension)
    try:
        cp._execute_stage = _fake_clean_execute
        cp._preflight_gate = lambda *a, **k: (None, False)
        cp._cheap_gate_findings = lambda *a, **k: (list(blocking), gate_ctx, list(blocked_by))
        cp._rerun_dimension = counted_rerun
        with patched_env(AI_ORG_ROOT=REPO, STREAM_LOG=None, STEFAN_ENABLED=None, CI_WRITERS_ENABLED=None):
            result = cp.run_pipeline(tmp, "demo objective", run_id, cache=False,
                                     max_parallel=1, max_repair_iterations=max_repair_iterations)
    finally:
        (cp._execute_stage, cp._preflight_gate, cp._cheap_gate_findings, cp._rerun_dimension) = saved
    return result, calls


def test_clean_retry_clears_one_shot_infra_then_verified():
    # the dimension fails infra ONCE, then a clean retry succeeds -> VERIFIED, the leaf proceeds.
    result, calls = _run_with_clean_retry(
        gate_ctx={"conformance": [_infra_finding()]},
        rerun=lambda dimension, attempt: _clean_report())
    assert calls == ["conformance"], ("retried exactly the unverified dimension, once", calls)
    assert result["converged"], result
    assert result["verification_status"] == "verified", result
    assert result["unverified_gate_findings"] == {}, result
    print("ok  a one-shot infra failure that clears on a clean retry ends VERIFIED")


def test_clean_retry_reproduced_infra_stays_unverified_and_is_bounded():
    # the dimension fails infra on EVERY attempt -> terminally UNVERIFIED; bounded to the DEFAULT single retry.
    result, calls = _run_with_clean_retry(
        gate_ctx={"conformance": [_infra_finding()]},
        rerun=lambda dimension, attempt: _infra_report())
    assert calls == ["conformance"], ("default cap is ONE retry (one re-run)", calls)
    assert len(calls) == cp.CLEAN_RETRY_DEFAULT_CAP, (calls, cp.CLEAN_RETRY_DEFAULT_CAP)
    assert result["converged"], result   # converged on linon, but...
    assert result["verification_status"] == "unverified", result   # ...fail-closed: not proven green
    assert result["unverified_gate_findings"], result
    print("ok  infra reproduced on every clean retry stays terminally UNVERIFIED, bounded to 1 retry")


def test_clean_retry_known_transient_infra_gets_two_attempts():
    # a KNOWN-transient infra class (DNS resolution) earns the larger budget: 2 retries, still bounded.
    transient = _infra_finding("could not resolve host registry.example.com")
    result, calls = _run_with_clean_retry(
        gate_ctx={"conformance": [transient]},
        rerun=lambda dimension, attempt: {"applicable": True, "passed": False,
                                          "findings": [transient], "checks_run": 1})
    assert len(calls) == cp.CLEAN_RETRY_TRANSIENT_CAP == 2, (calls, cp.CLEAN_RETRY_TRANSIENT_CAP)
    assert result["verification_status"] == "unverified", result
    print("ok  a known-transient infra class gets 2 bounded retries; still fail-closed when it never clears")


def test_clean_retry_revealing_code_blocks_as_normal():
    # if a retry reveals a real CODE/product defect, it is classified code and BLOCKS (never papered over).
    result, calls = _run_with_clean_retry(
        gate_ctx={"conformance": [_infra_finding()]},
        rerun=lambda dimension, attempt: {"applicable": True, "passed": False,
                                          "findings": [_code_finding()], "checks_run": 1})
    assert calls == ["conformance"], calls
    assert not result["converged"], result          # the code finding folded into blocking findings
    assert result["verification_status"] == "failed", result
    print("ok  a clean retry that reveals a real CODE defect blocks (fail-closed), not advisory infra")


def test_code_failure_is_not_clean_retried_and_drives_repair():
    # a CODE gate finding never enters the retry path (it is not a could-not-run): it blocks + drives repair.
    result, calls = _run_with_clean_retry(
        gate_ctx={"conformance": [_code_finding()]},
        rerun=lambda dimension, attempt: _clean_report(),   # would CLEAR if (wrongly) retried
        max_repair_iterations=1)
    assert calls == [], ("a code failure must NOT be clean-retried as infra", calls)
    assert not result["converged"], result
    assert result["verification_status"] == "failed", result
    assert result["repair_iterations"] >= 1, ("a code finding drives the repair loop", result)
    print("ok  a code failure is not clean-retried; it blocks and drives repair")


def test_is_transient_infra_and_retry_verdict_classifiers():
    assert cp._is_transient_infra([_infra_finding("ImagePullBackOff pulling image")])
    assert cp._is_transient_infra([_infra_finding("Address already in use")])
    assert cp._is_transient_infra([_infra_finding("connection refused")])
    assert not cp._is_transient_infra([_infra_finding("pytest runner could not start")])
    assert cp._retry_verdict(_clean_report()) == "cleared"
    assert cp._retry_verdict({"applicable": False, "passed": True, "findings": []}) == "cleared"
    assert cp._retry_verdict(_infra_report()) == "reproduced"
    assert cp._retry_verdict({"applicable": True, "passed": False,
                              "findings": [_code_finding()]}) == "code"
    print("ok  transient-infra + retry-verdict classifiers behave as specified")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
    print(f"\n{len(fns)} passed")
