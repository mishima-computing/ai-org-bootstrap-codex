#!/usr/bin/env python3
"""controller_goal loop: split -> run leaves -> recurse on failure -> stop at the floor / budget.
Uses STUB split + run_leaf so the loop is proven without a carrier or the dialectic. Run:
  python3 scripts/test_controller_goal.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import controller_goal as cg


def _leaf(i, scope, deps=None):
    return {"id": i, "objective": f"do {i}", "scope": scope, "depends_on": deps or [],
            "status": "pending", "run_id": None, "pr_url": None}


# a passing intake-refiner stub (ADR-0016 D1b): the real-splitter path now goes through the sufficiency
# gate, so tests that exercise splitter.split inject this to clear the gate and reach decomposition.
def _ok_refine(goal, ctx, carrier):
    return {"sufficient": True, "missing": [],
            "structured": {"outcome": "o", "success_condition": "s", "negative_control": "n", "owner": "w"}}


def _statuses(plan):
    out = {}
    def walk(ts):
        for t in ts:
            out[t["id"]] = cg.frontier.node_status(t) if t.get("children") else t.get("status")
            if t.get("children"):
                walk(t["children"])
    walk(plan)
    return out


def test_all_leaves_converge():
    split = lambda goal, ctx, carrier: [_leaf("a", ["x.py"]), _leaf("b", ["y.py"])]
    events = []
    plan = cg.run_goal("/repo", "build it", run_leaf=lambda r, t: "converged",
                       split=split, emit=events.append)
    st = _statuses(plan)
    assert st == {"a": "done", "b": "done"}, st
    assert any(e["type"] == "goal_split" for e in events)
    print("ok  all leaves converge -> all done")


def test_failure_recurses_then_converges():
    # the top task has a 2-file scope (NOT atomic) so failing it triggers a split into atomic children
    calls = {"n": 0}
    def split(goal, ctx, carrier):
        calls["n"] += 1
        if calls["n"] == 1:
            return [_leaf("big", ["a.py", "b.py"])]          # not floor -> can split
        return [_leaf("big.1", ["a.py"]), _leaf("big.2", ["b.py"])]  # children (atomic)
    # 'big' fails (repair cap); its atomic children converge
    run_leaf = lambda r, t: "failed" if t["id"] == "big" else "converged"
    plan = cg.run_goal("/repo", "g", run_leaf=run_leaf, split=split)
    st = _statuses(plan)
    assert st.get("big.1") == "done" and st.get("big.2") == "done", st
    assert st.get("big") == "done", ("parent done when all children done", st)  # node_status derives it
    print("ok  failure -> split into atomic children -> converge (recursion + node_status)")


def test_split_output_shape_is_runnable():
    # REGRESSION: the real splitter.split() returns tasks WITHOUT a "status" key (only id/objective/
    # scope/depends_on). ready_tasks must treat a status-less leaf as pending, or the live builder splits
    # then runs nothing (goal_split -> goal_done, no leaf). The _leaf() fixture masked this by setting status.
    def split(goal, ctx, carrier):
        return [{"id": "a", "objective": "do a", "scope": ["x.py"], "depends_on": []},
                {"id": "b", "objective": "do b", "scope": ["y.py"], "depends_on": ["a"]}]
    events = []
    plan = cg.run_goal("/repo", "g", run_leaf=lambda r, t: "converged", split=split, emit=events.append)
    st = _statuses(plan)
    assert st == {"a": "done", "b": "done"}, st
    assert any(e["type"] == "leaf_done" for e in events), ("a leaf must actually run", events)
    print("ok  status-less split output (the real splitter shape) is runnable -> leaves run")


def test_floor_stops_recursion():
    # an ATOMIC task (single file) that keeps failing must NOT split forever -> fail at the floor
    split = lambda goal, ctx, carrier: [_leaf("atom", ["only.py"])]
    plan = cg.run_goal("/repo", "g", run_leaf=lambda r, t: "failed", split=split)
    assert _statuses(plan) == {"atom": "failed"}, _statuses(plan)
    print("ok  atomic failing leaf fails at the floor (no infinite split, no human)")


def test_declared_smallest_is_floor():
    # a task that NAMES itself minimal/scaffold, with a MULTI-file scope (so the len(scope)<=1 floor does
    # NOT apply), must still fail at the floor and never split — honoring the "minimal" claim stops the
    # infinite 'minimal -> minimal' regression (the scaffold loop).
    split = lambda goal, ctx, carrier: [_leaf("minimal-package-scaffold", ["a.py", "b.py", "c.py"])]
    plan = cg.run_goal("/repo", "g", run_leaf=lambda r, t: "failed", split=split)
    assert _statuses(plan) == {"minimal-package-scaffold": "failed"}, _statuses(plan)
    assert cg.at_floor({"id": "x", "objective": "scaffold the project", "scope": ["a", "b"]}, 0)
    assert not cg.at_floor({"id": "x", "objective": "add feature", "scope": ["a", "b"]}, 0)
    print("ok  self-declared 'minimal'/scaffold fails at the floor, never splits (no infinite regression)")


def test_mechanical_failure_resumes_same_leaf():
    # a non-quality (mechanical) failure retries the SAME leaf (resume), and must NOT re-split.
    seq = iter([{"outcome": "failed", "reason": "mechanical", "diff": None}, {"outcome": "converged"}])
    split = lambda goal, ctx, carrier: [_leaf("m", ["x.py"])]
    events = []
    plan = cg.run_goal("/repo", "g", run_leaf=lambda r, t: next(seq), split=split, emit=events.append)
    assert _statuses(plan) == {"m": "done"}, _statuses(plan)
    assert any(e["type"] == "leaf_resume" for e in events), "mechanical fail should RESUME"
    assert not any(e["type"] == "leaf_split" for e in events), "mechanical fail must not re-split"
    print("ok  mechanical (non-Linon) failure resumes the same leaf, no re-split")


def test_crash_fails_loud_and_aborts_without_burning_budget():
    # a CRASH (a harness/setup error, NOT carrier work) must NOT be retried/re-split into the floor: the goal
    # fails LOUDLY with a goal_blocked event carrying the error, the top split is the ONLY split, and it
    # finishes "failed". Regression guard for the silent swallow that masked a missing registry / AI_ORG_ROOT.
    splits = []

    def split(goal, ctx, carrier):
        splits.append(goal)
        return [_leaf("a", ["x.py", "y.py"]), _leaf("b", ["z.py"])]   # multi-file: COULD re-split if not aborted

    run_leaf = lambda r, t: {"outcome": "failed", "reason": "crash", "error": "FileNotFoundError: registry yaml"}
    events = []
    plan = cg.run_goal("/repo", "g", run_leaf=run_leaf, split=split, emit=events.append)
    blocked = [e for e in events if e["type"] == "goal_blocked"]
    assert blocked and "FileNotFoundError" in blocked[0]["error"], events
    assert len(splits) == 1, f"a crash must abort, not keep re-splitting: {splits}"
    fin = [e for e in events if e["type"] == "goal_finished"]
    assert fin and fin[0]["status"] == "failed", events
    print("ok  a crash fails loud (goal_blocked) and aborts without re-splitting/burning budget")


def test_default_run_leaf_surfaces_a_crash_not_silently():
    # default_run_leaf must EMIT the exception (leaf_crash) and return reason="crash" with the detail, never
    # swallow it as a quiet "mechanical" failure (which masked the real error for an hour in practice).
    import os
    import subprocess
    import tempfile
    repo = tempfile.mkdtemp()
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "-C", repo, "init", "-q"], capture_output=True)
    subprocess.run(["git", "-C", repo, "commit", "--allow-empty", "-m", "base", "-q"], capture_output=True, env=env)
    events = []
    orig = cg.stream_emit
    cg.stream_emit = lambda r: events.append           # capture the per-leaf stream emitter
    try:
        res = cg.default_run_leaf(repo, {"id": "x", "objective": "o"},
                                  run_pipeline=lambda wt, obj, run_id: _raise(FileNotFoundError("registry yaml")))
    finally:
        cg.stream_emit = orig
    assert res["outcome"] == "failed" and res["reason"] == "crash", res
    assert "FileNotFoundError" in res["error"], res
    assert any(e.get("type") == "leaf_crash" for e in events), events
    print("ok  default_run_leaf surfaces a crash (leaf_crash + reason=crash), never swallows it")


def test_launch_precondition_fails_before_split_when_registry_missing():
    # A direct launch with the default leaf runner needs the org registry. If AI_ORG_ROOT is missing on a
    # cross-repo run, fail BEFORE the splitter/codex path starts, with a distinct precondition event.
    import os
    import tempfile
    old_org = os.environ.pop("AI_ORG_ROOT", None)
    try:
        with tempfile.TemporaryDirectory() as repo:
            split_called = {"v": False}
            events = []

            def split(_goal, _ctx, _carrier):
                split_called["v"] = True
                return [_leaf("late", ["x.py"])]

            try:
                cg.run_goal(repo, "g", split=split, emit=events.append)
                raise AssertionError("missing registry must abort launch")
            except cg.LaunchPreconditionError as exc:
                msg = str(exc)
            assert "AI Org precondition failed: runtime registry not found at" in msg, msg
            assert "Set AI_ORG_ROOT to the engine install" in msg, msg
            assert "registry/runtime-registry.yaml in --repo itself" in msg, msg
            assert split_called["v"] is False, "precondition must fire before split/codex is attempted"
            ev = [e for e in events if e.get("type") == "precondition_failed"]
            assert ev and ev[0].get("runtime_registry", "").endswith("registry/runtime-registry.yaml"), events
    finally:
        if old_org is None:
            os.environ.pop("AI_ORG_ROOT", None)
        else:
            os.environ["AI_ORG_ROOT"] = old_org
    print("ok  missing runtime registry fails launch before split with an actionable precondition event")


def test_launch_precondition_passes_with_ai_org_root_before_split():
    # Negative control for cross-repo builds: the build repo has no registry, but AI_ORG_ROOT points at the
    # engine install, so the cheap precondition passes and run_goal reaches the split normally.
    import os
    import tempfile
    old_org = os.environ.get("AI_ORG_ROOT")
    os.environ["AI_ORG_ROOT"] = str(Path(__file__).resolve().parent.parent)
    try:
        with tempfile.TemporaryDirectory() as repo:
            split_called = {"v": False}

            def split(_goal, _ctx, _carrier):
                split_called["v"] = True
                return []

            plan = cg.run_goal(repo, "g", split=split, emit=lambda _e: None)
            assert plan == [], plan
            assert split_called["v"] is True, "valid AI_ORG_ROOT should allow the launch to reach split"
    finally:
        if old_org is None:
            os.environ.pop("AI_ORG_ROOT", None)
        else:
            os.environ["AI_ORG_ROOT"] = old_org
    print("ok  AI_ORG_ROOT pointing at the engine makes the launch precondition pass before split")


def test_launch_precondition_passes_self_hosted_without_ai_org_root():
    # Self-hosted remains valid: when --repo is the engine repo and AI_ORG_ROOT is unset, org_root(repo)==repo
    # and the local registry satisfies the launch precondition.
    import os
    old_org = os.environ.pop("AI_ORG_ROOT", None)
    try:
        path = cg.check_launch_preconditions(Path(__file__).resolve().parent.parent)
        assert path.name == "runtime-registry.yaml" and path.is_file(), path
    finally:
        if old_org is None:
            os.environ.pop("AI_ORG_ROOT", None)
        else:
            os.environ["AI_ORG_ROOT"] = old_org
    print("ok  self-hosted launch precondition passes without AI_ORG_ROOT")


def _raise(exc):
    raise exc


def test_linon_failure_carries_findings_to_children():
    # a Linon rejection (bad reference) re-splits AND passes its findings to the children's split context.
    seen = {}
    def split(goal, ctx, carrier):
        seen.update(ctx)
        return [_leaf("big", ["a.py", "b.py"])] if "parent" not in ctx else [_leaf("big.1", ["a.py"])]
    def run_leaf(r, t):
        if t["id"] == "big":
            return {"outcome": "failed", "reason": "linon", "findings": ["NN2: unverified claim"]}
        return "converged"
    plan = cg.run_goal("/repo", "g", run_leaf=run_leaf, split=split)
    assert _statuses(plan).get("big.1") == "done", _statuses(plan)
    assert seen.get("prior_rejected_findings") == ["NN2: unverified claim"], seen
    print("ok  Linon rejection re-splits and carries its findings as retry context")


def test_budget_stops():
    split = lambda goal, ctx, carrier: [_leaf("a", ["x.py"]), _leaf("b", ["y.py"]), _leaf("c", ["z.py"])]
    plan = cg.run_goal("/repo", "g", run_leaf=lambda r, t: "converged", split=split, budget=1)
    done = sum(1 for v in _statuses(plan).values() if v == "done")
    assert done == 1, ("budget=1 -> only one leaf ran", _statuses(plan))
    print("ok  budget caps total leaf runs (autonomous bound, not a human)")


def test_stream_emit_appends():
    import tempfile, json
    with tempfile.TemporaryDirectory() as d:
        emit = cg.stream_emit(d)
        emit({"type": "a"})
        emit({"type": "b", "id": "x"})
        lines = (Path(d) / ".agent-runs" / "stream.jsonl").read_text(encoding="utf-8").splitlines()
        evs = [json.loads(line) for line in lines]
        assert [e["type"] for e in evs] == ["a", "b"], evs
        assert evs[1]["id"] == "x"
        print("ok  stream_emit appends events to the shared log (ADR-0009)")


def test_run_goal_streams_to_log():
    import tempfile, json
    with tempfile.TemporaryDirectory() as d:
        split = lambda goal, ctx, carrier: [_leaf("a", ["x.py"])]
        cg.run_goal(d, "g", run_leaf=lambda r, t: "converged", split=split)  # default emit -> the log
        evs = [json.loads(line) for line in
               (Path(d) / ".agent-runs" / "stream.jsonl").read_text(encoding="utf-8").splitlines()]
        types = [e["type"] for e in evs]
        assert "goal_split" in types and "leaf_done" in types, types
        print("ok  run_goal streams goal/leaf events to the shared log by default")


def test_goal_id_makes_the_org_own_its_state():
    # the received goal becomes the ORG's state at receipt: with a goal_id, run_goal records the goal,
    # commits its build (wip), and its outcome in its own GoalStore — independent of any consumer.
    import tempfile, os, subprocess, goal_store
    def git(r, *a): subprocess.run(["git", "-C", str(r), *a], capture_output=True)
    with tempfile.TemporaryDirectory() as d:
        repo = os.path.join(d, "r"); os.mkdir(repo)
        git(repo, "init", "-b", "main"); git(repo, "config", "user.email", "t@t"); git(repo, "config", "user.name", "t")
        open(os.path.join(repo, "seed.txt"), "w").write("x"); git(repo, "add", "-A"); git(repo, "commit", "-m", "base")
        split = lambda goal, ctx, carrier: [{"id": "a", "objective": "do a", "scope": ["x.py"], "depends_on": []}]
        def run_leaf(r, t):
            open(os.path.join(repo, "x.py"), "w").write("done\n")
            return "converged"
        events = []
        cg.run_goal(repo, "build it", run_leaf=run_leaf, split=split, goal_id="goal-own1", emit=events.append)
        rec = goal_store.GoalStore(repo).read("goal-own1")            # Read = safe observe (not Load)
        assert rec and rec["status"] == "done" and rec["goal"] == "build it", rec
        assert rec.get("wip"), ("the org records its build state (wip commit)", rec)
        # rich log: the org's terminal state (status + wip) flows into its own Stream
        gf = [e for e in events if e.get("type") == "goal_finished"]
        assert gf and gf[0]["status"] == "done" and gf[0]["wip"] == rec["wip"], gf
        # leaf lifecycle events carry goal_id, so a consumer (the town / steer UI) attributes each Queue
        # node to its goal — the basis for picking a node to steer.
        assert any(e.get("type") == "leaf_start" and e.get("goal_id") == "goal-own1" for e in events), \
            "leaf_start carries goal_id"
        print("ok  goal_id -> the org owns its goal state (record + wip) AND flows it to its rich log")


def test_converged_leaf_updates_wip_before_goal_finalize():
    # RESUME REGRESSION: a mid-run interruption after leaf A lands must be resumable from A's committed state.
    # This assertion runs while dependent leaf B is executing, before _finalize() can write the terminal wip.
    import tempfile, os, subprocess, goal_store
    def git(r, *a):
        return subprocess.run(["git", "-C", str(r), *a], check=True, capture_output=True, text=True)
    with tempfile.TemporaryDirectory() as d:
        repo = os.path.join(d, "r"); os.mkdir(repo)
        git(repo, "init", "-b", "main"); git(repo, "config", "user.email", "t@t"); git(repo, "config", "user.name", "t")
        open(os.path.join(repo, "seed.txt"), "w").write("x"); git(repo, "add", "-A"); git(repo, "commit", "-m", "base")
        base = git(repo, "rev-parse", "HEAD").stdout.strip()
        split = lambda goal, ctx, carrier: [
            {"id": "a", "objective": "do a", "scope": ["a.py"], "depends_on": []},
            {"id": "b", "objective": "do b", "scope": ["b.py"], "depends_on": ["a"]},
        ]
        events = []
        seen = {}

        def run_leaf(r, t):
            if t["id"] == "a":
                open(os.path.join(r, "a.py"), "w").write("landed a\n")
                return "converged"
            rec = goal_store.GoalStore(repo).read("goal-mid-wip") or {}
            seen["record"] = rec
            seen["ref"] = git(repo, "rev-parse", "--verify", "refs/goals/goal-mid-wip/wip").stdout.strip()
            seen["goal_finished_before_b"] = any(e.get("type") == "goal_finished" for e in events)
            w2 = os.path.join(d, "resume-target")
            git(repo, "worktree", "add", "-q", "--detach", w2, base)
            seen["loaded"] = goal_store.GoalStore(repo).load("goal-mid-wip", w2)
            seen["loaded_a"] = os.path.isfile(os.path.join(w2, "a.py"))
            return "failed"

        plan = cg.run_goal(repo, "build it", run_leaf=run_leaf, split=split,
                           goal_id="goal-mid-wip", emit=events.append)
        rec = seen.get("record") or {}
        assert seen.get("goal_finished_before_b") is False, events
        assert rec.get("wip"), ("wip must be set after leaf A lands, before goal finalization", rec)
        assert seen.get("ref") == rec["wip"], (seen, rec)
        assert seen.get("loaded") and seen.get("loaded_a"), ("store.load can resume leaf A mid-run", seen)
        assert _statuses(plan) == {"a": "done", "b": "failed"}, _statuses(plan)
    print("ok  each landed leaf updates durable wip before goal finalization (mid-run resume pointer)")


def test_additive_steering_reaches_the_dispatched_leaf():
    # a steering note added to a RUNNING goal is folded into the objective the leaf-runner actually
    # executes — the org consumes mid-run guidance at dispatch, with NO kill + re-fire (no work discarded).
    import tempfile, os, subprocess, goal_store
    def git(r, *a): subprocess.run(["git", "-C", str(r), *a], capture_output=True)
    seen = []
    def run_leaf(r, t): seen.append(t["objective"]); return {"outcome": "converged", "commit": None}
    with tempfile.TemporaryDirectory() as d:
        repo = os.path.join(d, "r"); os.mkdir(repo)
        git(repo, "init", "-b", "main"); git(repo, "config", "user.email", "t@t"); git(repo, "config", "user.name", "t")
        open(os.path.join(repo, "seed.txt"), "w").write("x"); git(repo, "add", "-A"); git(repo, "commit", "-m", "base")
        st = goal_store.GoalStore(repo)
        st.create("goal-steer1", "build it", "codex")
        st.steer("goal-steer1", "prefer official tools")             # injected BEFORE the leaf is dispatched
        cg.run_goal(repo, "build it", run_leaf=run_leaf, goal_id="goal-steer1",
                    split=lambda goal, ctx, carrier: [{"id": "a", "objective": "do a", "scope": ["x.py"], "depends_on": []}])
    assert seen and "prefer official tools" in seen[0], ("steering folded into the dispatched leaf", seen)
    assert "do a" in seen[0], "the original objective is preserved, the steering is appended"
    print("ok  additive steering reaches the dispatched leaf's objective (no kill+re-fire)")


def test_node_targeted_steering_hits_only_its_queue_node():
    # NODE-targeted steering (a Queue node) reaches only that leaf, not its siblings — goal-level alone is
    # the degenerate "whole Queue" case; targeting a node in the Queue is the point.
    import tempfile, os, subprocess, goal_store
    def git(r, *a): subprocess.run(["git", "-C", str(r), *a], capture_output=True)
    seen = {}
    def run_leaf(r, t): seen[t["id"]] = t["objective"]; return {"outcome": "converged", "commit": None}
    with tempfile.TemporaryDirectory() as d:
        repo = os.path.join(d, "r"); os.mkdir(repo)
        git(repo, "init", "-b", "main"); git(repo, "config", "user.email", "t@t"); git(repo, "config", "user.name", "t")
        open(os.path.join(repo, "seed.txt"), "w").write("x"); git(repo, "add", "-A"); git(repo, "commit", "-m", "base")
        st = goal_store.GoalStore(repo)
        st.create("goal-tgt", "build it", "codex")
        st.steer("goal-tgt", "rework only this node", target="b")        # targets the Queue node "b" only
        cg.run_goal(repo, "build it", run_leaf=run_leaf, goal_id="goal-tgt",
                    split=lambda g, c, ca: [{"id": "a", "objective": "do a", "scope": ["x.py"], "depends_on": []},
                                            {"id": "b", "objective": "do b", "scope": ["y.py"], "depends_on": []}])
    assert "rework only this node" in seen["b"], ("the targeted Queue node gets it", seen)
    assert "rework only this node" not in seen["a"], ("a sibling node does NOT", seen)
    print("ok  node-targeted steering reaches only its Queue node, not siblings")


def test_scaffold_fanout_scopes_children_in_base_and_suppresses_re_scaffold():
    # ADR-0008 Phase 2 guards (found live on B/packaging): a scaffolded leaf fans out its logic INTO the
    # base; a child whose scope DRIFTED outside the base (implement/, replace/) is dropped; and the logic
    # children do NOT re-scaffold (no deep recursive re-seeding to the floor).
    import tempfile, os, subprocess
    def git(r, *a): subprocess.run(["git", "-C", str(r), *a], capture_output=True)
    ran, tagged = [], []
    def run_leaf(r, t): ran.append(t["id"]); tagged.append(t.get("_scaffolded")); return {"outcome": "converged", "commit": None}
    def split(goal, ctx, carrier):
        if ctx.get("parent"):   # fan-out: two in-base children + one that DRIFTED outside the base
            assert "scope MUST be a path under" in goal, "fan-out objective constrains scope to the base"
            return [{"id": "logic-bundler", "objective": "build the bundler", "scope": ["marketplace/packaging/bundler.py"], "depends_on": []},
                    {"id": "logic-manifest", "objective": "build the manifest", "scope": ["marketplace/packaging/manifest.py"], "depends_on": []},
                    {"id": "logic-drift", "objective": "build core", "scope": ["implement/core.py"], "depends_on": []}]
        return [{"id": "packaging", "objective": "scaffold the packaging python package", "scope": ["marketplace/packaging/"], "depends_on": []}]
    with tempfile.TemporaryDirectory() as d:
        repo = os.path.join(d, "r"); os.mkdir(repo)
        git(repo, "init", "-b", "main"); git(repo, "config", "user.email", "t@t"); git(repo, "config", "user.name", "t")
        open(os.path.join(repo, "seed.txt"), "w").write("x"); git(repo, "add", "-A"); git(repo, "commit", "-m", "base")
        events = []
        cg.run_goal(repo, "build it", run_leaf=run_leaf, split=split, emit=events.append)
    fan = [e for e in events if e.get("type") == "scaffold_fanout"]
    # G1: the scope-drifter is dropped -> only the two in-base children fan out and run
    assert fan and fan[0]["n"] == 2, ("only in-base children fan out", fan)
    assert "logic-drift" not in ran and "logic-bundler" in ran and "logic-manifest" in ran, ran
    # G2: the logic children are tagged _scaffolded and do NOT re-scaffold (exactly one seed, no recursion)
    assert ran and all(tagged), ("logic children are tagged _scaffolded", ran, tagged)
    assert sum(1 for e in events if e.get("type") == "scaffold_seeded") == 1, "no recursive re-scaffold"
    print("ok  scaffold fan-out scopes children in-base (drift dropped) + suppresses re-scaffold (ADR-0008)")


def test_no_progress_breaks_the_blind_retry_loop():
    # ADR-0008: two consecutive resumes that preserve the SAME diff make no progress -> stop blind-retrying
    # (a deterministic failure won't yield to a blind retry), instead of burning the whole MECH_RETRY budget.
    import tempfile, os
    calls = {"n": 0}
    box = {"diff": None}
    def run_leaf(r, t, resume_diff=None):
        calls["n"] += 1
        return {"outcome": "failed", "reason": "mechanical", "diff": box["diff"]}   # same preserved work each time
    with tempfile.TemporaryDirectory() as d:
        box["diff"] = os.path.join(d, "x.patch"); open(box["diff"], "w").write("SAME FAILING WORK\n")
        events = []
        cg.run_goal("/repo", "build it", run_leaf=run_leaf, emit=events.append,
                    split=lambda g, c, ca: [{"id": "a", "objective": "do a", "scope": ["x.py"], "depends_on": []}])
    assert any(e.get("type") == "leaf_no_progress" for e in events), "no-progress must be detected"
    assert calls["n"] == 2, ("loop stops after one no-progress retry, not the full MECH_RETRY cap", calls["n"])
    print("ok  no-progress (same diff twice) breaks the blind-retry loop (ADR-0008)")


def test_splitter_speech_streams_as_agent_message():
    # log-is-the-state-source: goal_split carries only a COUNT; the decomposition itself (the splitter's
    # speech) must ride the stream so a consumer can show "what the splitter said" without scraping an ephemeral
    # leaf worktree. The decomposition is emitted as an `agent_message` with source="splitter".
    import tempfile
    plan_out = [_leaf("a", ["x.py"]), _leaf("b", ["y.py"])]
    events = []
    with tempfile.TemporaryDirectory() as d:
        cg.run_goal(d, "build it", run_leaf=lambda r, t: "converged",
                    split=lambda g, c, ca: plan_out, emit=events.append, goal_id="goal-spk")
    speeches = [e for e in events if e.get("type") == "agent_message" and e.get("source") == "splitter"]
    assert speeches, "splitter must stream its decomposition as an agent_message"
    sp = speeches[0]
    assert sp.get("run_id") == "goal-spk", sp
    ids = {t.get("id") for t in (sp.get("speech") or [])}
    assert ids == {"a", "b"}, ("the streamed speech IS the task DAG the splitter produced", sp.get("speech"))
    print("ok  splitter streams its decomposition as an agent_message (log-is-the-state-source)")


def test_leaf_sessions_recorded_per_leaf_role():
    # the per-role codex session ids a leaf used are recorded in state, keyed leaf×role (repair-reuse audit).
    import tempfile, os, subprocess, goal_store
    def git(r, *a): subprocess.run(["git", "-C", str(r), *a], capture_output=True)
    with tempfile.TemporaryDirectory() as d:
        repo = os.path.join(d, "r"); os.mkdir(repo)
        git(repo, "init", "-b", "main"); git(repo, "config", "user.email", "t@t"); git(repo, "config", "user.name", "t")
        open(os.path.join(repo, "seed.txt"), "w").write("x"); git(repo, "add", "-A"); git(repo, "commit", "-m", "base")
        split = lambda goal, ctx, carrier: [{"id": "a", "objective": "do a", "scope": ["x.py"], "depends_on": []}]
        run_leaf = lambda r, t: {"outcome": "converged", "commit": None,
                                 "sessions": {"genius": "sid-g", "implementer": "sid-i"}}
        cg.run_goal(repo, "g", run_leaf=run_leaf, split=split, goal_id="goal-sess")
        s = (goal_store.GoalStore(repo).read("goal-sess") or {}).get("sessions") or {}
        assert s.get("a:genius") == "sid-g" and s.get("a:implementer") == "sid-i", s
    print("ok  per-leaf×role codex sessions recorded in state (repair-reuse audit)")


def test_resume_feeds_restored_inventory_to_the_resplit():
    # resume re-splits FRESH (frontier not restored); it tells the splitter what the Load restored so the
    # re-split is idempotent against it (build on / patch, don't recreate).
    import tempfile, os, subprocess, goal_store
    def git(r, *a): subprocess.run(["git", "-C", str(r), *a], capture_output=True)
    with tempfile.TemporaryDirectory() as d:
        repo = os.path.join(d, "r"); os.mkdir(repo)
        git(repo, "init", "-b", "main"); git(repo, "config", "user.email", "t@t"); git(repo, "config", "user.name", "t")
        open(os.path.join(repo, "seed.txt"), "w").write("x"); git(repo, "add", "-A"); git(repo, "commit", "-m", "base")
        st = goal_store.GoalStore(repo); st.create("goal-prior", "g", org="")
        os.makedirs(os.path.join(repo, "mocks")); open(os.path.join(repo, "mocks", "a.py"), "w").write("y\n")
        git(repo, "add", "-A"); git(repo, "commit", "-m", "leaf"); st.save_wip("goal-prior", repo)
        git(repo, "reset", "--hard", "HEAD~1")
        seen = {}
        def split(goal, ctx, carrier):
            seen["ctx"] = ctx
            return [{"id": "a", "objective": "do a", "scope": ["mocks/a.py"], "depends_on": []}]
        cg.run_goal(repo, "g", run_leaf=lambda r, t: "converged", split=split,
                    goal_id="goal-new", resume_from="goal-prior")
        rpw = (seen.get("ctx") or {}).get("resumed_prior_work")
        assert rpw and "mocks/a.py" in rpw["files"], rpw
    print("ok  resume feeds the re-split its restored-file inventory (idempotent re-split)")


def test_self_steer_pushes_a_critical_finding_past_floor_then_floors():
    # ADR-0008 addendum: at the floor with a CRITICAL finding, the org SELF-STEERS a finer re-split up to the
    # severity-weighted counter (critical -> 2), then floors honestly — no human, deterministic bound.
    crit = [{"severity": "critical", "title": "import context lost"}]
    def split(goal, ctx, carrier):
        rnd = (ctx.get("self_steer") or {}).get("round", 0)        # unique child id per self-steer round
        return [_leaf(f"a{rnd}", ["a.py"])]                        # atomic (scope==1) -> always at the floor
    run_leaf = lambda r, t: {"outcome": "failed", "reason": "linon", "findings": crit}
    events = []
    cg.run_goal("/repo", "g", run_leaf=run_leaf, split=split, emit=events.append)
    ss = [e for e in events if e.get("type") == "self_steer"]
    ff = [e for e in events if e.get("type") == "leaf_failed_floor"]
    assert [e["round"] for e in ss] == [1, 2], ("critical self-steers up to cap=2", [e.get("round") for e in ss])
    assert ff, "after the self-steer counter is exhausted it floors for REAL"
    # a minor finding gets NO self-steer (cap 0) — it floors immediately
    events2 = []
    cg.run_goal("/repo", "g", run_leaf=lambda r, t: {"outcome": "failed", "reason": "linon",
                "findings": [{"severity": "minor"}]},
                split=lambda g, c, ca: [_leaf("m", ["m.py"])], emit=events2.append)
    assert not [e for e in events2 if e.get("type") == "self_steer"], "minor finding -> no self-steer"
    assert [e for e in events2 if e.get("type") == "leaf_failed_floor"], "minor -> straight to floor"
    print("ok  self-steer: critical pushed past floor (bounded), minor floored immediately (severity budget)")


def test_declared_boundary_steers_the_split():
    # #48: a goal that declares "inside X/ ONLY" feeds the splitter a scope_boundary so it scopes the plan
    # under X/ from the start (the prose path otherwise drifted to docs/).
    seen = {}
    def split(goal, ctx, carrier):
        seen["ctx"] = ctx
        return [_leaf("a", ["mocks/a.py"])]
    cg.run_goal("/repo", "Build it, keep ALL changes inside mocks/ ONLY.",
                run_leaf=lambda r, t: "converged", split=split, emit=lambda e: None)
    sb = (seen.get("ctx") or {}).get("scope_boundary")
    assert sb and sb.get("dir") == "mocks", ("the declared boundary is injected into the split context", sb)
    print("ok  declared boundary ('inside X/ ONLY') steers the split via scope_boundary context")


def test_splitter_session_resumes_on_resume():
    # the splitter session is recorded on the initial split and RESUMED on a later resume (the splitter keeps
    # its planning memory; the frontier stays non-restored).
    import tempfile, os, subprocess, goal_store, splitter
    def git(r, *a): subprocess.run(["git", "-C", str(r), *a], capture_output=True)
    seen = {"resume": []}
    real = cg.codex_carrier
    def fake(repo, *, model=None, resume_session=None):
        seen["resume"].append(resume_session)
        c = lambda prompt: '[{"id":"a","objective":"do a","scope":["mocks/a.py"],"depends_on":[]}]'
        c.captured = {"session_id": "splitsid-0"}
        return c
    old_org = os.environ.get("AI_ORG_ROOT")
    os.environ["AI_ORG_ROOT"] = str(Path(__file__).resolve().parent.parent)
    cg.codex_carrier = fake
    try:
        with tempfile.TemporaryDirectory() as d:
            repo = os.path.join(d, "r"); os.mkdir(repo)
            for a in (["init", "-b", "main"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
                git(repo, *a)
            open(os.path.join(repo, "seed.txt"), "w").write("x"); git(repo, "add", "-A"); git(repo, "commit", "-m", "base")
            cg.run_goal(repo, "build inside mocks/ ONLY", run_leaf=lambda r, t: "converged",
                        split=splitter.split, refine=_ok_refine, goal_id="goal-A")
            assert ((goal_store.GoalStore(repo).read("goal-A") or {}).get("sessions") or {}).get("_goal:splitter") \
                == "splitsid-0", "initial run records the splitter session"
            seen["resume"].clear()
            cg.run_goal(repo, "build inside mocks/ ONLY", run_leaf=lambda r, t: "converged",
                        split=splitter.split, refine=_ok_refine, goal_id="goal-B", resume_from="goal-A")
            assert "splitsid-0" in seen["resume"], ("the resumed top split RESUMES the prior splitter session",
                                                    seen["resume"])
    finally:
        if old_org is None:
            os.environ.pop("AI_ORG_ROOT", None)
        else:
            os.environ["AI_ORG_ROOT"] = old_org
        cg.codex_carrier = real
    print("ok  splitter session recorded on initial split, RESUMED on resume (planning memory kept)")


def test_intake_gate_holds_underdetermined_goal():
    # ADR-0016 D1b NEGATIVE CONTROL: an underdetermined goal must NOT be decomposed. Pre-gate, run_goal had
    # no `refine` and ALWAYS split -> this assertion FAILS without the gate (red), passes with it (green).
    split_calls = {"n": 0}
    def split(goal, ctx, carrier):
        split_calls["n"] += 1
        return [_leaf("a", ["x.py"])]
    insufficient = lambda g, c, k: {"sufficient": False, "structured": {"outcome": ""},
                                    "missing": ["outcome", "owner"]}
    events = []
    leaf_ran = {"n": 0}
    plan = cg.run_goal("/repo", "make it nice", run_leaf=lambda r, t: leaf_ran.__setitem__("n", leaf_ran["n"] + 1),
                       split=split, refine=insufficient, emit=events.append)
    assert split_calls["n"] == 0, "an underdetermined goal must NOT be decomposed (HOLD)"
    assert leaf_ran["n"] == 0, "nothing is built for a held goal"
    assert plan == [], plan
    und = [e for e in events if e["type"] == "goal_underdetermined"]
    assert und and set(und[0]["missing"]) == {"outcome", "owner"}, events
    assert not any(e["type"] == "goal_split" for e in events), "no split event on a held goal"
    print("ok  intake gate HOLDS an underdetermined goal, no decomposition (ADR-0016 D1b negative control)")


def test_intake_gate_proceeds_and_threads_structured_goal():
    # a sufficient goal proceeds to decomposition AND the named structured WHY is threaded into the splitter.
    seen_ctx = {}
    def split(goal, ctx, carrier):
        seen_ctx.update(ctx)
        return [_leaf("a", ["x.py"])]
    structured = {"outcome": "X", "success_condition": "Y", "negative_control": "Z", "owner": "W"}
    sufficient = lambda g, c, k: {"sufficient": True, "structured": structured, "missing": []}
    plan = cg.run_goal("/repo", "do X", run_leaf=lambda r, t: "converged", split=split, refine=sufficient)
    assert _statuses(plan) == {"a": "done"}, plan
    assert seen_ctx.get("structured_goal") == structured, ("the structured WHY is threaded into split", seen_ctx)
    print("ok  sufficient goal proceeds + threads the structured WHY into split")


def test_intake_gate_records_needs_info_in_store():
    # the ASK is the org's terminal state: a held goal_id run records status=needs_info + the missing fields.
    import tempfile, os, subprocess, goal_store
    def git(r, *a): subprocess.run(["git", "-C", str(r), *a], capture_output=True)
    insufficient = lambda g, c, k: {"sufficient": False, "structured": {"outcome": ""}, "missing": ["owner"]}
    split = lambda g, c, k: [_leaf("a", ["x.py"])]
    with tempfile.TemporaryDirectory() as d:
        repo = os.path.join(d, "r"); os.mkdir(repo)
        git(repo, "init", "-b", "main"); git(repo, "config", "user.email", "t@t"); git(repo, "config", "user.name", "t")
        open(os.path.join(repo, "seed.txt"), "w").write("x"); git(repo, "add", "-A"); git(repo, "commit", "-m", "base")
        plan = cg.run_goal(repo, "vague", run_leaf=lambda r, t: "converged",
                           split=split, refine=insufficient, goal_id="goal-h")
        assert plan == [], plan
        rec = goal_store.GoalStore(repo).read("goal-h") or {}
        assert rec.get("status") == "needs_info", rec
        assert rec.get("missing") == ["owner"], rec
    print("ok  underdetermined goal_id run records status=needs_info (the org records the ASK)")


def test_leaf_underdetermined_parks_and_siblings_keep_running():
    # A failed, underdetermined leaf becomes blocked_hitl, not failed; independent siblings still run, and the
    # ASK is durable/plural in the goal record. This fails if the old scalar needs_info + failed path returns.
    import tempfile, os, subprocess, goal_store
    def git(r, *a): subprocess.run(["git", "-C", str(r), *a], capture_output=True)
    def refine(g, ctx, carrier):
        if ctx.get("parent") == "blocked":
            return {"sufficient": False, "structured": {"outcome": ""}, "missing": ["owner"]}
        return _ok_refine(g, ctx, carrier)
    split = lambda g, c, k: [_leaf("blocked", ["a.py", "b.py"]), _leaf("sibling", ["c.py"])]
    def run_leaf(r, t):
        return {"outcome": "failed", "reason": "linon", "findings": [{"severity": "major"}]} \
            if t["id"] == "blocked" else "converged"
    with tempfile.TemporaryDirectory() as d:
        repo = os.path.join(d, "r"); os.mkdir(repo)
        git(repo, "init", "-b", "main"); git(repo, "config", "user.email", "t@t"); git(repo, "config", "user.name", "t")
        open(os.path.join(repo, "seed.txt"), "w").write("x"); git(repo, "add", "-A"); git(repo, "commit", "-m", "base")
        events = []
        plan = cg.run_goal(repo, "g", run_leaf=run_leaf, split=split, refine=refine,
                           goal_id="goal-leaf-ask", emit=events.append)
        rec = goal_store.GoalStore(repo).read("goal-leaf-ask") or {}
    st = _statuses(plan)
    assert st["blocked"] == "blocked_hitl" and st["sibling"] == "done", st
    assert not any(e.get("type") == "leaf_split" for e in events), events
    assert any(e.get("type") == "goal_finished" and e.get("status") == "blocked_hitl" for e in events), events
    assert not any(e.get("type") == "goal_finished" and e.get("status") == "failed" for e in events), events
    asks = rec.get("asks") or []
    assert asks and asks[0]["node_id"] == "blocked" and asks[0]["status"] == "open", rec
    print("ok  underdetermined leaf parks as blocked_hitl; sibling still runs; ASK is durable")


def test_blocked_hitl_outranks_failed_and_drives_main_exit_2():
    # REGRESSION: old finalization chose failed before blocked_hitl. With one hard failed leaf and one parked
    # open ASK, that old order persisted status=failed, omitted result, and made main() exit 1.
    import json, os, subprocess, tempfile, goal_store

    def git(r, *a):
        subprocess.run(["git", "-C", str(r), *a], check=True, capture_output=True)

    def make_repo(root, name):
        repo = os.path.join(root, name)
        os.mkdir(repo)
        git(repo, "init", "-b", "main")
        git(repo, "config", "user.email", "t@t")
        git(repo, "config", "user.name", "t")
        open(os.path.join(repo, "seed.txt"), "w").write("x")
        git(repo, "add", "-A")
        git(repo, "commit", "-m", "base")
        return repo

    def mixed_split(_goal, _ctx, _carrier):
        return [_leaf("hard_failed", ["failed.py"]), _leaf("needs_owner", ["ask.py"])]

    def mixed_run_leaf(_repo, task):
        return {"outcome": "failed", "reason": "linon"} if task["id"] == "needs_owner" else "failed"

    def mixed_refine(_goal, ctx, _carrier):
        if ctx.get("parent") == "needs_owner":
            return {"sufficient": False, "structured": {"outcome": "o"}, "missing": ["owner"]}
        return _ok_refine(_goal, ctx, _carrier)

    old_stream = os.environ.pop("STREAM_LOG", None)
    old_org = os.environ.get("AI_ORG_ROOT")
    os.environ["AI_ORG_ROOT"] = str(Path(__file__).resolve().parent.parent)
    try:
        with tempfile.TemporaryDirectory() as d:
            repo = make_repo(d, "run")
            events = []
            plan = cg.run_goal(repo, "g", run_leaf=mixed_run_leaf, split=mixed_split, refine=mixed_refine,
                               goal_id="mixed-precedence", emit=events.append)
            rec = goal_store.GoalStore(repo).read("mixed-precedence") or {}
            assert _statuses(plan) == {"hard_failed": "failed", "needs_owner": "blocked_hitl"}, _statuses(plan)
            assert rec.get("status") == "blocked_hitl", rec
            assert rec.get("result"), rec
            assert any(a.get("node_id") == "needs_owner" and a.get("status") == "open"
                       for a in rec.get("open_asks") or []), rec
            assert any(e.get("type") == "goal_finished" and e.get("status") == "blocked_hitl"
                       for e in events), events

            os.environ.pop("STREAM_LOG", None)
            repo_for_main = make_repo(d, "main")
            real_codex = cg.codex_carrier
            real_default_run_leaf = cg.default_run_leaf

            def fake_codex(_repo, *, model=None, resume_session=None):
                def carrier(prompt):
                    if "Decompose the goal into a child task DAG" in prompt:
                        return json.dumps([
                            {"id": "hard_failed", "objective": "do hard_failed",
                             "scope": ["failed.py"], "depends_on": []},
                            {"id": "needs_owner", "objective": "do needs_owner",
                             "scope": ["ask.py"], "depends_on": []},
                        ])
                    if "parent': 'needs_owner'" in prompt or '"parent": "needs_owner"' in prompt:
                        return json.dumps({"outcome": "o", "success_condition": "s",
                                           "negative_control": "n", "owner": "", "intent": "i"})
                    return json.dumps({"outcome": "o", "success_condition": "s",
                                       "negative_control": "n", "owner": "w", "intent": "i"})
                carrier.captured = {}
                return carrier

            cg.codex_carrier = fake_codex
            cg.default_run_leaf = mixed_run_leaf
            try:
                code = cg.main(["--repo", repo_for_main, "--goal", "g", "--goal-id", "mixed-main"])
            finally:
                cg.default_run_leaf = real_default_run_leaf
                cg.codex_carrier = real_codex
            assert code == 2, f"main() must return exit 2 when an open ask outranks a failed sibling, got {code}"
    finally:
        if old_org is None:
            os.environ.pop("AI_ORG_ROOT", None)
        else:
            os.environ["AI_ORG_ROOT"] = old_org
        if old_stream is None:
            os.environ.pop("STREAM_LOG", None)
        else:
            os.environ["STREAM_LOG"] = old_stream
    print("ok  blocked_hitl outranks failed, stores result, and main exits 2")


def test_at_floor_underdetermined_leaf_is_asked_not_failed():
    # The sufficiency check must run BEFORE the floor decision. An atomic/minimal underdetermined leaf is asked
    # and parked, not leaf_failed_floor/failed.
    def refine(g, ctx, carrier):
        if ctx.get("parent") == "atom":
            return {"sufficient": False, "structured": {}, "missing": ["negative_control"]}
        return _ok_refine(g, ctx, carrier)
    split = lambda g, c, k: [_leaf("atom", ["only.py"])]
    events = []
    plan = cg.run_goal("/repo", "g", run_leaf=lambda r, t: {"outcome": "failed", "reason": "linon"},
                       split=split, refine=refine, emit=events.append)
    assert _statuses(plan) == {"atom": "blocked_hitl"}, _statuses(plan)
    assert any(e.get("type") == "leaf_underdetermined" for e in events), events
    assert not any(e.get("type") == "leaf_failed_floor" for e in events), events
    print("ok  at-floor underdetermined leaf is ASKED/parked before floor failure can fire")


def test_answer_reactivates_parked_node_and_reaches_refine():
    # Resume must restore the parked frontier, re-activate only the answered node, and thread the answer into
    # the node's refine call. A fresh top split would leave `top_split_called` true and fail this test.
    import tempfile, os, subprocess, goal_store
    def git(r, *a): subprocess.run(["git", "-C", str(r), *a], capture_output=True)
    with tempfile.TemporaryDirectory() as d:
        repo = os.path.join(d, "r"); os.mkdir(repo)
        git(repo, "init", "-b", "main"); git(repo, "config", "user.email", "t@t"); git(repo, "config", "user.name", "t")
        open(os.path.join(repo, "seed.txt"), "w").write("x"); git(repo, "add", "-A"); git(repo, "commit", "-m", "base")
        st = goal_store.GoalStore(repo)
        st.create("goal-prior", "g", org="")
        prior_queue = [_leaf("blocked", ["a.py", "b.py"])]
        prior_queue[0]["status"] = "blocked_hitl"
        st.update("goal-prior", status="blocked_hitl", queue=prior_queue,
                  asks=[{"node_id": "blocked", "missing": ["owner"], "question": "owner?",
                         "structured": {}, "status": "open"}])
        st.steer("goal-prior", "Answer: QA owns this; acceptance rejects missing audit output.", target="blocked")
        top_split_called = {"v": False}
        refine_calls = []
        def refine(g, ctx, carrier):
            refine_calls.append((g, ctx))
            if ctx.get("parent") == "blocked":
                assert "QA owns this" in g or "QA owns this" in str(ctx), (g, ctx)
            return _ok_refine(g, ctx, carrier)
        def split(g, ctx, carrier):
            if not ctx.get("parent"):
                top_split_called["v"] = True
                return [_leaf("wrong-top", ["wrong.py"])]
            return [_leaf("blocked.child", ["a.py"])]
        def run_leaf(r, t):
            return {"outcome": "failed", "reason": "linon", "findings": [{"severity": "major"}]} \
                if t["id"] == "blocked" else "converged"
        events = []
        plan = cg.run_goal(repo, "g", run_leaf=run_leaf, split=split, refine=refine,
                           goal_id="goal-resumed", resume_from="goal-prior", emit=events.append)
        rec = goal_store.GoalStore(repo).read("goal-resumed") or {}
    assert not top_split_called["v"], "answered blocked frontier should be restored, not top-split fresh"
    assert any(e.get("type") == "blocked_hitl_resumed" and e.get("reactivated") == ["blocked"] for e in events), events
    assert any(ctx.get("parent") == "blocked" for _, ctx in refine_calls), refine_calls
    assert _statuses(plan).get("blocked.child") == "done", _statuses(plan)
    assert (rec.get("asks") or [{}])[0].get("status") == "answered", rec
    print("ok  supplied answer un-parks the node and is delivered into that node's refine")


def _write_doc(root, rel, text):
    path = Path(root) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_ask_search_single_adr_hit_emits_confirm_not_bare():
    import tempfile, os, subprocess, goal_store
    def git(r, *a): subprocess.run(["git", "-C", str(r), *a], capture_output=True)
    old_gh = os.environ.get("AOB_ASK_SEARCH_GH")
    os.environ["AOB_ASK_SEARCH_GH"] = "0"
    try:
        with tempfile.TemporaryDirectory() as d:
            repo = os.path.join(d, "r"); os.mkdir(repo)
            git(repo, "init", "-b", "main")
            _write_doc(repo, "docs/decisions/ADR-0999.md", "# ADR-0999\n\nOwner: Platform QA.")
            insufficient = lambda g, c, k: {"sufficient": False, "structured": {}, "missing": ["owner"]}
            events = []
            cg.run_goal(repo, "build the owner-gated feature", run_leaf=lambda r, t: "converged",
                        split=lambda g, c, k: [_leaf("a", ["x.py"])], refine=insufficient,
                        goal_id="goal-confirm", emit=events.append)
            rec = goal_store.GoalStore(repo).read("goal-confirm") or {}
        ask = (rec.get("open_asks") or [])[0]
        assert ask.get("kind") == "confirm", ask
        assert ask.get("candidates") and ask["candidates"][0]["source_ref"] == "docs/decisions/ADR-0999.md", ask
        assert "Confirm `owner`" in ask.get("question", ""), ask
        assert any(e.get("type") == "confirm_requested" for e in events), events
    finally:
        if old_gh is None:
            os.environ.pop("AOB_ASK_SEARCH_GH", None)
        else:
            os.environ["AOB_ASK_SEARCH_GH"] = old_gh
    print("ok  underdetermined ask with one ADR hit becomes confirm, not bare")


def test_ask_search_conflicting_adr_hits_emit_disambiguation():
    import tempfile, os, subprocess, goal_store
    def git(r, *a): subprocess.run(["git", "-C", str(r), *a], capture_output=True)
    old_gh = os.environ.get("AOB_ASK_SEARCH_GH")
    os.environ["AOB_ASK_SEARCH_GH"] = "0"
    real = cg.ask_search.propose_candidates
    try:
        with tempfile.TemporaryDirectory() as d:
            repo = os.path.join(d, "r"); os.mkdir(repo)
            git(repo, "init", "-b", "main")
            _write_doc(repo, "docs/decisions/ADR-1001.md", "Owner: Platform QA.")
            _write_doc(repo, "docs/decisions/ADR-1002.md", "Owner: Release Engineering.")

            def fake_propose(missing, structured, objective, passages):
                return [
                    {"field": "owner", "value": "Owner: Platform QA.",
                     "source_ref": "docs/decisions/ADR-1001.md", "excerpt": "Owner: Platform QA."},
                    {"field": "owner", "value": "Owner: Release Engineering.",
                     "source_ref": "docs/decisions/ADR-1002.md", "excerpt": "Owner: Release Engineering."},
                ]

            cg.ask_search.propose_candidates = fake_propose
            insufficient = lambda g, c, k: {"sufficient": False, "structured": {}, "missing": ["owner"]}
            cg.run_goal(repo, "build it", run_leaf=lambda r, t: "converged",
                        split=lambda g, c, k: [_leaf("a", ["x.py"])], refine=insufficient,
                        goal_id="goal-conflict")
            rec = goal_store.GoalStore(repo).read("goal-conflict") or {}
        ask = (rec.get("open_asks") or [])[0]
        q = ask.get("question", "")
        assert ask.get("kind") == "disambiguate", ask
        assert "docs/decisions/ADR-1001.md" in q and "docs/decisions/ADR-1002.md" in q, q
        assert "Conflicting candidates" in q and len(ask.get("candidates") or []) == 2, ask
    finally:
        cg.ask_search.propose_candidates = real
        if old_gh is None:
            os.environ.pop("AOB_ASK_SEARCH_GH", None)
        else:
            os.environ["AOB_ASK_SEARCH_GH"] = old_gh
    print("ok  contradictory ADR candidates surface side-by-side for human disambiguation")


def test_ask_search_no_result_is_byte_identical_bare_ask():
    import tempfile, os, subprocess, goal_store
    def git(r, *a): subprocess.run(["git", "-C", str(r), *a], capture_output=True)
    old_gh = os.environ.get("AOB_ASK_SEARCH_GH")
    os.environ["AOB_ASK_SEARCH_GH"] = "0"
    try:
        with tempfile.TemporaryDirectory() as d:
            repo = os.path.join(d, "r"); os.mkdir(repo)
            git(repo, "init", "-b", "main")
            insufficient = lambda g, c, k: {"sufficient": False, "structured": {}, "missing": ["owner"]}
            cg.run_goal(repo, "vague", run_leaf=lambda r, t: "converged",
                        split=lambda g, c, k: [_leaf("a", ["x.py"])], refine=insufficient,
                        goal_id="goal-bare")
            rec = goal_store.GoalStore(repo).read("goal-bare") or {}
        assert (rec.get("open_asks") or [])[0] == cg._make_ask("_goal", ["owner"], {}), rec
    finally:
        if old_gh is None:
            os.environ.pop("AOB_ASK_SEARCH_GH", None)
        else:
            os.environ["AOB_ASK_SEARCH_GH"] = old_gh
    print("ok  no search result falls back to the existing bare ask byte-for-byte")


def test_ask_search_fabricated_provenance_is_rejected_to_bare():
    import tempfile, os, subprocess, goal_store
    def git(r, *a): subprocess.run(["git", "-C", str(r), *a], capture_output=True)
    old_gh = os.environ.get("AOB_ASK_SEARCH_GH")
    os.environ["AOB_ASK_SEARCH_GH"] = "0"
    real = cg.ask_search.propose_candidates
    try:
        with tempfile.TemporaryDirectory() as d:
            repo = os.path.join(d, "r"); os.mkdir(repo)
            git(repo, "init", "-b", "main")
            _write_doc(repo, "docs/decisions/ADR-1003.md", "Owner: Real Team.")
            cg.ask_search.propose_candidates = lambda m, s, o, p: [
                {"field": "owner", "value": "Owner: Fake Team.",
                 "source_ref": "docs/decisions/ADR-DOES-NOT-EXIST.md", "excerpt": "Owner: Fake Team."}
            ]
            insufficient = lambda g, c, k: {"sufficient": False, "structured": {}, "missing": ["owner"]}
            events = []
            cg.run_goal(repo, "vague", run_leaf=lambda r, t: "converged",
                        split=lambda g, c, k: [_leaf("a", ["x.py"])], refine=insufficient,
                        goal_id="goal-fabricated", emit=events.append)
            rec = goal_store.GoalStore(repo).read("goal-fabricated") or {}
        assert (rec.get("open_asks") or [])[0] == cg._make_ask("_goal", ["owner"], {}), rec
        assert any(e.get("type") == "ask_candidate_rejected" and e.get("reason") == "bad_provenance"
                   for e in events), events
    finally:
        cg.ask_search.propose_candidates = real
        if old_gh is None:
            os.environ.pop("AOB_ASK_SEARCH_GH", None)
        else:
            os.environ["AOB_ASK_SEARCH_GH"] = old_gh
    print("ok  fabricated candidate provenance is rejected visibly and falls back to bare")


def test_confirmation_affirm_threads_candidate_value_and_marks_confirmed():
    import tempfile, os, subprocess, goal_store
    def git(r, *a): subprocess.run(["git", "-C", str(r), *a], capture_output=True)
    with tempfile.TemporaryDirectory() as d:
        repo = os.path.join(d, "r"); os.mkdir(repo)
        git(repo, "init", "-b", "main"); git(repo, "config", "user.email", "t@t"); git(repo, "config", "user.name", "t")
        open(os.path.join(repo, "seed.txt"), "w").write("x"); git(repo, "add", "-A"); git(repo, "commit", "-m", "base")
        st = goal_store.GoalStore(repo); st.create("prior-confirm", "g", org="")
        prior_queue = [_leaf("blocked", ["a.py", "b.py"])]; prior_queue[0]["status"] = "blocked_hitl"
        candidate = {"field": "owner", "value": "Owner: Platform QA.",
                     "source_ref": "docs/decisions/ADR-1001.md", "excerpt": "Owner: Platform QA."}
        st.update("prior-confirm", status="blocked_hitl", queue=prior_queue,
                  asks=[{"node_id": "blocked", "missing": [], "kind": "confirm",
                         "original_missing": ["owner"], "candidates": [candidate],
                         "question": "confirm?", "structured": {}, "status": "open"}])
        st.steer("prior-confirm", "yes", target="blocked")
        seen = []
        def refine(g, ctx, carrier):
            seen.append((g, ctx))
            if ctx.get("parent") == "blocked":
                assert "Owner: Platform QA." in g or "Owner: Platform QA." in str(ctx), (g, ctx)
            return _ok_refine(g, ctx, carrier)
        def split(g, ctx, carrier):
            return [_leaf("blocked.child", ["a.py"])] if ctx.get("parent") else [_leaf("wrong", ["x.py"])]
        def run_leaf(r, t):
            return {"outcome": "failed", "reason": "linon", "findings": [{"severity": "major"}]} \
                if t["id"] == "blocked" else "converged"
        cg.run_goal(repo, "g", run_leaf=run_leaf, split=split, refine=refine,
                    goal_id="goal-confirmed", resume_from="prior-confirm")
        rec = goal_store.GoalStore(repo).read("goal-confirmed") or {}
    ask = (rec.get("asks") or [])[0]
    assert ask.get("status") == "answered" and ask.get("resolved") == "confirmed", rec
    assert ask.get("answer") == "Owner: Platform QA.", rec
    assert any(ctx.get("parent") == "blocked" for _, ctx in seen), seen
    print("ok  affirmative confirmation threads candidate value into refine and records resolved=confirmed")


def test_confirmation_reject_reparks_as_bare_ask():
    import tempfile, os, subprocess, goal_store
    def git(r, *a): subprocess.run(["git", "-C", str(r), *a], capture_output=True)
    with tempfile.TemporaryDirectory() as d:
        repo = os.path.join(d, "r"); os.mkdir(repo)
        git(repo, "init", "-b", "main")
        st = goal_store.GoalStore(repo); st.create("prior-reject", "g", org="")
        prior_queue = [_leaf("blocked", ["a.py", "b.py"])]; prior_queue[0]["status"] = "blocked_hitl"
        st.update("prior-reject", status="blocked_hitl", queue=prior_queue,
                  asks=[{"node_id": "blocked", "missing": [], "kind": "confirm",
                         "original_missing": ["owner"], "candidates": [{"field": "owner", "value": "Owner: X"}],
                         "question": "confirm?", "structured": {}, "status": "open"}])
        st.steer("prior-reject", "no", target="blocked")
        events = []
        plan = cg.run_goal(repo, "g", run_leaf=lambda r, t: "converged",
                           split=lambda g, c, k: [_leaf("wrong", ["x.py"])], refine=_ok_refine,
                           goal_id="goal-rejected", resume_from="prior-reject", emit=events.append)
        rec = goal_store.GoalStore(repo).read("goal-rejected") or {}
    ask = (rec.get("open_asks") or [])[0]
    assert _statuses(plan) == {"blocked": "blocked_hitl"}, _statuses(plan)
    assert "kind" not in ask and "candidates" not in ask, ask
    assert ask.get("missing") == ["owner"] and ask.get("question") == cg._ask_question("blocked", ["owner"]), ask
    assert any(e.get("type") == "confirmation_rejected" for e in events), events
    print("ok  rejected confirmation keeps node parked and re-surfaces a bare ask")


def test_confirmation_correction_is_free_text_answer():
    import tempfile, os, subprocess, goal_store
    def git(r, *a): subprocess.run(["git", "-C", str(r), *a], capture_output=True)
    with tempfile.TemporaryDirectory() as d:
        repo = os.path.join(d, "r"); os.mkdir(repo)
        git(repo, "init", "-b", "main"); git(repo, "config", "user.email", "t@t"); git(repo, "config", "user.name", "t")
        open(os.path.join(repo, "seed.txt"), "w").write("x"); git(repo, "add", "-A"); git(repo, "commit", "-m", "base")
        st = goal_store.GoalStore(repo); st.create("prior-correct", "g", org="")
        prior_queue = [_leaf("blocked", ["a.py", "b.py"])]; prior_queue[0]["status"] = "blocked_hitl"
        st.update("prior-correct", status="blocked_hitl", queue=prior_queue,
                  asks=[{"node_id": "blocked", "missing": [], "kind": "confirm",
                         "original_missing": ["owner"], "candidates": [{"field": "owner", "value": "Owner: X"}],
                         "question": "confirm?", "structured": {}, "status": "open"}])
        st.steer("prior-correct", "Owner: Platform instead.", target="blocked")
        seen = []
        def refine(g, ctx, carrier):
            seen.append((g, ctx))
            if ctx.get("parent") == "blocked":
                assert "Owner: Platform instead." in g or "Owner: Platform instead." in str(ctx), (g, ctx)
                assert "Owner: X" not in g, (g, ctx)
            return _ok_refine(g, ctx, carrier)
        def split(g, ctx, carrier):
            return [_leaf("blocked.child", ["a.py"])] if ctx.get("parent") else [_leaf("wrong", ["x.py"])]
        def run_leaf(r, t):
            return {"outcome": "failed", "reason": "linon", "findings": [{"severity": "major"}]} \
                if t["id"] == "blocked" else "converged"
        cg.run_goal(repo, "g", run_leaf=run_leaf, split=split, refine=refine,
                    goal_id="goal-corrected", resume_from="prior-correct")
        rec = goal_store.GoalStore(repo).read("goal-corrected") or {}
    ask = (rec.get("asks") or [])[0]
    assert ask.get("resolved") == "corrected" and ask.get("answer") == "Owner: Platform instead.", rec
    assert any(ctx.get("parent") == "blocked" for _, ctx in seen), seen
    print("ok  non-affirmative correction flows as free-text answer and records resolved=corrected")


def test_disambiguation_reply_threads_selected_candidate_value():
    import tempfile, os, subprocess, goal_store
    def git(r, *a): subprocess.run(["git", "-C", str(r), *a], capture_output=True)
    with tempfile.TemporaryDirectory() as d:
        repo = os.path.join(d, "r"); os.mkdir(repo)
        git(repo, "init", "-b", "main"); git(repo, "config", "user.email", "t@t"); git(repo, "config", "user.name", "t")
        open(os.path.join(repo, "seed.txt"), "w").write("x"); git(repo, "add", "-A"); git(repo, "commit", "-m", "base")
        st = goal_store.GoalStore(repo); st.create("prior-disambig", "g", org="")
        prior_queue = [_leaf("blocked", ["a.py", "b.py"])]; prior_queue[0]["status"] = "blocked_hitl"
        c1 = {"field": "owner", "value": "Owner: Platform QA.", "source_ref": "docs/decisions/ADR-1001.md"}
        c2 = {"field": "owner", "value": "Owner: Release Engineering.", "source_ref": "docs/decisions/ADR-1002.md"}
        st.update("prior-disambig", status="blocked_hitl", queue=prior_queue,
                  asks=[{"node_id": "blocked", "missing": ["owner"], "kind": "disambiguate",
                         "original_missing": ["owner"], "candidates": [c1, c2],
                         "conflicts": [{"field": "owner", "candidates": [c1, c2]}],
                         "question": "which?", "structured": {}, "status": "open"}])
        st.steer("prior-disambig", "Use ADR-1002", target="blocked")
        seen = []
        def refine(g, ctx, carrier):
            seen.append((g, ctx))
            if ctx.get("parent") == "blocked":
                assert "Owner: Release Engineering." in g or "Owner: Release Engineering." in str(ctx), (g, ctx)
                assert "Owner: Platform QA." not in g, (g, ctx)
            return _ok_refine(g, ctx, carrier)
        def split(g, ctx, carrier):
            return [_leaf("blocked.child", ["a.py"])] if ctx.get("parent") else [_leaf("wrong", ["x.py"])]
        def run_leaf(r, t):
            return {"outcome": "failed", "reason": "linon", "findings": [{"severity": "major"}]} \
                if t["id"] == "blocked" else "converged"
        cg.run_goal(repo, "g", run_leaf=run_leaf, split=split, refine=refine,
                    goal_id="goal-disambiguated", resume_from="prior-disambig")
        rec = goal_store.GoalStore(repo).read("goal-disambiguated") or {}
    ask = (rec.get("asks") or [])[0]
    assert ask.get("resolved") == "confirmed" and ask.get("answer") == "Owner: Release Engineering.", rec
    assert any(ctx.get("parent") == "blocked" for _, ctx in seen), seen
    print("ok  disambiguation reply selects a listed candidate and flows through the normal answer path")


def test_ask_search_github_failure_degrades_to_empty_candidates():
    real_infer = cg.ask_search._infer_repo_query
    real_run = cg.ask_search.run_gh
    try:
        events = []
        cg.ask_search._infer_repo_query = lambda repo: "repo:owner/repo"
        cg.ask_search.run_gh = lambda args, timeout=30.0: (_raise(cg.ask_search.GhError("boom")))
        out = cg.ask_search.search_candidates("/repo", "_goal", ["owner"], {}, "g", emit=events.append)
        assert out == {"candidates": [], "conflicts": []}, out
        assert any(e.get("type") == "ask_search_tier_failed" for e in events), events
    finally:
        cg.ask_search._infer_repo_query = real_infer
        cg.ask_search.run_gh = real_run
    print("ok  GitHub search failure is fail-soft and degrades to no candidates")


def test_ask_search_disabled_is_pure_bare_without_calling_search():
    import tempfile, os, subprocess, goal_store
    def git(r, *a): subprocess.run(["git", "-C", str(r), *a], capture_output=True)
    old = os.environ.get("AOB_ASK_SEARCH")
    os.environ["AOB_ASK_SEARCH"] = "0"
    real = cg.ask_search.search_candidates
    try:
        cg.ask_search.search_candidates = lambda *a, **k: (_raise(AssertionError("search should be disabled")))
        with tempfile.TemporaryDirectory() as d:
            repo = os.path.join(d, "r"); os.mkdir(repo)
            git(repo, "init", "-b", "main")
            insufficient = lambda g, c, k: {"sufficient": False, "structured": {}, "missing": ["owner"]}
            cg.run_goal(repo, "vague", run_leaf=lambda r, t: "converged",
                        split=lambda g, c, k: [_leaf("a", ["x.py"])], refine=insufficient,
                        goal_id="goal-disabled")
            rec = goal_store.GoalStore(repo).read("goal-disabled") or {}
        assert (rec.get("open_asks") or [])[0] == cg._make_ask("_goal", ["owner"], {}), rec
    finally:
        cg.ask_search.search_candidates = real
        if old is None:
            os.environ.pop("AOB_ASK_SEARCH", None)
        else:
            os.environ["AOB_ASK_SEARCH"] = old
    print("ok  AOB_ASK_SEARCH=0 keeps the pure bare-ask path and does not invoke search")


def test_linion_resplit_budget_bounds_nameable_loop():
    # Even when refine says each leaf is well-defined/nameable, Linon rejection re-splits are explicitly bounded
    # by a counter, not only by depth.
    def split(g, ctx, carrier):
        parent = ctx.get("parent")
        return [_leaf("root" if not parent else parent + ".next", ["a.py", "b.py"])]
    run_leaf = lambda r, t: {"outcome": "failed", "reason": "linon", "findings": [{"severity": "major"}]}
    events = []
    cg.run_goal("/repo", "g", run_leaf=run_leaf, split=split, refine=_ok_refine, emit=events.append)
    splits = [e for e in events if e.get("type") == "leaf_split"]
    budget = [e for e in events if e.get("type") == "leaf_failed_resplit_budget"]
    assert len(splits) == cg.LINON_RESPLIT_CAP, (splits, events)
    assert budget and budget[0]["resplits"] == cg.LINON_RESPLIT_CAP, events
    print("ok  Linon re-split loop is bounded by explicit budget even when refine is sufficient")


def test_well_defined_linion_failure_still_resplits_normally():
    # NEGATIVE CONTROL: the new budget must not suppress the normal first Linon re-split for a sufficient leaf.
    calls = {"n": 0}
    def split(g, ctx, carrier):
        calls["n"] += 1
        return [_leaf("big", ["a.py", "b.py"])] if not ctx.get("parent") else [_leaf("big.child", ["a.py"])]
    def run_leaf(r, t):
        return {"outcome": "failed", "reason": "linon", "findings": [{"severity": "major"}]} \
            if t["id"] == "big" else "converged"
    events = []
    plan = cg.run_goal("/repo", "g", run_leaf=run_leaf, split=split, refine=_ok_refine, emit=events.append)
    assert any(e.get("type") == "leaf_split" and e.get("id") == "big" for e in events), events
    assert _statuses(plan).get("big.child") == "done", _statuses(plan)
    print("ok  well-defined Linon failure still performs the normal re-split")


def test_goal_acceptance_shadow_on_done():
    # ADR-0016 D7: a done goal carrying a structured WHY emits a SHADOW goal_acceptance (verified=False,
    # needs_info) — the composed outcome was NOT checked against the WHY. NEGATIVE CONTROL: pre-D7 run_goal
    # emitted no such record, so this assertion fails without the change (red) and passes with it (green).
    sufficient = lambda g, c, k: {"sufficient": True, "missing": [], "structured": {
        "outcome": "O", "success_condition": "S", "negative_control": "N", "owner": "W"}}
    split = lambda g, c, k: [_leaf("a", ["x.py"])]
    events = []
    cg.run_goal("/repo", "do O", run_leaf=lambda r, t: "converged",
                split=split, refine=sufficient, emit=events.append)
    acc = [e for e in events if e["type"] == "goal_acceptance"]
    assert acc, ("a done goal with a WHY emits a goal_acceptance shadow record", events)
    assert acc[0]["verified"] is False and acc[0]["status"] == "needs_info", acc
    assert acc[0]["negative_control"] == "N", acc
    assert any(e["type"] == "goal_finished" and e["status"] == "done" for e in events)
    # the record must precede goal_finished is not required; presence + shape is the contract
    print("ok  done goal emits SHADOW goal_acceptance (verified=False, needs_info) — D7, no fabricated green")


def test_no_goal_acceptance_without_why():
    # without a structured WHY (no refine injected) there is nothing to verify the composed outcome against,
    # so NO goal_acceptance record is emitted (the shadow record is only meaningful with a named acceptance).
    split = lambda g, c, k: [_leaf("a", ["x.py"])]
    events = []
    cg.run_goal("/repo", "g", run_leaf=lambda r, t: "converged", split=split, emit=events.append)
    assert not any(e["type"] == "goal_acceptance" for e in events), events
    print("ok  no WHY -> no goal_acceptance record (nothing to verify against)")


def test_no_goal_acceptance_on_failed_goal():
    # pins the `done` term: a FAILED goal (WITH a WHY) emits NO goal_acceptance — the shadow record is only
    # for a goal that reached `done`. A regression dropping the `done and` guard would emit on failure -> red here.
    sufficient = lambda g, c, k: {"sufficient": True, "missing": [], "structured": {
        "outcome": "O", "success_condition": "S", "negative_control": "N", "owner": "W"}}
    split = lambda g, c, k: [_leaf("a", ["x.py"])]      # atomic single-file -> floors on repeated failure
    events = []
    cg.run_goal("/repo", "do O", run_leaf=lambda r, t: "failed",
                split=split, refine=sufficient, emit=events.append)
    assert not any(e["type"] == "goal_acceptance" for e in events), ("no acceptance on a non-done goal", events)
    assert any(e["type"] == "goal_finished" and e["status"] == "failed" for e in events), events
    print("ok  FAILED goal with a WHY emits NO goal_acceptance (the `done` term is pinned)")


def test_goal_acceptance_persisted_in_store():
    # the shadow obligation is durable: a done goal_id run records goal_acceptance{verified:False} in the org's
    # record (the field a consumer reads), alongside status=done — exercising the store.update path (store None
    # in the other tests left it uncovered).
    import tempfile, os, subprocess, goal_store
    def git(r, *a): subprocess.run(["git", "-C", str(r), *a], capture_output=True)
    sufficient = lambda g, c, k: {"sufficient": True, "missing": [], "structured": {
        "outcome": "O", "success_condition": "S", "negative_control": "N", "owner": "W"}}
    split = lambda g, c, k: [_leaf("a", ["x.py"])]
    with tempfile.TemporaryDirectory() as d:
        repo = os.path.join(d, "r"); os.mkdir(repo)
        git(repo, "init", "-b", "main"); git(repo, "config", "user.email", "t@t"); git(repo, "config", "user.name", "t")
        open(os.path.join(repo, "seed.txt"), "w").write("x"); git(repo, "add", "-A"); git(repo, "commit", "-m", "base")
        cg.run_goal(repo, "do O", run_leaf=lambda r, t: "converged",
                    split=split, refine=sufficient, goal_id="goal-acc")
        rec = goal_store.GoalStore(repo).read("goal-acc") or {}
        assert rec.get("status") == "done", rec
        acc = rec.get("goal_acceptance") or {}
        assert acc.get("verified") is False and acc.get("negative_control") == "N", rec
    print("ok  goal_acceptance persisted in the org record (verified=False) on a done goal_id run")


def test_intake_gate_auto_binds_real_refiner_and_holds():
    # PRODUCTION PATH: split defaults to splitter.split, refine=None -> active_refine auto-binds the REAL
    # goal_refiner.refine with a real carrier. The refine-injecting tests above do NOT cover this seam; a
    # regression that drops the auto-binding (controller_goal.py line ~465) would pass them but lose the gate
    # in production. Here a real carrier returns "{}" (nothing nameable) -> the real kernel HOLDs.
    import splitter
    real = cg.codex_carrier
    old_org = os.environ.get("AI_ORG_ROOT")
    os.environ["AI_ORG_ROOT"] = str(Path(__file__).resolve().parent.parent)
    def fake(repo, *, model=None, resume_session=None):
        c = lambda prompt: "{}"
        c.captured = {}
        return c
    cg.codex_carrier = fake
    try:
        events = []
        plan = cg.run_goal("/repo", "make it nice", run_leaf=lambda r, t: "converged",
                           split=splitter.split, emit=events.append)   # refine omitted -> auto-bind real refiner
        assert plan == [], plan
        assert any(e["type"] == "goal_underdetermined" for e in events), events
        assert not any(e["type"] == "goal_split" for e in events), "real path HELD before decomposition"
    finally:
        if old_org is None:
            os.environ.pop("AI_ORG_ROOT", None)
        else:
            os.environ["AI_ORG_ROOT"] = old_org
        cg.codex_carrier = real
    print("ok  default-split path auto-binds the REAL refiner and HOLDs an empty intake (production path pinned)")


def test_intake_gate_auto_binds_real_refiner_and_proceeds():
    # the proceed half of the production path: a real carrier that NAMES the four fields for the refiner
    # prompt and a task array for the splitter prompt -> the real refiner passes and the real splitter runs.
    import json, splitter
    real = cg.codex_carrier
    old_org = os.environ.get("AI_ORG_ROOT")
    os.environ["AI_ORG_ROOT"] = str(Path(__file__).resolve().parent.parent)
    def fake(repo, *, model=None, resume_session=None):
        def c(prompt):
            if "Decompose the goal" in prompt:          # the SPLITTER prompt (splitter._build_prompt)
                return '[{"id":"a","objective":"do a","scope":["x.py"],"depends_on":[]}]'
            return json.dumps({"outcome": "o", "success_condition": "s",   # else: the refiner prompt
                               "negative_control": "n", "owner": "w", "intent": "i"})
        c.captured = {}
        return c
    cg.codex_carrier = fake
    try:
        events = []
        plan = cg.run_goal("/repo", "do a clear thing", run_leaf=lambda r, t: "converged",
                           split=splitter.split, emit=events.append)
        assert any(e["type"] == "goal_split" for e in events), ("sufficient intake proceeds to split", events)
        assert _statuses(plan) == {"a": "done"}, plan
    finally:
        if old_org is None:
            os.environ.pop("AI_ORG_ROOT", None)
        else:
            os.environ["AI_ORG_ROOT"] = old_org
        cg.codex_carrier = real
    print("ok  default-split path: sufficient intake auto-refines then decomposes (production proceed path)")


def test_main_returns_exit_2_on_intake_hold():
    """The public CLI contract: an underdetermined goal HELD at intake makes main() return exit code 2."""
    import goal_refiner
    import subprocess
    import tempfile
    real = goal_refiner.refine
    goal_refiner.refine = lambda goal, ctx, carrier: {"sufficient": False, "missing": ["owner"], "structured": {}}
    old_org = os.environ.get("AI_ORG_ROOT")
    os.environ["AI_ORG_ROOT"] = str(Path(__file__).resolve().parent.parent)
    repo = tempfile.mkdtemp()
    subprocess.run(["git", "init", "-q", repo], check=True)
    try:
        code = cg.main(["--repo", repo, "--goal", "make it nice", "--goal-id", "hold1"])
        assert code == 2, f"main() must return exit 2 on an intake HOLD (goal_underdetermined), got {code}"
    finally:
        if old_org is None:
            os.environ.pop("AI_ORG_ROOT", None)
        else:
            os.environ["AI_ORG_ROOT"] = old_org
        goal_refiner.refine = real
    print("ok  main() returns exit 2 on an intake HOLD (underdetermined goal)")


# ---------------------------------------------------------------------------------------------------
# GOAL WORKTREE (default ON): a DIRECT controller_goal launch runs the goal in an ISOLATED worktree of
# --repo so --repo's main never moves DURING the run; a GREEN goal is merged back into local main. Each
# test uses a temp git repo + a stubbed/fast run_leaf (no real dialectic), per the falsifiable acceptance.
# ---------------------------------------------------------------------------------------------------

def _wt_repo(d, name="r"):
    import os, subprocess
    repo = os.path.join(d, name); os.mkdir(repo)
    def git(*a): return subprocess.run(["git", "-C", repo, *a], capture_output=True, text=True)
    git("init", "-b", "main"); git("config", "user.email", "t@t"); git("config", "user.name", "t")
    open(os.path.join(repo, "seed.txt"), "w").write("seed\n"); git("add", "-A"); git("commit", "-m", "base")
    return repo, git


def _rev(git, ref="HEAD"):
    return git("rev-parse", ref).stdout.strip()


def test_goal_worktree_main_unchanged_during_run_commits_on_branch():
    # ACCEPTANCE (a): with isolation ON (default), main HEAD does NOT move DURING the run — the goal's
    # commits land on goal/<id>, not main. The leaf runs in an ISOLATED worktree, not --repo itself.
    import tempfile, os, subprocess
    with tempfile.TemporaryDirectory() as d:
        repo, git = _wt_repo(d)
        base = _rev(git)
        split = lambda g, c, ca: [{"id": "a", "objective": "do a", "scope": ["feat.py"], "depends_on": []}]
        seen = {}

        def run_leaf(r, t):
            seen["isolated"] = os.path.realpath(r) != os.path.realpath(repo)   # runs in a worktree, not --repo
            seen["main_during"] = _rev(git, "main")                            # main HEAD WHILE the leaf runs
            open(os.path.join(r, "feat.py"), "w").write("feature\n")
            subprocess.run(["git", "-C", r, "add", "-A"], capture_output=True)
            subprocess.run(["git", "-C", r, "commit", "-m", "leaf"], capture_output=True)
            seen["branch_after_commit"] = _rev(git, "goal/acc-a")             # the goal branch tip
            seen["main_after_commit"] = _rev(git, "main")                     # main STILL the base
            return {"outcome": "converged", "commit": None}

        cg.run_goal(repo, "build it", run_leaf=run_leaf, split=split, goal_id="acc-a")
        assert seen["isolated"], "the goal must run in an isolated worktree, not --repo directly"
        assert seen["main_during"] == base, ("main HEAD moved during the run", seen["main_during"], base)
        assert seen["branch_after_commit"] and seen["branch_after_commit"] != base, \
            ("the goal's commit must land on goal/<id>", seen)
        assert seen["main_after_commit"] == base, ("main must stay at base DURING the run", seen)
    print("ok  (a) isolation ON: main unchanged during the run; goal commits land on goal/<id>")


def test_goal_worktree_green_merges_into_local_main():
    # ACCEPTANCE (b): on a GREEN goal the result is merged into LOCAL main afterward — main HEAD advances to
    # include the goal's work (the town renders local main, so it must reach it) and the worktree is removed.
    import tempfile, os, subprocess
    with tempfile.TemporaryDirectory() as d:
        repo, git = _wt_repo(d)
        base = _rev(git)
        split = lambda g, c, ca: [{"id": "a", "objective": "do a", "scope": ["feat.py"], "depends_on": []}]

        def run_leaf(r, t):
            open(os.path.join(r, "feat.py"), "w").write("feature\n")
            subprocess.run(["git", "-C", r, "add", "-A"], capture_output=True)
            subprocess.run(["git", "-C", r, "commit", "-m", "leaf"], capture_output=True)
            return {"outcome": "converged", "commit": None}

        events = []
        cg.run_goal(repo, "build it", run_leaf=run_leaf, split=split, goal_id="acc-b", emit=events.append)
        assert _rev(git, "main") != base, "main HEAD must advance to include the goal's work after a green goal"
        assert os.path.isfile(os.path.join(repo, "feat.py")), "the goal's file must be on local main's tree"
        tracked = git("ls-files", "feat.py").stdout.strip()
        assert tracked == "feat.py", ("the goal's file must be committed on main", tracked)
        assert any(e.get("type") == "goal_merged" for e in events), "a green goal emits goal_merged"
        # the worktree is removed and the merged branch deleted on success
        wts = subprocess.run(["git", "-C", repo, "worktree", "list"], capture_output=True, text=True).stdout
        assert "goal-wt-" not in wts, ("the goal worktree must be removed on success", wts)
        assert git("rev-parse", "--verify", "--quiet", "goal/acc-b").returncode != 0, "merged branch deleted"
    print("ok  (b) green goal merges into local main (HEAD advances) and the worktree is cleaned up")


def test_goal_worktree_does_not_sweep_uncommitted_repo_edits():
    # ACCEPTANCE (c): an uncommitted file sitting in --repo's working tree is NOT swept into the goal's
    # commits (the pollution bug). The goal runs off a CLEAN HEAD in the worktree, so the stray work is
    # invisible to it; after the green merge, the stray file is still UNTRACKED and in no commit.
    import tempfile, os, subprocess
    with tempfile.TemporaryDirectory() as d:
        repo, git = _wt_repo(d)
        # a stray UNTRACKED file + an uncommitted edit to a TRACKED file, both sitting in --repo's tree
        open(os.path.join(repo, "dirty.txt"), "w").write("hand-edited, never committed\n")
        open(os.path.join(repo, "seed.txt"), "w").write("seed\nlocal uncommitted edit\n")
        split = lambda g, c, ca: [{"id": "a", "objective": "do a", "scope": ["feat.py"], "depends_on": []}]

        def run_leaf(r, t):
            assert not os.path.exists(os.path.join(r, "dirty.txt")), "the stray file leaked into the worktree"
            assert "uncommitted" not in open(os.path.join(r, "seed.txt")).read(), "the stray edit leaked in"
            open(os.path.join(r, "feat.py"), "w").write("feature\n")
            subprocess.run(["git", "-C", r, "add", "-A"], capture_output=True)
            subprocess.run(["git", "-C", r, "commit", "-m", "leaf"], capture_output=True)
            return {"outcome": "converged", "commit": None}

        cg.run_goal(repo, "build it", run_leaf=run_leaf, split=split, goal_id="acc-c")
        # the stray file is in NO commit anywhere, and is still an untracked working-tree file
        assert git("ls-files", "dirty.txt").stdout.strip() == "", "the stray file was swept into a commit"
        all_blobs = git("log", "--all", "--name-only", "--format=").stdout
        assert "dirty.txt" not in all_blobs, ("the stray file must be in no commit", all_blobs)
        assert os.path.isfile(os.path.join(repo, "dirty.txt")), "the stray file must remain in the tree"
        # the uncommitted tracked edit also never reached a commit (HEAD seed.txt has no local edit)
        assert "uncommitted" not in git("show", "HEAD:seed.txt").stdout, "the stray edit was committed"
    print("ok  (c) uncommitted --repo edits are NOT swept into the goal's commits (pollution bug fixed)")


def test_goal_worktree_opt_out_runs_on_repo_directly():
    # ACCEPTANCE (d): AI_ORG_GOAL_WORKTREE=off restores the OLD behavior — the goal runs on --repo directly
    # (for callers that manage isolation themselves, e.g. the cockpit).
    import tempfile, os, subprocess
    old = os.environ.get("AI_ORG_GOAL_WORKTREE")
    os.environ["AI_ORG_GOAL_WORKTREE"] = "off"
    try:
        with tempfile.TemporaryDirectory() as d:
            repo, git = _wt_repo(d)
            split = lambda g, c, ca: [{"id": "a", "objective": "do a", "scope": ["feat.py"], "depends_on": []}]
            seen = {}

            def run_leaf(r, t):
                seen["on_repo"] = os.path.realpath(r) == os.path.realpath(repo)   # runs on --repo, no worktree
                return {"outcome": "converged", "commit": None}

            events = []
            cg.run_goal(repo, "build it", run_leaf=run_leaf, split=split, goal_id="acc-d", emit=events.append)
            assert seen.get("on_repo"), "opt-out must run the goal on --repo directly (no worktree)"
            assert not any(e.get("type") == "goal_worktree" for e in events), "opt-out emits no goal_worktree"
    finally:
        if old is None:
            os.environ.pop("AI_ORG_GOAL_WORKTREE", None)
        else:
            os.environ["AI_ORG_GOAL_WORKTREE"] = old
    print("ok  (d) AI_ORG_GOAL_WORKTREE=off restores running on --repo directly")


def test_goal_worktree_falls_back_when_isolation_impossible():
    # ACCEPTANCE (e): a non-git --repo, or a failing `worktree add`, falls back to a direct run — no crash.
    import tempfile, os, subprocess
    # (1) _isolate_goal_repo returns None for a non-git dir and for a repo with no commits (unborn HEAD ->
    #     `worktree add` cannot branch off it): both real failure-to-isolate paths.
    with tempfile.TemporaryDirectory() as d:
        nongit = os.path.join(d, "plain"); os.mkdir(nongit)
        assert cg._isolate_goal_repo(nongit, "goal/x") is None, "non-git -> fall back (None)"
        empty = os.path.join(d, "empty"); os.mkdir(empty)
        subprocess.run(["git", "-C", empty, "init", "-q", "-b", "main"], capture_output=True)
        assert cg._isolate_goal_repo(empty, "goal/x") is None, "unborn HEAD (worktree add fails) -> None"
    # (2) run_goal on a non-git --repo must NOT crash and must run the leaf on --repo directly.
    with tempfile.TemporaryDirectory() as d:
        nongit = os.path.join(d, "plain"); os.mkdir(nongit)
        split = lambda g, c, ca: [{"id": "a", "objective": "do a", "scope": ["feat.py"], "depends_on": []}]
        seen = {}

        def run_leaf(r, t):
            seen["r"] = os.path.realpath(r)
            return {"outcome": "converged", "commit": None}

        events = []
        plan = cg.run_goal(nongit, "build it", run_leaf=run_leaf, split=split, emit=events.append)
        assert seen.get("r") == os.path.realpath(nongit), "fallback must run the leaf on --repo directly"
        assert not any(e.get("type") == "goal_worktree" for e in events), "no worktree was created"
        assert plan, "the goal still ran to a plan (no crash)"
    print("ok  (e) non-git / failed worktree-add falls back to a direct run (no crash)")


def test_merge_goal_to_main_leaves_main_clean_on_conflict():
    # POINT 2 (conflict guard): if local main moved under the run to a CONFLICTING state, the merge does not
    # corrupt main — it aborts and main keeps its own HEAD, branch left intact for inspection.
    import tempfile, os, subprocess
    with tempfile.TemporaryDirectory() as d:
        repo, git = _wt_repo(d)
        base = _rev(git)
        # a goal branch edits feat.py off base
        wt = os.path.join(d, "wt")
        git("worktree", "add", "-q", wt, "-b", "goal/conf", "HEAD")
        open(os.path.join(wt, "feat.py"), "w").write("branch version\n")
        subprocess.run(["git", "-C", wt, "add", "-A"], capture_output=True)
        subprocess.run(["git", "-C", wt, "commit", "-q", "-m", "branch feat"], capture_output=True)
        # main moves to a CONFLICTING version of the same file
        open(os.path.join(repo, "feat.py"), "w").write("main version\n")
        git("add", "-A"); git("commit", "-m", "main feat")
        main_before = _rev(git, "main")
        ok = cg._merge_goal_to_main(repo, "goal/conf", base, "main")
        assert ok is False, "a conflicting merge must report failure, not pretend success"
        assert _rev(git, "main") == main_before, "main must keep its own HEAD (uncorrupted) on conflict"
        assert git("rev-parse", "--verify", "--quiet", "goal/conf").returncode == 0, "branch left intact"
        # no merge is left in-progress (MERGE_HEAD absent)
        assert git("rev-parse", "--verify", "--quiet", "MERGE_HEAD").returncode != 0, "merge aborted cleanly"
    print("ok  (point 2) a conflicting merge aborts -> main stays clean, branch retained")


if __name__ == "__main__":
    import os
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        os.environ.pop("STREAM_LOG", None)   # isolate: run_goal binds STREAM_LOG (+ the GoalStore root); don't leak it across cases
        fn()
    print(f"\n{len(fns)} passed")
