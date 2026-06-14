from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


verifiers = _load("controller_verifiers")
workflow = _load("controller_workflow")


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _new_repo(tmp):
    repo = Path(tmp)
    git(repo, "init"); git(repo, "config", "user.email", "t@t"); git(repo, "config", "user.name", "t")
    (repo / "seed.txt").write_text("seed"); git(repo, "add", "-A"); git(repo, "commit", "-m", "seed")
    return repo


def _stub_carrier(write_path=None, ok=True):
    def runner(repo, prompt, sandbox, *, timeout, retries, out_dir):
        if write_path:
            (Path(repo) / write_path).write_text("carrier output")
        return {"ok": ok, "attempts": [{"attempt": 0, "exit": 0 if ok else 1,
                                        "timed_out": not ok, "stdin_hang": False}]}
    return runner


PASS = {"name": "ok", "argv": ["python3", "-c", "import sys; sys.exit(0)"]}
FAIL = {"name": "bad", "argv": ["python3", "-c", "import sys; sys.exit(2)"]}


class VerifierTests(unittest.TestCase):
    def test_pass_fail_error_shapes(self):
        with tempfile.TemporaryDirectory() as d:
            runs = verifiers.run_all([PASS, FAIL], evidence_dir=d)
            self.assertEqual([r.status for r in runs], ["pass", "fail"])
            self.assertEqual(runs[1].exit_code, 2)
            self.assertTrue(Path(runs[0].evidence_path).is_file())
            self.assertFalse(verifiers.all_passed(runs))


class WorkflowTests(unittest.TestCase):
    def _contract(self, allowed):
        return {"role": "implementer", "prompt": "do x", "sandbox": "workspace-write",
                "timeout": 60, "retries": 0, "files_allowed_to_change": allowed}

    def test_happy_path(self):
        with tempfile.TemporaryDirectory() as d:
            repo = _new_repo(d)
            rep = workflow.run_contract(
                repo, self._contract(["allowed.txt"]), "run-h",
                verifier_specs=[PASS], include_builtin_gates=False,
                carrier_runner=_stub_carrier("allowed.txt"), clock=lambda: 1)
            self.assertTrue(rep.ok)
            self.assertEqual(rep.changed_files, ["allowed.txt"])
            self.assertEqual(rep.unresolved_failures, [])

    def test_scope_violation_blocks(self):
        with tempfile.TemporaryDirectory() as d:
            repo = _new_repo(d)
            rep = workflow.run_contract(
                repo, self._contract(["allowed.txt"]), "run-s",
                verifier_specs=[PASS], include_builtin_gates=False,
                carrier_runner=_stub_carrier("extra.txt"), clock=lambda: 1)
            self.assertFalse(rep.ok)
            self.assertIn("extra.txt", rep.scope["deviations"])
            self.assertTrue(any("scope" in u for u in rep.unresolved_failures))

    def test_carrier_failure_blocks(self):
        with tempfile.TemporaryDirectory() as d:
            repo = _new_repo(d)
            rep = workflow.run_contract(
                repo, self._contract(["allowed.txt"]), "run-c",
                verifier_specs=[PASS], include_builtin_gates=False,
                carrier_runner=_stub_carrier("allowed.txt", ok=False), clock=lambda: 1)
            self.assertFalse(rep.ok)
            self.assertTrue(any("carrier" in u for u in rep.unresolved_failures))

    def test_verifier_failure_blocks(self):
        with tempfile.TemporaryDirectory() as d:
            repo = _new_repo(d)
            rep = workflow.run_contract(
                repo, self._contract(["allowed.txt"]), "run-v",
                verifier_specs=[FAIL], include_builtin_gates=False,
                carrier_runner=_stub_carrier("allowed.txt"), clock=lambda: 1)
            self.assertFalse(rep.ok)
            self.assertTrue(any("verifier bad" in u for u in rep.unresolved_failures))

    def test_journal_records_all_phases(self):
        with tempfile.TemporaryDirectory() as d:
            repo = _new_repo(d)
            workflow.run_contract(
                repo, self._contract(["allowed.txt"]), "run-j",
                verifier_specs=[PASS], include_builtin_gates=False,
                carrier_runner=_stub_carrier("allowed.txt"), clock=lambda: 1)
            journal = repo / ".agent-runs" / "controller" / "run-j" / "journal.jsonl"
            phases = [__import__("json").loads(l)["phase"] for l in journal.read_text().splitlines()]
            for expected in ["validate_contract", "baseline", "run_carrier", "enforce_scope",
                             "run_verifiers", "package_evidence"]:
                self.assertIn(expected, phases)

    def test_invalid_contract_fails_closed(self):
        with tempfile.TemporaryDirectory() as d:
            repo = _new_repo(d)
            with self.assertRaises(Exception):
                workflow.run_contract(repo, {"role": "", "prompt": "x"}, "run-bad",
                                      include_builtin_gates=False, carrier_runner=_stub_carrier())


if __name__ == "__main__":
    unittest.main()
