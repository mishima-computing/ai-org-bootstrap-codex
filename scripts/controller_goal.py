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
import scaffold_primitive  # noqa: E402 — ADR-0008 deterministic, LLM-free scaffold skeleton
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
    import datetime
    import json
    import os
    log = Path(os.environ.get("STREAM_LOG") or (Path(repo) / ".agent-runs" / "stream.jsonl"))

    def emit(event):
        try:
            log.parent.mkdir(parents=True, exist_ok=True)
            # stamp EVERY event with a ts (the pipeline already does for stage events) so a consumer can
            # judge liveness/recency from the stream ALONE — the freshest event's ts, not an off-band
            # process poll. A poor, ts-less goal log was a time bomb: leaf_start/leaf_done could not be
            # told fresh from stale, so "is this goal still moving?" leaked to fragile `pgrep`.
            ts = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
            with log.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"ts": ts, **dict(event)}, ensure_ascii=False) + "\n")   # event ts wins if present
        except OSError:
            pass

    return emit


_SPEECH_CAP = 16000   # max serialized chars of the splitter's decomposition that rides the stream verbatim


def _emit_splitter_speech(emit, run_id, plan) -> None:
    """Stream the splitter's actual output — the task DAG it produced — as an `agent_message` event, the same
    shape the pipeline uses for designer/implementer/linon speech. goal_split/scaffold_fanout carry only a
    COUNT; the decomposition itself is what a host (Shagiri) needs to show "what the splitter said", and the
    stream is its only durable home (the carrier log lives in an ephemeral leaf worktree). Bound, legibly."""
    import json as _json
    try:
        s = _json.dumps(plan, ensure_ascii=False)
        speech = plan if len(s) <= _SPEECH_CAP else {"_truncated": True, "_chars": len(s), "_preview": s[:_SPEECH_CAP]}
    except Exception:                                          # noqa: BLE001
        speech = {"_preview": str(plan)[:_SPEECH_CAP]}
    emit({"type": "agent_message", "source": "splitter", "run_id": run_id, "speech": speech})


def codex_carrier(repo, *, model=None, resume_session=None):
    """The real split carrier: run a read-only codex carrier that emits the child-DAG JSON to an output
    file, and return it (fail-soft '[]' on any error, so split() yields no children rather than crash).
    The carrier_harness import is lazy so tests that inject their own split never touch it.

    `resume_session` RESUMES the splitter's prior codex session — used when a goal is RESUMED, so the
    re-split is a CONTINUATION of the planning conversation that decomposed the goal the first time: the
    splitter keeps the MEMORY of its prior decomposition (and the file names it chose), so the fresh
    re-split (frontier is intentionally not restored) adapts without amnesiac duplication. The session id
    the carrier observed is exposed on `carrier.captured["session_id"]` for the caller to record in state."""
    captured: dict = {}

    def carrier(prompt):
        import carrier_harness
        out = Path(tempfile.mkdtemp(prefix="split-")) / "tasks.json"
        try:
            result = carrier_harness.run_carrier(repo, prompt, sandbox="read-only",
                                                 output_file=str(out), model=model, retries=1,
                                                 resume_session=resume_session)
            captured["session_id"] = result.get("session_id")   # the splitter's session (record for a later RESUME)
            if result.get("ok") and out.is_file():
                return out.read_text(encoding="utf-8")
        except Exception:                                      # noqa: BLE001 - a split failure is just no children
            pass
        finally:
            shutil.rmtree(out.parent, ignore_errors=True)
        return "[]"

    carrier.captured = captured
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
        return {"outcome": "converged", "commit": sha or None, "sessions": result.get("sessions") or {}}
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


# How far past the floor the org may SELF-STEER a leaf, by the SEVERITY of the findings blocking it — a
# critical finding is worth pushing a finer decomposition on, a cosmetic one is not. Budget follows the
# INFORMATION's importance (ADR-0008 addendum). 0 for low-severity / no findings.
_SELF_STEER_CAP = {"critical": 2, "blocker": 2, "high": 1, "major": 1}


def _self_steer_cap(findings) -> int:
    """The deterministic, severity-weighted COUNTER that bounds self-steer (ADR-0008: a count, never an
    LLM-content / findings-hash guard). Max over the findings' severities; 0 when none qualify."""
    caps = [_SELF_STEER_CAP.get(str((f or {}).get("severity", "")).lower(), 0)
            for f in (findings or []) if isinstance(f, dict)]
    return max(caps) if caps else 0


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


def _ancestry(plan, leaf_id, path=()):
    """The id set on the path from a root down to leaf_id, INCLUSIVE — so a steer TARGETED at an ancestor
    (a branch / internal Queue node) reaches the whole subtree under it. Empty if leaf_id is not in the
    tree (a freshly-split child whose id isn't placed yet falls back to {its own id})."""
    for t in plan:
        here = path + (t.get("id"),)
        if t.get("id") == leaf_id:
            return set(here)
        if t.get("children"):
            r = _ancestry(t["children"], leaf_id, here)
            if r:
                return r
    return set()


def _apply_steering(store, goal_id, leaf, plan):
    """Fold STEERING into THIS leaf's objective at dispatch — WITHOUT a kill + re-fire. A note applies when
    its target is "goal" (every leaf — the degenerate whole-Queue case) OR a node on this leaf's ancestry
    path (the leaf itself, or a BRANCH above it so a branch-targeted steer reaches its whole subtree).
    Node-targeting is the point: goal-level alone is just the Queue. Returns a COPY (never mutates the
    plan / the split source), or the leaf UNCHANGED when nothing applies. Standing guidance — re-evaluated
    for each new leaf at its own dispatch (re-split children inherit a branch's steer)."""
    if store is None or not goal_id:
        return leaf
    notes = store.read_steering(goal_id)
    if not notes:
        return leaf
    reach = _ancestry(plan, leaf.get("id")) or {leaf.get("id")}
    notes = [n for n in notes if n.get("target", "goal") == "goal" or n.get("target") in reach]
    if not notes:
        return leaf
    block = "\n".join(f"- {n['text']}" for n in notes)
    steered = dict(leaf)
    steered["objective"] = ((leaf.get("objective") or "")
                            + "\n\n[STEERING added mid-run — additional guidance you MUST follow]:\n" + block)
    return steered


def _failure_sig(res):
    """A signature of a mechanical failure: the sha256 of its preserved work (the diff). When two
    consecutive resumes preserve the SAME diff, the leaf is making NO PROGRESS — a blind retry won't fix a
    deterministic failure (Reflexion / FeedbackEval), so the loop stops and lets the floor / re-split handle
    it instead of burning the budget. None when there is no diff to compare on."""
    if not isinstance(res, dict):
        return None
    d = res.get("diff")
    try:
        if d and Path(d).is_file():
            import hashlib
            return hashlib.sha256(Path(d).read_bytes()).hexdigest()
    except Exception:                                          # noqa: BLE001 — signatures are best-effort
        pass
    return None


def _maybe_seed_scaffold(repo, leaf, emit, goal_text=None):
    """Before a GREENFIELD leaf that a trusted template fits, deterministically seed its skeleton into the
    goal repo and COMMIT it (ADR-0008) — acceptance-gated (build/import/smoke), NO LLM, NO Linon (there is
    no real logic to verify yet). The skeleton is the foundation, never the deliverable; the caller then
    FANS OUT the logic on it. No-op (returns None) when no template matches or the target dir already
    exists. Returns the seed `{base, template, files, acceptance_ok}` on success. Fail-soft.

    `goal_text` is the top GOAL string: it carries the declared target directory ("NEW directory X/ ONLY"),
    which is authoritative over a verb-first leaf objective — without it a leaf like "Create the core …"
    scaffolds into `create/` instead of the goal's real `engagement/`/`mocks/`."""
    if leaf.get("_scaffolded"):           # already inside a scaffolded subtree -> build on the seed, never re-seed
        return None
    try:
        obj = leaf.get("objective", "") or ""
        scope = leaf.get("scope") or []
        tid = scaffold_primitive.match_template(obj, scope)
        if tid is None:
            return None
        base = scaffold_primitive._scope_base(obj, scope, goal_text)
        if not base or (Path(repo) / base).exists():       # only GREENFIELD; an existing dir is patched
            return None
        files = scaffold_primitive.instantiate(tid, repo, base)
        if not files:
            return None
        gate = scaffold_primitive.acceptance(tid, repo, base)
        specs = [f":(literal){f}" for f in files]
        git_ops.ensure_identity(repo)
        subprocess.run(["git", "-C", str(repo), "add", "--", *specs], capture_output=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m",
                        f"scaffold: {base} (deterministic skeleton, ADR-0008)", "--", *specs],
                       capture_output=True)
        emit({"type": "scaffold_seeded", "id": leaf.get("id"), "template": tid, "base": base,
              "acceptance_ok": gate.get("ok"), "files": len(files)})
        return {"base": base, "template": tid, "files": files, "acceptance_ok": gate.get("ok")}
    except Exception:                                       # noqa: BLE001 — seeding is best-effort
        return None


def _scaffold_logic_objective(leaf, seeded) -> str:
    """The objective for fanning out a scaffolded leaf's LOGIC: a deterministic skeleton is already in
    place, so DECOMPOSE and build the REAL implementation ON it, scoped INSIDE the skeleton's directory. A
    skeleton-only result is rejected — the node is done only when its logic children are."""
    base = seeded.get("base")
    files = ", ".join(seeded.get("files") or [])
    return ((leaf.get("objective") or "")
            + f"\n\n[A deterministic skeleton ALREADY exists at `{base}/` (files: {files}). Build the REAL "
              f"implementation ON it. EVERY sub-task's scope MUST be a path under `{base}/` — do NOT invent "
              f"any other directory. Split by the modules / files the work needs WITHIN `{base}/`. Do not "
              f"re-create the skeleton; a skeleton-only result is rejected.]")


def _scope_under_base(task, base) -> bool:
    """True only when EVERY scope path of a fan-out child sits under the scaffold base — rejects a splitter
    that drifted to invented directories (implement/, replace/) instead of building in the skeleton."""
    base = str(base or "").strip().strip("/")
    scope = task.get("scope") or []
    if not base or not scope:
        return False
    return all((lambda p: p == base or p.startswith(base + "/"))(str(s).strip().strip("/")) for s in scope)


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
            # resume re-SPLITS fresh (frontier is intentionally NOT restored — a fresh split adapts to a
            # changed goal/codebase/steer and drops a bad plan). Its one cost is the LLM recreating already-
            # built work under new names; tell the splitter what the Load brought back so the re-split is
            # IDEMPOTENT against it (build on / patch these, do not recreate). The frontier stays non-restored.
            restored = store.restored_files(resume_from, repo)
            if restored:
                context = {**(context or {}), "resumed_prior_work": {
                    "files": restored[:200],
                    "instruction": "These files ALREADY EXIST, cherry-picked from resumed prior work. Build "
                                   "the goal ON them: extend or patch the existing files; do NOT recreate "
                                   "equivalent content under new names. Plan only the remaining work."}}
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

    # #48: the goal's DECLARED deliverable boundary ("inside X/ ONLY") steers the splitter to scope the plan
    # under X/ from the start — the prose path had no such steer and drifted to docs/. Confinement is
    # orthogonal to infra roles' lanes (Model A); a CI-writer's .github is handled by the cross-lane revert.
    boundary = scaffold_primitive._declared_dir(goal)
    if boundary:
        context = {**(context or {}), "scope_boundary": {
            "dir": boundary,
            "instruction": f"Every task's scope MUST be a path under `{boundary}/`. Place no deliverable "
                           f"file outside it and do not invent a sibling directory."}}

    if carrier is None and split is splitter.split:    # real run: decompose via codex (tests inject split)
        carrier = codex_carrier(repo)                  # fresh — used for the in-run re-splits / fan-out
    # TOP split: on RESUME, continue the PRIOR goal's splitter session so the splitter keeps the memory of
    # its original decomposition (the file names it chose) and the fresh re-split does not duplicate it. The
    # frontier stays non-restored; only the planning conversation is continued. Re-splits stay fresh.
    top_carrier = carrier
    if split is splitter.split and resume_from and store is not None:
        prior_sid = ((store.read(resume_from) or {}).get("sessions") or {}).get("_goal:splitter")
        if prior_sid:
            top_carrier = codex_carrier(repo, resume_session=prior_sid)
    plan = split(goal, context or {}, top_carrier)
    if store is not None and getattr(top_carrier, "captured", None):   # record the splitter session for a later RESUME
        sid = top_carrier.captured.get("session_id")
        if sid:
            store.record_session(goal_id, "_goal", "splitter", sid)
    errs = frontier.validate_plan(plan)
    if errs:
        emit({"type": "split_invalid", "goal": goal, "errors": errs})
        return _finalize(plan)
    emit({"type": "goal_split", "goal": goal, "n": len(plan)})
    _emit_splitter_speech(emit, goal_id, plan)

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
            emit({"type": "leaf_start", "id": leaf["id"], "goal_id": goal_id})   # goal_id attributes the node
            # fold any ADDITIVE STEERING (mid-run guidance) into THIS leaf at dispatch — no kill+re-fire.
            # exec_leaf carries the steered objective; the ORIGINAL leaf stays the plan/split source.
            exec_leaf = _apply_steering(store, goal_id, leaf, plan)
            if exec_leaf is not leaf:
                emit({"type": "steer_applied", "id": leaf["id"], "goal_id": goal_id})
            # ADR-0008 Phase 2: a GREENFIELD scaffold leaf seeds a deterministic skeleton (acceptance-gated,
            # NO Linon — nothing to adversarially verify yet), then FANS OUT its logic via the Queue: the
            # scaffold gives the seams to split along (walking-skeleton -> fan-out). The node is done only
            # when its logic children are, so a skeleton-only result is impossible and a heavy leaf no
            # longer dies atomically at the floor. Linon applies to the logic children, not the scaffold.
            seeded = _maybe_seed_scaffold(repo, exec_leaf, emit, goal)
            if seeded and split is not None and (_depth_of(plan, leaf["id"]) or 0) < FLOOR_MAX_DEPTH:
                base = seeded["base"]
                fan_ctx = {**(context or {}), "parent": leaf["id"], "scaffold_base": base}
                children = split(_scaffold_logic_objective(leaf, seeded), fan_ctx, carrier)
                children = [c for c in (children or []) if _scope_under_base(c, base)]   # G1: drop scope-drifters
                if children and not frontier.validate_plan(children):
                    for c in children:                          # G2: descendants build ON the seed, never re-scaffold
                        c["_scaffolded"] = True
                    plan = _set_children(plan, leaf["id"], children)
                    plan = frontier.advance(plan, leaf["id"], "pending")   # internal node: done when children are
                    emit({"type": "scaffold_fanout", "id": leaf["id"], "base": base, "n": len(children)})
                    _emit_splitter_speech(emit, leaf["id"], children)
                    continue
                # no in-base children -> build the logic atomically on the seed (fallback)
            outcome = run_leaf(repo, exec_leaf)
            res = outcome if isinstance(outcome, dict) else {"outcome": outcome}
            # RESUME: a non-quality (mechanical) failure — carrier timeout/hang, scope, malformed output —
            # is not a granularity problem, so retry the SAME leaf on its preserved work; do NOT re-split.
            tries = 0
            prev_sig = _failure_sig(res)
            while res.get("outcome") != "converged" and res.get("reason") == "mechanical" \
                    and tries < MECH_RETRY_CAP and not (budget is not None and spent >= budget):
                tries += 1
                spent += 1
                emit({"type": "leaf_resume", "id": leaf["id"], "attempt": tries})
                outcome = _call_leaf(run_leaf, repo, exec_leaf, res.get("diff"))
                res = outcome if isinstance(outcome, dict) else {"outcome": outcome}
                sig = _failure_sig(res)
                if res.get("outcome") != "converged" and sig is not None and sig == prev_sig:
                    emit({"type": "leaf_no_progress", "id": leaf["id"], "attempt": tries})
                    break        # same preserved work twice -> blind retry won't help; let floor/re-split run
                prev_sig = sig
            if res.get("outcome") == "converged":
                if store is not None:                          # AUDIT: record which codex session each role
                    for role, sid in (res.get("sessions") or {}).items():   # used on this leaf (repair reuse)
                        store.record_session(goal_id, leaf["id"], role, sid)
                plan = frontier.advance(plan, leaf["id"], "done")
                leaf_commits[leaf["id"]] = res.get("commit")   # this leaf's own commit (git scattered here)
                # rich log: carry the leaf's COMMIT sha (its build state), not just "it's done"
                emit({"type": "leaf_done", "id": leaf["id"], "commit": res.get("commit"), "goal_id": goal_id})
                continue
            depth = _depth_of(plan, leaf["id"]) or 0
            findings = res.get("findings")
            ss = leaf.get("_self_steer", 0)                  # self-steers already spent on this branch
            # at the floor with a severe finding and self-steer budget left, the org STEERS ITSELF: it floors
            # honestly UNLESS it can still push a finer decomposition that the (severity-weighted) counter
            # permits (ADR-0008 addendum — budget follows information; no human in the loop).
            if at_floor(leaf, depth) and not (findings and ss < _self_steer_cap(findings)):
                plan = frontier.advance(plan, leaf["id"], "failed")   # budget AND self-steer dry -> real floor
                emit({"type": "leaf_failed_floor", "id": leaf["id"], "depth": depth, "self_steers": ss})
                continue
            self_steering = at_floor(leaf, depth)            # past the floor only because self-steer permits it
            # a Linon rejection is a BAD REFERENCE: re-split, carrying its findings as retry CONTEXT so the
            # children do not repeat the rejected approach. A self-steer re-split additionally asks for a FINER
            # decomposition that resolves the findings (the org's own new information, earning a fresh budget).
            child_ctx = {**(context or {}), "parent": leaf["id"]}
            if findings:
                child_ctx["prior_rejected_findings"] = findings
            if self_steering:
                child_ctx["self_steer"] = {"round": ss + 1, "instruction":
                    "This node FLOORED on the findings above. Produce a FINER decomposition whose sub-tasks "
                    "each resolve a specific part of those findings — smaller and more targeted than before — "
                    "rather than repeating the rejected approach."}
            children = split(leaf["objective"], child_ctx, carrier)
            if not children or frontier.validate_plan(children):
                plan = frontier.advance(plan, leaf["id"], "failed")   # dry split (incl. a dry self-steer) -> floor
                emit({"type": ("leaf_failed_floor" if self_steering else "split_unusable"),
                      "id": leaf["id"], **({"depth": depth, "self_steers": ss} if self_steering else {})})
                continue
            if leaf.get("_scaffolded"):                  # re-split inside a scaffolded subtree stays scaffolded
                for c in children:
                    c["_scaffolded"] = True
            if self_steering:                            # carry the counter so the bound holds across rounds
                for c in children:
                    c["_self_steer"] = ss + 1
                emit({"type": "self_steer", "id": leaf["id"], "round": ss + 1, "n": len(children),
                      "depth": depth, "goal_id": goal_id})
            plan = _set_children(plan, leaf["id"], children)            # the leaf becomes an internal node
            plan = frontier.advance(plan, leaf["id"], "pending")
            emit({"type": "leaf_split", "id": leaf["id"], "n": len(children), "depth": depth, "goal_id": goal_id})
            _emit_splitter_speech(emit, leaf["id"], children)
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
