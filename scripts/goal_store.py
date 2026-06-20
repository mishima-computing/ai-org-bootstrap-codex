#!/usr/bin/env python3
"""Durable, method-encapsulated state OWNED BY THE AI ORG — its goals' state is the org's, not
another's. A consumer only READS this via the same record/refs; it never writes them.

The AI Org holds state once goals can be resumed. This is that state, behind a CLRUD method surface
(Create / Load / Read / Update / Delete — Load OPERATES on state, Read only observes) so callers never touch the backing store directly — the backend can
become sqlite later without changing a single caller.

Layout (all under <repo>/.agent-runs/goals/):
  - <goal_id>.json            one record per goal: status, prompt, org, resumed_from, and the two work
                              fields `wip` (in-progress) and `done` (completed) — each a git commit SHA.
  - refs/goals/<id>/wip,done  the actual work, held IN GIT (a commit off the goal's base), not a loose
                              patch. Resume "calls it with git": Load cherry-picks the wip commit range
                              into a fresh worktree. Refs live in the repo's object store, so they survive
                              worktree cleanup AND consumer restarts.

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
    def __init__(self, repo: str, emit=None):
        self.repo = str(repo)
        self.root = Path(repo).expanduser().resolve() / ".agent-runs" / "goals"
        self._lock = threading.Lock()
        # every OPERATION on state is also flowed to the log (the store is the current-state authority; the
        # log is the audit/observability of what was DONE to it — incl. Load). `emit` is the consumer's Stream.
        self._emit = emit if callable(emit) else (lambda e: None)

    # --- paths / refs -----------------------------------------------------------------------------
    def _path(self, goal_id: str) -> Path:
        return self.root / f"{goal_id}.json"

    @staticmethod
    def _ref(goal_id: str, kind: str) -> str:
        return f"refs/goals/{goal_id}/{kind}"

    def _steer_path(self, goal_id: str) -> Path:
        return self.root / f"{goal_id}.steering.jsonl"

    # --- CLRUD: Create / Load / Read / Update / Delete --------------------------------------------
    # Load and Read are DISTINCT: Load *operates* — it makes the target BECOME the stored state (sets git
    # to the goal's committed version). Read is the SAFE check that observes the record and mutates nothing.
    def create(self, goal_id: str, goal: str, org: str, resumed_from: str | None = None) -> dict:
        """C — open a goal record (status running). wip/done start empty (filled as work is committed)."""
        rec = {"goal_id": goal_id, "goal": goal, "org": org, "status": "running",
               "resumed_from": resumed_from or None, "wip": None, "done": None,
               "result": None, "delivery": None}
        self._write(goal_id, rec)
        self._emit({"type": "state", "op": "create", "goal_id": goal_id, "status": "running"})
        return rec

    def read(self, goal_id: str) -> dict | None:
        """Read — observe one record (None if absent). Returns the data; mutates nothing."""
        p = self._path(goal_id)
        if not p.is_file():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def read_all(self) -> dict[str, dict]:
        """Read — every record, keyed by goal_id (used to rebuild the in-memory index on startup)."""
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

    def find(self, **criteria) -> list[dict]:
        """Read (1:N) — every record matching ALL criteria, e.g. find(status="failed"), find(wip=sha). The
        flexible lookup: state is resolvable from various ids/fields, and an id may map to many (1:N)."""
        return [r for r in self.read_all().values()
                if all(r.get(k) == v for k, v in criteria.items())]

    def update(self, goal_id: str, **fields) -> dict:
        """U — merge fields into a record (read-modify-write under a lock; atomic temp+rename)."""
        with self._lock:
            rec = self.read(goal_id) or {"goal_id": goal_id}
            rec.update(fields)
            self._write(goal_id, rec)
        self._emit({"type": "state", "op": "update", "goal_id": goal_id, **fields})
        return rec

    def record_session(self, goal_id: str, leaf_id: str, role: str, session_id: str) -> dict | None:
        """U-like OPERATION (audit) — record a role's codex SESSION id under the goal's `sessions` map,
        keyed by `f"{leaf_id}:{role}"`, and flow it to the log. This is observability for the REPAIR
        session-reuse (which producer/implementer session a repair iteration resumed); the functional reuse
        is the in-memory `sessions` map in controller_pipeline's repair loop, not this record. Returns the
        merged record, or None for an empty session_id / unknown goal."""
        if not session_id or self.read(goal_id) is None:
            return None
        key = f"{leaf_id}:{role}"
        with self._lock:
            rec = self.read(goal_id) or {"goal_id": goal_id}
            sessions = dict(rec.get("sessions") or {})
            sessions[key] = session_id
            rec["sessions"] = sessions
            self._write(goal_id, rec)
        self._emit({"type": "state", "op": "record_session", "goal_id": goal_id,
                    "leaf_id": leaf_id, "role": role, "session_id": session_id})
        return rec

    def delete(self, goal_id: str) -> None:
        """D — drop the record, its steering sidecar, and both git refs."""
        with self._lock:
            self._path(goal_id).unlink(missing_ok=True)
            self._steer_path(goal_id).unlink(missing_ok=True)
        for kind in ("wip", "done"):
            _git(self.repo, "update-ref", "-d", self._ref(goal_id, kind))
        self._emit({"type": "state", "op": "delete", "goal_id": goal_id})

    # --- steering: additive mid-run guidance (steer a running goal WITHOUT kill + re-fire) ---------
    # Steering lives in an APPEND-ONLY sidecar (<id>.steering.jsonl), NOT the record. A consumer appends notes
    # here while the org keeps writing the record; append-only + a separate file means the two PROCESSES
    # never clobber each other (the record stays the org's, steering is consumer-ingress / org-read-only). The
    # org folds the notes into its not-yet-dispatched leaves at the next boundary — no work is discarded.
    def steer(self, goal_id: str, text: str, target: str = "goal") -> dict | None:
        """U-like OPERATION — append one steering note to a running goal; flowed to the log. `target` says
        WHERE in the Queue it applies: a NODE id (a leaf/branch in the split tree — folded into that node
        and its whole subtree) or "goal" (every leaf). Goal-level alone is the degenerate case (the whole
        Queue); NODE-targeting is the point. Returns the entry (1-based `seq`) or None for empty/unknown."""
        text = (text or "").strip()
        if not text or self.read(goal_id) is None:
            return None
        target = (target or "goal").strip() or "goal"
        self.root.mkdir(parents=True, exist_ok=True)
        with self._steer_path(goal_id).open("a", encoding="utf-8") as f:
            f.write(json.dumps({"text": text, "target": target}, ensure_ascii=False) + "\n")  # O_APPEND atomic
        seq = self._steer_count(goal_id)
        self._emit({"type": "state", "op": "steer", "goal_id": goal_id, "seq": seq,
                    "target": target, "text": text})
        return {"seq": seq, "text": text, "target": target}

    def read_steering(self, goal_id: str, since: int = 0) -> list[dict]:
        """Read (safe) — the goal's steering notes with seq > `since` (1-based line order); since=0 returns
        all. Lets a consumer apply only what is NEW since it last looked. Mutates nothing."""
        p = self._steer_path(goal_id)
        if not p.is_file():
            return []
        out: list[dict] = []
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), start=1):
            line = line.strip()
            if not line or i <= since:
                continue
            try:
                rec = json.loads(line)
                out.append({"seq": i, "text": rec.get("text", ""), "target": rec.get("target", "goal")})
            except json.JSONDecodeError:
                continue
        return out

    def _steer_count(self, goal_id: str) -> int:
        p = self._steer_path(goal_id)
        return sum(1 for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()) if p.is_file() else 0

    # --- git-backed work fields (the record's wip/done point here) --------------------------------
    def save_wip(self, goal_id: str, work: str) -> str | None:    # SAVE the current build as wip (<-> load)
        return self._commit_work(goal_id, work, "wip")

    def save_done(self, goal_id: str, work: str) -> str | None:   # SAVE the delivered build as done
        return self._commit_work(goal_id, work, "done")

    def load(self, goal_id: str, work: str | None = None) -> bool:
        """L of CLRUD — Load(id) makes the target BECOME that goal's state. `goal_id` IDENTIFIES which
        state; `work` is the target to load it into (defaults to the store's repo). This is an OPERATION,
        not a read — it sets the target's git to the goal's committed version (`wip`). `wip` is the TIP of
        a chain of per-leaf commits, so cherry-pick the whole RANGE base..wip — base being the fork point
        (merge-base of wip and the target's HEAD). Accepts a goal_id or a raw SHA/ref. Returns True if the
        state was loaded."""
        work = work or self.repo
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
            if ap.returncode == 0:
                self._emit({"type": "state", "op": "load", "goal_id": goal_id, "wip": sha})
            return ap.returncode == 0
        self._emit({"type": "state", "op": "load", "goal_id": goal_id, "wip": sha})
        return True

    def restored_files(self, goal_id: str, work: str | None = None) -> list:
        """The repo-relative files a Load(goal_id) brings back — the wip range `base..wip`. A resuming run
        feeds this to its splitter so the (intentionally fresh, frontier-non-restored) re-split is IDEMPOTENT
        against the restored work: build on / patch these, do not recreate them under new names. [] when there
        is no wip or no fork point."""
        work = work or self.repo
        sha = self._resolve(goal_id, "wip")
        if not sha:
            return []
        base = _git(work, "merge-base", sha, "HEAD").stdout.strip()
        if not base or base == sha:
            return []
        out = _git(work, "diff", "--name-only", f"{base}..{sha}").stdout
        return [f for f in out.splitlines() if f.strip() and not f.startswith(".agent-runs/")]   # not scratch

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
        self._emit({"type": "state", "op": "save", "goal_id": goal_id, "kind": kind, "sha": sha})
        return sha

    def _resolve(self, goal_id_or_sha: str, kind: str) -> str | None:
        rec = self.read(goal_id_or_sha)
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
        assert st.read("goal-x")["status"] == "running"
        # partial work in a goal worktree -> commit_wip
        w1 = Path(d) / "w1"; _git(repo, "worktree", "add", "-q", "--detach", str(w1), "HEAD")
        (w1 / "new.py").write_text("partial\n"); (w1 / "a.txt").write_text("base+edit\n")
        (w1 / "result.json").write_text("{}\n")                        # must be excluded
        sha = st.save_wip("goal-x", str(w1))
        assert sha and st.read("goal-x")["wip"] == sha, "wip field holds the commit sha"
        _git(repo, "worktree", "remove", "--force", str(w1))
        st.update("goal-x", status="failed")
        # Load: make a fresh worktree BECOME the goal's wip state
        w2 = Path(d) / "w2"; _git(repo, "worktree", "add", "-q", "--detach", str(w2), "HEAD")
        assert st.load("goal-x", str(w2)) is True
        assert (w2 / "new.py").is_file() and "edit" in (w2 / "a.txt").read_text(), "wip restored via git"
        assert not (w2 / "result.json").exists(), "result.json was excluded from wip"
        # steering: notes append to the sidecar; read_steering returns them; since= filters; target routes
        assert st.steer("goal-x", "prefer official tools") == {"seq": 1, "text": "prefer official tools",
                                                               "target": "goal"}
        assert st.steer("goal-x", "  ") is None and st.steer("nope", "x") is None, "empty/unknown -> None"
        st.steer("goal-x", "rework the parser", target="leaf-7")     # NODE-targeted (a Queue node)
        assert [(n["text"], n["target"]) for n in st.read_steering("goal-x")] == [
            ("prefer official tools", "goal"), ("rework the parser", "leaf-7")]
        assert st.read_steering("goal-x", since=1) == [
            {"seq": 2, "text": "rework the parser", "target": "leaf-7"}], "since= filters"
        # record_session: a role's codex session id lands under the goal's `sessions` map (audit-only) and
        # is flowed to the log; empty/unknown -> None.
        events = []
        st2 = GoalStore(str(repo), emit=events.append)
        assert st2.record_session("goal-x", "leaf-3", "implementer", "sess-42") is not None
        assert st2.read("goal-x")["sessions"] == {"leaf-3:implementer": "sess-42"}, "session recorded by key"
        assert st2.record_session("goal-x", "leaf-3", "genius", "") is None, "empty session_id -> None"
        assert st2.record_session("nope", "leaf-3", "genius", "s") is None, "unknown goal -> None"
        assert any(e.get("op") == "record_session" for e in events), "record_session is logged"
        # load_all + delete
        assert "goal-x" in st.read_all()
        st.delete("goal-x")
        assert st.read("goal-x") is None and not st._steer_path("goal-x").is_file() and _git(
            repo, "rev-parse", "--verify", "--quiet", "refs/goals/goal-x/wip").returncode != 0
        print("goal_store self-test passed (CLUD + git-backed wip restore + steering sidecar).")
    return 0


if __name__ == "__main__":
    raise SystemExit(self_test())
