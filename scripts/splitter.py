"""Pure Splitter adapter for child Frontier task DAGs."""
from __future__ import annotations

__all__ = ["split", "HOUSE_RULES"]

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
        "Decompose the goal into a child task DAG for the Frontier. There are only TWO reasons to split:\n"
        "(1) PARALLELISM: separate genuinely independent work (no shared file scope, no dependency between "
        "them) so the Frontier can run it concurrently.\n"
        "(2) REVIEWABILITY: each task must be small enough that the adversarial reviewer (Linon) can verify "
        "it completely in one pass. This is about the change's IMPACT / blast radius — everything it "
        "touches across the system — not its line count: a one-line edit to a shared contract is hard to "
        "verify, a large edit confined to a leaf module is easy.\n"
        "Make each task as LARGE as possible while still satisfying both — do NOT decompose to the smallest "
        "unit. Over-splitting inherently-sequential work just pays the heavy review cost N times with no "
        "parallel gain. Start COARSE: a task that later proves too big for the reviewer is split further "
        "automatically (recursion), so you do not need to pre-split everything. Order tasks by depends_on; "
        "isolate a high-impact shared-interface change into its own task.\n"
        "A SCAFFOLD / greenfield task — creating a project's skeleton (interdependent files like the "
        "manifest, the entry module, and config that must all exist together) — is ATOMIC: emit it as ONE "
        "task whose scope lists ALL the skeleton files, and do NOT split it. A skeleton cannot be built one "
        "file at a time; splitting it only yields failing sub-scaffolds. Likewise, never label a task "
        "'minimal'/'atomic' and then split it.\n"
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
