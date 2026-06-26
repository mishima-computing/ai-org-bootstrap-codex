"""Pure Frontier scheduler core for recursive task trees.

Tasks form sibling lists at each tree level. Each sibling list owns its ids,
depends_on references, and dependency cycles locally. A task with non-empty
children is an internal node: it is never returned as runnable, and terminal
status is derived from descendants. Tasks without children are leaves.
The public functions are pure helpers that validate plans, find ready leaves,
check scope conflicts, derive node status, and immutably advance one task.
"""
from __future__ import annotations

import fnmatch
from pathlib import PurePosixPath

__all__ = [
    "validate_plan",
    "ready_tasks",
    "advance",
    "node_status",
    "scope_conflict",
    "path_in_scope",
]


def _copy_task(task):
    copied = dict(task)
    for key in ("scope", "depends_on"):
        if isinstance(copied.get(key), list):
            copied[key] = list(copied[key])
    if isinstance(copied.get("children"), list):
        copied["children"] = [_copy_task(child) for child in copied["children"]]
    return copied


def _children(task):
    children = task.get("children")
    if isinstance(children, list):
        return children
    return []


def _has_glob(pattern):
    return any(char in pattern for char in "*?[")


def _literal_prefix(pattern):
    first_glob = len(pattern)
    for char in "*?[":
        index = pattern.find(char)
        if index != -1:
            first_glob = min(first_glob, index)
    return pattern[:first_glob]


def _scope_contains(container, member):
    """True when a non-glob entry CONTAINS a path under it — the merge guard's containment.

    A bare-directory (`"dir"`) or trailing-slash (`"dir/"`) entry admits every path beneath it; an
    exact entry contains only itself. This mirrors controller_goal._out_of_scope (which treats every
    non-glob entry as a directory prefix) and frontier.path_in_scope's trailing-slash dir handling, so
    the scheduler's conflict notion is a superset-or-equal of what the merge guard flags out-of-scope.
    """
    container = container.replace("\\", "/").rstrip("/")
    member = member.replace("\\", "/").rstrip("/")
    return member == container or member.startswith(container + "/")


def _conflict_subtree(pattern):
    pattern = pattern.replace("\\", "/")
    if _has_glob(pattern):
        pattern = _literal_prefix(pattern)
    return pattern.rstrip("/")


def _subtree_contains(container, member):
    if container == "" or member == "":
        return True
    return _scope_contains(container, member)


def _patterns_may_overlap(left, right):
    left_glob = _has_glob(left)
    right_glob = _has_glob(right)
    if not left_glob and not right_glob:
        # Directory-containment-aware (NOT mere equality): a dir-scoped entry overlaps any path
        # under it, just as the merge guard would. Without this, a dir leaf (`src/api/`) and a
        # file-under-it sibling (`src/api/routes.py`) co-schedule and both merge the same file.
        return _scope_contains(left, right) or _scope_contains(right, left)

    left_subtree = _conflict_subtree(left)
    right_subtree = _conflict_subtree(right)
    if _subtree_contains(left_subtree, right_subtree) or _subtree_contains(right_subtree, left_subtree):
        return True

    if not left_glob:
        return fnmatch.fnmatchcase(left, right)
    if not right_glob:
        return fnmatch.fnmatchcase(right, left)

    left_prefix = _literal_prefix(left)
    right_prefix = _literal_prefix(right)
    if left_prefix and right_prefix:
        if not (left_prefix.startswith(right_prefix) or right_prefix.startswith(left_prefix)):
            return False
    return True


def _scope_entries(task):
    return [str(entry).strip() for entry in task.get("scope", ()) if str(entry).strip()]


def scope_conflict(task_a, task_b):
    """Return True unless the two task scopes are proven disjoint.

    An empty/absent scope is an unconstrained writer, so treat it as repo-wide conflict.
    """
    left_entries = _scope_entries(task_a)
    right_entries = _scope_entries(task_b)
    if not left_entries or not right_entries:
        return True
    for left in left_entries:
        for right in right_entries:
            if _patterns_may_overlap(str(left), str(right)):
                return True
    return False


def _safe_rel(path: str) -> str | None:
    path = str(path or "").strip().replace("\\", "/")
    p = PurePosixPath(path)
    if not path or p.is_absolute() or ".." in p.parts:
        return None
    return str(p)


def path_in_scope(path: str, scope: list[str] | tuple[str, ...]) -> bool:
    """Return true when a changed relative path is covered by a declared scope entry.

    Scope entries support exact files, shell globs, and directory prefixes. Absolute paths and paths
    containing `..` are rejected for both the changed path and the scope declaration.
    """
    rel = _safe_rel(path)
    if rel is None:
        return False
    for raw in scope or ():
        pattern = _safe_rel(str(raw).strip())
        if pattern is None:
            continue
        if _has_glob(pattern):
            if fnmatch.fnmatchcase(rel, pattern):
                return True
            continue
        if rel == pattern:
            return True
        prefix = pattern.rstrip("/")
        if str(raw).strip().replace("\\", "/").endswith("/") and rel.startswith(prefix + "/"):
            return True
    return False


def _cycle_key(cycle):
    core = cycle[:-1]
    rotations = [tuple(core[index:] + core[:index]) for index in range(len(core))]
    return min(rotations)


def _validate_siblings(tasks, errors):
    seen = set()
    duplicate_ids = []
    ids = []
    for task in tasks:
        task_id = task.get("id")
        ids.append(task_id)
        if task_id in seen and task_id not in duplicate_ids:
            duplicate_ids.append(task_id)
        seen.add(task_id)

    for task_id in duplicate_ids:
        errors.append(f"duplicate id {task_id!r}")

    known_ids = set(ids)
    graph = {}
    for task in tasks:
        task_id = task.get("id")
        graph.setdefault(task_id, [])
        for dependency in task.get("depends_on", ()):
            if dependency not in known_ids:
                errors.append(f"task {task_id!r} depends_on unknown id {dependency!r}")
            else:
                graph[task_id].append(dependency)

    state = {}
    stack = []
    reported_cycles = set()

    def visit(task_id):
        state[task_id] = "visiting"
        stack.append(task_id)
        for dependency in graph.get(task_id, ()):
            dependency_state = state.get(dependency)
            if dependency_state == "visiting":
                start = stack.index(dependency)
                cycle = stack[start:] + [dependency]
                key = _cycle_key(cycle)
                if key not in reported_cycles:
                    reported_cycles.add(key)
                    errors.append(f"dependency cycle: {' -> '.join(cycle)}")
            elif dependency_state != "done":
                visit(dependency)
        stack.pop()
        state[task_id] = "done"

    for task_id in graph:
        if state.get(task_id) is None:
            visit(task_id)

    for task in tasks:
        children = _children(task)
        if children:
            _validate_siblings(children, errors)


def validate_plan(tasks):
    """Return aggregate validation errors for duplicate ids, unknown deps, and cycles."""
    errors = []
    _validate_siblings(tasks, errors)
    return errors


def node_status(task):
    """Return stored leaf status or derived internal-node status."""
    children = _children(task)
    if not children:
        return task.get("status")
    child_statuses = [node_status(child) for child in children]
    if any(status == "failed" for status in child_statuses):
        return "failed"
    if all(status == "done" for status in child_statuses):
        return "done"
    status = task.get("status", "pending")
    if status in ("pending", "running"):
        return status
    return "pending"


def _running_leaves(tasks):
    running = []
    for task in tasks:
        children = _children(task)
        if children:
            running.extend(_running_leaves(children))
        elif task.get("status") == "running":
            running.append(task)
    return running


def _collect_ready(tasks, running, selected):
    ready = []
    status_by_id = {task.get("id"): node_status(task) for task in tasks}
    for task in tasks:
        if any(status_by_id.get(dep) != "done" for dep in task.get("depends_on", ())):
            continue
        children = _children(task)
        if children:
            ready.extend(_collect_ready(children, running, selected))
            continue
        if (task.get("status") or "pending") != "pending":   # a fresh leaf (no status) is pending,
            continue                                          # consistent with node_status's default
        if any(scope_conflict(task, running_task) for running_task in running):
            continue
        if any(scope_conflict(task, selected_task) for selected_task in selected):
            continue
        copied = _copy_task(task)
        ready.append(copied)
        selected.append(copied)
    return ready


def ready_tasks(tasks):
    """Return pending leaf tasks that can run now, preserving plan order."""
    running = _running_leaves(tasks)
    selected = []
    return _collect_ready(tasks, running, selected)


def _advance_siblings(tasks, task_id, status, fields, found):
    advanced = []
    for task in tasks:
        copied = _copy_task(task)
        if not found and copied.get("id") == task_id:
            found = True
            copied["status"] = status
            for field in ("run_id", "pr_url"):
                if field in fields:
                    copied[field] = fields[field]
        elif not found and _children(copied):
            copied["children"], found = _advance_siblings(
                copied["children"],
                task_id,
                status,
                fields,
                found,
            )
        advanced.append(copied)
    return advanced, found


def advance(tasks, task_id, status, **fields):
    """Return a new plan with one task advanced to a valid status."""
    if status not in ("pending", "running", "done", "failed", "blocked_hitl"):
        raise ValueError(f"invalid status {status!r}")

    advanced, found = _advance_siblings(tasks, task_id, status, fields, False)
    if not found:
        raise KeyError(task_id)

    return advanced
