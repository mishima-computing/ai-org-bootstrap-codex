"""Red→green tests for the defects Linon found in the controller modules (ADR-0004 review)."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))

import carrier_harness            # noqa: E402
import controller_scope as scope  # noqa: E402
import controller_models as models  # noqa: E402
import controller_verifiers as verifiers  # noqa: E402
import controller_workflow as workflow    # noqa: E402
import controller_loop as cloop           # noqa: E402
from controller_evidence import RunJournal  # noqa: E402


def git(repo, *a):
    subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)


def _repo(tmp):
    r = Path(tmp); git(r, "init"); git(r, "config", "user.email", "t@t"); git(r, "config", "user.name", "t")
    (r / "seed.txt").write_text("s"); git(r, "add", "-A"); git(r, "commit", "-m", "s")
    return r


def _stub(write):
    def runner(repo, prompt, sandbox, *, timeout, retries, out_dir, **_):
        if write:
            target = Path(repo) / write
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("x")
        return {"ok": True, "attempts": [{"attempt": 0, "exit": 0}]}
    return runner


class LinonFixTests(unittest.TestCase):
    def test_empty_allowlist_is_fail_closed(self):
        with tempfile.TemporaryDirectory() as d:
            r = _repo(d); base = scope.baseline_of(r); (r / "x.txt").write_text("1")
            rep = scope.enforce(r, [], baseline=base)  # empty allow = nothing allowed
            self.assertIn("x.txt", rep.deviations)
            self.assertFalse(rep.scope_ok)

    def test_forbidden_caught_even_if_pre_dirty(self):
        with tempfile.TemporaryDirectory() as d:
            r = _repo(d)
            fb = "." + "clau" + "de"
            (r / fb).mkdir(); (r / fb / "x").write_text("pre")  # forbidden path already dirty
            base = scope.baseline_of(r)                          # baseline includes it
            (r / fb / "x").write_text("more")                    # carrier touches it again
            rep = scope.enforce(r, ["**"], baseline=base)
            self.assertTrue(rep.forbidden_hits)                  # still caught despite baseline
            self.assertFalse(rep.scope_ok)

    def test_git_status_failure_raises(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(scope.ScopeError):
                scope.porcelain_touched(Path(d))  # not a git repo → status fails

    def test_danger_full_access_requires_allowlist(self):
        with self.assertRaises(models.ContractError):
            models.CarrierContract(role="r", prompt="p", sandbox="danger-full-access").validate()

    def test_semantic_decision_rejects_unknown_and_bad_rationale(self):
        with self.assertRaises(models.ContractError):
            models.SemanticDecision.from_dict({"decision": "accept", "bogus": 1})
        with self.assertRaises(models.ContractError):
            models.SemanticDecision.from_dict({"decision": "accept", "rationale": 5})

    def test_contract_forbidden_cannot_replace_default(self):
        with tempfile.TemporaryDirectory() as d:
            r = _repo(d)
            fb = "." + "clau" + "de"
            contract = {"role": "implementer", "prompt": "p", "sandbox": "workspace-write",
                        "timeout": 30, "retries": 0,
                        "files_allowed_to_change": ["**"],          # allow everything
                        "forbidden_paths": ["totally-unrelated/**"]}  # try to swap the forbidden set
            rep = workflow.run_contract(r, contract, "fb",
                                        carrier_runner=_stub(f"{fb}/x"),
                                        include_builtin_gates=False, clock=lambda: 1)
            self.assertFalse(rep.ok)
            self.assertTrue(rep.scope["forbidden_hits"])  # DEFAULT still enforced

    def test_expected_verifier_missing_blocks(self):
        with tempfile.TemporaryDirectory() as d:
            r = _repo(d)
            contract = {"role": "implementer", "prompt": "p", "sandbox": "workspace-write",
                        "timeout": 30, "retries": 0, "files_allowed_to_change": ["allowed.txt"],
                        "expected_verifiers": ["a_gate_that_never_ran"]}
            rep = workflow.run_contract(r, contract, "ev", carrier_runner=_stub("allowed.txt"),
                                        include_builtin_gates=False, clock=lambda: 1)
            self.assertFalse(rep.ok)
            self.assertTrue(any("expected verifiers" in u for u in rep.unresolved_failures))

    def test_loop_accept_on_failed_report_is_overridden(self):
        with tempfile.TemporaryDirectory() as d:
            r = _repo(d)
            contract = {"role": "implementer", "prompt": "p", "sandbox": "workspace-write",
                        "timeout": 30, "retries": 0, "files_allowed_to_change": ["allowed.txt"]}
            # carrier writes OUT of scope → report.ok False; decider tries to accept anyway
            res = cloop.run_loop(r, contract, "ov",
                                 decider=lambda rep, i: {"decision": "accept"},
                                 carrier_runner=_stub("extra.txt"),
                                 include_builtin_gates=False, clock=lambda: 1)
            self.assertEqual(res["final"], "block")  # mechanical failure overrode accept

    def test_journal_tamper_detected(self):
        with tempfile.TemporaryDirectory() as d:
            j = RunJournal(d, "t", clock=lambda: 1)
            j.append("a", {"v": 1}); j.append("b", {"v": 2})
            self.assertEqual(len(j.events(verify=True)), 2)  # clean chain verifies
            # tamper: rewrite the first event's payload
            lines = j.path.read_text(encoding="utf-8").splitlines()
            e0 = json.loads(lines[0]); e0["v"] = 999
            lines[0] = json.dumps(e0)
            j.path.write_text("\n".join(lines) + "\n")
            with self.assertRaises(ValueError):
                RunJournal(d, "t", clock=lambda: 1).events(verify=True)

    def test_builtin_gates_use_running_interpreter(self):
        specs = verifiers.builtin_gate_specs(Path("."))
        self.assertEqual(specs[0]["argv"][0], sys.executable or "python3")

    def test_diff_artifact_includes_untracked(self):
        with tempfile.TemporaryDirectory() as d:
            r = _repo(d)
            (r / "new_deliverable.txt").write_text("carrier made this")  # untracked
            art = carrier_harness.diff_artifact(r, r / ".agent-runs" / "diff.patch")
            body = (r / ".agent-runs" / "diff.patch").read_text(encoding="utf-8")
            self.assertIn("new_deliverable.txt", body)
            self.assertGreaterEqual(art["untracked_count"], 1)


if __name__ == "__main__":
    unittest.main()
