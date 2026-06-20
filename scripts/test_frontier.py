#!/usr/bin/env python3
"""Direct tests for the pure Frontier scheduler core."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from frontier import advance, ready_tasks, scope_conflict, validate_plan  # noqa: E402


def task(task_id, scope, *, depends_on=(), status="pending", run_id=None, pr_url=None):
    return {
        "id": task_id,
        "objective": f"do {task_id}",
        "scope": list(scope),
        "depends_on": list(depends_on),
        "status": status,
        "run_id": run_id,
        "pr_url": pr_url,
    }


def test_independent_disjoint_pending_tasks_are_all_ready():
    tasks = [
        task("backend", ["src/frontier.py"]),
        task("docs", ["docs/adr/*.md"]),
    ]

    assert [t["id"] for t in ready_tasks(tasks)] == ["backend", "docs"]


def test_dependent_task_waits_until_dependency_is_done():
    blocked = [
        task("api", ["src/server.py"], status="running"),
        task("ui", ["src/clay/*.js"], depends_on=["api"]),
    ]
    unblocked = [
        task("api", ["src/server.py"], status="done"),
        task("ui", ["src/clay/*.js"], depends_on=["api"]),
    ]

    assert [t["id"] for t in ready_tasks(blocked)] == []
    assert [t["id"] for t in ready_tasks(unblocked)] == ["ui"]


def test_diamond_dependencies_advance_in_readiness_waves():
    tasks = [
        task("A", ["work/a.py"]),
        task("B", ["work/b.py"], depends_on=["A"]),
        task("C", ["work/c.py"], depends_on=["A"]),
        task("D", ["work/d.py"], depends_on=["B", "C"]),
    ]

    assert [t["id"] for t in ready_tasks(tasks)] == ["A"]

    after_a = advance(tasks, "A", "done")
    assert [t["id"] for t in ready_tasks(after_a)] == ["B", "C"]

    after_b = advance(after_a, "B", "done")
    assert [t["id"] for t in ready_tasks(after_b)] == ["C"]

    after_c = advance(after_b, "C", "done")
    assert [t["id"] for t in ready_tasks(after_c)] == ["D"]


def test_scope_conflict_handles_disjoint_and_overlapping_globs():
    api = task("api", ["src/*.py"])
    frontier = task("frontier", ["src/frontier.py"])
    docs = task("docs", ["docs/adr/*.md"])
    ambiguous_py = task("py", ["src/*.py"])
    ambiguous_md = task("md", ["src/*.md"])

    assert scope_conflict(api, frontier) is True
    assert scope_conflict(frontier, docs) is False
    assert scope_conflict(ambiguous_py, ambiguous_md) is True


def test_scope_conflict_handles_cockpit_recursive_glob_overlap():
    recursive = task("recursive", ["src/**"])
    server = task("server", ["src/server.py"])

    assert scope_conflict(recursive, server) is True


def test_overlapping_scope_is_blocked_while_another_task_runs():
    tasks = [
        task("server", ["src/*.py"], status="running"),
        task("frontier", ["src/frontier.py"]),
        task("docs", ["docs/adr/*.md"]),
    ]

    assert [t["id"] for t in ready_tasks(tasks)] == ["docs"]


def test_overlapping_pending_candidates_are_serialized_by_plan_order():
    tasks = [
        task("server", ["src/*.py"]),
        task("frontier", ["src/frontier.py"]),
        task("docs", ["docs/adr/*.md"]),
    ]

    assert [t["id"] for t in ready_tasks(tasks)] == ["server", "docs"]


def test_validate_plan_reports_duplicates_unknown_dependencies_and_cycles():
    tasks = [
        task("dup", ["a.py"]),
        task("dup", ["b.py"]),
        task("again", ["c.py"]),
        task("again", ["d.py"]),
        task("unknown-a", ["e.py"], depends_on=["missing-a"]),
        task("unknown-b", ["f.py"], depends_on=["missing-b"]),
        task("cycle-a", ["g.py"], depends_on=["cycle-b"]),
        task("cycle-b", ["h.py"], depends_on=["cycle-a"]),
    ]

    errors = validate_plan(tasks)
    joined = "\n".join(errors)

    assert "duplicate id 'dup'" in joined, errors
    assert "duplicate id 'again'" in joined, errors
    assert "unknown-a" in joined and "missing-a" in joined, errors
    assert "unknown-b" in joined and "missing-b" in joined, errors
    assert any("dependency cycle" in error and "cycle-a" in error and "cycle-b" in error
               for error in errors), errors


def test_validate_plan_reports_self_dependency_cycle_with_task_id():
    tasks = [
        task("self", ["self.py"], depends_on=["self"]),
    ]

    errors = validate_plan(tasks)

    assert any("dependency cycle" in error and "self" in error for error in errors), errors


def test_advance_returns_new_plan_and_only_model_run_fields_are_applied():
    tasks = [task("build", ["src/frontier.py"])]

    advanced = advance(
        tasks,
        "build",
        "running",
        run_id="run-1",
        pr_url="https://example.test/pr/1",
        ignored="not a task field",
    )

    assert advanced is not tasks
    assert advanced[0] is not tasks[0]
    assert advanced[0]["status"] == "running"
    assert advanced[0]["run_id"] == "run-1"
    assert advanced[0]["pr_url"] == "https://example.test/pr/1"
    assert "ignored" not in advanced[0]
    assert tasks[0]["status"] == "pending"
    assert tasks[0]["run_id"] is None
    advanced[0]["scope"].append("later.py")
    assert tasks[0]["scope"] == ["src/frontier.py"]

    try:
        advance(tasks, "build", "waiting")
    except ValueError:
        pass
    else:
        raise AssertionError("invalid status should raise ValueError")

    try:
        advance(tasks, "missing", "done")
    except KeyError:
        pass
    else:
        raise AssertionError("unknown task id should raise KeyError")


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for test in tests:
        test()
    print(f"{len(tests)} passed")


if __name__ == "__main__":
    main()
