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
        # leaf lifecycle events carry goal_id, so a consumer (the town / steer UI) attributes each Queue
        # node to its goal — the basis for picking a node to steer.
        assert any(e.get("type") == "leaf_start" and e.get("goal_id") == "goal-own1" for e in events), \
            "leaf_start carries goal_id"
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
    # speech) must ride the stream so a host can show "what the splitter said" without scraping an ephemeral
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
    cg.codex_carrier = fake
    try:
        with tempfile.TemporaryDirectory() as d:
            repo = os.path.join(d, "r"); os.mkdir(repo)
            for a in (["init", "-b", "main"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
                git(repo, *a)
            open(os.path.join(repo, "seed.txt"), "w").write("x"); git(repo, "add", "-A"); git(repo, "commit", "-m", "base")
            cg.run_goal(repo, "build inside mocks/ ONLY", run_leaf=lambda r, t: "converged",
                        split=splitter.split, goal_id="goal-A")
            assert ((goal_store.GoalStore(repo).read("goal-A") or {}).get("sessions") or {}).get("_goal:splitter") \
                == "splitsid-0", "initial run records the splitter session"
            seen["resume"].clear()
            cg.run_goal(repo, "build inside mocks/ ONLY", run_leaf=lambda r, t: "converged",
                        split=splitter.split, goal_id="goal-B", resume_from="goal-A")
            assert "splitsid-0" in seen["resume"], ("the resumed top split RESUMES the prior splitter session",
                                                    seen["resume"])
    finally:
        cg.codex_carrier = real
    print("ok  splitter session recorded on initial split, RESUMED on resume (planning memory kept)")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
    print(f"\n{len(fns)} passed")
