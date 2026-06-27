#!/usr/bin/env python3
"""Replay tests for the Border Collie detector pack. Plain def test_* + __main__."""
from __future__ import annotations

import datetime as dt
import json
import math
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import border_collie as bc  # noqa: E402
import controller_goal  # noqa: E402
import goal_store  # noqa: E402


def goal(goal_id="g1", text="Add a Usage section to README.md"):
    return {"goal_id": goal_id, "goal": text, "status": "running"}


def split(parent, *children):
    return {"type": "agent_message", "source": "splitter", "run_id": parent,
            "speech": [{"id": c, "objective": c} for c in children]}


def git_fn(mapping):
    calls = []

    def fn(commit):
        calls.append(commit)
        return mapping.get(commit, [])

    fn.calls = calls
    return fn


def scents(barks):
    return [b.scent for b in barks]


T0 = dt.datetime(2026, 6, 23, tzinfo=dt.timezone.utc)


def mins(n):
    return T0 + dt.timedelta(minutes=n)


def make_store(root):
    st = goal_store.GoalStore(str(root), emit=controller_goal.stream_emit(str(root)))
    st.create("g1", "Build a minimal scaffold", "codex")
    return st


def seed_bark(root, scent=bc.SCENT_CHURN, node="nodeA", strength=1, minute=0):
    bc.append_bark(root, bc.Bark("g1", node, scent, "seed", strength=strength),
                   instance="seed", seq=1, now=mins(minute))


def test_scaffold_infinite_split_targets_branch_and_healthy_land_is_silent():
    events = [
        split("g1", "minimal-scaffold"),
        {"type": "leaf_split", "goal_id": "g1", "id": "minimal-scaffold", "n": 2},
        {"type": "leaf_split", "goal_id": "g1", "id": "minimal-scaffold", "n": 2},
    ]
    barks = bc.scan(events, goal("g1", "Build a minimal scaffold for the tool"), git_fn({}),
                    {bc.SCENT_SCAFFOLD})
    assert scents(barks) == [bc.SCENT_SCAFFOLD]
    assert barks[0].node == "minimal-scaffold"
    assert barks[0].strength == 1

    healthy = events + [split("minimal-scaffold", "child"), {"type": "leaf_done", "goal_id": "g1",
                                                               "id": "child", "commit": None}]
    assert bc.scan(healthy, goal("g1", "Build a minimal scaffold for the tool"), git_fn({}),
                   {bc.SCENT_SCAFFOLD}) == []


def test_churn_fires_after_cap_and_landed_branch_is_silent():
    events = [
        {"type": "leaf_split", "goal_id": "g1", "id": "stuck", "n": 2},
        {"type": "leaf_split", "goal_id": "g1", "id": "stuck", "n": 2},
        {"type": "leaf_split", "goal_id": "g1", "id": "stuck", "n": 2},
    ]
    barks = bc.scan(events, goal(), git_fn({}), {bc.SCENT_CHURN})
    assert scents(barks) == [bc.SCENT_CHURN]
    assert barks[0].node == "stuck"
    assert barks[0].strength == 1

    healthy = events + [split("stuck", "landed"), {"type": "leaf_done", "goal_id": "g1",
                                                    "id": "landed", "commit": "c1"}]
    assert bc.scan(healthy, goal(), git_fn({"c1": ["README.md"]}), {bc.SCENT_CHURN}) == []


def test_churn_does_not_fire_on_self_steer_dressed_leaf_splits():
    events = []
    for round_no in range(1, 4):
        events.append({"type": "self_steer", "goal_id": "g1", "id": "floor", "round": round_no, "n": 2})
        events.append({"type": "leaf_split", "goal_id": "g1", "id": "floor", "n": 2})
    assert bc.scan(events, goal(), git_fn({}), {bc.SCENT_CHURN}) == []


def test_gold_plating_uses_overlapping_touch_sets_not_semantic_ids():
    events = [
        split("g1", "prereq"),
        {"type": "leaf_split", "goal_id": "g1", "id": "prereq", "n": 2},
        split("prereq", "a", "b"),
        {"type": "leaf_done", "goal_id": "g1", "id": "a", "commit": "c1"},
        {"type": "leaf_done", "goal_id": "g1", "id": "b", "commit": "c2"},
    ]
    commits = {
        "c1": ["src/config.py", "src/helpers.py"],
        "c2": ["src/config.py", "docs/internal.md"],
    }
    barks = bc.scan(events, goal("g1", "Add a Usage section to README.md"), git_fn(commits), {bc.SCENT_GOLD})
    assert scents(barks) == [bc.SCENT_GOLD]
    assert barks[0].node == "prereq", "nearest reconstructed branch parent should be targeted"
    assert barks[0].strength == 1

    healthy = events[:3] + [
        {"type": "leaf_done", "goal_id": "g1", "id": "a", "commit": "c1"},
        {"type": "leaf_done", "goal_id": "g1", "id": "b", "commit": "c3"},
    ]
    commits["c3"] = ["README.md"]
    assert bc.scan(healthy, goal("g1", "Add a Usage section to README.md"), git_fn(commits),
                   {bc.SCENT_GOLD}) == []


def test_falsey_commits_are_lands_with_empty_touch_sets():
    events = [
        split("g1", "branch"),
        {"type": "leaf_split", "goal_id": "g1", "id": "branch", "n": 2},
        {"type": "leaf_split", "goal_id": "g1", "id": "branch", "n": 2},
        {"type": "leaf_split", "goal_id": "g1", "id": "branch", "n": 2},
        split("branch", "none", "empty"),
        {"type": "leaf_done", "goal_id": "g1", "id": "none", "commit": None},
        {"type": "leaf_done", "goal_id": "g1", "id": "empty", "commit": ""},
    ]
    fn = git_fn({})
    assert bc.scan(events, goal("g1", "Build a minimal scaffold for README.md"), fn,
                   {bc.SCENT_CHURN, bc.SCENT_SCAFFOLD, bc.SCENT_GOLD}) == []
    assert fn.calls == [], "falsey commits must not be git-shown"


def test_dual_language_duplication_detects_added_mirror_and_healthy_is_silent():
    events = [{"type": "leaf_done", "goal_id": "g1", "id": "api", "commit": "c1"}]
    barks = bc.scan(events, goal(), git_fn({
        "c1": {"paths": ["server.py"], "added": ["server.py"], "existing": ["server.js"]},
    }), {bc.SCENT_DUAL})
    assert scents(barks) == [bc.SCENT_DUAL]
    assert barks[0].node == "api"
    assert barks[0].strength == 1

    assert bc.scan(events, goal(), git_fn({
        "c1": {"paths": ["worker.py"], "added": ["worker.py"], "existing": ["server.js"]},
    }), {bc.SCENT_DUAL}) == []


def test_documented_absence_detects_unwired_gate_and_healthy_symbol_is_silent():
    events = [{"type": "leaf_done", "goal_id": "g1", "id": "docs", "commit": "c1"}]
    content = "TODO: forward work must add the gate `acceptance_gate` before release\n"
    barks = bc.scan(events, goal(), git_fn({
        "c1": {"paths": ["README.md"], "contents": {"README.md": content}, "repo_symbols": set()},
    }), {bc.SCENT_DOC})
    assert scents(barks) == [bc.SCENT_DOC]
    assert barks[0].node == "docs"
    assert barks[0].strength == 1

    assert bc.scan(events, goal(), git_fn({
        "c1": {"paths": ["README.md"], "contents": {"README.md": content},
               "repo_symbols": {"acceptance_gate"}},
    }), {bc.SCENT_DOC}) == []


def test_sidecar_habituation_quiets_immediate_repeat_and_bark_shows_in_stream():
    with tempfile.TemporaryDirectory() as root:
        root = Path(root)
        st = goal_store.GoalStore(str(root), emit=controller_goal.stream_emit(str(root)))
        st.create("g1", "Build a minimal scaffold", "codex")
        bark = bc.Bark("g1", "node", bc.SCENT_SCAFFOLD, "advisory")

        first = bc.apply_barks(root, st, [bark], instance="dog-a", now=mins(0))
        second = bc.apply_barks(root, st, [bark], instance="dog-a", now=mins(1))

        assert first == [bark]
        assert second == []
        assert len(st.read_steering("g1")) == 1
        history = bc.read_bark_history(root)
        assert list(history) == [("g1", "node", bc.SCENT_SCAFFOLD)]
        assert history[("g1", "node", bc.SCENT_SCAFFOLD)][0][1] == 1
        stream = (root / ".agent-runs" / "stream.jsonl").read_text(encoding="utf-8")
        assert '"op": "steer"' in stream and '"target": "node"' in stream


def test_habituation_minimal_smell_barks_once_then_quiets():
    with tempfile.TemporaryDirectory() as root:
        root = Path(root)
        st = make_store(root)
        bark = bc.Bark("g1", "nodeA", bc.SCENT_CHURN, "advisory", strength=1)

        assert bc.apply_barks(root, st, [bark], instance="dog-a", now=mins(0)) == [bark]
        assert bc.apply_barks(root, st, [bark], instance="dog-a", now=mins(1)) == []
        assert bc.apply_barks(root, st, [bark], instance="dog-a", now=mins(5)) == []
        assert len(st.read_steering("g1")) == 1


def test_recovered_persistent_smell_rebarks_with_escalation_text():
    with tempfile.TemporaryDirectory() as root:
        root = Path(root)
        st = make_store(root)
        seed_bark(root, minute=0)
        bark = bc.Bark("g1", "nodeA", bc.SCENT_CHURN, "advisory", strength=1)

        assert bc.apply_barks(root, st, [bark], instance="dog-a", now=mins(20)) == []
        applied = bc.apply_barks(root, st, [bark], instance="dog-a", now=mins(22))

        assert len(applied) == 1
        assert "ESCALATION (alert #2" in applied[0].text
        assert "~22 min unaddressed" in applied[0].text
        assert "still unresolved 1->1" in applied[0].text
        assert applied[0].text != bark.text


def test_stronger_smell_barks_through_habituation_immediately():
    with tempfile.TemporaryDirectory() as root:
        root = Path(root)
        st = make_store(root)
        seed_bark(root, minute=0, strength=1)
        bark = bc.Bark("g1", "nodeA", bc.SCENT_CHURN, "advisory", strength=2)

        applied = bc.apply_barks(root, st, [bark], instance="dog-a", now=mins(1))

        assert len(applied) == 1
        assert "intensified 1->2" in applied[0].text


def test_different_scent_or_node_has_independent_threshold():
    with tempfile.TemporaryDirectory() as root:
        root = Path(root)
        st = make_store(root)
        seed_bark(root, scent=bc.SCENT_CHURN, node="nodeA", minute=0)
        barks = [
            bc.Bark("g1", "nodeA", bc.SCENT_SCAFFOLD, "scaffold advisory", strength=1),
            bc.Bark("g1", "nodeB", bc.SCENT_CHURN, "churn advisory", strength=1),
        ]

        applied = bc.apply_barks(root, st, barks, instance="dog-a", now=mins(1))

        assert [(b.node, b.scent) for b in applied] == [
            ("nodeA", bc.SCENT_SCAFFOLD),
            ("nodeB", bc.SCENT_CHURN),
        ]


def test_potentiation_rebark_before_full_recovery_lengthens_next_quiet_window():
    with tempfile.TemporaryDirectory() as root:
        root = Path(root)
        st = make_store(root)
        seed_bark(root, minute=0)
        bark = bc.Bark("g1", "nodeA", bc.SCENT_CHURN, "advisory", strength=1)

        assert bc.apply_barks(root, st, [bark], instance="dog-a", now=mins(22))
        assert bc.apply_barks(root, st, [bark], instance="dog-a", now=mins(44)) == []
        assert bc.apply_barks(root, st, [bark], instance="dog-a", now=mins(56))


def test_degenerate_infinite_delta_and_tau_reproduce_once_forever_dedup():
    with tempfile.TemporaryDirectory() as root:
        root = Path(root)
        st = make_store(root)
        seed_bark(root, minute=0)
        bark = bc.Bark("g1", "nodeA", bc.SCENT_CHURN, "advisory", strength=99)
        old = dict(bc.PARAMS[bc.SCENT_CHURN])
        bc.PARAMS[bc.SCENT_CHURN] = {"theta0": bc.THETA0, "delta": math.inf, "tau": math.inf}
        try:
            assert bc.apply_barks(root, st, [bark], instance="dog-a", now=mins(99)) == []
        finally:
            bc.PARAMS[bc.SCENT_CHURN] = old


def test_pack_disjoint_scent_instances_use_disjoint_partitions_without_duplicate_keys():
    events = [
        {"type": "leaf_split", "goal_id": "g1", "id": "minimal", "n": 2},
        {"type": "leaf_split", "goal_id": "g1", "id": "minimal", "n": 2},
        {"type": "leaf_split", "goal_id": "g1", "id": "minimal", "n": 2},
    ]
    rec = goal("g1", "Build a minimal scaffold")
    with tempfile.TemporaryDirectory() as root:
        root = Path(root)
        st = goal_store.GoalStore(str(root), emit=controller_goal.stream_emit(str(root)))
        st.create("g1", rec["goal"], "codex")

        dog_a = bc.scan(events, rec, git_fn({}), {bc.SCENT_SCAFFOLD})
        dog_b = bc.scan(events, rec, git_fn({}), {bc.SCENT_CHURN})
        applied_a = bc.apply_barks(root, st, dog_a, instance="dog-a")
        applied_b = bc.apply_barks(root, st, dog_b, instance="dog-b")

        keys = [b.key for b in applied_a + applied_b]
        assert len(keys) == len(set(keys))
        assert {b.scent for b in applied_a}.isdisjoint({b.scent for b in applied_b})
        sidecar = root / ".agent-runs" / "border_collie"
        assert (sidecar / "barks.dog-a.jsonl").is_file()
        assert (sidecar / "barks.dog-b.jsonl").is_file()


def test_run_once_discovers_running_goals_from_one_store_root():
    with tempfile.TemporaryDirectory() as root:
        root = Path(root)
        st = goal_store.GoalStore(str(root), emit=controller_goal.stream_emit(str(root)))
        st.create("g1", "Build a minimal scaffold", "codex")
        st.create("done", "Build a minimal scaffold", "codex")
        st.update("done", status="done")
        stream = root / ".agent-runs" / "stream.jsonl"
        with stream.open("a", encoding="utf-8") as f:
            for event in [
                {"type": "leaf_split", "goal_id": "g1", "id": "minimal", "n": 2},
                {"type": "leaf_split", "goal_id": "g1", "id": "minimal", "n": 2},
                {"type": "leaf_split", "goal_id": "done", "id": "minimal", "n": 2},
                {"type": "leaf_split", "goal_id": "done", "id": "minimal", "n": 2},
            ]:
                f.write(json.dumps(event) + "\n")

        applied = bc.run_once(root, scents={bc.SCENT_SCAFFOLD}, instance="dog-root")
        assert [(b.goal_id, b.node, b.scent) for b in applied] == [("g1", "minimal", bc.SCENT_SCAFFOLD)]


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for test in tests:
        test()
    print(f"{len(tests)} passed")


if __name__ == "__main__":
    main()
