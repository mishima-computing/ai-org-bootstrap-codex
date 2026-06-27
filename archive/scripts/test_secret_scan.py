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


def test_secret_inside_an_archive_is_caught_and_marked():
    # a secret packaged into the built artifact (a .zip/.whl) must be caught, not only loose source — with
    # the finding marked <archive>!<inner> + in_archive, and the value still redacted.
    import zipfile
    d = _tmpdir_with({"readme.txt": "nothing here"})
    secret_file = Path(d) / "id_rsa"
    secret_file.write_text("-----BEGIN RSA PRIVATE KEY-----\nMIIEdeadbeef\n-----END RSA PRIVATE KEY-----\n")
    with zipfile.ZipFile(Path(d) / "dist.whl", "w") as zf:
        zf.write(secret_file, "pkg/id_rsa")
    secret_file.unlink()                                       # only the packaged copy remains
    rep = ss.scan_dir(d, prefer_gitleaks=False)
    arch = [f for f in rep["findings"] if f.get("in_archive")]
    assert arch, ("a secret inside the .whl must be found", rep["findings"])
    assert arch[0]["file"].startswith("dist.whl!") and "id_rsa" in arch[0]["file"], arch[0]
    assert arch[0]["severity"] == "critical", arch[0]
    assert "deadbeef" not in repr(rep["findings"]), "archive findings must redact the value too"
    print("ok  secret inside an archive (.whl) is caught, marked <archive>!<inner>, redacted")


def test_archive_scan_is_bounded_and_failsoft():
    # a non-archive .zip-named junk file or a missing path must not raise; include_archives=False skips them.
    d = _tmpdir_with({"not-really.zip": "this is not a zip"})
    rep = ss.scan_dir(d, prefer_gitleaks=False)               # must not raise on the bogus archive
    assert rep["applicable"], rep
    rep2 = ss.scan_dir(d, prefer_gitleaks=False, include_archives=False)
    assert rep2["applicable"], rep2
    print("ok  archive scan is fail-soft on a bogus archive; include_archives=False skips")


def test_secret_scan_is_leaf_scoped():
    # P0 leaf-scoped: a secret in a file the leaf did NOT change is a pre-existing fixture, not the leaf's
    # finding. Only changed-file findings survive; a file-less finding (scanner_error) is always kept.
    report = {"applicable": True, "passed": False, "backend": "fallback", "findings": [
        {"source": "secret-scan", "check": "aws-access-token", "severity": "critical", "passed": False,
         "file": "fixtures/old.py"},                            # pre-existing, NOT in the leaf's changes
        {"source": "secret-scan", "check": "github-pat", "severity": "critical", "passed": False,
         "file": "new.py"},                                     # the leaf's changed file
        {"source": "secret-scan", "check": "scanner_error", "severity": "critical", "passed": False}]}
    saved = (ss.scan_dir, cp._changed_files, cp._stream_append, cp.SECRET_SCAN_MODE)
    cp.secret_scan.scan_dir = lambda path: report
    cp._changed_files = lambda repo: {"new.py"}
    cp._stream_append = lambda repo, ev: None
    try:
        cp.SECRET_SCAN_MODE = "shadow"
        rep = cp._secret_scan("/tmp/x", "r")
    finally:
        cp.secret_scan.scan_dir, cp._changed_files, cp._stream_append, cp.SECRET_SCAN_MODE = saved
    files = {f.get("file") for f in rep["findings"]}
    assert "fixtures/old.py" not in files, "a pre-existing secret outside the leaf is not the leaf's finding"
    assert "new.py" in files, "the leaf's changed-file secret is kept"
    assert any(f["check"] == "scanner_error" for f in rep["findings"]), "a file-less finding is always kept"
    print("ok  secret scan leaf-scoped: pre-existing dropped, changed-file + scanner_error kept")


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


def test_scanner_error_surfaces_as_critical_not_clean():
    # P0 fail-closed: a gitleaks/scan FAILURE must surface as a critical scanner_error, never silently read as
    # clean (the external review's example). With gitleaks present, an exploding runner triggers it.
    if not ss.gitleaks_available():
        print("skip  gitleaks not installed (the gitleaks-backend error path is the one under test)")
        return
    d = _tmpdir_with({"app.py": "x = 1\n"})
    def boom(report_path):
        raise RuntimeError("gitleaks exploded")
    rep = ss.scan_dir(d, prefer_gitleaks=True, run=boom)
    assert rep["applicable"] and rep["passed"] is False and rep.get("error"), rep
    assert rep["findings"][0]["check"] == "scanner_error" and rep["findings"][0]["severity"] == "critical", rep
    print("ok  a scanner failure surfaces a critical scanner_error (not silent clean / fail-open)")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
    print(f"\n{len(fns)} passed")
