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
import concurrent.futures
import inspect
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import os  # noqa: E402
import ask_search  # noqa: E402 — bounded search + provenance kernel for underdetermined asks
import conformance  # noqa: E402 — ADR-0016 D7 goal-level acceptance gate (reuses its service-boot helpers)
import controller_run  # noqa: E402 — org_root(repo): shared workspace-vs-engine install resolution
import frontier  # noqa: E402
import git_ops  # noqa: E402 — the per-leaf-commit git-state procedures (guards live there, once)
import goal_refiner  # noqa: E402 — ADR-0016 D1b intake sufficiency gate (raw goal -> candidate structured goal)
import goal_store  # noqa: E402 — the ORG's own goal-state store (the org owns its state, not the consumer)
import scaffold_primitive  # noqa: E402 — ADR-0008 deterministic, LLM-free scaffold skeleton
import splitter  # noqa: E402
import task_executor  # noqa: E402 — recursive TaskExecutor live path
from ai_org_bootstrap.registry import load_runtime_registry  # noqa: E402


def _env_enabled(name: str, *, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() not in ("", "0", "false", "no", "off")


def _use_taskexecutor() -> bool:
    """AI_ORG_USE_TASKEXECUTOR controls the goal-entry cutover. Default ON; off keeps the old frontier path."""
    return _env_enabled("AI_ORG_USE_TASKEXECUTOR", default=True)


def _shared_state_repo(repo) -> str:
    """The org's state STORE must be durable + shared so a consumer can read current state — NOT the ephemeral
    goal worktree. STREAM_LOG points at the shared `<repo>/.agent-runs/stream.jsonl`, so its grandparent is
    the shared repo. Falls back to `repo` (tests / no consumer)."""
    sl = os.environ.get("STREAM_LOG")
    if sl:
        p = Path(sl)
        if p.name == "stream.jsonl" and p.parent.name == ".agent-runs":
            return str(p.parent.parent)
    return str(repo)

FLOOR_MAX_DEPTH = 3
MECH_RETRY_CAP = 2     # a non-quality (mechanical) failure RESUMES the same leaf this many times
LINON_RESPLIT_CAP = 2  # Linon rejections may refine granularity only this many times per branch


class LaunchPreconditionError(RuntimeError):
    """A controller launch is misconfigured before any split/leaf work can validly start."""


def _precondition_message(cause: str) -> str:
    return ("AI Org precondition failed: "
            f"{cause}. Set AI_ORG_ROOT to the engine install (the cockpit's default_goal_runner does this); "
            "a self-hosted run needs registry/runtime-registry.yaml in --repo itself.")


def check_launch_preconditions(repo, emit=None) -> Path:
    """Verify the org runtime registry is present and non-empty before expensive split/leaf work starts.

    This uses the same org-root resolver and registry loader as the runtime path. Emitting is fail-soft, but
    the precondition failure itself always raises so a missing AI_ORG_ROOT cannot become a late leaf crash.
    """
    repo_path = Path(repo).resolve()
    org = controller_run.org_root(repo_path)
    registry_path = org / "registry" / "runtime-registry.yaml"
    cause = None
    entries = []
    if not registry_path.is_file():
        cause = f"runtime registry not found at {registry_path}"
    else:
        try:
            entries = load_runtime_registry(registry_path)
        except Exception as exc:                           # noqa: BLE001 - surface parser/path failures verbatim
            cause = f"runtime registry could not be loaded at {registry_path}: {type(exc).__name__}: {exc}"
        else:
            if len(entries) < 1:
                cause = f"runtime registry has no role entries at {registry_path}"
    if cause is None:
        return registry_path
    message = _precondition_message(cause)
    if callable(emit):
        try:
            emit({"type": "precondition_failed", "repo": str(repo_path), "org_root": str(org),
                  "runtime_registry": str(registry_path), "cause": cause, "message": message})
        except Exception:                                  # noqa: BLE001 - telemetry must not mask the loud raise
            pass
    raise LaunchPreconditionError(message)


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
                try:                                     # advisory cross-process guard on the shared log: cheap
                    import fcntl                          # insurance for exotic/NFS filesystems. POSIX-only -> skip
                    fcntl.flock(f.fileno(), fcntl.LOCK_EX)  # (unlocked) if fcntl is absent (Windows) or unsupported.
                except Exception:                        # A regular-file O_APPEND write is already atomic for any
                    pass                                 # size on Linux/macOS, so the lock is belt-and-suspenders.
                f.write(json.dumps({"ts": ts, **dict(event)}, ensure_ascii=False) + "\n")
        except Exception:                                # fail-soft: observability never breaks a build. (Widened from
            pass                                         # OSError so a malformed event drops silently, never raises.)

    return emit


_SPEECH_CAP = 16000   # max serialized chars of the splitter's decomposition that rides the stream verbatim


def _emit_splitter_speech(emit, run_id, plan) -> None:
    """Stream the splitter's actual output — the task DAG it produced — as an `agent_message` event, the same
    shape the pipeline uses for designer/implementer/linon speech. goal_split/scaffold_fanout carry only a
    COUNT; the decomposition itself is what a consumer needs to show "what the splitter said", and the
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


def _run_leaf_signature(run_leaf):
    try:
        return inspect.signature(run_leaf)
    except (TypeError, ValueError):
        return None


def _run_leaf_accepts_defer_merge(run_leaf) -> bool:
    sig = _run_leaf_signature(run_leaf)
    if sig is None:
        return False
    params = sig.parameters
    return "defer_merge" in params or any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())


def _call_leaf(run_leaf, repo, task, resume_diff=None, goal_context=None, *, defer_merge=False):
    """Call run_leaf, passing optional execution context only if it accepts it."""
    sig = _run_leaf_signature(run_leaf)
    if sig is not None:
        params = sig.parameters
        has_var_kw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())
        kwargs = {}
        if "resume_diff" in params or has_var_kw:
            kwargs["resume_diff"] = resume_diff
        if "goal_context" in params or has_var_kw:
            kwargs["goal_context"] = goal_context
        if defer_merge:
            if not ("defer_merge" in params or has_var_kw):
                raise TypeError("run_leaf does not accept defer_merge")
            kwargs["defer_merge"] = True
        return run_leaf(repo, task, **kwargs)
    if defer_merge:
        return run_leaf(repo, task, resume_diff=resume_diff, goal_context=goal_context, defer_merge=True)
    try:
        return run_leaf(repo, task, resume_diff=resume_diff, goal_context=goal_context)
    except TypeError:
        try:
            return run_leaf(repo, task, resume_diff=resume_diff)
        except TypeError:
            return run_leaf(repo, task)


def _max_parallel() -> int:
    try:
        max_parallel = int(os.environ.get("AI_ORG_MAX_PARALLEL", "4"))
    except ValueError:
        max_parallel = 4
    return max(1, max_parallel)


def _call_run_pipeline(run_pipeline, wt, objective, run_id, goal_context=None):
    """Call run_pipeline with goal_context AND design-wave parallelism when the implementation accepts them.

    The goal path historically called run_pipeline with the serial default (max_parallel=1), so per-leaf design
    waves ran the independent producers (genius/conservative/aggressive + the CI writers) one at a time even
    though the CLI defaults to 4. Those producers are read-only isolated runs whose edits merge back SERIALLY
    after the futures complete (the controller_parallel pattern proven by test_write_role_isolation), so running
    them concurrently is a tested-safe, waste-outside-the-LLM speedup — it does NOT touch the shared goal
    worktree's index/HEAD (that is the frontier-leaf parallelism, which needs a merge lock and is NOT enabled
    here). Matches the CLI default of 4; AI_ORG_MAX_PARALLEL overrides (set 1 to restore serial).
    """
    import inspect
    max_parallel = _max_parallel()
    try:
        sig = inspect.signature(run_pipeline)
        params = sig.parameters
        has_var_kw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())
        kwargs = {}
        if "goal_context" in params or has_var_kw:
            kwargs["goal_context"] = goal_context
        if "max_parallel" in params or has_var_kw:
            kwargs["max_parallel"] = max_parallel
        if kwargs:
            return run_pipeline(wt, objective, run_id, **kwargs)
    except (TypeError, ValueError):
        pass
    return run_pipeline(wt, objective, run_id)


def _out_of_scope(changed, scope) -> list:
    """Changed files that fall OUTSIDE a leaf's declared scope — the scope-contract guard's core predicate.

    Scope SEMANTICS (the guard must NOT auto-fail a legitimate leaf):
      - empty/absent scope -> a NO-OP: no declared file boundary, so NOTHING is out of scope. A
        legitimately scopeless leaf is not auto-failed (the prior `path_in_scope(p, [])` returned False for
        every path, so an empty scope wrongly flagged every changed file as a violation).
      - a directory-prefix entry admits files under it, AND a bare directory name (no trailing slash) is
        honored as a prefix too, so a directory-scoped leaf (scope `["pkg"]` or `["pkg/"]`) is not
        auto-failed when it writes `pkg/x.py`.
      - exact-file and glob entries keep frontier.path_in_scope's semantics unchanged.
    """
    entries = [s for s in (str(x).strip() for x in (scope or [])) if s]
    if not entries:                                            # no declared scope -> no constraint (NO-OP)
        return []
    prefixes = [e.replace("\\", "/").rstrip("/") + "/" for e in entries
                if not any(c in e for c in "*?[")]             # non-glob entries also act as directory prefixes
    out = []
    for p in changed:
        if frontier.path_in_scope(p, entries):                 # exact / glob / trailing-slash dir prefix
            continue
        rel = str(p).replace("\\", "/")
        if any(rel.startswith(pre) for pre in prefixes):       # bare-directory-name prefix
            continue
        out.append(p)
    return out


def default_run_leaf(repo, task, *, run_pipeline=None, resume_diff=None, goal_context=None,
                     defer_merge: bool = False) -> dict:
    """Run ONE leaf's dialectic (controller_pipeline) in its OWN worktree off the leaf base, so parallel leaves
    never collide on the shared repo (per-run isolation, ADR-0009). Returns
    {"outcome": "converged"|"unverified"|"failed", "reason": "linon"|"mechanical"|None, "findings", "diff"}:
      - converged -> the leaf's changed files merge back into the shared repo (ONLY when the pipeline's
        verification_status is "verified" — convergence alone never merges; see the fail-close below).
      - unverified -> the pipeline converged but a required gate was NOT proven green
        (verification_status != "verified"); TERMINAL and fail-closed — NOT merged, NOT done, never resumed
        or re-split. Carries `unverified_gate_findings` (ADR-0011 / ADR-0016).
      - failed/"linon" -> Linon reviewed the diff and rejected it: a BAD REFERENCE. Its findings come back
        to carry as CONTEXT to a re-split (what was tried and rejected), never as a base to build on.
      - failed/"mechanical" -> it failed for a non-quality reason (carrier timeout/hang, scope, malformed
        output); the partial work is preserved (`diff`) so a retry can RESUME on it.
    `resume_diff` (a patch path) is applied to the fresh worktree before the run, to resume prior work.
    The worktree is removed here unless `defer_merge` hands it to the caller's serial fold."""
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
    base_sha = task.get("base_sha") or "HEAD"
    # Parallel leaves add/remove worktrees against shared git metadata; rely on git's own lock files there.
    add = subprocess.run(["git", "-C", str(repo), "worktree", "add", "--detach", wt, base_sha],
                         capture_output=True, text=True)
    if add.returncode != 0:
        shutil.rmtree(wt, ignore_errors=True)
        return fail()
    if resume_diff and Path(resume_diff).is_file():            # RESUME prior (non-quality-rejected) work
        subprocess.run(["git", "-C", wt, "apply", "--whitespace=nowarn", str(resume_diff)],
                       capture_output=True)
    # handed_off is True ONLY on the converged defer-mode return that actually hands `wt` to the caller's
    # serial merge fold (the fold then removes it). EVERY other exit — crash, linon, mechanical, merge error,
    # and the whole non-defer path — leaves it False, so the `finally` removes the worktree + tempdir. This is
    # what closes the defer-mode leak: a non-converged leaf used to skip removal in defer mode and leak.
    handed_off = False
    try:
        try:
            result = _call_run_pipeline(run_pipeline, wt, task["objective"], run_id, goal_context)
        except Exception as exc:                               # noqa: BLE001 — a run_pipeline CRASH (a harness/
            # setup error, NOT carrier work): the registry/imports/worktree, e.g. AI_ORG_ROOT unset -> the
            # registry yaml missing. Surface it LOUDLY and classify it "crash"; NEVER swallow it. A swallowed
            # crash was treated as "mechanical", retried, re-split to the floor, and reported as a quiet
            # "failed" — masking a hard setup error and burning the whole budget on a crash re-split can't fix.
            # Scoped to run_pipeline ONLY: a later merge/handoff error stays "mechanical" (retryable), not an
            # abort (cross-checked — the broad boundary would also catch a merge crash and could abort on
            # partial files).
            import traceback
            detail = f"{type(exc).__name__}: {exc}"
            try:
                stream_emit(repo)({"type": "leaf_crash", "task_id": task.get("id"), "run_id": run_id,
                                   "error": detail, "traceback": traceback.format_exc()[-1500:]})
            except Exception:                                  # noqa: BLE001 — telemetry never breaks the run
                pass
            return fail(reason="crash", error=detail)          # "crash" (not "mechanical"): re-split can't fix it
        if not bool(result.get("converged")):
            if (result.get("linon_findings_count") or 0) > 0:  # Linon judged the diff -> a bad reference
                lin = (result.get("results") or {}).get("linon") or {}
                return fail(reason="linon",
                            findings=lin.get("findings") if isinstance(lin, dict) else None)
            return fail(reason="mechanical", diff=_preserve_diff(repo, wt, task))   # resume-able
        # CONSUMER FAIL-CLOSE (ADR-0011 unproven-never-passes / ADR-0016 never-fabricate-a-pass): the pipeline
        # can report converged=True yet leave a required gate UNVERIFIED — e.g. a gate that could not RUN, so it
        # is non-blocking-but-not-proven-green. The producer signals this on its result: verification_status
        # ("verified" | "unverified" | "failed") + unverified_gate_findings (controller_pipeline.run_pipeline,
        # controller_pipeline.py:1590/1598-1599). Convergence ALONE is not proof: only an explicit "verified"
        # verdict earns outcome:"converged". Any explicit non-"verified" status returns the DISTINCT, terminal
        # `unverified` outcome carrying the gate findings — which every caller treats as NOT mergeable, NOT done,
        # and NOT a resume/re-split (implementer-repair) target: it is neither "mechanical" nor "linon". (Missing
        # status only occurs in the legacy `{"converged": True}` test shorthand — the REAL producer ALWAYS emits
        # it — so absence is honored as verified-by-convergence and never masks a truly-unverified leaf.) Flipping
        # the producer's classification default is a SEPARATE, later increment; this only stops the consumer from
        # fabricating a pass over a verdict the producer already reported honestly.
        vstatus = result.get("verification_status")
        if vstatus is not None and vstatus != "verified":
            return {"outcome": "unverified", "verification_status": vstatus,
                    "unverified_gate_findings": result.get("unverified_gate_findings") or {}}
        if defer_merge:
            handed_off = True                                  # the ONLY hand-off: the fold now owns + removes wt
            return {"outcome": "converged", "leaf_worktree": wt, "_cleanup_worktree": True,
                    "sessions": result.get("sessions") or {}}
        changed = git_ops.leaf_changed_files(wt)
        out_of_scope = _out_of_scope(changed, task.get("scope"))
        if out_of_scope:
            try:
                stream_emit(repo)({"type": "leaf_scope_violation", "task_id": task.get("id"),
                                   "changed_files": changed, "out_of_scope": out_of_scope,
                                   "scope": task.get("scope") or []})
            except Exception:                                  # noqa: BLE001 - telemetry never breaks the run
                pass
            return fail(reason="mechanical", scope_violation=True,
                        changed_files=changed, out_of_scope=out_of_scope)
        # merge the leaf's files into the goal worktree and commit them as ONE commit (the handoff to
        # dependent leaves). Every git-state guard — dir expansion, literal pathspecs, scratch exclusion,
        # identity, add/commit-failure rollback — lives ONCE in git_ops.merge_and_commit_leaf, not inline.
        sha = git_ops.merge_and_commit_leaf(repo, wt, task.get("id"), task.get("objective"))
        if sha is None:                                       # None = handoff FAILED (paths rolled back)
            return fail(reason="mechanical")                  # "" = nothing to commit (still converged)
        return {"outcome": "converged", "commit": sha or None, "sessions": result.get("sessions") or {}}
    except Exception:                                          # noqa: BLE001 — a merge/handoff error is NOT a
        return fail(reason="mechanical")                      # setup crash: retryable, never a goal-abort
    finally:
        if not handed_off:                                     # cleaned up on EVERY exit that did not hand wt off
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


def _apply_steering(store, goal_id, leaf, plan, steering_goal_ids=None, notes=None):
    """Fold STEERING into THIS leaf's objective at dispatch — WITHOUT a kill + re-fire. A note applies when
    its target is "goal" (every leaf — the degenerate whole-Queue case) OR a node on this leaf's ancestry
    path (the leaf itself, or a BRANCH above it so a branch-targeted steer reaches its whole subtree).
    Node-targeting is the point: goal-level alone is just the Queue. Returns a COPY (never mutates the
    plan / the split source), or the leaf UNCHANGED when nothing applies. Standing guidance — re-evaluated
    for each new leaf at its own dispatch (re-split children inherit a branch's steer)."""
    notes = notes if notes is not None else _steering_notes_for(store, steering_goal_ids or [goal_id], leaf, plan)
    if not notes:
        return leaf
    block = "\n".join(f"- {n['text']}" for n in notes)
    steered = dict(leaf)
    steered["objective"] = ((leaf.get("objective") or "")
                            + "\n\n[STEERING added mid-run — additional guidance you MUST follow]:\n" + block)
    return steered


def _read_steering(store, goal_ids) -> list[dict]:
    """Read steering from one or more goal sidecars, preserving order and avoiding duplicate goal ids."""
    if store is None:
        return []
    notes: list[dict] = []
    seen = set()
    for gid in goal_ids or []:
        if not gid or gid in seen:
            continue
        seen.add(gid)
        notes.extend(store.read_steering(gid))
    return notes


def _steering_notes_for(store, goal_ids, leaf, plan) -> list[dict]:
    """Steering notes that reach a leaf by goal-wide or ancestry-targeted routing."""
    notes = _read_steering(store, goal_ids)
    if not notes:
        return []
    reach = _ancestry(plan, leaf.get("id")) or {leaf.get("id")}
    return [n for n in notes if n.get("target", "goal") == "goal" or n.get("target") in reach]


def _steer_refine(goal_text: str, context: dict, notes: list[dict]) -> tuple[str, dict]:
    """Thread answered steering into a refine call, not only into leaf dispatch."""
    if not notes:
        return goal_text, context
    block = "\n".join(f"- {n['text']}" for n in notes)
    steered_goal = ((goal_text or "")
                    + "\n\n[STEERING / ANSWER supplied for this refinement]:\n" + block)
    return steered_goal, {**(context or {}), "steering_answers": notes}


def _ask_question(node_id: str, missing: list) -> str:
    fields = ", ".join(str(m) for m in (missing or [])) or "the missing acceptance detail"
    return f"Please provide {fields} for `{node_id}` so the work can be checked without guessing."


def _make_ask(node_id: str, missing: list, structured: dict | None) -> dict:
    return {"node_id": node_id, "missing": list(missing or []), "question": _ask_question(node_id, missing),
            "structured": structured or {}, "status": "open"}


def _candidate_label(cand: dict) -> str:
    ref = cand.get("source_ref") or cand.get("url") or "source"
    return f"{ref}: {cand.get('value')}"


def _make_confirm_ask(node_id: str, missing: list, structured: dict | None, candidates: list[dict]) -> dict:
    fields = ", ".join(f"`{c.get('field')}`" for c in candidates)
    found = " ".join(f"I found `{c.get('field')}` in {c.get('source_ref')}: \"{c.get('value')}\"."
                     for c in candidates)
    residual = ""
    if missing:
        residual = " " + _ask_question(node_id, missing)
    return {"node_id": node_id, "missing": list(missing or []), "kind": "confirm",
            "original_missing": sorted({*(str(m) for m in missing or []),
                                        *(str(c.get("field")) for c in candidates if c.get("field"))}),
            "candidates": [dict(c) for c in candidates],
            "question": f"{found} Confirm {fields}? Reply yes to confirm, or supply the correction.{residual}",
            "structured": structured or {}, "status": "open"}


def _make_disambiguate_ask(node_id: str, missing: list, structured: dict | None, conflicts: list[dict]) -> dict:
    candidates = []
    lines = []
    for conflict in conflicts or []:
        field = conflict.get("field")
        lines.append(f"Conflicting candidates for `{field}`:")
        for cand in conflict.get("candidates") or []:
            candidates.append(dict(cand))
            lines.append(f"- {_candidate_label(cand)}")
    lines.append("Reply with the source/value to use, or supply the corrected value.")
    return {"node_id": node_id, "missing": list(missing or []), "kind": "disambiguate",
            "original_missing": list(missing or []),
            "candidates": candidates, "conflicts": [dict(c) for c in conflicts or []],
            "question": " ".join(lines), "structured": structured or {}, "status": "open"}


def _ask_search_enabled() -> bool:
    return os.environ.get("AOB_ASK_SEARCH", "1").strip().lower() not in {"0", "false", "no", "off"}


def _ask_or_confirm(repo: str, node_id: str, missing: list, structured: dict | None,
                    objective: str, emit) -> dict:
    bare = _make_ask(node_id, missing, structured)
    if not _ask_search_enabled():
        return bare
    try:
        found = ask_search.search_candidates(repo, node_id, missing, structured, objective, emit=emit, enabled=True)
    except Exception as exc:                                  # noqa: BLE001 - search is fail-soft
        if callable(emit):
            emit({"type": "ask_search_failed", "node_id": node_id, "error": str(exc)[:300]})
        return bare
    conflicts = found.get("conflicts") or []
    if conflicts:
        if callable(emit):
            emit({"type": "confirm_requested", "node_id": node_id, "fields": [c.get("field") for c in conflicts],
                  "shape": "disambiguate"})
        return _make_disambiguate_ask(node_id, missing, structured, conflicts)
    candidates = found.get("candidates") or []
    if not candidates:
        return bare
    answered = {c.get("field") for c in candidates}
    residual = [m for m in (missing or []) if m not in answered]
    if callable(emit):
        emit({"type": "confirm_requested", "node_id": node_id, "fields": list(answered), "shape": "confirm"})
    return _make_confirm_ask(node_id, residual, structured, candidates)


def _upsert_open_ask(asks: list[dict], ask: dict) -> list[dict]:
    out = [dict(a) for a in asks if not (a.get("node_id") == ask.get("node_id") and a.get("status") == "open")]
    out.append(dict(ask))
    return out


def _affirmed(text: str) -> bool:
    return (text or "").strip().lower().strip(".! ") in {"yes", "y", "confirm", "confirmed", "approve", "approved"}


def _rejected(text: str) -> bool:
    return (text or "").strip().lower().strip(".! ") in {"no", "n", "reject", "rejected", "decline", "declined"}


def _open_ask_by_node(asks: list[dict]) -> dict:
    return {a.get("node_id"): a for a in asks or [] if a.get("status") in {"open", "answered"}}


def _resolve_confirmations(notes: list[dict], asks: list[dict], emit=None) -> list[dict]:
    """Resolve confirm/disambiguate replies before refinement sees steering text."""
    open_by_node = _open_ask_by_node(asks)
    resolved = []
    for note in notes or []:
        copied = dict(note)
        target = copied.get("target")
        ask = open_by_node.get(target)
        text = copied.get("text", "")
        if not ask:
            resolved.append(copied)
            continue
        kind = ask.get("kind") or "bare"
        if kind == "confirm" and _affirmed(text):
            values = [str(c.get("value")) for c in ask.get("candidates") or [] if str(c.get("value") or "").strip()]
            copied["text"] = "\n".join(values)
            copied["_resolved"] = "confirmed"
            if callable(emit):
                emit({"type": "confirmation_affirmed", "node_id": target})
        elif kind in {"confirm", "disambiguate"} and _rejected(text):
            copied["_resolved"] = "rejected"
            if callable(emit):
                emit({"type": "confirmation_rejected", "node_id": target})
        elif kind == "disambiguate":
            chosen = None
            low = (text or "").lower()
            for cand in ask.get("candidates") or []:
                ref = str(cand.get("source_ref") or "")
                ref_l = ref.lower()
                ref_name = Path(ref).name.lower()
                ref_stem = Path(ref).stem.lower()
                value_l = str(cand.get("value") or "").lower()
                if ref_l in low or ref_name in low or ref_stem in low or value_l in low:
                    chosen = cand
                    break
            if chosen:
                copied["text"] = str(chosen.get("value") or "")
                copied["_resolved"] = "confirmed"
                if callable(emit):
                    emit({"type": "confirmation_affirmed", "node_id": target})
            else:
                copied["_resolved"] = "corrected"
        else:
            copied["_resolved"] = "corrected"
        resolved.append(copied)
    return resolved


def _repark_rejected_confirmations(asks: list[dict], notes: list[dict]) -> list[dict]:
    rejected = {n.get("target") for n in notes or [] if n.get("_resolved") == "rejected"}
    if not rejected:
        return asks
    out = []
    for ask in asks or []:
        copied = dict(ask)
        if copied.get("node_id") in rejected and copied.get("status") == "open":
            original_missing = copied.get("original_missing") or copied.get("missing") or []
            copied.pop("kind", None)
            copied.pop("candidates", None)
            copied.pop("conflicts", None)
            copied.pop("original_missing", None)
            copied["missing"] = list(original_missing)
            copied["question"] = _ask_question(copied.get("node_id"), copied.get("missing"))
        out.append(copied)
    return out


def _blocked_ids(tasks: list) -> set[str]:
    out: set[str] = set()
    for task in tasks or []:
        if task.get("children"):
            out.update(_blocked_ids(task["children"]))
        elif task.get("status") == "blocked_hitl":
            out.add(task.get("id"))
    return out


def _any_status(tasks: list, status: str) -> bool:
    for task in tasks or []:
        if task.get("children"):
            if _any_status(task["children"], status):
                return True
        elif task.get("status") == status:
            return True
    return False


def _reactivate_answered(tasks: list, answer_targets: set[str]) -> tuple[list, list[str]]:
    """Restore a parked frontier and flip only answered blocked nodes back to pending."""
    out = []
    reactivated = []
    for task in tasks or []:
        copied = dict(task)
        if isinstance(copied.get("scope"), list):
            copied["scope"] = list(copied["scope"])
        if isinstance(copied.get("depends_on"), list):
            copied["depends_on"] = list(copied["depends_on"])
        if copied.get("children"):
            copied["children"], child_ids = _reactivate_answered(copied["children"], answer_targets)
            reactivated.extend(child_ids)
        elif copied.get("status") == "blocked_hitl" and copied.get("id") in answer_targets:
            copied["status"] = "pending"
            reactivated.append(copied.get("id"))
        out.append(copied)
    return out, reactivated


def _mark_answered_asks(asks: list[dict], notes: list[dict], reactivated: set[str]) -> list[dict]:
    answer_by_target = {}
    resolved_by_target = {}
    for note in notes:
        target = note.get("target")
        if target in reactivated:
            answer_by_target[target] = note.get("text", "")
            resolved_by_target[target] = "confirmed" if note.get("_resolved") == "confirmed" else "corrected"
    out = []
    for ask in asks or []:
        copied = dict(ask)
        if copied.get("node_id") in reactivated and copied.get("status") == "open":
            copied["status"] = "answered"
            copied["answer"] = answer_by_target.get(copied.get("node_id"), "")
            copied["resolved"] = resolved_by_target.get(copied.get("node_id"), "corrected")
        out.append(copied)
    return out


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


def _goal_worktree_enabled() -> bool:
    """Goal-level worktree isolation is ON by default. A caller that already manages its own isolation
    (e.g. the cockpit, which isolates per RUN before it ever spawns controller_goal) opts OUT via
    AI_ORG_GOAL_WORKTREE=off/0/false/no — then run_goal runs on `--repo` directly, the old behavior."""
    return os.environ.get("AI_ORG_GOAL_WORKTREE", "1").strip().lower() not in {"0", "false", "no", "off"}


def _isolate_goal_repo(repo, branch):
    """Create a git worktree of `repo` on a fresh `branch` off `repo`'s current HEAD, so the WHOLE goal
    (its leaves, wip, commits) runs against the worktree and `repo`'s main working tree never moves during
    the run — closing the pollution bug where a manual launch's goal-level wip/commits landed on `repo`'s
    main AND an uncommitted hand-edit in `repo` got swept into the goal's commits. Mirrors the cockpit's
    per-run `_isolate_run_repo`. Returns the worktree path, or None to FALL BACK to running on `repo`
    directly (not a git repo, or `worktree add` failed) — fail-safe, never crashes."""
    try:
        if not (Path(repo) / ".git").exists():
            return None
        if subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                          capture_output=True, text=True).returncode != 0:
            return None                                        # no commits yet -> nothing to branch off
        wt = tempfile.mkdtemp(prefix="goal-wt-")
        add = subprocess.run(["git", "-C", str(repo), "worktree", "add", wt, "-b", branch, "HEAD"],
                             capture_output=True, text=True)
        if add.returncode != 0:                                # branch may already exist (a resumed id) ->
            shutil.rmtree(wt, ignore_errors=True)              # reuse it instead of failing to isolate
            wt = tempfile.mkdtemp(prefix="goal-wt-")
            add = subprocess.run(["git", "-C", str(repo), "worktree", "add", wt, branch],
                                 capture_output=True, text=True)
            if add.returncode != 0:
                shutil.rmtree(wt, ignore_errors=True)
                return None
        return wt
    except Exception:                                          # noqa: BLE001 — isolation is fail-safe
        return None


def _merge_goal_to_main(repo, branch, base_head, main_branch):
    """Merge the goal `branch` into `repo`'s local `main_branch` after a GREEN goal, so the work reaches the
    tree a consumer renders (the cockpit town renders LOCAL main). Fast-forward when main has not moved off
    `base_head`; otherwise a clean merge commit. If the merge does NOT apply cleanly (main moved under us),
    ABORT it and leave main untouched + the branch intact — main is never corrupted. Returns True iff main
    now includes the goal's work."""
    def g(*a):
        return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True)
    branch_head = g("rev-parse", "--verify", "--quiet", branch).stdout.strip()
    if not branch_head:
        return False
    if branch_head == base_head:
        return True                                            # goal produced no commits -> main already has it
    git_ops.ensure_identity(repo)
    main_head = g("rev-parse", "--verify", "--quiet", main_branch).stdout.strip()
    if main_head == base_head:                                 # main has not moved -> fast-forward
        if g("merge", "--ff-only", branch).returncode == 0:
            return True
    r = g("merge", "--no-ff", "--no-edit", "-m", f"merge: goal {branch} into {main_branch}", branch)
    if r.returncode == 0:
        return True
    g("merge", "--abort")                                      # conflict -> never corrupt main
    return False


def _cleanup_goal_worktree(repo, worktree, branch, *, delete_branch) -> None:
    """Remove the goal worktree (and, on success, its now-merged branch). Worktree first — a branch checked
    out in a worktree cannot be deleted. Fail-soft: cleanup never breaks the goal's reported outcome."""
    subprocess.run(["git", "-C", str(repo), "worktree", "remove", "--force", str(worktree)],
                   capture_output=True)
    shutil.rmtree(worktree, ignore_errors=True)
    if delete_branch and branch:
        subprocess.run(["git", "-C", str(repo), "branch", "-D", branch], capture_output=True)


def _goal_acceptance_profile(context):
    """The EXECUTABLE goal `acceptance_profile` (ADR-0016 D7) — the goal contract the OWNER authored/confirmed
    at INTAKE, carried verbatim through `context`. It is NOT derived from the natural-language
    success_condition (compiling NL into a probe at the end would re-introduce the LLM-label-trust the
    goal-level hole came from). Returns the profile dict, or None when absent (= today's shadow behavior)."""
    if not isinstance(context, dict):
        return None
    profile = context.get("acceptance_profile")
    return profile if isinstance(profile, dict) and profile else None


def run_goal(repo, goal, run_leaf=None, *, goal_id=None, resume_from=None, split=splitter.split,
             refine=None, context=None, carrier=None, budget=None, emit=None) -> list:
    """Run a goal through the live recursive TaskExecutor path, or the legacy frontier path when disabled."""
    if not _use_taskexecutor():
        return _run_goal_legacy(repo, goal, run_leaf=run_leaf, goal_id=goal_id, resume_from=resume_from,
                                split=split, refine=refine, context=context, carrier=carrier,
                                budget=budget, emit=emit)
    return _run_goal_taskexecutor(repo, goal, run_leaf=run_leaf, goal_id=goal_id, resume_from=resume_from,
                                  split=split, refine=refine, context=context, carrier=carrier,
                                  budget=budget, emit=emit)


def _repo_head(repo) -> str:
    out = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True)
    return out.stdout.strip() or "HEAD"


def _format_refined_goal(goal: str, structured) -> str:
    if not isinstance(structured, dict) or not structured:
        return goal
    parts = [f"Raw goal: {goal}"]
    for key in ("intent", "outcome", "success_condition", "negative_control", "owner"):
        value = structured.get(key)
        if value:
            parts.append(f"{key}: {value}")
    return "\n".join(parts)


def _task_from_node(node: task_executor.TaskNode) -> dict:
    task = {
        "id": node.id,
        "objective": node.objective,
        "scope": list(getattr(node, "scope", []) or []),
        "depends_on": list(node.depends_on or []),
        "base_sha": node.base_sha,
        "status": getattr(node, "status", "done"),
        "run_id": None,
        "pr_url": None,
    }
    if node.subtasks:
        task["children"] = [_task_from_node(child) for child in node.subtasks]
    return task


def _plan_from_taskexecutor_root(root: task_executor.TaskNode, *, status: str = "done") -> list[dict]:
    def mark(node):
        node.status = status
        for child in node.subtasks:
            mark(child)
    mark(root)
    nodes = root.subtasks or [root]
    return [_task_from_node(node) for node in nodes]


def _tasknodes_from_legacy_plan(plan: list[dict], parent: task_executor.TaskNode) -> list[task_executor.TaskNode]:
    children = []
    for item in plan or []:
        if not isinstance(item, dict):
            continue
        child = task_executor.TaskNode(
            id=str(item.get("id") or f"{parent.id}.{len(children) + 1}"),
            kind=task_executor.COMPOSITE if item.get("children") else task_executor.LEAF,
            depends_on=[str(d) for d in (item.get("depends_on") or [])],
            base_sha=item.get("base_sha") or parent.base_sha,
            objective=str(item.get("objective") or item.get("id") or ""),
            depth=parent.depth + 1,
        )
        child.scope = list(item.get("scope") or [])
        if item.get("children"):
            child.subtasks = _tasknodes_from_legacy_plan(item.get("children") or [], child)
        children.append(child)
    return children


def _commit_worktree_off_base(repo, wt, base: str, message: str) -> str:
    git_ops.ensure_identity(repo)
    changed = git_ops.leaf_changed_files(wt)
    if changed:
        specs = [f":(literal){path}" for path in changed]
        subprocess.run(["git", "-C", str(wt), "add", "--", *specs], capture_output=True)
    tree = subprocess.run(["git", "-C", str(wt), "write-tree"], capture_output=True, text=True).stdout.strip()
    if not tree:
        raise task_executor.TaskExecutorIntegrationError(f"write-tree failed in {wt}")
    out = subprocess.run(["git", "-C", str(repo), "commit-tree", tree, "-p", base, "-m", message],
                         capture_output=True, text=True)
    sha = out.stdout.strip()
    if not sha:
        raise task_executor.TaskExecutorIntegrationError(f"commit-tree failed: {out.stderr.strip()}")
    return sha


def _taskexecutor_plan_snapshot(root: task_executor.TaskNode) -> list[dict]:
    nodes = root.subtasks or [root]
    return [_task_from_node(node) for node in nodes]


def _verified_leaf_adapter(repo, run_leaf, context_ref, steer_node=None):
    leaf_runner = run_leaf or default_run_leaf

    def adapter(node: task_executor.TaskNode) -> task_executor.VerifiedCommit:
        if callable(steer_node):
            steer_node(node, refine_objective=False)
        task = {"id": node.id, "objective": node.objective, "scope": list(getattr(node, "scope", []) or []),
                "depends_on": list(node.depends_on or []), "base_sha": node.base_sha}
        if _run_leaf_accepts_defer_merge(leaf_runner):
            res = _call_leaf(leaf_runner, repo, task, goal_context=context_ref.get("value"), defer_merge=True)
            if isinstance(res, task_executor.VerifiedCommit):
                return res
            if not isinstance(res, dict) or res.get("outcome") != "converged":
                raise task_executor.TaskExecutorIntegrationError(
                    f"leaf {node.id} did not converge: {res!r}")
            wt = res.get("leaf_worktree")
            try:
                if wt:
                    base = node.base_sha or _repo_head(repo)
                    sha = _commit_worktree_off_base(repo, wt, base, f"leaf: {node.id}")
                else:
                    sha = res.get("commit_sha") or res.get("commit") or (node.base_sha or _repo_head(repo))
                evidence = {k: v for k, v in res.items() if k not in ("leaf_worktree", "_cleanup_worktree")}
                return task_executor.VerifiedCommit(node.id, sha, evidence)
            finally:
                if wt and res.get("_cleanup_worktree"):
                    subprocess.run(["git", "-C", str(repo), "worktree", "remove", "--force", str(wt)],
                                   capture_output=True)
                    shutil.rmtree(wt, ignore_errors=True)
        res = _call_leaf(leaf_runner, repo, task, goal_context=context_ref.get("value"))
        if isinstance(res, task_executor.VerifiedCommit):
            return res
        if isinstance(res, dict) and res.get("outcome") not in (None, "converged"):
            raise task_executor.TaskExecutorIntegrationError(f"leaf {node.id} did not converge: {res!r}")
        if res == "converged":
            return task_executor.VerifiedCommit(node.id, node.base_sha or _repo_head(repo),
                                                {"outcome": "converged"})
        return task_executor._as_verified_commit(res, node)

    return adapter


def _apply_verified_commit(repo, verified: task_executor.VerifiedCommit, emit) -> None:
    sha = (verified or task_executor.VerifiedCommit("", "", {})).commit_sha
    if not sha or not (Path(repo) / ".git").exists():
        return
    exists = subprocess.run(["git", "-C", str(repo), "cat-file", "-e", f"{sha}^{{commit}}"],
                            capture_output=True)
    if exists.returncode != 0:
        return
    head = _repo_head(repo)
    if sha == head:
        return
    cp = subprocess.run(["git", "-C", str(repo), "cherry-pick", "--allow-empty",
                         "--keep-redundant-commits", sha], capture_output=True, text=True)
    if cp.returncode != 0:
        subprocess.run(["git", "-C", str(repo), "cherry-pick", "--abort"], capture_output=True)
        emit({"type": "goal_blocked", "id": verified.task_id, "error": cp.stderr.strip(),
              "detail": "TaskExecutor produced a commit that could not be applied to the goal worktree"})
        raise task_executor.TaskExecutorIntegrationError(cp.stderr.strip())


def _root_ci_writers_enabled() -> bool:
    try:
        import controller_pipeline
        return controller_pipeline._ci_writers_enabled()
    except Exception:  # noqa: BLE001 - opt-in root CI must not become enabled through an import failure
        return False


def _commit_root_ci_changes(repo) -> str | None:
    if not (Path(repo) / ".git").exists():
        return None
    changed = subprocess.run(["git", "-C", str(repo), "status", "--porcelain", "--", ".github/workflows"],
                             capture_output=True, text=True).stdout.strip()
    if not changed:
        return None
    git_ops.ensure_identity(repo)
    subprocess.run(["git", "-C", str(repo), "add", "--", ".github/workflows"], capture_output=True)
    commit = subprocess.run(["git", "-C", str(repo), "commit", "-m", "root ci workflows"],
                            capture_output=True, text=True)
    if commit.returncode != 0:
        return None
    return _repo_head(repo)


def _run_root_ci_writers(repo, goal, context, emit) -> bool:
    """Run CI-action-writer roles once at the root, after TaskExecutor composition and before acceptance."""
    import controller_pipeline
    entries = controller_pipeline._entries(Path(repo))
    roles = sorted(controller_pipeline.CI_WRITER_ROLES & set(entries))
    if not roles:
        emit({"type": "root_ci_skipped", "reason": "no_roles"})
        return True
    emit({"type": "root_ci_writers_start", "roles": roles})
    ok = True
    for role in roles:
        stage_ok, _result, report_dict, stage = controller_pipeline._execute_stage_isolated(
            Path(repo), role, entries[role], goal, {}, f"root-ci-{uuid.uuid4().hex[:10]}-{role}",
            True, goal_context=context)
        emit({"type": "root_ci_writer_done", "role": role, "ok": bool(stage_ok),
              "unresolved": report_dict.get("unresolved_failures") or [], "stage": stage})
        ok = ok and bool(stage_ok)
    commit = _commit_root_ci_changes(repo)
    if commit:
        emit({"type": "root_ci_writers_committed", "commit": commit})
    return ok


def _make_taskexecutor_decomposer(repo, goal, split, context_ref, carrier, emit, steer_node=None):
    decompose_carrier = task_executor.codex_decompose_carrier(repo)

    def decomposer(node: task_executor.TaskNode):
        if callable(steer_node):
            steer_node(node, refine_objective=True)
        context = context_ref.get("value") or {}
        if split is splitter.split:
            result = task_executor.decompose_with_metadata(node, decompose_carrier, task_executor._max_depth())
            children = result.children
            out = result
        else:
            legacy_plan = split(node.objective, {**context, "parent": None if node.id == "root" else node.id},
                                carrier)
            children = _tasknodes_from_legacy_plan(legacy_plan, node)
            out = task_executor.DecomposeResult(children=children)
        if node.id == "root":
            emit({"type": "goal_split", "goal": goal, "n": len(children)})
            _emit_splitter_speech(emit, None, [_task_from_node(child) for child in children])
        return out

    return decomposer


def _make_taskexecutor_steering(store, goal_id, steering_goal_ids, asks, root, context_ref, emit):
    applied: dict[str, set[tuple]] = {}

    def _note_key(note: dict) -> tuple:
        return (note.get("target", "goal"), note.get("text", ""), note.get("_resolved"))

    def steer_node(node: task_executor.TaskNode, *, refine_objective: bool) -> None:
        notes = _resolve_confirmations(_read_steering(store, steering_goal_ids), asks, emit)
        notes = [n for n in notes if n.get("_resolved") != "rejected"]
        if not notes:
            return
        task = _task_from_node(node)
        plan = _taskexecutor_plan_snapshot(root)
        reach = _ancestry(plan, task.get("id")) or {task.get("id")}
        applicable = [n for n in notes if n.get("target", "goal") == "goal" or n.get("target") in reach]
        seen = applied.setdefault(node.id, set())
        fresh = [n for n in applicable if _note_key(n) not in seen]
        if not fresh:
            return
        for note in fresh:
            seen.add(_note_key(note))
        if refine_objective:
            node.objective, refined_context = _steer_refine(node.objective, context_ref.get("value") or {}, fresh)
            context_ref["value"] = refined_context
        else:
            steered = _apply_steering(store, goal_id, task, plan, steering_goal_ids, notes=fresh)
            node.objective = steered.get("objective") or node.objective
        emit({"type": "steer_applied", "id": node.id, "goal_id": goal_id})

    return steer_node


def _run_goal_taskexecutor(repo, goal, run_leaf=None, *, goal_id=None, resume_from=None, split=splitter.split,
                           refine=None, context=None, carrier=None, budget=None, emit=None) -> list:
    """Live goal path: intake/refine -> recursive TaskExecutor -> root CI -> goal acceptance -> merge."""
    if not os.environ.get("STREAM_LOG"):
        os.environ["STREAM_LOG"] = str(Path(repo).resolve() / ".agent-runs" / "stream.jsonl")
    emit = emit or stream_emit(repo)
    default_leaf_path = run_leaf is None or run_leaf is default_run_leaf
    run_leaf = run_leaf or default_run_leaf
    if default_leaf_path or split is splitter.split:
        check_launch_preconditions(repo, emit=emit)

    orig_repo = str(repo)
    iso_wt = None
    goal_branch = None
    orig_head = None
    orig_branch_name = None
    if _goal_worktree_enabled():
        goal_branch = "goal/" + (str(goal_id) if goal_id else ("anon-" + uuid.uuid4().hex[:8]))
        iso_wt = _isolate_goal_repo(orig_repo, goal_branch)
        if iso_wt is not None:
            _g = lambda *a: subprocess.run(["git", "-C", orig_repo, *a], capture_output=True, text=True)  # noqa: E731
            orig_head = _g("rev-parse", "HEAD").stdout.strip()
            orig_branch_name = _g("rev-parse", "--abbrev-ref", "HEAD").stdout.strip() or "main"
            emit({"type": "goal_worktree", "repo": orig_repo, "worktree": iso_wt,
                  "branch": goal_branch, "base": orig_head})
            repo = iso_wt
        else:
            goal_branch = None

    store = goal_store.GoalStore(_shared_state_repo(repo), emit=emit) if goal_id else None
    prior_record = store.read(resume_from) if store is not None and resume_from else None
    if store is not None:
        store.create(goal_id, goal, org="", resumed_from=resume_from)
        if resume_from and store.load(resume_from, repo):
            restored = store.restored_files(resume_from, repo)
            if restored:
                context = {**(context or {}), "resumed_prior_work": {
                    "files": restored[:200],
                    "instruction": "These files ALREADY EXIST, cherry-picked from resumed prior work. Build "
                                   "the goal ON them: extend or patch the existing files; do NOT recreate "
                                   "equivalent content under new names. Plan only the remaining work."}}
    steering_goal_ids = [goal_id] + ([resume_from] if resume_from and resume_from != goal_id else [])
    asks: list[dict] = [dict(a) for a in ((prior_record or {}).get("asks") or []) if isinstance(a, dict)]

    boundary = scaffold_primitive._declared_dir(goal)
    if boundary:
        context = {**(context or {}), "scope_boundary": {
            "dir": boundary,
            "instruction": f"Every task's scope MUST be a path under `{boundary}/`. Place no deliverable "
                           f"file outside it and do not invent a sibling directory."}}

    top_carrier = carrier if carrier is not None else (codex_carrier(repo) if split is splitter.split else None)
    active_refine = refine if refine is not None else (goal_refiner.refine if split is splitter.split else None)
    if active_refine is not None:
        refine_carrier = top_carrier if refine is not None and top_carrier is not None else codex_carrier(repo)
        goal_notes = [n for n in _resolve_confirmations(_read_steering(store, steering_goal_ids), asks, emit)
                      if n.get("target", "goal") in ("goal", "_goal")]
        goal_notes = [n for n in goal_notes if n.get("_resolved") != "rejected"]
        refine_goal, refine_context = _steer_refine(goal, context or {}, goal_notes)
        verdict = active_refine(refine_goal, refine_context, refine_carrier)
        if not verdict.get("sufficient"):
            missing = verdict.get("missing", [])
            emit({"type": "goal_underdetermined", "goal": goal, "missing": missing})
            ask = _ask_or_confirm(repo, "_goal", missing, verdict.get("structured"), goal, emit)
            asks[:] = _upsert_open_ask(asks, ask)
            if store is not None:
                store.update(goal_id, status="needs_info", missing=missing,
                             structured_goal=verdict.get("structured"), asks=asks, open_asks=[ask])
            if iso_wt is not None:
                _cleanup_goal_worktree(orig_repo, iso_wt, goal_branch, delete_branch=True)
            return []
        context = {**(context or {}), "structured_goal": verdict.get("structured")}

    context_ref = {"value": context or {}}
    root = task_executor.TaskNode(
        id="root",
        kind=task_executor.COMPOSITE,
        base_sha=_repo_head(repo),
        objective=_format_refined_goal(goal, context_ref["value"].get("structured_goal")),
        depth=0,
    )
    def task_emit(event):
        enriched = dict(event)
        if goal_id is not None:
            enriched.setdefault("goal_id", goal_id)
        emit(enriched)

    steer_node = _make_taskexecutor_steering(store, goal_id, steering_goal_ids, asks, root, context_ref, task_emit)
    executor = task_executor.TaskExecutor(
        repo,
        run_leaf=_verified_leaf_adapter(repo, run_leaf, context_ref, steer_node),
        decompose_carrier=task_executor.codex_decompose_carrier(repo),
        decomposer=_make_taskexecutor_decomposer(repo, goal, split, context_ref, top_carrier, task_emit, steer_node),
        emit=task_emit,
    )

    final_verified = None
    execution_ok = False
    try:
        emit({"type": "taskexecutor_start", "goal": goal, "goal_id": goal_id})
        final_verified = executor.execute(root)
        _apply_verified_commit(repo, final_verified, emit)
        execution_ok = True
        emit({"type": "taskexecutor_done", "goal": goal, "goal_id": goal_id,
              "commit": final_verified.commit_sha})
    except Exception as exc:  # noqa: BLE001 - TaskExecutor is the live executor; a failure fails this goal
        emit({"type": "goal_aborted", "error": f"{type(exc).__name__}: {exc}",
              "detail": "TaskExecutor failed; no legacy frontier work is attempted while the flag is on."})
    plan = _plan_from_taskexecutor_root(root, status="done" if execution_ok else "failed")

    if execution_ok:
        if _root_ci_writers_enabled():
            execution_ok = _run_root_ci_writers(repo, goal, context_ref["value"], emit)
            if not execution_ok:
                plan = _plan_from_taskexecutor_root(root, status="failed")
        else:
            emit({"type": "root_ci_skipped", "reason": "disabled"})

    return _finalize_taskexecutor_goal(repo, goal, plan, context_ref["value"], store, goal_id, asks, execution_ok, emit,
                                       iso_wt=iso_wt, orig_repo=orig_repo, goal_branch=goal_branch,
                                       orig_head=orig_head, orig_branch_name=orig_branch_name,
                                       final_verified=final_verified)


def _finalize_taskexecutor_goal(repo, goal, final_plan, context, store, goal_id, asks, execution_ok, emit, *,
                                iso_wt=None, orig_repo=None, goal_branch=None, orig_head=None,
                                orig_branch_name=None, final_verified=None):
    done = bool(final_plan) and execution_ok and all(frontier.node_status(t) == "done" for t in final_plan)
    status = "done" if done else "failed"
    sg = (context or {}).get("structured_goal") if isinstance(context, dict) else None
    profile = _goal_acceptance_profile(context)
    goal_acc = None
    if done and profile is not None:
        result = conformance.run_goal_acceptance(profile, repo)
        verified = bool(result.get("verified"))
        goal_acc = {"type": "goal_acceptance", "verified": verified,
                    "status": "verified" if verified else "failed_acceptance",
                    "outcome": (sg or {}).get("outcome"),
                    "success_condition": (sg or {}).get("success_condition"),
                    "negative_control": (sg or {}).get("negative_control"),
                    "owner": (sg or {}).get("owner"),
                    "evidence": result.get("evidence"), "findings": result.get("findings"),
                    "probes_run": result.get("probes_run"),
                    "note": ("composed goal artifact booted and satisfied the executable acceptance profile"
                             if verified else
                             "composed goal artifact does NOT satisfy the goal WHY — the acceptance probe "
                             "failed; NOT merged to main")}
        if not verified:
            status = "failed"
            done = False
    elif done and isinstance(sg, dict) and (sg.get("negative_control") or sg.get("success_condition")):
        goal_acc = {"type": "goal_acceptance", "verified": False, "status": "needs_info",
                    "outcome": sg.get("outcome"), "success_condition": sg.get("success_condition"),
                    "negative_control": sg.get("negative_control"), "owner": sg.get("owner"),
                    "note": "composed outcome NOT checked against the goal WHY — no executable "
                            "acceptance_profile was authored at intake; the leaves proved only "
                            "leaf-obeys-contract"}
        status = "needs_info"
        done = False
    elif done and profile is None:
        goal_acc = {"type": "goal_acceptance", "verified": False, "status": "needs_info",
                    "note": "composed outcome NOT checked against the goal WHY — no executable "
                            "acceptance_profile was authored at intake"}
        status = "needs_info"
        done = False

    wip = None
    if store is not None:
        wip = store.save_wip(goal_id, repo)
        leaf_commits = {}
        if final_verified is not None:
            leaf_commits[final_verified.task_id] = final_verified.commit_sha
        update = {"status": status, "queue": final_plan, "leaf_commits": leaf_commits,
                  "asks": asks, "open_asks": [a for a in asks if a.get("status") == "open"]}
        if goal_acc is not None:
            update["goal_acceptance"] = goal_acc
        store.update(goal_id, **update)
    if goal_acc is not None:
        emit(goal_acc)
    emit({"type": "goal_finished", "status": status, "wip": wip})
    if status == "done":
        emit({"type": "goal_done", "goal": goal})

    if iso_wt is not None:
        if status == "done":
            if _merge_goal_to_main(orig_repo, goal_branch, orig_head, orig_branch_name):
                _cleanup_goal_worktree(orig_repo, iso_wt, goal_branch, delete_branch=True)
                emit({"type": "goal_merged", "branch": goal_branch, "into": orig_branch_name})
            else:
                emit({"type": "goal_merge_conflict", "branch": goal_branch, "into": orig_branch_name,
                      "worktree": iso_wt, "detail": "local main moved under the run and the goal branch "
                      "did not merge cleanly; branch + worktree left intact, main untouched"})
        else:
            emit({"type": "goal_worktree_retained", "branch": goal_branch, "worktree": iso_wt,
                  "status": status, "detail": "non-green outcome — worktree+branch left for inspection, "
                  "main untouched"})
    return final_plan


def _run_goal_legacy(repo, goal, run_leaf=None, *, goal_id=None, resume_from=None, split=splitter.split,
                     refine=None, context=None, carrier=None, budget=None, emit=None) -> list:
    """Decompose `goal` and build it.

    run_leaf(repo, task) -> "converged" | "failed" runs one leaf's dialectic (defaults to
    default_run_leaf, which runs controller_pipeline in an isolated worktree; a stub in tests). budget
    caps the number of leaf runs (None = unbounded, bounded only by the floor). emit(event) streams
    progress (ADR-0009). When `goal_id` is given, the ORG OWNS this goal's state — the received goal
    becomes the org's at receipt: it records the goal, commits its build (wip) and its outcome, in its own
    GoalStore. A consumer only READS that state. Returns the final task tree."""
    # Bind STREAM_LOG to the SHARED stream (ABSOLUTE) so the leaf dialectic — which runs in-process with
    # repo=<temp worktree> — appends to the shared log, not an ephemeral worktree-local one that is destroyed
    # with the worktree (the deep dialectic was both invisible to consumers AND lost). Absolute is required:
    # the child runs with cwd in a temp worktree, so a relative value would resolve under /tmp.
    # INVARIANT: this sets PROCESS-GLOBAL env, and only when unset, assuming ONE repo per process (production
    # spawns a controller_goal subprocess per goal — server.py). A long-lived process running goals for
    # DIFFERENT repos must set STREAM_LOG itself per goal; an external pre-set (a host pointing N goals at
    # one shared log, ADR-0007) is intentionally respected. It also fixes the GoalStore root (derived from
    # STREAM_LOG) to this repo. Tests that call run_goal repeatedly pop STREAM_LOG between cases.
    if not os.environ.get("STREAM_LOG"):
        os.environ["STREAM_LOG"] = str(Path(repo).resolve() / ".agent-runs" / "stream.jsonl")
    emit = emit or stream_emit(repo)
    default_leaf_path = run_leaf is None or run_leaf is default_run_leaf
    run_leaf = run_leaf or default_run_leaf
    if default_leaf_path or split is splitter.split:
        check_launch_preconditions(repo, emit=emit)
    # GOAL WORKTREE (default ON): run the WHOLE goal in an isolated git worktree of `--repo` on a fresh
    # `goal/<id>` branch off main HEAD, so `--repo`'s main working tree never moves during the run — the
    # goal's wip/commits land on the branch (not main), and an uncommitted hand-edit in `--repo` cannot be
    # swept into the goal's commits (it isn't in the worktree). The shared STREAM_LOG / GoalStore stay
    # pinned at `--repo` (above), so streaming + durable state are unaffected. On GREEN the branch is merged
    # back into `--repo`'s local main (the town renders local main). A caller that isolates per-run itself
    # (the cockpit) opts out; a non-git repo or a failed `worktree add` falls back to running on `--repo`
    # directly — exactly as the cockpit's helper falls back. main stays clean on every non-green outcome.
    orig_repo = str(repo)
    iso_wt = None
    goal_branch = None
    orig_head = None
    orig_branch_name = None
    if _goal_worktree_enabled():
        goal_branch = "goal/" + (str(goal_id) if goal_id else ("anon-" + uuid.uuid4().hex[:8]))
        iso_wt = _isolate_goal_repo(orig_repo, goal_branch)
        if iso_wt is not None:
            _g = lambda *a: subprocess.run(["git", "-C", orig_repo, *a], capture_output=True, text=True)  # noqa: E731
            orig_head = _g("rev-parse", "HEAD").stdout.strip()
            orig_branch_name = _g("rev-parse", "--abbrev-ref", "HEAD").stdout.strip() or "main"
            emit({"type": "goal_worktree", "repo": orig_repo, "worktree": iso_wt,
                  "branch": goal_branch, "base": orig_head})
            repo = iso_wt                                       # run the entire goal against the worktree
        else:
            goal_branch = None                                 # fall back to running on --repo directly
    # the org's state STORE is durable + SHARED (so a consumer can READ current state, DB-style): write it where
    # STREAM_LOG points (the shared .agent-runs), not the ephemeral goal worktree. git refs are already
    # shared. `emit` is threaded in so every state OPERATION (create/load/save/update) also lands in the log.
    store = goal_store.GoalStore(_shared_state_repo(repo), emit=emit) if goal_id else None
    prior_record = store.read(resume_from) if store is not None and resume_from else None
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
    steering_goal_ids = [goal_id] + ([resume_from] if resume_from and resume_from != goal_id else [])
    asks: list[dict] = [dict(a) for a in ((prior_record or {}).get("asks") or []) if isinstance(a, dict)]
    leaf_commits: dict = dict((prior_record or {}).get("leaf_commits") or {})  # leaf_id -> its own commit sha

    def _finalize(final_plan):
        done = bool(final_plan) and all(frontier.node_status(t) == "done" for t in final_plan)
        blocked = _any_status(final_plan, "blocked_hitl")
        status = "done" if done else ("blocked_hitl" if blocked else "failed")
        # ADR-0016 D7 — GOAL-LEVEL ACCEPTANCE GATE (the COMPOSING-layer WHY check, the goal-level analogue of
        # the per-leaf real-wiring gate). Per-leaf conformance proved only leaf-obeys-contract; a goal whose
        # leaves are all `done` has NOT been checked against its OWN outcome. When the owner authored an
        # EXECUTABLE acceptance_profile at INTAKE, BOOT the COMPOSED goal artifact (this worktree — AFTER all
        # leaves merged, BEFORE merge-to-main) and probe it against the profile, INDEPENDENT of any leaf's
        # deliverable_kind, under the SAME rlimit sandbox + guaranteed process teardown as the per-leaf gate.
        # PASS -> verified:true + durable evidence -> proceed to merge. FAIL -> verified:false; flip status to
        # `failed` so it does NOT merge to main (the composed artifact does not satisfy the goal WHY). No
        # profile -> keep today's SHADOW behavior EXACTLY (verified:false/needs_info, still merges) — no
        # regression. The determinism lives in the intake-fixed profile, NOT in compiling the NL
        # success_condition into a probe at the end (that re-introduces the LLM-label-trust the hole came from).
        sg = (context or {}).get("structured_goal") if isinstance(context, dict) else None
        profile = _goal_acceptance_profile(context)
        goal_acc = None
        if done and profile is not None:
            result = conformance.run_goal_acceptance(profile, repo)
            verified = bool(result.get("verified"))
            goal_acc = {"type": "goal_acceptance", "verified": verified,
                        "status": "verified" if verified else "failed_acceptance",
                        "outcome": (sg or {}).get("outcome"),
                        "success_condition": (sg or {}).get("success_condition"),
                        "negative_control": (sg or {}).get("negative_control"),
                        "owner": (sg or {}).get("owner"),
                        "evidence": result.get("evidence"), "findings": result.get("findings"),
                        "probes_run": result.get("probes_run"),
                        "note": ("composed goal artifact booted and satisfied the executable acceptance profile"
                                 if verified else
                                 "composed goal artifact does NOT satisfy the goal WHY — the acceptance probe "
                                 "failed; NOT merged to main")}
            if not verified:
                status = "failed"          # the composed artifact failed acceptance -> do NOT merge to main
                done = False
        elif done and isinstance(sg, dict) and (sg.get("negative_control") or sg.get("success_condition")):
            # SHADOW (no executable profile authored): surface that the composed outcome is unverified against
            # the WHY, NEVER fabricate a green (D5). Unchanged pre-gate behavior — the goal still merges.
            goal_acc = {"type": "goal_acceptance", "verified": False, "status": "needs_info",
                        "outcome": sg.get("outcome"), "success_condition": sg.get("success_condition"),
                        "negative_control": sg.get("negative_control"), "owner": sg.get("owner"),
                        "note": "composed outcome NOT checked against the goal WHY — no executable "
                                "acceptance_profile was authored at intake; the leaves proved only "
                                "leaf-obeys-contract"}
        wip = None
        if store is not None:                          # OPERATE the org's state: record its build + outcome
            wip = store.save_wip(goal_id, repo)
            # the state EXPRESSES the per-Queue git scattering: the Queue itself (the recursive split tree)
            # plus each leaf's OWN commit — git scatters one worktree/commit per leaf, not just the wip tip.
            update = {"status": status, "queue": final_plan, "leaf_commits": leaf_commits,
                      "asks": asks, "open_asks": [a for a in asks if a.get("status") == "open"]}
            if status == "blocked_hitl":
                update["result"] = "partial" if _any_status(final_plan, "done") else "blocked_hitl"
            if goal_acc is not None:
                update["goal_acceptance"] = goal_acc
            store.update(goal_id, **update)
        if goal_acc is not None:
            emit(goal_acc)
        # rich log: the org flows its TERMINAL state (outcome + the wip commit) into its own Stream, so the
        # state is reconstructible from the log too — the log is the best resource for grasping state.
        emit({"type": "goal_finished", "status": status, "wip": wip})
        # GOAL WORKTREE outcome: on GREEN merge the goal branch into `--repo`'s local main (so the work
        # reaches the tree a consumer renders) and remove the now-merged worktree+branch. On a clean merge
        # FAILURE (main moved under us) leave both intact and main untouched. On any non-green outcome leave
        # the worktree+branch for inspection — main stays clean either way.
        if iso_wt is not None:
            if status == "done":
                if _merge_goal_to_main(orig_repo, goal_branch, orig_head, orig_branch_name):
                    _cleanup_goal_worktree(orig_repo, iso_wt, goal_branch, delete_branch=True)
                    emit({"type": "goal_merged", "branch": goal_branch, "into": orig_branch_name})
                else:
                    emit({"type": "goal_merge_conflict", "branch": goal_branch, "into": orig_branch_name,
                          "worktree": iso_wt, "detail": "local main moved under the run and the goal branch "
                          "did not merge cleanly; branch + worktree left intact, main untouched"})
            else:
                emit({"type": "goal_worktree_retained", "branch": goal_branch, "worktree": iso_wt,
                      "status": status, "detail": "non-green outcome — worktree+branch left for inspection, "
                      "main untouched"})
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
    # ADR-0016 D1b — INTAKE SUFFICIENCY GATE: refine raw -> candidate structured goal BEFORE decomposing.
    # A goal proceeds only when a falsifiable acceptance can be NAMED (outcome / success_condition /
    # negative_control / owner). If it cannot, the engine emits the ASK and HOLDs — it does NOT guess the ends
    # (D5). Runs on the real path, or whenever a caller injects `refine`. When a caller injects only `split`
    # (the existing test path), the gate is skipped so injected plans run unchanged. NOTE: on resume_from the
    # prior work was already loaded into the worktree above — a HOLD here means "not decomposed/built", not
    # "no worktree state".
    active_refine = refine if refine is not None else (goal_refiner.refine if split is splitter.split else None)
    if active_refine is not None:
        refine_carrier = top_carrier if refine is not None else codex_carrier(repo)
        goal_notes = [n for n in _resolve_confirmations(_read_steering(store, steering_goal_ids), asks, emit)
                      if n.get("target", "goal") in ("goal", "_goal")]
        goal_notes = [n for n in goal_notes if n.get("_resolved") != "rejected"]
        refine_goal, refine_context = _steer_refine(goal, context or {}, goal_notes)
        verdict = active_refine(refine_goal, refine_context, refine_carrier)
        if not verdict.get("sufficient"):
            missing = verdict.get("missing", [])
            emit({"type": "goal_underdetermined", "goal": goal, "missing": missing})
            ask = _ask_or_confirm(repo, "_goal", missing, verdict.get("structured"), goal, emit)
            asks[:] = _upsert_open_ask(asks, ask)
            if store is not None:                       # the org records the ASK as its terminal state
                store.update(goal_id, status="needs_info", missing=missing,
                             structured_goal=verdict.get("structured"), asks=asks, open_asks=[ask])
            if iso_wt is not None:                       # HOLD built nothing -> drop the empty goal worktree
                _cleanup_goal_worktree(orig_repo, iso_wt, goal_branch, delete_branch=True)
            return []                                    # HOLD: do not decompose (D1b)
        context = {**(context or {}), "structured_goal": verdict.get("structured")}
    plan = None
    if prior_record and prior_record.get("queue"):
        notes = _resolve_confirmations(_read_steering(store, steering_goal_ids), asks, emit)
        asks[:] = _repark_rejected_confirmations(asks, notes)
        blocked_prior = _blocked_ids(prior_record["queue"])
        answer_targets = {n.get("target") for n in notes
                          if n.get("target") in blocked_prior and n.get("_resolved") != "rejected"}
        if answer_targets:
            plan, reactivated = _reactivate_answered(prior_record["queue"], answer_targets)
            asks[:] = _mark_answered_asks(asks, notes, set(reactivated))
            emit({"type": "blocked_hitl_resumed", "goal_id": goal_id, "resume_from": resume_from,
                  "reactivated": reactivated})
            if store is not None:
                store.update(goal_id, queue=plan, asks=asks, open_asks=[a for a in asks if a.get("status") == "open"])
        elif blocked_prior:
            plan = prior_record["queue"]
            emit({"type": "blocked_hitl_waiting", "goal_id": goal_id, "resume_from": resume_from,
                  "blocked": sorted(blocked_prior)})
    if plan is None:
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

    def _cleanup_deferred_leaf(res):
        wt = (res or {}).get("leaf_worktree") if isinstance(res, dict) else None
        if wt and (res or {}).get("_cleanup_worktree"):
            subprocess.run(["git", "-C", str(repo), "worktree", "remove", "--force", str(wt)],
                           capture_output=True)
            shutil.rmtree(wt, ignore_errors=True)

    def _merge_deferred_leaf(leaf, res):
        wt = (res or {}).get("leaf_worktree") if isinstance(res, dict) else None
        if not wt:
            return res
        changed = git_ops.leaf_changed_files(wt)
        out_of_scope = _out_of_scope(changed, leaf.get("scope"))
        if out_of_scope:
            emit({"type": "leaf_scope_violation", "id": leaf["id"], "goal_id": goal_id,
                  "changed_files": changed, "out_of_scope": out_of_scope, "scope": leaf.get("scope") or []})
            return {**res, "outcome": "failed", "reason": "mechanical", "scope_violation": True,
                    "changed_files": changed, "out_of_scope": out_of_scope}
        sha = git_ops.merge_and_commit_leaf(repo, wt, leaf.get("id"), leaf.get("objective"))
        if sha is None:
            return {**res, "outcome": "failed", "reason": "mechanical"}
        return {**res, "commit": sha or None}

    def _run_leaf_future(exec_leaf, *, defer_merge):
        try:
            outcome = _call_leaf(run_leaf, repo, exec_leaf, goal_context=context, defer_merge=defer_merge)
            return outcome if isinstance(outcome, dict) else {"outcome": outcome}
        except Exception as exc:                              # noqa: BLE001 - fold crashes as leaf failures
            return {"outcome": "failed", "reason": "crash", "error": f"{type(exc).__name__}: {exc}"}

    def _fold_leaf_result(leaf, exec_leaf, res, *, defer_merge):
        nonlocal plan, spent, goal_aborted
        res = res if isinstance(res, dict) else {"outcome": res}
        # Within-wave disjointness is TEXTUAL safety only; semantic composition still rests on the
        # goal-acceptance profile, which is shadow/non-blocking unless intake authored an executable profile.
        try:
            if res.get("reason") == "crash":
                # A crash is SYSTEMIC (a harness/setup error — registry/imports/worktree), NOT a recoverable
                # per-leaf failure: re-split can't fix it. Restore the old serial path's FAIL-FAST — mark the
                # leaf failed and ABORT the goal (no new wave). In-flight sibling futures still finish + fold
                # cleanly (they run in their own worktrees); the abort is checked after the wave drains. At
                # AI_ORG_MAX_PARALLEL=1 the wave is one leaf, so this is exactly the old fail-fast-on-crash.
                emit({"type": "goal_blocked", "id": leaf["id"], "error": res.get("error"),
                      "detail": "a leaf crashed (systemic, not recoverable per-leaf work); the goal aborts "
                                "fail-fast after the in-flight wave drains. Sibling futures finish cleanly; "
                                "no new wave is dispatched."})
                plan = frontier.advance(plan, leaf["id"], "failed")
                goal_aborted = res.get("error") or "leaf crash"
                return
            if res.get("outcome") == "unverified":
                # FAIL-CLOSE (ADR-0011 / ADR-0016): the leaf converged but a required gate was left UNVERIFIED
                # (default_run_leaf honored the producer's verification_status). An unproven leaf is TERMINAL:
                # advance it "failed" — never resume it and never re-split it (that would send unproven work to
                # implementer repair / decomposition and could fabricate a pass). The goal then reports
                # partial/blocked rather than done, preserving goal-level outcome-honesty.
                emit({"type": "leaf_unverified", "id": leaf["id"], "goal_id": goal_id,
                      "unverified_gate_findings": res.get("unverified_gate_findings") or {}})
                plan = frontier.advance(plan, leaf["id"], "failed")
                return
            # RESUME: a non-quality (mechanical) failure — carrier timeout/hang, malformed output — is not
            # a granularity problem, so retry the SAME leaf on its preserved work; do NOT re-split.
            tries = 0
            prev_sig = _failure_sig(res)
            while res.get("outcome") != "converged" and res.get("reason") == "mechanical" \
                    and not res.get("scope_violation") \
                    and tries < MECH_RETRY_CAP and not (budget is not None and spent >= budget):
                tries += 1
                spent += 1
                emit({"type": "leaf_resume", "id": leaf["id"], "attempt": tries})
                outcome = _call_leaf(run_leaf, repo, exec_leaf, res.get("diff"), goal_context=context,
                                     defer_merge=defer_merge)
                res = outcome if isinstance(outcome, dict) else {"outcome": outcome}
                sig = _failure_sig(res)
                if res.get("outcome") != "converged" and sig is not None and sig == prev_sig:
                    emit({"type": "leaf_no_progress", "id": leaf["id"], "attempt": tries})
                    break        # same preserved work twice -> blind retry won't help; let floor/re-split run
                prev_sig = sig
            if res.get("outcome") == "converged":
                res = _merge_deferred_leaf(leaf, res)         # serial fold: scope guard + merge lock
            if res.get("scope_violation"):
                plan = frontier.advance(plan, leaf["id"], "failed")
                return
            if res.get("reason") == "crash":
                emit({"type": "goal_blocked", "id": leaf["id"], "error": res.get("error"),
                      "detail": "a resumed leaf crashed (systemic); the goal aborts fail-fast."})
                plan = frontier.advance(plan, leaf["id"], "failed")
                goal_aborted = res.get("error") or "leaf crash"
                return
            if res.get("outcome") == "converged":
                if store is not None:                          # AUDIT: record which codex session each role
                    for role, sid in (res.get("sessions") or {}).items():   # used on this leaf (repair reuse)
                        store.record_session(goal_id, leaf["id"], role, sid)
                plan = frontier.advance(plan, leaf["id"], "done")
                leaf_commits[leaf["id"]] = res.get("commit")   # this leaf's own commit (git scattered here)
                # rich log: carry the leaf's COMMIT sha (its build state), not just "it's done"
                emit({"type": "leaf_done", "id": leaf["id"], "commit": res.get("commit"), "goal_id": goal_id})
                if store is not None and goal_id:
                    try:
                        store.save_wip(goal_id, repo)
                    except Exception as e:                       # noqa: BLE001 - resume pointer is fail-soft
                        try:
                            emit({"type": "wip_save_failed", "goal_id": goal_id, "id": leaf["id"],
                                  "error": repr(e)})
                        except Exception:                         # noqa: BLE001 - observability is fail-soft
                            pass
                return
            depth = _depth_of(plan, leaf["id"]) or 0
            findings = res.get("findings")
            ss = leaf.get("_self_steer", 0)                  # self-steers already spent on this branch
            # a Linon rejection is a BAD REFERENCE: re-split, carrying its findings as retry CONTEXT so the
            # children do not repeat the rejected approach. A self-steer re-split additionally asks for a FINER
            # decomposition that resolves the findings (the org's own information, earning a fresh budget).
            child_ctx = {**(context or {}), "parent": leaf["id"]}
            if findings:
                child_ctx["prior_rejected_findings"] = findings
            if active_refine is not None:
                refine_carrier = carrier if refine is not None else codex_carrier(repo)
                notes = _resolve_confirmations(_steering_notes_for(store, steering_goal_ids, leaf, plan), asks, emit)
                notes = [n for n in notes if n.get("_resolved") != "rejected"]
                refine_goal, refine_context = _steer_refine(leaf["objective"], child_ctx, notes)
                verdict = active_refine(refine_goal, refine_context, refine_carrier)
                if not verdict.get("sufficient"):
                    missing = verdict.get("missing", [])
                    ask = _ask_or_confirm(repo, leaf["id"], missing, verdict.get("structured"),
                                          leaf.get("objective") or "", emit)
                    asks[:] = _upsert_open_ask(asks, ask)
                    plan = frontier.advance(plan, leaf["id"], "blocked_hitl")
                    if store is not None:
                        store.update(goal_id, asks=asks, open_asks=[a for a in asks if a.get("status") == "open"])
                    emit({"type": "leaf_underdetermined", "id": leaf["id"], "goal_id": goal_id,
                          "missing": missing, "structured": verdict.get("structured"),
                          "detail": "leaf is underdetermined; send back for definition rather than splitting"})
                    return
            # at the floor with a severe finding and self-steer budget left, the org STEERS ITSELF.
            if at_floor(leaf, depth) and not (findings and ss < _self_steer_cap(findings)):
                plan = frontier.advance(plan, leaf["id"], "failed")   # budget AND self-steer dry -> real floor
                emit({"type": "leaf_failed_floor", "id": leaf["id"], "depth": depth, "self_steers": ss})
                return
            self_steering = at_floor(leaf, depth)            # past the floor only because self-steer permits it
            if self_steering:
                child_ctx["self_steer"] = {"round": ss + 1, "instruction":
                    "This node FLOORED on the findings above. Produce a FINER decomposition whose sub-tasks "
                    "each resolve a specific part of those findings — smaller and more targeted than before — "
                    "rather than repeating the rejected approach."}
            linon_resplits = int(leaf.get("_linon_resplits") or 0)
            if (res.get("reason") == "linon" or findings) and linon_resplits >= LINON_RESPLIT_CAP:
                plan = frontier.advance(plan, leaf["id"], "failed")
                emit({"type": "leaf_failed_resplit_budget", "id": leaf["id"], "resplits": linon_resplits,
                      "cap": LINON_RESPLIT_CAP, "goal_id": goal_id})
                return
            children = split(leaf["objective"], child_ctx, carrier)
            if not children or frontier.validate_plan(children):
                plan = frontier.advance(plan, leaf["id"], "failed")   # dry split (incl. a dry self-steer) -> floor
                emit({"type": ("leaf_failed_floor" if self_steering else "split_unusable"),
                      "id": leaf["id"], **({"depth": depth, "self_steers": ss} if self_steering else {})})
                return
            if leaf.get("_scaffolded"):                  # re-split inside a scaffolded subtree stays scaffolded
                for c in children:
                    c["_scaffolded"] = True
            if self_steering:                            # carry the counter so the bound holds across rounds
                for c in children:
                    c["_self_steer"] = ss + 1
                emit({"type": "self_steer", "id": leaf["id"], "round": ss + 1, "n": len(children),
                      "depth": depth, "goal_id": goal_id})
            if res.get("reason") == "linon" or findings:
                for c in children:
                    c["_linon_resplits"] = linon_resplits + 1
            plan = _set_children(plan, leaf["id"], children)            # the leaf becomes an internal node
            plan = frontier.advance(plan, leaf["id"], "pending")
            emit({"type": "leaf_split", "id": leaf["id"], "n": len(children), "depth": depth, "goal_id": goal_id})
            _emit_splitter_speech(emit, leaf["id"], children)
        finally:
            _cleanup_deferred_leaf(res)

    spent = 0
    goal_aborted = None                                        # set by the fold when a systemic crash aborts
    defer_leaf_merge = _run_leaf_accepts_defer_merge(run_leaf)
    while True:
        ready = [t for t in frontier.ready_tasks(plan) if str(t.get("status") or "pending") == "pending"]
        if not ready:
            break
        requested_max_workers = _max_parallel()
        max_workers = requested_max_workers if defer_leaf_merge else 1
        dispatched = []
        for leaf in ready[:max_workers]:
            if budget is not None and spent >= budget:
                if dispatched:
                    break
                emit({"type": "budget_exhausted", "spent": spent})
                return _finalize(plan)
            spent += 1
            plan = frontier.advance(plan, leaf["id"], "running")
            emit({"type": "leaf_start", "id": leaf["id"], "goal_id": goal_id})   # goal_id attributes the node
            # fold any ADDITIVE STEERING (mid-run guidance) into THIS leaf at dispatch — no kill+re-fire.
            # exec_leaf carries the steered objective; the ORIGINAL leaf stays the plan/split source.
            exec_leaf = _apply_steering(store, goal_id, leaf, plan, steering_goal_ids)
            if exec_leaf is not leaf:
                emit({"type": "steer_applied", "id": leaf["id"], "goal_id": goal_id})
            # ADR-0008 Phase 2 scaffold fan-out remains in the serial dispatch/fold path because it mutates
            # the shared plan and git state before any leaf work is submitted.
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
            dispatched.append((leaf, exec_leaf))
        if not dispatched:
            continue
        if max_workers == 1:
            for leaf, exec_leaf in dispatched:
                res = _run_leaf_future(exec_leaf, defer_merge=defer_leaf_merge)
                _fold_leaf_result(leaf, exec_leaf, res, defer_merge=defer_leaf_merge)
        else:
            with concurrent.futures.ThreadPoolExecutor(max_workers=min(max_workers, len(dispatched))) as executor:
                futures = {executor.submit(_run_leaf_future, exec_leaf, defer_merge=True): (leaf, exec_leaf)
                           for leaf, exec_leaf in dispatched}
                for future in concurrent.futures.as_completed(futures):
                    leaf, exec_leaf = futures[future]
                    try:
                        res = future.result()
                    except Exception as exc:                   # noqa: BLE001 - defensive; workers also catch
                        res = {"outcome": "failed", "reason": "crash", "error": f"{type(exc).__name__}: {exc}"}
                    _fold_leaf_result(leaf, exec_leaf, res, defer_merge=True)
        if goal_aborted is not None:                           # a systemic crash -> fail-fast (old serial path)
            emit({"type": "goal_aborted", "error": goal_aborted,
                  "detail": "a systemic leaf crash aborted the goal; no further waves dispatched."})
            return _finalize(plan)
    emit({"type": "goal_done", "goal": goal})
    return _finalize(plan)


def main(argv=None) -> int:
    import argparse
    import json
    p = argparse.ArgumentParser(description="Run a GOAL through the org's autonomous builder (ADR-0008).")
    p.add_argument("--repo", required=True)
    p.add_argument("--goal", required=True)
    p.add_argument("--goal-id", default=None, help="the org records THIS goal's state under this id (it "
                   "owns its state); a consumer passes the id it dispatched with so it can read the org's state")
    p.add_argument("--resume-from", default=None, help="a prior goal_id (or sha/ref): the org LOADS that "
                   "state into the worktree before building, so it resumes its own prior work (org behavior)")
    p.add_argument("--budget", type=int, default=None, help="cap on total leaf runs (autonomous bound)")
    p.add_argument("--acceptance-profile", default=None, help="path to a JSON file holding the OWNER-authored "
                   "executable goal acceptance_profile (ADR-0016 D7): {start:{command,base_url?,ready_path?,"
                   "timeout?}, probes:[{request,expect}], negative_control?}. Fixed at intake; absent = the "
                   "goal-level acceptance stays shadow (no goal-level probe).")
    a = p.parse_args(argv)
    context = None
    if a.acceptance_profile:                           # the OWNER submits the executable acceptance contract
        context = {"acceptance_profile": json.loads(Path(a.acceptance_profile).read_text(encoding="utf-8"))}
    events = []                                        # capture the stream so the EXIT CODE can tell HOLD apart
    emitter = stream_emit(a.repo)
    def _emit(e):
        emitter(e)                                     # keep the default rich logging
        events.append(e)
    try:
        plan = run_goal(a.repo, a.goal, goal_id=a.goal_id, resume_from=a.resume_from, budget=a.budget,
                        context=context, emit=_emit)
    except LaunchPreconditionError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    # exit codes: 2 = HELD/sent back as underdetermined — the engine needs more info (ADR-0016 D1b), NOT a build;
    # 1 = built but not all done (or no plan); 0 = a real, non-empty, fully-done plan. A held goal must NOT
    # report success (an empty plan is `all([])==True` — that is why the non-empty check is explicit).
    if any(e.get("type") == "goal_underdetermined" for e in events):
        return 2
    if any(e.get("type") == "goal_finished" and e.get("status") == "blocked_hitl" for e in events):
        return 2
    if any(e.get("type") == "goal_finished" and e.get("status") == "needs_info" for e in events):
        return 2
    # A goal whose composed artifact FAILED goal-level acceptance is reported `goal_finished: failed` and is NOT
    # merged to main (~:902/929). Its leaves can all be `done`, so the all-leaves-done check below would wrongly
    # return 0 — an orchestrator/CI would treat an acceptance-blocked goal as success. Surface the failure.
    if any(e.get("type") == "goal_finished" and e.get("status") == "failed" for e in events):
        return 1
    return 0 if plan and all(frontier.node_status(t) == "done" for t in plan) else 1


if __name__ == "__main__":
    raise SystemExit(main())
