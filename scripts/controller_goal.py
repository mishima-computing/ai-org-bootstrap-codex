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

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import frontier  # noqa: E402
import splitter  # noqa: E402

FLOOR_MAX_DEPTH = 3


def at_floor(task: dict, depth: int) -> bool:
    """A node not worth splitting further: at max depth, or atomic (<= 1 file in scope). The floor makes
    the recursion FINITE (a smallest step), so it always terminates without a human (ADR-0008)."""
    return depth >= FLOOR_MAX_DEPTH or len(task.get("scope") or []) <= 1


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


def run_goal(repo, goal, run_leaf, *, split=splitter.split, context=None, carrier=None,
             budget=None, emit=lambda e: None) -> list:
    """Decompose `goal` and build it.

    run_leaf(repo, task) -> "converged" | "failed" runs one leaf's dialectic (inject the real
    controller_pipeline runner; a stub in tests). budget caps the number of leaf runs (None = unbounded,
    bounded only by the floor). emit(event) streams progress (ADR-0009). Returns the final task tree."""
    plan = split(goal, context or {}, carrier)
    errs = frontier.validate_plan(plan)
    if errs:
        emit({"type": "split_invalid", "goal": goal, "errors": errs})
        return plan
    emit({"type": "goal_split", "goal": goal, "n": len(plan)})

    spent = 0
    while True:
        ready = [t for t in frontier.ready_tasks(plan) if str(t.get("status") or "pending") == "pending"]
        if not ready:
            break
        for leaf in ready:
            if budget is not None and spent >= budget:
                emit({"type": "budget_exhausted", "spent": spent})
                return plan
            spent += 1
            plan = frontier.advance(plan, leaf["id"], "running")
            emit({"type": "leaf_start", "id": leaf["id"]})
            outcome = run_leaf(repo, leaf)
            if outcome == "converged":
                plan = frontier.advance(plan, leaf["id"], "done")
                emit({"type": "leaf_done", "id": leaf["id"]})
                continue
            depth = _depth_of(plan, leaf["id"]) or 0
            if at_floor(leaf, depth):                       # floor reached -> fail it, never split forever
                plan = frontier.advance(plan, leaf["id"], "failed")
                emit({"type": "leaf_failed_floor", "id": leaf["id"], "depth": depth})
                continue
            children = split(leaf["objective"], {**(context or {}), "parent": leaf["id"]}, carrier)
            if not children or frontier.validate_plan(children):
                plan = frontier.advance(plan, leaf["id"], "failed")   # bad/empty split -> stop this branch
                emit({"type": "split_unusable", "id": leaf["id"]})
                continue
            plan = _set_children(plan, leaf["id"], children)            # the leaf becomes an internal node
            plan = frontier.advance(plan, leaf["id"], "pending")
            emit({"type": "leaf_split", "id": leaf["id"], "n": len(children), "depth": depth})
    return plan
