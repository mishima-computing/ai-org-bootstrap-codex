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
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "codex-org-bootstrap" / "src"))
import conformance as conf
import controller_pipeline as cp

REPO = Path(__file__).resolve().parents[1]
SCHEMA = REPO / "schemas" / "implementation-contract.schema.json"


def fake_runner(table):
    """A runner that returns a canned RunResult per command. Unknown commands surface as a loud failure so a
    test never silently passes against a command it did not stub."""
    def _run(cmd, *, cwd=None, stdin=None):
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
    print("ok  build failure -> single critical finding, examples skipped (no derived noise)")


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
                                           http_request=_json_rpc_request({"add": {"result": 3}}))
    assert rep["applicable"] and rep["passed"] and rep["findings"] == [], rep
    assert runner.stopped, "the service must be stopped after the calls"
    print("ok  rpc(json_rpc_http): a real call whose result matches -> passes; service stopped")


def test_rpc_json_rpc_unexpected_error_is_major_not_gated_on_http_status():
    profile = {"start": {"command": "serve"}, "base_url": "http://x", "transport": "json_rpc_http",
               "calls": [{"method": "add", "params": {}}]}
    rep = conf.run_rpc_service_conformance(_rpc_contract(profile), _SvcRunner(),
        http_request=_json_rpc_request({"add": {"error": {"code": -32000, "message": "boom"}}}))
    assert not rep["passed"] and any(f["check"] == "error" for f in rep["findings"]), rep
    print("ok  rpc(json_rpc_http): an error in the body (over HTTP 200) -> major, not a pass")


def test_rpc_json_rpc_expected_error_code_matches():
    profile = {"start": {"command": "serve"}, "base_url": "http://x", "transport": "json_rpc_http",
               "calls": [{"method": "bad", "expected_error_code": -32601}]}
    rep = conf.run_rpc_service_conformance(_rpc_contract(profile), _SvcRunner(),
        http_request=_json_rpc_request({"bad": {"error": {"code": -32601, "message": "no method"}}}))
    assert rep["passed"], rep
    print("ok  rpc(json_rpc_http): a declared expected_error_code that matches -> passes")


def test_rpc_unsupported_transport_is_critical_without_boot():
    profile = {"start": {"command": "serve"}, "base_url": "x", "transport": "thrift", "calls": [{"method": "m"}]}
    runner = _SvcRunner()
    rep = conf.run_rpc_service_conformance(_rpc_contract(profile), runner)
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

    rep = conf.run_rpc_service_conformance(_rpc_contract(profile), _SvcRunner(), grpc_invoker=invoker)
    assert rep["applicable"] and rep["passed"] and rep["findings"] == [], rep
    print("ok  rpc(grpc): a real dynamic invocation (injected) whose result matches -> passes")


def test_rpc_grpc_unavailable_machinery_is_a_finding_not_silent():
    # no injected invoker and (no descriptor_set_path / no grpcio) -> ONE transport_unavailable finding: the
    # heavy machinery is loaded lazily and its absence is surfaced, never a silent pass nor an always-on dep.
    profile = {"start": {"command": "serve"}, "base_url": "h:50051", "transport": "grpc",
               "calls": [{"method": "pkg.Svc/Get"}]}
    rep = conf.run_rpc_service_conformance(_rpc_contract(profile), _SvcRunner())
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
        "calls": [{"method": "ping"}]}}}
    assert v.is_valid(ok), list(v.iter_errors(ok))
    incomplete = {**base, "deliverable_kind": "rpc_service", "conformance": {"rpc_service": {
        "start": {"command": "serve"}, "base_url": "http://x", "transport": "json_rpc_http"}}}  # no calls
    assert not v.is_valid(incomplete), "rpc profile requires calls"
    assert not v.is_valid({**base, "deliverable_kind": "rpc_service", "conformance": {"rpc_service": {
        "start": {"command": "s"}, "base_url": "x", "transport": "smoke-signals", "calls": [{"method": "m"}]}}}), \
        "transport is constrained to the supported set"
    print("ok  schema: rpc profile requires start/base_url/transport/calls; transport is an enum")


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
    cp._stream_append = lambda repo, ev: events.append(ev)        # capture stream, touch no disk
    try:
        report = cp._shadow_conformance("/tmp/leaf", results, "run-1", runner=runner)
    finally:
        cp._stream_append = orig

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
    assert cp.CONFORMANCE_SHADOW == "shadow", "default mode is shadow (observe, never block)"
    print("ok  gate dormant for a non-CLI contract (no event, no exec, default mode=shadow)")


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


def test_closed_loop_block_gate_keeps_findings_when_linon_clean():
    # P0 (closed loop): linon is CLEAN, but a block-mode conformance gate still FAILS on the current artifact.
    # _closed_loop_findings re-runs the gates and must keep findings non-empty (so the repair loop stays open
    # and convergence requires the gate clean) — instead of being overwritten by linon-only.
    results = {"linon": {"findings": []}}                       # linon says clean
    failing = {"applicable": True, "passed": False,
               "findings": [{"check": "exit_status", "severity": "major", "passed": False}]}
    saved = (cp._shadow_conformance, cp._secret_scan, cp._fuzz_cli, cp.CONFORMANCE_SHADOW)
    cp._shadow_conformance = lambda repo, results, run_id: failing
    cp._secret_scan = lambda repo, run_id: None
    cp._fuzz_cli = lambda repo, results, run_id: None
    try:
        cp.CONFORMANCE_SHADOW = "block"
        findings, gate_ctx = cp._closed_loop_findings("/tmp/x", results, "r", None)
        assert findings, "a failing BLOCK gate must keep findings non-empty even when linon is clean"
        assert gate_ctx["conformance"], "the gate findings are carried to feed the repair agents"
        cp.CONFORMANCE_SHADOW = "shadow"                        # same failing gate, but shadow folds nothing
        sh_findings, _ = cp._closed_loop_findings("/tmp/x", results, "r", None)
        assert sh_findings == [], "in shadow the failing gate is telemetry only (no fold)"
    finally:
        cp._shadow_conformance, cp._secret_scan, cp._fuzz_cli, cp.CONFORMANCE_SHADOW = saved
    print("ok  closed loop: a block gate that fails keeps the loop open (linon-clean no longer converges)")


def test_gate_error_fails_closed_in_block_only():
    # P0 fail-closed: a gate that ERRORED is NOT clean (no silent fail-open). The error report folds in block
    # (blocks) and is telemetry-only in shadow.
    err = cp._gate_error_report("conformance", "boom")
    assert err["passed"] is False and err["error"] and err["findings"][0]["severity"] == "critical", err
    assert cp._apply_conformance_gate([], err, "block"), "a gate ERROR must block in block mode"
    assert cp._apply_conformance_gate([], err, "shadow") == [], "a gate ERROR is telemetry-only in shadow"
    print("ok  gate ERROR fails closed in block, telemetry-only in shadow (no silent fail-open)")


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
    rep = conf.run_http_service_conformance(_http_contract(_HTTP_PROFILE), boot, http_request=_http_req(200, b"ok"))
    assert rep["applicable"] and rep["passed"] and rep["findings"] == [], rep
    assert boot.started and boot.stopped, "service is started and stopped"
    print("ok  http_service: boot -> ready -> examples pass -> stopped")


def test_http_service_status_mismatch_is_major():
    rep = conf.run_http_service_conformance(_http_contract(_HTTP_PROFILE), FakeBoot(), http_request=_http_req(500, b"ok"))
    checks = {f["check"] for f in rep["findings"]}
    assert "status" in checks and not rep["passed"], rep["findings"]
    print("ok  http_service: wrong status -> major status finding")


def test_http_service_readiness_timeout_skips_examples():
    boot = FakeBoot()
    rep = conf.run_http_service_conformance(_http_contract(_HTTP_PROFILE), boot, http_request=_http_req(ready=False))
    assert not rep["passed"] and any(f["check"] == "lifecycle" for f in rep["findings"]), rep
    assert boot.stopped, "the service is still stopped after a readiness timeout"
    print("ok  http_service: readiness timeout -> critical lifecycle, examples skipped, still stopped")


def test_http_service_not_applicable_for_non_http():
    rep = conf.run_conformance(_cli_contract({"entrypoint": {"invocation": "t"}, "examples": []}),
                               fake_runner({"t": R(0, "", "")}))
    assert rep.get("applicable") is True   # cli path still works through the dispatcher
    print("ok  dispatcher still routes cli (http_service port did not break it)")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
    print(f"\n{len(fns)} passed")
