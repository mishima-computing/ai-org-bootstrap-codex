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
    big = "x" * 20000   # >> PIPE_BUF (4096): unguarded O_APPEND would interleave these across processes
    N, PER = 10, 6
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


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
    print(f"\n{len(fns)} passed")
