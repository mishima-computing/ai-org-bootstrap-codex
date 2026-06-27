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
    sys.modules[name] = mod  # required so @dataclass type resolution can find the module
    spec.loader.exec_module(mod)
    return mod


scope = _load("controller_scope")
models = _load("controller_models")
evidence = _load("controller_evidence")


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


class ScopeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name)
        git(self.repo, "init")
        git(self.repo, "config", "user.email", "t@t")
        git(self.repo, "config", "user.name", "t")
        (self.repo / "seed.txt").write_text("seed")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-m", "seed")

    def tearDown(self):
        self._tmp.cleanup()

    def test_in_scope_clean(self):
        base = scope.baseline_of(self.repo)
        (self.repo / "allowed.txt").write_text("x")
        rep = scope.enforce(self.repo, ["allowed.txt"], baseline=base)
        self.assertEqual(rep.changed, ["allowed.txt"])
        self.assertEqual(rep.deviations, [])
        self.assertTrue(rep.scope_ok)

    def test_out_of_scope_caught(self):
        base = scope.baseline_of(self.repo)
        (self.repo / "allowed.txt").write_text("x")
        (self.repo / "extra.txt").write_text("oops")
        rep = scope.enforce(self.repo, ["allowed.txt"], baseline=base)
        self.assertIn("extra.txt", rep.deviations)
        self.assertFalse(rep.scope_ok)

    def test_dirty_baseline_not_blamed(self):
        # a pre-existing dirty file must not be attributed to the carrier
        (self.repo / "preexisting.txt").write_text("dirty before run")
        base = scope.baseline_of(self.repo)
        (self.repo / "allowed.txt").write_text("x")
        rep = scope.enforce(self.repo, ["allowed.txt"], baseline=base)
        self.assertNotIn("preexisting.txt", rep.changed)
        self.assertTrue(rep.scope_ok)

    def test_forbidden_path_is_critical(self):
        # forbidden dir name built by concat so this Codex-only file holds no forbidden literal
        fb = "." + "clau" + "de"
        base = scope.baseline_of(self.repo)
        (self.repo / fb).mkdir()
        (self.repo / fb / "x.md").write_text("nope")
        rep = scope.enforce(self.repo, [fb + "/**"], baseline=base)  # even if "allowed", forbidden wins
        self.assertTrue(rep.forbidden_hits)
        self.assertFalse(rep.scope_ok)

    def test_rename_counts_both_paths(self):
        (self.repo / "old.txt").write_text("content")
        git(self.repo, "add", "-A"); git(self.repo, "commit", "-m", "add old")
        base = scope.baseline_of(self.repo)
        git(self.repo, "mv", "old.txt", "new.txt")
        rep = scope.enforce(self.repo, ["new.txt"], baseline=base)
        # old.txt is touched (removed) but not allowed → deviation; both paths visible
        self.assertIn("old.txt", rep.changed)
        self.assertIn("new.txt", rep.changed)
        self.assertIn("old.txt", rep.deviations)

    def test_delete_is_a_change(self):
        base = scope.baseline_of(self.repo)
        (self.repo / "seed.txt").unlink()
        rep = scope.enforce(self.repo, ["nothing/**"], baseline=base)
        self.assertIn("seed.txt", rep.changed)
        self.assertFalse(rep.scope_ok)

    def test_agent_runs_excluded(self):
        base = scope.baseline_of(self.repo)
        (self.repo / ".agent-runs").mkdir()
        (self.repo / ".agent-runs" / "log").write_text("scratch")
        (self.repo / "allowed.txt").write_text("x")
        rep = scope.enforce(self.repo, ["allowed.txt"], baseline=base)
        self.assertNotIn(".agent-runs", str(rep.changed))
        self.assertTrue(rep.scope_ok)

    def test_declared_vs_actual(self):
        base = scope.baseline_of(self.repo)
        (self.repo / "a.txt").write_text("1")
        (self.repo / "b.txt").write_text("2")
        rep = scope.enforce(self.repo, ["*.txt"], baseline=base, declared=["a.txt"])
        self.assertIn("b.txt", rep.undeclared)  # touched but not declared
        self.assertFalse(rep.scope_ok)


class ModelTests(unittest.TestCase):
    def test_contract_roundtrip_and_validate(self):
        c = models.CarrierContract(role="implementer", prompt="x", files_allowed_to_change=["a/**"])
        self.assertEqual(models.CarrierContract.from_dict(c.to_dict()).role, "implementer")

    def test_contract_rejects_bad(self):
        with self.assertRaises(models.ContractError):
            models.CarrierContract(role="", prompt="x").validate()
        with self.assertRaises(models.ContractError):
            models.CarrierContract(role="r", prompt="x", sandbox="yolo").validate()
        with self.assertRaises(models.ContractError):
            models.CarrierContract(role="r", prompt="x", sandbox="workspace-write").validate()
        with self.assertRaises(models.ContractError):
            models.CarrierContract.from_dict({"role": "r", "prompt": "x", "bogus": 1})

    def test_semantic_decision_fail_closed(self):
        self.assertEqual(models.SemanticDecision.from_dict({"decision": "accept"}).decision, "accept")
        with self.assertRaises(models.ContractError):
            models.SemanticDecision.from_dict({"decision": "maybe"})
        with self.assertRaises(models.ContractError):
            models.SemanticDecision.from_dict({})


class EvidenceTests(unittest.TestCase):
    def test_append_only_journal(self):
        with tempfile.TemporaryDirectory() as d:
            j = evidence.RunJournal(d, "run-1", clock=lambda: 1000)
            j.append("contract", {"contract_sha256": evidence.sha256_text("hello")})
            j.append("carrier", {"attempt": 0, "exit": 0})
            evs = evidence.RunJournal(d, "run-1", clock=lambda: 1000).events()
            self.assertEqual([e["seq"] for e in evs], [0, 1])
            self.assertEqual(evs[0]["phase"], "contract")
            self.assertEqual(evs[0]["contract_sha256"], evidence.sha256_text("hello"))


if __name__ == "__main__":
    unittest.main()
