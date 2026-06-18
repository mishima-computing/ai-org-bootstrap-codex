"""Pure Splitter adapter for child Frontier task DAGs."""
from __future__ import annotations

import json


HOUSE_RULES = """Definition of done:
- No silent failures.
- Handle every error path with robust integration.
- Keep scope to the declared files.
- Reuse existing helpers.
- Do not invent unrequested behaviour.
"""

_TASK_KEYS = {"id", "objective", "scope", "depends_on"}


def _default_carrier(_prompt):
    return "[]"


def _build_prompt(goal, context):
    return (
        "Decompose the goal into a child task DAG for the Frontier.\n"
        "Split by the change's IMPACT (blast radius), NOT by diff size. The reviewer verifies everything a "
        "change touches across the system — its callers and dependents — not the line count. A one-line "
        "edit to a widely-depended-on core (a shared API, a contract, a base symbol) has a large blast "
        "radius and is hard to verify; a large edit confined to one leaf module is easy. So make each task "
        "ONE change with a small, contained blast radius: isolate a high-impact change to a shared "
        "interface into its own task, and order tasks by depends_on so each one's ripple can be verified "
        "alone. Shrinking a high-impact change into smaller diffs does NOT help — its impact is unchanged; "
        "isolate and sequence it instead.\n"
        "Return only a JSON array of task objects. Each task must contain exactly "
        "id, objective, scope, and depends_on. id and objective are strings; "
        "scope and depends_on are lists of strings.\n\n"
        f"Goal:\n{goal}\n\n"
        f"Codebase context:\n{context}\n\n"
        f"HOUSE_RULES:\n{HOUSE_RULES}"
    )


def _normalize_string_list(value):
    if not isinstance(value, list):
        raise ValueError("expected list")
    if any(not isinstance(item, str) for item in value):
        raise ValueError("expected list of strings")
    return list(value)


def _normalize_task(task):
    if not isinstance(task, dict):
        raise ValueError("expected task object")
    if set(task) != _TASK_KEYS:
        raise ValueError("expected exact task keys")
    if not isinstance(task["id"], str):
        raise ValueError("expected string id")
    if not isinstance(task["objective"], str):
        raise ValueError("expected string objective")
    return {
        "id": task["id"],
        "objective": task["objective"],
        "scope": _normalize_string_list(task["scope"]),
        "depends_on": _normalize_string_list(task["depends_on"]),
    }


def _normalize_tasks(value):
    if not isinstance(value, list):
        raise ValueError("expected task array")
    return [_normalize_task(task) for task in value]


def _validate_with_frontier(tasks):
    try:
        from frontier import validate_plan
    except ImportError:
        from frontier import validate_plan

    return validate_plan(tasks)


def split(goal, context, carrier=_default_carrier):
    """Return validated child Frontier tasks, or [] on any carrier/schema error."""
    try:
        prompt = _build_prompt(goal, context)
        carrier_output = carrier(prompt)
        tasks = _normalize_tasks(json.loads(carrier_output))
        if _validate_with_frontier(tasks):
            return []
        return tasks
    except Exception:
        return []
