#!/usr/bin/env python3
"""controller_goal — the org's autonomous-builder entry (ADR-0008): a GOAL in, built parts out.

  goal -> split() -> a task DAG (a frontier plan) -> each ready LEAF runs the dialectic
  (controller_pipeline) -> on convergence the leaf is done; on repair-cap failure the leaf is SPLIT into
  children (recursion) UNLESS it is at the FLOOR (atomic scope / max depth). Termination is the floor +
  a budget, never a human (ADR-0008).

frontier.py owns the recursive task model (validate_plan / ready_tasks / advance / node_status);
splitter.py owns split() (goal -> child DAG via a carrier); this owns the loop that runs the leaves and
recurses on failure. The per-leaf runner is INJECTED (run_leaf) so this is testable without a carrier.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import os  # noqa: E402
import frontier  # noqa: E402
import git_ops  # noqa: E402 — the per-leaf-commit git-state procedures (guards live there, once)
import goal_store  # noqa: E402 — the ORG's own goal-state store (the org owns its state, not the host)
import splitter  # noqa: E402


def _shared_state_repo(repo) -> str:
    """The org's state STORE must be durable + shared so a host can read current state — NOT the ephemeral
    goal worktree. STREAM_LOG points at the shared `<repo>/.agent-runs/stream.jsonl`, so its grandparent is
    the shared repo. Falls back to `repo` (tests / no host)."""
    sl = os.environ.get("STREAM_LOG")
    if sl:
        p = Path(sl)
        if p.name == "stream.jsonl" and p.parent.name == ".agent-runs":
            return str(p.parent.parent)
    return str(repo)

FLOOR_MAX_DEPTH = 3
MECH_RETRY_CAP = 2     # a non-quality (mechanical) failure RESUMES the same leaf this many times


def stream_emit(repo):
    """Return an emit(event) that APPENDS a JSON line to the shared stream log (ADR-0009): one
    append-only log everything streams to, which consumers (the town, monitoring, the audit trail) tail.
    STREAM_LOG (env) points it at the SHARED log even when the build runs in an isolated worktree, so the
    town sees events live regardless of where the leaf executes. Fail-soft — observability never breaks a
    build."""
    import json
    import os
    log = Path(os.environ.get("STREAM_LOG") or (Path(repo) / ".agent-runs" / "stream.jsonl"))

    def emit(event):
        try:
            log.parent.mkdir(parents=True, exist_ok=True)
            with log.open("a", encoding="utf-8") as f:
                f.write(json.dumps(dict(event), ensure_ascii=False) + "\n")
        except OSError:
            pass

    return emit


def codex_carrier(repo, *, model=None):
    """The real split carrier: run a read-only codex carrier that emits the child-DAG JSON to an output
    file, and return it (fail-soft '[]' on any error, so split() yields no children rather than crash).
    The carrier_harness import is lazy so tests that inject their own split never touch it."""
    def carrier(prompt):
        import carrier_harness
        out = Path(tempfile.mkdtemp(prefix="split-")) / "tasks.json"
        try:
            result = carrier_harness.run_carrier(repo, prompt, sandbox="read-only",
                                                 output_file=str(out), model=model, retries=1)
            if result.get("ok") and out.is_file():
                return out.read_text(encoding="utf-8")
        except Exception:                                      # noqa: BLE001 - a split failure is just no children
            pass
        finally:
            shutil.rmtree(out.parent, ignore_errors=True)
        return "[]"

    return carrier


def _preserve_diff(repo, wt, task):
    """Save a non-Linon-failed leaf's partial work as a patch under `.agent-runs/resume/<id>.patch`, so a
    retry can RESUME on it instead of starting from scratch (the work was interrupted, not quality-rejected).
    Excludes scratch. Fail-soft -> None."""
    try:
        subprocess.run(["git", "-C", str(wt), "add", "-A"], capture_output=True)
        diff = subprocess.run(["git", "-C", str(wt), "diff", "--cached", "HEAD", "--",
                               ".", ":(exclude).agent-runs", ":(exclude)result.json"],
                              capture_output=True, text=True).stdout
        if not diff.strip():
            return None
        out = Path(repo) / ".agent-runs" / "resume" / (str(task.get("id")).replace("/", "_") + ".patch")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(diff, encoding="utf-8")
        return str(out)
    except Exception:                                          # noqa: BLE001 - preservation is best-effort
        return None


def _call_leaf(run_leaf, repo, task, resume_diff=None):
    """Call run_leaf, passing resume_diff only if it accepts it (test stubs take just (repo, task))."""
    try:
        return run_leaf(repo, task, resume_diff=resume_diff)
    except TypeError:
        return run_leaf(repo, task)


def default_run_leaf(repo, task, *, run_pipeline=None, resume_diff=None) -> dict:
    """Run ONE leaf's dialectic (controller_pipeline) in its OWN worktree off HEAD, so parallel leaves
    never collide on the shared repo (per-run isolation, ADR-0009). Returns
    {"outcome": "converged"|"failed", "reason": "linon"|"mechanical"|None, "findings", "diff"}:
      - converged -> the leaf's changed files merge back into the shared repo.
      - failed/"linon" -> Linon reviewed the diff and rejected it: a BAD REFERENCE. Its findings come back
        to carry as CONTEXT to a re-split (what was tried and rejected), never as a base to build on.
      - failed/"mechanical" -> it failed for a non-quality reason (carrier timeout/hang, scope, malformed
        output); the partial work is preserved (`diff`) so a retry can RESUME on it.
    `resume_diff` (a patch path) is applied to the fresh worktree before the run, to resume prior work.
    The worktree is always removed."""
    fail = lambda **k: {"outcome": "failed", **k}              # noqa: E731
    if run_pipeline is None:
        import controller_pipeline
        run_pipeline = controller_pipeline.run_pipeline
    if not (Path(repo) / ".git").exists():
        return fail()
    run_id = "goal-" + uuid.uuid4().hex[:10]
    # bridge the task (what the goal-layer leaf events carry) to the run_id (what the per-stage events
    # carry, as <run_id>-<role>), so a consumer can attribute each leaf's stage-marmots to its task.
    stream_emit(repo)({"type": "leaf_run", "task_id": task.get("id"), "run_id": run_id})
    wt = tempfile.mkdtemp(prefix=f"leaf-{task['id']}-")
    add = subprocess.run(["git", "-C", str(repo), "worktree", "add", "--detach", wt, "HEAD"],
                         capture_output=True, text=True)
    if add.returncode != 0:
        shutil.rmtree(wt, ignore_errors=True)
        return fail()
    if resume_diff and Path(resume_diff).is_file():            # RESUME prior (non-quality-rejected) work
        subprocess.run(["git", "-C", wt, "apply", "--whitespace=nowarn", str(resume_diff)],
                       capture_output=True)
    try:
        result = run_pipeline(wt, task["objective"], run_id)
        if not bool(result.get("converged")):
            if (result.get("linon_findings_count") or 0) > 0:  # Linon judged the diff -> a bad reference
                lin = (result.get("results") or {}).get("linon") or {}
                return fail(reason="linon",
                            findings=lin.get("findings") if isinstance(lin, dict) else None)
            return fail(reason="mechanical", diff=_preserve_diff(repo, wt, task))   # resume-able
        # merge the leaf's files into the goal worktree and commit them as ONE commit (the handoff to
        # dependent leaves). Every git-state guard — dir expansion, literal pathspecs, scratch exclusion,
        # identity, add/commit-failure rollback — lives ONCE in git_ops.merge_and_commit_leaf, not inline.
        sha = git_ops.merge_and_commit_leaf(repo, wt, task.get("id"), task.get("objective"))
        if sha is None:                                       # None = handoff FAILED (paths rolled back)
            return fail(reason="mechanical")                  # "" = nothing to commit (still converged)
        return {"outcome": "converged", "commit": sha or None}
    except Exception:                                          # noqa: BLE001 - a leaf crash is mechanical
        return fail(reason="mechanical")
    finally:
        subprocess.run(["git", "-C", str(repo), "worktree", "remove", "--force", wt], capture_output=True)
        shutil.rmtree(wt, ignore_errors=True)


def _declares_smallest(task: dict) -> bool:
    """True when a task is already the smallest meaningful unit and must NOT be split. Two ways in:
    (1) it DECLARES itself minimal/atomic (splitting 'minimal' into more 'minimal' is the infinite
        regression — atom → proton → quark → … — so honor the word: floor it the moment it appears);
    (2) it is structurally ANTI-decomposable — a scaffold / greenfield skeleton whose interdependent
        files (manifest, entry module, config) must all exist together and cannot be built one at a time.
    Such a task is built whole or fails; it is never split (that only yields more failing sub-units)."""
    text = ((task.get("id") or "") + " " + (task.get("objective") or "")).lower()
    return any(k in text for k in (
        "minimal", "smallest", "atomic", "indivisible",                 # self-declared smallest unit
        "scaffold", "materialize", "bootstrap the", "skeleton",         # anti-decomposable greenfield
        "set up the project", "create the project", "project structure"))


def at_floor(task: dict, depth: int) -> bool:
    """A node not worth splitting further: at max depth, atomic (<= 1 file in scope), or one that is
    already the smallest unit (self-declared minimal/atomic, or a scaffold — see _declares_smallest). The
    floor makes the recursion FINITE, so it always terminates without a human (ADR-0008)."""
    return depth >= FLOOR_MAX_DEPTH or len(task.get("scope") or []) <= 1 or _declares_smallest(task)


def _depth_of(tasks: list, task_id: str, depth: int = 0):
    for t in tasks:
        if t.get("id") == task_id:
            return depth
        if t.get("children"):
            d = _depth_of(t["children"], task_id, depth + 1)
            if d is not None:
                return d
    return None


def _set_children(tasks: list, task_id: str, children: list) -> list:
    """Return a NEW tree with task_id's children set (recursive search; never mutates the input)."""
    out = []
    for t in tasks:
        t = dict(t)
        if t.get("id") == task_id:
            t["children"] = children
        elif t.get("children"):
            t["children"] = _set_children(t["children"], task_id, children)
        out.append(t)
    return out


def run_goal(repo, goal, run_leaf=None, *, goal_id=None, resume_from=None, split=splitter.split,
             context=None, carrier=None, budget=None, emit=None) -> list:
    """Decompose `goal` and build it.

    run_leaf(repo, task) -> "converged" | "failed" runs one leaf's dialectic (defaults to
    default_run_leaf, which runs controller_pipeline in an isolated worktree; a stub in tests). budget
    caps the number of leaf runs (None = unbounded, bounded only by the floor). emit(event) streams
    progress (ADR-0009). When `goal_id` is given, the ORG OWNS this goal's state — the received goal
    becomes the org's at receipt: it records the goal, commits its build (wip) and its outcome, in its own
    GoalStore. A host (Shagiri) only READS that state. Returns the final task tree."""
    run_leaf = run_leaf or default_run_leaf
    emit = emit or stream_emit(repo)
    # the org's state STORE is durable + SHARED (so a host can READ current state, DB-style): write it where
    # STREAM_LOG points (the shared .agent-runs), not the ephemeral goal worktree. git refs are already
    # shared. `emit` is threaded in so every state OPERATION (create/load/save/update) also lands in the log.
    store = goal_store.GoalStore(_shared_state_repo(repo), emit=emit) if goal_id else None
    if store is not None:
        store.create(goal_id, goal, org="", resumed_from=resume_from)   # received goal is now the ORG's
        if resume_from and store.load(resume_from, repo):   # Load(prior id): the worktree BECOMES that state
            pass                                            # store.load already logs the op
    leaf_commits: dict = {}                             # leaf_id -> its own commit sha (git scatters per leaf)

    def _finalize(final_plan):
        done = all(frontier.node_status(t) == "done" for t in final_plan)
        status = "done" if done else "failed"
        wip = None
        if store is not None:                          # OPERATE the org's state: record its build + outcome
            wip = store.save_wip(goal_id, repo)
            # the state EXPRESSES the per-Queue git scattering: the Queue itself (the recursive split tree)
            # plus each leaf's OWN commit — git scatters one worktree/commit per leaf, not just the wip tip.
            store.update(goal_id, status=status, queue=final_plan, leaf_commits=leaf_commits)
        # rich log: the org flows its TERMINAL state (outcome + the wip commit) into its own Stream, so the
        # state is reconstructible from the log too — the log is the best resource for grasping state.
        emit({"type": "goal_finished", "status": status, "wip": wip})
        return final_plan

    if carrier is None and split is splitter.split:    # real run: decompose via codex (tests inject split)
        carrier = codex_carrier(repo)
    plan = split(goal, context or {}, carrier)
    errs = frontier.validate_plan(plan)
    if errs:
        emit({"type": "split_invalid", "goal": goal, "errors": errs})
        return _finalize(plan)
    emit({"type": "goal_split", "goal": goal, "n": len(plan)})

    spent = 0
    while True:
        ready = [t for t in frontier.ready_tasks(plan) if str(t.get("status") or "pending") == "pending"]
        if not ready:
            break
        for leaf in ready:
            if budget is not None and spent >= budget:
                emit({"type": "budget_exhausted", "spent": spent})
                return _finalize(plan)
            spent += 1
            plan = frontier.advance(plan, leaf["id"], "running")
            emit({"type": "leaf_start", "id": leaf["id"]})
            outcome = run_leaf(repo, leaf)
            res = outcome if isinstance(outcome, dict) else {"outcome": outcome}
            # RESUME: a non-quality (mechanical) failure — carrier timeout/hang, scope, malformed output —
            # is not a granularity problem, so retry the SAME leaf on its preserved work; do NOT re-split.
            tries = 0
            while res.get("outcome") != "converged" and res.get("reason") == "mechanical" \
                    and tries < MECH_RETRY_CAP and not (budget is not None and spent >= budget):
                tries += 1
                spent += 1
                emit({"type": "leaf_resume", "id": leaf["id"], "attempt": tries})
                outcome = _call_leaf(run_leaf, repo, leaf, res.get("diff"))
                res = outcome if isinstance(outcome, dict) else {"outcome": outcome}
            if res.get("outcome") == "converged":
                plan = frontier.advance(plan, leaf["id"], "done")
                leaf_commits[leaf["id"]] = res.get("commit")   # this leaf's own commit (git scattered here)
                # rich log: carry the leaf's COMMIT sha (its build state), not just "it's done"
                emit({"type": "leaf_done", "id": leaf["id"], "commit": res.get("commit")})
                continue
            depth = _depth_of(plan, leaf["id"]) or 0
            if at_floor(leaf, depth):                       # floor reached -> fail it, never split forever
                plan = frontier.advance(plan, leaf["id"], "failed")
                emit({"type": "leaf_failed_floor", "id": leaf["id"], "depth": depth})
                continue
            # a Linon rejection is a BAD REFERENCE: re-split, but carry its findings as retry CONTEXT (what
            # was tried and rejected) so the children do not repeat the rejected approach.
            child_ctx = {**(context or {}), "parent": leaf["id"]}
            if res.get("findings"):
                child_ctx["prior_rejected_findings"] = res["findings"]
            children = split(leaf["objective"], child_ctx, carrier)
            if not children or frontier.validate_plan(children):
                plan = frontier.advance(plan, leaf["id"], "failed")   # bad/empty split -> stop this branch
                emit({"type": "split_unusable", "id": leaf["id"]})
                continue
            plan = _set_children(plan, leaf["id"], children)            # the leaf becomes an internal node
            plan = frontier.advance(plan, leaf["id"], "pending")
            emit({"type": "leaf_split", "id": leaf["id"], "n": len(children), "depth": depth})
    emit({"type": "goal_done", "goal": goal})
    return _finalize(plan)


def main(argv=None) -> int:
    import argparse
    import json
    p = argparse.ArgumentParser(description="Run a GOAL through the org's autonomous builder (ADR-0008).")
    p.add_argument("--repo", required=True)
    p.add_argument("--goal", required=True)
    p.add_argument("--goal-id", default=None, help="the org records THIS goal's state under this id (it "
                   "owns its state); a host passes the id it dispatched with so it can read the org's state")
    p.add_argument("--resume-from", default=None, help="a prior goal_id (or sha/ref): the org LOADS that "
                   "state into the worktree before building, so it resumes its own prior work (org behavior)")
    p.add_argument("--budget", type=int, default=None, help="cap on total leaf runs (autonomous bound)")
    a = p.parse_args(argv)
    plan = run_goal(a.repo, a.goal, goal_id=a.goal_id, resume_from=a.resume_from, budget=a.budget)
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    return 0 if all(frontier.node_status(t) == "done" for t in plan) else 1


if __name__ == "__main__":
    raise SystemExit(main())
