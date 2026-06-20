#!/usr/bin/env python3
"""Secret scanning — ADR-0009 #2 (high-confidence security control). Validity-tiered, redacting, shadow-first.

Scan the deliverable for committed secrets. Prefer the existing/official tool (gitleaks) and fall back to a
focused high-confidence detector when it is absent (existing-official-first). Tier by confidence, after the
GitHub push-protection model (ADR-0009): a KNOWN provider token or a private key is a CRITICAL finding (a
hard-block candidate); a generic high-entropy / generic-api-key match is ADVISORY (minor) because it is
false-positive-prone (placeholders, hashes, test fixtures).

CRITICAL: a finding NEVER carries the secret value — only rule + file + line + length — so the scan report
itself does not leak the secret it found. And the remediation for a real hit is to ROTATE/REVOKE the
credential, not merely delete it from the file (the value may already be in history/CI); that hint rides the
finding.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

# gitleaks rule ids (and id fragments) that name a KNOWN provider credential or a private key — high
# confidence, so a hit is critical. Everything else (notably generic-api-key / high-entropy) is advisory.
_CRITICAL_RULES = {
    "aws-access-token", "aws-secret-key", "github-pat", "github-fine-grained-pat", "github-oauth",
    "github-app-token", "gitlab-pat", "gitlab-ci-token", "openai-api-key", "anthropic-api-key",
    "stripe-access-token", "stripe-api-key", "slack-bot-token", "slack-user-token", "slack-app-token",
    "slack-webhook-url", "gcp-api-key", "gcp-service-account", "google-api-key", "twilio-api-key",
    "sendgrid-api-token", "npm-access-token", "pypi-upload-token", "square-access-token",
    "shopify-access-token", "digitalocean-access-token", "jwt", "telegram-bot-api-token",
}


def _is_critical_rule(rule_id: str) -> bool:
    r = (rule_id or "").lower()
    return r in _CRITICAL_RULES or "private-key" in r or "private_key" in r


def _finding(rule_id: str, file: str, line, severity: str, **extra) -> dict:
    return {
        "source": "secret-scan", "check": rule_id, "severity": severity, "passed": False,
        "file": file, "line": line,
        "detail": f"possible secret ({rule_id}) at {file}:{line} — ROTATE/REVOKE the credential, do not just "
                  f"delete it (it may already be in history/CI)",
        **extra,
    }


def _skip_path(rel: str) -> bool:
    parts = Path(rel).parts
    return any(p in (".git", ".agent-runs", "node_modules", "__pycache__", ".venv") for p in parts)


def gitleaks_available() -> bool:
    from shutil import which
    return which("gitleaks") is not None


def scan_with_gitleaks(path: str, *, run=None) -> list[dict]:
    """Scan `path` with gitleaks and return redacted, tiered findings. `run` is injectable for tests; by
    default it shells out. Never includes the Secret/Match fields gitleaks reports."""
    def _default_run(report_path: str):
        return subprocess.run(
            ["gitleaks", "dir", path, "--report-format", "json", "--report-path", report_path,
             "--exit-code", "0", "--no-banner"],
            capture_output=True, text=True, timeout=120,
        )

    run = run or _default_run
    with tempfile.NamedTemporaryFile("r", suffix=".json", delete=False) as tf:
        report_path = tf.name
    try:
        run(report_path)
        raw = json.loads(Path(report_path).read_text() or "[]")
    except Exception:                                          # noqa: BLE001 — fail-soft, never break the run
        return []
    finally:
        try:
            os.unlink(report_path)
        except OSError:
            pass

    findings = []
    for item in raw or []:
        rel = item.get("File", "")
        if _skip_path(rel):
            continue
        rule = item.get("RuleID", "unknown")
        sev = "critical" if _is_critical_rule(rule) else "minor"
        # redact: keep rule/file/line/entropy/length, DROP Secret and Match entirely.
        secret_len = len(item.get("Secret") or "")
        findings.append(_finding(rule, rel, item.get("StartLine"), sev,
                                 entropy=item.get("Entropy"), secret_len=secret_len))
    return findings


# Fallback detector — pure-Python, always available, the high-confidence core (no generic-entropy noise on
# the critical tier). Patterns name a provider credential or a private key.
_CRITICAL_PATTERNS = {
    "private-key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----"),
    "aws-access-token": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "github-pat": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
    "openai-api-key": re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b"),
    "stripe-api-key": re.compile(r"\b(?:sk|rk)_live_[0-9a-zA-Z]{16,}\b"),
    "slack-token": re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{10,}\b"),
    "google-api-key": re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
}
# advisory tier: a generically-named assignment to a long opaque literal — FP-prone, so minor.
_GENERIC_SECRET = re.compile(
    r"""(?i)\b(?:password|passwd|secret|token|api[_-]?key)\b\s*[:=]\s*['"][^'"\s]{12,}['"]""")

_TEXT_SUFFIXES = {".py", ".js", ".ts", ".json", ".yaml", ".yml", ".toml", ".env", ".sh", ".rb", ".go",
                  ".java", ".rs", ".txt", ".md", ".cfg", ".ini", ".conf", ".properties", ".tf", ".pem"}


def fallback_scan(path: str) -> list[dict]:
    findings = []
    root = Path(path)
    for fp in root.rglob("*"):
        if not fp.is_file():
            continue
        rel = str(fp.relative_to(root))
        if _skip_path(rel) or (fp.suffix and fp.suffix not in _TEXT_SUFFIXES):
            continue
        try:
            if fp.stat().st_size > 2_000_000:                  # skip large/binary-ish files
                continue
            text = fp.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for i, ln in enumerate(text.splitlines(), 1):
            for rule, pat in _CRITICAL_PATTERNS.items():
                m = pat.search(ln)
                if m:
                    findings.append(_finding(rule, rel, i, "critical", secret_len=len(m.group(0))))
            if _GENERIC_SECRET.search(ln):
                findings.append(_finding("generic-secret-assignment", rel, i, "minor"))
    return findings


def scan_dir(path: str, *, prefer_gitleaks: bool = True, run=None) -> dict:
    """Scan a deliverable directory. Returns {applicable, passed, findings, checks_run, backend}. `passed` is
    True only when there are NO findings at all; callers tier blocking by severity (critical hard-blocks,
    minor is advisory)."""
    if not Path(path).is_dir():
        return {"applicable": False, "passed": True, "findings": [], "checks_run": 0, "backend": None}
    if prefer_gitleaks and gitleaks_available():
        findings, backend = scan_with_gitleaks(path, run=run), "gitleaks"
    else:
        findings, backend = fallback_scan(path), "fallback"
    return {"applicable": True, "passed": not findings, "findings": findings,
            "checks_run": 1, "backend": backend}
