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


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
    print(f"\n{len(fns)} passed")
