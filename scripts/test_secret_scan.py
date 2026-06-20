#!/usr/bin/env python3
"""Tests for ADR-0009 #2 secret scanning. The pure-Python fallback detector and the tiering/redaction logic
are pinned deterministically; a live gitleaks smoke runs only when gitleaks is installed. The last test
covers the pipeline wiring (streams; only CRITICAL hard-blocks; generic stays advisory; secret value never
rides the finding)."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "codex-org-bootstrap" / "src"))
import secret_scan as ss
import controller_pipeline as cp


def _tmpdir_with(files: dict) -> str:
    d = tempfile.mkdtemp(prefix="secret-scan-test-")
    for rel, content in files.items():
        p = Path(d) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return d


def test_fallback_detects_provider_tokens_as_critical():
    d = _tmpdir_with({
        "app.py": 'AWS = "AKIAIOSFODNN7EXAMPLE"\nGH = "ghp_0123456789abcdefghijklmnopqrstuvwxyzAB"\n',
        "key.pem": "-----BEGIN RSA PRIVATE KEY-----\nMIIE...\n-----END RSA PRIVATE KEY-----\n",
    })
    rep = ss.scan_dir(d, prefer_gitleaks=False)
    assert rep["backend"] == "fallback" and not rep["passed"], rep
    rules = {f["check"]: f["severity"] for f in rep["findings"]}
    assert rules.get("aws-access-token") == "critical", rules
    assert rules.get("github-pat") == "critical", rules
    assert rules.get("private-key") == "critical", rules
    print("ok  fallback flags AWS/GitHub/private-key as CRITICAL")


def test_fallback_generic_assignment_is_advisory():
    d = _tmpdir_with({"conf.py": 'password = "hunter2-not-really-a-prod-secret"\n'})
    rep = ss.scan_dir(d, prefer_gitleaks=False)
    sev = {f["severity"] for f in rep["findings"]}
    assert sev == {"minor"}, ("a generically-named assignment is advisory, not critical", rep["findings"])
    print("ok  generic secret-ish assignment -> minor (advisory, FP-prone)")


def test_finding_never_carries_the_secret_value():
    d = _tmpdir_with({"app.py": 'AWS = "AKIAIOSFODNN7EXAMPLE"\n'})
    rep = ss.scan_dir(d, prefer_gitleaks=False)
    blob = repr(rep["findings"])
    assert "AKIAIOSFODNN7EXAMPLE" not in blob, "the secret value must NEVER appear in a finding"
    assert any("secret_len" in f for f in rep["findings"]), "length is reported instead of the value"
    print("ok  findings redact the secret value (length only)")


def test_skips_agent_runs_and_git():
    d = _tmpdir_with({".agent-runs/x.json": 'k="AKIAIOSFODNN7EXAMPLE"', ".git/cfg": "AKIAIOSFODNN7EXAMPLE"})
    rep = ss.scan_dir(d, prefer_gitleaks=False)
    assert rep["passed"] and rep["findings"] == [], "must not scan .agent-runs/.git internals"
    print("ok  .agent-runs and .git are skipped")


def test_live_gitleaks_smoke():
    if not ss.gitleaks_available():
        print("skip  gitleaks not installed")
        return
    # a private-key block is the most reliable gitleaks trigger (no entropy gate, unlike a synthetic token).
    body = "MIIEpAIBAAKCAQEA7Yn8kF2pQ9zXvBcD3eF4gH5iJ6kL7mN8oP9qR0sT1uV2wX3yZ"
    d = _tmpdir_with({"id_rsa": f"-----BEGIN RSA PRIVATE KEY-----\n{body}\n-----END RSA PRIVATE KEY-----\n"})
    rep = ss.scan_dir(d, prefer_gitleaks=True)
    assert rep["backend"] == "gitleaks", rep
    assert not rep["passed"] and rep["findings"], "gitleaks must find the planted private key"
    assert any(f["severity"] == "critical" for f in rep["findings"]), "a private key is critical"
    assert body not in repr(rep["findings"]), "gitleaks path must also redact the value"
    print("ok  live gitleaks smoke: finds + tiers (critical) + redacts a planted private key")


def test_wired_secret_gate_only_critical_blocks():
    # the gate streams everything but only CRITICAL folds into the convergence loop in block mode.
    crit = {"source": "secret-scan", "check": "aws-access-token", "severity": "critical", "passed": False}
    adv = {"source": "secret-scan", "check": "generic-secret-assignment", "severity": "minor", "passed": False}
    report = {"applicable": True, "passed": False, "findings": [crit, adv]}
    assert cp._apply_secret_gate([], report, "shadow") == [], "shadow never blocks"
    folded = cp._apply_secret_gate([], report, "block")
    assert folded == [crit], "block folds ONLY the critical finding; advisory stays advisory"
    assert cp.SECRET_SCAN_MODE == "shadow", "secret scan defaults to shadow"
    print("ok  wired gate: shadow never blocks; block folds only critical (advisory stays advisory)")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
    print(f"\n{len(fns)} passed")
