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

import json
import os
import re
import shlex
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, NamedTuple, Optional


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


def _finding(check: str, severity: str, passed: bool, detail: str, **extra) -> dict:
    f = {"source": "cli-conformance", "check": check, "severity": severity, "passed": passed, "detail": detail}
    f.update(extra)
    return f


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
            findings.append(_finding(
                "build_and_install", "critical", False,
                f"install command failed (exit {res.returncode}): {cmd}",
                command=cmd, returncode=res.returncode,
                stderr_tail=_normalize(res.stderr)[-800:],
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
            findings.append(_finding(
                "exit_status", "major", False,
                f"example {i} `{cmd}`: expected exit {ex['expected_status']}, got {res.returncode}",
                example=i, command=cmd, expected=ex["expected_status"], actual=res.returncode,
                stderr_tail=_normalize(res.stderr)[-800:],
            ))

        for needle in ex.get("expected_stdout_contains", []):
            if needle not in res.stdout:
                findings.append(_finding(
                    "stdout_contains", "major", False,
                    f"example {i} `{cmd}`: stdout missing expected substring {needle!r}",
                    example=i, command=cmd, expected=needle,
                    actual=_normalize(res.stdout)[-800:],
                ))

        for needle in ex.get("expected_stderr_contains", []):
            if needle not in res.stderr:
                findings.append(_finding(
                    "stderr_contains", "major", False,
                    f"example {i} `{cmd}`: stderr missing expected substring {needle!r}",
                    example=i, command=cmd, expected=needle,
                    actual=_normalize(res.stderr)[-800:],
                ))

        if "expected_stdout" in ex:
            want, got = _normalize(ex["expected_stdout"]), _normalize(res.stdout)
            if want != got:
                findings.append(_finding(
                    "stdout_exact", "major", False,
                    f"example {i} `{cmd}`: normalized stdout did not match",
                    example=i, command=cmd, expected=want, actual=got,
                ))
    return findings


_SCRIPT_EXTS = (".py", ".js", ".ts", ".mjs", ".cjs", ".sh", ".rb", ".pl")


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
            artifact=missing))
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
# Each slot below exists so a future checker drops in here; until then it returns an HONEST no-op that is
# still VISIBLE on the stream (recognized-but-unchecked), never a silent pass. `cli`, `http_service`, and
# `library` are now real checkers; `rpc_service` and `batch_job` remain slots.
_SLOT_KINDS = ("rpc_service", "batch_job")


def _empty_slot(kind: str) -> dict:
    """The report for a recognized deliverable kind whose checker is not built yet. applicable=False so it
    blocks nothing and folds no findings; `slot` marks it so the gate can STREAM that the kind was recognized
    but not checked (no silent cap)."""
    return {"applicable": False, "passed": True, "findings": [], "checks_run": 0,
            "slot": kind, "status": "no-checker-yet"}


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
        return [_finding(
            "library_probe", "critical", False,
            f"could not introspect the library: the import probe produced no result (exit {res.returncode})",
            module=module, stderr_tail=_normalize(res.stderr)[-800:])]
    try:
        data = json.loads(line[len(_LIBRARY_PROBE_MARKER):])
    except ValueError:
        return [_finding("library_probe", "critical", False,
                         "the import probe emitted an unparseable result", module=module)]
    if data.get("import_error"):
        return [_finding("library_import", "critical", False,
                         f"module {module!r} failed to import: {data['import_error']}", module=module)]
    return [_finding(
        "exported_symbol", "major", False,
        f"declared export {sym!r} does not resolve on module {module!r}", module=module, symbol=sym)
        for sym in data.get("missing", [])]


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
        python = profile.get("python") or "python3"
        probe = _library_probe_source(module, import_paths, symbols)
        res = runner(f"{python} -c {shlex.quote(probe)}", cwd=cwd)
        checks_run += 1
        findings += _library_probe_findings(res, module, symbols)

    return {
        "applicable": True,
        "passed": all(f["passed"] for f in findings),
        "findings": findings,
        "checks_run": checks_run,
    }


def run_rpc_service_conformance(contract: dict, runner: Runner, *, cwd: Optional[str] = None) -> dict:
    """SLOT (ADR-0009 #1). Fill with: boot the service, drive it over the RPC/message transport, Buf-style
    backwards-compatibility checks against the baseline."""
    return _empty_slot("rpc_service")


def run_batch_job_conformance(contract: dict, runner: Runner, *, cwd: Optional[str] = None) -> dict:
    """SLOT (ADR-0009 #1). Closest to the CLI checker: run the job, assert exit status + produced_artifacts
    against declared expectations. Likely reuses most of run_cli_conformance when filled."""
    return _empty_slot("batch_job")


_SLOT_CHECKERS = {
    "library": run_library_conformance,
    "rpc_service": run_rpc_service_conformance,
    "batch_job": run_batch_job_conformance,
}


_HTTP_BODY_LIMIT = 8192
_HTTP_REQUEST_TIMEOUT_SECONDS = 2.0
_HTTP_POLL_INTERVAL_SECONDS = 0.05


def _http_finding(check: str, severity: str, passed: bool, detail: str, **extra) -> dict:
    f = {"source": "http-conformance", "check": check, "severity": severity, "passed": passed, "detail": detail}
    f.update(extra)
    return f


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
    return _http_finding("lifecycle", "critical", False, detail, error=type(last_error).__name__)


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
            ))
            continue

        if resp.status != ex.get("expected_status"):
            findings.append(_http_finding(
                "status", "major", False,
                f"example {i} {method} {path}: expected status {ex.get('expected_status')}, got {resp.status}",
                example=i, method=method, path=path, expected=ex.get("expected_status"), actual=resp.status,
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
        missing=missing,
    )]


def run_http_service_conformance(contract: dict, runner: Runner, *, cwd: Optional[str] = None,
                                 http_request=None) -> dict:
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
        start = getattr(runner, "start")
        handle = start(profile["start"]["command"], cwd=cwd)
        checks_run += 1
        readiness = _wait_for_http_readiness(profile, http_request)
        if readiness:
            findings.append(readiness)
        else:
            example_findings = _http_example_findings(profile, http_request)
            findings.extend(example_findings)
            checks_run += len(profile.get("examples", []))
    except Exception as exc:
        findings.append(_http_finding(
            "lifecycle", "critical", False,
            "service lifecycle setup failed",
            error=type(exc).__name__,
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
                ))

    return {
        "applicable": True,
        "passed": all(f["passed"] for f in findings),
        "findings": findings,
        "checks_run": checks_run,
    }


def run_conformance(contract: dict, runner: Runner, *, cwd: Optional[str] = None, http_request=None) -> dict:
    """Dispatch the conformance gate on `deliverable_kind`. CLI is the one real checker today; the other kinds
    route to their empty slot (recognized, unchecked). A contract with no kind / no profile is not applicable.
    This is the single entry point the pipeline calls, so adding a real checker later is a one-line wiring in
    `_SLOT_CHECKERS` (or the `cli` branch), not a change to the gate's call site."""
    kind = contract.get("deliverable_kind")
    if kind == "cli":
        return run_cli_conformance(contract, runner, cwd=cwd)
    if kind == "http_service":
        return run_http_service_conformance(contract, runner, cwd=cwd, http_request=http_request)
    checker = _SLOT_CHECKERS.get(kind)
    if checker is not None:
        return checker(contract, runner, cwd=cwd)
    return {"applicable": False, "passed": True, "findings": [], "checks_run": 0}


def _cap_output(text: str, limit: int) -> str:
    text = text or ""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n<conformance: output truncated at {limit} bytes>"


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

    preexec = _rlimit_preexec(mem_bytes, int(timeout) + 1, fsize_bytes) if os.name == "posix" else None

    def _run(cmd: str, *, cwd: Optional[str] = None, stdin: Optional[str] = None) -> RunResult:
        try:
            proc = subprocess.run(
                cmd, shell=True, cwd=cwd, input=stdin, capture_output=True, text=True, timeout=timeout,
                preexec_fn=preexec,
            )
            return RunResult(proc.returncode, _cap_output(proc.stdout, max_output),
                             _cap_output(proc.stderr, max_output))
        except subprocess.TimeoutExpired as exc:
            out = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            err = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
            return RunResult(124, _cap_output(out, max_output),
                             _cap_output(err, max_output) + f"\n<conformance: timeout after {timeout}s>")

    return _run
