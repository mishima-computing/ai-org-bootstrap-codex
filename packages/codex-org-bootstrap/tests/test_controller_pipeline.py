"""Generic controller pipeline: registry-declared DAG order and fan-in."""
from __future__ import annotations

import json
import hashlib
import os
import re
import shutil
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "packages" / "codex-org-bootstrap" / "src"))

import controller_pipeline as pipeline  # noqa: E402
import controller_run  # noqa: E402
import carrier_harness  # noqa: E402

ISO8601_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")


class _Rep:
    def __init__(self, ok=True, *, run_id="run", role="role"):
        self.ok = ok
        self.run_id = run_id
        self.role = role

    def to_dict(self):
        report = {
            "ok": self.ok,
            "attempts": [{
                "attempt": 0,
                "exit": 0,
                "timed_out": False,
                "stdin_hang": False,
                "log": str(ROOT / ".agent-runs" / "controller" / self.run_id / "carrier-attempt0.log"),
            }],
        }
        if self.role in {
            "functional-ci-action-writer",
            "nonfunctional-ci-action-writer",
            "security-ci-action-writer",
            "implementer",
        }:
            report["diff_artifact"] = {
                "path": str(ROOT / ".agent-runs" / "controller" / self.run_id / "diff.patch"),
                "sha256": f"diff-sha-{self.role}",
                "bytes": 17,
                "untracked_count": 0,
            }
        return report


class ControllerPipelineTests(unittest.TestCase):
    def setUp(self):
        self._orig = controller_run.run
        self._env = mock.patch.dict(os.environ, {"STEFAN_ENABLED": ""})
        self._env.start()
        self.calls = []

        def _fake_run(repo, contract, run_id, *, cache=True, **_):
            role = contract["role"]
            payload = json.loads(contract["prompt"])
            self.calls.append((role, payload, run_id, cache, contract))
            if "files_allowed_to_change" not in contract:
                result = {"role_id": role, "seen_inputs": payload["inputs"]}
                if role == "aufheben-designer":
                    result = {
                        "role_id": "aufheben-designer",
                        "contract_id": "impl-1",
                        "objective": payload["objective"],
                        "files_allowed_to_change": ["scripts/controller_pipeline.py"],
                        "files_not_allowed_to_change": ["registry/runtime-registry.yaml"],
                        "required_checks": [
                            "python -m unittest packages/codex-org-bootstrap/tests/test_controller_pipeline.py"
                        ],
                        "received_from": sorted(payload["inputs"]),
                        "acceptance_criteria": ["pipeline reaches implementer and verifiers"],
                        "deliverable_kind": "none",
                    }
                (Path(repo) / pipeline.RESULT_FILE).write_text(json.dumps(result), encoding="utf-8")
            return _Rep(True, run_id=run_id, role=role)

        controller_run.run = _fake_run

    def tearDown(self):
        self._env.stop()
        controller_run.run = self._orig
        result_file = ROOT / pipeline.RESULT_FILE
        if result_file.exists():
            result_file.unlink()
        shutil.rmtree(ROOT / ".agent-runs" / "controller" / "pipe-test", ignore_errors=True)

    def test_registry_dag_order_fan_in_and_default_verifiers(self):
        result = pipeline.run_pipeline(ROOT, "wire declared org", "pipe-test", cache=False)
        order = [call[0] for call in self.calls]
        self.assertEqual(order, [
            "aggressive-designer",
            "conservative-designer",
            "functional-ci-action-writer",
            "genius",
            "nonfunctional-ci-action-writer",
            "security-ci-action-writer",
            "aufheben-designer",
            "implementer",
            "linon",
        ])
        self.assertNotIn("stefan", order)
        self.assertTrue(all(result["summary"].values()))
        self.assertTrue(all(call[3] is False for call in self.calls))

        by_role = {role: payload for role, payload, _run_id, _cache, _contract in self.calls}
        self.assertEqual(by_role["aufheben-designer"]["inputs"]["aggressive-designer"]["role_id"],
                         "aggressive-designer")
        self.assertEqual(
            by_role["aufheben-designer"]["inputs"]["security-ci-action-writer"]["diff_artifact"]["sha256"],
            "diff-sha-security-ci-action-writer",
        )
        self.assertEqual(by_role["implementer"]["inputs"]["aufheben-designer"]["contract_id"], "impl-1")
        self.assertEqual(by_role["linon"]["inputs"]["implementer"]["diff_artifact"]["sha256"],
                         "diff-sha-implementer")
        self.assertNotIn("stefan", by_role)

        implementer_contract = [call[4] for call in self.calls if call[0] == "implementer"][0]
        self.assertEqual(implementer_contract["files_allowed_to_change"],
                         ["scripts/controller_pipeline.py"])
        self.assertEqual(implementer_contract["forbidden_paths"],
                         ["registry/runtime-registry.yaml"])

        manifest_path = ROOT / ".agent-runs" / "controller" / "pipe-test" / "provenance-manifest.json"
        self.assertEqual(Path(result["manifest_path"]), manifest_path)
        self.assertTrue(manifest_path.is_file())
        self.assertEqual(json.loads(manifest_path.read_text(encoding="utf-8")), result["manifest"])
        self.assertRegex(result["manifest"]["started_at"], ISO8601_UTC)
        self.assertRegex(result["manifest"]["finished_at"], ISO8601_UTC)

        self.assertEqual([stage["role"] for stage in result["manifest"]["stages"]], order)
        self.assertNotIn("stefan", [stage["role"] for stage in result["manifest"]["stages"]])
        self.assertNotIn("stefan", result["required_ok"])
        self.assertNotIn("stefan", result["reports"])
        self.assertNotIn("stefan", result["results"])
        self.assertTrue(result["converged"])
        for stage in result["manifest"]["stages"]:
            stage_run_id = f"pipe-test-{stage['role']}"
            self.assertEqual(stage["run_id"], stage_run_id)
            self.assertEqual(stage["journal_path"],
                             str(ROOT / ".agent-runs" / "controller" / stage_run_id / "journal.jsonl"))
            self.assertEqual(stage["conversation_log_path"],
                             str(ROOT / ".agent-runs" / "controller" / stage_run_id / "carrier-attempt0.log"))
            self.assertTrue(stage["report_ok"])
            self.assertIn("duration_seconds", stage["timing"])
            self.assertRegex(stage["timing"]["started_at"], ISO8601_UTC)
            self.assertRegex(stage["timing"]["finished_at"], ISO8601_UTC)
            self.assertNotIn("started_at_epoch", stage["timing"])
            self.assertNotIn("finished_at_epoch", stage["timing"])

        write_roles = {
            "functional-ci-action-writer",
            "nonfunctional-ci-action-writer",
            "security-ci-action-writer",
            "implementer",
        }
        for stage in result["manifest"]["stages"]:
            artifact = stage["artifact"]
            if stage["role"] in write_roles:
                self.assertIsNone(artifact["result"])
                self.assertIsNone(artifact["result_path"])
                self.assertIsNone(artifact["result_sha256"])
                self.assertEqual(artifact["diff_artifact"]["sha256"], f"diff-sha-{stage['role']}")
            else:
                preserved = ROOT / ".agent-runs" / "controller" / stage["run_id"] / pipeline.RESULT_FILE
                self.assertEqual(artifact["result"]["role_id"], stage["role"])
                self.assertEqual(artifact["result_path"], str(preserved))
                self.assertTrue(preserved.is_file())
                self.assertEqual(
                    artifact["result_sha256"],
                    hashlib.sha256(preserved.read_bytes()).hexdigest(),
                )
                self.assertIsNone(artifact["diff_artifact"])

        implementer_stage = [stage for stage in result["manifest"]["stages"] if stage["role"] == "implementer"][0]
        self.assertEqual(implementer_stage["artifact"]["diff_artifact"]["sha256"], "diff-sha-implementer")

    def test_stefan_enabled_runs_opt_in_verifier(self):
        with mock.patch.dict(os.environ, {"STEFAN_ENABLED": "1"}):
            result = pipeline.run_pipeline(ROOT, "wire declared org", "pipe-test", cache=False)

        order = [call[0] for call in self.calls]
        self.assertEqual(order[-2:], ["linon", "stefan"])
        self.assertIn("stefan", result["required_ok"])
        self.assertTrue(result["required_ok"]["stefan"])
        self.assertIn("stefan", result["reports"])
        self.assertIn("stefan", result["results"])
        self.assertIn("pipe-test-stefan", [stage["run_id"] for stage in result["manifest"]["stages"]])

        by_role = {role: payload for role, payload, _run_id, _cache, _contract in self.calls}
        self.assertEqual(by_role["stefan"]["inputs"]["implementer"]["diff_artifact"]["sha256"],
                         "diff-sha-implementer")

    def test_single_producer_failure_still_reaches_aufheben_implementer_and_reviewers(self):
        def _producer_failure_run(repo, contract, run_id, *, cache=True, **_):
            role = contract["role"]
            payload = json.loads(contract["prompt"])
            self.calls.append((role, payload, run_id, cache, contract))
            if "files_allowed_to_change" not in contract:
                result = {"role_id": role, "seen_inputs": payload["inputs"]}
                if role == "aufheben-designer":
                    result = {
                        "role_id": "aufheben-designer",
                        "contract_id": "impl-1",
                        "objective": payload["objective"],
                        "files_allowed_to_change": ["scripts/controller_pipeline.py"],
                        "files_not_allowed_to_change": [],
                        "required_checks": [],
                        "received_from": sorted(payload["inputs"]),
                        "acceptance_criteria": ["pipeline reaches implementer and verifiers"],
                        "deliverable_kind": "none",
                    }
                (Path(repo) / pipeline.RESULT_FILE).write_text(json.dumps(result), encoding="utf-8")
            return _Rep(role != "aggressive-designer", run_id=run_id, role=role)

        controller_run.run = _producer_failure_run

        result = pipeline.run_pipeline(ROOT, "wire declared org", "pipe-test", cache=False)
        order = [call[0] for call in self.calls]

        self.assertEqual(order[-2:], ["implementer", "linon"])
        self.assertFalse(result["required_ok"]["aggressive-designer"])
        self.assertNotIn("stefan", result["required_ok"])
        self.assertNotIn("stefan", result["results"])
        self.assertNotIn("aggressive-designer", result["fatal_ok"])
        self.assertTrue(all(result["fatal_ok"].values()))
        self.assertTrue(result["converged"])
        self.assertNotIn("aggressive-designer", result["results"])

        by_role = {role: payload for role, payload, _run_id, _cache, _contract in self.calls}
        aufheben_inputs = by_role["aufheben-designer"]["inputs"]
        self.assertNotIn("aggressive-designer", aufheben_inputs)
        self.assertIn("conservative-designer", aufheben_inputs)
        self.assertIn("genius", aufheben_inputs)

        aggressive_stage = [
            stage for stage in result["manifest"]["stages"]
            if stage["role"] == "aggressive-designer"
        ][0]
        self.assertFalse(aggressive_stage["stage_ok"])

    def test_linon_findings_trigger_repair_loop_until_converged(self):
        linon_runs = 0

        def _repair_run(repo, contract, run_id, *, cache=True, **_):
            nonlocal linon_runs
            role = contract["role"]
            payload = json.loads(contract["prompt"])
            self.calls.append((role, payload, run_id, cache, contract))
            if "files_allowed_to_change" not in contract:
                result = {"role_id": role, "seen_inputs": payload["inputs"]}
                if role == "aufheben-designer":
                    result = {
                        "role_id": "aufheben-designer",
                        "contract_id": f"impl-{len([call for call in self.calls if call[0] == role])}",
                        "objective": payload["objective"],
                        "files_allowed_to_change": ["scripts/controller_pipeline.py"],
                        "required_checks": [
                            "python -m unittest packages/codex-org-bootstrap/tests/test_controller_pipeline.py"
                        ],
                        "received_from": sorted(payload["inputs"]),
                        "acceptance_criteria": ["pipeline reaches implementer and verifiers"],
                        "deliverable_kind": "none",
                    }
                elif role == "linon":
                    linon_runs += 1
                    result = {
                        "profile_id": "linon-review",
                        "findings": [{
                            "file": "scripts/controller_pipeline.py",
                            "line_range": {"start": 1, "end": 1},
                            "severity": "major",
                            "lens": "silent-failure",
                            "basis": "static-read",
                            "claim": "first pass finding",
                            "evidence_ref": "scripts/controller_pipeline.py:1",
                        }] if linon_runs == 1 else [],
                        "criterion_verdicts": [],
                        "gaps": [],
                    }
                (Path(repo) / pipeline.RESULT_FILE).write_text(json.dumps(result), encoding="utf-8")
            return _Rep(True, run_id=run_id, role=role)

        controller_run.run = _repair_run

        result = pipeline.run_pipeline(ROOT, "wire declared org", "pipe-test", cache=False)

        self.assertTrue(result["converged"])
        self.assertEqual(result["repair_iterations"], 1)
        self.assertEqual([call[0] for call in self.calls], [
            "aggressive-designer",
            "conservative-designer",
            "functional-ci-action-writer",
            "genius",
            "nonfunctional-ci-action-writer",
            "security-ci-action-writer",
            "aufheben-designer",
            "implementer",
            "linon",
            "aggressive-designer",
            "conservative-designer",
            "genius",
            "aufheben-designer",
            "implementer",
            "linon",
        ])

        repair_designer_payload = self.calls[9][1]
        self.assertEqual(repair_designer_payload["inputs"]["linon"]["findings"][0]["claim"],
                         "first pass finding")
        self.assertEqual(repair_designer_payload["objective"], "wire declared org")

        self.assertEqual(len(result["manifest"]["iterations"]), 2)
        self.assertEqual(result["manifest"]["iterations"][0]["kind"], "initial")
        self.assertEqual(result["manifest"]["iterations"][1]["kind"], "repair")
        self.assertRegex(result["manifest"]["iterations"][0]["started_at"], ISO8601_UTC)
        self.assertRegex(result["manifest"]["iterations"][1]["finished_at"], ISO8601_UTC)
        self.assertEqual(
            [stage["role"] for stage in result["manifest"]["iterations"][1]["stages"]],
            ["aggressive-designer", "conservative-designer", "genius",
             "aufheben-designer", "implementer", "linon"],
        )
        self.assertEqual(result["manifest"]["iterations"][0]["linon_findings_count"], 1)
        self.assertEqual(result["manifest"]["iterations"][1]["linon_findings_count"], 0)

    def test_linon_findings_stop_at_repair_iteration_cap(self):
        linon_runs = 0

        def _capped_run(repo, contract, run_id, *, cache=True, **_):
            nonlocal linon_runs
            role = contract["role"]
            payload = json.loads(contract["prompt"])
            self.calls.append((role, payload, run_id, cache, contract))
            if "files_allowed_to_change" not in contract:
                result = {"role_id": role, "seen_inputs": payload["inputs"]}
                if role == "aufheben-designer":
                    result = {
                        "role_id": "aufheben-designer",
                        "contract_id": f"impl-{len([call for call in self.calls if call[0] == role])}",
                        "objective": payload["objective"],
                        "files_allowed_to_change": ["scripts/controller_pipeline.py"],
                        "required_checks": [
                            "python -m unittest packages/codex-org-bootstrap/tests/test_controller_pipeline.py"
                        ],
                        "received_from": sorted(payload["inputs"]),
                        "acceptance_criteria": ["pipeline reaches implementer and verifiers"],
                        "deliverable_kind": "none",
                    }
                elif role == "linon":
                    linon_runs += 1
                    result = {
                        "profile_id": "linon-review",
                        "findings": [{
                            "file": "scripts/controller_pipeline.py",
                            "line_range": {"start": 1, "end": 1},
                            # an UNWEIGHTED severity keeps the cap at the base (max_repair_iterations=2), so
                            # this test exercises the BASE repair cap; the severity-weighted scaling (ADR-0008
                            # addendum) is covered by test_severity_weighted_repair_cap.
                            "severity": "moderate",
                            "lens": "silent-failure",
                            "basis": "static-read",
                            "claim": f"finding {linon_runs}",
                            "evidence_ref": "scripts/controller_pipeline.py:1",
                        }],
                        "criterion_verdicts": [],
                        "gaps": [],
                    }
                (Path(repo) / pipeline.RESULT_FILE).write_text(json.dumps(result), encoding="utf-8")
            return _Rep(True, run_id=run_id, role=role)

        controller_run.run = _capped_run

        result = pipeline.run_pipeline(
            ROOT, "wire declared org", "pipe-test", cache=False, max_repair_iterations=2
        )

        self.assertFalse(result["converged"])
        self.assertEqual(result["repair_iterations"], 2)
        self.assertEqual(result["max_repair_iterations"], 2)
        self.assertEqual(result["linon_findings_count"], 1)
        self.assertEqual(linon_runs, 3)
        self.assertEqual([iteration["iteration"] for iteration in result["manifest"]["iterations"]], [0, 1, 2])
        self.assertEqual([iteration["kind"] for iteration in result["manifest"]["iterations"]],
                         ["initial", "repair", "repair"])
        self.assertEqual([iteration["linon_findings_count"] for iteration in result["manifest"]["iterations"]],
                         [1, 1, 1])
        for iteration in result["manifest"]["iterations"][1:]:
            self.assertEqual(
                [stage["role"] for stage in iteration["stages"]],
                ["aggressive-designer", "conservative-designer", "genius",
                 "aufheben-designer", "implementer", "linon"],
            )

    # ---- gate-behind: skip the expensive Linon reviewer when a cheap deterministic gate already blocks ----
    @staticmethod
    def _blocking_conf_report():
        """A `block`-mode conformance failure — folds into the convergence findings, so it BLOCKS this
        iteration (its source is deterministic-impl, so repair routes to the implementer)."""
        return {"applicable": True, "passed": False, "checks_run": 1,
                "findings": [{"source": "cli-conformance", "check": "probe", "passed": False,
                              "severity": "major", "detail": "end-to-end probe failed"}]}

    @staticmethod
    def _stream_events(stream_path):
        return [json.loads(line) for line in Path(stream_path).read_text(encoding="utf-8").splitlines()
                if line.strip()]

    def _linon_carrier_calls(self):
        return [call for call in self.calls if call[0] == "linon"]

    @staticmethod
    def _gate_report(finding):
        return {"applicable": True, "passed": False, "checks_run": 1, "findings": [finding]}

    def _run_with_conformance_reports(self, reports, *, gate_mode="block", max_repair_iterations=2):
        seq = list(reports)

        def _conf(repo, results, run_id, runner=None):
            return seq.pop(0) if seq else None

        with mock.patch.object(pipeline, "CONFORMANCE_GATE_MODE", gate_mode), \
             mock.patch.object(pipeline, "_shadow_conformance", _conf), \
             mock.patch.object(pipeline, "_secret_scan", return_value=None), \
             mock.patch.object(pipeline, "_fuzz_cli", return_value=None):
            return pipeline.run_pipeline(
                ROOT, "wire declared org", "pipe-test", cache=False,
                max_repair_iterations=max_repair_iterations,
            )

    def _repair_implementer_inputs(self):
        matches = [payload["inputs"] for role, payload, run_id, _cache, _contract in self.calls
                   if role == "implementer" and "-repair" in run_id]
        self.assertTrue(matches, "expected a repair implementer invocation")
        return matches[-1]

    # ---- R4: repair-iteration re-localization around the defect locus ----
    def test_finding_defect_locus_extracts_file_line_and_symbol(self):
        # picks the WORST blocking finding that names a reviewable file; carries its line range + symbol.
        findings = [
            {"file": "scripts/a.py", "severity": "minor", "claim": "nit"},
            {"file": "scripts/b.py", "line_range": {"start": 12, "end": 20},
             "symbol": "do_thing", "severity": "critical", "claim": "real bug"},
        ]
        self.assertEqual(
            pipeline._finding_defect_locus(findings),
            {"file": "scripts/b.py", "line_range": [12, 20], "symbols": ["do_thing"]},
        )

    def test_finding_defect_locus_skips_nonreviewable_and_fileless(self):
        # scratch/artifact targets and findings with no file are not a usable locus.
        self.assertIsNone(pipeline._finding_defect_locus(
            [{"file": ".agent-runs/x/journal.jsonl", "severity": "critical"},
             {"severity": "critical", "claim": "no file named"}]))

    def test_finding_line_range_handles_dict_list_and_bare_line(self):
        self.assertEqual(pipeline._finding_line_range({"line_range": {"start": 3, "end": 9}}), [3, 9])
        self.assertEqual(pipeline._finding_line_range({"range": [4, 4]}), [4, 4])
        self.assertEqual(pipeline._finding_line_range({"line": 7}), [7, 7])
        self.assertIsNone(pipeline._finding_line_range({"severity": "major"}))

    def test_repair_threads_defect_locus_into_implementer_only(self):
        # On a repair the blocking linon finding's locus reaches the implementer's controller_run.run call
        # (and ONLY the implementer's); the first attempt and the producer/aufheben repairs carry no locus.
        captured = []   # (role, run_id, defect_locus)
        linon_runs = 0

        def _repair_run(repo, contract, run_id, *, cache=True, defect_locus=None, **_):
            nonlocal linon_runs
            role = contract["role"]
            payload = json.loads(contract["prompt"])
            self.calls.append((role, payload, run_id, cache, contract))
            captured.append((role, run_id, defect_locus))
            if "files_allowed_to_change" not in contract:
                result = {"role_id": role, "seen_inputs": payload["inputs"]}
                if role == "aufheben-designer":
                    result = {
                        "role_id": "aufheben-designer",
                        "contract_id": f"impl-{len([c for c in self.calls if c[0] == role])}",
                        "objective": payload["objective"],
                        "files_allowed_to_change": ["scripts/controller_pipeline.py"],
                        "required_checks": ["python -m unittest x"],
                        "received_from": sorted(payload["inputs"]),
                        "acceptance_criteria": ["reach implementer"],
                        "deliverable_kind": "none",
                    }
                elif role == "linon":
                    linon_runs += 1
                    result = {
                        "profile_id": "linon-review",
                        "findings": [{
                            "file": "scripts/controller_pipeline.py",
                            "line_range": {"start": 5, "end": 8},
                            "severity": "major", "lens": "silent-failure", "basis": "static-read",
                            "claim": "first pass finding",
                            "evidence_ref": "scripts/controller_pipeline.py:5",
                        }] if linon_runs == 1 else [],
                        "criterion_verdicts": [], "gaps": [],
                    }
                (Path(repo) / pipeline.RESULT_FILE).write_text(json.dumps(result), encoding="utf-8")
            return _Rep(True, run_id=run_id, role=role)

        controller_run.run = _repair_run
        result = pipeline.run_pipeline(ROOT, "wire declared org", "pipe-test", cache=False)
        self.assertTrue(result["converged"])

        impl_loci = {run_id: locus for role, run_id, locus in captured if role == "implementer"}
        first = next(locus for run_id, locus in impl_loci.items() if "-repair" not in run_id)
        repair = next(locus for run_id, locus in impl_loci.items() if "-repair" in run_id)
        self.assertIsNone(first)                                    # first attempt unchanged
        self.assertEqual(repair, {"file": "scripts/controller_pipeline.py", "line_range": [5, 8]})
        # the locus is implementer-only: no other role's repair call carried it.
        self.assertTrue(all(locus is None for role, _run, locus in captured if role != "implementer"))

    def test_adr0018_structural_finding_forwards_only_inert_evidence(self):
        finding = {
            "source": "http-conformance", "check": "lifecycle", "severity": "critical", "passed": False,
            "detail": "service start command exited before readiness",
            "returncode": 7, "stdout_tail": "own stdout", "stderr_tail": "own stderr",
            "expected": "hidden-golden",
        }
        self._run_with_conformance_reports([self._gate_report(finding), None])

        evidence = self._repair_implementer_inputs()["gate_findings"]
        forwarded = evidence["findings"][0]
        self.assertEqual(evidence["kind"], "inert_deterministic_gate_evidence")
        self.assertEqual(forwarded["check"], "lifecycle")
        self.assertEqual(forwarded["severity"], "critical")
        self.assertEqual(forwarded["returncode"], 7)
        self.assertEqual(forwarded["stdout_tail"], "own stdout")
        self.assertEqual(forwarded["stderr_tail"], "own stderr")
        self.assertNotIn("expected", forwarded)
        self.assertNotIn("hidden-golden", json.dumps(evidence, sort_keys=True))

    def test_adr0018_cli_http_rpc_oracle_findings_redact_expected_and_detail(self):
        for source in ("cli-conformance", "http-conformance", "rpc-conformance"):
            with self.subTest(source=source):
                self.calls.clear()
                golden = f"withheld-{source}"
                finding = {
                    "source": source, "check": "body_contains", "severity": "major", "passed": False,
                    "detail": f"example 0 missed golden {golden}",
                    "example": 0, "expected": golden, "actual": "observed-output",
                }
                self._run_with_conformance_reports([self._gate_report(finding), None])

                evidence = self._repair_implementer_inputs()["gate_findings"]
                forwarded = evidence["findings"][0]
                self.assertEqual(forwarded["actual"], "observed-output")
                self.assertTrue(forwarded["_oracle_withheld"])
                self.assertNotIn("expected", forwarded)
                self.assertNotIn(golden, json.dumps(self._repair_implementer_inputs(), sort_keys=True))

    def test_adr0018_rpc_call_oracle_withheld_but_batch_and_json_specs_remain(self):
        contract = {
            "role_id": "aufheben-designer",
            "deliverable_kind": "rpc_service",
            "conformance": {
                "rpc_service": {
                    "start": {"command": "serve"}, "base_url": "http://x", "transport": "json_rpc_http",
                    "calls": [
                        {"method": "add", "params": {"a": 1}, "expected_result_contains": ["sum:2"]},
                        {"method": "bad", "params": {}, "expected_error_code": -32601},
                    ],
                },
                "batch_job": {"run": {"command": "job"}, "expected_status": 2,
                              "produced_artifacts": ["out.json"]},
                "json": {"files": [{"path": "out.json", "required_paths": ["ok"]}]},
            },
        }

        redacted = pipeline._withhold_acceptance_bundle("implementer", {pipeline.AUFHEBEN_ROLE: contract})
        conf = redacted[pipeline.AUFHEBEN_ROLE]["conformance"]
        calls = conf["rpc_service"]["calls"]
        self.assertEqual(conf["rpc_service"]["_calls_oracle_withheld"], 2)
        self.assertEqual(calls[0], {"method": "add", "params": {"a": 1}})
        self.assertEqual(calls[1], {"method": "bad", "params": {}})
        self.assertEqual(conf["batch_job"], contract["conformance"]["batch_job"])
        self.assertEqual(conf["json"], contract["conformance"]["json"])
        self.assertIn("expected_result_contains", contract["conformance"]["rpc_service"]["calls"][0])

    def test_adr0018_http_and_rpc_deterministic_sources_route_to_implementer_only(self):
        roles = ["aggressive-designer", "conservative-designer", "genius",
                 pipeline.AUFHEBEN_ROLE, "implementer"]
        for source in ("http-conformance", "rpc-conformance"):
            with self.subTest(source=source):
                self.assertEqual(pipeline._repair_roles_for([{"source": source}], roles), ["implementer"])

    def test_adr0018_shadow_conformance_findings_are_never_forwarded(self):
        finding = {"source": "http-conformance", "check": "body_contains", "severity": "major",
                   "passed": False, "detail": "example 0 missed golden shadow-secret",
                   "example": 0, "expected": "shadow-secret", "actual": "body"}
        result = self._run_with_conformance_reports([self._gate_report(finding)], gate_mode="shadow")

        implementer_calls = [call for call in self.calls if call[0] == "implementer"]
        self.assertEqual(len(implementer_calls), 1)
        self.assertNotIn("gate_findings", implementer_calls[0][1]["inputs"])
        self.assertTrue(result["converged"])

    def test_adr0018_targeted_gate_repair_still_converges_after_clean_iteration(self):
        finding = {"source": "cli-conformance", "check": "stdout_contains", "severity": "major",
                   "passed": False, "detail": "example 0 missing expected output",
                   "example": 0, "expected": "golden-output", "actual": "wrong-output"}
        result = self._run_with_conformance_reports([self._gate_report(finding), None])

        self.assertTrue(result["converged"])
        self.assertEqual(result["repair_iterations"], 1)
        self.assertEqual([call[0] for call in self.calls if call[0] in {"implementer", "linon"}],
                         ["implementer", "implementer", "linon"])

    def test_blocking_cheap_gate_skips_linon_entirely_first_pass(self):
        # ACCEPTANCE 1: a blocking cheap-gate finding -> NO *-linon stage, a linon_skipped record, no fabricated
        # stefan/linon pass in required_ok, and the loop continues (not converged).
        with tempfile.TemporaryDirectory() as td:
            stream = Path(td) / "stream.jsonl"
            with mock.patch.dict(os.environ, {"STREAM_LOG": str(stream)}), \
                 mock.patch.object(pipeline, "_shadow_conformance",
                                   lambda repo, results, run_id, runner=None: self._blocking_conf_report()):
                result = pipeline.run_pipeline(ROOT, "wire declared org", "pipe-test",
                                               cache=False, max_repair_iterations=1)
            events = self._stream_events(stream)

        stages = result["manifest"]["stages"]
        self.assertNotIn("linon", [stage["role"] for stage in stages])
        self.assertFalse(any(stage["run_id"].endswith("-linon") for stage in stages))
        self.assertEqual(self._linon_carrier_calls(), [])           # no carrier call -> no tokens, no wall-clock
        self.assertNotIn("linon", result["required_ok"])            # NOT fabricated (ADR-0016 D5)
        self.assertNotIn("stefan", result["required_ok"])
        skips = [e for e in events if e.get("type") == "linon_skipped"]
        self.assertTrue(skips)
        self.assertEqual(skips[0]["source"], "linon")
        self.assertEqual(skips[0]["iteration"], 0)
        self.assertIn("conformance", skips[0]["reason"])            # the cheap gate that blocked
        self.assertFalse(result["converged"])                       # findings non-empty -> loop continues
        self.assertGreaterEqual(result["linon_findings_count"], 1)

    def test_clean_cheap_gates_run_full_linon_unchanged(self):
        # ACCEPTANCE 2: a gate-CLEAN iteration runs the FULL linon stage, byte-for-byte the same invocation as
        # before gate-behind (same scope/inputs/run-id), and its verdict is recorded.
        result = pipeline.run_pipeline(ROOT, "wire declared org", "pipe-test", cache=False)

        linon_calls = self._linon_carrier_calls()
        self.assertEqual(len(linon_calls), 1)
        role, payload, run_id, cache, _contract = linon_calls[0]
        self.assertEqual(run_id, "pipe-test-linon")                 # same stage run-id
        self.assertFalse(cache)
        # linon saw the implementer's diff (the terminal-write verifier inputs) — unchanged scope
        self.assertEqual(payload["inputs"]["implementer"]["diff_artifact"]["sha256"], "diff-sha-implementer")
        self.assertTrue(result["required_ok"]["linon"])             # verdict recorded
        self.assertIn("pipe-test-linon", [stage["run_id"] for stage in result["manifest"]["stages"]])
        self.assertTrue(result["converged"])

    def test_converged_only_via_gate_clean_iteration_that_ran_linon(self):
        # ACCEPTANCE 3 (verdict-safety): the ONLY path to required_ok["linon"]==True / converged is a gate-clean
        # iteration that ACTUALLY ran linon. Here iteration 0 is clean and linon passes (stage_ok) but FINDS a
        # defect -> repair; the repair trips a cheap gate every iteration -> linon is SKIPPED -> the iter-0 linon
        # pass MUST NOT survive as a stale green. Proves no green is ever declared on a skipped-linon iteration.
        def _run(repo, contract, run_id, *, cache=True, **_):
            role = contract["role"]
            payload = json.loads(contract["prompt"])
            self.calls.append((role, payload, run_id, cache, contract))
            if "files_allowed_to_change" not in contract:
                result = {"role_id": role, "seen_inputs": payload["inputs"]}
                if role == "aufheben-designer":
                    result = {
                        "role_id": "aufheben-designer",
                        "contract_id": f"impl-{len([c for c in self.calls if c[0] == role])}",
                        "objective": payload["objective"],
                        "files_allowed_to_change": ["scripts/controller_pipeline.py"],
                        "required_checks": ["python -m unittest x"],
                        "received_from": sorted(payload["inputs"]),
                        "acceptance_criteria": ["reach verifiers"],
                        "deliverable_kind": "none",
                    }
                elif role == "linon":
                    result = {"profile_id": "linon-review", "criterion_verdicts": [], "gaps": [],
                              "findings": [{
                                  "file": "scripts/controller_pipeline.py",
                                  "line_range": {"start": 1, "end": 1}, "severity": "major",
                                  "lens": "silent-failure", "basis": "static-read",
                                  "claim": "linon finding", "evidence_ref": "scripts/controller_pipeline.py:1"}]}
                (Path(repo) / pipeline.RESULT_FILE).write_text(json.dumps(result), encoding="utf-8")
            return _Rep(True, run_id=run_id, role=role)

        controller_run.run = _run
        conf_n = {"n": 0}

        def _conf(repo, results, run_id, runner=None):           # clean on the FIRST pass, blocking thereafter
            conf_n["n"] += 1
            return None if conf_n["n"] == 1 else self._blocking_conf_report()

        with tempfile.TemporaryDirectory() as td:
            stream = Path(td) / "stream.jsonl"
            with mock.patch.dict(os.environ, {"STREAM_LOG": str(stream)}), \
                 mock.patch.object(pipeline, "_shadow_conformance", _conf):
                result = pipeline.run_pipeline(ROOT, "wire declared org", "pipe-test",
                                               cache=False, max_repair_iterations=3)
            events = self._stream_events(stream)

        self.assertEqual(len(self._linon_carrier_calls()), 1)       # linon ran ONLY on the clean first pass
        self.assertEqual(self._linon_carrier_calls()[0][2], "pipe-test-linon")
        self.assertNotIn("linon", result["required_ok"])            # the stale iter-0 pass was DROPPED on skip
        self.assertFalse(result["converged"])                       # never green on a skipped-linon iteration
        skips = [e for e in events if e.get("type") == "linon_skipped"]
        self.assertTrue(skips)                                      # repair iterations skipped linon
        self.assertTrue(all(e["iteration"] >= 1 for e in skips))

    def test_repair_loop_skips_linon_until_cheap_gates_pass(self):
        # ACCEPTANCE 4: a repaired diff that STILL fails a cheap gate runs NO linon; once the gates pass, linon
        # runs BEFORE convergence. conformance blocks the first pass + first repair, then goes clean.
        conf_n = {"n": 0}

        def _conf(repo, results, run_id, runner=None):
            conf_n["n"] += 1
            return self._blocking_conf_report() if conf_n["n"] <= 2 else None

        with tempfile.TemporaryDirectory() as td:
            stream = Path(td) / "stream.jsonl"
            with mock.patch.dict(os.environ, {"STREAM_LOG": str(stream)}), \
                 mock.patch.object(pipeline, "_shadow_conformance", _conf):
                result = pipeline.run_pipeline(ROOT, "wire declared org", "pipe-test",
                                               cache=False, max_repair_iterations=3)
            events = self._stream_events(stream)

        # linon ran exactly once — in the FIRST repair iteration where the cheap gates finally passed.
        linon_calls = self._linon_carrier_calls()
        self.assertEqual(len(linon_calls), 1)
        self.assertEqual(linon_calls[0][2], "pipe-test-repair2-linon")
        # no linon stage exists before that iteration
        linon_stage_ids = [s["run_id"] for s in result["manifest"]["stages"] if s["role"] == "linon"]
        self.assertEqual(linon_stage_ids, ["pipe-test-repair2-linon"])
        # two skips: the first pass (iter 0) and the first repair (iter 1)
        skips = sorted(e["iteration"] for e in events if e.get("type") == "linon_skipped")
        self.assertEqual(skips, [0, 1])
        self.assertTrue(result["required_ok"]["linon"])
        self.assertTrue(result["converged"])                        # clean gates + clean linon -> green

    def test_malformed_result_json_fails_stage_without_crashing(self):
        def _malformed_run(repo, contract, run_id, *, cache=True, **_):
            role = contract["role"]
            try:                                   # the reask path appends a non-JSON repair instruction
                payload = json.loads(contract["prompt"])
            except json.JSONDecodeError:
                payload = None
            self.calls.append((role, payload, run_id, cache, contract))
            if "files_allowed_to_change" not in contract:
                (Path(repo) / pipeline.RESULT_FILE).write_text("{not valid json", encoding="utf-8")
            return _Rep(True, run_id=run_id, role=role)

        controller_run.run = _malformed_run

        result = pipeline.run_pipeline(ROOT, "wire declared org", "pipe-test", cache=False)

        self.assertFalse(result["required_ok"]["aggressive-designer"])
        self.assertFalse(result["converged"])
        self.assertFalse(result["fatal_ok"]["aufheben-designer"])
        self.assertNotIn("aufheben-designer", [call[0] for call in self.calls])
        first_stage = result["manifest"]["stages"][0]
        self.assertFalse(first_stage["stage_ok"])
        self.assertIn("result.json: invalid JSON", first_stage["stage_errors"][0])
        self.assertIsNone(first_stage["artifact"]["result"])
        self.assertIsNone(first_stage["artifact"]["result_path"])
        self.assertIsNone(first_stage["artifact"]["result_sha256"])

    def test_run_id_must_be_safe_single_path_segment(self):
        for bad_run_id in ["", "/", ".", "..", "../escape", "nested/run", "nested\\run"]:
            with self.subTest(run_id=bad_run_id):
                with self.assertRaises(ValueError):
                    pipeline.run_pipeline(ROOT, "wire declared org", bad_run_id, cache=False)

    def test_manifest_records_freeze_events_from_attempts(self):
        def _freeze_run(repo, contract, run_id, *, cache=True, **_):
            result = {"role_id": contract["role"], "seen_inputs": json.loads(contract["prompt"])["inputs"]}
            (Path(repo) / pipeline.RESULT_FILE).write_text(json.dumps(result), encoding="utf-8")
            rep = _Rep(True, run_id=run_id, role=contract["role"])
            original_to_dict = rep.to_dict

            def _to_dict():
                d = original_to_dict()
                d["attempts"][0].update({
                    "frozen": True,
                    "killed": True,
                    "retryable": True,
                    "no_output_timeout": 120,
                    "timestamp": "2026-06-17T00:00:00Z",
                })
                d["attempts"].append({
                    "attempt": 1,
                    "exit": 0,
                    "timed_out": False,
                    "stdin_hang": False,
                    "frozen": False,
                    "killed": False,
                    "retryable": False,
                    "timestamp": "2026-06-17T00:00:01Z",
                    "log": str(ROOT / ".agent-runs" / "controller" / run_id / "carrier-attempt1.log"),
                })
                return d

            rep.to_dict = _to_dict
            return rep

        controller_run.run = _freeze_run

        result = pipeline.run_pipeline(ROOT, "wire declared org", "pipe-test", cache=False)
        first_stage = result["manifest"]["stages"][0]

        self.assertEqual(first_stage["events"], [{
            "type": "carrier_freeze_killed",
            "attempt": 0,
            "timestamp": "2026-06-17T00:00:00Z",
            "no_output_timeout": 120,
            "retryable": True,
        }])

    def test_no_output_watchdog_kills_and_retries(self):
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d)
            quiet = [sys.executable, "-c", "import time; time.sleep(5)"]
            with mock.patch.object(carrier_harness, "build_codex_argv", return_value=quiet), \
                    mock.patch.dict(os.environ, {"CODEX_CARRIER_NO_OUTPUT_TIMEOUT_SECONDS": "0.1"}):
                result = carrier_harness.run_carrier(repo, "prompt", "workspace-write", timeout=10, retries=1)

        self.assertFalse(result["ok"])
        self.assertEqual(len(result["attempts"]), 2)
        self.assertTrue(result["attempts"][0]["frozen"])
        self.assertTrue(result["attempts"][0]["killed"])
        self.assertTrue(result["attempts"][0]["retryable"])
        self.assertFalse(result["attempts"][0]["timed_out"])
        self.assertEqual(result["attempts"][0]["no_output_timeout"], 0.1)
        self.assertRegex(result["attempts"][0]["timestamp"], ISO8601_UTC)
        self.assertTrue(result["attempts"][1]["frozen"])
        self.assertFalse(result["attempts"][1]["retryable"])

    def test_no_output_watchdog_freeze_then_success(self):
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d)
            marker = repo / "first-attempt-froze"
            script = (
                "import pathlib, time; "
                f"p = pathlib.Path({str(marker)!r}); "
                "print('success') if p.exists() else (p.write_text('1'), time.sleep(5))"
            )
            with mock.patch.object(carrier_harness, "build_codex_argv",
                                   return_value=[sys.executable, "-c", script]), \
                    mock.patch.dict(os.environ, {"CODEX_CARRIER_NO_OUTPUT_TIMEOUT_SECONDS": "0.1"}):
                result = carrier_harness.run_carrier(repo, "prompt", "workspace-write", timeout=10, retries=1)

        self.assertTrue(result["ok"])
        self.assertEqual(len(result["attempts"]), 2)
        self.assertTrue(result["attempts"][0]["frozen"])
        self.assertTrue(result["attempts"][0]["killed"])
        self.assertTrue(result["attempts"][0]["retryable"])
        self.assertFalse(result["attempts"][1]["frozen"])
        self.assertFalse(result["attempts"][1]["killed"])
        self.assertEqual(result["attempts"][1]["exit"], 0)

    def test_linon_via_codex_review_flag(self):
        # behind LINON_VIA_CODEX_REVIEW, the review stage runs `codex review` and returns findings in the
        # SAME shape the repair loop consumes; flag OFF keeps the role-carrier path (default, unchanged).
        import codex_review
        import subprocess as sp
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d)
            sp.run(["git", "-C", str(repo), "init"], check=True, capture_output=True)
            fake = {"ok": True, "raw": "- [P2] bug — app.py:3-3\n  body",
                    "findings": [{"file": "app.py", "line_range": {"start": 3, "end": 3},
                                  "severity": "major", "claim": "off-by-one"}]}
            with mock.patch.dict(os.environ, {"LINON_VIA_CODEX_REVIEW": "1"}), \
                    mock.patch.object(codex_review, "review", return_value=fake):
                stage_ok, result, report_dict, stage = pipeline._execute_stage(
                    repo, "linon", None, "obj", {}, "run-linon", False)
            self.assertTrue(stage_ok)
            self.assertEqual(pipeline._linon_findings(result), fake["findings"])  # feeds the repair loop unchanged
            self.assertEqual(stage["reviewer"], "codex-review")
            self.assertTrue(report_dict["ok"])
        self.assertTrue(pipeline._linon_via_codex_review_enabled.__module__)        # helper exists
        with mock.patch.dict(os.environ, {"LINON_VIA_CODEX_REVIEW": "0"}):
            self.assertFalse(pipeline._linon_via_codex_review_enabled())            # OFF by 0 / default

    def test_linon_findings_drop_nondeliverable_targets(self):
        # The hard backstop: a reviewer finding about controller scratch / a generated file can never be
        # cleared by changing the deliverable, so it would drive `while findings` repair forever (the live
        # scaffold loop on .agent-runs/.../journal). _linon_findings drops such findings before they gate.
        real = {"file": "cockpit/server.py", "severity": "major", "claim": "off-by-one in the parser"}
        result = {"findings": [
            {"file": ".agent-runs/controller/goal-x-implementer/journal.jsonl", "claim": "ok:true but cmd exited 1"},
            {"file": "tests/__pycache__/m.cpython.pyc", "claim": "stale bytecode"},
            {"file": "package-lock.json", "claim": "lock churn"},
            {"path": "/abs/repo/.agent-runs/stream.jsonl", "claim": "scratch via absolute path"},
            real,
        ]}
        kept = pipeline._linon_findings(result)
        self.assertEqual(kept, [real], "only the deliverable-targeting finding survives")
        # path classifier directly
        self.assertFalse(pipeline._is_reviewable_finding_path(".agent-runs/controller/j.jsonl"))
        self.assertFalse(pipeline._is_reviewable_finding_path("node_modules/x/i.js"))
        self.assertFalse(pipeline._is_reviewable_finding_path("poetry.lock"))
        self.assertTrue(pipeline._is_reviewable_finding_path("src/app.py"))
        self.assertTrue(pipeline._is_reviewable_finding_path(""), "no concrete target -> kept (conservative)")
        # scratch-only findings collapse to empty -> a leaf with only such findings can converge
        self.assertEqual(pipeline._linon_findings({"findings": [
            {"file": ".agent-runs/x/journal.jsonl", "claim": "self-report-trust"}]}), [])

    def test_carrier_view_hides_scratch_and_noise(self):
        # A reviewer carrier free-reads the worktree, so machine noise must be OUT of its file range — else
        # it reviews the controller's own .agent-runs/ journals ("packet says ok but the journal shows a
        # command exited 1") instead of the deliverable, looping on a finding no code change can clear
        # (observed: a scaffold leaf failing linon r0..r3 on .agent-runs/.../journal). Hidden via
        # .git/info/exclude (not the tracked .gitignore), so it holds for any target repo without touching
        # its files; codex discovers files through gitignore-respecting search, so excluded == unseen.
        import subprocess as sp
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d)
            sp.run(["git", "-C", str(repo), "init"], check=True, capture_output=True)
            (repo / "code.py").write_text("print(1)\n")
            carrier_harness._ensure_carrier_view_clean(repo)
            # every noise class is now invisible to a gitignore-respecting search...
            for noise in (".agent-runs/controller/journal.jsonl", "__pycache__/m.pyc", "pkg.egg-info/PKG",
                          "node_modules/lib/index.js", ".venv/bin/python", "htmlcov/index.html",
                          ".DS_Store", "ui/.idea/workspace.xml"):
                ign = sp.run(["git", "-C", str(repo), "check-ignore", noise], capture_output=True)
                self.assertEqual(ign.returncode, 0, f"{noise} must be git-excluded (unseen)")
            # ...but real source stays visible
            vis = sp.run(["git", "-C", str(repo), "check-ignore", "code.py"], capture_output=True)
            self.assertNotEqual(vis.returncode, 0, "real code must stay visible to the reviewer")
            # build/dist/target are deliberately NOT hidden (can be real source dirs)
            self.assertNotEqual(sp.run(["git", "-C", str(repo), "check-ignore", "build/x"],
                                       capture_output=True).returncode, 0)
            carrier_harness._ensure_carrier_view_clean(repo)   # idempotent — no duplicate entries
            excl = sp.run(["git", "-C", str(repo), "rev-parse", "--git-path", "info/exclude"],
                          capture_output=True, text=True).stdout.strip()
            self.assertEqual((repo / excl).read_text().count(".agent-runs/"), 1)


class WriteRoleRetriesTests(unittest.TestCase):
    """Write roles carry an extra carrier retry so codex's transient non-zero 'submission failed'
    exit is absorbed; read-only producers keep the default (their non-zero exits are cheap to redo
    and the contract default already retries once)."""

    def test_write_role_contract_sets_retries(self):
        entries = pipeline._entries(ROOT)
        write_role = next(r for r, e in entries.items() if e.write_scope)
        read_role = next(r for r, e in entries.items() if not e.write_scope)

        wc = pipeline._contract(entries[write_role], "obj", {})
        self.assertEqual(wc.get("retries"), pipeline.WRITE_ROLE_RETRIES)
        self.assertGreater(pipeline.WRITE_ROLE_RETRIES, 1)

        rc = pipeline._contract(entries[read_role], "obj", {})
        self.assertNotIn("retries", rc)  # read-only producer → contract default (1)

        # the contract must still parse (retries is a known CarrierContract field)
        import controller_models
        self.assertEqual(controller_models.CarrierContract.from_dict(wc).retries,
                         pipeline.WRITE_ROLE_RETRIES)


class ParallelWaveTests(unittest.TestCase):
    """The three independent designers (aggressive/conservative/genius) must overlap when
    --max-parallel>1: each read-only producer runs in its own git worktree off the repo HEAD."""

    def tearDown(self):
        result_file = ROOT / pipeline.RESULT_FILE
        if result_file.exists():
            result_file.unlink()
        ctrl = ROOT / ".agent-runs" / "controller"
        if ctrl.is_dir():
            for d in ctrl.glob("par-test*"):
                shutil.rmtree(d, ignore_errors=True)

    def test_independent_producers_run_concurrently(self):
        import threading
        import time
        entries = pipeline._entries(ROOT)
        peak = {"n": 0, "max": 0}
        lock = threading.Lock()

        def _fake_run(repo, contract, run_id, *, cache=True, **_):
            role = contract["role"]
            if not entries[role].write_scope:                 # the read-only producers (parallelized)
                with lock:
                    peak["n"] += 1
                    peak["max"] = max(peak["max"], peak["n"])
                time.sleep(0.3)                               # hold so any overlap is observable
                with lock:
                    peak["n"] -= 1
            payload = json.loads(contract["prompt"])
            if "files_allowed_to_change" not in contract:
                result = {"role_id": role, "seen_inputs": payload["inputs"]}
                if role == "aufheben-designer":
                    result = {"role_id": "aufheben-designer", "contract_id": "impl-1",
                              "objective": payload["objective"],
                              "files_allowed_to_change": ["scripts/controller_pipeline.py"],
                              "files_not_allowed_to_change": [], "required_checks": [],
                              "received_from": sorted(payload["inputs"])}
                (Path(repo) / pipeline.RESULT_FILE).write_text(json.dumps(result), encoding="utf-8")
            return _Rep(True, run_id=run_id, role=role)

        orig = controller_run.run
        controller_run.run = _fake_run
        try:
            pipeline.run_pipeline(ROOT, "obj", "par-test", cache=False, max_parallel=4)
            self.assertGreater(peak["max"], 1, "independent producers must run concurrently")
        finally:
            controller_run.run = orig


if __name__ == "__main__":
    unittest.main()
