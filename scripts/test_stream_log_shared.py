#!/usr/bin/env python3
"""Regression: the leaf dialectic (run with repo=<temp worktree>) must append to the SHARED stream, not a
worktree-local one (which is destroyed with the worktree, losing the dialectic); and concurrent appends of
lines LARGER than PIPE_BUF must not interleave/corrupt (the flock guard). Plain def test_* + __main__."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import controller_goal  # noqa: E402
import controller_pipeline  # noqa: E402

_HERE = Path(__file__).resolve().parent


def test_worktree_speech_lands_in_shared_stream():
    with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as wt:
        os.environ["STREAM_LOG"] = str(Path(root) / ".agent-runs" / "stream.jsonl")
        # a leaf runs with repo = the ephemeral worktree; speech must STILL land on the shared stream
        controller_pipeline._stream_append(wt, {"source": "genius", "type": "agent_message", "speech": "designing"})
        controller_goal.stream_emit(wt)({"type": "leaf_start"})
        shared = Path(root) / ".agent-runs" / "stream.jsonl"
        wt_local = Path(wt) / ".agent-runs" / "stream.jsonl"
        assert shared.exists(), "shared stream must exist"
        srcs = [json.loads(l).get("source") for l in shared.read_text().splitlines() if l.strip()]
        assert "genius" in srcs, "the leaf's genius agent_message must land on the SHARED stream"
        assert not wt_local.exists(), f"nothing may go to the worktree-local stream, found {wt_local}"
        os.environ.pop("STREAM_LOG", None)
    print("ok  leaf (repo=worktree) speech lands in the SHARED stream, not the worktree-local one")


def test_run_goal_binds_absolute_stream_log():
    os.environ.pop("STREAM_LOG", None)
    with tempfile.TemporaryDirectory() as repo:
        # default_run_leaf=lambda so no real pipeline runs; the split yields one trivial leaf
        controller_goal.run_goal(repo, "a goal", run_leaf=lambda r, t: "converged",
                                 split=lambda *a, **k: [{"id": "x", "objective": "x"}])
        sl = os.environ.get("STREAM_LOG")
        assert sl and os.path.isabs(sl), f"run_goal must bind STREAM_LOG to an ABSOLUTE path, got {sl!r}"
        assert sl.endswith(".agent-runs/stream.jsonl"), sl
        os.environ.pop("STREAM_LOG", None)
    print("ok  run_goal binds STREAM_LOG to an absolute shared path")


def _mp_worker(log_path: str, wid: int, n: int, big: str):
    os.environ["STREAM_LOG"] = log_path
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import controller_pipeline as cp
    for _ in range(n):
        cp._stream_append("/tmp", {"source": f"w{wid}", "type": "agent_message", "speech": big})


def test_concurrent_large_appends_do_not_corrupt():
    import multiprocessing as mp
    big = "x" * 20000   # large lines + many concurrent cross-process writers; assert no torn/interleaved JSON.
    N, PER = 10, 6      # NOTE: regular-file O_APPEND is already atomic for any size on Linux/macOS, so this also
                        # passes WITHOUT the flock — test_flock_guard_present pins the lock that is its edge-case insurance.
    with tempfile.TemporaryDirectory() as root:
        log = str(Path(root) / "stream.jsonl")
        ctx = mp.get_context("spawn")
        procs = [ctx.Process(target=_mp_worker, args=(log, i, PER, big)) for i in range(N)]
        for p in procs:
            p.start()
        for p in procs:
            p.join()
        lines = [l for l in Path(log).read_text().splitlines() if l.strip()]
        bad = sum(1 for l in lines if _is_bad(l))
        assert bad == 0, f"{bad}/{len(lines)} lines corrupted by concurrent cross-process appends"
        assert len(lines) == N * PER, f"expected {N*PER} intact lines, got {len(lines)}"
    print(f"ok  {N*PER} concurrent cross-process 20KB appends: every line intact (flock works)")


def _is_bad(line: str) -> bool:
    try:
        json.loads(line)
        return False
    except Exception:
        return True


def test_bind_makes_leaf_worktree_append_inherit_shared_stream():
    """The real loop: run_goal binds STREAM_LOG, so a leaf that appends from its worktree lands on the SHARED log."""
    os.environ.pop("STREAM_LOG", None)
    with tempfile.TemporaryDirectory() as repo, tempfile.TemporaryDirectory() as fake_wt:
        def fake_leaf(_r, _task):
            controller_pipeline._stream_append(fake_wt, {"source": "genius", "type": "agent_message", "speech": "x"})
            return "converged"
        controller_goal.run_goal(repo, "g", run_leaf=fake_leaf,
                                 split=lambda *a, **k: [{"id": "L", "objective": "o"}])
        shared = Path(repo) / ".agent-runs" / "stream.jsonl"
        srcs = [json.loads(l).get("source") for l in shared.read_text().splitlines() if l.strip()]
        assert "genius" in srcs, "a worktree-side append during a leaf must land on the SHARED stream"
        assert not (Path(fake_wt) / ".agent-runs" / "stream.jsonl").exists(), "nothing may go to the worktree-local stream"
        os.environ.pop("STREAM_LOG", None)
    print("ok  run_goal's bind makes a leaf's worktree-side append inherit the shared stream")


def test_preset_stream_log_is_respected():
    with tempfile.TemporaryDirectory() as preset_dir, tempfile.TemporaryDirectory() as repo:
        preset = str(Path(preset_dir) / "shared.jsonl")
        os.environ["STREAM_LOG"] = preset
        controller_goal.run_goal(repo, "g", run_leaf=lambda r, t: "converged",
                                 split=lambda *a, **k: [{"id": "L", "objective": "o"}])
        assert os.environ.get("STREAM_LOG") == preset, "a pre-set STREAM_LOG must be respected (cockpit shared-log case)"
        os.environ.pop("STREAM_LOG", None)
    print("ok  a pre-set STREAM_LOG is respected, not overwritten")


def test_goalstore_record_lands_under_repo():
    """GoalStore root derives from STREAM_LOG; the bind must put the record under the repo, not a worktree."""
    os.environ.pop("STREAM_LOG", None)
    with tempfile.TemporaryDirectory() as repo:
        controller_goal.run_goal(repo, "g", goal_id="g1", run_leaf=lambda r, t: "converged",
                                 split=lambda *a, **k: [{"id": "L", "objective": "o"}])
        rec = Path(repo) / ".agent-runs" / "goals" / "g1.json"
        assert rec.exists(), f"the GoalStore record must land under the repo: {rec}"
        os.environ.pop("STREAM_LOG", None)
    print("ok  GoalStore record lands under the repo (root follows the bound STREAM_LOG)")


def test_flock_guard_present_in_both_append_sites():
    import inspect
    assert "flock" in inspect.getsource(controller_goal.stream_emit), "stream_emit must take the flock guard"
    assert "flock" in inspect.getsource(controller_pipeline._stream_append), "_stream_append must take the flock guard"
    print("ok  flock guard present in both append sites (deleting it fails this test)")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        os.environ.pop("STREAM_LOG", None)   # isolate cases: run_goal binds STREAM_LOG (+ GoalStore root)
        fn()
    print(f"\n{len(fns)} passed")
