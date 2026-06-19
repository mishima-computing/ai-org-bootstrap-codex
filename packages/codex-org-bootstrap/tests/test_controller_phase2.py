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

    def _forbidden_contract(self, allowed):
        return {**self._contract(allowed), "forbidden_paths": [".github/*"]}

    def test_coordination_forbidden_is_stripped_and_passes(self):
        # an over-eager implementer adds a CI-writer's .github/workflows file "to be helpful". That extra
        # must be reverted BEFORE the scope check so it neither lands nor sinks the otherwise-correct stage.
        with tempfile.TemporaryDirectory() as d:
            repo = _new_repo(d)
            def carrier(repo, prompt, sandbox, *, timeout, retries, out_dir):
                (Path(repo) / "allowed.txt").write_text("ok")
                wf = Path(repo) / ".github" / "workflows"; wf.mkdir(parents=True)
                (wf / "x.yml").write_text("on: push")
                return {"ok": True, "attempts": [{"attempt": 0, "exit": 0,
                                                  "timed_out": False, "stdin_hang": False}]}
            rep = workflow.run_contract(
                repo, self._forbidden_contract(["allowed.txt"]), "run-strip",
                verifier_specs=[PASS], include_builtin_gates=False, carrier_runner=carrier, clock=lambda: 1)
            self.assertTrue(rep.ok, rep.unresolved_failures)
            self.assertEqual(rep.changed_files, ["allowed.txt"])
            self.assertFalse((repo / ".github" / "workflows" / "x.yml").exists())

    def test_coordination_forbidden_leftover_from_earlier_iteration_is_stripped(self):
        # REGRESSION: a .github/workflows file created by an EARLIER (timed-out) repair iteration persists
        # in the shared worktree and sits in THIS stage's baseline. The old strip looked only at this
        # stage's delta (changed_since), so it skipped the leftover — but enforce flags forbidden against
        # the FULL dirty set, so every later repair stage failed forever on a file its own delta no longer
        # contained. Strip must clean the full set.
        with tempfile.TemporaryDirectory() as d:
            repo = _new_repo(d)
            wf = Path(repo) / ".github" / "workflows"; wf.mkdir(parents=True)
            (wf / "old.yml").write_text("leftover from a prior timed-out iteration")  # pre-existing dirty
            rep = workflow.run_contract(
                repo, self._forbidden_contract(["allowed.txt"]), "run-leftover",
                verifier_specs=[PASS], include_builtin_gates=False,
                carrier_runner=_stub_carrier("allowed.txt"), clock=lambda: 1)
            self.assertTrue(rep.ok, rep.unresolved_failures)        # was False before the fix
            self.assertFalse((wf / "old.yml").exists())             # leftover cleaned

    def test_coordination_forbidden_cleaned_even_when_carrier_fails(self):
        # the strip runs regardless of carrier_ok, so a forbidden leftover does not survive a timed-out
        # iteration into the next one (the stage still fails on the carrier, but the worktree starts clean).
        with tempfile.TemporaryDirectory() as d:
            repo = _new_repo(d)
            wf = Path(repo) / ".github" / "workflows"; wf.mkdir(parents=True)
            (wf / "old.yml").write_text("leftover")
            rep = workflow.run_contract(
                repo, self._forbidden_contract(["allowed.txt"]), "run-clean-onfail",
                verifier_specs=[PASS], include_builtin_gates=False,
                carrier_runner=_stub_carrier("allowed.txt", ok=False), clock=lambda: 1)
            self.assertFalse(rep.ok)                                 # carrier failed -> stage fails
            self.assertFalse((wf / "old.yml").exists())              # ...but the forbidden leftover is cleaned

    def test_journal_records_all_phases(self):
        with tempfile.TemporaryDirectory() as d:
            repo = _new_repo(d)
            workflow.run_contract(
                repo, self._contract(["allowed.txt"]), "run-j",
                verifier_specs=[PASS], include_builtin_gates=False,
                carrier_runner=_stub_carrier("allowed.txt"), clock=lambda: 1)
            journal = repo / ".agent-runs" / "controller" / "run-j" / "journal.jsonl"
            phases = [__import__("json").loads(l)["phase"] for l in journal.read_text(encoding="utf-8").splitlines()]
            for expected in ["validate_contract", "baseline", "run_carrier", "enforce_scope",
                             "run_verifiers", "package_evidence"]:
                self.assertIn(expected, phases)

    def test_invalid_contract_fails_closed(self):
        with tempfile.TemporaryDirectory() as d:
            repo = _new_repo(d)
            with self.assertRaises(Exception):
                workflow.run_contract(repo, {"role": "", "prompt": "x"}, "run-bad",
                                      include_builtin_gates=False, carrier_runner=_stub_carrier())


class ProducerOutputRetryTests(unittest.TestCase):
    """A producer can exit cleanly yet leave an empty result.json (transient carrier miss); the
    process-level retry never fires for that. _ensure_producer_output re-runs until the deliverable is
    non-empty so one flake does not sink the producer wave."""

    class _Contract:
        prompt = "p"; sandbox = "read-only"; timeout = 600; retries = 1

    class _Journal:
        def __init__(self, d): self.dir = d
        def append(self, *a, **k): pass

    def test_empty_deliverable_is_retried_until_filled(self):
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d)
            (repo / "result.json").write_text("", encoding="utf-8")     # the transient empty miss
            calls = {"n": 0}

            def fake_runner(repo_, prompt, sandbox, *, timeout, retries, out_dir, output_file=None):
                calls["n"] += 1
                Path(output_file).write_text('{"role_id":"genius"}', encoding="utf-8")  # the re-run delivers
                return {"ok": True, "attempts": []}

            orig = workflow._default_carrier_runner
            workflow._default_carrier_runner = fake_runner
            try:
                carrier, ok = workflow._ensure_producer_output(
                    repo, self._Contract(), "schemas/x.json", "result.json", {"ok": True}, True,
                    self._Journal(repo))
            finally:
                workflow._default_carrier_runner = orig
            self.assertEqual(calls["n"], 1)                              # retried once, then non-empty
            self.assertTrue((repo / "result.json").read_text().strip())
            self.assertTrue(ok)

    def test_nonempty_deliverable_is_not_retried(self):
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d)
            (repo / "result.json").write_text('{"already":"here"}', encoding="utf-8")
            calls = {"n": 0}

            def fake_runner(*a, **k):
                calls["n"] += 1
                return {"ok": True}

            orig = workflow._default_carrier_runner
            workflow._default_carrier_runner = fake_runner
            try:
                workflow._ensure_producer_output(repo, self._Contract(), "schemas/x.json",
                                                 "result.json", {"ok": True}, True, self._Journal(repo))
            finally:
                workflow._default_carrier_runner = orig
            self.assertEqual(calls["n"], 0)                              # good output → no re-run


if __name__ == "__main__":
    unittest.main()
