"""Clean implementer quality gate + content-hash baseline (reimplementation, Linon-defects designed out)."""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))

import quality_gate            # noqa: E402
import controller_scope as scope  # noqa: E402
import controller_workflow as workflow  # noqa: E402


def git(repo, *a):
    subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)


def _repo(tmp):
    r = Path(tmp)
    git(r, "init"); git(r, "config", "user.email", "t@t"); git(r, "config", "user.name", "t")
    (r / "seed.txt").write_text("s"); git(r, "add", "-A"); git(r, "commit", "-m", "s")
    return r


def _stub(rel, content):
    def runner(repo, prompt, sandbox, *, timeout, retries, out_dir, **_):
        (Path(repo) / rel).write_text(content)
        return {"ok": True, "attempts": [{"attempt": 0, "exit": 0}]}
    return runner


def _contract(allowed):
    return {"role": "implementer", "prompt": "p", "sandbox": "workspace-write",
            "timeout": 30, "retries": 0, "files_allowed_to_change": allowed}


class QualityGateTests(unittest.TestCase):
    def test_self_test(self):
        self.assertEqual(quality_gate.self_test(), 0)

    def test_debug_regex_broad_but_not_bare_word(self):
        for good in ["x  # [DEBUG-1234]", "[debug_ab]", "y # [Debug:tmp9]"]:
            self.assertTrue(quality_gate.DEBUG_TAG_RE.search(good), good)
        for bad in ["debugging the parser", "# a normal comment"]:
            self.assertIsNone(quality_gate.DEBUG_TAG_RE.search(bad), bad)

    def test_graceful_without_tools(self):
        with tempfile.TemporaryDirectory() as d:
            r = _repo(d); (r / "a.py").write_text("x = 1\n")
            q = quality_gate.run_quality_gate(["a.py"], r)
            self.assertTrue(q["quality_pass"])  # no tools → pass; no debug tags

    def test_debug_tag_caught(self):
        with tempfile.TemporaryDirectory() as d:
            r = _repo(d); (r / "a.py").write_text("x = 1  # [DEBUG-1234] left\n")
            q = quality_gate.run_quality_gate(["a.py"], r)
            self.assertFalse(q["quality_pass"])
            self.assertTrue(q["debug_tags"])

    def test_collect_fail_closed(self):
        # invoked tool that errors → tools_failed (the self-test asserts this too)
        errs, used, failed = [], [], []
        quality_gate._collect("t", [sys.executable, "-c", "import sys;sys.exit(2)"],
                              Path("."), lambda o, e: None, errs, used, failed)
        self.assertIn("t", failed)


class ContentBaselineTests(unittest.TestCase):
    def test_changed_since_catches_re_edited_dirty_file(self):
        with tempfile.TemporaryDirectory() as d:
            r = _repo(d)
            (r / "f.txt").write_text("dirty v1")          # pre-dirty before snapshot
            snap = scope.baseline_snapshot(r)
            self.assertNotIn("f.txt", scope.changed_since(r, snap))  # unchanged since snapshot
            (r / "f.txt").write_text("carrier edited v2")  # carrier edits it further
            self.assertIn("f.txt", scope.changed_since(r, snap))     # caught by content

    def test_enforce_with_snapshot_flags_re_edited_out_of_scope(self):
        with tempfile.TemporaryDirectory() as d:
            r = _repo(d)
            (r / "f.txt").write_text("dirty")             # pre-dirty
            snap = scope.baseline_snapshot(r)
            (r / "f.txt").write_text("changed by carrier")  # re-edited, out of allowed scope
            rep = scope.enforce(r, ["allowed/**"], baseline_snapshot=snap)
            self.assertIn("f.txt", rep.changed)
            self.assertFalse(rep.scope_ok)

    def test_changed_since_catches_revert_of_tracked(self):
        # carrier reverting a pre-dirty tracked file to HEAD must still be attributed (it leaves the
        # post-run touched set, so path-only iteration would miss it — Linon re-review #1)
        with tempfile.TemporaryDirectory() as d:
            r = _repo(d)
            (r / "seed.txt").write_text("dirtied")        # pre-dirty tracked
            snap = scope.baseline_snapshot(r)
            (r / "seed.txt").write_text("s")              # carrier reverts to HEAD content
            self.assertIn("seed.txt", scope.changed_since(r, snap))

    def test_changed_since_catches_delete_of_dirty_untracked(self):
        with tempfile.TemporaryDirectory() as d:
            r = _repo(d)
            (r / "u.txt").write_text("untracked dirty")   # pre-dirty untracked
            snap = scope.baseline_snapshot(r)
            (r / "u.txt").unlink()                         # carrier deletes it
            self.assertIn("u.txt", scope.changed_since(r, snap))

    def test_untracked_dir_expanded_not_collapsed(self):
        with tempfile.TemporaryDirectory() as d:
            r = _repo(d)
            (r / "pkg").mkdir(); (r / "pkg" / "a.txt").write_text("1")  # pre-existing untracked dir
            snap = scope.baseline_snapshot(r)
            (r / "pkg" / "b.txt").write_text("carrier added inside")    # new file inside the dir
            changed = scope.changed_since(r, snap)
            self.assertIn("pkg/b.txt", changed)           # -uall expands; not hidden behind pkg/


class WorkflowQualityTests(unittest.TestCase):
    def test_disabled_by_default(self):
        with tempfile.TemporaryDirectory() as d:
            r = _repo(d)
            rep = workflow.run_contract(r, _contract(["a.py"]), "qd",
                                        carrier_runner=_stub("a.py", "x = 1\n"),
                                        include_builtin_gates=False, clock=lambda: 1)
            self.assertIsNone(rep.quality)
            self.assertTrue(rep.ok)

    def test_enabled_clean_passes(self):
        with tempfile.TemporaryDirectory() as d:
            r = _repo(d)
            rep = workflow.run_contract(r, _contract(["a.py"]), "qp",
                                        carrier_runner=_stub("a.py", "x = 1\n"),
                                        include_builtin_gates=False, quality_gate_enabled=True,
                                        clock=lambda: 1)
            self.assertIsNotNone(rep.quality)
            self.assertTrue(rep.quality["quality_pass"])
            self.assertTrue(rep.ok)

    def test_enabled_blocks_on_debug_tag(self):
        with tempfile.TemporaryDirectory() as d:
            r = _repo(d)
            rep = workflow.run_contract(r, _contract(["a.py"]), "qb",
                                        carrier_runner=_stub("a.py", "x = 1  # [DEBUG-1234]\n"),
                                        include_builtin_gates=False, quality_gate_enabled=True,
                                        clock=lambda: 1)
            self.assertFalse(rep.ok)
            self.assertTrue(any("quality gate" in u for u in rep.unresolved_failures))


if __name__ == "__main__":
    unittest.main()
