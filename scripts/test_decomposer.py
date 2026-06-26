#!/usr/bin/env python3
"""Decomposer tests for the TaskGraph/TaskExecutor model.

Run:  python3 scripts/test_decomposer.py
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import task_executor as S


LEAF_OBJECTIVE = (
    "Acceptance: verified. Limited scope. Fits one context. "
    "Further split costs more than it saves."
)


def _carrier_for(tasks, prompts):
    def carrier(prompt):
        prompts.append(prompt)
        return json.dumps(tasks)
    return carrier


def _carrier_for_response(response, prompts):
    def carrier(prompt):
        prompts.append(prompt)
        return json.dumps(response)
    return carrier


def _carrier_by_parent(tasks_by_parent, prompts):
    def carrier(prompt):
        prompts.append(prompt)
        parent_id = prompt.split("Parent id: ", 1)[1].splitlines()[0]
        return json.dumps(tasks_by_parent.get(parent_id, []))
    return carrier


def _stubs():
    def verify(node, integrated_head, child_commits):
        return {"verified": True, "node": node.id, "children": [c.task_id for c in child_commits]}

    def integrate(node, base, child_commits):
        return (f"integrated-head-{node.id}", None)

    def commit_integration(node, base, integrated_head, integ_wt, evidence):
        return f"integ-sha-{node.id}"

    return verify, integrate, commit_integration


def test_decompose_parallel_and_serial_shapes():
    prompts: list[str] = []
    root = S.TaskNode("root", kind=S.COMPOSITE, base_sha="BASE0", objective="large feature")

    parallel_children = S.decompose(root, _carrier_for([
        {"id": "api", "objective": LEAF_OBJECTIVE, "depends_on": [], "base_sha": None},
        {"id": "ui", "objective": LEAF_OBJECTIVE, "depends_on": [], "base_sha": "BASE-UI"},
    ], prompts), S.FLOOR_MAX_DEPTH)
    assert [c.id for c in parallel_children] == ["api", "ui"], parallel_children
    assert all(c.depends_on == [] for c in parallel_children), parallel_children
    assert parallel_children[0].base_sha == "BASE0"
    assert parallel_children[1].base_sha == "BASE-UI"
    assert all(c.kind == S.COMPOSITE for c in parallel_children), parallel_children

    serial_children = S.decompose(root, _carrier_for([
        {"id": "schema", "objective": LEAF_OBJECTIVE, "depends_on": [], "base_sha": None},
        {"id": "service", "objective": LEAF_OBJECTIVE, "depends_on": ["schema"], "base_sha": None},
        {"id": "caller", "objective": LEAF_OBJECTIVE, "depends_on": ["service"], "base_sha": None},
    ], prompts), S.FLOOR_MAX_DEPTH)
    assert [(c.id, c.depends_on) for c in serial_children] == [
        ("schema", []),
        ("service", ["schema"]),
        ("caller", ["service"]),
    ], serial_children

    prompt = prompts[0]
    assert "PARALLEL split first" in prompt
    assert "SERIAL split" in prompt
    assert "depends_on encodes serial/dependent versus parallel/independent" in prompt
    assert "return an EMPTY JSON array [] -- it is a leaf" in prompt
    assert "There are only TWO reasons to split:" in prompt
    assert "(1) PARALLELISM: separate genuinely INDEPENDENT work" in prompt
    assert "(2) REVIEWABILITY: each task must be small enough" in prompt
    assert "IMPACT / BLAST RADIUS" in prompt and "NOT line count" in prompt
    assert "Make each task as LARGE as possible while still satisfying both" in prompt
    assert "Start COARSE: a task that later proves too big is split further automatically" in prompt
    assert "SCAFFOLD / greenfield is ATOMIC" in prompt
    assert "creating a project skeleton" in prompt and "do NOT split it" in prompt
    assert "return an EMPTY children array [] == leaf" in prompt
    assert "Isolate a high-impact shared-interface change into its OWN task" in prompt
    assert f"HOUSE_RULES:\n{S.HOUSE_RULES}" in prompt
    assert "You are at depth 0 of max 3. If you are at or beyond the max depth" in prompt
    assert 'At depth 0 only, return a JSON object: {"max_depth": <integer 1-5>, "children": [...]}' in prompt
    assert "Each child object must contain exactly id, objective, depends_on, and base_sha" in prompt
    print("ok  decompose() schema-gates TaskNodes and preserves parallel/serial depends_on")


def test_decompose_prompt_is_depth_aware():
    node = S.TaskNode("deep", kind=S.COMPOSITE, base_sha="BASE0", objective="large feature", depth=2)
    prompt = S._build_decompose_prompt(node, 4)

    assert "You are at depth 2 of max 4." in prompt
    assert "MUST return an empty children array (this node is a leaf)." in prompt
    assert "At depth 0 only" not in prompt
    print("ok  decompose prompt tells the LLM current depth and max depth")


def test_root_decompose_max_depth_is_clamped():
    root = S.TaskNode("root", kind=S.COMPOSITE, base_sha="BASE0", objective="large feature")

    high = S.decompose_with_metadata(root, _carrier_for_response({"max_depth": 9, "children": []}, []), 3)
    low = S.decompose_with_metadata(root, _carrier_for_response({"max_depth": 0, "children": []}, []), 3)

    assert high.max_depth == 5, high
    assert low.max_depth == 1, low
    print("ok  root decompose max_depth is clamped to 1..5")


def test_execute_empty_decompose_runs_node_as_leaf():
    root = S.TaskNode("root", kind=S.COMPOSITE, base_sha="BASE0", objective="small enough")
    prompts: list[str] = []
    leaves_executed: list[str] = []

    def run_leaf(node):
        leaves_executed.append(node.id)
        return S.VerifiedCommit(node.id, f"leaf-sha-{node.id}", {"kind": "leaf"})

    verify, integrate, commit_integration = _stubs()
    task_executor = S.TaskExecutor(run_leaf=run_leaf, verify=verify, integrate=integrate,
                      commit_integration=commit_integration, decompose_carrier=_carrier_for([], prompts),
                      max_parallel=1)
    result = task_executor.execute(root)

    assert result.commit_sha == "leaf-sha-root", result
    assert leaves_executed == ["root"], leaves_executed
    assert root.subtasks == [], root.subtasks
    assert task_executor.recursion_edges == [], task_executor.recursion_edges
    assert len(prompts) == 1 and "Parent id: root" in prompts[0], prompts
    print("ok  empty decompose output declares the current node a leaf")


def test_execute_decomposes_unsupplied_composite_and_runs_independent_children_concurrently():
    root = S.TaskNode("root", kind=S.COMPOSITE, base_sha="BASE0", objective="large feature")
    prompts: list[str] = []
    carrier = _carrier_by_parent({
        "root": [
            {"id": "api", "objective": LEAF_OBJECTIVE, "depends_on": [], "base_sha": None},
            {"id": "ui", "objective": LEAF_OBJECTIVE, "depends_on": [], "base_sha": None},
        ],
        "api": [],
        "ui": [],
    }, prompts)

    barrier = threading.Barrier(2, timeout=2.0)
    started: list[str] = []
    lock = threading.Lock()

    def run_leaf(node):
        with lock:
            started.append(node.id)
        barrier.wait()
        return S.VerifiedCommit(node.id, f"leaf-sha-{node.id}", {"kind": "leaf"})

    verify, integrate, commit_integration = _stubs()
    task_executor = S.TaskExecutor(run_leaf=run_leaf, verify=verify, integrate=integrate,
                      commit_integration=commit_integration, decompose_carrier=carrier,
                      max_parallel=2)
    result = task_executor.execute(root)

    assert result.commit_sha == "integ-sha-root", result
    assert sorted(started) == ["api", "ui"], started
    assert set(task_executor.recursion_edges) == {("root", "api"), ("root", "ui")}, task_executor.recursion_edges
    assert [c.id for c in root.subtasks] == ["api", "ui"], root.subtasks
    assert {p.split("Parent id: ", 1)[1].splitlines()[0] for p in prompts} == {"root", "api", "ui"}
    print("ok  execute() decomposes an empty composite and runs independent children in one parallel wave")


def test_execute_serial_fallback_depends_on_chain_runs_in_order():
    root = S.TaskNode("root", kind=S.COMPOSITE, base_sha="BASE0", objective="large sequential feature")
    prompts: list[str] = []
    carrier = _carrier_by_parent({
        "root": [
            {"id": "schema", "objective": LEAF_OBJECTIVE, "depends_on": [], "base_sha": None},
            {"id": "service", "objective": LEAF_OBJECTIVE, "depends_on": ["schema"], "base_sha": None},
            {"id": "caller", "objective": LEAF_OBJECTIVE, "depends_on": ["service"], "base_sha": None},
        ],
        "schema": [],
        "service": [],
        "caller": [],
    }, prompts)

    order: list[str] = []
    active = {"n": 0, "max": 0}
    lock = threading.Lock()

    def run_leaf(node):
        with lock:
            active["n"] += 1
            active["max"] = max(active["max"], active["n"])
            order.append(node.id)
        time.sleep(0.01)
        with lock:
            active["n"] -= 1
        return S.VerifiedCommit(node.id, f"leaf-sha-{node.id}", {"kind": "leaf"})

    verify, integrate, commit_integration = _stubs()
    task_executor = S.TaskExecutor(run_leaf=run_leaf, verify=verify, integrate=integrate,
                      commit_integration=commit_integration, decompose_carrier=carrier,
                      max_parallel=3)
    result = task_executor.execute(root)

    assert result.evidence["integrated_children"] == [
        "leaf-sha-schema", "leaf-sha-service", "leaf-sha-caller"], result.evidence
    assert order == ["schema", "service", "caller"], order
    assert active["max"] == 1, active
    assert [(c.id, c.depends_on) for c in root.subtasks] == [
        ("schema", []),
        ("service", ["schema"]),
        ("caller", ["service"]),
    ], root.subtasks
    print("ok  serial fallback depends_on chain runs in dependency order with no sibling overlap")


def test_execute_always_split_decomposer_stops_at_depth_ceiling():
    root = S.TaskNode("root", kind=S.COMPOSITE, base_sha="BASE0", objective="large recursive feature")
    decomposed: list[tuple[str, int]] = []
    leaves_executed: list[tuple[str, int]] = []
    old = os.environ.pop("AI_ORG_MAX_DEPTH", None)

    def decomposer(node):
        decomposed.append((node.id, node.depth))
        return [S.TaskNode(f"{node.id}.child", kind=S.COMPOSITE, base_sha=node.base_sha,
                           objective="split again", depth=node.depth + 1)]

    def run_leaf(node):
        leaves_executed.append((node.id, node.depth))
        return S.VerifiedCommit(node.id, f"leaf-sha-{node.id}", {"kind": "leaf"})

    try:
        verify, integrate, commit_integration = _stubs()
        task_executor = S.TaskExecutor(run_leaf=run_leaf, verify=verify, integrate=integrate,
                          commit_integration=commit_integration, decomposer=decomposer,
                          max_parallel=1)
        task_executor.execute(root)
    finally:
        if old is not None:
            os.environ["AI_ORG_MAX_DEPTH"] = old

    assert decomposed == [("root", 0), ("root.child", 1), ("root.child.child", 2)], decomposed
    assert leaves_executed == [("root.child.child.child", S.FLOOR_MAX_DEPTH)], leaves_executed
    assert all(depth < S.FLOOR_MAX_DEPTH for _, depth in decomposed), decomposed
    print("ok  always-split decomposer is capped by the deterministic depth ceiling")


def test_execute_custom_max_depth_stops_before_default_ceiling():
    root = S.TaskNode("root", kind=S.COMPOSITE, base_sha="BASE0", objective="large recursive feature")
    decomposed: list[tuple[str, int]] = []
    leaves_executed: list[tuple[str, int]] = []

    def decomposer(node):
        decomposed.append((node.id, node.depth))
        return [S.TaskNode(f"{node.id}.child", kind=S.COMPOSITE, base_sha=node.base_sha,
                           objective="split again", depth=node.depth + 1)]

    def run_leaf(node):
        leaves_executed.append((node.id, node.depth))
        return S.VerifiedCommit(node.id, f"leaf-sha-{node.id}", {"kind": "leaf"})

    verify, integrate, commit_integration = _stubs()
    task_executor = S.TaskExecutor(run_leaf=run_leaf, verify=verify, integrate=integrate,
                      commit_integration=commit_integration, decomposer=decomposer,
                      max_parallel=1, max_depth=1)
    task_executor.execute(root)

    assert decomposed == [("root", 0)], decomposed
    assert leaves_executed == [("root.child", 1)], leaves_executed
    print("ok  custom max_depth forces an always-split decomposer to stop at the configured depth")


def test_root_chosen_max_depth_overrides_default_and_caps_recursion():
    root = S.TaskNode("root", kind=S.COMPOSITE, base_sha="BASE0", objective="large recursive feature")
    prompts: list[str] = []
    leaves_executed: list[tuple[str, int]] = []

    def carrier(prompt):
        prompts.append(prompt)
        parent_id = prompt.split("Parent id: ", 1)[1].splitlines()[0]
        if parent_id == "root":
            return json.dumps({
                "max_depth": 2,
                "children": [
                    {"id": "root.child", "objective": "split again", "depends_on": [], "base_sha": None},
                ],
            })
        return json.dumps({
            "max_depth": 5,
            "children": [
                {"id": f"{parent_id}.child", "objective": "split again", "depends_on": [], "base_sha": None},
            ],
        })

    def run_leaf(node):
        leaves_executed.append((node.id, node.depth))
        return S.VerifiedCommit(node.id, f"leaf-sha-{node.id}", {"kind": "leaf"})

    verify, integrate, commit_integration = _stubs()
    task_executor = S.TaskExecutor(run_leaf=run_leaf, verify=verify, integrate=integrate,
                      commit_integration=commit_integration, decompose_carrier=carrier,
                      max_parallel=1, max_depth=3)
    task_executor.execute(root)

    assert task_executor.max_depth == 2, task_executor.max_depth
    assert leaves_executed == [("root.child.child", 2)], leaves_executed
    assert [p.split("Parent id: ", 1)[1].splitlines()[0] for p in prompts] == [
        "root", "root.child"], prompts
    print("ok  root-chosen max_depth overrides the default and caps later recursion")


def test_missing_root_max_depth_falls_back_to_configured_default():
    root = S.TaskNode("root", kind=S.COMPOSITE, base_sha="BASE0", objective="medium feature")
    leaves_executed: list[tuple[str, int]] = []

    def run_leaf(node):
        leaves_executed.append((node.id, node.depth))
        return S.VerifiedCommit(node.id, f"leaf-sha-{node.id}", {"kind": "leaf"})

    verify, integrate, commit_integration = _stubs()
    task_executor = S.TaskExecutor(run_leaf=run_leaf, verify=verify, integrate=integrate,
                      commit_integration=commit_integration,
                      decompose_carrier=_carrier_for_response({
                          "children": [
                              {"id": "child", "objective": "small leaf", "depends_on": [], "base_sha": None},
                          ],
                      }, []),
                      max_parallel=1, max_depth=1)
    task_executor.execute(root)

    assert task_executor.max_depth == 1, task_executor.max_depth
    assert leaves_executed == [("child", 1)], leaves_executed
    print("ok  missing root max_depth falls back to the configured default")


if __name__ == "__main__":
    test_decompose_parallel_and_serial_shapes()
    test_decompose_prompt_is_depth_aware()
    test_root_decompose_max_depth_is_clamped()
    test_execute_empty_decompose_runs_node_as_leaf()
    test_execute_decomposes_unsupplied_composite_and_runs_independent_children_concurrently()
    test_execute_serial_fallback_depends_on_chain_runs_in_order()
    test_execute_always_split_decomposer_stops_at_depth_ceiling()
    test_execute_custom_max_depth_stops_before_default_ceiling()
    test_root_chosen_max_depth_overrides_default_and_caps_recursion()
    test_missing_root_max_depth_falls_back_to_configured_default()
    print("\nall decomposer tests passed.")
