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
import re
import shutil
import subprocess
import tempfile
from typing import Any

from . import contribution
from .maintainers import mainline, subsystem
from .rfc import decompose, review
from .rfc.receive import RFC, receive
from .rfc.task import Task

STATE_BRANCH = "ai-org-state"
STATE_REF = f"refs/heads/{STATE_BRANCH}"
STATE_ROOT = "state"
MAINLINE_REF = "refs/heads/ai-org/mainline"

_ZERO_OID = "0" * 40
_CAS_RETRIES = 5


def advance(rfc: RFC, repo: str | Path) -> dict[str, Any]:
    """Advance exactly one ready step for ``rfc`` in ``repo``.

    RFC/verdict/plan state is reconstructed from committed JSON files under
    ``state/<rfc-id>/`` on ``ai-org-state``. Code progress is represented by
    normal branches under ``refs/heads/ai-org/...``.
    """
    repo = Path(repo).resolve()
    rfc_id = _rfc_id(rfc)

    verdict = _read_state_json(repo, rfc_id, "verdict.json")
    if verdict is None:
        result = review.run_rfc_review(rfc, repo)
        verdict = _verdict_doc(rfc_id, result)
        rfc_doc = _rfc_doc(
            rfc,
            rfc_id,
            phase="rejected" if verdict["status"] == "nak" else "review",
            status="rejected" if verdict["status"] == "nak" else "approved",
        )
        _commit_state_change(
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
        }

    if verdict.get("status") == "nak":
        return {"action": "none", "status": "rejected", "terminal": True, "rfc_id": rfc_id}

    plan = _read_state_json(repo, rfc_id, "plan.json")
    if plan is None:
        tasks = decompose.decompose(rfc, repo)
        plan = _plan_doc(rfc_id, tasks)
        rfc_doc = _rfc_doc(rfc, rfc_id, phase="plan", status="planned")
        _commit_state_change(
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
        }

    tasks = [_task_from_json(raw) for raw in plan.get("tasks", [])]
    if not tasks:
        if not _ref_exists(repo, MAINLINE_REF):
            _update_ref(repo, MAINLINE_REF, "HEAD")
        rfc_doc = _rfc_doc(rfc, rfc_id, phase="done", status="done")
        _commit_state_change(
            repo,
            rfc_id,
            {"rfc.json": rfc_doc},
            {"type": "complete-empty-plan", "phase": "done", "status": "done"},
        )
        return {"action": "none", "status": "done", "terminal": True, "rfc_id": rfc_id, "mainline": MAINLINE_REF}

    for task in tasks:
        contrib_ref = _contribution_ref(task)
        if not _ref_exists(repo, contrib_ref):
            branch = _make_contribution_in_repo(repo, rfc, task)
            if branch != contrib_ref and _ref_exists(repo, branch):
                _update_ref(repo, contrib_ref, branch)
            if not _ref_exists(repo, contrib_ref):
                rfc_doc = _rfc_doc(rfc, rfc_id, phase="contribution", status="rejected")
                plan = _with_patch_state(plan, task.id, phase="contribution", status="reject")
                _commit_state_change(
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
                }
            rfc_doc = _rfc_doc(rfc, rfc_id, phase="contribution", status="active")
            plan = _with_patch_state(
                plan,
                task.id,
                phase="contribution",
                status="created",
                contribution_ref=contrib_ref,
            )
            _commit_state_change(
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
            }

    for task in tasks:
        contrib_ref = _contribution_ref(task)
        subsystem_ref = _subsystem_ref(task)
        if not _ref_exists(repo, subsystem_ref):
            result = subsystem.review_and_integrate(contrib_ref, repo)
            if result == "reject":
                rfc_doc = _rfc_doc(rfc, rfc_id, phase="subsystem", status="rejected")
                plan = _with_patch_state(plan, task.id, phase="subsystem", status="reject")
                _commit_state_change(
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
                }
            if result != subsystem_ref and _ref_exists(repo, result):
                _update_ref(repo, subsystem_ref, result)
            if not _ref_exists(repo, subsystem_ref):
                rfc_doc = _rfc_doc(rfc, rfc_id, phase="subsystem", status="rejected")
                plan = _with_patch_state(plan, task.id, phase="subsystem", status="reject")
                _commit_state_change(
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
            _commit_state_change(
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
            }

    subsystem_refs = [_subsystem_ref(task) for task in tasks]
    if _mainline_contains_all(repo, subsystem_refs):
        rfc_doc = _rfc_doc(rfc, rfc_id, phase="done", status="done")
        plan = _with_all_patch_state(plan, phase="mainline", status="integrated", mainline_ref=MAINLINE_REF)
        _commit_state_change(
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
        }

    result = mainline.review_and_integrate(subsystem_refs, repo)
    if result == "reject":
        rfc_doc = _rfc_doc(rfc, rfc_id, phase="mainline", status="rejected")
        _commit_state_change(
            repo,
            rfc_id,
            {"rfc.json": rfc_doc},
            {"type": "mainline", "phase": "mainline", "status": "reject"},
        )
        return {"action": "mainline", "status": "reject", "terminal": True, "rfc_id": rfc_id}
    if result != MAINLINE_REF and _ref_exists(repo, result):
        _update_ref(repo, MAINLINE_REF, result)
    if not _mainline_contains_all(repo, subsystem_refs):
        rfc_doc = _rfc_doc(rfc, rfc_id, phase="mainline", status="rejected")
        _commit_state_change(
            repo,
            rfc_id,
            {"rfc.json": rfc_doc},
            {"type": "mainline", "phase": "mainline", "status": "reject"},
        )
        return {"action": "mainline", "status": "reject", "terminal": True, "rfc_id": rfc_id}

    rfc_doc = _rfc_doc(rfc, rfc_id, phase="done", status="done")
    plan = _with_all_patch_state(plan, phase="mainline", status="integrated", mainline_ref=MAINLINE_REF)
    _commit_state_change(
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
    }


def status(repo: str | Path) -> dict[str, Any]:
    """Report RFC and patch progress from the state branch plus code branches."""
    repo = Path(repo).resolve()
    rfcs = []
    for rfc_id in _state_rfc_ids(repo):
        rfc_doc = _read_state_json(repo, rfc_id, "rfc.json") or {}
        verdict = _read_state_json(repo, rfc_id, "verdict.json") or {}
        plan = _read_state_json(repo, rfc_id, "plan.json") or {}
        patches = []
        for raw in plan.get("tasks", []):
            task = _task_from_json(raw)
            contrib_ref = _contribution_ref(task)
            subsystem_ref = _subsystem_ref(task)
            has_contrib = _ref_exists(repo, contrib_ref)
            has_subsystem = _ref_exists(repo, subsystem_ref)
            mainline_integrated = has_subsystem and _mainline_contains_all(repo, [subsystem_ref])
            patches.append(
                {
                    "id": task.id,
                    "phase": _patch_phase(
                        rfc_doc.get("status"),
                        has_contrib=has_contrib,
                        has_subsystem=has_subsystem,
                        mainline_integrated=mainline_integrated,
                    ),
                    "review_verdict": verdict.get("status"),
                    "contribution_ref": contrib_ref if has_contrib else None,
                    "contribution_status": "present" if has_contrib else "missing",
                    "subsystem_ref": subsystem_ref if has_subsystem else None,
                    "subsystem_status": "present" if has_subsystem else "missing",
                    "mainline_status": "integrated" if mainline_integrated else "pending",
                }
            )
        rfcs.append(
            {
                "rfc_id": rfc_id,
                "phase": rfc_doc.get("phase"),
                "status": rfc_doc.get("status"),
                "title": rfc_doc.get("title"),
                "review": {
                    "phase": verdict.get("phase"),
                    "status": verdict.get("status"),
                },
                "patches": patches,
                "mainline_ref": MAINLINE_REF if _ref_exists(repo, MAINLINE_REF) else None,
            }
        )
    return {"state_branch": STATE_BRANCH, "state_ref": STATE_REF, "rfcs": rfcs}


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


def _state_path(rfc_id: str, name: str) -> str:
    return f"{STATE_ROOT}/{rfc_id}/{name}"


def _contribution_ref(task: Task) -> str:
    return f"refs/heads/ai-org/contrib/{_ref_component(task.id)}"


def _subsystem_ref(task: Task) -> str:
    return f"refs/heads/ai-org/subsystem/{_ref_component(task.id)}"


def _ref_component(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip(".-/")
    return cleaned or "patch"


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
    _validate_state_doc("rfc.json", doc)
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
    _validate_state_doc("verdict.json", doc)
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
    _validate_state_doc("plan.json", doc)
    return doc


def _with_patch_state(plan: dict[str, Any], patch_id: str, *, phase: str, status: str, **refs: Any) -> dict[str, Any]:
    next_plan = copy.deepcopy(plan)
    next_plan["phase"] = phase
    next_plan["status"] = "active" if status not in {"reject", "integrated"} else status
    patch = dict(next_plan.setdefault("patches", {}).get(patch_id, {}))
    patch.update({"phase": phase, "status": status})
    patch.update(refs)
    next_plan["patches"][patch_id] = patch
    _validate_state_doc("plan.json", next_plan)
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
    _validate_state_doc("plan.json", next_plan)
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


def _read_state_json(repo: Path, rfc_id: str, name: str) -> dict[str, Any] | None:
    raw = _read_state_text(repo, _state_path(rfc_id, name), _state_head(repo))
    if raw is None:
        return None
    data = json.loads(raw)
    _validate_state_doc(name, data)
    return data


def _read_state_text(repo: Path, path: str, treeish: str | None) -> str | None:
    if not treeish:
        return None
    result = subprocess.run(
        ["git", "-C", str(repo), "show", f"{treeish}:{path}"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode != 0:
        return None
    return result.stdout


def _commit_state_change(
    repo: Path,
    rfc_id: str,
    updates: dict[str, dict[str, Any]],
    event: dict[str, Any],
) -> None:
    for name, doc in updates.items():
        _validate_state_doc(name, doc)

    event_doc = {
        "schema_version": 1,
        "rfc_id": rfc_id,
        **event,
    }
    _validate_event_doc(event_doc)

    for _attempt in range(_CAS_RETRIES):
        base = _state_head(repo)
        paths = {
            _state_path(rfc_id, name): _json_dumps(doc)
            for name, doc in updates.items()
        }
        event_path = _state_path(rfc_id, "events.ndjson")
        old_events = _read_state_text(repo, event_path, base) or ""
        paths[event_path] = old_events + json.dumps(event_doc, sort_keys=True, separators=(",", ":")) + "\n"
        commit = _build_state_commit(repo, base, paths, f"ai-org state: {rfc_id} {event_doc['type']}")
        if _update_ref_cas(repo, STATE_REF, commit, base):
            return
    raise RuntimeError(f"could not update {STATE_REF}; concurrent writers did not settle")


def _build_state_commit(repo: Path, base: str | None, paths: dict[str, str], message: str) -> str:
    temp_dir = Path(tempfile.mkdtemp(prefix="ai-org-state-index-"))
    try:
        index = temp_dir / "index"
        env = {
            **os.environ,
            "GIT_INDEX_FILE": str(index),
            "GIT_AUTHOR_NAME": os.environ.get("GIT_AUTHOR_NAME", "AI Org"),
            "GIT_AUTHOR_EMAIL": os.environ.get("GIT_AUTHOR_EMAIL", "ai-org@example.invalid"),
            "GIT_COMMITTER_NAME": os.environ.get("GIT_COMMITTER_NAME", "AI Org"),
            "GIT_COMMITTER_EMAIL": os.environ.get("GIT_COMMITTER_EMAIL", "ai-org@example.invalid"),
        }
        if base:
            _git(repo, "read-tree", base, env=env)
        for path, payload in paths.items():
            oid = _git(repo, "hash-object", "-w", "--stdin", input=payload, env=env).stdout.strip()
            _git(repo, "update-index", "--add", "--cacheinfo", "100644", oid, path, env=env)
        tree = _git(repo, "write-tree", env=env).stdout.strip()
        args = ["commit-tree", tree]
        if base:
            args.extend(["-p", base])
        args.extend(["-m", message])
        return _git(repo, *args, env=env).stdout.strip()
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _update_ref_cas(repo: Path, ref: str, new_oid: str, expected_old_oid: str | None) -> bool:
    old = expected_old_oid or _ZERO_OID
    result = subprocess.run(
        ["git", "-C", str(repo), "update-ref", ref, new_oid, old],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return result.returncode == 0


def _state_head(repo: Path) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--verify", "--quiet", STATE_REF],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _state_rfc_ids(repo: Path) -> list[str]:
    head = _state_head(repo)
    if not head:
        return []
    result = subprocess.run(
        ["git", "-C", str(repo), "ls-tree", "-r", "--name-only", head, STATE_ROOT],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode != 0:
        return []
    ids = set()
    for line in result.stdout.splitlines():
        parts = line.split("/")
        if len(parts) >= 3 and parts[0] == STATE_ROOT:
            ids.add(parts[1])
    return sorted(ids)


def _validate_state_doc(name: str, doc: dict[str, Any]) -> None:
    if not isinstance(doc, dict):
        raise ValueError(f"{name} must be a JSON object")
    for field in ("schema_version", "rfc_id", "phase", "status"):
        if field not in doc:
            raise ValueError(f"{name} missing required field {field}")
    if doc["schema_version"] != 1:
        raise ValueError(f"{name} has unsupported schema_version")
    if not isinstance(doc["rfc_id"], str) or not doc["rfc_id"]:
        raise ValueError(f"{name} rfc_id must be a non-empty string")
    if not isinstance(doc["phase"], str) or not doc["phase"]:
        raise ValueError(f"{name} phase must be a non-empty string")
    if not isinstance(doc["status"], str) or not doc["status"]:
        raise ValueError(f"{name} status must be a non-empty string")
    if name == "rfc.json":
        for field in ("title", "problem", "proposed_change", "interface_sketch", "notes"):
            if field not in doc:
                raise ValueError(f"rfc.json missing required field {field}")
    if name == "verdict.json" and not isinstance(doc.get("verdict"), dict):
        raise ValueError("verdict.json verdict must be an object")
    if name == "plan.json":
        if not isinstance(doc.get("tasks"), list):
            raise ValueError("plan.json tasks must be an array")
        if not isinstance(doc.get("patches"), dict):
            raise ValueError("plan.json patches must be an object")


def _validate_event_doc(doc: dict[str, Any]) -> None:
    for field in ("schema_version", "rfc_id", "type", "phase", "status"):
        if field not in doc:
            raise ValueError(f"event missing required field {field}")
    if not all(isinstance(doc[field], str) and doc[field] for field in ("rfc_id", "type", "phase", "status")):
        raise ValueError("event fields must be non-empty strings")


def _json_dumps(data: dict[str, Any]) -> str:
    return json.dumps(data, indent=2, sort_keys=True) + "\n"


def _update_ref(repo: Path, ref: str, target: str) -> None:
    _git(repo, "update-ref", ref, target)


def _ref_exists(repo: Path, ref: str) -> bool:
    result = subprocess.run(
        ["git", "-C", str(repo), "show-ref", "--verify", "--quiet", ref],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return result.returncode == 0


def _mainline_contains_all(repo: Path, subsystem_refs: list[str]) -> bool:
    if not subsystem_refs or not _ref_exists(repo, MAINLINE_REF):
        return False
    for ref in subsystem_refs:
        result = subprocess.run(
            ["git", "-C", str(repo), "merge-base", "--is-ancestor", ref, MAINLINE_REF],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if result.returncode != 0:
            return False
    return True


def _patch_phase(
    rfc_status: Any,
    *,
    has_contrib: bool,
    has_subsystem: bool,
    mainline_integrated: bool,
) -> str:
    if rfc_status == "rejected":
        return "rejected"
    if mainline_integrated:
        return "mainline"
    if has_subsystem:
        return "subsystem"
    if has_contrib:
        return "contribution"
    return "planned"


def _git(
    repo: Path,
    *args: str,
    input: str | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        input=input,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
