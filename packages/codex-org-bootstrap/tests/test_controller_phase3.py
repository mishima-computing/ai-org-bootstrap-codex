from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))  # controller_loop + its sibling imports resolve here

import controller_loop as loop  # noqa: E402


def git(repo, *a):
    subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)


def _new_repo(tmp):
    repo = Path(tmp)
    git(repo, "init"); git(repo, "config", "user.email", "t@t"); git(repo, "config", "user.name", "t")
    (repo / "seed.txt").write_text("seed"); git(repo, "add", "-A"); git(repo, "commit", "-m", "s")
    return repo


def _stub_carrier(write_path):
    def runner(repo, prompt, sandbox, *, timeout, retries, out_dir):
        if write_path:
            (Path(repo) / write_path).write_text("x")
        return {"ok": True, "attempts": [{"attempt": 0, "exit": 0}]}
    return runner


PASS = {"name": "ok", "argv": ["python3", "-c", "import sys;sys.exit(0)"]}


def _contract(allowed):
    return {"role": "implementer", "prompt": "do", "sandbox": "workspace-write",
            "timeout": 60, "retries": 0, "files_allowed_to_change": allowed}


class LoopTests(unittest.TestCase):
    def test_accept_terminates_first_round(self):
        with tempfile.TemporaryDirectory() as d:
            repo = _new_repo(d)
            res = loop.run_loop(repo, _contract(["allowed.txt"]), "L1",
                                decider=lambda rep, r: {"decision": "accept"},
                                carrier_runner=_stub_carrier("allowed.txt"),
                                verifier_specs=[PASS], include_builtin_gates=False, clock=lambda: 1)
            self.assertEqual(res["final"], "accept")
            self.assertEqual(res["round_count"], 1)

    def test_revise_then_accept(self):
        with tempfile.TemporaryDirectory() as d:
            repo = _new_repo(d)
            calls = {"n": 0}

            def decider(report, r):
                calls["n"] += 1
                if r == 0:
                    # first round failed scope; revise with a wider allow-list
                    self_ok = report["ok"]
                    return {"decision": "revise_contract", "rationale": "widen scope",
                            "next_contract": _contract(["allowed.txt", "extra.txt"])}
                return {"decision": "accept"}
            # round 0 carrier writes extra.txt (out of scope of allowed.txt) -> report not ok
            res = loop.run_loop(repo, _contract(["allowed.txt"]), "L2",
                                decider=decider, carrier_runner=_stub_carrier("extra.txt"),
                                verifier_specs=[PASS], include_builtin_gates=False,
                                max_rounds=3, clock=lambda: 1)
            self.assertEqual(res["final"], "accept")
            self.assertEqual(res["round_count"], 2)
            self.assertEqual(calls["n"], 2)

    def test_block_terminates(self):
        with tempfile.TemporaryDirectory() as d:
            repo = _new_repo(d)
            res = loop.run_loop(repo, _contract(["allowed.txt"]), "L3",
                                decider=lambda rep, r: {"decision": "block", "rationale": "stop"},
                                carrier_runner=_stub_carrier("allowed.txt"),
                                verifier_specs=[PASS], include_builtin_gates=False, clock=lambda: 1)
            self.assertEqual(res["final"], "block")

    def test_max_rounds_blocks(self):
        with tempfile.TemporaryDirectory() as d:
            repo = _new_repo(d)
            res = loop.run_loop(
                repo, _contract(["allowed.txt"]), "L4",
                decider=lambda rep, r: {"decision": "revise_contract",
                                        "next_contract": _contract(["allowed.txt"])},
                carrier_runner=_stub_carrier("allowed.txt"),
                verifier_specs=[PASS], include_builtin_gates=False, max_rounds=2, clock=lambda: 1)
            self.assertEqual(res["final"], "block")
            self.assertEqual(res["reason"], "max_rounds exhausted")
            self.assertEqual(res["round_count"], 2)

    def test_loop_without_next_contract_fails_closed(self):
        with tempfile.TemporaryDirectory() as d:
            repo = _new_repo(d)
            res = loop.run_loop(repo, _contract(["allowed.txt"]), "L5",
                                decider=lambda rep, r: {"decision": "revise_contract"},  # no next_contract
                                carrier_runner=_stub_carrier("allowed.txt"),
                                verifier_specs=[PASS], include_builtin_gates=False, clock=lambda: 1)
            self.assertEqual(res["final"], "block")
            self.assertIn("next_contract", res["reason"])

    def test_invalid_decision_raises(self):
        with tempfile.TemporaryDirectory() as d:
            repo = _new_repo(d)
            with self.assertRaises(Exception):
                loop.run_loop(repo, _contract(["allowed.txt"]), "L6",
                              decider=lambda rep, r: {"decision": "maybe"},
                              carrier_runner=_stub_carrier("allowed.txt"),
                              verifier_specs=[PASS], include_builtin_gates=False, clock=lambda: 1)


if __name__ == "__main__":
    unittest.main()
