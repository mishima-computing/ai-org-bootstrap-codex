#!/usr/bin/env python3
"""Black-box CLI conformance checker — ADR-0009 investment #1, the executable part of the contract.

The review today is almost entirely *static*: Linon (`codex review`) reads the diff, and the implementer
*self-reports* its own test results. This module is the first *dynamic* gate: given an aufheben contract
that carries a `conformance.cli` profile and the built artifact, it INSTALLS via the declared commands, RUNS
each declared example, and compares **exit status + stdout/stderr** against the contract. It never trusts
the implementer's self-report; it re-runs.

The boundary (ADR-0009): the design agent *chooses and encodes* the interface (the `cli_profile` in the
contract); this checker *deterministically verifies* the built artifact obeys it. Exit code is pinned first
because it is the largest observed interface leak.

Design:
- Pure functions + an **injectable `runner`** — so it is unit-testable without executing anything, and so in
  production the same code runs *inside the ADR-0022 inner box* (the runner is the box boundary). The codex
  repo holds the logic; the box holds the execution.
- Output is the recovery-ladder **finding** shape (`severity`, `passed`, `detail`, ...), so a mismatch
  routes through the existing severity budget / repair routing (ADR-0008 addendum, ADR-0009) like any other
  finding — no parallel path.
- Comparison is conservative: exit status is exact; stdout/stderr default to **substring** checks
  (`expected_stdout_contains`), with optional normalized exact match (`expected_stdout`). Whole-output
  snapshots are avoided on purpose (they manufacture false positives — ADR-0009 / Tricorder discipline).
"""
from __future__ import annotations

import fnmatch
import json
import os
import re
import shlex
import signal
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, NamedTuple, Optional

try:
    import controller_scope as _controller_scope
except ImportError:  # pragma: no cover - direct reuse when scripts/ is importable; fallback stays local.
    _controller_scope = None


class RunResult(NamedTuple):
    """What a runner returns for one invocation. Mirrors subprocess.CompletedProcess' useful fields."""
    returncode: int
    stdout: str
    stderr: str


class HttpResponse(NamedTuple):
    """The HTTP checker's bounded, testable response shape."""
    status: int
    body: bytes


# A runner executes one shell command and returns a RunResult. In tests it is a fake keyed by command; in
# production it is the inner-box exec (the box boundary). Signature kept tiny on purpose.
Runner = Callable[..., RunResult]


# Volatile substrings that must not turn a correct artifact into a false mismatch on exact-stdout checks.
# Normalization is deliberately small (ADR-0009: normalize timestamps/paths/ordering, do not snapshot blobs).
_ISO_TS = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?")
_TMP_PATH = re.compile(r"/(?:tmp|var/folders|private/var)/[^\s'\"]+")
_HEX_BLOB = re.compile(r"\b[0-9a-f]{12,}\b")


def _normalize(text: str) -> str:
    """Collapse the volatility that makes exact-output checks brittle: CRLF, trailing per-line whitespace,
    ISO timestamps, temp paths, long hex blobs (ids/digests). Intentionally minimal — we normalize the
    *known-volatile*, never the content under test."""
    text = text.replace("\r\n", "\n")
    text = _ISO_TS.sub("<TS>", text)
    text = _TMP_PATH.sub("<TMP>", text)
    text = _HEX_BLOB.sub("<HEX>", text)
    lines = [ln.rstrip() for ln in text.split("\n")]
    return "\n".join(lines).strip("\n")


_FAILURE_CLASSES = {"code", "infra", "timeout", "resource", "undetermined"}
_INFRA_TEXT_RE = re.compile(
    r"(command not found|executable file not found)",
    re.IGNORECASE,
)
_ADDRESS_IN_USE_RE = re.compile(r"(Address already in use|Errno 48|Errno 98)", re.IGNORECASE)
_RESOURCE_TEXT_RE = re.compile(r"\b(killed|out of memory|oom)\b", re.IGNORECASE)
# A NARROW whitelist of host/runner-absence text: a known TEST RUNNER or TOOL is missing. `python -m pytest`
# reports an absent pytest as `No module named pytest` on exit 1 (NOT 127), so this MUST be recognized as a
# verification-environment gap (infra/unsupported-env) BEFORE the generic `No module named` CODE clause below —
# otherwise a missing runner is blamed on the product. Kept deliberately NARROW (ADR-0006): ONLY the known
# runner/tool names match here, so a missing PRODUCT dependency (e.g. `No module named requests`, not a known
# runner) still falls through to `_CODE_TEXT_RE` and stays a product defect (an undeclared dependency, incr #3).
#
# PRECISION (the masking direction, ADR-0006/0011): every clause must be an UNAMBIGUOUS absence of a KNOWN
# external runner/tool, never a generic phrase a product could emit — otherwise a genuine product failure whose
# output happens to contain the phrase is re-labeled infra and a real bug is parked at "unverified", never
# repaired. Two such seams were closed:
#   * `no module named pytest` is ANCHORED with a trailing `\b` so it matches the RUNNER `pytest` ONLY. The
#     plugin module `pytest_asyncio` (an `_`-suffixed name the PRODUCT's own test code imports) keeps `pytest`
#     adjacent to a word char `_`, so no boundary forms and it falls through to `_CODE_TEXT_RE` -> code: a
#     product's missing pytest-plugin dependency is the product's defect, not a missing runner.
#   * the generic `is not available` clause was DROPPED (a real product failure can legitimately print
#     "X is not available"). Only the two SPECIFIC, checker-emitted tool phrasings remain — `machinery is
#     unavailable` (grpcio/protobuf absent, `_build_grpc_invoker`) and `jsonschema is not available`
#     (`_json_schema_findings`).
#   * the python-runner whitelist is `pytest|tox|nox|coverage|nose2|unittest` — a small EXPLICIT set of canonical
#     `python -m <runner>` test/build-tool modules, each ANCHORED with a trailing `\b` exactly as `pytest` is, so a
#     PRODUCT plugin extending a runner name (`tox_<plugin>`, `nox_<plugin>`, `coverage_<x>`, `unittest2`) keeps the
#     runner adjacent to a word char and falls through to `code`. NON-python runners (jest/go test) are still NOT
#     whitelisted (absent ones already exit 127 -> infra when invoked directly); they remain the SAFE direction.
#
# INVARIANT (executable guard, NOT just reviewer discipline): because this regex is consulted ABOVE `_CODE_TEXT_RE`
# in `_failure_classification`, every clause here must match ONLY strings the CHECKER/RUNNER ITSELF emits about its
# OWN absence — never any string a PRODUCT or its TEST suite could produce. A too-broad clause would re-label a real
# product defect as infra/unsupported-env and park it at "unverified" forever (the masking direction). Any NEW
# clause must be ANCHORED (word boundaries, no generic phrase) AND must keep
# `test_product_emittable_failures_stay_code_never_unsupported_env` (test_conformance.py) GREEN — that test asserts
# product-emittable strings stay `code`, so whitelist drift becomes a test failure rather than a silent masked defect.
_UNSUPPORTED_ENV_RE = re.compile(
    r"(command not found|executable file not found|"
    # KNOWN python test-runner / build-tool modules absent under `python -m <runner>` (exit 1, not 127). Each
    # alternative is a CANONICAL runner module name, ANCHORED by a trailing `\b`, so a PRODUCT plugin whose name
    # extends the runner (e.g. `pytest_asyncio`, `tox_<plugin>`, `coverage_<x>`) keeps the runner adjacent to a
    # word char `_`/digit -> no boundary forms -> it falls through to `_CODE_TEXT_RE` -> code (a product defect).
    r"no module named (?:pytest|tox|nox|coverage|nose2|unittest)\b|"
    r"machinery is unavailable|jsonschema is not available)",
    re.IGNORECASE,
)
# Established CODE signals in the OUTPUT of a command that actually RAN (we have already returned `infra` for a
# 127 command-not-found / a known-runner-absence above, so an import/assertion failure here is the product's own
# defect, not a host gap — a `No module named X` for a NON-runner X is the product's undeclared dependency).
_CODE_TEXT_RE = re.compile(r"(ModuleNotFoundError|ImportError|No module named|AssertionError)", re.IGNORECASE)
# A POSIX shell reports a child that died on a signal as a POSITIVE exit 128+N (the in-process runner reports it
# as a NEGATIVE rc; both are could-not-run, not a product defect). SIGKILL/OOM (137), SIGABRT (134), SIGSEGV
# (139) are a resource death; SIGTERM (143) / SIGINT (130) are an external terminate/interrupt (infra).
_SIGNAL_EXIT_RESOURCE = {137, 134, 139}
_SIGNAL_EXIT_INFRA = {143, 130}


def _stringify(value) -> str:
    try:
        return "" if value is None else str(value)
    except Exception:
        return ""


def _failure_classification(*, returncode=None, stdout="", stderr="", error=None, detail: str = "",
                            default: str = "undetermined") -> str:
    """Classify whether a failed gate points at product code or at the host/runner that tried to verify it.

    `default` is what a *genuinely ambiguous* nonzero exit resolves to. It is `undetermined` for generic call
    sites (ADR-0011/0016: a could-not-tell outcome is UNVERIFIED, never a hidden product pass), and `code` for
    ORACLE/ANALYZER/BUILD gates whose contract makes a plain nonzero a positive product signal (a build/install
    that fails — the artifact cannot even be built — an example exit-code mismatch, a once-green regression suite
    that now fails, a static analyzer that ran and reports errors). Could-not-run
    signals (127 / 124 / signal-death codes / addr-in-use) and established code signals (import/assertion in the
    output) are decided BEFORE the default, so they hold regardless of which default a call site passes."""
    if isinstance(error, TimeoutError):
        return "timeout"
    if isinstance(error, FileNotFoundError):
        return "infra"
    if isinstance(error, OSError):
        if getattr(error, "errno", None) in (48, 98) or _ADDRESS_IN_USE_RE.search(_stringify(error)):
            return "infra"
    text = "\n".join(_stringify(v) for v in (stdout, stderr, error, detail))
    if _ADDRESS_IN_USE_RE.search(text):
        return "infra"
    try:
        rc = int(returncode)
    except (TypeError, ValueError):
        rc = None
    if rc == 124:
        return "timeout"
    if rc == 127:
        return "infra"
    # A known RUNNER/TOOL is absent (e.g. `python -m pytest` -> "No module named pytest" on exit 1, not 127). That
    # is a verification-environment gap, decided HERE — in the host/runner-absent group, BEFORE signal/resource
    # deaths and BEFORE the generic `No module named` CODE clause — so a missing runner reroutes to escalate/
    # clean_retry and is never reported to the implementer as a product defect. NARROW: only the known
    # runner/tool names; a missing PRODUCT dependency falls through to `_CODE_TEXT_RE` -> code (incr #3).
    if _UNSUPPORTED_ENV_RE.search(text):
        return "infra"
    if rc is not None and rc < 0:
        return "resource"
    if rc in _SIGNAL_EXIT_RESOURCE:
        return "resource"
    if rc in _SIGNAL_EXIT_INFRA:
        return "infra"
    if _INFRA_TEXT_RE.search(text):
        return "infra"
    if _RESOURCE_TEXT_RE.search(text):
        return "resource"
    if _CODE_TEXT_RE.search(text):
        return "code"
    if returncode is None and not any(_stringify(v) for v in (stdout, stderr, error, detail)):
        return "undetermined"
    return default


# ── incr #3: the producer FINDINGS-ROUTER. Each non-passing finding carries a small, EXPLICIT routing model so
# the gate decision (controller_pipeline._finding_blocks_convergence) reads an intent, not a back-compat label.
# `failure_classification` is kept verbatim for back-compat; the routing fields are derived from it + signals.
_GATE_STATE_PASS = "VERIFIED_PASS"
_GATE_STATE_PRODUCT_FAILURE = "VERIFIED_PRODUCT_FAILURE"
_GATE_STATE_INFRA = "COULD_NOT_RUN_INFRA"
_GATE_STATE_UNSUPPORTED = "COULD_NOT_RUN_UNSUPPORTED_ENV"
_GATE_STATE_INDETERMINATE = "INDETERMINATE"

# failure_classification -> (gate_state, repair_route, retryable, confidence). Only `code` routes to the
# implementer repair loop; could-not-run/inconclusive route to the incr-#2 clean retry (or escalate), never the
# implementer — so an ambiguous outcome can no longer masquerade as a product defect (nor, post-#1, as a pass).
_ROUTING_BY_CLASS = {
    "code":         (_GATE_STATE_PRODUCT_FAILURE, "implementer", False, "high"),
    "infra":        (_GATE_STATE_INFRA,           "clean_retry", True,  "medium"),
    "timeout":      (_GATE_STATE_INFRA,           "clean_retry", True,  "medium"),
    "resource":     (_GATE_STATE_INFRA,           "clean_retry", True,  "medium"),
    "undetermined": (_GATE_STATE_INDETERMINATE,   "clean_retry", True,  "low"),
}
# An infra failure a clean retry will NOT fix because the HOST lacks a required interpreter/tool/library: route
# it to ESCALATE (unsupported env), not the implementer and not an indefinite retry. Kept as `infra` for
# back-compat; only the routing view distinguishes it. The matching whitelist `_UNSUPPORTED_ENV_RE` is defined
# above (near `_CODE_TEXT_RE`) because `_failure_classification` now consults it in the host/runner-absent group.


# example/oracle checks whose failure means the artifact RAN and produced the wrong answer — the failure
# surfaced in the call phase, not the report phase.
_CALL_PHASE_CHECKS = {"exit_status", "stdout_contains", "stderr_contains", "stdout_exact", "request", "response"}


def _derive_phase(text: str, returncode, check: str = "") -> str:
    """Best-effort: which PHASE of the gate did the failure surface in, from the signal we have. The `check`
    name is the most reliable signal (a `build_and_install` finding is a build-phase failure regardless of its
    text); free-text regexes refine the rest. NOT a blanket 'report' — that is only the genuine fallback."""
    try:
        rc = int(returncode)
    except (TypeError, ValueError):
        rc = None
    if rc == 127 or _INFRA_TEXT_RE.search(text):
        return "launch"
    if check == "build_and_install" or re.search(r"could not build wheel|failed building wheel|compile", text, re.IGNORECASE):
        return "build"
    if re.search(r"ModuleNotFoundError|ImportError|No module named", text, re.IGNORECASE):
        return "dependency"
    if re.search(r"during collection|collection error", text, re.IGNORECASE):
        return "collection"
    if _ADDRESS_IN_USE_RE.search(text):
        return "setup"
    if check in _CALL_PHASE_CHECKS or rc == 124:
        return "call"
    return "report"


def _with_failure_classification(finding: dict, passed: bool) -> dict:
    """Normalize `failure_classification` and attach the explicit routing model (incr #3). A passed finding is a
    VERIFIED_PASS that needs no repair. Routing fields use `setdefault`, so a producer that KNOWS the route (an
    example/oracle product mismatch) may pin it explicitly and this never overrides it."""
    if passed:
        finding.setdefault("gate_state", _GATE_STATE_PASS)
        finding.setdefault("repair_route", "none")
        finding.setdefault("phase", "report")
        finding.setdefault("confidence", "high")
        finding.setdefault("retryable", False)
        return finding
    cls = finding.get("failure_classification")
    if cls is None:
        # incr #3 (narrowed flip): a finding with NO explicit class is NOT blindly `undetermined` — first
        # recognize any ESTABLISHED product-failure / infra / resource signal in the finding's OWN evidence
        # (returncode + output: a signal-death exit, an import/assertion in the output, an addr-in-use). Only a
        # genuinely unrecognizable nonzero falls through to `undetermined`. An established signal is never swallowed.
        cls = _failure_classification(
            returncode=finding.get("returncode"),
            stdout=finding.get("stdout_tail", ""),
            stderr=finding.get("stderr_tail", ""),
            detail=finding.get("detail", ""),
        )
    cls = cls if cls in _FAILURE_CLASSES else "undetermined"
    finding["failure_classification"] = cls
    gate_state, repair_route, retryable, confidence = _ROUTING_BY_CLASS[cls]
    text = " ".join(_stringify(finding.get(k)) for k in ("detail", "stderr_tail", "stdout_tail", "check"))
    if cls == "infra" and _UNSUPPORTED_ENV_RE.search(text):
        gate_state, repair_route, retryable, confidence = (_GATE_STATE_UNSUPPORTED, "escalate", False, "medium")
    finding.setdefault("gate_state", gate_state)
    finding.setdefault("repair_route", repair_route)
    finding.setdefault("phase", _derive_phase(text, finding.get("returncode"), finding.get("check", "")))
    finding.setdefault("confidence", confidence)
    finding.setdefault("retryable", retryable)
    return finding


def _finding(check: str, severity: str, passed: bool, detail: str, **extra) -> dict:
    f = {"source": "cli-conformance", "check": check, "severity": severity, "passed": passed, "detail": detail}
    f.update(extra)
    return _with_failure_classification(f, passed)


def _full_command(entrypoint: str, invocation: str) -> str:
    """Join the entrypoint with an example's invocation. The empty invocation is the no-argument launch."""
    inv = invocation.strip()
    return entrypoint.strip() if not inv else f"{entrypoint.strip()} {inv}"


def install_findings(profile: dict, runner: Runner, *, cwd: Optional[str] = None) -> list[dict]:
    """Run the declared build/install commands in order. A non-zero exit is a *critical* finding (the
    artifact cannot even be built — nothing downstream is meaningful). Stops at the first failure."""
    build = profile.get("build_and_install") or {}
    findings: list[dict] = []
    for cmd in build.get("commands", []):
        res = runner(cmd, cwd=cwd)
        if res.returncode != 0:
            # incr #3 (build/install path): a build/install/compile gate that RAN and failed is an ESTABLISHED
            # product-failure signal — the artifact cannot even be built, which is the product's own defect
            # (`default="code"`). A could-not-run exit (127 / signal-death / addr-in-use) is still recognized
            # BEFORE the default, so a missing toolchain stays `infra`, not a false product defect.
            cls = _failure_classification(returncode=res.returncode, stdout=res.stdout, stderr=res.stderr,
                                          default="code")
            findings.append(_finding(
                "build_and_install", "critical", False,
                f"install command failed (exit {res.returncode}): {cmd}",
                command=cmd, returncode=res.returncode,
                stderr_tail=_normalize(res.stderr)[-800:],
                failure_classification=cls,
            ))
            break  # a broken build invalidates every example; do not pile on derived failures
    return findings


def example_findings(profile: dict, runner: Runner, *, cwd: Optional[str] = None) -> list[dict]:
    """Run each declared example and compare exit status + stdout/stderr. Exit-status mismatch and a wrong
    output channel are the contract-precision leaks ADR-0009 targets, so they are `major` findings."""
    entry = (profile.get("entrypoint") or {}).get("invocation", "")
    findings: list[dict] = []
    for i, ex in enumerate(profile.get("examples", [])):
        cmd = _full_command(entry, ex.get("invocation", ""))
        res = runner(cmd, cwd=cwd, stdin=ex.get("stdin"))

        if "expected_status" in ex and res.returncode != ex["expected_status"]:
            # incr #3 (item #5): an example exit-code mismatch is the positive PRODUCT signal (`default="code"`),
            # but a could-not-run exit (127/124/137/...) still wins — the artifact never produced an answer.
            cls = _failure_classification(returncode=res.returncode, stdout=res.stdout, stderr=res.stderr,
                                          default="code")
            findings.append(_finding(
                "exit_status", "major", False,
                f"example {i} `{cmd}`: expected exit {ex['expected_status']}, got {res.returncode}",
                example=i, command=cmd, expected=ex["expected_status"], actual=res.returncode,
                stderr_tail=_normalize(res.stderr)[-800:], returncode=res.returncode,
                failure_classification=cls,
            ))

        for needle in ex.get("expected_stdout_contains", []):
            if needle not in res.stdout:
                findings.append(_finding(
                    "stdout_contains", "major", False,
                    f"example {i} `{cmd}`: stdout missing expected substring {needle!r}",
                    example=i, command=cmd, expected=needle,
                    actual=_normalize(res.stdout)[-800:],
                    failure_classification="code",        # incr #3 (item #5): the artifact RAN and produced wrong output
                ))

        for needle in ex.get("expected_stderr_contains", []):
            if needle not in res.stderr:
                findings.append(_finding(
                    "stderr_contains", "major", False,
                    f"example {i} `{cmd}`: stderr missing expected substring {needle!r}",
                    example=i, command=cmd, expected=needle,
                    actual=_normalize(res.stderr)[-800:],
                    failure_classification="code",        # incr #3 (item #5): output-oracle mismatch -> product defect
                ))

        if "expected_stdout" in ex:
            want, got = _normalize(ex["expected_stdout"]), _normalize(res.stdout)
            if want != got:
                findings.append(_finding(
                    "stdout_exact", "major", False,
                    f"example {i} `{cmd}`: normalized stdout did not match",
                    example=i, command=cmd, expected=want, actual=got,
                    failure_classification="code",        # incr #3 (item #5): output-oracle mismatch -> product defect
                ))
    return findings


_SCRIPT_EXTS = (".py", ".js", ".ts", ".mjs", ".cjs", ".sh", ".rb", ".pl", ".ps1", ".psm1")


def _missing_entrypoint_artifact(profile: dict, cwd: Optional[str]) -> Optional[str]:
    """If the entrypoint names a concrete file (a script path) that is NOT present in the workspace, return
    that token. Distinguishes "nothing was built/delivered" (one clear finding) from "built but wrong" (the
    example failures) — without it, a missing artifact makes every example fail with the same file-not-found
    exit and reads as a buggy build. Heuristic and conservative: only a token that clearly names a file is
    checked; a bare command (`mytool`) or a module launch (`python -m pkg`) is left to the examples."""
    if not cwd:
        return None
    inv = (profile.get("entrypoint") or {}).get("invocation", "")
    tokens = inv.split()
    if "-m" in tokens:                                         # `python -m pkg` has no file token to check
        return None
    for tok in tokens:
        if tok.startswith("-"):
            continue
        names_file = tok.endswith(_SCRIPT_EXTS) or ("/" in tok and not tok.endswith("/"))
        if names_file:
            cand = tok if os.path.isabs(tok) else os.path.join(cwd, tok)
            if not os.path.exists(cand):
                return tok
    return None


def run_cli_conformance(contract: dict, runner: Runner, *, cwd: Optional[str] = None) -> dict:
    """Top-level gate entry. Returns a report:
        {applicable, passed, findings, checks_run}
    `applicable` is False (and passed True, vacuously) when the contract carries no cli profile — so the
    gate is a no-op for non-CLI deliverables and never fabricates a finding it cannot ground. When the build
    fails, examples are skipped (their failures would be derived, not independent)."""
    profile = (contract.get("conformance") or {}).get("cli")
    if contract.get("deliverable_kind") != "cli" or not profile:
        return {"applicable": False, "passed": True, "findings": [], "checks_run": 0}

    findings = install_findings(profile, runner, cwd=cwd)
    build_broke = any(f["check"] == "build_and_install" for f in findings)
    missing = None if build_broke else _missing_entrypoint_artifact(profile, cwd)
    if missing:
        # one clear finding instead of N derived file-not-found example failures: nothing was delivered to run.
        findings.append(_finding(
            "artifact_missing", "critical", False,
            f"the entrypoint artifact '{missing}' is not present in the workspace — nothing was "
            f"built/delivered to verify (the implementer wrote no file, or it did not merge back)",
            artifact=missing, failure_classification="code"))   # incr #3: a delivery defect -> implementer
    elif not build_broke:
        findings += example_findings(profile, runner, cwd=cwd)

    checks_run = len(profile.get("build_and_install", {}).get("commands", [])) + len(profile.get("examples", []))
    return {
        "applicable": True,
        "passed": all(f["passed"] for f in findings),
        "findings": findings,
        "checks_run": checks_run,
    }


# ADR-0009 #1 remainder — EMPTY SLOTS for the other deliverable kinds. The plumbing (schema profile ->
# aufheben emits -> shadow gate -> finding routing) is proven by the CLI path and is kind-agnostic, so it is
# replicated for free. The per-kind CHECKER is the real, differentiated work and is NOT done: a library has
# no process to run (it needs API introspection + API-diff), a service needs boot + protocol-driven testing.
# Every executable kind (cli, http_service, library, json, batch_job, rpc_service) has a real checker. The
# `undetermined` kind is the explicit ENTRY to the empty-slot mechanism: a deliverable WITH a checkable
# interface whose kind no checker supports yet — recognized, streamed as unchecked (a checker is owed), never
# a silent pass. It is distinct from `none`, which asserts there is no interface to check. Without it, a novel
# interface deliverable would have to mislabel itself `none` and ship unverified.
_SLOT_KINDS = ("undetermined",)


def _empty_slot(kind: str) -> dict:
    """The report for a recognized deliverable kind whose checker is not built yet. applicable=False so it
    blocks nothing and folds no findings; `slot` marks it so the gate can STREAM that the kind was recognized
    but not checked (no silent cap)."""
    return {"applicable": False, "passed": True, "findings": [], "checks_run": 0,
            "slot": kind, "status": "no-checker-yet"}


def run_undetermined_conformance(contract: dict, runner: Runner, *, cwd: Optional[str] = None) -> dict:
    """A deliverable with a checkable interface of a kind no checker supports yet. Recognized but unchecked —
    streamed (slot_unchecked), never a silent pass; declaring it (rather than mislabelling the artifact
    `none`) honestly signals that a real checker is owed for this new kind."""
    return _empty_slot("undetermined")


_LIBRARY_PROBE_MARKER = "__LIBPROBE__ "


def _library_probe_source(module: str, import_paths: list, symbols: list) -> str:
    """A self-contained Python probe: import the module and report (via a single marker line of JSON) whether
    it imported and which declared symbols are missing. It always exits 0 if it ran at all — the outcome is in
    the marker, so 'the probe could not run' (no marker) is distinguishable from 'imported, symbols missing'."""
    return (
        "import importlib, sys, json\n"
        f"sys.path[:0] = {list(import_paths)!r}\n"
        "try:\n"
        f"    m = importlib.import_module({module!r})\n"
        "except Exception as e:\n"
        f"    print({_LIBRARY_PROBE_MARKER!r} + json.dumps("
        "{'import_error': type(e).__name__ + ': ' + str(e)}))\n"
        "    sys.exit(0)\n"
        f"missing = [s for s in {list(symbols)!r} if not hasattr(m, s)]\n"
        f"print({_LIBRARY_PROBE_MARKER!r} + json.dumps({{'missing': missing}}))\n"
    )


def _library_probe_findings(res: RunResult, module: str, symbols: list) -> list[dict]:
    """Parse the probe's marker line into findings. No marker -> the probe could not run (critical, the
    introspection itself failed); import_error -> the module does not import (critical); each missing symbol
    -> the declared public surface is absent (major)."""
    line = next((ln for ln in res.stdout.splitlines() if ln.startswith(_LIBRARY_PROBE_MARKER)), None)
    if line is None:
        cls = _failure_classification(returncode=res.returncode, stdout=res.stdout, stderr=res.stderr)
        return [_finding(
            "library_probe", "critical", False,
            f"could not introspect the library: the import probe produced no result (exit {res.returncode})",
            module=module, stderr_tail=_normalize(res.stderr)[-800:], failure_classification=cls)]
    try:
        data = json.loads(line[len(_LIBRARY_PROBE_MARKER):])
    except ValueError:
        return [_finding("library_probe", "critical", False,
                         "the import probe emitted an unparseable result", module=module)]
    if data.get("import_error"):
        # the module the contract declares does not import: a product defect (default="code"), unless the text
        # carries a could-not-run signal that _failure_classification recognizes first.
        cls = _failure_classification(detail=data["import_error"], default="code")
        return [_finding("library_import", "critical", False,
                         f"module {module!r} failed to import: {data['import_error']}", module=module,
                         failure_classification=cls)]
    return [_finding(
        "exported_symbol", "major", False,
        f"declared export {sym!r} does not resolve on module {module!r}", module=module, symbol=sym,
        failure_classification="code")        # incr #3: a missing declared export is a product defect
        for sym in data.get("missing", [])]


def _ps_single_quote(value: str) -> str:
    """Quote a value as a PowerShell single-quoted string literal (doubling embedded quotes)."""
    return "'" + str(value).replace("'", "''") + "'"


def _powershell_library_probe_source(module: str, import_paths: list, symbols: list) -> str:
    """The pwsh counterpart of the python probe: Import-Module the module and report — on the SAME
    `__LIBPROBE__` marker line, as JSON — the import error or the declared exported commands that do not
    resolve, so `_library_probe_findings` parses it unchanged. Built by placeholder substitution (PowerShell's
    `{}`/`@{}` collide with f-string braces)."""
    paths = "@(" + ",".join(_ps_single_quote(p) for p in import_paths) + ")"
    syms = "@(" + ",".join(_ps_single_quote(s) for s in symbols) + ")"
    marker = repr(_LIBRARY_PROBE_MARKER)  # '__LIBPROBE__ ' — a valid PowerShell single-quoted literal too
    tmpl = (
        "$ErrorActionPreference='Stop';"
        "$paths=__PATHS__;"
        "if($paths.Count -gt 0){$env:PSModulePath=($paths -join [IO.Path]::PathSeparator)+"
        "[IO.Path]::PathSeparator+$env:PSModulePath};"
        "$mod=__MOD__;"
        "try{$m=Import-Module $mod -Force -PassThru -ErrorAction Stop}"
        "catch{Write-Output (__MARKER__+(@{import_error=($_.Exception.GetType().Name+': '+"
        "$_.Exception.Message)}|ConvertTo-Json -Compress));exit 0};"
        "$exported=@($m.ExportedCommands.Keys);"
        "$missing=@();foreach($s in __SYMS__){if($exported -notcontains $s){$missing+=$s}};"
        "Write-Output (__MARKER__+(@{missing=@($missing)}|ConvertTo-Json -Compress))"
    )
    return (tmpl.replace("__PATHS__", paths).replace("__SYMS__", syms)
            .replace("__MOD__", _ps_single_quote(module)).replace("__MARKER__", marker))


def _library_probe_command(profile: dict, module: str, import_paths: list, symbols: list) -> str:
    """The shell command that runs the import probe for the profile's language. `python` -> the interpreter
    with `-c`; `powershell` -> `pwsh -NoProfile -Command`. Both emit the shared `__LIBPROBE__` marker line, so
    only the command differs."""
    if (profile.get("language") or "python").lower() == "powershell":
        return f"pwsh -NoProfile -Command {shlex.quote(_powershell_library_probe_source(module, import_paths, symbols))}"
    interpreter = profile.get("python") or "python3"
    return f"{interpreter} -c {shlex.quote(_library_probe_source(module, import_paths, symbols))}"


def run_library_conformance(contract: dict, runner: Runner, *, cwd: Optional[str] = None) -> dict:
    """Black-box library checker (ADR-0009 #1). Runs the declared build/install, then an import probe THROUGH
    the runner (never importing the built artifact in-process): import the module and assert each declared
    exported symbol resolves. A broken build skips the probe (its failure would be derived). Signatures and
    baseline API-diff are a later addition; this pins importability + public-surface presence."""
    profile = (contract.get("conformance") or {}).get("library")
    if contract.get("deliverable_kind") != "library" or not profile:
        return {"applicable": False, "passed": True, "findings": [], "checks_run": 0}

    findings = install_findings(profile, runner, cwd=cwd)
    checks_run = len(profile.get("build_and_install", {}).get("commands", []))
    if not any(f["check"] == "build_and_install" for f in findings):
        module = profile.get("module") or ""
        symbols = profile.get("exported_symbols") or []
        import_paths = list(profile.get("import_paths") or ["."])
        res = runner(_library_probe_command(profile, module, import_paths, symbols), cwd=cwd)
        checks_run += 1
        findings += _library_probe_findings(res, module, symbols)

    return {
        "applicable": True,
        "passed": all(f["passed"] for f in findings),
        "findings": findings,
        "checks_run": checks_run,
    }


def _resolve_json_path(data, dotted: str) -> bool:
    """True if a dotted key path resolves through nested objects. List indexing is out of scope for v1 —
    'this nested field exists' is the common presence contract."""
    cur = data
    for key in dotted.split("."):
        if isinstance(cur, dict) and key in cur:
            cur = cur[key]
        else:
            return False
    return True


def _json_schema_findings(data, schema: dict, path: str) -> list[dict]:
    """Validate parsed data against a declared JSON Schema; each error is a major finding. A missing
    jsonschema or an invalid declared schema is itself a finding — never a silent skip."""
    try:
        from jsonschema import validators
    except ImportError:
        return [_finding("json_schema", "major", False,
                         f"could not validate '{path}': jsonschema is not available", path=path,
                         failure_classification="infra")]
    try:
        validator_cls = validators.validator_for(schema)
        validator_cls.check_schema(schema)
        validator = validator_cls(schema)
    except Exception as exc:  # noqa: BLE001 — an invalid declared schema is a contract defect; surface it
        return [_finding("json_schema", "major", False,
                         f"the declared JSON Schema for '{path}' is itself invalid: {exc}", path=path,
                         failure_classification="code")]
    findings = []
    for err in sorted(validator.iter_errors(data), key=lambda e: list(e.path)):
        loc = "/".join(str(p) for p in err.path) or "(root)"
        findings.append(_finding("json_schema", "major", False,
                                 f"'{path}' violates its schema at {loc}: {err.message}", path=path, location=loc,
                                 failure_classification="code"))   # incr #3: a schema violation is a product defect
    return findings


def run_json_conformance(contract: dict, runner: Runner = None, *, cwd: Optional[str] = None) -> dict:
    """Structural checker for a produced JSON document (ADR-0009 #1). Reads each declared file, parses it,
    validates it against a declared JSON Schema (inline or referenced), and asserts declared key paths
    resolve. Runs NO process (the runner is accepted for dispatch uniformity and ignored) — it only reads
    data, so it executes nothing untrusted and needs no isolation. A missing or unparseable file is critical;
    schema and key-path violations are major."""
    profile = (contract.get("conformance") or {}).get("json")
    if contract.get("deliverable_kind") != "json" or not profile:
        return {"applicable": False, "passed": True, "findings": [], "checks_run": 0}

    findings: list[dict] = []
    specs = profile.get("files", [])
    for spec in specs:
        path = spec.get("path") or ""
        full = path if (cwd is None or os.path.isabs(path)) else os.path.join(cwd, path)
        try:
            with open(full, encoding="utf-8") as fh:
                data = json.load(fh)
        except FileNotFoundError:
            findings.append(_finding("json_missing", "critical", False,
                                     f"declared JSON file '{path}' is not present in the workspace", path=path,
                                     failure_classification="code"))   # incr #3: undelivered output -> product defect
            continue
        except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
            findings.append(_finding("json_parse", "critical", False,
                                     f"'{path}' is not valid JSON: {exc}", path=path,
                                     failure_classification="code"))   # incr #3: malformed output -> product defect
            continue

        schema = spec.get("schema")
        if schema is None and spec.get("schema_path"):
            sp = spec["schema_path"]
            sfull = sp if (cwd is None or os.path.isabs(sp)) else os.path.join(cwd, sp)
            try:
                with open(sfull, encoding="utf-8") as fh:
                    schema = json.load(fh)
            except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
                findings.append(_finding("json_schema", "major", False,
                                         f"could not load schema_path '{sp}' for '{path}': {exc}", path=path))
                schema = None
        if schema is not None:
            findings += _json_schema_findings(data, schema, path)

        for rp in spec.get("required_paths", []):
            if not _resolve_json_path(data, rp):
                findings.append(_finding("json_required_path", "major", False,
                                         f"'{path}' is missing the required key path '{rp}'",
                                         path=path, key_path=rp,
                                         failure_classification="code"))   # incr #3: missing required field -> product defect

    return {
        "applicable": True,
        "passed": all(f["passed"] for f in findings),
        "findings": findings,
        "checks_run": len(specs),
    }


_RPC_TRANSPORTS = ("json_rpc_http", "grpc")


class _RpcTransportUnavailable(Exception):
    """The declared transport's machinery (e.g. grpcio) or inputs (e.g. a descriptor set) are not present, so
    the call cannot be invoked. Surfaced as a finding — never a silent pass."""


def _rpc_finding(check: str, severity: str, passed: bool, detail: str, **extra) -> dict:
    f = {"source": "rpc-conformance", "check": check, "severity": severity, "passed": passed, "detail": detail}
    f.update(extra)
    return _with_failure_classification(f, passed)


def _rpc_profile_findings(profile: dict) -> list[dict]:
    """Structural completeness, checked before booting anything."""
    findings: list[dict] = []
    transport = profile.get("transport")
    if transport not in _RPC_TRANSPORTS:
        findings.append(_rpc_finding(
            "transport", "critical", False,
            f"unsupported rpc transport {transport!r} (supported: {', '.join(_RPC_TRANSPORTS)})",
            transport=transport, failure_classification="code"))
    if not profile.get("calls"):
        findings.append(_rpc_finding("calls", "major", False, "rpc contract declares no calls to verify",
                                     failure_classification="code"))
    return findings


def _check_rpc_result(i: int, method, call: dict, result, error) -> list[dict]:
    """Compare one decoded RPC response against the call's expectations. JSON-RPC and gRPC both reduce to
    'a result or an error', so the comparison is shared. An expected_error_code means the call SHOULD fail
    with that code; otherwise an error is unexpected and the declared result substrings must be present."""
    findings: list[dict] = []
    if "expected_error_code" in call:
        code = (error or {}).get("code") if isinstance(error, dict) else error
        if code != call["expected_error_code"]:
            findings.append(_rpc_finding(
                "error_code", "major", False,
                f"call {i} {method}: expected error code {call['expected_error_code']}, got {code!r}",
                call=i, method=method, expected=call["expected_error_code"], actual=code,
                failure_classification="code"))   # incr #3: the call RAN and returned the wrong code -> product defect
        return findings
    if error is not None:
        findings.append(_rpc_finding("error", "major", False,
                                     f"call {i} {method}: unexpected error {error!r}", call=i, method=method,
                                     failure_classification="code"))   # incr #3: an unexpected error -> product defect
        return findings
    result_text = json.dumps(result, default=str)
    for needle in call.get("expected_result_contains", []):
        if needle not in result_text:
            findings.append(_rpc_finding(
                "result_contains", "major", False,
                f"call {i} {method}: result missing expected substring {needle!r}",
                call=i, method=method, expected=needle,
                failure_classification="code"))   # incr #3: result-oracle mismatch -> product defect
    return findings


def _json_rpc_call_findings(profile: dict, http_request) -> list[dict]:
    """json_rpc_http transport (stdlib): POST a JSON-RPC 2.0 envelope and check the response's result/error.
    JSON-RPC returns HTTP 200 even on an application error (the error is in the body), so the contract is
    checked on result/error, NOT the HTTP status — this is the distinction a plain http_service profile
    would miss."""
    findings: list[dict] = []
    url = profile["base_url"]
    for i, call in enumerate(profile.get("calls", [])):
        method = call.get("method")
        envelope = {"jsonrpc": "2.0", "id": i + 1, "method": method, "params": call.get("params", {})}
        try:
            resp = http_request("POST", url, json_body=envelope, has_json_body=True,
                                timeout=_HTTP_REQUEST_TIMEOUT_SECONDS)
        except (OSError, TimeoutError, urllib.error.URLError) as exc:
            findings.append(_rpc_finding("request", "major", False, f"call {i} {method}: request failed",
                                         call=i, method=method, error=type(exc).__name__,
                                         failure_classification=_failure_classification(error=exc)))
            continue
        try:
            body = json.loads(_decode_http_body(resp.body))
        except ValueError:
            findings.append(_rpc_finding("response", "major", False,
                                         f"call {i} {method}: response is not valid JSON-RPC", call=i, method=method,
                                         failure_classification="code"))   # incr #3: malformed response -> product defect
            continue
        findings += _check_rpc_result(i, method, call, body.get("result"), body.get("error"))
    return findings


def _build_grpc_invoker(profile: dict, cwd: Optional[str]):
    """Lazily assemble a dynamic gRPC caller from a compiled FileDescriptorSet — imported ONLY here, when a
    grpc rpc deliverable is actually checked, so grpcio/protobuf are never an always-on dependency. Returns a
    callable(method_path, params) -> (result_dict, error). Raises _RpcTransportUnavailable when the machinery
    or the descriptor set is absent. NOTE: the live-server path requires a running gRPC service + grpcio and
    is exercised by integration, not the unit suite (which injects a fake invoker)."""
    try:
        import grpc
        from google.protobuf import descriptor_pb2, descriptor_pool, json_format, message_factory
    except ImportError as exc:
        raise _RpcTransportUnavailable(
            f"grpc transport declared but its machinery is unavailable ({exc}); install grpcio + protobuf "
            f"to verify gRPC services") from exc
    ds_path = profile.get("descriptor_set_path")
    if not ds_path:
        raise _RpcTransportUnavailable(
            "grpc transport requires a descriptor_set_path (a compiled FileDescriptorSet) to invoke methods")
    full = ds_path if (cwd is None or os.path.isabs(ds_path)) else os.path.join(cwd, ds_path)
    try:
        with open(full, "rb") as fh:
            fds = descriptor_pb2.FileDescriptorSet.FromString(fh.read())
    except OSError as exc:
        raise _RpcTransportUnavailable(f"could not read descriptor_set_path '{ds_path}': {exc}") from exc
    pool = descriptor_pool.DescriptorPool()
    for fdp in fds.file:
        pool.Add(fdp)
    factory = message_factory.MessageFactory(pool)
    channel = grpc.insecure_channel(profile["base_url"])

    def invoke(method_path, params):
        service_name, method_name = method_path.rsplit("/", 1)
        method = pool.FindServiceByName(service_name).FindMethodByName(method_name)
        request = json_format.ParseDict(params or {}, factory.GetPrototype(method.input_type)())
        response_cls = factory.GetPrototype(method.output_type)
        call = channel.unary_unary(f"/{service_name}/{method_name}",
                                   request_serializer=lambda m: m.SerializeToString(),
                                   response_deserializer=response_cls.FromString)
        response = call(request, timeout=_HTTP_REQUEST_TIMEOUT_SECONDS)
        return json_format.MessageToDict(response), None

    return invoke


def _grpc_call_findings(profile: dict, cwd: Optional[str], grpc_invoker=None) -> list[dict]:
    """grpc transport: invoke each call dynamically. The invoker is built lazily (or injected for tests); if
    the transport machinery/inputs are unavailable, ONE finding says so — never a silent pass."""
    if grpc_invoker is None:
        try:
            grpc_invoker = _build_grpc_invoker(profile, cwd)
        except _RpcTransportUnavailable as exc:
            return [_rpc_finding("transport_unavailable", "major", False, str(exc), transport="grpc",
                                 failure_classification="infra")]
    findings: list[dict] = []
    for i, call in enumerate(profile.get("calls", [])):
        method = call.get("method")
        try:
            result, error = grpc_invoker(method, call.get("params", {}))
        except Exception as exc:  # noqa: BLE001 — a failed invocation is a finding, not a gate crash
            findings.append(_rpc_finding("request", "major", False,
                                         f"call {i} {method}: invocation failed: {type(exc).__name__}: {exc}",
                                         call=i, method=method,
                                         failure_classification=_failure_classification(error=exc)))
            continue
        findings += _check_rpc_result(i, method, call, result, error)
    return findings


def run_rpc_service_conformance(contract: dict, runner: Runner, *, cwd: Optional[str] = None,
                                http_request=None, grpc_invoker=None, service_launcher=None) -> dict:
    """Black-box RPC checker (ADR-0009 #1). Boots the service and ACTUALLY INVOKES each declared call over the
    declared transport — it is not a static JSON shape check. `json_rpc_http` runs through stdlib HTTP and
    checks the JSON-RPC result/error (not the HTTP status); `grpc` invokes dynamically via a descriptor set
    with grpcio imported lazily, only when a grpc deliverable is checked. A structural defect (bad transport /
    no calls) is reported without booting."""
    profile = (contract.get("conformance") or {}).get("rpc_service")
    if contract.get("deliverable_kind") != "rpc_service" or not profile:
        return {"applicable": False, "passed": True, "findings": [], "checks_run": 0}

    findings = _rpc_profile_findings(profile)
    if findings:
        return {"applicable": True, "passed": False, "findings": findings, "checks_run": 0}

    transport = profile["transport"]
    http_request = http_request or _stdlib_http_request
    checks_run = 0
    handle = None
    try:
        profile = _profile_with_ephemeral_service_port(profile)
        launcher = service_launcher or _launch_service
        handle = launcher(profile["start"]["command"], cwd=cwd, profile=profile)
        checks_run += 1
        if transport == "json_rpc_http":
            readiness = _wait_for_http_readiness(profile, http_request)
            if readiness:
                findings.append(readiness)
            else:
                findings += _json_rpc_call_findings(profile, http_request)
                checks_run += len(profile.get("calls", []))
        else:  # grpc
            findings += _grpc_call_findings(profile, cwd, grpc_invoker)
            checks_run += len(profile.get("calls", []))
    except _ServiceStartError as exc:
        result = exc.result
        cls = _failure_classification(returncode=result.returncode, stdout=result.stdout, stderr=result.stderr)
        findings.append(_rpc_finding(
            "lifecycle", "critical", False,
            f"service start command exited before readiness (exit {result.returncode})",
            command=exc.command, returncode=result.returncode,
            stdout_tail=_normalize(result.stdout)[-800:],
            stderr_tail=_normalize(result.stderr)[-800:],
            failure_classification=cls,
        ))
    except Exception as exc:  # noqa: BLE001 — lifecycle failure is a finding
        findings.append(_rpc_finding("lifecycle", "critical", False,
                                     f"service lifecycle setup failed: {exc}",
                                     error=type(exc).__name__,
                                     failure_classification=_failure_classification(error=exc)))
    finally:
        if handle is not None:
            try:
                handle.stop()
            except Exception as exc:  # noqa: BLE001
                findings.append(_rpc_finding("lifecycle", "critical", False, "service lifecycle cleanup failed",
                                             error=type(exc).__name__,
                                             failure_classification=_failure_classification(error=exc)))

    return {
        "applicable": True,
        "passed": all(f["passed"] for f in findings),
        "findings": findings,
        "checks_run": checks_run,
    }


def run_batch_job_conformance(contract: dict, runner: Runner, *, cwd: Optional[str] = None) -> dict:
    """Black-box batch-job checker (ADR-0009 #1). Runs the declared build/install, runs the job once, asserts
    its exit status (default 0), and asserts each declared produced_artifact exists — the existence probe runs
    THROUGH the runner (`test -e`), so it holds inside the execution sandbox, not only on the host. A broken
    build skips the run (its failure would be derived)."""
    profile = (contract.get("conformance") or {}).get("batch_job")
    if contract.get("deliverable_kind") != "batch_job" or not profile:
        return {"applicable": False, "passed": True, "findings": [], "checks_run": 0}

    findings = install_findings(profile, runner, cwd=cwd)
    checks_run = len(profile.get("build_and_install", {}).get("commands", []))
    if not any(f["check"] == "build_and_install" for f in findings):
        command = (profile.get("run") or {}).get("command") or ""
        expected = profile.get("expected_status", 0)
        res = runner(command, cwd=cwd)
        checks_run += 1
        if res.returncode != expected:
            # incr #3: a job exit-code mismatch is the positive PRODUCT signal (`default="code"`); a could-not-run
            # exit (127/124/137/...) still wins, so an OOM-killed job is not mistaken for a wrong answer.
            cls = _failure_classification(returncode=res.returncode, stdout=res.stdout, stderr=res.stderr,
                                          default="code")
            findings.append(_finding(
                "exit_status", "major", False,
                f"batch job `{command}`: expected exit {expected}, got {res.returncode}",
                command=command, expected=expected, actual=res.returncode, returncode=res.returncode,
                stderr_tail=_normalize(res.stderr)[-800:], failure_classification=cls))
        for art in profile.get("produced_artifacts", []):
            checks_run += 1
            probe = runner(f"test -e {shlex.quote(art)}", cwd=cwd)
            if probe.returncode != 0:
                findings.append(_finding(
                    "produced_artifact", "major", False,
                    f"batch job did not produce the declared artifact '{art}'", artifact=art,
                    failure_classification="code"))   # incr #3: a missing declared output -> product defect

    return {
        "applicable": True,
        "passed": all(f["passed"] for f in findings),
        "findings": findings,
        "checks_run": checks_run,
    }


_SLOT_CHECKERS = {
    "library": run_library_conformance,
    "batch_job": run_batch_job_conformance,
    "undetermined": run_undetermined_conformance,
}


_HTTP_BODY_LIMIT = 8192
_HTTP_REQUEST_TIMEOUT_SECONDS = 2.0
_HTTP_POLL_INTERVAL_SECONDS = 0.05


def _free_loopback_port(host: str) -> int:
    """Reserve an OS-selected TCP port long enough to learn it, then release it for the service under test."""
    bind_host = host
    if host in ("localhost", "127.0.0.1", "::1"):
        bind_host = host
    elif not host:
        bind_host = "127.0.0.1"
    with socket.socket(socket.AF_INET6 if bind_host == "::1" else socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((bind_host, 0))
        return int(sock.getsockname()[1])


def _replace_url_port(url: str, port: int) -> str:
    parsed = urllib.parse.urlsplit(url)
    host = parsed.hostname
    if not host:
        return url
    netloc = f"[{host}]" if ":" in host and not host.startswith("[") else host
    netloc += f":{port}"
    if parsed.username:
        auth = parsed.username
        if parsed.password:
            auth += f":{parsed.password}"
        netloc = f"{auth}@{netloc}"
    return urllib.parse.urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))


def _rewrite_command_port(command: str, old_port: int, new_port: int, old_url: str, new_url: str) -> tuple[str, bool]:
    """Rewrite the declared service command when the nominal port is present as an argument token."""
    old = str(old_port)
    new = str(new_port)
    try:
        parts = shlex.split(command)
    except ValueError:
        return command, False
    changed = False
    rewritten: list[str] = []
    previous = ""
    for part in parts:
        updated = part
        if part == old or (previous in ("--port", "-p") and part == old):
            updated = new
        elif part == old_url:
            updated = new_url
        elif part.startswith("--port=") and part.split("=", 1)[1] == old:
            updated = f"--port={new}"
        elif part.startswith("-p=") and part.split("=", 1)[1] == old:
            updated = f"-p={new}"
        elif part == f":{old}":
            updated = f":{new}"
        if updated != part:
            changed = True
        rewritten.append(updated)
        previous = part
    return (shlex.join(rewritten) if changed else command), changed


def _profile_with_ephemeral_service_port(profile: dict) -> dict:
    """Use a fresh port for local HTTP service checks so a busy nominal contract port cannot fail lifecycle."""
    base_url = str(profile.get("base_url") or "")
    parsed = urllib.parse.urlsplit(base_url)
    if parsed.scheme not in ("http", "https") or parsed.port is None:
        return profile
    host = parsed.hostname or "127.0.0.1"
    if host not in ("localhost", "127.0.0.1", "::1"):
        return profile
    new_port = _free_loopback_port(host)
    if new_port == parsed.port:
        return profile
    new_url = _replace_url_port(base_url, new_port)
    start = dict(profile.get("start") or {})
    command = str(start.get("command") or "")
    new_command, changed = _rewrite_command_port(command, parsed.port, new_port, base_url, new_url)
    if not changed:
        return profile
    start["command"] = new_command
    updated = dict(profile)
    updated["start"] = start
    updated["base_url"] = new_url
    return updated


def _http_finding(check: str, severity: str, passed: bool, detail: str, **extra) -> dict:
    f = {"source": "http-conformance", "check": check, "severity": severity, "passed": passed, "detail": detail}
    f.update(extra)
    return _with_failure_classification(f, passed)


def _decode_http_body(body: bytes, limit: int = _HTTP_BODY_LIMIT) -> str:
    body = body or b""
    clipped = body[:limit]
    text = clipped.decode("utf-8", errors="replace")
    if len(body) > limit:
        text += f"\n<conformance: response body truncated at {limit} bytes>"
    return text


def _read_http_response(resp, limit: int = _HTTP_BODY_LIMIT) -> bytes:
    return resp.read(limit + 1)


def _stdlib_http_request(method: str, url: str, *, json_body=None, has_json_body: bool = False,
                         timeout: Optional[float] = None) -> HttpResponse:
    data = None
    headers = {"Accept": "application/json, text/plain, */*"}
    has_json_body = has_json_body or json_body is not None
    if has_json_body:
        data = json.dumps(json_body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout or _HTTP_REQUEST_TIMEOUT_SECONDS) as resp:
            return HttpResponse(resp.status, _read_http_response(resp))
    except urllib.error.HTTPError as exc:
        return HttpResponse(exc.code, _read_http_response(exc))


def _join_http_url(base_url: str, path: str) -> str:
    return urllib.parse.urljoin(base_url.rstrip("/") + "/", (path or "").lstrip("/"))


def _wait_for_http_readiness(profile: dict, http_request) -> Optional[dict]:
    timeout = float(profile.get("readiness_timeout_seconds", 0))
    deadline = time.monotonic() + max(0.0, timeout)
    last_error = None
    while True:
        remaining = deadline - time.monotonic()
        request_timeout = min(_HTTP_REQUEST_TIMEOUT_SECONDS, max(0.05, remaining if remaining > 0 else 0.05))
        try:
            http_request("GET", profile["base_url"], timeout=request_timeout)
            return None
        except (OSError, TimeoutError, urllib.error.URLError) as exc:
            last_error = exc
            if time.monotonic() >= deadline:
                break
            time.sleep(min(_HTTP_POLL_INTERVAL_SECONDS, max(0.0, deadline - time.monotonic())))
    detail = f"service did not become ready before timeout ({timeout:g}s)"
    return _http_finding("lifecycle", "critical", False, detail, error=type(last_error).__name__,
                         failure_classification=_failure_classification(error=last_error, detail=detail))


def _http_example_findings(profile: dict, http_request) -> list[dict]:
    findings: list[dict] = []
    base_url = profile["base_url"]
    for i, ex in enumerate(profile.get("examples", [])):
        method = ex.get("method", "GET").upper()
        path = ex.get("path", "")
        url = _join_http_url(base_url, path)
        has_json_body = "json" in ex
        try:
            resp = http_request(
                method, url, json_body=ex.get("json"), has_json_body=has_json_body,
                timeout=_HTTP_REQUEST_TIMEOUT_SECONDS,
            )
        except (OSError, TimeoutError, urllib.error.URLError) as exc:
            findings.append(_http_finding(
                "request", "major", False,
                f"example {i} {method} {path}: request failed",
                example=i, method=method, path=path, error=type(exc).__name__,
                failure_classification=_failure_classification(error=exc),
            ))
            continue

        if resp.status != ex.get("expected_status"):
            findings.append(_http_finding(
                "status", "major", False,
                f"example {i} {method} {path}: expected status {ex.get('expected_status')}, got {resp.status}",
                example=i, method=method, path=path, expected=ex.get("expected_status"), actual=resp.status,
                failure_classification="code",        # incr #3 (item #5): the service ANSWERED with the wrong status
            ))

        decoded = None
        for needle in ex.get("expected_body_contains", []):
            if decoded is None:
                decoded = _decode_http_body(resp.body)
            if needle not in decoded:
                findings.append(_http_finding(
                    "body_contains", "major", False,
                    f"example {i} {method} {path}: response body missing expected substring {needle!r}",
                    example=i, method=method, path=path, expected=needle, actual=decoded,
                    failure_classification="code",    # incr #3 (item #5): body-oracle mismatch -> product defect
                ))
    return findings


def _http_profile_findings(profile) -> list[dict]:
    missing: list[str] = []
    if not isinstance(profile, dict):
        missing.append("conformance.http_service")
    else:
        start = profile.get("start")
        if not isinstance(start, dict) or not start.get("command"):
            missing.append("start.command")
        if not profile.get("base_url"):
            missing.append("base_url")
        if "readiness_timeout_seconds" not in profile:
            missing.append("readiness_timeout_seconds")
        examples = profile.get("examples")
        if not isinstance(examples, list) or not examples:
            missing.append("examples")
    if not missing:
        return []
    return [_http_finding(
        "profile", "critical", False,
        "http_service conformance profile is missing required fields",
        missing=missing, failure_classification="code",   # incr #3: a malformed contract is a defect to fix, not a retry
    )]


def run_http_service_conformance(contract: dict, runner: Runner, *, cwd: Optional[str] = None,
                                 http_request=None, service_launcher=None) -> dict:
    """Boot the declared HTTP service, wait for shallow readiness, then compare black-box examples."""
    profile = (contract.get("conformance") or {}).get("http_service")
    if contract.get("deliverable_kind") != "http_service":
        return {"applicable": False, "passed": True, "findings": [], "checks_run": 0}

    http_request = http_request or _stdlib_http_request
    findings: list[dict] = _http_profile_findings(profile)
    checks_run = 0
    if findings:
        return {
            "applicable": True,
            "passed": False,
            "findings": findings,
            "checks_run": checks_run,
        }

    handle = None
    try:
        profile = _profile_with_ephemeral_service_port(profile)
        launcher = service_launcher or _launch_service
        handle = launcher(profile["start"]["command"], cwd=cwd, profile=profile)
        checks_run += 1
        readiness = _wait_for_http_readiness(profile, http_request)
        if readiness:
            findings.append(readiness)
        else:
            example_findings = _http_example_findings(profile, http_request)
            findings.extend(example_findings)
            checks_run += len(profile.get("examples", []))
    except _ServiceStartError as exc:
        result = exc.result
        cls = _failure_classification(returncode=result.returncode, stdout=result.stdout, stderr=result.stderr)
        findings.append(_http_finding(
            "lifecycle", "critical", False,
            f"service start command exited before readiness (exit {result.returncode})",
            command=exc.command, returncode=result.returncode,
            stdout_tail=_normalize(result.stdout)[-800:],
            stderr_tail=_normalize(result.stderr)[-800:],
            failure_classification=cls,
        ))
    except Exception as exc:
        findings.append(_http_finding(
            "lifecycle", "critical", False,
            f"service lifecycle setup failed: {exc}",
            error=type(exc).__name__,
            failure_classification=_failure_classification(error=exc),
        ))
    finally:
        if handle is not None:
            try:
                handle.stop()
            except Exception as exc:
                findings.append(_http_finding(
                    "lifecycle", "critical", False,
                    "service lifecycle cleanup failed",
                    error=type(exc).__name__,
                    failure_classification=_failure_classification(error=exc),
                ))

    return {
        "applicable": True,
        "passed": all(f["passed"] for f in findings),
        "findings": findings,
        "checks_run": checks_run,
    }


# ---------------------------------------------------------------------------------------------------------
# GOAL-LEVEL acceptance gate (ADR-0016 D7 — the COMPOSING-layer WHY check).
#
# The per-leaf checkers above prove only leaf-obeys-contract. A goal whose leaves are all `done` has NOT been
# checked against its OWN outcome. This gate boots the COMPOSED, assembled goal artifact and probes it.
#
# CRITICAL: the determinism lives in an EXECUTABLE `acceptance_profile` FIXED AT INTAKE (the goal contract the
# owner submits/confirms) — NOT in compiling the natural-language `success_condition` into a probe at the end
# (that re-introduces the exact LLM-label-trust the goal-level hole came from). Profile shape:
#   { "start": {"command": str, "base_url"?: str, "ready_path"?: str, "timeout"?: float},
#     "probes": [ {"request": {"method"?, "path"?, "json"?}, "expect": {"status"?, "body_contains"?}} ],
#     "negative_control"?: {"request": {...}, "expect": {...}} }
# The probe is driven ENTIRELY by the goal profile, INDEPENDENT of any leaf's `deliverable_kind`.
#
# It REUSES the per-leaf service-boot helpers (`_launch_service` -> the `_rlimit_preexec` sandbox + killpg
# teardown), so the goal boot has the SAME resource bound + GUARANTEED process teardown as the leaf gate.
_GOAL_ACCEPTANCE_DEFAULT_BASE_URL = "http://127.0.0.1:8000"
_GOAL_ACCEPTANCE_DEFAULT_TIMEOUT = 10.0
_GOAL_EVIDENCE_BODY_LIMIT = 2000


def _as_substrings(value) -> list[str]:
    """Coerce an `expect.body_contains` (a string OR a list of strings) to a list — WITHOUT exploding a bare
    string into characters (list("ab") == ['a','b'] would silently weaken the check)."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value]
    return [str(value)]


def validate_acceptance_profile(profile) -> list[str]:
    """Shape-check a goal `acceptance_profile` (the executable goal contract authored at INTAKE). Returns the
    list of missing/invalid field paths — EMPTY iff the profile is runnable. An ABSENT profile is the caller's
    concern (no goal-level probe = today's shadow behavior), never reaches here."""
    bad: list[str] = []
    if not isinstance(profile, dict):
        return ["acceptance_profile"]
    start = profile.get("start")
    if not isinstance(start, dict) or not str(start.get("command") or "").strip():
        bad.append("start.command")
    probes = profile.get("probes")
    if not isinstance(probes, list) or not probes:
        bad.append("probes")
    else:
        for i, pr in enumerate(probes):
            expect = pr.get("expect") if isinstance(pr, dict) else None
            if not isinstance(expect, dict) or (expect.get("status") is None and not expect.get("body_contains")):
                bad.append(f"probes[{i}].expect")   # an expectation that asserts NOTHING is not a probe
    nc = profile.get("negative_control")
    if nc is not None and (not isinstance(nc, dict) or not isinstance(nc.get("expect"), dict)):
        bad.append("negative_control.expect")
    return bad


def _wait_for_goal_readiness(base_url: str, ready_path, timeout: float, http_request) -> Optional[dict]:
    """Poll the booted artifact until it answers (mirrors `_wait_for_http_readiness`, but on the goal profile's
    own `start.ready_path`/`start.timeout`). Returns None on ready, or a lifecycle finding on timeout."""
    deadline = time.monotonic() + max(0.0, timeout)
    url = _join_http_url(base_url, ready_path) if ready_path else base_url
    last_error = None
    while True:
        remaining = deadline - time.monotonic()
        request_timeout = min(_HTTP_REQUEST_TIMEOUT_SECONDS, max(0.05, remaining if remaining > 0 else 0.05))
        try:
            http_request("GET", url, timeout=request_timeout)
            return None
        except (OSError, TimeoutError, urllib.error.URLError) as exc:
            last_error = exc
            if time.monotonic() >= deadline:
                break
            time.sleep(min(_HTTP_POLL_INTERVAL_SECONDS, max(0.0, deadline - time.monotonic())))
    return _http_finding(
        "lifecycle", "critical", False,
        f"composed goal artifact did not become ready before timeout ({timeout:g}s)",
        error=type(last_error).__name__ if last_error else None,
        failure_classification=_failure_classification(error=last_error),
    )


def _goal_probe_evidence(base_url: str, probe: dict, http_request, *, label: str) -> tuple[dict, list[dict]]:
    """Replay one probe (or the negative control) against the booted artifact. Returns (evidence, findings):
    `evidence` is the captured request+response (durable proof), `findings` is non-empty iff the response does
    NOT satisfy the authored `expect` (status, if named + every `body_contains` substring)."""
    request = probe.get("request") or {}
    expect = probe.get("expect") or {}
    method = str(request.get("method", "GET")).upper()
    path = request.get("path", "")
    url = _join_http_url(base_url, path)
    has_json_body = "json" in request
    want_status = expect.get("status")
    want_body = _as_substrings(expect.get("body_contains"))
    evidence = {"label": label, "method": method, "path": path,
                "expect": {"status": want_status, "body_contains": want_body}}
    findings: list[dict] = []
    try:
        resp = http_request(method, url, json_body=request.get("json"),
                            has_json_body=has_json_body, timeout=_HTTP_REQUEST_TIMEOUT_SECONDS)
    except (OSError, TimeoutError, urllib.error.URLError) as exc:
        evidence.update({"ok": False, "error": type(exc).__name__})
        findings.append(_http_finding(
            "request", "critical", False, f"{label} {method} {path}: request failed",
            error=type(exc).__name__, failure_classification=_failure_classification(error=exc)))
        return evidence, findings
    decoded = _decode_http_body(resp.body)
    evidence.update({"status": resp.status, "body": decoded[:_GOAL_EVIDENCE_BODY_LIMIT]})
    if want_status is not None and resp.status != want_status:
        findings.append(_http_finding(
            "status", "critical", False,
            f"{label} {method} {path}: expected status {want_status}, got {resp.status}",
            expected=want_status, actual=resp.status))
    for needle in want_body:
        if needle not in decoded:
            findings.append(_http_finding(
                "body_contains", "critical", False,
                f"{label} {method} {path}: response body missing expected substring {needle!r}",
                expected=needle, actual=decoded[:_GOAL_EVIDENCE_BODY_LIMIT]))
    evidence["ok"] = not findings
    return evidence, findings


def run_goal_acceptance(profile: dict, composed_repo: str, *, http_request=None,
                        service_launcher=None) -> dict:
    """Boot the COMPOSED, assembled goal artifact (the goal worktree AFTER all leaves have merged, BEFORE the
    final merge-to-main) by running `profile.start.command` in `composed_repo`, wait for readiness, replay each
    `probe` asserting status + body_contains, then GUARANTEE teardown. Driven ENTIRELY by the goal profile —
    INDEPENDENT of any leaf's `deliverable_kind`. Reuses `_launch_service` so the boot runs under the SAME
    rlimit sandbox + killpg teardown as the per-leaf gate; the process is ALWAYS torn down (finally), even on
    probe failure or exception.

    Returns {applicable, verified, probes_run, evidence, findings}. `verified` is True IFF the profile is
    runnable, at least one probe ran, readiness held, every probe satisfied its `expect`, and (if declared) the
    negative control produced its expected red. A declared `negative_control` whose expected red does NOT
    appear means the probe set is a green-only smoke -> NOT verified."""
    http_request = http_request or _stdlib_http_request
    bad = validate_acceptance_profile(profile)
    if bad:
        return {"applicable": True, "verified": False, "probes_run": 0, "evidence": [],
                "findings": [_http_finding("profile", "critical", False,
                             "goal acceptance_profile is missing/invalid required fields", missing=bad)]}
    start = profile["start"]
    base_url = str(start.get("base_url") or _GOAL_ACCEPTANCE_DEFAULT_BASE_URL)
    ready_path = start.get("ready_path")
    timeout = float(start.get("timeout") or _GOAL_ACCEPTANCE_DEFAULT_TIMEOUT)
    findings: list[dict] = []
    evidence: list[dict] = []
    probes_run = 0
    handle = None
    try:
        launcher = service_launcher or _launch_service
        handle = launcher(start["command"], cwd=str(composed_repo),
                          profile={"readiness_timeout_seconds": timeout})
        readiness = _wait_for_goal_readiness(base_url, ready_path, timeout, http_request)
        if readiness:
            findings.append(readiness)
        else:
            for i, probe in enumerate(profile["probes"]):
                ev, fs = _goal_probe_evidence(base_url, probe, http_request, label=f"probe[{i}]")
                evidence.append(ev)
                findings.extend(fs)
                probes_run += 1
            nc = profile.get("negative_control")
            if nc is not None:
                ev, fs = _goal_probe_evidence(base_url, nc, http_request, label="negative_control")
                evidence.append(ev)
                probes_run += 1
                if fs:                               # the control did NOT show its expected red -> smoke-only
                    findings.append(_http_finding(
                        "negative_control", "critical", False,
                        "negative control did not produce its expected (red) response — the probe set is a "
                        "green-only smoke and cannot discriminate the goal WHY", control_findings=fs))
    except _ServiceStartError as exc:
        result = exc.result
        cls = _failure_classification(returncode=result.returncode, stdout=result.stdout, stderr=result.stderr)
        findings.append(_http_finding(
            "lifecycle", "critical", False,
            f"composed goal artifact start command exited before readiness (exit {result.returncode})",
            command=exc.command, returncode=result.returncode,
            stdout_tail=_normalize(result.stdout)[-800:], stderr_tail=_normalize(result.stderr)[-800:],
            failure_classification=cls))
    except Exception as exc:                         # noqa: BLE001 — any boot failure is a non-verified result
        findings.append(_http_finding(
            "lifecycle", "critical", False, f"goal acceptance lifecycle setup failed: {exc}",
            error=type(exc).__name__, failure_classification=_failure_classification(error=exc)))
    finally:
        if handle is not None:                       # GUARANTEE teardown — no leaked process/port, ever
            try:
                handle.stop()
            except Exception as exc:                 # noqa: BLE001
                findings.append(_http_finding(
                    "lifecycle", "critical", False, "goal acceptance lifecycle cleanup failed",
                    error=type(exc).__name__, failure_classification=_failure_classification(error=exc)))
    verified = (not findings) and probes_run > 0
    return {"applicable": True, "verified": verified, "probes_run": probes_run,
            "evidence": evidence, "findings": findings}


# Forbidden-pattern gate (ADR-0009 / ADR-0016 D7): the cheapest, most general deterministic check — grep the
# produced tree for a token that should be gone. It is KIND-AGNOSTIC (a rename/refactor straggler is a defect
# whatever the deliverable is), so it folds into run_conformance alongside the kind-specific checker rather
# than living on one profile. grep is a FACT (~0-FP); this moves a deterministically-catchable defect off the
# expensive semantic reviewer (Linon), which previously had to read the regex to find it.
_FORBIDDEN_SKIP_DIRS = {"node_modules", ".hg", ".svn", ".venv", "venv"}
_FORBIDDEN_BINARY_SNIFF_BYTES = 8192
_FORBIDDEN_MAX_HITS_REPORTED = 5     # cap the file:line evidence so a noisy match cannot flood the finding


def _forbidden_finding(check: str, severity: str, passed: bool, detail: str, **extra) -> dict:
    f = {"source": "forbidden-pattern", "check": check, "severity": severity, "passed": passed, "detail": detail}
    f.update(extra)
    return _with_failure_classification(f, passed)


def _is_probably_binary(path: str) -> bool:
    """Sniff a leading chunk for a NUL byte — the cheap, conventional 'is this binary' heuristic (git uses the
    same). A read error is treated as binary (skip it): the gate never crashes on an unreadable file."""
    try:
        with open(path, "rb") as fh:
            return b"\x00" in fh.read(_FORBIDDEN_BINARY_SNIFF_BYTES)
    except OSError:
        return True


def _is_tree_scan_scratch(rel: str) -> bool:
    rel = rel.replace(os.sep, "/")
    if rel.startswith("./"):
        rel = rel[2:]
    if not rel:
        return False
    if rel == ".agent-runs" or rel.startswith(".agent-runs/") or rel == ".git" or rel.startswith(".git/"):
        return True
    if "__pycache__" in rel.split("/"):
        return True
    if _controller_scope is not None:
        return bool(_controller_scope._is_scratch(rel))
    return False


def _skip_forbidden_scan_rel(rel: str) -> bool:
    rel = rel.replace(os.sep, "/")
    if rel.startswith("./"):
        rel = rel[2:]
    if _is_tree_scan_scratch(rel):
        return True
    return any(part in _FORBIDDEN_SKIP_DIRS for part in rel.split("/"))


def _forbidden_pattern_findings(patterns: list, cwd: Optional[str]) -> list[dict]:
    """For each declared pattern, grep the produced tree at `cwd` (text files only; .git / node_modules / vendor
    dirs and binary files skipped) and count matches in files NOT matching the pattern's `exclude` globs. When
    the count exceeds `max_occurrences` (default 0), emit ONE BLOCKING `forbidden-pattern` finding naming the
    pattern, the count, and up to a few `file:line` hits. A pattern is compiled as a regex; if it is not valid
    regex it is matched as a literal substring (so a bare token like `_scaffold_seed_commit` works either way)."""
    root = cwd or "."
    findings: list[dict] = []
    for spec in patterns:
        if not isinstance(spec, dict):
            continue
        raw = spec.get("pattern")
        if not raw:
            continue
        try:
            rx = re.compile(raw)
        except re.error:
            rx = re.compile(re.escape(raw))            # an invalid regex is a literal token, not a gate crash
        excludes = spec.get("exclude") or []
        max_occ = spec.get("max_occurrences", 0)
        count = 0
        hits: list[str] = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [
                d for d in dirnames
                if not _skip_forbidden_scan_rel(os.path.relpath(os.path.join(dirpath, d), root))
            ]
            for fn in sorted(filenames):
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, root)
                if _skip_forbidden_scan_rel(rel):
                    continue
                if any(fnmatch.fnmatch(rel, g) for g in excludes):
                    continue
                if _is_probably_binary(full):
                    continue
                try:
                    with open(full, encoding="utf-8") as fh:
                        for lineno, line in enumerate(fh, 1):
                            if rx.search(line):
                                count += len(rx.findall(line))
                                if len(hits) < _FORBIDDEN_MAX_HITS_REPORTED:
                                    hits.append(f"{rel}:{lineno}")
                except (OSError, UnicodeDecodeError):
                    continue                           # unreadable / non-utf8 text: skip, never crash the gate
        if count > max_occ:
            reason = spec.get("reason")
            detail = (f"forbidden pattern {raw!r} appears {count} time(s) in the produced tree "
                      f"(allowed at most {max_occ})")
            if reason:
                detail += f" — {reason}"
            if hits:
                detail += "; e.g. " + ", ".join(hits)
            # ACTIONABLE fix_hint (ADR-0016): a "dumb" implementer told only "X failed" repeats the mistake on
            # repair, so the BLOCKING finding carries a concrete WHAT+WHERE remediation — the hit locations are
            # already captured, so the fix is mechanically known (never vague filler).
            fix_hint = f"Remove or rename all {count} occurrence(s) of `{raw}` in the produced tree"
            if hits:
                shown = ", ".join(hits)
                more = "" if count <= len(hits) else f" (and {count - len(hits)} more)"
                fix_hint += f" at {shown}{more}"
            fix_hint += "."
            if reason:
                fix_hint += f" {reason}."
            findings.append(_forbidden_finding(
                "forbidden_pattern", "major", False, detail,
                pattern=raw, count=count, max_occurrences=max_occ, hits=hits, fix_hint=fix_hint,
                failure_classification="code"))   # incr #3: a grep hit in delivered source is a fact -> product defect
    return findings


def run_forbidden_patterns(contract: dict, *, cwd: Optional[str] = None) -> dict:
    """The kind-agnostic forbidden-pattern gate. Applicable only when the contract declares a non-empty
    `forbidden_patterns` list — otherwise a vacuous pass (no finding, no false positive). Runs NO process: it
    only reads the produced files, so it executes nothing untrusted and needs no runner."""
    patterns = contract.get("forbidden_patterns")
    if not isinstance(patterns, list) or not patterns:
        return {"applicable": False, "passed": True, "findings": [], "checks_run": 0}
    findings = _forbidden_pattern_findings(patterns, cwd)
    return {
        "applicable": True,
        "passed": all(f["passed"] for f in findings),
        "findings": findings,
        "checks_run": len(patterns),
    }


# Regression-suite gate (ADR-0009 / ADR-0016): the cheapest catch for the BIGGEST modification-defect class —
# "the change broke previously-working code" (SWE-CI: ~73.6% of modification-task failures). The contract names
# a PRE-EXISTING repo test command that must still pass (exit 0) after the change; the gate re-runs it through
# the conformance runner. A green suite is a FACT (~0-FP — it either passes or it doesn't), so this moves a
# deterministically-catchable regression off the expensive semantic reviewer (Linon). Like forbidden_patterns
# it is KIND-AGNOSTIC and folds into run_conformance alongside the kind-specific checker.
_REGRESSION_DEFAULT_TIMEOUT_SECONDS = 300
_REGRESSION_TAIL_CHARS = 1200          # bound the captured failure output so a chatty suite cannot flood the finding


def _regression_finding(check: str, severity: str, passed: bool, detail: str, **extra) -> dict:
    f = {"source": "regression", "check": check, "severity": severity, "passed": passed, "detail": detail}
    f.update(extra)
    return _with_failure_classification(f, passed)


# Failing-test extraction for the actionable fix_hint: pytest (`FAILED path::test` / `path::test FAILED`) and
# unittest (`FAIL: test (Case)` / `ERROR: ...`) are the common forms. Best-effort and bounded — naming the
# broken test makes the remediation concrete; an unparseable suite simply omits the names, never guesses.
_FAILING_TEST_RE = re.compile(
    r"FAILED\s+(\S+::\S+|\S+)"          # pytest:   FAILED tests/x.py::test_a
    r"|(\S+::\S+)\s+FAILED"             # pytest:   tests/x.py::test_a FAILED
    r"|(?:FAIL|ERROR):\s+(\S+)"         # unittest: FAIL: test_a (mod.Case)
)
_FAILING_TESTS_REPORTED = 5


def _parse_failing_tests(stdout, stderr) -> list:
    """Pull up to a few failing test identifiers out of the suite's output for the fix_hint. Defensive:
    coerces non-string output and never raises (a garbled result yields []), preserving first-seen order."""
    names: list[str] = []
    seen: set[str] = set()
    for val in (stdout, stderr):
        try:
            text = val if isinstance(val, str) else ("" if val is None else str(val))
        except Exception:
            continue
        for m in _FAILING_TEST_RE.finditer(text):
            name = next((g for g in m.groups() if g), None)
            if name and name not in seen:
                seen.add(name)
                names.append(name)
                if len(names) >= _FAILING_TESTS_REPORTED:
                    return names
    return names


def _regression_output_tail(stdout, stderr, limit: int = _REGRESSION_TAIL_CHARS) -> str:
    """Build a bounded tail from the suite's output (the failing test names usually live here). Defensive on
    purpose: coerces non-string / None / missing output and never raises, so a garbled runner result yields an
    empty tail rather than crashing the gate (acceptance (d))."""
    parts: list[str] = []
    for label, val in (("stdout", stdout), ("stderr", stderr)):
        try:
            text = val if isinstance(val, str) else ("" if val is None else str(val))
            text = _normalize(text)
        except Exception:
            text = ""
        if text:
            parts.append(f"{label}: {text[-limit:]}")
    return " | ".join(parts)


def run_regression_suite(contract: dict, runner: Runner, *, cwd: Optional[str] = None) -> dict:
    """The kind-agnostic regression-suite gate. Applicable only when the contract declares a `regression_suite`
    with a non-empty `command` — otherwise a vacuous pass (no finding, no false positive). Runs the declared
    pre-existing test command THROUGH the runner (the box boundary) against the produced artifact; a non-zero
    exit emits ONE BLOCKING `regression` finding capturing the command, exit code, declared timeout, and an
    output tail. The `timeout_seconds` budget is passed to the runner so a hung suite cannot stall the gate."""
    suite = contract.get("regression_suite")
    if not isinstance(suite, dict):
        return {"applicable": False, "passed": True, "findings": [], "checks_run": 0}
    command = suite.get("command")
    if not isinstance(command, str) or not command.strip():
        return {"applicable": False, "passed": True, "findings": [], "checks_run": 0}

    timeout = suite.get("timeout_seconds", _REGRESSION_DEFAULT_TIMEOUT_SECONDS)
    try:
        res = runner(command, cwd=cwd, timeout=timeout)
    except Exception as exc:  # noqa: BLE001 - runner failures are classified findings, not product regressions.
        detail = f"regression suite `{command}` could not run: {type(exc).__name__}: {exc}"
        return {"applicable": True, "passed": False,
                "findings": [_regression_finding(
                    "regression_suite", "major", False, detail,
                    command=command, exit_code=None, timeout_seconds=timeout, output_tail="",
                    failure_classification=_failure_classification(error=exc, detail=detail))],
                "checks_run": 1}
    returncode = getattr(res, "returncode", None)              # garbled result -> None != 0 -> blocks, never crashes
    stdout = getattr(res, "stdout", "")
    stderr = getattr(res, "stderr", "")

    findings: list[dict] = []
    if returncode != 0:
        tail = _regression_output_tail(stdout, stderr)
        # incr #3: a once-green suite that now exits nonzero is a real regression (`default="code"`); a
        # could-not-run exit (missing pytest=127, timeout=124, OOM=137, ...) is still classified could-not-run.
        cls = _failure_classification(returncode=returncode, stdout=stdout, stderr=stderr, default="code")
        if cls == "code":
            detail = (f"regression suite `{command}`: expected exit 0, got {returncode} — "
                      "the change broke previously-working code")
        else:
            detail = f"regression suite `{command}` could not run cleanly (exit {returncode})"
        reason = suite.get("reason")
        if reason:
            detail += f" — {reason}"
        if tail:
            detail += f"; output tail: {tail}"
        # ACTIONABLE fix_hint (ADR-0016): tell the implementer exactly WHAT broke and WHAT NOT to do, so a
        # repair does not just re-run the same logic or (worse) edit the tests to pass. The command + exit code
        # are mechanically known; the failing test name(s) are added when parseable from the output.
        extra = {
            "command": command, "exit_code": returncode, "timeout_seconds": timeout, "output_tail": tail,
            "failure_classification": cls,
        }
        if cls == "code":
            failing = _parse_failing_tests(stdout, stderr)
            fix_hint = (f"Your change broke the pre-existing suite `{command}` (exit {returncode}). "
                        "Inspect the failing output below; revert or correct the logic affecting it — "
                        "do not modify the tests to pass.")
            if failing:
                fix_hint += " Failing: " + ", ".join(failing) + "."
            extra["fix_hint"] = fix_hint
        findings.append(_regression_finding("regression_suite", "major", False, detail, **extra))
    return {
        "applicable": True,
        "passed": all(f["passed"] for f in findings),
        "findings": findings,
        "checks_run": 1,
    }


# Static-checks gate (ADR-0009 / ADR-0016): the cheapest catch for the HALLUCINATION / INCONSISTENCY defect
# class — invented/undefined APIs (~26%), undefined variables (~16.9%), syntax errors. The contract declares a
# LIST of static analyzers (py_compile, pyflakes, ruff, tsc --noEmit, ...), each of which must exit 0 on the
# produced artifact; the gate re-runs each one through the conformance runner. An analyzer's exit code is a FACT
# (~0-FP — it either resolves/parses or it doesn't), so this moves a deterministically-catchable hallucination
# off the expensive semantic reviewer (Linon). Like regression_suite / forbidden_patterns it is KIND-AGNOSTIC
# and folds into run_conformance alongside the kind-specific checker.
_STATIC_CHECK_DEFAULT_TIMEOUT_SECONDS = 120
_STATIC_CHECK_TAIL_CHARS = 1200        # bound the captured analyzer output so a chatty linter cannot flood the finding


def _static_check_finding(check: str, severity: str, passed: bool, detail: str, **extra) -> dict:
    f = {"source": "static-check", "check": check, "severity": severity, "passed": passed, "detail": detail}
    f.update(extra)
    return _with_failure_classification(f, passed)


def _static_check_output_tail(stdout, stderr, limit: int = _STATIC_CHECK_TAIL_CHARS) -> str:
    """Build a bounded tail from the analyzer's output (the reported undefined-name / import / syntax errors live
    here). Defensive on purpose: coerces non-string / None / missing output and never raises, so a garbled runner
    result yields an empty tail rather than crashing the gate."""
    parts: list[str] = []
    for label, val in (("stdout", stdout), ("stderr", stderr)):
        try:
            text = val if isinstance(val, str) else ("" if val is None else str(val))
            text = _normalize(text)
        except Exception:
            text = ""
        if text:
            parts.append(f"{label}: {text[-limit:]}")
    return " | ".join(parts)


def run_static_checks(contract: dict, runner: Runner, *, cwd: Optional[str] = None) -> dict:
    """The kind-agnostic static-analysis gate. Applicable only when the contract declares a non-empty
    `static_checks` LIST with at least one well-formed `{ command }` entry — otherwise a vacuous pass (no finding,
    no false positive). Runs EACH declared analyzer THROUGH the runner (the box boundary) against the produced
    artifact; ANY non-zero exit emits ONE BLOCKING `static-check` finding for that command, capturing the command,
    exit code, declared timeout, and an output tail (the analyzer's reported errors). One finding per failing
    analyzer, so several failures all surface and the failing one is always named. Each check's `timeout_seconds`
    budget is passed to the runner so a hung analyzer cannot stall the gate."""
    checks = contract.get("static_checks")
    if not isinstance(checks, list):
        return {"applicable": False, "passed": True, "findings": [], "checks_run": 0}
    runnable = [c for c in checks
                if isinstance(c, dict) and isinstance(c.get("command"), str) and c["command"].strip()]
    if not runnable:
        return {"applicable": False, "passed": True, "findings": [], "checks_run": 0}

    findings: list[dict] = []
    for check in runnable:
        command = check["command"]
        timeout = check.get("timeout_seconds", _STATIC_CHECK_DEFAULT_TIMEOUT_SECONDS)
        try:
            res = runner(command, cwd=cwd, timeout=timeout)
        except Exception as exc:  # noqa: BLE001 - analyzer runner failures are infra/resource findings.
            detail = f"static check `{command}` could not run: {type(exc).__name__}: {exc}"
            findings.append(_static_check_finding(
                "static_checks", "major", False, detail,
                command=command, exit_code=None, timeout_seconds=timeout, output_tail="",
                failure_classification=_failure_classification(error=exc, detail=detail)))
            continue
        returncode = getattr(res, "returncode", None)          # garbled result -> None != 0 -> blocks, never crashes
        if returncode == 0:
            continue
        stdout = getattr(res, "stdout", "")
        stderr = getattr(res, "stderr", "")
        tail = _static_check_output_tail(stdout, stderr)
        # incr #3: an analyzer that exits nonzero reported real errors in the artifact (`default="code"`); a
        # could-not-run exit (analyzer not installed=127, timeout=124, ...) stays could-not-run.
        cls = _failure_classification(returncode=returncode, stdout=stdout, stderr=stderr, default="code")
        if cls == "code":
            detail = (f"static check `{command}`: expected exit 0, got {returncode} — the analyzer reported errors in "
                      "the produced artifact (undefined names / unresolved imports / syntax)")
        else:
            detail = f"static check `{command}` could not run cleanly (exit {returncode})"
        reason = check.get("reason")
        if reason:
            detail += f" — {reason}"
        if tail:
            detail += f"; output tail: {tail}"
        # ACTIONABLE fix_hint (ADR-0016): tell the implementer exactly WHICH analyzer failed and WHAT NOT to do,
        # so a repair fixes the real defect instead of suppressing the linter. The command + exit code are
        # mechanically known facts, so the hint is concrete (never vague filler).
        extra = {
            "command": command, "exit_code": returncode, "timeout_seconds": timeout, "output_tail": tail,
            "failure_classification": cls,
        }
        if cls == "code":
            extra["fix_hint"] = (f"Static check `{command}` failed (exit {returncode}) — fix the reported errors "
                                 "(undefined names / unresolved imports / syntax), do not silence the analyzer.")
        findings.append(_static_check_finding("static_checks", "major", False, detail, **extra))
    return {
        "applicable": True,
        "passed": all(f["passed"] for f in findings),
        "findings": findings,
        "checks_run": len(runnable),
    }


def _merge_reports(base: dict, extra: dict) -> dict:
    """Fold a secondary gate's report into the kind-specific one: union the findings, AND the pass, sum the
    checks, and mark applicable if EITHER ran. Lets the kind-agnostic forbidden-pattern gate ride alongside
    the per-kind checker without a parallel call site or a second routing path."""
    if not extra.get("applicable"):
        return base
    merged = dict(base)
    merged["applicable"] = True
    merged["findings"] = list(base.get("findings") or []) + list(extra.get("findings") or [])
    merged["passed"] = bool(base.get("passed", True)) and bool(extra.get("passed", True))
    merged["checks_run"] = int(base.get("checks_run", 0)) + int(extra.get("checks_run", 0))
    return merged


def run_conformance(contract: dict, runner: Runner, *, cwd: Optional[str] = None, http_request=None) -> dict:
    """Dispatch the conformance gate on `deliverable_kind`. CLI is the one real checker today; the other kinds
    route to their empty slot (recognized, unchecked). A contract with no kind / no profile is not applicable.
    This is the single entry point the pipeline calls, so adding a real checker later is a one-line wiring in
    `_SLOT_CHECKERS` (or the `cli` branch), not a change to the gate's call site.

    The kind-agnostic gates ALWAYS fold in afterward when their fields are declared, so they block on ANY
    deliverable_kind (including `none`), not just CLI: the forbidden-pattern gate (ADR-0016 D7) when the
    contract declares `forbidden_patterns` (an incomplete rename's stragglers), the regression-suite gate when
    it declares `regression_suite` (the change broke previously-working code — SWE-CI's biggest class), and the
    static-checks gate when it declares `static_checks` (the hallucination/inconsistency class — undefined APIs,
    undefined variables, syntax errors)."""
    base = _dispatch_kind_conformance(contract, runner, cwd=cwd, http_request=http_request)
    base = _merge_reports(base, run_forbidden_patterns(contract, cwd=cwd))
    base = _merge_reports(base, run_regression_suite(contract, runner, cwd=cwd))
    return _merge_reports(base, run_static_checks(contract, runner, cwd=cwd))


def _dispatch_kind_conformance(contract: dict, runner: Runner, *, cwd: Optional[str] = None,
                               http_request=None) -> dict:
    kind = contract.get("deliverable_kind")
    if kind == "cli":
        return run_cli_conformance(contract, runner, cwd=cwd)
    if kind == "http_service":
        return run_http_service_conformance(contract, runner, cwd=cwd, http_request=http_request)
    if kind == "json":
        return run_json_conformance(contract, cwd=cwd)
    if kind == "rpc_service":
        return run_rpc_service_conformance(contract, runner, cwd=cwd, http_request=http_request)
    checker = _SLOT_CHECKERS.get(kind)
    if checker is not None:
        return checker(contract, runner, cwd=cwd)
    return {"applicable": False, "passed": True, "findings": [], "checks_run": 0}


def _cap_output(text: str, limit: int) -> str:
    text = text or ""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n<conformance: output truncated at {limit} bytes>"


class _ServiceStartError(RuntimeError):
    def __init__(self, command: str, result: RunResult):
        super().__init__(f"service start command exited before readiness (exit {result.returncode})")
        self.command = command
        self.result = result


class _ProcessOutputCapture:
    def __init__(self, max_output: int):
        import threading

        self.max_output = max_output
        self.stdout = ""
        self.stderr = ""
        self._lock = threading.Lock()
        self._threads = []

    def watch(self, stream, attr: str) -> None:
        import threading

        if stream is None:
            return
        thread = threading.Thread(target=self._read_stream, args=(stream, attr), daemon=True)
        thread.start()
        self._threads.append(thread)

    def snapshot(self) -> tuple[str, str]:
        with self._lock:
            return self.stdout, self.stderr

    def join(self, timeout: float = 0.2) -> None:
        for thread in self._threads:
            thread.join(timeout=timeout)

    def _read_stream(self, stream, attr: str) -> None:
        try:
            while True:
                chunk = stream.read(4096)
                if not chunk:
                    break
                self._append(attr, chunk)
        except (OSError, ValueError):
            pass
        finally:
            try:
                stream.close()
            except OSError:
                pass

    def _append(self, attr: str, chunk: str) -> None:
        if not chunk:
            return
        marker = "<conformance: output truncated"
        with self._lock:
            current = getattr(self, attr)
            if marker in current:
                return
            setattr(self, attr, _cap_output(current + chunk, self.max_output))


class _ServiceProcessHandle:
    def __init__(self, proc, output: _ProcessOutputCapture):
        self.proc = proc
        self._output = output

    def result(self) -> RunResult:
        self._output.join(timeout=0.2)
        stdout, stderr = self._output.snapshot()
        return RunResult(self.proc.poll(), stdout, stderr)

    def stop(self, timeout: float = 3.0) -> RunResult:
        import subprocess

        if self.proc.poll() is None:
            self._terminate()
            try:
                self.proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self._kill()
                self.proc.wait(timeout=timeout)
        self._output.join(timeout=0.5)
        return self.result()

    def _terminate(self) -> None:
        try:
            if os.name == "posix":
                os.killpg(self.proc.pid, signal.SIGTERM)
            else:
                self.proc.terminate()
        except ProcessLookupError:
            pass

    def _kill(self) -> None:
        try:
            if os.name == "posix":
                os.killpg(self.proc.pid, signal.SIGKILL)
            else:
                self.proc.kill()
        except ProcessLookupError:
            pass


def _launch_service(command: str, *, cwd: Optional[str] = None, profile: Optional[dict] = None,
                    max_output: int = 1_000_000) -> _ServiceProcessHandle:
    """Start a declared service command as a long-lived background process.

    The normal runner is intentionally run-to-completion; service conformance needs a separate lifecycle
    helper that can keep the process alive while readiness and black-box calls execute.
    """
    import subprocess

    profile = profile or {}
    readiness_timeout = float(profile.get("readiness_timeout_seconds", 0) or 0)
    probe_seconds = min(0.25, max(0.05, readiness_timeout / 10 if readiness_timeout else 0.1))
    preexec = _rlimit_preexec(512 * 1024 * 1024, 60 * 60, 50 * 1024 * 1024) if os.name == "posix" else None
    proc = subprocess.Popen(
        command, shell=True, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        errors="replace", preexec_fn=preexec, start_new_session=(os.name == "posix"),
    )
    output = _ProcessOutputCapture(max_output)
    output.watch(proc.stdout, "stdout")
    output.watch(proc.stderr, "stderr")
    handle = _ServiceProcessHandle(proc, output)
    time.sleep(probe_seconds)
    if proc.poll() is not None:
        raise _ServiceStartError(command, handle.result())
    return handle


def _rlimit_preexec(mem_bytes: int, cpu_seconds: int, fsize_bytes: int):
    """ADR-0009 #2 (codex-side resource bound): best-effort soft rlimits on the child that runs the UNTRUSTED
    built artifact — address space, CPU seconds, file size, no core dumps. Each is wrapped because a given
    limit may be unsupported/clamped on a platform (notably RLIMIT_AS on macOS); the box's cgroups (ADR-0022)
    are the authoritative enforcement, this is defence-in-depth so the gate's own run cannot exhaust the host."""
    import resource

    def _apply():
        for res, soft, hard in (
            (getattr(resource, "RLIMIT_AS", None), mem_bytes, mem_bytes),
            (getattr(resource, "RLIMIT_CPU", None), cpu_seconds, cpu_seconds + 1),
            (getattr(resource, "RLIMIT_FSIZE", None), fsize_bytes, fsize_bytes),
            (getattr(resource, "RLIMIT_CORE", None), 0, 0),
        ):
            if res is None:
                continue
            try:
                resource.setrlimit(res, (soft, hard))
            except (ValueError, OSError):
                pass

    return _apply


def subprocess_runner(timeout: float = 60.0, *, mem_bytes: int = 512 * 1024 * 1024,
                      max_output: int = 1_000_000, fsize_bytes: int = 50 * 1024 * 1024) -> Runner:
    """A real runner for in-box execution. NOT used in unit tests (those inject a fake). Runs the command
    through the shell because invocations are declared as shell strings; the box is the containment boundary
    (ADR-0022), so shell execution is acceptable *there* and only there.

    The run is RESOURCE-BOUNDED (ADR-0009 #2 defence-in-depth): a wall-clock timeout, soft rlimits on memory /
    CPU / file size (POSIX, best-effort), and a cap on captured output — so an artifact that loops, leaks
    memory, or floods stdout cannot exhaust the host running the gate. The authoritative isolation is still
    the box's cgroups; this protects the gate even outside the box (e.g. the local single-host simulation)."""
    import subprocess

    default_timeout = timeout
    default_preexec = _rlimit_preexec(mem_bytes, int(default_timeout) + 1, fsize_bytes) if os.name == "posix" else None

    def _run(cmd: str, *, cwd: Optional[str] = None, stdin: Optional[str] = None,
             timeout: Optional[float] = None) -> RunResult:
        # A per-call `timeout` override (e.g. a regression_suite's `timeout_seconds`) wins over the baked-in
        # default; the CPU rlimit is recomputed to match so the override is actually honored, not just the
        # wall-clock. None -> the runner's default budget (backward compatible with existing callers).
        effective = default_timeout if timeout is None else timeout
        preexec = default_preexec if timeout is None else (
            _rlimit_preexec(mem_bytes, int(effective) + 1, fsize_bytes) if os.name == "posix" else None)
        try:
            proc = subprocess.run(
                cmd, shell=True, cwd=cwd, input=stdin, capture_output=True, text=True, timeout=effective,
                preexec_fn=preexec,
            )
            return RunResult(proc.returncode, _cap_output(proc.stdout, max_output),
                             _cap_output(proc.stderr, max_output))
        except subprocess.TimeoutExpired as exc:
            out = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            err = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
            return RunResult(124, _cap_output(out, max_output),
                             _cap_output(err, max_output) + f"\n<conformance: timeout after {effective}s>")

    return _run
