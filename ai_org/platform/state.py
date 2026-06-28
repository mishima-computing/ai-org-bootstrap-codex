"""Git-backed durable AI Org state.

All state is stored on ``ai-org-state`` as committed JSON files under
``state/<rfc-id>/``. This module deliberately stays below the domain phases:
it works with JSON documents, refs, and repository state only.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any

from . import git

STATE_BRANCH = "ai-org-state"
STATE_REF = f"refs/heads/{STATE_BRANCH}"
STATE_ROOT = "state"
MAINLINE_REF = "refs/heads/ai-org/mainline"

_CAS_RETRIES = 5


def read_json(repo: str | Path, rfc_id: str, name: str) -> dict[str, Any] | None:
    """Read and validate one state JSON document from the state branch."""
    repo_path = Path(repo)
    raw = read_text(repo_path, state_path(rfc_id, name), state_head(repo_path))
    if raw is None:
        return None
    data = json.loads(raw)
    validate_state_doc(name, data)
    return data


def commit_change(
    repo: str | Path,
    rfc_id: str,
    updates: dict[str, dict[str, Any]],
    event: dict[str, Any],
) -> dict[str, Any]:
    """Commit state document updates and one append-only event with CAS retry."""
    repo_path = Path(repo)
    for name, doc in updates.items():
        validate_state_doc(name, doc)

    event_doc = {
        "schema_version": 1,
        "rfc_id": rfc_id,
        **event,
    }
    validate_event_doc(event_doc)

    for _attempt in range(_CAS_RETRIES):
        base = state_head(repo_path)
        paths = {
            state_path(rfc_id, name): json_dumps(doc)
            for name, doc in updates.items()
        }
        event_path = state_path(rfc_id, "events.ndjson")
        old_events = read_text(repo_path, event_path, base) or ""
        paths[event_path] = old_events + json.dumps(event_doc, sort_keys=True, separators=(",", ":")) + "\n"
        commit = build_state_commit(repo_path, base, paths, f"ai-org state: {rfc_id} {event_doc['type']}")
        if update_ref_cas(repo_path, STATE_REF, commit, base):
            return git.push_ref(repo_path, STATE_REF)
    raise RuntimeError(f"could not update {STATE_REF}; concurrent writers did not settle")


def status(repo: str | Path) -> dict[str, Any]:
    """Report RFC and patch progress from state JSON plus visible code refs."""
    repo_path = Path(repo).resolve()
    rfcs = []
    for rfc_id in rfc_ids(repo_path):
        rfc_doc = read_json(repo_path, rfc_id, "rfc.json") or {}
        verdict = read_json(repo_path, rfc_id, "verdict.json") or {}
        plan = read_json(repo_path, rfc_id, "plan.json") or {}
        patches = []
        for raw in plan.get("tasks", []):
            patch_id = str(raw.get("id", ""))
            contrib_ref = contribution_ref(patch_id)
            subsystem_ref = subsystem_ref_for_patch(patch_id)
            has_contrib = git.ref_exists(repo_path, contrib_ref)
            has_subsystem = git.ref_exists(repo_path, subsystem_ref)
            mainline_integrated = has_subsystem and git.mainline_contains_all(repo_path, [subsystem_ref], MAINLINE_REF)
            patches.append(
                {
                    "id": patch_id,
                    "phase": patch_phase(
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
                "mainline_ref": MAINLINE_REF if git.ref_exists(repo_path, MAINLINE_REF) else None,
            }
        )
    return {"state_branch": STATE_BRANCH, "state_ref": STATE_REF, "rfcs": rfcs}


def state_path(rfc_id: str, name: str) -> str:
    return f"{STATE_ROOT}/{rfc_id}/{name}"


def contribution_ref(patch_id: str) -> str:
    return f"refs/heads/ai-org/contrib/{git.ref_component(patch_id)}"


def subsystem_ref_for_patch(patch_id: str) -> str:
    return f"refs/heads/ai-org/subsystem/{git.ref_component(patch_id)}"


def read_text(repo: Path, path: str, treeish: str | None) -> str | None:
    if not treeish:
        return None
    result = git.run(
        repo,
        "show",
        f"{treeish}:{path}",
        check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout


def build_state_commit(repo: Path, base: str | None, paths: dict[str, str], message: str) -> str:
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
            git.run(repo, "read-tree", base, env=env)
        for path, payload in paths.items():
            oid = git.run(repo, "hash-object", "-w", "--stdin", input=payload, env=env).stdout.strip()
            git.run(repo, "update-index", "--add", "--cacheinfo", "100644", oid, path, env=env)
        tree = git.run(repo, "write-tree", env=env).stdout.strip()
        args = ["commit-tree", tree]
        if base:
            args.extend(["-p", base])
        args.extend(["-m", message])
        return git.run(repo, *args, env=env).stdout.strip()
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def update_ref_cas(repo: Path, ref: str, new_oid: str, expected_old_oid: str | None) -> bool:
    return git.update_ref_cas(repo, ref, new_oid, expected_old_oid)


def state_head(repo: Path) -> str | None:
    result = git.run(repo, "rev-parse", "--verify", "--quiet", STATE_REF, check=False)
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def rfc_ids(repo: Path) -> list[str]:
    head = state_head(repo)
    if not head:
        return []
    result = git.run(repo, "ls-tree", "-r", "--name-only", head, STATE_ROOT, check=False)
    if result.returncode != 0:
        return []
    ids = set()
    for line in result.stdout.splitlines():
        parts = line.split("/")
        if len(parts) >= 3 and parts[0] == STATE_ROOT:
            ids.add(parts[1])
    return sorted(ids)


def validate_state_doc(name: str, doc: dict[str, Any]) -> None:
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


def validate_event_doc(doc: dict[str, Any]) -> None:
    for field in ("schema_version", "rfc_id", "type", "phase", "status"):
        if field not in doc:
            raise ValueError(f"event missing required field {field}")
    if not all(isinstance(doc[field], str) and doc[field] for field in ("rfc_id", "type", "phase", "status")):
        raise ValueError("event fields must be non-empty strings")


def json_dumps(data: dict[str, Any]) -> str:
    return json.dumps(data, indent=2, sort_keys=True) + "\n"


def patch_phase(
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

