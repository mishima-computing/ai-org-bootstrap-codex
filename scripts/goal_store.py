#!/usr/bin/env python3
"""Durable, method-encapsulated state OWNED BY THE AI ORG — its goals' state is the org's, not
the host's. A host (Shagiri) only READS this via the same record/refs; it never writes them.

The AI Org holds state once goals can be resumed. This is that state, behind a CLUD method surface
(Create / Load / Update / Delete) so callers never touch the backing store directly — the backend can
become sqlite later without changing a single caller.

Layout (all under <repo>/.agent-runs/goals/):
  - <goal_id>.json            one record per goal: status, prompt, org, resumed_from, and the two work
                              fields `wip` (in-progress) and `done` (completed) — each a git commit SHA.
  - refs/goals/<id>/wip,done  the actual work, held IN GIT (a commit off the goal's base), not a loose
                              patch. Resume "calls it with git": restore_wip cherry-picks the wip commit
                              into a fresh worktree. Refs live in the repo's object store, so they survive
                              worktree cleanup AND cockpit restarts.

Heavy content is git's job (content-addressed, integrity-checked, diffable, free dedup/history); the JSON
record is just the small pointer + status. The Stream (ADR-0009) remains the append-only event log.
"""
from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path


def _git(repo: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)


class GoalStore:
    def __init__(self, repo: str):
        self.repo = str(repo)
        self.root = Path(repo).expanduser().resolve() / ".agent-runs" / "goals"
        self._lock = threading.Lock()

    # --- paths / refs -----------------------------------------------------------------------------
    def _path(self, goal_id: str) -> Path:
        return self.root / f"{goal_id}.json"

    @staticmethod
    def _ref(goal_id: str, kind: str) -> str:
        return f"refs/goals/{goal_id}/{kind}"

    # --- CLUD: Create / Load / Update / Delete ----------------------------------------------------
    def create(self, goal_id: str, goal: str, org: str, resumed_from: str | None = None) -> dict:
        """C — open a goal record (status running). wip/done start empty (filled as work is committed)."""
        rec = {"goal_id": goal_id, "goal": goal, "org": org, "status": "running",
               "resumed_from": resumed_from or None, "wip": None, "done": None,
               "result": None, "delivery": None}
        self._write(goal_id, rec)
        return rec

    def load(self, goal_id: str) -> dict | None:
        """L — read one record (None if absent)."""
        p = self._path(goal_id)
        if not p.is_file():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def load_all(self) -> dict[str, dict]:
        """L — every record, keyed by goal_id (used to rebuild the in-memory index on startup)."""
        out: dict[str, dict] = {}
        if not self.root.is_dir():
            return out
        for p in sorted(self.root.glob("*.json")):
            try:
                rec = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(rec, dict) and rec.get("goal_id"):
                    out[rec["goal_id"]] = rec
            except (json.JSONDecodeError, OSError):
                continue
        return out

    def update(self, goal_id: str, **fields) -> dict:
        """U — merge fields into a record (read-modify-write under a lock; atomic temp+rename)."""
        with self._lock:
            rec = self.load(goal_id) or {"goal_id": goal_id}
            rec.update(fields)
            self._write(goal_id, rec)
            return rec

    def delete(self, goal_id: str) -> None:
        """D — drop the record and both git refs."""
        with self._lock:
            self._path(goal_id).unlink(missing_ok=True)
        for kind in ("wip", "done"):
            _git(self.repo, "update-ref", "-d", self._ref(goal_id, kind))

    # --- git-backed work fields (the record's wip/done point here) --------------------------------
    def commit_wip(self, goal_id: str, work: str) -> str | None:
        return self._commit_work(goal_id, work, "wip")

    def commit_done(self, goal_id: str, work: str) -> str | None:
        return self._commit_work(goal_id, work, "done")

    def restore_wip(self, goal_id: str, work: str) -> bool:
        """Resume: replay the in-progress work into a fresh worktree. `wip` is the TIP of a chain of
        per-leaf commits (one commit per converged leaf), so we cherry-pick the whole RANGE base..wip —
        base being the fork point (merge-base of wip and this fresh worktree's HEAD). Accepts a goal_id or
        a raw SHA/ref. Returns True if work was restored."""
        sha = self._resolve(goal_id, "wip")
        if not sha:
            return False
        base = _git(work, "merge-base", sha, "HEAD").stdout.strip()
        if not base or base == sha:                             # wip not ahead of this worktree's base
            return False
        rng = f"{base}..{sha}"
        cp = _git(work, "cherry-pick", "-n", rng)
        if cp.returncode != 0:                                  # conflict/drift — fall back to a tree diff
            _git(work, "cherry-pick", "--abort")
            patch = _git(work, "diff", base, sha).stdout
            if not patch.strip():
                return False
            ap = subprocess.run(["git", "-C", str(work), "apply", "--whitespace=nowarn"],
                                input=patch, text=True, capture_output=True)
            return ap.returncode == 0
        return True

    # --- internals --------------------------------------------------------------------------------
    def _commit_work(self, goal_id: str, work: str, kind: str) -> str | None:
        """Commit the worktree's accumulated work to a dangling commit and pin it under refs/goals/<id>/
        <kind>; record the SHA in the `wip`/`done` field. Excludes result.json (.agent-runs is gitignored).
        Returns the SHA (the tip), or None on failure. The goal worktree already holds one commit per
        converged leaf, so we fold any uncommitted REMAINDER into a commit and then record HEAD — the work
        IS the tip of the leaf-commit chain, not a single squash."""
        _git(work, "add", "-A", "--", ".", ":(exclude)result.json")
        if _git(work, "diff", "--cached", "--quiet").returncode != 0:   # an uncommitted remainder exists
            if _git(work, "commit", "-q", "-m", f"{kind}:{goal_id}").returncode != 0:
                return None
        sha = _git(work, "rev-parse", "HEAD").stdout.strip()
        if not sha:
            return None
        _git(work, "update-ref", self._ref(goal_id, kind), sha)        # shared ref store -> durable
        self.update(goal_id, **{kind: sha})
        return sha

    def _resolve(self, goal_id_or_sha: str, kind: str) -> str | None:
        rec = self.load(goal_id_or_sha)
        if rec and rec.get(kind):
            return rec[kind]
        for cand in (self._ref(goal_id_or_sha, kind), goal_id_or_sha):
            r = _git(self.repo, "rev-parse", "--verify", "--quiet", cand)
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip()
        return None

    def _write(self, goal_id: str, rec: dict) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self._path(goal_id).with_suffix(".json.tmp")
        tmp.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._path(goal_id))                               # atomic


def self_test() -> int:
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        repo = Path(d) / "repo"; repo.mkdir()
        _git(repo, "init", "-q", "-b", "main"); _git(repo, "config", "user.email", "t@t")
        _git(repo, "config", "user.name", "t")
        (repo / "a.txt").write_text("base\n"); _git(repo, "add", "-A"); _git(repo, "commit", "-q", "-m", "base")
        st = GoalStore(str(repo))
        st.create("goal-x", "build it", "codex")
        assert st.load("goal-x")["status"] == "running"
        # partial work in a goal worktree -> commit_wip
        w1 = Path(d) / "w1"; _git(repo, "worktree", "add", "-q", "--detach", str(w1), "HEAD")
        (w1 / "new.py").write_text("partial\n"); (w1 / "a.txt").write_text("base+edit\n")
        (w1 / "result.json").write_text("{}\n")                        # must be excluded
        sha = st.commit_wip("goal-x", str(w1))
        assert sha and st.load("goal-x")["wip"] == sha, "wip field holds the commit sha"
        _git(repo, "worktree", "remove", "--force", str(w1))
        st.update("goal-x", status="failed")
        # resume: fresh worktree, restore_wip
        w2 = Path(d) / "w2"; _git(repo, "worktree", "add", "-q", "--detach", str(w2), "HEAD")
        assert st.restore_wip("goal-x", str(w2)) is True
        assert (w2 / "new.py").is_file() and "edit" in (w2 / "a.txt").read_text(), "wip restored via git"
        assert not (w2 / "result.json").exists(), "result.json was excluded from wip"
        # load_all + delete
        assert "goal-x" in st.load_all()
        st.delete("goal-x")
        assert st.load("goal-x") is None and _git(repo, "rev-parse", "--verify", "--quiet",
                                                  "refs/goals/goal-x/wip").returncode != 0
        print("goal_store self-test passed (CLUD + git-backed wip restore, result.json excluded).")
    return 0


if __name__ == "__main__":
    raise SystemExit(self_test())
