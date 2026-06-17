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
        self.calls = []

        def _fake_run(repo, contract, run_id, *, cache=True):
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
                    }
                (Path(repo) / pipeline.RESULT_FILE).write_text(json.dumps(result), encoding="utf-8")
            return _Rep(True, run_id=run_id, role=role)

        controller_run.run = _fake_run

    def tearDown(self):
        controller_run.run = self._orig
        result_file = ROOT / pipeline.RESULT_FILE
        if result_file.exists():
            result_file.unlink()
        shutil.rmtree(ROOT / ".agent-runs" / "controller" / "pipe-test", ignore_errors=True)

    def test_registry_dag_order_fan_in_and_verifiers(self):
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
            "stefan",
        ])
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
        self.assertEqual(by_role["stefan"]["inputs"]["implementer"]["diff_artifact"]["sha256"],
                         "diff-sha-implementer")

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

    def test_linon_findings_trigger_repair_loop_until_converged(self):
        linon_runs = 0

        def _repair_run(repo, contract, run_id, *, cache=True):
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
            "stefan",
            "aggressive-designer",
            "conservative-designer",
            "genius",
            "aufheben-designer",
            "implementer",
            "linon",
        ])

        repair_designer_payload = self.calls[10][1]
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

        def _capped_run(repo, contract, run_id, *, cache=True):
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

    def test_malformed_result_json_fails_stage_without_crashing(self):
        def _malformed_run(repo, contract, run_id, *, cache=True):
            role = contract["role"]
            payload = json.loads(contract["prompt"])
            self.calls.append((role, payload, run_id, cache, contract))
            if "files_allowed_to_change" not in contract:
                (Path(repo) / pipeline.RESULT_FILE).write_text("{not valid json", encoding="utf-8")
            return _Rep(True, run_id=run_id, role=role)

        controller_run.run = _malformed_run

        result = pipeline.run_pipeline(ROOT, "wire declared org", "pipe-test", cache=False)

        self.assertFalse(result["required_ok"]["aggressive-designer"])
        self.assertFalse(result["converged"])
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
        def _freeze_run(repo, contract, run_id, *, cache=True):
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

        def _fake_run(repo, contract, run_id, *, cache=True):
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
