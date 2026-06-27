#!/usr/bin/env python3
"""Direct tests for the pure Frontier scheduler core."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from frontier import advance, path_in_scope, ready_tasks, scope_conflict, validate_plan  # noqa: E402


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


def test_path_in_scope_exact_glob_dir_prefix_and_rejects_unsafe_paths():
    scope = ["src/app.py", "docs/*.md", "pkg/"]

    assert path_in_scope("src/app.py", scope) is True
    assert path_in_scope("docs/adr.md", scope) is True
    assert path_in_scope("pkg/nested/mod.py", scope) is True
    assert path_in_scope("src/other.py", scope) is False
    assert path_in_scope("/abs/src/app.py", scope) is False
    assert path_in_scope("../src/app.py", scope) is False
    assert path_in_scope("src/app.py", ["/abs/*", "../*", "safe.py"]) is False


def test_scope_conflict_treats_directory_entry_as_containing_files_under_it():
    """A dir-scoped leaf and a file-under-it sibling must CONFLICT (never co-schedule), so they

    cannot both merge the same file. The scheduler's containment must equal the merge guard's:
    every path the guard would admit under a dir entry is a scheduling conflict for that entry.
    """
    dir_slash = task("dir-slash", ["src/api/"])
    bare_dir = task("bare-dir", ["src/api"])
    leaf = task("leaf", ["src/api/routes.py"])
    disjoint = task("disjoint", ["web/login/"])

    # trailing-slash dir contains the file under it -> conflict
    assert scope_conflict(dir_slash, leaf) is True
    assert scope_conflict(leaf, dir_slash) is True
    # bare directory name contains the file under it too -> conflict
    assert scope_conflict(bare_dir, leaf) is True
    assert scope_conflict(leaf, bare_dir) is True
    # truly disjoint directories still co-schedule
    assert scope_conflict(dir_slash, disjoint) is False
    assert scope_conflict(disjoint, dir_slash) is False
    # the two co-scheduling dir leaves do not block each other in a wave
    assert [t["id"] for t in ready_tasks([dir_slash, disjoint])] == ["dir-slash", "disjoint"]
    # the dir leaf and the file-under-it leaf ARE serialized (only the first is ready)
    assert [t["id"] for t in ready_tasks([dir_slash, leaf])] == ["dir-slash"]


def test_scope_conflict_treats_glob_literal_prefix_as_subtree():
    dir_slash = task("dir-slash", ["src/api/"])
    bare_dir = task("bare-dir", ["src/api"])
    py_glob = task("py-glob", ["src/api/*.py"])
    one_char_glob = task("one-char-glob", ["src/api/?.py"])
    src_dir = task("src-dir", ["src/"])
    nested_glob = task("nested-glob", ["src/api/**/*.py"])
    api_py = task("api-py", ["src/api/*.py"])
    web_ts = task("web-ts", ["web/login/*.ts"])

    assert scope_conflict(dir_slash, py_glob) is True
    assert scope_conflict(py_glob, dir_slash) is True
    assert scope_conflict(bare_dir, py_glob) is True
    assert scope_conflict(py_glob, bare_dir) is True
    assert scope_conflict(dir_slash, one_char_glob) is True
    assert scope_conflict(one_char_glob, dir_slash) is True
    assert scope_conflict(src_dir, nested_glob) is True
    assert scope_conflict(nested_glob, src_dir) is True

    assert scope_conflict(api_py, web_ts) is False
    assert scope_conflict(web_ts, api_py) is False
    assert [t["id"] for t in ready_tasks([api_py, web_ts])] == ["api-py", "web-ts"]


def test_scheduler_directory_containment_matches_merge_guard():
    """Demonstrate the scheduler and the merge guard share IDENTICAL directory semantics, comparing

    the scheduler directly against the real merge guard (controller_goal._out_of_scope): for a dir
    entry, a path the guard admits in-scope is exactly a path the scheduler flags as a conflict, for
    both bare-directory and trailing-slash forms.
    """
    import controller_goal as cg

    for entry in ("src/api/", "src/api"):
        for path in ("src/api/routes.py", "src/api/nested/mod.py", "src/api"):
            # merge guard: the dir entry CONTAINS the path -> nothing out of scope
            assert cg._out_of_scope([path], [entry]) == []
            # scheduler: a leaf scoped to that path conflicts with the dir-scoped leaf
            assert scope_conflict(task("dir", [entry]), task("file", [path])) is True
        # a path the guard rejects (out of scope) is one the scheduler also clears as disjoint
        assert cg._out_of_scope(["web/login/view.py"], [entry]) == ["web/login/view.py"]
        assert scope_conflict(task("dir", [entry]), task("other", ["web/login/view.py"])) is False


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


def test_empty_scope_is_repo_wide_conflict_and_runs_alone():
    tasks = [
        task("unscoped", []),
        task("scoped", ["src/app.py"]),
        task("docs", ["docs/readme.md"]),
    ]

    assert scope_conflict(tasks[0], tasks[1]) is True
    assert scope_conflict(tasks[1], tasks[0]) is True
    assert [t["id"] for t in ready_tasks(tasks)] == ["unscoped"]

    after_unscoped = advance(tasks, "unscoped", "done")
    assert [t["id"] for t in ready_tasks(after_unscoped)] == ["scoped", "docs"]

    later_unscoped = [
        task("scoped", ["src/app.py"]),
        task("unscoped", []),
        task("docs", ["docs/readme.md"]),
    ]
    ready_ids = [t["id"] for t in ready_tasks(later_unscoped)]
    assert "unscoped" not in ready_ids and ready_ids == ["scoped", "docs"], ready_ids


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
