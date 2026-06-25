#!/usr/bin/env python3
"""Tests for the black-box CLI conformance checker (ADR-0009 investment #1).

The checker is the first *dynamic* gate: it re-runs the built artifact against the contract's declared
examples instead of trusting the implementer's self-report. These tests use a **fake runner** keyed by the
exact command string, so they verify the comparison logic deterministically without executing anything. The
last test validates the contract schema itself (the CLI profile is required exactly when the deliverable is
a CLI, and existing profile-less contracts still validate — backward compatible / shadow-first)."""
from __future__ import annotations

import json
import os
import shlex
import shutil
import socket
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "codex-org-bootstrap" / "src"))
import conformance as conf
import controller_pipeline as cp
import runner_factory

REPO = Path(__file__).resolve().parents[1]
SCHEMA = REPO / "schemas" / "implementation-contract.schema.json"


def fake_runner(table):
    """A runner that returns a canned RunResult per command. Unknown commands surface as a loud failure so a
    test never silently passes against a command it did not stub."""
    def _run(cmd, *, cwd=None, stdin=None, timeout=None):
        if cmd not in table:
            raise AssertionError(f"fake_runner: unstubbed command {cmd!r} (stubbed: {sorted(table)})")
        return table[cmd]
    return _run


R = conf.RunResult


def _cli_contract(profile):
    return {"role_id": "aufheben-designer", "deliverable_kind": "cli", "conformance": {"cli": profile}}


def test_missing_artifact_is_one_clear_finding_not_example_spam():
    # surfaced by a live run: the implementer wrote no file, so `python3 jsonpick.py` returned file-not-found
    # for every example. The gate must say "artifact_missing" ONCE, not emit N derived example failures.
    import tempfile
    d = tempfile.mkdtemp(prefix="conf-missing-")        # empty workspace, no jsonpick.py
    profile = {
        "entrypoint": {"invocation": "python3 jsonpick.py"},
        "examples": [{"invocation": "--help", "expected_status": 0},
                     {"invocation": "", "expected_status": 2},
                     {"invocation": "a.b", "expected_status": 0}],
    }
    def runner(cmd, *, cwd=None, stdin=None):                # would return 2 (file-not-found) for everything
        return R(2, "", "can't open file 'jsonpick.py'")
    rep = conf.run_cli_conformance(_cli_contract(profile), runner, cwd=d)
    assert rep["passed"] is False, rep
    checks = [f["check"] for f in rep["findings"]]
    assert checks == ["artifact_missing"], ("one clear finding, not example spam", checks)
    assert rep["findings"][0]["severity"] == "critical", rep["findings"]
    # when the artifact IS present, examples run normally (no false artifact_missing)
    Path(d, "jsonpick.py").write_text("x")
    rep2 = conf.run_cli_conformance(_cli_contract(profile), runner, cwd=d)
    assert all(f["check"] != "artifact_missing" for f in rep2["findings"]), rep2["findings"]
    print("ok  missing artifact -> one critical artifact_missing (not N example failures); present -> examples run")


def test_powershell_script_entrypoint_missing_artifact():
    # a PowerShell CLI is a `cli` deliverable (`pwsh tool.ps1 ...`); the .ps1 entrypoint must be recognised as
    # a script file so a not-delivered script is one clear artifact_missing finding, not example spam.
    import tempfile
    d = tempfile.mkdtemp(prefix="conf-ps-")
    profile = {"entrypoint": {"invocation": "pwsh tool.ps1"},
               "examples": [{"invocation": "-Help", "expected_status": 0}]}
    rep = conf.run_cli_conformance(_cli_contract(profile), lambda *a, **k: R(1, "", "not found"), cwd=d)
    assert [f["check"] for f in rep["findings"]] == ["artifact_missing"], rep["findings"]
    Path(d, "tool.ps1").write_text("param() exit 0")
    rep2 = conf.run_cli_conformance(_cli_contract(profile), lambda *a, **k: R(0, "", ""), cwd=d)
    assert all(f["check"] != "artifact_missing" for f in rep2["findings"]), rep2["findings"]
    print("ok  cli: a PowerShell .ps1 entrypoint is recognised — missing script -> one artifact_missing")


def test_non_cli_is_not_applicable():
    # a contract without a cli profile must be a vacuous pass — the gate never fabricates a finding it
    # cannot ground, and is a no-op for library/service deliverables.
    rep = conf.run_cli_conformance({"role_id": "aufheben-designer"}, fake_runner({}))
    assert rep["applicable"] is False and rep["passed"] is True and rep["findings"] == [], rep
    print("ok  non-CLI contract -> not applicable, vacuous pass")


def test_all_examples_pass():
    profile = {
        "entrypoint": {"invocation": "mytool"},
        "examples": [
            {"invocation": "--help", "expected_status": 0, "expected_stdout_contains": ["usage:"]},
            {"invocation": "", "expected_status": 0, "expected_stdout_contains": ["usage:"]},
        ],
    }
    runner = fake_runner({
        "mytool --help": R(0, "usage: mytool [opts]\n", ""),
        "mytool": R(0, "usage: mytool [opts]\n", ""),
    })
    rep = conf.run_cli_conformance(_cli_contract(profile), runner)
    assert rep["applicable"] and rep["passed"] and rep["findings"] == [], rep
    assert rep["checks_run"] == 2, rep
    print("ok  all examples pass -> green report")


def test_exit_status_mismatch_is_major():
    # the named #1 leak: declared exit 2 on bad input, artifact returns 1. Must be a major, actionable finding.
    profile = {
        "entrypoint": {"invocation": "mytool"},
        "status_and_errors": {"invalid_input_codes": [2]},
        "examples": [{"invocation": "build bad", "expected_status": 2}],
    }
    runner = fake_runner({"mytool build bad": R(1, "", "error: bad input\n")})
    rep = conf.run_cli_conformance(_cli_contract(profile), runner)
    assert rep["passed"] is False, rep
    (f,) = rep["findings"]
    assert f["check"] == "exit_status" and f["severity"] == "major", f
    assert f["expected"] == 2 and f["actual"] == 1, f
    assert f["failure_classification"] == "code", f
    print("ok  exit-status mismatch -> major finding (expected/actual pinned)")


def test_stdout_and_stderr_substring_checks():
    profile = {
        "entrypoint": {"invocation": "mytool"},
        "examples": [{
            "invocation": "x", "expected_status": 0,
            "expected_stdout_contains": ["done"], "expected_stderr_contains": ["warn:"],
        }],
    }
    # stdout missing 'done', stderr missing 'warn:' -> two findings
    runner = fake_runner({"mytool x": R(0, "nothing here\n", "")})
    rep = conf.run_cli_conformance(_cli_contract(profile), runner)
    checks = {f["check"] for f in rep["findings"]}
    assert checks == {"stdout_contains", "stderr_contains"}, rep["findings"]
    assert rep["passed"] is False
    print("ok  stdout/stderr substring misses -> findings on both channels")


def test_build_failure_is_critical_and_skips_examples():
    # a broken build invalidates every example; the checker must report the build as critical and NOT pile on
    # derived example failures (which would be noise, against Tricorder discipline).
    profile = {
        "build_and_install": {"commands": ["pip install .", "make"]},
        "entrypoint": {"invocation": "mytool"},
        "examples": [{"invocation": "--help", "expected_status": 0}],
    }
    runner = fake_runner({
        "pip install .": R(1, "", "ERROR: could not build wheel\n"),
        # 'make' and the example are never reached
    })
    rep = conf.run_cli_conformance(_cli_contract(profile), runner)
    assert rep["passed"] is False
    (f,) = rep["findings"]
    assert f["check"] == "build_and_install" and f["severity"] == "critical", f
    assert "could not build wheel" in f["stderr_tail"], f
    # incr #3 (narrowed flip): a build/install that RAN and failed is an ESTABLISHED product-failure signal —
    # a package that cannot be built is the artifact's own defect, NOT a genuinely-ambiguous nonzero. It stays
    # `code` / VERIFIED_PRODUCT_FAILURE / implementer (a could-not-run exit like 127 would still win first).
    assert f["failure_classification"] == "code", f
    assert f["gate_state"] == "VERIFIED_PRODUCT_FAILURE", f
    assert f["repair_route"] == "implementer", f
    assert f["phase"] == "build", f
    print("ok  build failure -> single critical CODE finding (implementer), examples skipped (no derived noise)")


def test_normalization_tolerates_volatile_output():
    # exact-stdout check must not flap on timestamps / temp paths / long hex ids — those are normalized.
    profile = {
        "entrypoint": {"invocation": "mytool"},
        "examples": [{
            "invocation": "report", "expected_status": 0,
            "expected_stdout": "built at <TS> in <TMP> (<HEX>)",
        }],
    }
    runner = fake_runner({
        "mytool report": R(0, "built at 2026-06-20T11:02:33Z in /tmp/build-xyz (deadbeefcafe123)\r\n", ""),
    })
    rep = conf.run_cli_conformance(_cli_contract(profile), runner)
    assert rep["passed"] is True, rep["findings"]
    print("ok  normalization tolerates timestamps/temp-paths/hex on exact stdout")


def _lib_contract(profile):
    return {"role_id": "aufheben-designer", "deliverable_kind": "library", "conformance": {"library": profile}}


def _probe_result(payload, returncode=0, stderr=""):
    """A runner whose probe call returns a canned marker line (the JSON outcome). payload=None -> no marker
    line at all (the probe could not run)."""
    body = conf._LIBRARY_PROBE_MARKER + json.dumps(payload) if payload is not None else ""
    return lambda cmd, *, cwd=None, stdin=None: R(returncode, body, stderr)


def test_library_all_symbols_resolve_passes():
    rep = conf.run_library_conformance(
        _lib_contract({"module": "jsonpick", "exported_symbols": ["pick", "main"]}),
        _probe_result({"missing": []}))
    assert rep["applicable"] and rep["passed"] and rep["findings"] == [], rep
    assert rep["checks_run"] == 1, rep  # one probe, no install commands
    print("ok  library: module imports and all exported symbols resolve -> passes")


def test_library_missing_symbol_is_major():
    rep = conf.run_library_conformance(
        _lib_contract({"module": "jsonpick", "exported_symbols": ["pick", "main"]}),
        _probe_result({"missing": ["main"]}))
    sym = [f for f in rep["findings"] if f["check"] == "exported_symbol"]
    assert not rep["passed"] and sym and sym[0]["symbol"] == "main" and sym[0]["severity"] == "major", rep
    print("ok  library: a declared export that does not resolve -> exported_symbol major")


def test_library_import_error_is_critical():
    rep = conf.run_library_conformance(
        _lib_contract({"module": "nope", "exported_symbols": ["x"]}),
        _probe_result({"import_error": "ModuleNotFoundError: No module named 'nope'"}))
    crit = [f for f in rep["findings"] if f["check"] == "library_import"]
    assert not rep["passed"] and crit and crit[0]["severity"] == "critical", rep
    assert crit[0]["failure_classification"] == "code", crit
    print("ok  library: a module that fails to import -> library_import critical")


def test_library_no_probe_result_is_critical():
    # no marker line (python missing / crashed before printing) -> the introspection itself failed; fail
    # closed with a critical finding rather than a vacuous pass.
    rep = conf.run_library_conformance(
        _lib_contract({"module": "m", "exported_symbols": ["x"]}),
        _probe_result(None, returncode=127, stderr="python3: not found"))
    crit = [f for f in rep["findings"] if f["check"] == "library_probe"]
    assert not rep["passed"] and crit and crit[0]["severity"] == "critical", rep
    print("ok  library: a probe that yields no result -> library_probe critical (no vacuous pass)")


def test_library_build_failure_skips_probe():
    profile = {"module": "m", "exported_symbols": ["x"], "build_and_install": {"commands": ["pip install ."]}}
    rep = conf.run_library_conformance(_lib_contract(profile), fake_runner({"pip install .": R(1, "", "boom")}))
    assert not rep["passed"]
    assert any(f["check"] == "build_and_install" and f["severity"] == "critical" for f in rep["findings"]), rep
    assert not any(f["check"] in ("library_probe", "library_import", "exported_symbol") for f in rep["findings"]), rep
    print("ok  library: a broken build is critical and skips the import probe")


def test_library_probe_command_carries_module_and_import_paths():
    seen = {}

    def capture(cmd, *, cwd=None, stdin=None):
        seen["cmd"] = cmd
        return R(0, conf._LIBRARY_PROBE_MARKER + json.dumps({"missing": []}), "")

    conf.run_library_conformance(
        _lib_contract({"module": "mypkg.core", "exported_symbols": ["f"], "import_paths": ["src"]}), capture)
    assert "mypkg.core" in seen["cmd"] and "src" in seen["cmd"] and "importlib" in seen["cmd"], seen
    print("ok  library: the probe command carries the declared module + import_paths")


def test_schema_library_profile_requires_module_and_symbols():
    try:
        import jsonschema
    except ImportError:
        print("skip  jsonschema not installed")
        return
    v = jsonschema.Draft202012Validator(json.loads(SCHEMA.read_text()))
    base = {"role_id": "aufheben-designer", "contract_id": "c", "objective": "o", "selected_direction": "d",
            "rejected_parts": [], "implementation_summary": "s", "acceptance_criteria": [],
            "files_allowed_to_change": [], "files_not_allowed_to_change": [], "required_checks": [],
            "security_requirements": [], "nonfunctional_requirements": [], "non_goals": [], "risks": [],
            "fallback_plan": "f", "handoff_to_implementer": "h"}
    ok = {**base, "deliverable_kind": "library", "conformance": {"library": {"module": "m", "exported_symbols": ["f"]}}}
    assert v.is_valid(ok), list(v.iter_errors(ok))
    assert not v.is_valid({**base, "deliverable_kind": "library", "conformance": {"library": {"module": "m"}}}), \
        "library profile requires exported_symbols"
    assert not v.is_valid({**base, "deliverable_kind": "library",
                           "conformance": {"library": {"module": "m", "exported_symbols": []}}}), \
        "exported_symbols must be non-empty (minItems 1)"
    print("ok  schema: library profile requires module + non-empty exported_symbols")


def test_library_powershell_probe_command_uses_pwsh():
    captured = {}

    def capture(cmd, *, cwd=None, stdin=None):
        captured["cmd"] = cmd
        return R(0, conf._LIBRARY_PROBE_MARKER + json.dumps({"missing": []}), "")

    conf.run_library_conformance(
        _lib_contract({"module": "MyMod", "exported_symbols": ["Get-Thing"], "language": "powershell"}), capture)
    cmd = captured["cmd"]
    assert cmd.startswith("pwsh ") and "Import-Module" in cmd and "MyMod" in cmd and "Get-Thing" in cmd, cmd
    assert "python3 -c" not in cmd, cmd
    print("ok  library(powershell): the probe runs through pwsh + Import-Module, carrying module + symbols")


def test_library_powershell_passes_and_missing_via_shared_parser():
    # the powershell probe emits the same __LIBPROBE__ marker, so the shared parser yields the same findings.
    ps = {"module": "MyMod", "exported_symbols": ["Get-Thing"], "language": "powershell"}
    ok = conf.run_library_conformance(_lib_contract(ps), _probe_result({"missing": []}))
    assert ok["applicable"] and ok["passed"], ok
    bad = conf.run_library_conformance(_lib_contract(ps), _probe_result({"missing": ["Get-Thing"]}))
    sym = [f for f in bad["findings"] if f["check"] == "exported_symbol"]
    assert not bad["passed"] and sym and sym[0]["symbol"] == "Get-Thing", bad
    print("ok  library(powershell): shared parser — all-resolve passes; a missing cmdlet -> exported_symbol major")


def test_schema_library_language_is_constrained():
    try:
        import jsonschema
    except ImportError:
        print("skip  jsonschema not installed")
        return
    v = jsonschema.Draft202012Validator(json.loads(SCHEMA.read_text()))
    base = {"role_id": "aufheben-designer", "contract_id": "c", "objective": "o", "selected_direction": "d",
            "rejected_parts": [], "implementation_summary": "s", "acceptance_criteria": [],
            "files_allowed_to_change": [], "files_not_allowed_to_change": [], "required_checks": [],
            "security_requirements": [], "nonfunctional_requirements": [], "non_goals": [], "risks": [],
            "fallback_plan": "f", "handoff_to_implementer": "h"}

    def lib(profile):
        return {**base, "deliverable_kind": "library", "conformance": {"library": profile}}

    assert v.is_valid(lib({"module": "m", "exported_symbols": ["f"], "language": "powershell"})), "powershell ok"
    assert v.is_valid(lib({"module": "m", "exported_symbols": ["f"]})), "language optional (default python)"
    assert not v.is_valid(lib({"module": "m", "exported_symbols": ["f"], "language": "bash"})), "language is an enum"
    print("ok  schema: library language is python|powershell, optional (default python)")


def _json_contract(files):
    return {"role_id": "aufheben-designer", "deliverable_kind": "json", "conformance": {"json": {"files": files}}}


def _ws(files: dict):
    """A temp workspace with {relpath: content} written; returns a TemporaryDirectory (use as a context)."""
    import tempfile
    d = tempfile.TemporaryDirectory()
    for rel, content in files.items():
        with open(os.path.join(d.name, rel), "w", encoding="utf-8") as fh:
            fh.write(content)
    return d


def test_json_valid_document_passes():
    with _ws({"config.json": '{"name": "x", "version": "1.0", "info": {"title": "t"}}'}) as d:
        rep = conf.run_json_conformance(_json_contract([{
            "path": "config.json",
            "schema": {"type": "object", "required": ["name", "version"]},
            "required_paths": ["info.title"]}]), cwd=d)
    assert rep["applicable"] and rep["passed"] and rep["findings"] == [], rep
    assert rep["checks_run"] == 1, rep
    print("ok  json: parses + validates against schema + required_paths resolve -> passes")


def test_json_missing_file_is_critical():
    with _ws({}) as d:
        rep = conf.run_json_conformance(_json_contract([{"path": "absent.json"}]), cwd=d)
    crit = [f for f in rep["findings"] if f["check"] == "json_missing"]
    assert not rep["passed"] and crit and crit[0]["severity"] == "critical", rep
    print("ok  json: a declared file not present -> json_missing critical")


def test_json_parse_error_is_critical():
    with _ws({"bad.json": '{"name": "x",'}) as d:
        rep = conf.run_json_conformance(_json_contract([{"path": "bad.json"}]), cwd=d)
    crit = [f for f in rep["findings"] if f["check"] == "json_parse"]
    assert not rep["passed"] and crit and crit[0]["severity"] == "critical", rep
    print("ok  json: malformed content -> json_parse critical")


def test_json_schema_violation_is_major():
    with _ws({"c.json": '{"name": 7}'}) as d:  # name should be a string
        rep = conf.run_json_conformance(_json_contract([{
            "path": "c.json",
            "schema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}}]), cwd=d)
    viol = [f for f in rep["findings"] if f["check"] == "json_schema"]
    assert not rep["passed"] and viol and viol[0]["severity"] == "major", rep
    print("ok  json: a schema violation -> json_schema major")


def test_json_required_path_missing_is_major():
    with _ws({"c.json": '{"info": {"title": "t"}}'}) as d:
        rep = conf.run_json_conformance(_json_contract([{
            "path": "c.json", "required_paths": ["info.version"]}]), cwd=d)
    miss = [f for f in rep["findings"] if f["check"] == "json_required_path"]
    assert not rep["passed"] and miss and miss[0]["key_path"] == "info.version", rep
    print("ok  json: a missing required key path -> json_required_path major")


def test_json_invalid_declared_schema_is_major():
    with _ws({"c.json": '{"a": 1}'}) as d:
        rep = conf.run_json_conformance(_json_contract([{
            "path": "c.json", "schema": {"type": "not-a-real-type"}}]), cwd=d)
    assert not rep["passed"] and any(f["check"] == "json_schema" for f in rep["findings"]), rep
    print("ok  json: an invalid declared schema is surfaced (not a silent skip)")


def test_json_dispatch_routes_to_real_checker():
    with _ws({"d.json": '{"a": 1}'}) as d:
        rep = conf.run_conformance(_json_contract([{"path": "d.json"}]), fake_runner({}), cwd=d)
    assert rep["applicable"] and rep["passed"], rep
    print("ok  dispatch: json -> real json checker (runner ignored, no process run)")


def test_schema_json_profile_requires_files():
    try:
        import jsonschema
    except ImportError:
        print("skip  jsonschema not installed")
        return
    v = jsonschema.Draft202012Validator(json.loads(SCHEMA.read_text()))
    base = {"role_id": "aufheben-designer", "contract_id": "c", "objective": "o", "selected_direction": "d",
            "rejected_parts": [], "implementation_summary": "s", "acceptance_criteria": [],
            "files_allowed_to_change": [], "files_not_allowed_to_change": [], "required_checks": [],
            "security_requirements": [], "nonfunctional_requirements": [], "non_goals": [], "risks": [],
            "fallback_plan": "f", "handoff_to_implementer": "h"}
    ok = {**base, "deliverable_kind": "json", "conformance": {"json": {"files": [{"path": "x.json"}]}}}
    assert v.is_valid(ok), list(v.iter_errors(ok))
    assert not v.is_valid({**base, "deliverable_kind": "json"}), "json deliverable must require a json profile"
    assert not v.is_valid({**base, "deliverable_kind": "json", "conformance": {"json": {"files": []}}}), \
        "files must be non-empty"
    assert not v.is_valid({**base, "deliverable_kind": "json", "conformance": {"json": {"files": [{}]}}}), \
        "each file requires a path"
    print("ok  schema: json profile requires non-empty files, each with a path")


def _batch_contract(profile):
    return {"role_id": "aufheben-designer", "deliverable_kind": "batch_job", "conformance": {"batch_job": profile}}


def test_batch_job_success_and_artifacts_pass():
    profile = {"run": {"command": "python3 job.py"}, "produced_artifacts": ["out.csv"]}
    runner = fake_runner({"python3 job.py": R(0, "", ""), "test -e out.csv": R(0, "", "")})
    rep = conf.run_batch_job_conformance(_batch_contract(profile), runner)
    assert rep["applicable"] and rep["passed"] and rep["findings"] == [], rep
    assert rep["checks_run"] == 2, rep  # the run + one artifact probe
    print("ok  batch_job: job exits 0 and the declared artifact exists -> passes")


def test_batch_job_exit_status_mismatch_is_major():
    runner = fake_runner({"python3 job.py": R(3, "", "boom")})
    rep = conf.run_batch_job_conformance(_batch_contract({"run": {"command": "python3 job.py"}}), runner)
    es = [f for f in rep["findings"] if f["check"] == "exit_status"]
    assert not rep["passed"] and es and es[0]["expected"] == 0 and es[0]["actual"] == 3, rep
    print("ok  batch_job: a non-zero (vs expected) exit -> exit_status major")


def test_batch_job_expected_status_honored():
    profile = {"run": {"command": "python3 job.py"}, "expected_status": 2}
    rep = conf.run_batch_job_conformance(_batch_contract(profile), fake_runner({"python3 job.py": R(2, "", "")}))
    assert rep["passed"], rep  # exit 2 matches the declared expected_status
    print("ok  batch_job: a declared non-zero expected_status is honored")


def test_batch_job_missing_artifact_is_major():
    profile = {"run": {"command": "j"}, "produced_artifacts": ["out.csv", "report.json"]}
    runner = fake_runner({"j": R(0, "", ""), "test -e out.csv": R(0, "", ""), "test -e report.json": R(1, "", "")})
    rep = conf.run_batch_job_conformance(_batch_contract(profile), runner)
    miss = [f for f in rep["findings"] if f["check"] == "produced_artifact"]
    assert not rep["passed"] and miss and miss[0]["artifact"] == "report.json", rep
    print("ok  batch_job: a declared artifact the job did not produce -> produced_artifact major")


def test_batch_job_build_failure_skips_run():
    profile = {"run": {"command": "j"}, "build_and_install": {"commands": ["make"]}}
    rep = conf.run_batch_job_conformance(_batch_contract(profile), fake_runner({"make": R(1, "", "compile error")}))
    assert not rep["passed"]
    assert any(f["check"] == "build_and_install" and f["severity"] == "critical" for f in rep["findings"]), rep
    assert not any(f["check"] in ("exit_status", "produced_artifact") for f in rep["findings"]), rep
    print("ok  batch_job: a broken build is critical and skips the job run")


def test_schema_batch_job_profile_requires_run():
    try:
        import jsonschema
    except ImportError:
        print("skip  jsonschema not installed")
        return
    v = jsonschema.Draft202012Validator(json.loads(SCHEMA.read_text()))
    base = {"role_id": "aufheben-designer", "contract_id": "c", "objective": "o", "selected_direction": "d",
            "rejected_parts": [], "implementation_summary": "s", "acceptance_criteria": [],
            "files_allowed_to_change": [], "files_not_allowed_to_change": [], "required_checks": [],
            "security_requirements": [], "nonfunctional_requirements": [], "non_goals": [], "risks": [],
            "fallback_plan": "f", "handoff_to_implementer": "h"}
    ok = {**base, "deliverable_kind": "batch_job", "conformance": {"batch_job": {"run": {"command": "j"}}}}
    assert v.is_valid(ok), list(v.iter_errors(ok))
    assert not v.is_valid({**base, "deliverable_kind": "batch_job"}), "batch_job deliverable must require a profile"
    assert not v.is_valid({**base, "deliverable_kind": "batch_job", "conformance": {"batch_job": {}}}), \
        "the batch_job profile requires run.command"
    print("ok  schema: batch_job profile requires a run.command")


def _rpc_contract(profile):
    return {"role_id": "aufheben-designer", "deliverable_kind": "rpc_service", "conformance": {"rpc_service": profile}}


class _SvcRunner:
    """A service runner: .start(command) returns a handle whose .stop() records that the service was torn down."""
    def __init__(self):
        self.stopped = False

    def start(self, command, *, cwd=None):
        outer = self

        class _Handle:
            def stop(self):
                outer.stopped = True

        return _Handle()


def _launcher_from_start(obj):
    def launch(command, *, cwd=None, profile=None):
        return obj.start(command, cwd=cwd)
    return launch


def _json_rpc_request(by_method):
    """A fake http_request: GET (readiness) returns 200; a JSON-RPC POST returns the canned envelope for its
    method, always over HTTP 200 (the JSON-RPC convention the checker must look past)."""
    def _req(method, url, *, json_body=None, has_json_body=False, timeout=None):
        if method == "GET":
            return conf.HttpResponse(200, b"ready")
        env = {"jsonrpc": "2.0", "id": json_body["id"], **by_method.get(json_body["method"], {"result": None})}
        return conf.HttpResponse(200, json.dumps(env).encode())
    return _req


def test_rpc_json_rpc_call_passes_and_stops_service():
    profile = {"start": {"command": "serve"}, "base_url": "http://x", "transport": "json_rpc_http",
               "calls": [{"method": "add", "params": {"a": 1, "b": 2}, "expected_result_contains": ["3"]}]}
    runner = _SvcRunner()
    rep = conf.run_rpc_service_conformance(_rpc_contract(profile), runner,
                                           http_request=_json_rpc_request({"add": {"result": 3}}),
                                           service_launcher=_launcher_from_start(runner))
    assert rep["applicable"] and rep["passed"] and rep["findings"] == [], rep
    assert runner.stopped, "the service must be stopped after the calls"
    print("ok  rpc(json_rpc_http): a real call whose result matches -> passes; service stopped")


def test_rpc_json_rpc_unexpected_error_is_major_not_gated_on_http_status():
    profile = {"start": {"command": "serve"}, "base_url": "http://x", "transport": "json_rpc_http",
               "calls": [{"method": "add", "params": {}}]}
    runner = _SvcRunner()
    rep = conf.run_rpc_service_conformance(_rpc_contract(profile), runner,
        http_request=_json_rpc_request({"add": {"error": {"code": -32000, "message": "boom"}}}),
        service_launcher=_launcher_from_start(runner))
    assert not rep["passed"] and any(f["check"] == "error" for f in rep["findings"]), rep
    print("ok  rpc(json_rpc_http): an error in the body (over HTTP 200) -> major, not a pass")


def test_rpc_json_rpc_expected_error_code_matches():
    profile = {"start": {"command": "serve"}, "base_url": "http://x", "transport": "json_rpc_http",
               "calls": [{"method": "bad", "expected_error_code": -32601}]}
    runner = _SvcRunner()
    rep = conf.run_rpc_service_conformance(_rpc_contract(profile), runner,
        http_request=_json_rpc_request({"bad": {"error": {"code": -32601, "message": "no method"}}}),
        service_launcher=_launcher_from_start(runner))
    assert rep["passed"], rep
    print("ok  rpc(json_rpc_http): a declared expected_error_code that matches -> passes")


def test_rpc_unsupported_transport_is_critical_without_boot():
    profile = {"start": {"command": "serve"}, "base_url": "x", "transport": "thrift", "calls": [{"method": "m"}]}
    runner = _SvcRunner()
    rep = conf.run_rpc_service_conformance(_rpc_contract(profile), runner,
                                           service_launcher=_launcher_from_start(runner))
    assert not rep["passed"], rep
    assert any(f["check"] == "transport" and f["severity"] == "critical" for f in rep["findings"]), rep
    assert not runner.stopped, "an unsupported transport is caught before booting"
    print("ok  rpc: an unsupported transport -> critical, no boot")


def test_rpc_grpc_real_invocation_with_injected_invoker_passes():
    profile = {"start": {"command": "serve"}, "base_url": "h:50051", "transport": "grpc",
               "calls": [{"method": "pkg.Svc/Get", "params": {"id": 1}, "expected_result_contains": ["ok"]}]}

    def invoker(method, params):
        assert method == "pkg.Svc/Get" and params == {"id": 1}
        return ({"status": "ok"}, None)

    runner = _SvcRunner()
    rep = conf.run_rpc_service_conformance(_rpc_contract(profile), runner, grpc_invoker=invoker,
                                           service_launcher=_launcher_from_start(runner))
    assert rep["applicable"] and rep["passed"] and rep["findings"] == [], rep
    print("ok  rpc(grpc): a real dynamic invocation (injected) whose result matches -> passes")


def test_rpc_grpc_unavailable_machinery_is_a_finding_not_silent():
    # no injected invoker and (no descriptor_set_path / no grpcio) -> ONE transport_unavailable finding: the
    # heavy machinery is loaded lazily and its absence is surfaced, never a silent pass nor an always-on dep.
    profile = {"start": {"command": "serve"}, "base_url": "h:50051", "transport": "grpc",
               "calls": [{"method": "pkg.Svc/Get"}]}
    runner = _SvcRunner()
    rep = conf.run_rpc_service_conformance(_rpc_contract(profile), runner,
                                           service_launcher=_launcher_from_start(runner))
    assert not rep["passed"] and any(f["check"] == "transport_unavailable" for f in rep["findings"]), rep
    print("ok  rpc(grpc): missing transport machinery -> transport_unavailable finding (lazy, not silent)")


def test_schema_rpc_profile_requires_start_base_transport_calls():
    try:
        import jsonschema
    except ImportError:
        print("skip  jsonschema not installed")
        return
    v = jsonschema.Draft202012Validator(json.loads(SCHEMA.read_text()))
    base = {"role_id": "aufheben-designer", "contract_id": "c", "objective": "o", "selected_direction": "d",
            "rejected_parts": [], "implementation_summary": "s", "acceptance_criteria": [],
            "files_allowed_to_change": [], "files_not_allowed_to_change": [], "required_checks": [],
            "security_requirements": [], "nonfunctional_requirements": [], "non_goals": [], "risks": [],
            "fallback_plan": "f", "handoff_to_implementer": "h"}
    ok = {**base, "deliverable_kind": "rpc_service", "conformance": {"rpc_service": {
        "start": {"command": "serve"}, "base_url": "http://x", "transport": "json_rpc_http",
        "calls": [{"method": "ping", "expected_result_contains": ["pong"]}]}}}
    assert v.is_valid(ok), list(v.iter_errors(ok))
    incomplete = {**base, "deliverable_kind": "rpc_service", "conformance": {"rpc_service": {
        "start": {"command": "serve"}, "base_url": "http://x", "transport": "json_rpc_http"}}}  # no calls
    assert not v.is_valid(incomplete), "rpc profile requires calls"
    no_result = {**base, "deliverable_kind": "rpc_service", "conformance": {"rpc_service": {
        "start": {"command": "serve"}, "base_url": "http://x", "transport": "json_rpc_http",
        "calls": [{"method": "ping"}]}}}
    assert not v.is_valid(no_result), "rpc profile requires at least one call with expected result/error"
    assert not v.is_valid({**base, "deliverable_kind": "rpc_service", "conformance": {"rpc_service": {
        "start": {"command": "s"}, "base_url": "x", "transport": "smoke-signals",
        "calls": [{"method": "m", "expected_result_contains": ["ok"]}]}}}), \
        "transport is constrained to the supported set"
    print("ok  schema: rpc profile requires start/base_url/transport/calls/result; transport is an enum")


def test_schema_http_profile_requires_production_boundary_body_probe():
    try:
        import jsonschema
    except ImportError:
        print("skip  jsonschema not installed")
        return
    v = jsonschema.Draft202012Validator(json.loads(SCHEMA.read_text()))
    base = {"role_id": "aufheben-designer", "contract_id": "c", "objective": "o", "selected_direction": "d",
            "rejected_parts": [], "implementation_summary": "s", "acceptance_criteria": [],
            "files_allowed_to_change": [], "files_not_allowed_to_change": [], "required_checks": [],
            "security_requirements": [], "nonfunctional_requirements": [], "non_goals": [], "risks": [],
            "fallback_plan": "f", "handoff_to_implementer": "h"}
    ok = {**base, "deliverable_kind": "http_service", "conformance": {"http_service": {
        "start": {"command": "serve"}, "base_url": "http://x", "readiness_timeout_seconds": 3,
        "examples": [{"method": "GET", "path": "/health", "expected_status": 200,
                      "expected_body_contains": ["ok"]}]}}}
    assert v.is_valid(ok), list(v.iter_errors(ok))
    status_only = {**base, "deliverable_kind": "http_service", "conformance": {"http_service": {
        "start": {"command": "serve"}, "base_url": "http://x", "readiness_timeout_seconds": 3,
        "examples": [{"method": "GET", "path": "/health", "expected_status": 200}]}}}
    assert not v.is_valid(status_only), "http_service profile requires at least one expected body probe"
    print("ok  schema: http_service requires launch+endpoint+status/body production-boundary probe")


def test_dispatch_routes_every_kind_to_a_real_checker():
    # the single entry point: every executable kind routes to a real checker; `undetermined` routes to the
    # recognized-but-unchecked slot (never a silent pass); an unknown/absent kind is simply not applicable.
    profile = {"entrypoint": {"invocation": "t"}, "examples": [{"invocation": "", "expected_status": 0}]}
    cli = conf.run_conformance(_cli_contract(profile), fake_runner({"t": R(0, "", "")}))
    assert cli["applicable"] and cli["passed"], cli
    undetermined = conf.run_conformance({"deliverable_kind": "undetermined"}, fake_runner({}))
    assert undetermined["applicable"] is False and undetermined.get("slot") == "undetermined", undetermined
    assert conf.run_conformance({"role_id": "x"}, fake_runner({})) == {
        "applicable": False, "passed": True, "findings": [], "checks_run": 0}
    unknown = conf.run_conformance({"deliverable_kind": "mystery", "conformance": {"mystery": {}}}, fake_runner({}))
    assert unknown["applicable"] is False and not unknown.get("slot"), unknown
    print("ok  dispatch: cli -> real checker; undetermined -> slot; unknown/absent -> not applicable")


def test_slot_kind_is_streamed_not_silent():
    # a recognized-but-unchecked kind must emit a `slot_unchecked` stream event (no silent cap), while the
    # convergence findings stay untouched.
    # `undetermined` is the real entry to the empty-slot mechanism: a checkable interface of a kind no checker
    # supports yet. It streams slot_unchecked (visible, never silent) and folds nothing — it is recognized,
    # not silently passed.
    results = {"aufheben-designer": {"deliverable_kind": "undetermined"}}
    events = []
    orig = cp._stream_append
    cp._stream_append = lambda repo, ev: events.append(ev)
    try:
        rep = cp._shadow_conformance("/tmp/leaf", results, "run-undet", runner=fake_runner({}))
    finally:
        cp._stream_append = orig
    assert rep and rep.get("slot") == "undetermined", rep
    slot_events = [e for e in events if e.get("type") == "slot_unchecked"]
    assert slot_events and slot_events[0]["slot"] == "undetermined", events
    assert cp._apply_conformance_gate([], rep, "block") == [], "an empty slot folds no findings even in block"
    print("ok  undetermined streams slot_unchecked (visible, not silent); folds nothing")


def test_schema_every_executable_kind_requires_its_profile():
    try:
        import jsonschema
    except ImportError:
        print("skip  jsonschema not installed")
        return
    v = jsonschema.Draft202012Validator(json.loads(SCHEMA.read_text()))
    base = {"role_id": "aufheben-designer", "contract_id": "c", "objective": "o", "selected_direction": "d",
            "rejected_parts": [], "implementation_summary": "s", "acceptance_criteria": [],
            "files_allowed_to_change": [], "files_not_allowed_to_change": [], "required_checks": [],
            "security_requirements": [], "nonfunctional_requirements": [], "non_goals": [], "risks": [],
            "fallback_plan": "f", "handoff_to_implementer": "h"}
    # every executable kind now REQUIRES its conformance profile — no optional slots remain.
    for kind in ("cli", "http_service", "library", "json", "batch_job", "rpc_service"):
        assert not v.is_valid({**base, "deliverable_kind": kind}), (kind, "executable kind must require its profile")
    # 'none' (no interface) and 'undetermined' (interface but no checker yet) need no conformance profile, but
    # the kind must still be DECLARED — deliverable_kind is now schema-required, so omitting it is invalid.
    assert v.is_valid({**base, "deliverable_kind": "none"}), "none needs no profile"
    assert v.is_valid({**base, "deliverable_kind": "undetermined"}), "undetermined needs no profile"
    assert not v.is_valid(base), "deliverable_kind is now required — a contract omitting it is invalid"
    print("ok  schema: every executable kind requires its profile; none/undetermined need none; kind is required")


def test_schema_cli_profile_required_iff_cli():
    try:
        import jsonschema
    except ImportError:
        print("skip  jsonschema not installed (schema conditional not checked here)")
        return
    schema = json.loads(SCHEMA.read_text())
    validator = jsonschema.Draft202012Validator(schema)

    base = {
        "role_id": "aufheben-designer", "contract_id": "c1", "objective": "o", "selected_direction": "d",
        "rejected_parts": [], "implementation_summary": "s", "acceptance_criteria": [],
        "files_allowed_to_change": [], "files_not_allowed_to_change": [], "required_checks": [],
        "security_requirements": [], "nonfunctional_requirements": [], "non_goals": [], "risks": [],
        "fallback_plan": "f", "handoff_to_implementer": "h",
    }
    # 1) deliverable_kind is now SCHEMA-REQUIRED — a contract that omits it is invalid (the shadow-first
    # pre-flight obligation, promoted to a hard requirement now that every kind is expressible)
    assert not validator.is_valid(base), "deliverable_kind is now a required property"
    # 2) deliverable_kind=cli WITHOUT a conformance.cli profile must FAIL
    cli_missing = {**base, "deliverable_kind": "cli"}
    assert not validator.is_valid(cli_missing), "cli deliverable must require a conformance.cli profile"
    # 3) deliverable_kind=cli WITH a valid profile validates
    cli_ok = {**base, "deliverable_kind": "cli", "conformance": {"cli": {
        "entrypoint": {"invocation": "mytool"},
        "examples": [{"invocation": "--help", "expected_status": 0}],
    }}}
    assert validator.is_valid(cli_ok), list(validator.iter_errors(cli_ok))
    # 4) a library deliverable requires a LIBRARY profile, not a cli one (the schema is not a universal checklist)
    lib_ok = {**base, "deliverable_kind": "library",
              "conformance": {"library": {"module": "m", "exported_symbols": ["f"]}}}
    assert validator.is_valid(lib_ok), list(validator.iter_errors(lib_ok))
    assert not validator.is_valid({**base, "deliverable_kind": "library"}), \
        "a library deliverable must require a conformance.library profile"
    print("ok  schema: cli profile required iff deliverable_kind==cli; library needs its own profile; legacy valid")


def test_shadow_gate_streams_but_does_not_block():
    # the wired gate: when the contract is a CLI and an example fails, the controller streams a
    # `shadow_findings` event for observation, but in SHADOW mode the failure must NOT enter the convergence
    # findings (so it cannot block the merge). Promotion to `block` is the only path that folds them in.
    profile = {"entrypoint": {"invocation": "mytool"},
               "examples": [{"invocation": "x", "expected_status": 0}]}
    results = {"aufheben-designer": _cli_contract(profile)}
    runner = fake_runner({"mytool x": R(3, "", "boom\n")})   # wrong exit -> a failing conformance finding

    events = []
    orig = cp._stream_append
    orig_mode = cp.CONFORMANCE_GATE_MODE
    cp._stream_append = lambda repo, ev: events.append(ev)        # capture stream, touch no disk
    cp.CONFORMANCE_GATE_MODE = "shadow"                           # this test asserts the SHADOW stream type
    try:
        report = cp._shadow_conformance("/tmp/leaf", results, "run-1", runner=runner)
    finally:
        cp._stream_append = orig
        cp.CONFORMANCE_GATE_MODE = orig_mode

    assert report and report["applicable"] and report["passed"] is False, report
    streamed = [e for e in events if e.get("source") == "cli-conformance"]
    assert streamed and streamed[0]["type"] == "shadow_findings", streamed
    # SHADOW: the convergence findings are untouched; BLOCK: the failing finding is folded in.
    base = [{"severity": "major", "file": "x.py"}]
    assert cp._apply_conformance_gate(base, report, "shadow") == base, "shadow must not block"
    folded = cp._apply_conformance_gate(base, report, "block")
    assert len(folded) == len(base) + 1 and folded[-1]["check"] == "exit_status", folded
    print("ok  wired gate streams shadow_findings; shadow never blocks, block folds the failure in")


def test_shadow_gate_dormant_for_non_cli_contract():
    # no contract declares a CLI today, so the gate must be a no-op: no event, returns a not-applicable
    # report (or None), and the runner is never touched.
    results = {"aufheben-designer": {"role_id": "aufheben-designer", "implementation_summary": "prose only"}}
    events = []
    orig = cp._stream_append
    cp._stream_append = lambda repo, ev: events.append(ev)
    try:
        report = cp._shadow_conformance("/tmp/leaf", results, "run-2",
                                        runner=fake_runner({}))   # empty table: any exec would raise
    finally:
        cp._stream_append = orig
    assert report is None or report.get("applicable") is False, report
    assert [e for e in events if e.get("source") == "cli-conformance"] == [], events
    assert cp.CONFORMANCE_GATE_MODE == "block", "default mode is block (promoted 2026-06-21 on FP-audit evidence)"
    print("ok  gate dormant for a non-CLI contract (no event, no exec; default mode=block, still a no-op here)")


def test_acceptance_bundle_withheld_from_implementer_only():
    # ADR-0009 #1 immutable acceptance bundle: the implementer sees the spec (entrypoint, status_and_errors,
    # acceptance_criteria) but NOT the golden examples; a verifier sees the full contract; results untouched.
    contract = {
        "role_id": "aufheben-designer", "acceptance_criteria": ["does X"],
        "deliverable_kind": "cli",
        "conformance": {"cli": {
            "entrypoint": {"invocation": "t"},
            "status_and_errors": {"success_codes": [0], "invalid_input_codes": [2]},
            "examples": [{"invocation": "--help", "expected_status": 0},
                         {"invocation": "", "expected_status": 2}],
        }},
    }
    inputs = {"aufheben-designer": contract}

    impl = cp._withhold_acceptance_bundle("implementer", inputs)
    icli = impl["aufheben-designer"]["conformance"]["cli"]
    assert icli["examples"] == [], "implementer must NOT see the golden examples"
    assert icli["_examples_withheld"] == 2, "the withholding is marked, not silent"
    assert icli["entrypoint"] == {"invocation": "t"}, "implementer keeps the entrypoint spec"
    assert icli["status_and_errors"]["invalid_input_codes"] == [2], "implementer keeps the exit-code policy"
    assert impl["aufheben-designer"]["acceptance_criteria"] == ["does X"], "implementer keeps acceptance_criteria"
    # the ORIGINAL contract (what the controller/gate uses) still has the examples
    assert len(contract["conformance"]["cli"]["examples"]) == 2, "the gate's contract is untouched"

    # a verifier / any non-implementer role sees the full contract unchanged
    same = cp._withhold_acceptance_bundle("linon", inputs)
    assert same["aufheben-designer"]["conformance"]["cli"]["examples"], "non-implementer sees full goldens"
    assert cp.WITHHOLD_BUNDLE == "on", "withholding is on by default"
    print("ok  acceptance bundle withheld from implementer only (spec kept, goldens hidden, gate intact)")


def test_acceptance_bundle_withholds_rpc_oracle_but_keeps_shape_specs():
    # ADR-0018: withholding is principle-based. CLI/HTTP examples remain hidden; RPC call methods/params are
    # visible build spec, while expected result/error assertions are hidden. Batch/JSON shape stays visible.
    contract = {
        "role_id": "aufheben-designer",
        "acceptance_criteria": ["does X"],
        "deliverable_kind": "rpc_service",
        "conformance": {
            "cli": {"entrypoint": {"invocation": "tool"},
                    "examples": [{"invocation": "--secret", "expected_stdout_contains": ["CLI_GOLDEN"]}]},
            "http_service": {"base_url": "http://x",
                             "examples": [{"method": "GET", "path": "/secret",
                                           "expected_body_contains": ["HTTP_GOLDEN"]}]},
            "rpc_service": {"transport": "json_rpc_http", "base_url": "http://x",
                            "calls": [{"method": "svc.Get", "params": {"id": 7},
                                       "expected_result_contains": ["RPC_GOLDEN"]},
                                      {"method": "svc.Bad", "params": {},
                                       "expected_error_code": -32601}]},
            "batch_job": {"run": {"command": "python3 job.py"}, "expected_status": 3,
                          "produced_artifacts": ["out/report.json"]},
            "json": {"files": [{"path": "out/report.json", "required_paths": ["$.items[0].id"]}]},
        },
    }
    impl = cp._withhold_acceptance_bundle("implementer", {"aufheben-designer": contract})
    conf = impl["aufheben-designer"]["conformance"]

    assert conf["cli"]["examples"] == [] and conf["cli"]["_examples_withheld"] == 1
    assert conf["http_service"]["examples"] == [] and conf["http_service"]["_examples_withheld"] == 1
    calls = conf["rpc_service"]["calls"]
    assert calls[0]["method"] == "svc.Get" and calls[0]["params"] == {"id": 7}
    assert calls[1]["method"] == "svc.Bad" and calls[1]["params"] == {}
    assert "expected_result_contains" not in calls[0] and "expected_error_code" not in calls[1]
    assert conf["rpc_service"]["_calls_oracle_withheld"] == 2
    assert conf["batch_job"]["produced_artifacts"] == ["out/report.json"]
    assert conf["json"]["files"][0]["required_paths"] == ["$.items[0].id"]
    assert contract["conformance"]["rpc_service"]["calls"][0]["expected_result_contains"] == ["RPC_GOLDEN"]
    print("ok  ADR-0018 withholding: RPC oracle hidden, RPC method/params + batch/json spec kept")


def test_closed_loop_block_gate_keeps_findings_when_linon_clean():
    # P0 (closed loop): linon is CLEAN, but a block-mode conformance gate still FAILS on the current artifact.
    # _closed_loop_findings re-runs the gates and must keep findings non-empty (so the repair loop stays open
    # and convergence requires the gate clean) — instead of being overwritten by linon-only.
    results = {"linon": {"findings": []}}                       # linon says clean
    failing = {"applicable": True, "passed": False,
               "findings": [{"check": "exit_status", "severity": "major", "passed": False,
                             "failure_classification": "code"}]}
    saved = (cp._shadow_conformance, cp._secret_scan, cp._fuzz_cli, cp.CONFORMANCE_GATE_MODE)
    cp._shadow_conformance = lambda repo, results, run_id, runner=None: failing
    cp._secret_scan = lambda repo, run_id: None
    cp._fuzz_cli = lambda repo, results, run_id: None
    try:
        cp.CONFORMANCE_GATE_MODE = "block"
        findings, gate_ctx = cp._closed_loop_findings("/tmp/x", results, "r", None)
        assert findings, "a failing BLOCK gate must keep findings non-empty even when linon is clean"
        assert gate_ctx["conformance"], "the gate findings are carried to feed the repair agents"
        cp.CONFORMANCE_GATE_MODE = "shadow"                        # same failing gate, but shadow folds nothing
        sh_findings, _ = cp._closed_loop_findings("/tmp/x", results, "r", None)
        assert sh_findings == [], "in shadow the failing gate is telemetry only (no fold)"
    finally:
        cp._shadow_conformance, cp._secret_scan, cp._fuzz_cli, cp.CONFORMANCE_GATE_MODE = saved
    print("ok  closed loop: a block gate that fails keeps the loop open (linon-clean no longer converges)")


def test_gate_error_is_infra_advisory_for_convergence():
    # A conformance gate that ERRORED is not a product-code defect. It is still streamed, but it must not feed
    # the implementer repair loop as a code finding.
    err = cp._gate_error_report("conformance", "boom")
    assert err["passed"] is False and err["error"] and err["findings"][0]["severity"] == "critical", err
    assert err["findings"][0]["failure_classification"] == "infra", err
    assert cp._apply_conformance_gate([], err, "block") == [], "an infra gate ERROR must not drive repair"
    assert cp._apply_conformance_gate([], err, "shadow") == [], "a gate ERROR is telemetry-only in shadow"
    print("ok  gate ERROR is classified infra and remains advisory for convergence")


def test_subprocess_runner_is_resource_bounded():
    # ADR-0009 #2 codex-side bound: a normal run works; output is capped; a too-long run times out to 124.
    run = conf.subprocess_runner(timeout=1.0, max_output=10)
    ok = run("printf hi")
    assert ok.returncode == 0 and "hi" in ok.stdout, ok
    capped = run("printf abcdefghijklmnopqrstuvwxyz")
    assert "truncated at 10 bytes" in capped.stdout and len(capped.stdout) < 60, capped
    slow = run("sleep 5")
    assert slow.returncode == 124 and "timeout" in slow.stderr, slow
    print("ok  subprocess_runner: normal run ok, output capped, slow run -> 124 timeout")


def _free_local_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", 0))
        except PermissionError:
            return None
        return sock.getsockname()[1]


def _eventually_process_gone(pid_file: Path) -> bool:
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        if not pid_file.exists():
            time.sleep(0.05)
            continue
        pid = int(pid_file.read_text())
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.05)
    return False


def _eventually_closed(url: str) -> bool:
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(url, timeout=0.1).read()
        except (OSError, TimeoutError, urllib.error.URLError):
            return True
        time.sleep(0.05)
    return False


def _run_http_wiring_probe(wired: bool) -> dict:
    port = _free_local_port()
    with tempfile.TemporaryDirectory(prefix="conf-http-wire-") as tmp:
        tmpdir = Path(tmp)
        dep = tmpdir / "dependency.txt"
        dep.write_text("real-wire", encoding="utf-8")
        mode = "wired" if wired else "stub"

        if port is None:
            ready_file = tmpdir / "ready"
            script = r"""
import pathlib
import sys
import time

pathlib.Path(sys.argv[1]).write_text("ready")
while True:
    time.sleep(60)
"""
            profile = {"start": {"command": f"python3 -u -c {shlex.quote(script)} "
                                             f"{shlex.quote(str(ready_file))}"},
                       "base_url": "http://service.local", "readiness_timeout_seconds": 3,
                       "examples": [{"method": "GET", "path": "/items", "expected_status": 200,
                                     "expected_body_contains": ["real-wire"]}]}

            def req(method, url, *, json_body=None, has_json_body=False, timeout=None):
                if not ready_file.exists():
                    raise conf.urllib.error.URLError("not ready")
                body = dep.read_bytes() if wired else b"stub-wire"
                return conf.HttpResponse(200, body)

            return conf.run_http_service_conformance(
                _http_contract(profile), conf.subprocess_runner(timeout=2.0), http_request=req)

        script = r"""
import http.server
import pathlib
import socketserver
import sys

dep = pathlib.Path(sys.argv[2])
mode = sys.argv[3]

class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass
    def do_GET(self):
        body = dep.read_bytes() if mode == "wired" else b"stub-wire"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

class Server(socketserver.TCPServer):
    allow_reuse_address = True

with Server(("127.0.0.1", int(sys.argv[1])), Handler) as server:
    server.serve_forever()
"""
        base_url = f"http://127.0.0.1:{port}"
        profile = {"start": {"command": f"python3 -u -c {shlex.quote(script)} {port} "
                                        f"{shlex.quote(str(dep))} {mode}"},
                   "base_url": base_url, "readiness_timeout_seconds": 3,
                   "examples": [{"method": "GET", "path": "/items", "expected_status": 200,
                                 "expected_body_contains": ["real-wire"]}]}
        rep = conf.run_http_service_conformance(_http_contract(profile), conf.subprocess_runner(timeout=2.0))
        assert _eventually_closed(base_url), "real launched wiring-control service must be stopped"
        return rep


def test_http_service_production_boundary_probe_red_on_stub_green_on_real_wiring():
    red = _run_http_wiring_probe(wired=False)
    assert red["applicable"] and red["passed"] is False, red
    assert any(f["check"] == "body_contains" and f.get("expected") == "real-wire" for f in red["findings"]), red

    green = _run_http_wiring_probe(wired=True)
    assert green["applicable"] and green["passed"] and green["findings"] == [], green
    print("ok  http_service production-boundary probe: stubbed path RED; real wiring GREEN (D2 control)")


def test_http_service_real_subprocess_runner_launches_and_stops_service():
    port = _free_local_port()
    if port is None:
        with tempfile.TemporaryDirectory(prefix="conf-http-real-") as tmp:
            tmpdir = Path(tmp)
            pid_file = tmpdir / "pid"
            ready_file = tmpdir / "ready"
            script = r"""
import os
import pathlib
import sys
import time

pathlib.Path(sys.argv[1]).write_text(str(os.getpid()))
pathlib.Path(sys.argv[2]).write_text("ready")
while True:
    time.sleep(60)
"""
            profile = {"start": {"command": f"python3 -u -c {shlex.quote(script)} "
                                             f"{shlex.quote(str(pid_file))} {shlex.quote(str(ready_file))}"},
                       "base_url": "http://service.local", "readiness_timeout_seconds": 3,
                       "examples": [{"method": "GET", "path": "/", "expected_status": 200,
                                     "expected_body_contains": ["ok-live-http"]}]}

            def req(method, url, *, json_body=None, has_json_body=False, timeout=None):
                if not ready_file.exists():
                    raise conf.urllib.error.URLError("not ready")
                return conf.HttpResponse(200, b"ok-live-http")

            rep = conf.run_http_service_conformance(
                _http_contract(profile), conf.subprocess_runner(timeout=2.0), http_request=req)
            assert rep["applicable"] and rep["passed"] and rep["checks_run"] == 2, rep
            assert _eventually_process_gone(pid_file), "real launched http stand-in must be stopped"
            print("ok  http_service: production callable runner launches a real process and stops it")
            return

    script = r"""
import http.server
import socketserver
import sys

class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok-live-http")

class Server(socketserver.TCPServer):
    allow_reuse_address = True

with Server(("127.0.0.1", int(sys.argv[1])), Handler) as server:
    server.serve_forever()
"""
    base_url = f"http://127.0.0.1:{port}"
    profile = {"start": {"command": f"python3 -u -c {shlex.quote(script)} {port}"},
               "base_url": base_url, "readiness_timeout_seconds": 3,
               "examples": [{"method": "GET", "path": "/", "expected_status": 200,
                             "expected_body_contains": ["ok-live-http"]}]}
    rep = conf.run_http_service_conformance(_http_contract(profile), conf.subprocess_runner(timeout=2.0))
    assert rep["applicable"] and rep["passed"] and rep["checks_run"] == 2, rep
    assert _eventually_closed(base_url), "real launched http service must be stopped after conformance"
    print("ok  http_service: production callable runner launches a real process and stops it")


def test_http_service_busy_nominal_port_uses_free_ephemeral_port():
    held = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        held.bind(("127.0.0.1", 0))
    except PermissionError:
        held.close()
        original_free_port = conf._free_loopback_port
        seen = {}

        class Handle:
            def stop(self):
                seen["stopped"] = True

        def launcher(command, *, cwd=None, profile=None):
            seen["command"] = command
            seen["profile"] = profile
            return Handle()

        def req(method, url, *, json_body=None, has_json_body=False, timeout=None):
            seen.setdefault("urls", []).append(url)
            return conf.HttpResponse(200, b"ok-ephemeral-port")

        try:
            conf._free_loopback_port = lambda host: 54321
            profile = {"start": {"command": "serve --host 127.0.0.1 --port 8765"},
                       "base_url": "http://127.0.0.1:8765", "readiness_timeout_seconds": 3,
                       "examples": [{"method": "GET", "path": "/", "expected_status": 200,
                                     "expected_body_contains": ["ok-ephemeral-port"]}]}
            rep = conf.run_http_service_conformance(_http_contract(profile), fake_runner({}),
                                                    http_request=req, service_launcher=launcher)
            assert rep["applicable"] and rep["passed"] and rep["findings"] == [], rep
            assert "--port 54321" in seen["command"], seen
            assert seen["profile"]["base_url"] == "http://127.0.0.1:54321", seen
            assert all(":54321" in url for url in seen["urls"]), seen
            assert seen.get("stopped"), seen
        finally:
            conf._free_loopback_port = original_free_port
        print("ok  http_service: nominal port is rewritten to an injected free ephemeral port")
        return
    held.listen(1)
    held_port = held.getsockname()[1]
    try:
        with tempfile.TemporaryDirectory(prefix="conf-http-busy-port-") as tmp:
            marker = Path(tmp) / "bound-port"
            script = r"""
import http.server
import pathlib
import socketserver
import sys

port = int(sys.argv[sys.argv.index("--port") + 1])
marker = pathlib.Path(sys.argv[sys.argv.index("--marker") + 1])

class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass
    def do_GET(self):
        body = b"ok-ephemeral-port"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

class Server(socketserver.TCPServer):
    allow_reuse_address = True

with Server(("127.0.0.1", port), Handler) as server:
    marker.write_text(str(server.server_address[1]))
    server.serve_forever()
"""
            base_url = f"http://127.0.0.1:{held_port}"
            profile = {"start": {"command": f"python3 -u -c {shlex.quote(script)} "
                                            f"--port {held_port} --marker {shlex.quote(str(marker))}"},
                       "base_url": base_url, "readiness_timeout_seconds": 3,
                       "examples": [{"method": "GET", "path": "/", "expected_status": 200,
                                     "expected_body_contains": ["ok-ephemeral-port"]}]}
            rep = conf.run_http_service_conformance(_http_contract(profile), conf.subprocess_runner(timeout=2.0))
            assert rep["applicable"] and rep["passed"] and rep["findings"] == [], rep
            assert marker.exists(), "service should have started on a rewritten free port"
            assert int(marker.read_text()) != held_port, "the busy nominal port must not be reused"
    finally:
        held.close()
    print("ok  http_service: busy nominal port is rewritten to a free ephemeral port")


def test_rpc_service_real_subprocess_runner_launches_and_stops_json_rpc_service():
    port = _free_local_port()
    if port is None:
        with tempfile.TemporaryDirectory(prefix="conf-rpc-real-") as tmp:
            tmpdir = Path(tmp)
            pid_file = tmpdir / "pid"
            ready_file = tmpdir / "ready"
            script = r"""
import os
import pathlib
import sys
import time

pathlib.Path(sys.argv[1]).write_text(str(os.getpid()))
pathlib.Path(sys.argv[2]).write_text("ready")
while True:
    time.sleep(60)
"""
            profile = {"start": {"command": f"python3 -u -c {shlex.quote(script)} "
                                             f"{shlex.quote(str(pid_file))} {shlex.quote(str(ready_file))}"},
                       "base_url": "http://service.local", "transport": "json_rpc_http",
                       "readiness_timeout_seconds": 3,
                       "calls": [{"method": "ping", "expected_result_contains": ["ok-live-rpc"]}]}

            def req(method, url, *, json_body=None, has_json_body=False, timeout=None):
                if not ready_file.exists():
                    raise conf.urllib.error.URLError("not ready")
                if method == "GET":
                    return conf.HttpResponse(200, b"ready")
                body = {"jsonrpc": "2.0", "id": json_body["id"], "result": {"message": "ok-live-rpc"}}
                return conf.HttpResponse(200, json.dumps(body).encode())

            rep = conf.run_rpc_service_conformance(
                _rpc_contract(profile), conf.subprocess_runner(timeout=2.0), http_request=req)
            assert rep["applicable"] and rep["passed"] and rep["checks_run"] == 2, rep
            assert _eventually_process_gone(pid_file), "real launched rpc stand-in must be stopped"
            print("ok  rpc_service: production callable runner launches a real process and stops it")
            return

    script = r"""
import http.server
import json
import socketserver
import sys

class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ready")
    def do_POST(self):
        size = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(size) or b"{}")
        response = {"jsonrpc": "2.0", "id": body.get("id"), "result": {"message": "ok-live-rpc"}}
        encoded = json.dumps(response).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

class Server(socketserver.TCPServer):
    allow_reuse_address = True

with Server(("127.0.0.1", int(sys.argv[1])), Handler) as server:
    server.serve_forever()
"""
    base_url = f"http://127.0.0.1:{port}"
    profile = {"start": {"command": f"python3 -u -c {shlex.quote(script)} {port}"},
               "base_url": base_url, "transport": "json_rpc_http",
               "readiness_timeout_seconds": 3,
               "calls": [{"method": "ping", "expected_result_contains": ["ok-live-rpc"]}]}
    rep = conf.run_rpc_service_conformance(_rpc_contract(profile), conf.subprocess_runner(timeout=2.0))
    assert rep["applicable"] and rep["passed"] and rep["checks_run"] == 2, rep
    assert _eventually_closed(base_url), "real launched rpc service must be stopped after conformance"
    print("ok  rpc_service: production callable runner launches a real process and stops it")

# ---- http_service conformance (ADR-0009 #5, ported from an AI Org build) ----
class _Handle:
    def __init__(self, owner): self.owner = owner
    def stop(self): self.owner.stopped = True


class FakeBoot:
    """A runner with the http_service .start()/.stop() lifecycle shape."""
    def __init__(self, fail_start=False): self.started = False; self.stopped = False; self.fail_start = fail_start
    def start(self, command, cwd=None):
        if self.fail_start: raise RuntimeError("boot failed")
        self.started = True; return _Handle(self)


def _http_contract(profile):
    return {"role_id": "aufheben-designer", "deliverable_kind": "http_service", "conformance": {"http_service": profile}}


_HTTP_PROFILE = {"start": {"command": "serve"}, "base_url": "http://x", "readiness_timeout_seconds": 1,
                 "examples": [{"method": "GET", "path": "/health", "expected_status": 200,
                               "expected_body_contains": ["ok"]}]}


def _http_req(status=200, body=b"ok", ready=True):
    def req(method, url, *, json_body=None, has_json_body=False, timeout=None):
        if not ready:
            raise conf.urllib.error.URLError("refused")
        return conf.HttpResponse(status, body)
    return req


def test_http_service_missing_profile_is_critical():
    rep = conf.run_http_service_conformance(_http_contract({"base_url": "http://x"}), FakeBoot(), http_request=_http_req())
    assert rep["applicable"] and rep["passed"] is False
    assert rep["findings"][0]["check"] == "profile" and rep["findings"][0]["severity"] == "critical", rep
    print("ok  http_service: an incomplete profile -> one critical profile finding")


def test_http_service_success_boots_checks_and_stops():
    boot = FakeBoot()
    rep = conf.run_http_service_conformance(_http_contract(_HTTP_PROFILE), boot, http_request=_http_req(200, b"ok"),
                                            service_launcher=_launcher_from_start(boot))
    assert rep["applicable"] and rep["passed"] and rep["findings"] == [], rep
    assert boot.started and boot.stopped, "service is started and stopped"
    print("ok  http_service: boot -> ready -> examples pass -> stopped")


def test_http_service_status_mismatch_is_major():
    boot = FakeBoot()
    rep = conf.run_http_service_conformance(_http_contract(_HTTP_PROFILE), boot, http_request=_http_req(500, b"ok"),
                                            service_launcher=_launcher_from_start(boot))
    checks = {f["check"] for f in rep["findings"]}
    assert "status" in checks and not rep["passed"], rep["findings"]
    print("ok  http_service: wrong status -> major status finding")


def test_http_service_readiness_timeout_skips_examples():
    boot = FakeBoot()
    rep = conf.run_http_service_conformance(_http_contract(_HTTP_PROFILE), boot, http_request=_http_req(ready=False),
                                            service_launcher=_launcher_from_start(boot))
    assert not rep["passed"] and any(f["check"] == "lifecycle" for f in rep["findings"]), rep
    assert boot.stopped, "the service is still stopped after a readiness timeout"
    print("ok  http_service: readiness timeout -> critical lifecycle, examples skipped, still stopped")


def test_http_service_start_failure_reports_exit_and_captured_stderr():
    profile = {"start": {"command": "python3 -c 'import sys; sys.stderr.write(\"boot exploded\"); sys.exit(7)'"},
               "base_url": "http://service.local", "readiness_timeout_seconds": 1,
               "examples": [{"method": "GET", "path": "/", "expected_status": 200}]}
    rep = conf.run_http_service_conformance(_http_contract(profile), conf.subprocess_runner(timeout=2.0),
                                            http_request=_http_req())
    finding = rep["findings"][0]
    assert not rep["passed"] and finding["check"] == "lifecycle", rep
    assert finding["returncode"] == 7 and "boot exploded" in finding["stderr_tail"], finding
    assert finding.get("error") != "AttributeError", finding
    # incr #3 safe flip: a service that exits nonzero on boot is GENUINELY AMBIGUOUS (a code/config bug vs a
    # missing host dependency), so it is `undetermined` -> clean-retry/unverified (#1/#2), not blamed as `code`.
    assert finding["failure_classification"] == "undetermined", finding
    assert finding["repair_route"] != "implementer", finding
    print("ok  http_service: failed start reports real exit code and captured stderr")


def test_http_service_address_in_use_lifecycle_is_infra():
    def launcher(command, *, cwd=None, profile=None):
        raise OSError(48, "Address already in use")

    rep = conf.run_http_service_conformance(_http_contract(_HTTP_PROFILE), FakeBoot(), http_request=_http_req(),
                                            service_launcher=launcher)
    f = next(f for f in rep["findings"] if f["check"] == "lifecycle")
    assert not rep["passed"] and f["failure_classification"] == "infra", f
    assert cp._apply_conformance_gate([], rep, "block") == [], "infra lifecycle findings must be advisory"
    print("ok  http_service: address-in-use lifecycle failure is classified infra and advisory")


def test_http_service_not_applicable_for_non_http():
    rep = conf.run_conformance(_cli_contract({"entrypoint": {"invocation": "t"}, "examples": []}),
                               fake_runner({"t": R(0, "", "")}))
    assert rep.get("applicable") is True   # cli path still works through the dispatcher
    print("ok  dispatcher still routes cli (http_service port did not break it)")


# --- forbidden-pattern gate (ADR-0016 D7): the cheap, kind-agnostic grep gate ----------------------------

def _fp_contract(patterns, kind="none"):
    """A contract carrying forbidden_patterns. kind='none' proves the gate is kind-agnostic — it fires even
    where no per-kind conformance profile exists."""
    return {"role_id": "aufheben-designer", "deliverable_kind": kind, "forbidden_patterns": patterns}


def test_forbidden_pattern_straggler_blocks_with_file_line():
    # (a) the live motivation: a scaffold-rename left `_scaffold_seed_commit` behind -> BLOCKING finding.
    with _ws({"demo.py": "def go():\n    return _scaffold_seed_commit()\n"}) as d:
        rep = conf.run_conformance(_fp_contract([{"pattern": "_scaffold_seed_commit",
                                                  "reason": "renamed to _seed_commit"}]),
                                   fake_runner({}), cwd=d)
    assert rep["applicable"] and not rep["passed"], rep
    f = next(f for f in rep["findings"] if f["source"] == "forbidden-pattern")
    assert f["severity"] == "major" and not f["passed"], f
    assert f["pattern"] == "_scaffold_seed_commit" and f["count"] == 1, f
    assert f["hits"] == ["demo.py:2"], f                      # snake_case ref caught (no token-regex blind spot)
    assert "renamed to _seed_commit" in f["detail"], f
    # FIX 2 / acceptance (a): the BLOCKING finding carries an ACTIONABLE fix_hint naming the pattern, the
    # remove/rename action, and the captured hit location(s) — not a vague "X failed".
    fh = f["fix_hint"]
    assert fh and "_scaffold_seed_commit" in fh, f
    assert ("remove" in fh.lower() or "rename" in fh.lower()), f
    assert "demo.py:2" in fh, f                                # references the actual hit location
    assert "renamed to _seed_commit" in fh, f                  # carries the reason when declared
    print("ok  forbidden-pattern: a rename straggler blocks with pattern + file:line hit + actionable fix_hint")


def test_forbidden_pattern_fix_hint_counts_unshown_hits():
    # the hit evidence is capped (_FORBIDDEN_MAX_HITS_REPORTED), so the fix_hint must still say HOW MANY total
    # occurrences remain — "(and N more)" — so the implementer does not stop after fixing only the shown few.
    body = "TODO\n" * 8                                        # 8 occurrences, only ~5 hits captured
    with _ws({"a.py": body}) as d:
        rep = conf.run_conformance(_fp_contract([{"pattern": "TODO"}]), fake_runner({}), cwd=d)
    f = next(f for f in rep["findings"] if f["source"] == "forbidden-pattern")
    assert f["count"] == 8 and len(f["hits"]) == conf._FORBIDDEN_MAX_HITS_REPORTED, f
    assert "8 occurrence" in f["fix_hint"] and "more)" in f["fix_hint"], f
    print("ok  forbidden-pattern: fix_hint counts total occurrences incl. unshown hits")


def test_forbidden_pattern_clean_tree_passes():
    # (b) the token is fully gone -> no finding, the gate passes.
    with _ws({"demo.py": "def go():\n    return _seed_commit()\n"}) as d:
        rep = conf.run_conformance(_fp_contract([{"pattern": "_scaffold_seed_commit"}]), fake_runner({}), cwd=d)
    assert rep["applicable"] and rep["passed"] and rep["findings"] == [], rep
    print("ok  forbidden-pattern: a clean tree (token absent) passes with no finding")


def test_forbidden_pattern_match_inside_exclude_does_not_block():
    # (c) the only remaining occurrence is in an excluded file (e.g. the changelog) -> does NOT block.
    with _ws({"src.py": "x = _seed_commit()\n",
              "CHANGELOG.md": "renamed _scaffold_seed_commit -> _seed_commit\n"}) as d:
        rep = conf.run_conformance(_fp_contract([{"pattern": "_scaffold_seed_commit",
                                                  "exclude": ["CHANGELOG.md"]}]), fake_runner({}), cwd=d)
    assert rep["applicable"] and rep["passed"] and rep["findings"] == [], rep
    print("ok  forbidden-pattern: a match inside an exclude glob does not block")


def test_forbidden_pattern_max_occurrences_honored():
    # (d) max_occurrences budget: N allowed passes, N+1 blocks.
    two = {"a.py": "TODO\nTODO\n"}
    with _ws(two) as d:
        ok = conf.run_conformance(_fp_contract([{"pattern": "TODO", "max_occurrences": 2}]), fake_runner({}), cwd=d)
    assert ok["passed"] and ok["findings"] == [], ok
    three = {"a.py": "TODO\nTODO\nTODO\n"}
    with _ws(three) as d:
        bad = conf.run_conformance(_fp_contract([{"pattern": "TODO", "max_occurrences": 2}]), fake_runner({}), cwd=d)
    f = next(f for f in bad["findings"] if f["source"] == "forbidden-pattern")
    assert not bad["passed"] and f["count"] == 3 and f["max_occurrences"] == 2, f
    print("ok  forbidden-pattern: max_occurrences honored (N allowed, N+1 blocks)")


def test_forbidden_pattern_absent_is_noop():
    # (e) no forbidden_patterns declared -> not applicable, no finding, no false positive.
    with _ws({"a.py": "_scaffold_seed_commit\n"}) as d:
        rep = conf.run_conformance({"role_id": "aufheben-designer", "deliverable_kind": "none"},
                                   fake_runner({}), cwd=d)
    assert rep["applicable"] is False and rep["passed"] and rep["findings"] == [], rep
    empty = conf.run_forbidden_patterns(_fp_contract([]), cwd=d)
    assert empty["applicable"] is False and empty["passed"], empty
    print("ok  forbidden-pattern: absent (or empty) -> no-op, no false positive")


def test_forbidden_pattern_skips_binary_and_git():
    # (f) the token lives only in a binary blob and under .git/ -> both skipped, gate passes.
    with _ws({}) as d:
        os.makedirs(os.path.join(d, ".git"))
        with open(os.path.join(d, ".git", "COMMIT_EDITMSG"), "w") as fh:
            fh.write("_scaffold_seed_commit\n")              # under .git -> pruned by the walk
        with open(os.path.join(d, "blob.bin"), "wb") as fh:
            fh.write(b"prefix\x00_scaffold_seed_commit\x00tail")  # NUL byte -> sniffed as binary, skipped
        rep = conf.run_conformance(_fp_contract([{"pattern": "_scaffold_seed_commit"}]), fake_runner({}), cwd=d)
    assert rep["applicable"] and rep["passed"] and rep["findings"] == [], rep
    print("ok  forbidden-pattern: binary files and .git/ are skipped")


def test_forbidden_pattern_skips_runtime_scratch_but_blocks_delivered_source():
    with _ws({}) as d:
        os.makedirs(os.path.join(d, ".agent-runs", "controller", "run1"), exist_ok=True)
        os.makedirs(os.path.join(d, ".git", "logs"), exist_ok=True)
        os.makedirs(os.path.join(d, "pkg", "__pycache__"), exist_ok=True)
        Path(d, ".agent-runs", "controller", "run1", "result.json").write_text("SHAGIRI_SCAFFOLD\n")
        Path(d, ".git", "logs", "HEAD").write_text("SHAGIRI_SCAFFOLD\n")
        Path(d, "pkg", "__pycache__", "mod.py").write_text("SHAGIRI_SCAFFOLD\n")

        contract = _fp_contract([{"pattern": "SHAGIRI_SCAFFOLD"}])
        scratch_only = conf.run_conformance(contract, fake_runner({}), cwd=d)
        assert scratch_only["applicable"] and scratch_only["passed"] and scratch_only["findings"] == [], scratch_only

        Path(d, "delivered.py").write_text("SHAGIRI_SCAFFOLD\n")
        delivered = conf.run_conformance(contract, fake_runner({}), cwd=d)
    f = next(f for f in delivered["findings"] if f["source"] == "forbidden-pattern")
    assert not delivered["passed"] and f["hits"] == ["delivered.py:1"], delivered
    assert f["count"] == 1, f
    assert f["failure_classification"] == "code", f
    print("ok  forbidden-pattern: runtime scratch is skipped; delivered-source hit still blocks")


def test_forbidden_pattern_folds_alongside_cli_checker():
    # the gate is kind-agnostic: it rides ALONGSIDE a per-kind (cli) checker, not instead of it. A clean CLI
    # run with a forbidden straggler still blocks, and checks_run sums both gates.
    with _ws({"tool.py": "old_name = 1\n"}) as d:
        contract = {"role_id": "aufheben-designer", "deliverable_kind": "cli",
                    "conformance": {"cli": {"entrypoint": {"invocation": "echo hi"},
                                            "examples": [{"invocation": "", "expected_status": 0}]}},
                    "forbidden_patterns": [{"pattern": "old_name"}]}
        rep = conf.run_conformance(contract, fake_runner({"echo hi": R(0, "", "")}), cwd=d)
    sources = {f["source"] for f in rep["findings"]}
    assert not rep["passed"] and "forbidden-pattern" in sources, rep
    assert rep["checks_run"] >= 2, rep                        # cli examples + 1 forbidden pattern
    print("ok  forbidden-pattern: folds alongside the per-kind (cli) checker")


def test_forbidden_pattern_is_a_deterministic_impl_source():
    # the source must be recognized as a BLOCKING deterministic implementation source, so gate-behind can
    # route a straggler to the implementer ONLY and skip the expensive Linon reviewer.
    assert "forbidden-pattern" in cp._DETERMINISTIC_IMPL_SOURCES
    findings = [{"source": "forbidden-pattern", "passed": False}]
    assert cp._repair_roles_for(findings, ["aufheben-designer", "implementer"]) == ["implementer"], \
        "a pure forbidden-pattern defect must route to the implementer only"
    print("ok  forbidden-pattern: recognized as a deterministic impl source (implementer-only repair)")


def test_schema_forbidden_patterns_is_optional_and_shaped():
    try:
        import jsonschema
    except ImportError:
        print("skip  jsonschema not installed")
        return
    v = jsonschema.Draft202012Validator(json.loads(SCHEMA.read_text()))
    base = {"role_id": "aufheben-designer", "contract_id": "c", "objective": "o", "selected_direction": "d",
            "rejected_parts": [], "implementation_summary": "s", "acceptance_criteria": [],
            "files_allowed_to_change": [], "files_not_allowed_to_change": [], "required_checks": [],
            "security_requirements": [], "nonfunctional_requirements": [], "non_goals": [], "risks": [],
            "fallback_plan": "f", "handoff_to_implementer": "h", "deliverable_kind": "none"}
    assert v.is_valid(base), list(v.iter_errors(base))        # optional: absent still validates
    ok = {**base, "forbidden_patterns": [{"pattern": "_scaffold_seed_commit",
                                          "exclude": ["CHANGELOG.md"], "max_occurrences": 0, "reason": "renamed"}]}
    assert v.is_valid(ok), list(v.iter_errors(ok))
    assert not v.is_valid({**base, "forbidden_patterns": [{"reason": "no pattern"}]}), "pattern is required"
    assert not v.is_valid({**base, "forbidden_patterns": [{"pattern": "x", "max_occurrences": -1}]}), \
        "max_occurrences must be >= 0"
    assert not v.is_valid({**base, "forbidden_patterns": [{"pattern": "x", "junk": 1}]}), "no extra props"
    print("ok  schema: forbidden_patterns is optional and shape-constrained")


# --- regression-suite gate (ADR-0009 / ADR-0016): the cheap, kind-agnostic "did the change break working code" gate ---

def _rs_contract(suite, kind="none"):
    """A contract carrying regression_suite. kind='none' proves the gate is kind-agnostic — it fires even where
    no per-kind conformance profile exists."""
    return {"role_id": "aufheben-designer", "deliverable_kind": kind, "regression_suite": suite}


def test_regression_suite_failure_blocks_with_exit_code_and_tail():
    # (a) the pre-existing suite now fails (exit 1) -> a BLOCKING `regression` finding with exit code + output tail.
    cmd = "python3 -m pytest -q"
    runner = fake_runner({cmd: R(1, "collected 3 items\n", "FAILED tests/test_core.py::test_add - assert 3 == 4\n")})
    rep = conf.run_conformance(_rs_contract({"command": cmd, "reason": "core suite must stay green"}), runner)
    assert rep["applicable"] and not rep["passed"], rep
    f = next(f for f in rep["findings"] if f["source"] == "regression")
    assert f["severity"] == "major" and not f["passed"], f
    assert f["check"] == "regression_suite" and f["command"] == cmd and f["exit_code"] == 1, f
    assert f["failure_classification"] == "code", f
    assert "FAILED tests/test_core.py::test_add" in f["output_tail"], f      # failing test name captured
    assert "core suite must stay green" in f["detail"], f
    # FIX 2 / acceptance (b): the BLOCKING finding carries an ACTIONABLE fix_hint naming the failing command,
    # the exit code, the "do not modify the tests" guardrail, and the parsed failing test name.
    fh = f["fix_hint"]
    assert fh and cmd in fh, f
    assert "do not modify the tests" in fh.lower(), f
    assert "exit 1" in fh, f                                   # the exit code is on the hint, not only the detail
    assert "tests/test_core.py::test_add" in fh, f            # failing test name parsed out of the output
    print("ok  regression: a failing pre-existing suite blocks with exit code + failing-test tail + fix_hint")


def test_regression_fix_hint_present_even_when_tests_unparseable():
    # acceptance (d): when the failing test names can't be parsed, the fix_hint is STILL emitted (command +
    # exit code + the no-edit-tests guardrail are mechanically known) — concrete, never vague filler, never
    # omitted just because the test names were not extractable.
    cmd = "make check"
    rep = conf.run_regression_suite(_rs_contract({"command": cmd}),
                                    fake_runner({cmd: R(2, "build noise with no test ids\n", "boom\n")}))
    f = next(f for f in rep["findings"] if f["source"] == "regression")
    fh = f["fix_hint"]
    assert fh and cmd in fh and "exit 2" in fh and "do not modify the tests" in fh.lower(), f
    assert "Failing:" not in fh, f                             # no fabricated test names when none parsed
    print("ok  regression: fix_hint emitted (command + exit + guardrail) even when test names unparseable")


def test_regression_suite_passing_command_is_clean_pass():
    # (b) the suite still passes (exit 0) -> no finding, the gate passes.
    cmd = "python3 scripts/test_foo.py"
    rep = conf.run_conformance(_rs_contract({"command": cmd}), fake_runner({cmd: R(0, "5 passed\n", "")}))
    assert rep["applicable"] and rep["passed"] and rep["findings"] == [], rep
    print("ok  regression: a passing suite (exit 0) passes with no finding")


def test_regression_suite_missing_pytest_is_infra_advisory():
    cmd = "python3 -m pytest -q"
    rep = conf.run_regression_suite(_rs_contract({"command": cmd}),
                                    fake_runner({cmd: R(127, "", "No module named pytest\n")}))
    f = next(f for f in rep["findings"] if f["source"] == "regression")
    assert not rep["passed"] and f["failure_classification"] == "infra", f
    assert "fix_hint" not in f, f
    assert cp._apply_conformance_gate([], rep, "block") == [], "missing pytest must not block convergence"
    print("ok  regression: missing pytest is infra/advisory, not a code regression")


def test_regression_suite_product_missing_dependency_blocks():
    cmd = "python3 app.py"
    output = "Traceback (most recent call last):\nModuleNotFoundError: No module named 'requests'\n"
    rep = conf.run_regression_suite(_rs_contract({"command": cmd}),
                                    fake_runner({cmd: R(1, "", output)}))
    f = next(f for f in rep["findings"] if f["source"] == "regression")
    assert not rep["passed"] and f["failure_classification"] == "code", f
    assert "fix_hint" in f, f
    assert cp._apply_conformance_gate([], rep, "block") == [f], "product missing dependency must block"
    print("ok  regression: product ModuleNotFoundError is code and blocks convergence")


def test_regression_suite_timeout_is_advisory_timeout():
    cmd = "python3 -m pytest -q"
    rep = conf.run_regression_suite(_rs_contract({"command": cmd, "timeout_seconds": 1}),
                                    fake_runner({cmd: R(124, "", "<conformance: timeout after 1s>")}))
    f = next(f for f in rep["findings"] if f["source"] == "regression")
    assert not rep["passed"] and f["failure_classification"] == "timeout", f
    assert cp._apply_conformance_gate([], rep, "block") == [], "timeout must not drive repair"
    print("ok  regression: rc 124 is classified timeout and stays advisory")


def test_regression_suite_absent_is_noop():
    # (c) no regression_suite declared -> not applicable, no finding, no false positive (runner never invoked).
    rep = conf.run_conformance({"role_id": "aufheben-designer", "deliverable_kind": "none"}, fake_runner({}))
    assert rep["applicable"] is False and rep["passed"] and rep["findings"] == [], rep
    # an empty / command-less suite is likewise a vacuous pass, not a fabricated finding.
    for empty in ({}, {"command": ""}, {"command": "   "}, {"reason": "no command"}):
        r = conf.run_regression_suite({"regression_suite": empty}, fake_runner({}))
        assert r["applicable"] is False and r["passed"] and r["findings"] == [], (empty, r)
    print("ok  regression: absent / command-less -> no-op, runner never run, no false positive")


def test_regression_suite_garbled_runner_output_does_not_crash():
    # (d) a garbled / partial runner result (None fields, missing returncode, non-string output) must still
    # produce a finding without crashing the gate.
    cmd = "make test"

    class _Garbled:                                            # a result missing returncode, with odd output types
        stdout = None
        stderr = 12345

    for bad in (_Garbled(), None, R(2, None, None)):
        r = conf.run_regression_suite(_rs_contract({"command": cmd}), lambda c, *, cwd=None, timeout=None, _b=bad: _b)
        f = next(f for f in r["findings"] if f["source"] == "regression")
        assert not r["passed"] and isinstance(f["output_tail"], str), (bad, r)   # never raises, tail is a string
    print("ok  regression: garbled / partial runner output yields a finding, never crashes")


def test_regression_suite_timeout_passed_to_runner():
    # (e) the declared timeout_seconds is passed through to the runner; absent -> the default budget is used.
    cmd = "python3 -m pytest -q"
    seen = {}

    def spy(c, *, cwd=None, timeout=None):
        seen["cmd"], seen["timeout"] = c, timeout
        return R(0, "", "")

    conf.run_regression_suite(_rs_contract({"command": cmd, "timeout_seconds": 45}), spy)
    assert seen["cmd"] == cmd and seen["timeout"] == 45, seen
    seen.clear()
    conf.run_regression_suite(_rs_contract({"command": cmd}), spy)
    assert seen["timeout"] == conf._REGRESSION_DEFAULT_TIMEOUT_SECONDS, seen
    # and the failing finding records the budget it ran under.
    rep = conf.run_regression_suite(_rs_contract({"command": cmd, "timeout_seconds": 45}),
                                    lambda c, *, cwd=None, timeout=None: R(1, "", "boom"))
    f = next(f for f in rep["findings"] if f["source"] == "regression")
    assert f["timeout_seconds"] == 45, f
    print("ok  regression: timeout_seconds is passed to the runner (and recorded on the finding)")


def test_subprocess_runner_honors_per_call_timeout_override():
    # the production runner accepts a per-call timeout override (so a regression_suite's budget is real, not
    # only the baked-in default) while staying backward compatible with timeout-less callers.
    run = conf.subprocess_runner(timeout=30)
    fast = run("printf ok", cwd=None)                          # no override -> default budget, runs fine
    assert fast.returncode == 0 and "ok" in fast.stdout, fast
    slow = run("sleep 2", timeout=1)                           # override below the sleep -> times out (124)
    assert slow.returncode == 124 and "timeout after 1" in slow.stderr, slow
    print("ok  regression: subprocess_runner honors a per-call timeout override")


def test_regression_suite_folds_alongside_cli_checker():
    # the gate is kind-agnostic: it rides ALONGSIDE a per-kind (cli) checker. A clean CLI run with a broken
    # regression suite still blocks, and checks_run sums both gates.
    cmd = "python3 -m pytest -q"
    contract = {"role_id": "aufheben-designer", "deliverable_kind": "cli",
                "conformance": {"cli": {"entrypoint": {"invocation": "echo hi"},
                                        "examples": [{"invocation": "", "expected_status": 0}]}},
                "regression_suite": {"command": cmd}}
    rep = conf.run_conformance(contract, fake_runner({"echo hi": R(0, "", ""), cmd: R(1, "", "FAILED\n")}))
    sources = {f["source"] for f in rep["findings"]}
    assert not rep["passed"] and "regression" in sources, rep
    assert rep["checks_run"] >= 2, rep                         # cli examples + 1 regression suite
    print("ok  regression: folds alongside the per-kind (cli) checker")


def test_regression_is_a_deterministic_impl_source():
    # the source must be a BLOCKING deterministic implementation source, so gate-behind routes a regression to
    # the implementer ONLY and skips the expensive Linon reviewer.
    assert "regression" in cp._DETERMINISTIC_IMPL_SOURCES
    findings = [{"source": "regression", "passed": False}]
    assert cp._repair_roles_for(findings, ["aufheben-designer", "implementer"]) == ["implementer"], \
        "a pure regression defect must route to the implementer only"
    print("ok  regression: recognized as a deterministic impl source (implementer-only repair)")


def test_other_gate_findings_omit_fix_hint_no_vague_filler():
    # acceptance (d): fix_hint is ADDITIVE and only on gates whose fix is mechanically known. A gate whose
    # remediation is NOT known (e.g. a cli exit-status mismatch — the implementer must reason about WHY) emits
    # NO fix_hint key rather than vague filler.
    profile = {"entrypoint": {"invocation": "mytool"},
               "examples": [{"invocation": "--bad", "expected_status": 0}]}
    rep = conf.run_cli_conformance(_cli_contract(profile), fake_runner({"mytool --bad": R(3, "", "nope")}))
    f = next(f for f in rep["findings"] if f["check"] == "exit_status")
    assert "fix_hint" not in f, f                              # absent, not present-but-empty
    print("ok  fix_hint: omitted (not vague filler) on gates whose remediation is not mechanically known")


def test_schema_regression_suite_is_optional_and_shaped():
    try:
        import jsonschema
    except ImportError:
        print("skip  jsonschema not installed")
        return
    v = jsonschema.Draft202012Validator(json.loads(SCHEMA.read_text()))
    base = {"role_id": "aufheben-designer", "contract_id": "c", "objective": "o", "selected_direction": "d",
            "rejected_parts": [], "implementation_summary": "s", "acceptance_criteria": [],
            "files_allowed_to_change": [], "files_not_allowed_to_change": [], "required_checks": [],
            "security_requirements": [], "nonfunctional_requirements": [], "non_goals": [], "risks": [],
            "fallback_plan": "f", "handoff_to_implementer": "h", "deliverable_kind": "none"}
    assert v.is_valid(base), list(v.iter_errors(base))         # optional: absent still validates
    ok = {**base, "regression_suite": {"command": "python3 -m pytest -q", "timeout_seconds": 120,
                                       "reason": "core suite must stay green"}}
    assert v.is_valid(ok), list(v.iter_errors(ok))
    assert not v.is_valid({**base, "regression_suite": {"reason": "no command"}}), "command is required"
    assert not v.is_valid({**base, "regression_suite": {"command": ""}}), "command must be non-empty"
    assert not v.is_valid({**base, "regression_suite": {"command": "x", "timeout_seconds": 0}}), \
        "timeout_seconds must be >= 1"
    assert not v.is_valid({**base, "regression_suite": {"command": "x", "junk": 1}}), "no extra props"
    print("ok  schema: regression_suite is optional and shape-constrained")


# --- static-checks gate (ADR-0009 / ADR-0016): the cheap, kind-agnostic "did the change hallucinate" gate ---
# (catches invented/undefined APIs, undefined variables, syntax errors via declared static analyzers) ---

def _sc_contract(checks, kind="none"):
    """A contract carrying static_checks. kind='none' proves the gate is kind-agnostic — it fires even where no
    per-kind conformance profile exists."""
    return {"role_id": "aufheben-designer", "deliverable_kind": kind, "static_checks": checks}


def test_static_check_failure_blocks_with_exit_code_and_fix_hint():
    # (a) a declared static analyzer exits non-zero -> a BLOCKING `static-check` finding with the exit code and a
    # non-empty actionable fix_hint.
    cmd = "python3 -m pyflakes ."
    runner = fake_runner({cmd: R(1, "", "server.py:12:5 undefined name 'reqeusts'\n")})
    rep = conf.run_conformance(_sc_contract([{"command": cmd, "reason": "no undefined names"}]), runner)
    assert rep["applicable"] and not rep["passed"], rep
    f = next(f for f in rep["findings"] if f["source"] == "static-check")
    assert f["severity"] == "major" and not f["passed"], f
    assert f["check"] == "static_checks" and f["command"] == cmd and f["exit_code"] == 1, f
    assert f["failure_classification"] == "code", f
    assert "undefined name 'reqeusts'" in f["output_tail"], f   # the analyzer's reported error captured
    assert "no undefined names" in f["detail"], f               # declared reason surfaced
    # acceptance (a): the BLOCKING finding carries a NON-EMPTY actionable fix_hint naming the failing command,
    # the exit code, and the "do not silence the analyzer" guardrail.
    fh = f["fix_hint"]
    assert fh and cmd in fh, f
    assert "exit 1" in fh, f                                     # the exit code is on the hint, not only the detail
    assert "do not silence the analyzer" in fh.lower(), f
    print("ok  static-check: a failing analyzer blocks with exit code + output tail + actionable fix_hint")


def test_static_checks_all_pass_is_clean_pass():
    # (b) every declared analyzer exits 0 -> no finding, the gate passes.
    checks = [{"command": "python3 -m py_compile cockpit/server.py"}, {"command": "ruff check"}]
    runner = fake_runner({"python3 -m py_compile cockpit/server.py": R(0, "", ""), "ruff check": R(0, "All checks passed!\n", "")})
    rep = conf.run_conformance(_sc_contract(checks), runner)
    assert rep["applicable"] and rep["passed"] and rep["findings"] == [], rep
    assert rep["checks_run"] >= 2, rep                           # both analyzers counted
    print("ok  static-check: all analyzers passing (exit 0) passes with no finding")


def test_static_check_command_not_found_is_infra_advisory():
    cmd = "pyflakes ."
    rep = conf.run_static_checks(_sc_contract([{"command": cmd}]),
                                 fake_runner({cmd: R(127, "", "sh: pyflakes: command not found\n")}))
    f = next(f for f in rep["findings"] if f["source"] == "static-check")
    assert not rep["passed"] and f["failure_classification"] == "infra", f
    assert "fix_hint" not in f, f
    assert cp._apply_conformance_gate([], rep, "block") == [], "missing analyzer binary must not block"
    print("ok  static-check: command-not-found is infra/advisory, not a code defect")


def test_static_checks_absent_or_empty_is_noop():
    # (c) no static_checks declared, or an empty/command-less list -> not applicable, no finding, no FP, runner
    # never invoked.
    rep = conf.run_conformance({"role_id": "aufheben-designer", "deliverable_kind": "none"}, fake_runner({}))
    assert rep["applicable"] is False and rep["passed"] and rep["findings"] == [], rep
    for empty in ([], [{}], [{"command": ""}], [{"command": "   "}], [{"reason": "no command"}], "not-a-list"):
        r = conf.run_static_checks({"static_checks": empty}, fake_runner({}))
        assert r["applicable"] is False and r["passed"] and r["findings"] == [], (empty, r)
    print("ok  static-check: absent / empty / command-less -> no-op, runner never run, no false positive")


def test_static_checks_multiple_one_failing_blocks_naming_the_failing_one():
    # (d) several analyzers, exactly ONE failing -> the gate blocks and the finding NAMES the failing command
    # (not the passing ones); checks_run counts all declared analyzers.
    ok1, ok2, bad = "python3 -m py_compile app.py", "ruff check", "python3 -m pyflakes ."
    runner = fake_runner({
        ok1: R(0, "", ""),
        ok2: R(0, "", ""),
        bad: R(1, "", "app.py:3:1 undefined name 'foo'\n"),
    })
    rep = conf.run_conformance(_sc_contract([{"command": ok1}, {"command": ok2}, {"command": bad}]), runner)
    assert not rep["passed"], rep
    sc = [f for f in rep["findings"] if f["source"] == "static-check"]
    assert len(sc) == 1 and sc[0]["command"] == bad, sc       # only the failing analyzer surfaces, by name
    assert bad in sc[0]["fix_hint"] and ok1 not in sc[0]["fix_hint"], sc
    assert rep["checks_run"] >= 3, rep                          # all three analyzers counted
    print("ok  static-check: one failing analyzer among several blocks, naming the failing one")


def test_static_checks_garbled_runner_output_does_not_crash():
    # a garbled / partial runner result (None fields, missing returncode, non-string output) must still produce a
    # finding without crashing the gate (the exit code is treated as != 0 -> blocks).
    cmd = "tsc --noEmit"

    class _Garbled:                                            # a result missing returncode, with odd output types
        stdout = None
        stderr = 12345

    for bad in (_Garbled(), None, R(2, None, None)):
        r = conf.run_static_checks(_sc_contract([{"command": cmd}]), lambda c, *, cwd=None, timeout=None, _b=bad: _b)
        f = next(f for f in r["findings"] if f["source"] == "static-check")
        assert not r["passed"] and isinstance(f["output_tail"], str), (bad, r)   # never raises, tail is a string
    print("ok  static-check: garbled / partial runner output yields a finding, never crashes")


def test_static_checks_timeout_passed_to_runner():
    # the declared timeout_seconds is passed through to the runner per check; absent -> the default budget.
    cmd = "python3 -m pyflakes ."
    seen = {}

    def spy(c, *, cwd=None, timeout=None):
        seen["cmd"], seen["timeout"] = c, timeout
        return R(0, "", "")

    conf.run_static_checks(_sc_contract([{"command": cmd, "timeout_seconds": 30}]), spy)
    assert seen["cmd"] == cmd and seen["timeout"] == 30, seen
    seen.clear()
    conf.run_static_checks(_sc_contract([{"command": cmd}]), spy)
    assert seen["timeout"] == conf._STATIC_CHECK_DEFAULT_TIMEOUT_SECONDS, seen
    # and the failing finding records the budget it ran under.
    rep = conf.run_static_checks(_sc_contract([{"command": cmd, "timeout_seconds": 30}]),
                                 lambda c, *, cwd=None, timeout=None: R(1, "", "boom"))
    f = next(f for f in rep["findings"] if f["source"] == "static-check")
    assert f["timeout_seconds"] == 30, f
    print("ok  static-check: timeout_seconds is passed to the runner (and recorded on the finding)")


def test_static_checks_folds_alongside_cli_checker():
    # the gate is kind-agnostic: it rides ALONGSIDE a per-kind (cli) checker. A clean CLI run with a failing
    # static analyzer still blocks, and checks_run sums both gates.
    cmd = "python3 -m pyflakes ."
    contract = {"role_id": "aufheben-designer", "deliverable_kind": "cli",
                "conformance": {"cli": {"entrypoint": {"invocation": "echo hi"},
                                        "examples": [{"invocation": "", "expected_status": 0}]}},
                "static_checks": [{"command": cmd}]}
    rep = conf.run_conformance(contract, fake_runner({"echo hi": R(0, "", ""), cmd: R(1, "", "undefined name\n")}))
    sources = {f["source"] for f in rep["findings"]}
    assert not rep["passed"] and "static-check" in sources, rep
    assert rep["checks_run"] >= 2, rep                          # cli examples + 1 static check
    print("ok  static-check: folds alongside the per-kind (cli) checker")


def test_static_check_is_a_deterministic_impl_source():
    # (e) the source must be a BLOCKING deterministic implementation source, so gate-behind routes a pure
    # static-check defect to the implementer ONLY and skips the expensive Linon reviewer.
    assert "static-check" in cp._DETERMINISTIC_IMPL_SOURCES
    findings = [{"source": "static-check", "passed": False}]
    assert cp._repair_roles_for(findings, ["aufheben-designer", "implementer"]) == ["implementer"], \
        "a pure static-check defect must route to the implementer only"
    print("ok  static-check: recognized as a deterministic impl source (implementer-only repair)")


def test_schema_static_checks_is_optional_and_shaped():
    try:
        import jsonschema
    except ImportError:
        print("skip  jsonschema not installed")
        return
    v = jsonschema.Draft202012Validator(json.loads(SCHEMA.read_text()))
    base = {"role_id": "aufheben-designer", "contract_id": "c", "objective": "o", "selected_direction": "d",
            "rejected_parts": [], "implementation_summary": "s", "acceptance_criteria": [],
            "files_allowed_to_change": [], "files_not_allowed_to_change": [], "required_checks": [],
            "security_requirements": [], "nonfunctional_requirements": [], "non_goals": [], "risks": [],
            "fallback_plan": "f", "handoff_to_implementer": "h", "deliverable_kind": "none"}
    assert v.is_valid(base), list(v.iter_errors(base))          # optional: absent still validates
    ok = {**base, "static_checks": [{"command": "python3 -m pyflakes .", "timeout_seconds": 60,
                                     "reason": "no undefined names"},
                                    {"command": "python3 -m py_compile cockpit/server.py"}]}
    assert v.is_valid(ok), list(v.iter_errors(ok))
    assert v.is_valid({**base, "static_checks": []}), "empty list still validates (vacuous no-op)"
    assert not v.is_valid({**base, "static_checks": [{"reason": "no command"}]}), "command is required"
    assert not v.is_valid({**base, "static_checks": [{"command": ""}]}), "command must be non-empty"
    assert not v.is_valid({**base, "static_checks": [{"command": "x", "timeout_seconds": 0}]}), \
        "timeout_seconds must be >= 1"
    assert not v.is_valid({**base, "static_checks": [{"command": "x", "junk": 1}]}), "no extra props"
    assert not v.is_valid({**base, "static_checks": {"command": "x"}}), "must be a list, not an object"
    print("ok  schema: static_checks is optional, a list, and shape-constrained")


# ---------------------------------------------------------------------------------------------------------
# GOAL-LEVEL acceptance gate (ADR-0016 D7) — run_goal_acceptance boots the COMPOSED artifact and probes it
# against the OWNER's intake-fixed executable profile, reusing the per-leaf service-boot helpers (so the same
# rlimit sandbox + GUARANTEED killpg teardown apply). These tests boot a REAL process; if the sandbox cannot
# bind a port they fall back to a real stand-in process + an injected http_request (mirroring the existing
# real-service tests). The helper ASSERTS the process group is torn down on EVERY call — proving (e).
_GOAL_SERVER = r"""
import http.server, os, pathlib, socketserver, sys

pathlib.Path(sys.argv[2]).write_text(str(os.getpid()))
serves = sys.argv[3] == "1"

class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass
    def do_GET(self):
        p = self.path
        if p == "/__nope":
            body, code = b"missing", 404
        elif p.startswith("/time"):
            body, code = (b'{"the-time": "now"}', 200) if serves else (b"not found", 404)
        else:
            body, code = b"ok", 200
        self.send_response(code)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

class S(socketserver.TCPServer):
    allow_reuse_address = True

with S(("127.0.0.1", int(sys.argv[1])), H) as srv:
    srv.serve_forever()
"""


def _goal_routes(serves: bool, path: str):
    if path == "/__nope":
        return 404, b"missing"
    if path.startswith("/time"):
        return (200, b'{"the-time": "now"}') if serves else (404, b"not found")
    return 200, b"ok"


def _run_goal_acceptance(serves: bool, probes, negative_control=None, *, timeout=4):
    """Boot a tiny composed HTTP artifact and run the goal-acceptance gate against it. Real port when one is
    bindable; otherwise a real stand-in process + injected http_request. ASSERTS teardown after the run."""
    port = _free_local_port()
    tmp = tempfile.mkdtemp(prefix="goal-acc-")
    try:
        pid_file = Path(tmp) / "pid"
        if port is None:
            ready_file = Path(tmp) / "ready"
            stub = ("import os, pathlib, sys, time\n"
                    "pathlib.Path(sys.argv[1]).write_text(str(os.getpid()))\n"
                    "pathlib.Path(sys.argv[2]).write_text('ready')\n"
                    "while True:\n    time.sleep(60)\n")
            base_url = "http://goal.local"
            command = (f"python3 -u -c {shlex.quote(stub)} "
                       f"{shlex.quote(str(pid_file))} {shlex.quote(str(ready_file))}")

            def req(method, url, *, json_body=None, has_json_body=False, timeout=None):
                if not ready_file.exists():
                    raise conf.urllib.error.URLError("not ready")
                path = url[len(base_url):] or "/"
                code, body = _goal_routes(serves, path)
                return conf.HttpResponse(code, body)

            profile = {"start": {"command": command, "base_url": base_url, "ready_path": "/", "timeout": timeout},
                       "probes": probes}
            if negative_control is not None:
                profile["negative_control"] = negative_control
            result = conf.run_goal_acceptance(profile, tmp, http_request=req)
            assert _eventually_process_gone(pid_file), "goal-acceptance stand-in process must be torn down"
            return result
        base_url = f"http://127.0.0.1:{port}"
        command = (f"python3 -u -c {shlex.quote(_GOAL_SERVER)} {port} "
                   f"{shlex.quote(str(pid_file))} {'1' if serves else '0'}")
        profile = {"start": {"command": command, "base_url": base_url, "ready_path": "/", "timeout": timeout},
                   "probes": probes}
        if negative_control is not None:
            profile["negative_control"] = negative_control
        result = conf.run_goal_acceptance(profile, tmp)
        assert _eventually_closed(base_url), "real launched goal artifact port must be closed after the gate"
        assert _eventually_process_gone(pid_file), "real launched goal process group must be killed"
        return result
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_goal_acceptance_verifies_when_composed_artifact_serves():
    # (a) profile present + the composed artifact actually serves the probe -> verified True, evidence captured.
    result = _run_goal_acceptance(
        serves=True,
        probes=[{"request": {"method": "GET", "path": "/time"},
                 "expect": {"status": 200, "body_contains": "the-time"}}],
        negative_control={"request": {"path": "/__nope"}, "expect": {"status": 404}})
    assert result["verified"] is True and result["findings"] == [], result
    assert result["probes_run"] == 2, result          # the probe + the negative control both ran
    ev = [e for e in result["evidence"] if e["label"] == "probe[0]"]
    assert ev and ev[0]["status"] == 200 and "the-time" in ev[0]["body"], result
    print("ok  goal acceptance: serving artifact -> verified True, durable evidence, control sees its red")


def test_goal_acceptance_red_when_artifact_does_not_serve():
    # (b) the artifact boots+is ready but does NOT serve the probe (mislabeled/stubbed) -> verified False with a
    # status finding. (e) the process is STILL torn down — asserted inside the helper.
    result = _run_goal_acceptance(
        serves=False,
        probes=[{"request": {"method": "GET", "path": "/time"},
                 "expect": {"status": 200, "body_contains": "the-time"}}])
    assert result["verified"] is False, result
    assert any(f["check"] == "status" for f in result["findings"]), result
    print("ok  goal acceptance: non-serving artifact -> verified False (+ guaranteed teardown) — hole closing")


def test_goal_acceptance_negative_control_smoke_is_caught():
    # a declared negative control that does NOT produce its expected red means the probe set is a green-only
    # smoke -> verified False even though the positive probe passed (the D2 precondition for the goal gate).
    result = _run_goal_acceptance(
        serves=True,
        probes=[{"request": {"path": "/time"}, "expect": {"status": 200}}],
        negative_control={"request": {"path": "/"}, "expect": {"status": 404}})   # "/" returns 200, not 404
    assert result["verified"] is False, result
    assert any(f["check"] == "negative_control" for f in result["findings"]), result
    print("ok  goal acceptance: green-only smoke (control shows no red) -> verified False")


def test_goal_acceptance_profile_validation_and_no_boot_on_invalid():
    # the executable profile is shape-checked at the gate (the intake contract); an invalid one is rejected
    # WITHOUT a boot — verified False with a profile finding and zero probes run.
    assert conf.validate_acceptance_profile(
        {"start": {"command": "x"}, "probes": [{"request": {"path": "/"}, "expect": {"status": 200}}]}) == []
    assert "start.command" in conf.validate_acceptance_profile({"probes": [{"expect": {"status": 200}}]})
    assert "probes" in conf.validate_acceptance_profile({"start": {"command": "x"}, "probes": []})
    assert any(m.startswith("probes[0].expect") for m in conf.validate_acceptance_profile(
        {"start": {"command": "x"}, "probes": [{"request": {"path": "/"}, "expect": {}}]}))
    bad = conf.run_goal_acceptance({"probes": []}, "/tmp")
    assert bad["verified"] is False and bad["probes_run"] == 0, bad
    assert any(f["check"] == "profile" for f in bad["findings"]), bad
    print("ok  goal acceptance: profile shape-checked; invalid contract -> no boot, verified False")


# ── incr #3: the producer FINDINGS-ROUTER (explicit routing model + the SAFE flip of the ambiguous default) ──

def test_ambiguous_nonzero_exit_is_undetermined_not_code():
    # the safe flip: a plain nonzero exit with NO could-not-run signal and NO established code signal is
    # GENUINELY AMBIGUOUS -> `undetermined` (NOT `code`). Safe only because #1 fail-closes it as unverified.
    assert conf._failure_classification(returncode=1, stdout="some output", stderr="boom") == "undetermined"
    # the routing model then keeps it OFF the implementer repair loop (could-not-run/inconclusive).
    f = conf._with_failure_classification({"passed": False, "returncode": 1, "detail": "boom"}, False)
    assert f["failure_classification"] == "undetermined", f
    assert f["gate_state"] == "INDETERMINATE" and f["repair_route"] != "implementer", f
    assert f["repair_route"] == "clean_retry" and f["retryable"] is True, f
    assert not cp._finding_blocks_convergence(f), f               # ambiguous never blocks-as-code
    # an ORACLE/ANALYZER call site may opt a plain nonzero INTO `code` (its contract makes nonzero a product
    # signal) — the flip moves the DEFAULT, it does not remove the deliberate-code path.
    assert conf._failure_classification(returncode=1, stdout="", stderr="", default="code") == "code"
    print("ok  incr#3: an ambiguous nonzero exit -> undetermined (unverified), not blamed as code")


def test_positive_signal_exit_codes_are_resource_or_infra_not_code():
    # the RESOURCE HOLE: a shell reports a signal death as POSITIVE 128+N. 137 (SIGKILL/OOM), 134 (SIGABRT),
    # 139 (SIGSEGV) -> resource; 143 (SIGTERM), 130 (SIGINT) -> infra. None is a product `code` defect.
    assert conf._failure_classification(returncode=137) == "resource"   # 128+9 OOM-kill
    assert conf._failure_classification(returncode=134) == "resource"   # 128+6 SIGABRT
    assert conf._failure_classification(returncode=139) == "resource"   # 128+11 SIGSEGV
    assert conf._failure_classification(returncode=143) == "infra"      # 128+15 SIGTERM
    assert conf._failure_classification(returncode=130) == "infra"      # 128+2 SIGINT
    # even when the call site asks for default="code" (an oracle gate), a signal death still wins as could-not-run.
    assert conf._failure_classification(returncode=137, default="code") == "resource"
    f = conf._with_failure_classification({"passed": False, "returncode": 137}, False)
    assert f["gate_state"] == "COULD_NOT_RUN_INFRA" and f["repair_route"] == "clean_retry", f
    assert not cp._finding_blocks_convergence(f), f
    print("ok  incr#3: positive 137/134/139 -> resource, 143/130 -> infra (signal death, never code)")


def test_module_not_found_in_ran_output_is_still_code():
    # an established CODE signal must SURVIVE the flip: a ModuleNotFoundError in the OUTPUT of a command that
    # actually RAN (exit != 127) is the product's own missing import -> `code` -> implementer.
    out = "Traceback (most recent call last):\nModuleNotFoundError: No module named 'requests'\n"
    assert conf._failure_classification(returncode=1, stderr=out) == "code"
    # but the SAME text at exit 127 is command-not-found -> infra (the host gap wins; checked before code).
    assert conf._failure_classification(returncode=127, stderr="No module named pytest") == "infra"
    f = conf._with_failure_classification({"passed": False, "returncode": 1, "detail": out}, False)
    assert f["failure_classification"] == "code" and f["repair_route"] == "implementer", f
    assert f["gate_state"] == "VERIFIED_PRODUCT_FAILURE" and f["phase"] == "dependency", f
    assert cp._finding_blocks_convergence(f), f
    print("ok  incr#3: ModuleNotFoundError in ran output (exit 1) -> still code/implementer (blocks)")


def test_missing_pytest_runner_at_exit_1_is_unsupported_env_not_code():
    # the MIRROR of a fabricated pass: a `python -m pytest` invocation whose RUNNER is missing emits
    # "No module named pytest" in OUTPUT at exit 1 (NOT 127). The known-runner-absence whitelist must win over
    # the generic `No module named` CODE clause -> infra/unsupported-env, NOT a product defect blamed on the impl.
    out = "No module named pytest\n"
    assert conf._failure_classification(returncode=1, stderr=out) == "infra"
    # even an ORACLE/regression call site (default="code") must not flip a missing runner into code.
    assert conf._failure_classification(returncode=1, stderr=out, default="code") == "infra"
    f = conf._with_failure_classification(
        {"passed": False, "returncode": 1, "detail": "regression suite could not run; output tail: " + out}, False)
    assert f["failure_classification"] == "infra", f
    assert f["gate_state"] == "COULD_NOT_RUN_UNSUPPORTED_ENV", f
    assert f["repair_route"] in ("escalate", "clean_retry") and f["repair_route"] != "implementer", f
    assert not cp._finding_blocks_convergence(f), f               # a missing runner must NOT block as code
    print("ok  fix: missing pytest runner at exit 1 -> unsupported-env/infra (not code, not implementer)")


def test_other_known_python_runners_absent_at_exit_1_are_unsupported_env_not_code():
    # EXTENSION of the known-runner whitelist (the SAFE under-coverage fix): OTHER canonical `python -m <runner>`
    # modules that are absent (tox/nox/coverage/nose2/unittest) emit `No module named <runner>` in OUTPUT at exit 1
    # (NOT 127, just like pytest). They are an absent VERIFICATION RUNNER, not a product defect -> infra/
    # unsupported-env -> escalate, exactly as a missing pytest runner. Each name is anchored, so only the bare
    # canonical module matches (the plugin-extension case is guarded below).
    for runner in ("tox", "nox", "coverage", "nose2", "unittest"):
        out = f"No module named {runner}\n"
        assert conf._failure_classification(returncode=1, stderr=out) == "infra", out
        # even an ORACLE/regression call site (default="code") must not flip a missing runner into code.
        assert conf._failure_classification(returncode=1, stderr=out, default="code") == "infra", out
        f = conf._with_failure_classification(
            {"passed": False, "returncode": 1,
             "detail": "regression suite could not run; output tail: " + out}, False)
        assert f["failure_classification"] == "infra", f
        assert f["gate_state"] == "COULD_NOT_RUN_UNSUPPORTED_ENV", f
        assert f["repair_route"] in ("escalate", "clean_retry") and f["repair_route"] != "implementer", f
        assert not cp._finding_blocks_convergence(f), f            # an absent runner must NOT block as code
    print("ok  fix: tox/nox/coverage/nose2/unittest absent at exit 1 -> unsupported-env/infra (not implementer)")


def test_product_plugin_extending_known_runner_stays_code_not_infra():
    # the OVER-REACH guard for the whitelist EXTENSION: a PRODUCT plugin whose name extends a runner module
    # (`tox_someplugin`, `nox_fixtures`, `coverage_enable_subprocess`, `unittest2`) is the product's own undeclared
    # dependency, NOT an absent runner. The trailing `\b` keeps the runner adjacent to a word char (`_`/digit) so no
    # boundary forms and it falls through to `_CODE_TEXT_RE` -> code -> implementer (blocks). If it leaked to infra,
    # a real dep defect would be parked at "unverified" forever (the dangerous code->infra masking direction).
    for plugin in ("tox_someplugin", "nox_fixtures", "coverage_enable_subprocess", "nose2_html", "unittest2"):
        out = f"Traceback (most recent call last):\nModuleNotFoundError: No module named '{plugin}'\n"
        assert conf._failure_classification(returncode=1, stderr=out) == "code", out
        assert conf._failure_classification(returncode=1, stderr=out, default="code") == "code", out
        assert not conf._UNSUPPORTED_ENV_RE.search(out), out       # the anchored whitelist must NOT match a plugin
        f = conf._with_failure_classification({"passed": False, "returncode": 1, "detail": out}, False)
        assert f["failure_classification"] == "code" and f["repair_route"] == "implementer", f
        assert f["gate_state"] == "VERIFIED_PRODUCT_FAILURE", f
        assert cp._finding_blocks_convergence(f), f                # a missing product plugin still blocks as code
    print("ok  fix: `No module named tox_someplugin` (& peers) at exit 1 -> code/implementer (plugin gap, not runner)")


def test_product_missing_dependency_at_exit_1_stays_code_not_infra():
    # the NARROW guard (ADR-0006): `No module named requests` is NOT a known runner -> it falls through the
    # whitelist to `_CODE_TEXT_RE` -> code. A product that forgot to declare its dependency is a code defect.
    out = "Traceback (most recent call last):\nModuleNotFoundError: No module named 'requests'\n"
    assert conf._failure_classification(returncode=1, stderr=out) == "code"
    assert conf._failure_classification(returncode=1, stderr=out, default="code") == "code"
    f = conf._with_failure_classification({"passed": False, "returncode": 1, "detail": out}, False)
    assert f["failure_classification"] == "code" and f["repair_route"] == "implementer", f
    assert f["gate_state"] == "VERIFIED_PRODUCT_FAILURE", f
    assert cp._finding_blocks_convergence(f), f                   # product missing dep still blocks
    print("ok  fix: product `No module named requests` at exit 1 -> still code/implementer (blocks)")


def test_pytest_plugin_gap_at_exit_1_stays_code_not_infra():
    # the MASKING seam (over-reach of the unsupported-env whitelist): `No module named pytest_asyncio` is the
    # PRODUCT's own missing pytest-PLUGIN dependency raised BY its test code, NOT an absent runner. The anchored
    # `no module named pytest\b` must NOT match `pytest_asyncio` (the `_` keeps `pytest` inside a word), so it
    # falls through to `_CODE_TEXT_RE` -> code -> implementer. If it leaked to infra, a real dep defect would be
    # parked at "unverified" and never repaired (the dangerous code->infra direction).
    out = "Traceback (most recent call last):\nModuleNotFoundError: No module named 'pytest_asyncio'\n"
    assert conf._failure_classification(returncode=1, stderr=out) == "code"
    assert conf._failure_classification(returncode=1, stderr=out, default="code") == "code"
    # the bare runner-absence phrasing (no plugin suffix) also stays code-free of the anchor's word char:
    assert conf._failure_classification(returncode=1, stderr="No module named pytest_asyncio\n") == "code"
    f = conf._with_failure_classification({"passed": False, "returncode": 1, "detail": out}, False)
    assert f["failure_classification"] == "code" and f["repair_route"] == "implementer", f
    assert f["gate_state"] == "VERIFIED_PRODUCT_FAILURE", f
    assert cp._finding_blocks_convergence(f), f                   # a missing product plugin still blocks as code
    print("ok  fix: `No module named pytest_asyncio` at exit 1 -> code/implementer (plugin gap, not runner)")


def test_generic_is_not_available_in_product_output_stays_code_not_infra():
    # the SECOND masking seam: the generic `is not available` clause was dropped. A real product failure can
    # legitimately print "X is not available" — matching it as unsupported-env would re-label a genuine defect as
    # an infra gap and never repair it. With an established product signal in the same output it must stay code.
    out = "AssertionError: feature flag 'fast_path' is not available in this build\n"
    assert conf._failure_classification(returncode=1, stderr=out) == "code"
    assert conf._failure_classification(returncode=1, stderr=out, default="code") == "code"
    f = conf._with_failure_classification({"passed": False, "returncode": 1, "detail": out}, False)
    assert f["failure_classification"] == "code" and f["repair_route"] == "implementer", f
    assert f["gate_state"] == "VERIFIED_PRODUCT_FAILURE", f
    assert cp._finding_blocks_convergence(f), f
    # and even a bare ambiguous nonzero with a generic "is not available" no longer routes to unsupported-env:
    bare = conf._with_failure_classification(
        {"passed": False, "returncode": 1, "detail": "the requested codec is not available"}, False)
    assert bare["gate_state"] != "COULD_NOT_RUN_UNSUPPORTED_ENV", bare
    assert bare["repair_route"] != "escalate", bare
    print("ok  fix: a product `X is not available` -> code/undetermined, never unsupported-env (no masking)")


def test_product_emittable_failures_stay_code_never_unsupported_env():
    # STRUCTURAL REGRESSION GUARD (Mona walkthrough): `_UNSUPPORTED_ENV_RE` precedence sits ABOVE `_CODE_TEXT_RE`
    # in `_failure_classification`, so a FUTURE too-broad clause added to the whitelist would silently re-label a
    # real PRODUCT defect as infra/unsupported-env -> escalate/unverified (a MASKED defect, the dangerous
    # code->infra direction). The narrowness guarantee currently rests on reviewer discipline; this converts it
    # to an EXECUTABLE guard. Every string below is the kind a real PRODUCT or its TEST suite would print. The
    # invariant the guard locks in: NONE of them may ever be classified infra/unsupported-env (the masking
    # direction), and the whitelist regex must not match any of them. If anyone later broadens `_UNSUPPORTED_ENV_RE`
    # enough to catch one of these, this test fails — making whitelist drift a visible test failure, not a silent
    # masked defect.
    product_emittable = [
        # a missing PRODUCT dependency (real lib) raised by product/test code, not an absent runner:
        "Traceback (most recent call last):\nModuleNotFoundError: No module named 'requests'\n",
        # a missing pytest PLUGIN the product's own tests import (the `_` keeps `pytest` inside a word):
        "Traceback (most recent call last):\nModuleNotFoundError: No module named 'pytest_asyncio'\n",
        # a made-up application module the product forgot to ship/declare:
        "Traceback (most recent call last):\nModuleNotFoundError: No module named 'acme_payments.core'\n",
        # a plain assertion failure from the product's own test suite:
        "AssertionError: expected 200 but got 503\n",
        # a build/install failure a product emits when its wheel cannot be built:
        "ERROR: Could not build wheels for cryptography, which is required to install pyproject.toml-based projects\n",
        # generic `X is not available` phrasings a PRODUCT (not the checker) can legitimately print:
        "AssertionError: feature flag 'fast_path' is not available in this build\n",
        "RuntimeError: payment service is not available\n",
    ]
    for out in product_emittable:
        # MASKING-DIRECTION GUARD (the core invariant): never infra/unsupported-env/timeout/resource, at a generic
        # call site (default undetermined) AND at an ORACLE/BUILD/regression site (default code) alike. This is the
        # assertion that breaks if the whitelist is broadened to swallow a product string.
        for default in ("undetermined", "code"):
            cls = conf._failure_classification(returncode=1, stderr=out, default=default)
            assert cls not in ("infra", "timeout", "resource"), (out, default, cls)
        # and the whitelist regex itself must NOT match any product-emittable string:
        assert not conf._UNSUPPORTED_ENV_RE.search(out), out
        # at an ORACLE/BUILD/regression gate (default="code"), where a nonzero is a positive product signal and
        # masking is most dangerous, every product-emittable failure resolves to exactly `code` -> implementer.
        assert conf._failure_classification(returncode=1, stderr=out, default="code") == "code", out
    # the subset that carries an ESTABLISHED product signal (import/assertion) is `code` even at a generic site,
    # and end-to-end routes to the implementer and BLOCKS convergence (never parked as unverified).
    signal_bearing = [s for s in product_emittable if ("No module named" in s or "AssertionError" in s)]
    for out in signal_bearing:
        assert conf._failure_classification(returncode=1, stderr=out) == "code", out
        f = conf._with_failure_classification({"passed": False, "returncode": 1, "detail": out}, False)
        assert f["failure_classification"] == "code" and f["repair_route"] == "implementer", f
        assert f["gate_state"] == "VERIFIED_PRODUCT_FAILURE", f
        assert cp._finding_blocks_convergence(f), f
    print("ok  guard: product-emittable failures never infra/unsupported-env; whitelist drift would fail this test")


def test_example_comparison_mismatch_is_code_and_blocks():
    # item #5: the stdout/exit oracle mismatch is the POSITIVE product signal -> code/implementer, and it BLOCKS.
    profile = {"entrypoint": {"invocation": "mytool"},
               "examples": [{"invocation": "run", "expected_status": 0,
                             "expected_stdout_contains": ["DONE"]}]}
    runner = fake_runner({"mytool run": R(3, "nope\n", "")})       # wrong exit AND wrong stdout, plain exit 3
    rep = conf.run_cli_conformance(_cli_contract(profile), runner)
    assert not rep["passed"], rep
    by_check = {f["check"]: f for f in rep["findings"]}
    for check in ("exit_status", "stdout_contains"):
        f = by_check[check]
        assert f["failure_classification"] == "code", f
        assert f["gate_state"] == "VERIFIED_PRODUCT_FAILURE" and f["repair_route"] == "implementer", f
        assert cp._finding_blocks_convergence(f), f
    assert cp._apply_conformance_gate([], rep, "block") == rep["findings"], "oracle mismatch must block"
    print("ok  incr#3: an example-comparison mismatch -> code/implementer (blocks convergence)")


def test_finding_blocks_convergence_routes_on_repair_route_with_legacy_code_fallback():
    # the gate decision moves onto repair_route, KEEPING the legacy failure_classification=="code" fallback.
    assert cp._finding_blocks_convergence({"passed": False, "repair_route": "implementer"})
    assert not cp._finding_blocks_convergence({"passed": False, "repair_route": "clean_retry"})
    assert not cp._finding_blocks_convergence({"passed": False, "repair_route": "escalate"})
    assert not cp._finding_blocks_convergence({"passed": False, "repair_route": "none"})
    # legacy findings (predating the routing model, NO repair_route) still block on the code class.
    assert cp._finding_blocks_convergence({"passed": False, "failure_classification": "code"})
    assert not cp._finding_blocks_convergence({"passed": False, "failure_classification": "infra"})
    assert not cp._finding_blocks_convergence({"passed": False, "failure_classification": "undetermined"})
    # a passed finding never blocks, regardless of route.
    assert not cp._finding_blocks_convergence({"passed": True, "repair_route": "implementer"})
    print("ok  incr#3: _finding_blocks_convergence routes on repair_route; legacy ==code path still works")


def test_unsupported_env_infra_routes_to_escalate_not_retry():
    # a could-not-run that a clean retry will NOT fix (the host lacks the tool) routes to ESCALATE, not the
    # implementer and not an indefinite retry — kept as `infra` for back-compat, distinguished only in routing.
    f = conf._with_failure_classification(
        {"passed": False, "failure_classification": "infra",
         "detail": "static check `pyflakes .`: command not found"}, False)
    assert f["failure_classification"] == "infra", f               # back-compat label unchanged
    assert f["gate_state"] == "COULD_NOT_RUN_UNSUPPORTED_ENV" and f["repair_route"] == "escalate", f
    assert f["retryable"] is False and not cp._finding_blocks_convergence(f), f
    print("ok  incr#3: an unsupported-env infra -> escalate (not implementer, not retried)")


# ── runner_factory: the box-backed conformance runner (host-injectable execution via AI_ORG_RUNNER_CMD) ──────
# These tests drive a FAKE shim script (a tiny stand-in for a box CLI wrapper) and assert conformance runs
# THROUGH it, that the transport boundary is honest (a shim that can't run -> infra/could-not-run, never a
# fabricated pass and never product blame), and that the unset path is the plain subprocess runner unchanged.

# A shim that EXECUTES the framed command (mirroring a box) and frames the result back over stdout.
_EXEC_SHIM = r'''#!/usr/bin/env python3
import sys, subprocess
MAGIC = b"AOBRUN1"
def read_block(buf):
    len_line, rest = buf.split(b"\n", 1)
    n = int(len_line)
    return rest[:n], rest[n:]
cwd = sys.argv[1] if len(sys.argv) > 1 else ""
buf = sys.stdin.buffer.read()
magic, rest = buf.split(b"\n", 1)
assert magic == MAGIC, magic
cmd_b, rest = read_block(rest)
stdin_b, rest = read_block(rest)
proc = subprocess.run(cmd_b.decode(), shell=True, cwd=(cwd or None), input=stdin_b,
                      stdout=subprocess.PIPE, stderr=subprocess.PIPE)
out, err = proc.stdout or b"", proc.stderr or b""
sys.stdout.buffer.write(b"".join([MAGIC, b"\n", str(proc.returncode).encode(), b"\n",
                                  str(len(out)).encode(), b"\n", out,
                                  str(len(err)).encode(), b"\n", err]))
'''

# A shim that FAILS at the transport layer: it never produces a frame and exits nonzero (box unavailable).
_FAILING_SHIM = "#!/usr/bin/env python3\nimport sys\nsys.stderr.write('box unavailable\\n')\nsys.exit(3)\n"

# A shim that exits 0 but emits a MALFORMED (non-AOBRUN1) body — a corrupt/garbage frame.
_MALFORMED_SHIM = "#!/usr/bin/env python3\nimport sys\nsys.stdout.write('not a frame at all')\n"


def _install_shim(d, body):
    p = Path(d) / "shim.py"
    p.write_text(body)
    return f"{sys.executable} {shlex.quote(str(p))}"


from contextlib import contextmanager  # noqa: E402 — local to the runner_factory tests below


@contextmanager
def _runner_env(**updates):
    old = {k: os.environ.get(k) for k in updates}
    try:
        for k, v in updates.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_runner_factory_routes_through_shim_passing_rc_stdout_stderr():
    with tempfile.TemporaryDirectory() as d:
        cmd = _install_shim(d, _EXEC_SHIM)
        with _runner_env(AI_ORG_RUNNER_CMD=cmd, AI_ORG_RUNNER_FALLBACK_SUBPROCESS=None):
            runner = runner_factory.get_conformance_runner()
            res = runner("python3 -c \"import sys; sys.stdout.write('OUT-data'); "
                         "sys.stderr.write('ERR-data'); sys.exit(7)\"", cwd=d)
    assert res.returncode == 7, res                            # the COMMAND's exit, surfaced through the frame
    assert res.stdout == "OUT-data" and res.stderr == "ERR-data", res
    print("ok  runner_factory: conformance runs THROUGH the shim; gates see its rc/stdout/stderr")


def test_runner_factory_framing_survives_newlines_and_sentinel_in_output():
    # length-prefixed framing must carry output that contains newlines AND the sentinel string itself.
    with tempfile.TemporaryDirectory() as d:
        cmd = _install_shim(d, _EXEC_SHIM)
        payload = "line1\nAOBRUN1\nline3\n"
        with _runner_env(AI_ORG_RUNNER_CMD=cmd, AI_ORG_RUNNER_FALLBACK_SUBPROCESS=None):
            runner = runner_factory.get_conformance_runner()
            res = runner("printf %s " + shlex.quote(payload), cwd=d)
    assert res.returncode == 0 and res.stdout == payload, res
    print("ok  runner_factory: length-prefixed framing carries newlines + the sentinel verbatim")


def test_runner_factory_passes_cwd_and_stdin_through_the_shim():
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "marker.txt").write_text("INSIDE-CWD")
        cmd = _install_shim(d, _EXEC_SHIM)
        with _runner_env(AI_ORG_RUNNER_CMD=cmd, AI_ORG_RUNNER_FALLBACK_SUBPROCESS=None):
            runner = runner_factory.get_conformance_runner()
            cwd_res = runner("cat marker.txt", cwd=d)           # relative path -> proves cwd was applied
            stdin_res = runner("cat", cwd=d, stdin="piped-payload")
    assert cwd_res.stdout == "INSIDE-CWD", cwd_res
    assert stdin_res.stdout == "piped-payload", stdin_res
    print("ok  runner_factory: cwd + command stdin are delivered to the shim")


def test_runner_factory_unset_uses_subprocess_runner_unchanged():
    sentinel = object()
    saved = conf.subprocess_runner
    try:
        conf.subprocess_runner = lambda *a, **k: sentinel
        with _runner_env(AI_ORG_RUNNER_CMD=None):
            got = runner_factory.get_conformance_runner()
        with _runner_env(AI_ORG_RUNNER_CMD="   "):              # blank/whitespace also means "no shim"
            got_blank = runner_factory.get_conformance_runner()
    finally:
        conf.subprocess_runner = saved
    assert got is sentinel and got_blank is sentinel, (got, got_blank)
    print("ok  runner_factory: AI_ORG_RUNNER_CMD unset -> conformance.subprocess_runner unchanged")


def test_runner_factory_shim_transport_failure_is_infra_not_product_not_pass():
    with tempfile.TemporaryDirectory() as d:
        cmd = _install_shim(d, _FAILING_SHIM)
        with _runner_env(AI_ORG_RUNNER_CMD=cmd, AI_ORG_RUNNER_FALLBACK_SUBPROCESS=None):
            runner = runner_factory.get_conformance_runner()
            res = runner("echo should-not-matter", cwd=d)
    assert res.returncode == 127 and res.returncode != 0, res   # could-not-run, NOT a fabricated pass (0)
    cls = conf._failure_classification(returncode=res.returncode, stdout=res.stdout, stderr=res.stderr)
    assert cls == "infra", (cls, res)                           # routes to escalate/clean-retry/unverified
    assert cls != "code", res                                  # NEVER blamed on product code
    print("ok  runner_factory: a shim that can't run (nonzero transport exit) -> infra/could-not-run")


def test_runner_factory_unrunnable_shim_is_infra():
    with _runner_env(AI_ORG_RUNNER_CMD="/no/such/runner-shim-xyz", AI_ORG_RUNNER_FALLBACK_SUBPROCESS=None):
        runner = runner_factory.get_conformance_runner()
        res = runner("echo hi")
    cls = conf._failure_classification(returncode=res.returncode, stdout=res.stdout, stderr=res.stderr)
    assert res.returncode == 127 and cls == "infra", (cls, res)
    print("ok  runner_factory: an unrunnable/missing shim -> infra (the isolation did not happen, honestly)")


def test_runner_factory_malformed_frame_is_infra():
    with tempfile.TemporaryDirectory() as d:
        cmd = _install_shim(d, _MALFORMED_SHIM)
        with _runner_env(AI_ORG_RUNNER_CMD=cmd, AI_ORG_RUNNER_FALLBACK_SUBPROCESS=None):
            runner = runner_factory.get_conformance_runner()
            res = runner("echo hi", cwd=d)
    cls = conf._failure_classification(returncode=res.returncode, stdout=res.stdout, stderr=res.stderr)
    assert res.returncode == 127 and cls == "infra", (cls, res)
    print("ok  runner_factory: a malformed/absent result frame -> infra (not a silent pass)")


def test_runner_factory_fallback_opt_in_runs_locally_on_transport_failure():
    with tempfile.TemporaryDirectory() as d:
        cmd = _install_shim(d, _FAILING_SHIM)                   # shim can't run -> would be infra...
        with _runner_env(AI_ORG_RUNNER_CMD=cmd, AI_ORG_RUNNER_FALLBACK_SUBPROCESS="1"):
            runner = runner_factory.get_conformance_runner()   # ...but the explicit opt-in runs it locally
            res = runner("python3 -c \"print('local-ran')\"", cwd=d)
    assert res.returncode == 0 and res.stdout.strip() == "local-ran", res
    print("ok  runner_factory: AI_ORG_RUNNER_FALLBACK_SUBPROCESS opt-in runs locally when the box is absent")


def test_runner_factory_shim_drives_real_conformance_gate_end_to_end():
    # End-to-end: a real CLI contract + artifact, with conformance executing every check THROUGH the shim.
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "mytool").write_text("#!/bin/sh\necho ok\n")
        os.chmod(Path(d) / "mytool", 0o755)
        profile = {"entrypoint": {"invocation": "./mytool"}, "examples": [
            {"invocation": "", "expected_status": 0, "expected_stdout_contains": ["ok"]}]}
        cmd = _install_shim(d, _EXEC_SHIM)
        with _runner_env(AI_ORG_RUNNER_CMD=cmd, AI_ORG_RUNNER_FALLBACK_SUBPROCESS=None):
            runner = runner_factory.get_conformance_runner()
            rep = conf.run_cli_conformance(_cli_contract(profile), runner, cwd=d)
    assert rep["applicable"] and rep["passed"], rep
    print("ok  runner_factory: conformance passes a real artifact when executed end-to-end through the shim")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
    print(f"\n{len(fns)} passed")
