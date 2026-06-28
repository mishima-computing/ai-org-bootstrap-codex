"""Git-driven AI Org driver.

Git is the durable state. ``advance`` inspects committed state files on the
``ai-org-state`` branch plus standard code branches, performs one ready action,
records the transition as a new state-branch commit, and returns.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, is_dataclass
import copy
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from . import contribution
from .maintainers import mainline, subsystem
from .platform import git, state
from .rfc import decompose, review
from .rfc.receive import RFC, receive
from .rfc.task import Task

STATE_BRANCH = state.STATE_BRANCH
STATE_REF = state.STATE_REF
MAINLINE_REF = state.MAINLINE_REF


def advance(rfc: RFC, repo: str | Path) -> dict[str, Any]:
    """Advance exactly one ready step for ``rfc`` in ``repo``.

    RFC/verdict/plan state is reconstructed from committed JSON files under
    ``state/<rfc-id>/`` on ``ai-org-state``. Code progress is represented by
    normal branches under ``refs/heads/ai-org/...``.
    """
    repo = Path(repo).resolve()
    rfc_id = _rfc_id(rfc)

    verdict = state.read_json(repo, rfc_id, "verdict.json")
    if verdict is None:
        result = review.run_rfc_review(rfc, repo)
        verdict = _verdict_doc(rfc_id, result)
        rfc_doc = _rfc_doc(
            rfc,
            rfc_id,
            phase="rejected" if verdict["status"] == "nak" else "review",
            status="rejected" if verdict["status"] == "nak" else "approved",
        )
        state_push = state.commit_change(
            repo,
            rfc_id,
            {"rfc.json": rfc_doc, "verdict.json": verdict},
            {"type": "review", "phase": "review", "status": verdict["status"]},
        )
        return {
            "action": "review",
            "status": verdict.get("status"),
            "terminal": verdict.get("status") == "nak",
            "rfc_id": rfc_id,
            "state_push": state_push,
        }

    if verdict.get("status") == "nak":
        return {"action": "none", "status": "rejected", "terminal": True, "rfc_id": rfc_id}

    plan = state.read_json(repo, rfc_id, "plan.json")
    if plan is None:
        tasks = decompose.decompose(rfc, repo)
        plan = _plan_doc(rfc_id, tasks)
        rfc_doc = _rfc_doc(rfc, rfc_id, phase="plan", status="planned")
        state_push = state.commit_change(
            repo,
            rfc_id,
            {"rfc.json": rfc_doc, "plan.json": plan},
            {
                "type": "decompose",
                "phase": "plan",
                "status": "planned",
                "tasks": [task.id for task in tasks],
            },
        )
        return {
            "action": "decompose",
            "status": "planned",
            "terminal": False,
            "rfc_id": rfc_id,
            "tasks": [task["id"] for task in plan["tasks"]],
            "state_push": state_push,
        }

    tasks = [_task_from_json(raw) for raw in plan.get("tasks", [])]
    if not tasks:
        if not git.ref_exists(repo, MAINLINE_REF):
            git.update_ref(repo, MAINLINE_REF, "HEAD")
        mainline_push = git.push_ref(repo, MAINLINE_REF)
        rfc_doc = _rfc_doc(rfc, rfc_id, phase="done", status="done")
        state_push = state.commit_change(
            repo,
            rfc_id,
            {"rfc.json": rfc_doc},
            {"type": "complete-empty-plan", "phase": "done", "status": "done"},
        )
        return {
            "action": "none",
            "status": "done",
            "terminal": True,
            "rfc_id": rfc_id,
            "mainline": MAINLINE_REF,
            "push": mainline_push,
            "state_push": state_push,
        }

    for task in tasks:
        contrib_ref = _contribution_ref(task)
        if not git.ref_exists(repo, contrib_ref):
            branch = _make_contribution_in_repo(repo, rfc, task)
            if branch != contrib_ref and git.ref_exists(repo, branch):
                git.update_ref(repo, contrib_ref, branch)
            if not git.ref_exists(repo, contrib_ref):
                rfc_doc = _rfc_doc(rfc, rfc_id, phase="contribution", status="rejected")
                plan = _with_patch_state(plan, task.id, phase="contribution", status="reject")
                state_push = state.commit_change(
                    repo,
                    rfc_id,
                    {"rfc.json": rfc_doc, "plan.json": plan},
                    {
                        "type": "contribution",
                        "patch": task.id,
                        "phase": "contribution",
                        "status": "reject",
                    },
                )
                return {
                    "action": "contribution",
                    "status": "reject",
                    "terminal": True,
                    "rfc_id": rfc_id,
                    "patch": task.id,
                    "state_push": state_push,
                }
            rfc_doc = _rfc_doc(rfc, rfc_id, phase="contribution", status="active")
            plan = _with_patch_state(
                plan,
                task.id,
                phase="contribution",
                status="created",
                contribution_ref=contrib_ref,
            )
            contrib_push = git.push_ref(repo, contrib_ref)
            state_push = state.commit_change(
                repo,
                rfc_id,
                {"rfc.json": rfc_doc, "plan.json": plan},
                {
                    "type": "contribution",
                    "patch": task.id,
                    "phase": "contribution",
                    "status": "created",
                    "ref": contrib_ref,
                },
            )
            return {
                "action": "contribution",
                "status": "created",
                "terminal": False,
                "rfc_id": rfc_id,
                "patch": task.id,
                "ref": contrib_ref,
                "push": contrib_push,
                "state_push": state_push,
            }

    for task in tasks:
        contrib_ref = _contribution_ref(task)
        subsystem_ref = _subsystem_ref(task)
        if not git.ref_exists(repo, subsystem_ref):
            result = subsystem.review_and_integrate(contrib_ref, repo)
            if result == "reject":
                rfc_doc = _rfc_doc(rfc, rfc_id, phase="subsystem", status="rejected")
                plan = _with_patch_state(plan, task.id, phase="subsystem", status="reject")
                state_push = state.commit_change(
                    repo,
                    rfc_id,
                    {"rfc.json": rfc_doc, "plan.json": plan},
                    {
                        "type": "subsystem",
                        "patch": task.id,
                        "phase": "subsystem",
                        "status": "reject",
                    },
                )
                return {
                    "action": "subsystem",
                    "status": "reject",
                    "terminal": True,
                    "rfc_id": rfc_id,
                    "patch": task.id,
                    "state_push": state_push,
                }
            if result != subsystem_ref and git.ref_exists(repo, result):
                git.update_ref(repo, subsystem_ref, result)
            if not git.ref_exists(repo, subsystem_ref):
                rfc_doc = _rfc_doc(rfc, rfc_id, phase="subsystem", status="rejected")
                plan = _with_patch_state(plan, task.id, phase="subsystem", status="reject")
                state_push = state.commit_change(
                    repo,
                    rfc_id,
                    {"rfc.json": rfc_doc, "plan.json": plan},
                    {
                        "type": "subsystem",
                        "patch": task.id,
                        "phase": "subsystem",
                        "status": "reject",
                    },
                )
                return {
                    "action": "subsystem",
                    "status": "reject",
                    "terminal": True,
                    "rfc_id": rfc_id,
                    "patch": task.id,
                    "state_push": state_push,
                }
            rfc_doc = _rfc_doc(rfc, rfc_id, phase="subsystem", status="active")
            plan = _with_patch_state(
                plan,
                task.id,
                phase="subsystem",
                status="integrated",
                contribution_ref=contrib_ref,
                subsystem_ref=subsystem_ref,
            )
            subsystem_push = git.push_ref(repo, subsystem_ref)
            state_push = state.commit_change(
                repo,
                rfc_id,
                {"rfc.json": rfc_doc, "plan.json": plan},
                {
                    "type": "subsystem",
                    "patch": task.id,
                    "phase": "subsystem",
                    "status": "integrated",
                    "ref": subsystem_ref,
                },
            )
            return {
                "action": "subsystem",
                "status": "integrated",
                "terminal": False,
                "rfc_id": rfc_id,
                "patch": task.id,
                "ref": subsystem_ref,
                "push": subsystem_push,
                "state_push": state_push,
            }

    subsystem_refs = [_subsystem_ref(task) for task in tasks]
    if git.mainline_contains_all(repo, subsystem_refs, MAINLINE_REF):
        rfc_doc = _rfc_doc(rfc, rfc_id, phase="done", status="done")
        plan = _with_all_patch_state(plan, phase="mainline", status="integrated", mainline_ref=MAINLINE_REF)
        state_push = state.commit_change(
            repo,
            rfc_id,
            {"rfc.json": rfc_doc, "plan.json": plan},
            {"type": "mainline-observed", "phase": "done", "status": "done", "ref": MAINLINE_REF},
        )
        return {
            "action": "none",
            "status": "done",
            "terminal": True,
            "rfc_id": rfc_id,
            "mainline": MAINLINE_REF,
            "state_push": state_push,
        }

    result = mainline.review_and_integrate(subsystem_refs, repo)
    if result == "reject":
        rfc_doc = _rfc_doc(rfc, rfc_id, phase="mainline", status="rejected")
        state_push = state.commit_change(
            repo,
            rfc_id,
            {"rfc.json": rfc_doc},
            {"type": "mainline", "phase": "mainline", "status": "reject"},
        )
        return {
            "action": "mainline",
            "status": "reject",
            "terminal": True,
            "rfc_id": rfc_id,
            "state_push": state_push,
        }
    if result != MAINLINE_REF and git.ref_exists(repo, result):
        git.update_ref(repo, MAINLINE_REF, result)
    if not git.mainline_contains_all(repo, subsystem_refs, MAINLINE_REF):
        rfc_doc = _rfc_doc(rfc, rfc_id, phase="mainline", status="rejected")
        state_push = state.commit_change(
            repo,
            rfc_id,
            {"rfc.json": rfc_doc},
            {"type": "mainline", "phase": "mainline", "status": "reject"},
        )
        return {
            "action": "mainline",
            "status": "reject",
            "terminal": True,
            "rfc_id": rfc_id,
            "state_push": state_push,
        }

    rfc_doc = _rfc_doc(rfc, rfc_id, phase="done", status="done")
    plan = _with_all_patch_state(plan, phase="mainline", status="integrated", mainline_ref=MAINLINE_REF)
    mainline_push = git.push_ref(repo, MAINLINE_REF)
    state_push = state.commit_change(
        repo,
        rfc_id,
        {"rfc.json": rfc_doc, "plan.json": plan},
        {"type": "mainline", "phase": "done", "status": "integrated", "ref": MAINLINE_REF},
    )
    return {
        "action": "mainline",
        "status": "integrated",
        "terminal": True,
        "rfc_id": rfc_id,
        "mainline": MAINLINE_REF,
        "push": mainline_push,
        "state_push": state_push,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Advance an RFC through the Git-state AI Org driver.")
    parser.add_argument("rfc", help="Path to an RFC JSON file.")
    parser.add_argument("--repo", default=".", help="Target Git repository.")
    args = parser.parse_args(argv)

    rfc = receive(args.rfc)
    while True:
        record = advance(rfc, args.repo)
        print(json.dumps(record, sort_keys=True))
        if record.get("terminal"):
            return 0 if record.get("status") in {"done", "integrated"} else 1


def _rfc_id(rfc: RFC) -> str:
    payload = {
        "title": rfc.title,
        "problem": rfc.problem,
        "proposed_change": rfc.proposed_change,
        "interface_sketch": rfc.interface_sketch,
        "notes": rfc.notes,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:12]


def _contribution_ref(task: Task) -> str:
    return state.contribution_ref(task.id)


def _subsystem_ref(task: Task) -> str:
    return state.subsystem_ref_for_patch(task.id)


def _make_contribution_in_repo(repo: Path, rfc: RFC, task: Task) -> str:
    cwd = Path.cwd()
    try:
        os.chdir(repo)
        return contribution.make(rfc, task)
    finally:
        os.chdir(cwd)


def _rfc_doc(rfc: RFC, rfc_id: str, *, phase: str, status: str) -> dict[str, Any]:
    doc = {
        "schema_version": 1,
        "rfc_id": rfc_id,
        "phase": phase,
        "status": status,
        "title": rfc.title,
        "problem": rfc.problem,
        "proposed_change": rfc.proposed_change,
        "interface_sketch": rfc.interface_sketch,
        "notes": rfc.notes,
    }
    state.validate_state_doc("rfc.json", doc)
    return doc


def _verdict_doc(rfc_id: str, result: Any) -> dict[str, Any]:
    verdict = _jsonable(result)
    doc = {
        "schema_version": 1,
        "rfc_id": rfc_id,
        "phase": "review",
        "status": str(verdict.get("status", "")),
        "verdict": verdict,
    }
    state.validate_state_doc("verdict.json", doc)
    return doc


def _plan_doc(rfc_id: str, tasks: list[Task]) -> dict[str, Any]:
    patches = {
        task.id: {
            "phase": "plan",
            "status": "planned",
            "contribution_ref": None,
            "subsystem_ref": None,
            "mainline_ref": None,
        }
        for task in tasks
    }
    doc = {
        "schema_version": 1,
        "rfc_id": rfc_id,
        "phase": "plan",
        "status": "planned",
        "tasks": [_task_to_json(task) for task in tasks],
        "patches": patches,
    }
    state.validate_state_doc("plan.json", doc)
    return doc


def _with_patch_state(plan: dict[str, Any], patch_id: str, *, phase: str, status: str, **refs: Any) -> dict[str, Any]:
    next_plan = copy.deepcopy(plan)
    next_plan["phase"] = phase
    next_plan["status"] = "active" if status not in {"reject", "integrated"} else status
    patch = dict(next_plan.setdefault("patches", {}).get(patch_id, {}))
    patch.update({"phase": phase, "status": status})
    patch.update(refs)
    next_plan["patches"][patch_id] = patch
    state.validate_state_doc("plan.json", next_plan)
    return next_plan


def _with_all_patch_state(plan: dict[str, Any], *, phase: str, status: str, **refs: Any) -> dict[str, Any]:
    next_plan = copy.deepcopy(plan)
    next_plan["phase"] = phase
    next_plan["status"] = status
    for raw in next_plan.get("tasks", []):
        patch_id = str(raw.get("id", ""))
        patch = dict(next_plan.setdefault("patches", {}).get(patch_id, {}))
        patch.update({"phase": phase, "status": status})
        patch.update(refs)
        next_plan["patches"][patch_id] = patch
    state.validate_state_doc("plan.json", next_plan)
    return next_plan


def _task_to_json(task: Task) -> dict[str, Any]:
    return asdict(task)


def _task_from_json(raw: dict[str, Any]) -> Task:
    return Task(
        id=str(raw.get("id", "")),
        objective=str(raw.get("objective", "")),
        contract=str(raw.get("contract", "")),
        base_sha=str(raw.get("base_sha", "")),
        scope=list(raw.get("scope", [])),
        checks=list(raw.get("checks", [])),
        depends_on=list(raw.get("depends_on", [])),
    )


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if hasattr(value, "__dict__"):
        return _jsonable(vars(value))
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
