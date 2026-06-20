"""Per-stage scope isolation (Issue #39): a write role runs in its OWN worktree (detached at HEAD), so
its scope check sees only its own diff — an implementer is never charged for, nor even sees, a CI
writer's .github edits — and every write role's changes still merge back into the workspace.

Proven against a throwaway git workspace with AI_ORG_ROOT pointing at the codex org install (the same
cross-repo shape the cockpit dogfoods): the real registry/DAG, a mocked carrier that actually writes a
file in each write role's scope and records what it can see."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "packages" / "codex-org-bootstrap" / "src"))

import controller_pipeline as pipeline  # noqa: E402
import controller_run  # noqa: E402

# one CI writer's merged-back file: the implementer must NOT see this in its isolated worktree
CI_FILE = ".github/workflows/functional-ci-action-writer.yml"
IMPL_FILE = "src/impl_marker.py"


class _Rep:
    def __init__(self, ok, run_id, role, changed):
        self.ok, self.run_id, self.role, self.changed = ok, run_id, role, changed

    def to_dict(self):
        d = {"ok": self.ok, "changed_files": list(self.changed),
             "attempts": [{"attempt": 0, "exit": 0, "timed_out": False, "stdin_hang": False,
                           "log": f".agent-runs/controller/{self.run_id}/carrier-attempt0.log"}]}
        if self.changed:
            d["diff_artifact"] = {"path": f".agent-runs/controller/{self.run_id}/diff.patch",
                                  "sha256": f"sha-{self.role}", "bytes": 1, "untracked_count": 0}
        return d


class WriteRoleIsolationTests(unittest.TestCase):
    def setUp(self):
        self._orig = controller_run.run
        self._orig_env = os.environ.get("AI_ORG_ROOT")
        os.environ["AI_ORG_ROOT"] = str(ROOT)          # org install = codex; workspace = the temp repo
        self.tmp = tempfile.mkdtemp(prefix="iso-ws-")
        self.work = Path(self.tmp) / "work"
        self.work.mkdir(parents=True)
        self._git("init")
        self._git("config", "user.email", "t@e.x")
        self._git("config", "user.name", "t")
        self._git("checkout", "-b", "main")
        (self.work / "seed").write_text("seed\n", encoding="utf-8")
        self._git("add", "-A")
        self._git("commit", "-m", "init")
        self.visibility = {}

        def _fake_run(repo, contract, run_id, *, cache=True, resume_session=None, **_):
            repo = Path(repo)
            role = contract["role"]
            if "files_allowed_to_change" not in contract:          # producer / aufheben: emit result.json
                payload = json.loads(contract["prompt"])
                result = {"role_id": role, "seen_inputs": payload["inputs"]}
                if role == "aufheben-designer":
                    result = {"role_id": role, "contract_id": "impl-1",
                              "objective": payload["objective"],
                              "files_allowed_to_change": [IMPL_FILE],
                              "files_not_allowed_to_change": [], "required_checks": [],
                              "received_from": sorted(payload["inputs"])}
                (repo / pipeline.RESULT_FILE).write_text(json.dumps(result), encoding="utf-8")
                return _Rep(True, run_id, role, [])
            # a write role: record what it can SEE, then write a file in its own scope
            if role == "implementer":
                # the CI writers ran in an earlier wave and merged .github back into the WORKSPACE;
                # this isolated worktree is off HEAD, so that file must be invisible here.
                self.visibility["impl_sees_ci_file"] = (repo / CI_FILE).is_file()
                rel = IMPL_FILE
            else:
                rel = f".github/workflows/{role}.yml"
            p = repo / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(f"# {role}\n", encoding="utf-8")
            return _Rep(True, run_id, role, [rel])

        controller_run.run = _fake_run

    def tearDown(self):
        controller_run.run = self._orig
        if self._orig_env is None:
            os.environ.pop("AI_ORG_ROOT", None)
        else:
            os.environ["AI_ORG_ROOT"] = self._orig_env
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _git(self, *args):
        return subprocess.run(["git", "-C", str(self.work), *args], capture_output=True, text=True)

    def test_implementer_is_isolated_from_ci_writer_edits_and_all_changes_merge_back(self):
        result = pipeline.run_pipeline(self.work, "isolation", "iso-test", cache=False)
        self.assertTrue(all(result["summary"].values()), result["summary"])

        # ISOLATION: the implementer never saw the CI writer's .github file in its worktree
        self.assertIn("impl_sees_ci_file", self.visibility)
        self.assertFalse(self.visibility["impl_sees_ci_file"],
                         "implementer must NOT see a CI writer's merged-back .github edit (Issue #39)")

        # MERGE-BACK: every write role's own change landed in the workspace
        for role in ("functional-ci-action-writer", "nonfunctional-ci-action-writer",
                     "security-ci-action-writer"):
            self.assertTrue((self.work / f".github/workflows/{role}.yml").is_file(),
                            f"{role}'s workflow should merge back")
        self.assertTrue((self.work / IMPL_FILE).is_file(), "implementer's file should merge back")

    def test_isolation_holds_under_parallel(self):
        result = pipeline.run_pipeline(self.work, "isolation", "iso-test", cache=False, max_parallel=4)
        self.assertTrue(all(result["summary"].values()), result["summary"])
        self.assertFalse(self.visibility.get("impl_sees_ci_file", True))
        self.assertTrue((self.work / IMPL_FILE).is_file())
        self.assertTrue((self.work / f".github/workflows/security-ci-action-writer.yml").is_file())


if __name__ == "__main__":
    unittest.main()
