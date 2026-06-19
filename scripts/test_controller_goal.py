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
    # commits its build (wip), and its outcome in its own GoalStore — independent of any host.
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
        print("ok  goal_id -> the org owns its goal state (record + wip) AND flows it to its rich log")


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


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
    print(f"\n{len(fns)} passed")
