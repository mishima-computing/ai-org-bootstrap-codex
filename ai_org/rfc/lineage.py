"""RFC LINEAGE: Reference-informed canonical lineage node model.

Memento: A/B experiment outcome.
- lineage_b is promoted to the canonical implementation and lineage_a is kept
  temporarily as ai_org.rfc.lineage_a_deprecated.
- Implementations consult the org Reference before behavior changes.
- Influencing facets:
  required all props payload artifact tolerance
  (ai-org-bootstrap-codex@871c99a; ai-org-bootstrap-codex@2f5f13b);
  schema field mode naming discipline
  (ai-org-bootstrap-codex@2f5f13b; ai-org-bootstrap-codex@67563c5);
  child branch metadata inheritance hazard
  (ai-org-bootstrap-codex@67563c5; Gerrit NoteDb-style separation);
  codex output schema safe subset
  (ai-org-bootstrap-codex@345bc17; tests/test_codex_output_schema_guard.py).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from ai_org import git_wrapper
import ai_org.rfc.codex_exec as codex_exec
from ai_org.rfc.field_registry import empty_user_experience_requirements


RFC_PREFIX = "ai-org/rfc/"
LEDGER_PATH = "lineage-ledger.json"
METADATA_PATH = "rfc-metadata.json"
VALIDATION_ATTEMPTS = 2
MAX_RESOLUTION_DEPTH = 64

LINEAGE_SPLIT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["split_mode", "rationale", "children", "parent_retained_scope_ids"],
    "properties": {
        "split_mode": {
            "enum": ["right_sized", "split_into_children"],
            "description": "Whether the approved technical approach is already one bounded leaf or must split.",
        },
        "rationale": {"type": "string", "description": "Short sizing or split rationale."},
        "children": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "child_key",
                    "title",
                    "stage_name",
                    "node_kind",
                    "branching_mode",
                    "serial_after_child_key",
                    "depends_on_child_keys",
                    "summary",
                    "acceptance_criteria",
                    "functional_check",
                    "scope_item_ids",
                    "systems",
                    "ux_acceptance_tests",
                    "risks",
                ],
                "properties": {
                    "child_key": {"type": "string", "description": "Stable local key unique within this split."},
                    "title": {"type": "string", "description": "Human-readable child RFC title."},
                    "stage_name": {"type": "string", "description": "Rolling-wave stage this child belongs to."},
                    "node_kind": {
                        "enum": ["leaf", "coarse"],
                        "description": "Leaf is near-horizon executable work; coarse is a later-stage placeholder.",
                    },
                    "branching_mode": {
                        "enum": ["parallel_from_parent", "serial_after_child"],
                        "description": "Whether git ancestry starts from the parent branch or a predecessor child.",
                    },
                    "serial_after_child_key": {
                        "type": "string",
                        "description": "Predecessor child key when branching_mode is serial_after_child, otherwise empty.",
                    },
                    "depends_on_child_keys": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "DAG dependency child keys beyond the hierarchy relation.",
                    },
                    "summary": {"type": "string", "description": "Single-concern scope summary."},
                    "acceptance_criteria": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Child acceptance criteria copied from the deliberated approach.",
                    },
                    "functional_check": {"type": "string", "description": "How functional_check verifies this child."},
                    "scope_item_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Parent scope item ids covered exactly by this child.",
                    },
                    "systems": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Implementation systems touched by this child.",
                    },
                    "ux_acceptance_tests": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "UX acceptance tests covered by this child.",
                    },
                    "risks": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Must-address risks handled by this child.",
                    },
                },
            },
        },
        "parent_retained_scope_ids": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Scope item ids retained by the parent integration gate.",
        },
    },
}


def refine(repo: str | Path, rfc_id_or_branch: str, *, horizon: int = 1) -> dict[str, Any]:
    """Split an approved technical approach into lineage child RFC branches."""
    repo_path = Path(repo).resolve()
    branch = _rfc_branch(rfc_id_or_branch)
    if git_wrapper.file_exists(repo_path, branch, LEDGER_PATH):
        ledger = _read_json(repo_path, branch, LEDGER_PATH)
        return {"ok": True, "status": "already-refined", "branch": branch, "ledger": ledger}

    rfc = _read_json(repo_path, branch, "rfc.json")
    approach = _read_json(repo_path, branch, "technical-approach.json")
    if not isinstance(rfc, Mapping) or not isinstance(approach, Mapping):
        return {"ok": False, "status": "missing-input", "branch": branch, "error": "rfc.json and technical-approach.json are required"}

    deterministic_right_sized = right_sized({"rfc": rfc, "technical_approach": approach})
    if deterministic_right_sized:
        git_wrapper.commit_empty(repo_path, branch, "lineage: right-sized", body="deterministic right-sized leaf")
        return {"ok": True, "status": "right-sized", "branch": branch, "rationale": "deterministic right-sized leaf", "surplus_children_ignored": 0}

    scope_items = _scope_items(rfc, approach)
    feedback = ""
    last_error: dict[str, Any] | None = None
    contradiction_retried = False
    for attempt in range(1, VALIDATION_ATTEMPTS + 1):
        split_result = _split_with_codex(repo_path, branch, rfc, approach, scope_items, horizon, feedback)
        if not split_result["ok"]:
            return split_result
        split = split_result["split"]
        if split["split_mode"] == "right_sized":
            surplus_children_ignored = int(split.get("surplus_children_ignored", 0))
            # Reference facet: required all props payload artifact tolerance.
            # Safe-subset schemas require children even when split_mode says no
            # children, so surplus child payload is ignored as an artifact. If
            # the deterministic pre-check disagreed and surplus exists, ask once
            # for clarification before honoring the model verdict.
            if surplus_children_ignored and not deterministic_right_sized and not contradiction_retried:
                contradiction_retried = True
                feedback = (
                    "Clarification: deterministic pre-check says this parent is not already one "
                    "right-sized leaf, but split_mode was right_sized while children was populated. "
                    "If children contains a real decomposition, use split_mode=split_into_children. "
                    "Use split_mode=right_sized only when no child RFCs are needed."
                )
                continue
            git_wrapper.commit_empty(repo_path, branch, "lineage: right-sized", body=split["rationale"])
            return {
                "ok": True,
                "status": "right-sized",
                "branch": branch,
                "rationale": split["rationale"],
                "surplus_children_ignored": surplus_children_ignored,
            }

        normalized = _normalize_split(repo_path, branch, split, scope_items, horizon)
        validation = validate_ledger_contract(normalized, scope_items)
        if validation["ok"]:
            ledger = _ledger(branch, scope_items, normalized, validation)
            # Gerrit NoteDb's append-only metadata branches influenced this committed parent ledger:
            # https://gerrit-review.googlesource.com/Documentation/note-db.html
            ledger_commit = git_wrapper.commit_files(
                repo_path,
                branch,
                {LEDGER_PATH: ledger},
                subject="lineage: ledger",
            )
            children = _create_child_branches(repo_path, branch, normalized, ledger_commit["commit"])
            return {
                "ok": True,
                "status": "refined",
                "branch": branch,
                "ledger_commit": ledger_commit["commit"],
                "children": children,
                "dependency_graph": ledger["dependency_graph"],
            }
        last_error = validation
        feedback = _validation_feedback(validation)

    return {
        "ok": False,
        "status": "feedback-retry",
        "branch": branch,
        "error": "lineage split failed deterministic validation",
        "validation": last_error or {},
    }


def right_sized(view: Mapping[str, Any]) -> bool:
    """Return whether a lineage view is one bounded executable leaf."""
    approach = _unwrap_approach(view)
    if approach.get("split_mode") == "right_sized":
        return True
    child = approach.get("lineage_child")
    if isinstance(child, Mapping):
        criteria = child.get("acceptance_criteria")
        return child.get("node_kind") == "leaf" and isinstance(criteria, list) and bool(criteria) and isinstance(child.get("functional_check"), str)
    if isinstance(approach.get("lineage_parent"), Mapping) and isinstance(approach.get("approach_slice"), Mapping):
        return True
    if approach.get("node_kind") == "leaf":
        criteria = approach.get("acceptance_criteria")
        return isinstance(criteria, list) and len(criteria) > 0 and isinstance(approach.get("functional_check"), str)

    patch_plan = _find_first_mapping(approach, "patch_plan")
    if not patch_plan:
        return False
    if _list_of_mappings(patch_plan.get("follow_ups")) or _list_of_mappings(patch_plan.get("deferred")):
        return False
    systems = _find_named_strings(approach, {"systems", "subsystem", "key_modules", "implementation"})
    if len(systems) > 1:
        return False
    first_playable = patch_plan.get("first_playable")
    if isinstance(first_playable, Mapping):
        return bool(first_playable.get("how_verified"))
    return True


def resolved(repo: str | Path, branch: str) -> bool:
    """Return whether a leaf or parent lineage node is resolved."""
    repo_path = Path(repo).resolve()
    normalized = _rfc_branch(branch)
    return _resolved(repo_path, normalized, 0)


def _resolved(repo_path: Path, normalized: str, depth: int) -> bool:
    if depth >= MAX_RESOLUTION_DEPTH:
        raise RuntimeError(f"lineage resolution exceeded maximum depth {MAX_RESOLUTION_DEPTH} at {normalized}")
    metadata = _read_metadata(repo_path, normalized)
    parent_branch = str(metadata.get("parent_branch", "")) if metadata else ""
    if parent_branch and parent_branch != normalized:
        return _leaf_resolved(repo_path, normalized, parent_branch)

    ledger = _read_ledger_from_first_parent_history(repo_path, normalized)
    # Reference facet: child branch metadata inheritance hazard. Parent-scope
    # ledgers can be inherited through branch history, so a ledger only belongs
    # to this node when its parent_branch matches the queried branch.
    if (
        isinstance(ledger, Mapping)
        and ledger.get("parent_branch") == normalized
        and isinstance(ledger.get("children"), list)
    ):
        child_branches = [
            str(child.get("branch"))
            for child in ledger["children"]
            if isinstance(child, Mapping) and isinstance(child.get("branch"), str)
        ]
        return all(_resolved(repo_path, child, depth + 1) for child in child_branches) and _has_integration_gate(repo_path, normalized)

    return _leaf_resolved(repo_path, normalized, parent_branch)


def _leaf_resolved(repo: Path, branch: str, parent_branch: str = "") -> bool:
    accepted = git_wrapper.has_subject(repo, branch, "acceptance: passed") or git_wrapper.has_subject(
        repo, branch, "acceptance: reachable"
    )
    if not accepted:
        return False
    if parent_branch and git_wrapper.is_ancestor(repo, branch, parent_branch):
        return True
    default = git_wrapper.default_branch(repo)
    return git_wrapper.is_ancestor(repo, branch, default)


def escalate(repo: str | Path, child_branch: str, evidence: Mapping[str, Any] | str) -> dict[str, Any]:
    """Record that a child is blocked because the parent scope was invalidated."""
    repo_path = Path(repo).resolve()
    branch = _rfc_branch(child_branch)
    metadata = _read_metadata(repo_path, branch)
    parent = str(metadata.get("parent_branch", ""))
    record = dict(metadata)
    record["lifecycle_status"] = "blocked:parent-invalidated"
    record["escalation_evidence"] = evidence if isinstance(evidence, Mapping) else {"summary": str(evidence)}
    child_commit = git_wrapper.commit_files(
        repo_path,
        branch,
        {METADATA_PATH: record},
        subject="lineage: blocked parent-invalidated",
    )
    parent_commit = None
    stale: list[dict[str, Any]] = []
    if parent and git_wrapper.branch_exists(repo_path, parent):
        parent_commit = git_wrapper.commit_empty(
            repo_path,
            parent,
            "rfc: needs-revision lineage parent invalidated",
            body=json.dumps(record["escalation_evidence"], sort_keys=True),
        )
        stale = mark_stale(repo_path, _dependent_branches(repo_path, parent, branch), "upstream child invalidated parent scope")[
            "branches"
        ]
    return {"ok": True, "branch": branch, "parent_branch": parent, "child_commit": child_commit["commit"], "parent_commit": parent_commit, "stale": stale}


def mark_stale(repo: str | Path, branches: list[str], reason: str) -> dict[str, Any]:
    """Mark dependent lineage branches stale after a parent re-baseline."""
    repo_path = Path(repo).resolve()
    updated: list[dict[str, Any]] = []
    for item in branches:
        branch = _rfc_branch(item)
        if not git_wrapper.branch_exists(repo_path, branch):
            continue
        metadata = _read_metadata(repo_path, branch)
        metadata["lifecycle_status"] = "stale"
        metadata["stale_reason"] = reason
        written = git_wrapper.commit_files(
            repo_path,
            branch,
            {METADATA_PATH: metadata},
            subject="lineage: stale",
        )
        updated.append({"branch": branch, "commit": written["commit"]})
    return {"ok": True, "branches": updated}


def elaborate(repo: str | Path, coarse_child_branch: str, *, horizon: int = 1) -> dict[str, Any]:
    """Refine a coarse child once its declared dependencies are resolved."""
    repo_path = Path(repo).resolve()
    branch = _rfc_branch(coarse_child_branch)
    if not coarse_ready(repo_path, branch):
        return {"ok": False, "status": "blocked-by-dependencies", "branch": branch}
    return refine(repo_path, branch, horizon=horizon)


def split_pending(repo: str | Path, branch: str) -> bool:
    """Return whether a branch has an approved approach but no lineage decision."""
    repo_path = Path(repo).resolve()
    normalized = _rfc_branch(branch)
    if not git_wrapper.branch_exists(repo_path, normalized):
        return False
    if not git_wrapper.has_subject(repo_path, normalized, "rfc: direction-ok"):
        return False
    if git_wrapper.file_exists(repo_path, normalized, LEDGER_PATH):
        return False
    if git_wrapper.has_subject(repo_path, normalized, "lineage: right-sized"):
        return False
    metadata = _read_metadata(repo_path, normalized)
    # Reference facet: child branch metadata inheritance hazard. Child metadata
    # identifies leaf/coarse lineage nodes; leaf nodes go to patch, and coarse
    # nodes wait for dependency readiness, so neither re-enters split_pending.
    if metadata.get("node_kind") in {"leaf", "leafed", "coarse"}:
        return False
    lineage = metadata.get("lineage")
    if isinstance(lineage, Mapping) and lineage.get("horizon_status") in {"leafed", "coarse"}:
        return False
    approach = _read_json(repo_path, normalized, "technical-approach.json")
    if not isinstance(approach, Mapping):
        return False
    if right_sized(approach):
        return False
    return (
        git_wrapper.file_exists(repo_path, normalized, "technical-approach.json")
    )


def coarse_ready(repo: str | Path, branch: str) -> bool:
    """Return whether a coarse child may be elaborated."""
    repo_path = Path(repo).resolve()
    normalized = _rfc_branch(branch)
    metadata = _read_metadata(repo_path, normalized)
    if metadata.get("node_kind") != "coarse" or git_wrapper.file_exists(repo_path, normalized, LEDGER_PATH):
        return False
    deps = metadata.get("depends_on_branches")
    if not isinstance(deps, list):
        return False
    return all(isinstance(dep, str) and resolved(repo_path, dep) for dep in deps)


def validate_ledger_contract(split: Mapping[str, Any], scope_items: list[Mapping[str, str]]) -> dict[str, Any]:
    """Validate exact parent-scope coverage and child dependency topology."""
    children = split.get("children")
    retained = split.get("parent_retained_scope_ids")
    if not isinstance(children, list) or not isinstance(retained, list):
        return _invalid("split must contain children and parent_retained_scope_ids arrays")
    scope_ids = {item["id"] for item in scope_items}
    child_keys = [child.get("child_key") for child in children if isinstance(child, Mapping)]
    if len(child_keys) != len(set(child_keys)) or not all(isinstance(key, str) and key for key in child_keys):
        return _invalid("child keys must be unique non-empty strings")
    child_key_set = set(child_keys)

    coverage: dict[str, list[str]] = {scope_id: [] for scope_id in scope_ids}
    unknown: list[str] = []
    for child in children:
        if not isinstance(child, Mapping):
            return _invalid("child entries must be objects")
        child_key = str(child["child_key"])
        item_ids = child.get("scope_item_ids")
        if not isinstance(item_ids, list) or not all(isinstance(item, str) for item in item_ids):
            return _invalid(f"{child_key} scope_item_ids must be strings")
        for scope_id in item_ids:
            if scope_id not in scope_ids:
                unknown.append(scope_id)
            else:
                coverage[scope_id].append(child_key)

    retained_ids: list[str] = []
    for scope_id in retained:
        if not isinstance(scope_id, str):
            return _invalid("parent_retained_scope_ids must be strings")
        if scope_id not in scope_ids:
            unknown.append(scope_id)
        else:
            coverage[scope_id].append("parent:integration-gate")
            retained_ids.append(scope_id)

    unmapped = sorted(scope_id for scope_id, owners in coverage.items() if not owners)
    double_mapped = sorted(scope_id for scope_id, owners in coverage.items() if len(owners) > 1)
    dependency_errors = _dependency_errors(children, child_key_set)
    cycle = _cycle(_dependency_edges(children))
    first_playable_errors = _first_playable_dependency_errors(children)
    if unknown or unmapped or double_mapped or dependency_errors or cycle:
        return {
            "ok": False,
            "status": "feedback-retry",
            "unknown_scope_ids": sorted(set(unknown)),
            "unmapped_scope_ids": unmapped,
            "double_mapped_scope_ids": double_mapped,
            "dependency_errors": dependency_errors,
            "cycle": cycle,
        }
    if first_playable_errors:
        return {
            "ok": False,
            "status": "feedback-retry",
            "unknown_scope_ids": [],
            "unmapped_scope_ids": [],
            "double_mapped_scope_ids": [],
            "dependency_errors": first_playable_errors,
            "cycle": [],
        }
    return {
        "ok": True,
        "coverage": [{"scope_item_id": scope_id, "owner": coverage[scope_id][0]} for scope_id in sorted(scope_ids)],
        "retained_scope_ids": sorted(retained_ids),
    }


def _split_with_codex(
    repo: Path,
    branch: str,
    rfc: Mapping[str, Any],
    approach: Mapping[str, Any],
    scope_items: list[Mapping[str, str]],
    horizon: int,
    feedback: str,
) -> dict[str, Any]:
    run = codex_exec.run_json(
        repo,
        schema=LINEAGE_SPLIT_SCHEMA,
        prompt=_split_prompt(branch, rfc, approach, scope_items, horizon, feedback),
        schema_filename="rfc-lineage-b.schema.json",
        output_filename="rfc-lineage-b.json",
        failure_label="Codex lineage split",
    )
    if not run["ok"]:
        return {"ok": False, "status": "codex-failed", "branch": branch, "error": run["error"]}
    return _parse_split(run["raw"], branch)


def _split_prompt(
    branch: str,
    rfc: Mapping[str, Any],
    approach: Mapping[str, Any],
    scope_items: list[Mapping[str, str]],
    horizon: int,
    feedback: str,
) -> str:
    scope_text = json.dumps(scope_items, indent=2, sort_keys=True, ensure_ascii=True)
    approach_text = json.dumps(_approach_split_view(approach), indent=2, sort_keys=True, ensure_ascii=True)
    rfc_text = json.dumps(rfc, indent=2, sort_keys=True, ensure_ascii=True)
    feedback_text = f"\nPrevious deterministic validation failed. Fix only these issues:\n{feedback}\n" if feedback else ""
    return (
        "You are the clean-room RFC lineage split step. Split only the approved technical-approach: "
        "patch_plan, implementation systems, UX acceptance, and must-address risks. Do not derive new "
        "scope from prose.\n\n"
        "Typed relations: version-of stays on the same branch and is not part of this output; "
        "split-into creates child RFC branches; depends-on is a separate DAG; supersedes is rare and "
        "not emitted unless already present in input.\n"
        "Rolling wave: define every stage now. Children within the near horizon must be leaf nodes. "
        "Later-stage children must be coarse nodes with acceptance criteria.\n"
        "Every scope item id must be mapped exactly once to either one child scope_item_ids array or "
        "parent_retained_scope_ids for an integration gate. Use serial_after_child_key for serial chains; "
        "parallel children branch from the parent. Do not use order numbers.\n"
        # Airflow DAG scheduling shaped this separation of topology from execution:
        # https://airflow.apache.org/docs/apache-airflow/stable/core-concepts/dags.html
        "Return only topology and scope mapping. Do not schedule execution or write patches.\n"
        f"{feedback_text}\nRFC branch: {branch}\nHorizon: {max(1, horizon)}\n"
        f"Scope items:\n{scope_text}\n\nTechnical approach split input:\n{approach_text}\n\nRFC context:\n{rfc_text}\n"
        "Return only JSON matching the provided schema."
    )


def _parse_split(raw: str, branch: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        return {"ok": False, "status": "invalid-codex-output", "branch": branch, "error": f"invalid JSON: {exc}"}
    if not isinstance(parsed, dict):
        return {"ok": False, "status": "invalid-codex-output", "branch": branch, "error": "output must be an object"}
    if set(parsed) != set(LINEAGE_SPLIT_SCHEMA["properties"]):
        return {"ok": False, "status": "invalid-codex-output", "branch": branch, "error": "output fields do not match schema"}
    if parsed["split_mode"] not in {"right_sized", "split_into_children"}:
        return {"ok": False, "status": "invalid-codex-output", "branch": branch, "error": "invalid split_mode"}
    if not isinstance(parsed["rationale"], str):
        return {"ok": False, "status": "invalid-codex-output", "branch": branch, "error": "invalid rationale"}
    if not isinstance(parsed["parent_retained_scope_ids"], list) or not all(
        isinstance(item, str) for item in parsed["parent_retained_scope_ids"]
    ):
        return {"ok": False, "status": "invalid-codex-output", "branch": branch, "error": "invalid parent_retained_scope_ids"}
    if not isinstance(parsed["children"], list):
        return {"ok": False, "status": "invalid-codex-output", "branch": branch, "error": "invalid children"}
    if parsed["split_mode"] == "right_sized":
        # Reference facet: required all props payload artifact tolerance.
        # Mode-irrelevant children are counted and ignored, not interpreted as
        # semantic child commitments.
        surplus_children_ignored = len(parsed["children"])
        return {
            "ok": True,
            "split": {
                **parsed,
                "children": [],
                "parent_retained_scope_ids": [],
                "surplus_children_ignored": surplus_children_ignored,
            },
        }
    for child in parsed["children"]:
        error = _child_error(child)
        if error:
            return {"ok": False, "status": "invalid-codex-output", "branch": branch, "error": error}
    return {"ok": True, "split": parsed}


def _child_error(child: object) -> str:
    child_fields = set(LINEAGE_SPLIT_SCHEMA["properties"]["children"]["items"]["properties"])
    if not isinstance(child, dict) or set(child) != child_fields:
        return "child fields do not match schema"
    string_fields = (
        "child_key",
        "title",
        "stage_name",
        "serial_after_child_key",
        "summary",
        "functional_check",
    )
    if not all(isinstance(child[field], str) for field in string_fields):
        return "child string fields are invalid"
    if child["node_kind"] not in {"leaf", "coarse"}:
        return "child node_kind is invalid"
    if child["branching_mode"] not in {"parallel_from_parent", "serial_after_child"}:
        return "child branching_mode is invalid"
    for field in ("depends_on_child_keys", "acceptance_criteria", "scope_item_ids", "systems", "ux_acceptance_tests", "risks"):
        if not isinstance(child[field], list) or not all(isinstance(item, str) for item in child[field]):
            return f"child {field} is invalid"
    if child["branching_mode"] == "serial_after_child" and not child["serial_after_child_key"].strip():
        return "serial child must name serial_after_child_key"
    if child["branching_mode"] == "parallel_from_parent" and child["serial_after_child_key"].strip():
        return "parallel child must not name serial_after_child_key"
    return ""


def _normalize_split(
    repo: Path,
    parent_branch: str,
    split: Mapping[str, Any],
    scope_items: list[Mapping[str, str]],
    horizon: int,
) -> dict[str, Any]:
    root_id = _root_serial_id(repo, parent_branch)
    depths = _child_depths(split["children"])
    normalized_children: list[dict[str, Any]] = []
    for index, child in enumerate(split["children"], start=1):
        copy = dict(child)
        copy["declared_node_kind"] = child.get("node_kind")
        child_id = f"{root_id}-{index}"
        copy["id"] = child_id
        copy["branch"] = f"{RFC_PREFIX}{child_id}"
        depth = depths.get(copy["child_key"], 0)
        copy["node_kind"] = "leaf" if depth < max(1, horizon) else "coarse"
        copy["acceptance_criteria"] = list(copy["acceptance_criteria"]) or _criteria_for_scope(scope_items, copy["scope_item_ids"])
        normalized_children.append(copy)
    return {
        "split_mode": split["split_mode"],
        "rationale": split["rationale"],
        "parent_retained_scope_ids": list(split["parent_retained_scope_ids"]),
        "children": normalized_children,
    }


def _create_child_branches(repo: Path, parent_branch: str, split: Mapping[str, Any], ledger_commit: str) -> list[dict[str, Any]]:
    by_key = {child["child_key"]: child for child in split["children"]}
    created: list[dict[str, Any]] = []
    created_keys: set[str] = set()
    for child in _topological_children(split["children"]):
        base = parent_branch
        if child["branching_mode"] == "serial_after_child":
            base = by_key[child["serial_after_child_key"]]["branch"]
        metadata = _child_metadata(parent_branch, child, by_key, ledger_commit)
        files = {
            "rfc.json": _child_rfc(child),
            "technical-approach.json": _child_approach(child),
            METADATA_PATH: metadata,
        }
        written = git_wrapper.create_branch_with_files(
            repo,
            child["branch"],
            base,
            files,
            commit_message=f"lineage: child {child['id']}",
            deletions=[LEDGER_PATH],
        )
        # Graphite/git-branchless restack concepts shaped serial ancestry invariants:
        # https://graphite.com/docs/command-reference
        created.append(
            {
                "id": child["id"],
                "child_key": child["child_key"],
                "branch": child["branch"],
                "commit": written["commit"],
                "base": base,
                "node_kind": child["node_kind"],
            }
        )
        created_keys.add(child["child_key"])
    if len(created_keys) != len(split["children"]):
        raise RuntimeError("lineage child creation did not cover every child")
    return created


def _ledger(
    parent_branch: str,
    scope_items: list[Mapping[str, str]],
    split: Mapping[str, Any],
    validation: Mapping[str, Any],
) -> dict[str, Any]:
    edges = _dependency_edges(split["children"])
    by_key = {child["child_key"]: child for child in split["children"]}
    # Airflow's scheduler model influenced storing topology separately from execution state:
    # https://airflow.apache.org/docs/apache-airflow/stable/administration-and-deployment/scheduler.html
    return {
        "schema": "lineage-ledger-v1",
        "parent_branch": parent_branch,
        "relation": "split-into",
        "split_operator": "AND",
        "scope_items": list(scope_items),
        "coverage": validation["coverage"],
        "parent_retained_scope_ids": validation["retained_scope_ids"],
        # Reference facet: schema field mode naming discipline. Persist
        # dependency endpoints with prerequisite/dependent names, never from/to.
        "dependency_graph": [
            {
                "prerequisite_branch": by_key[edge["prerequisite_child_key"]]["branch"],
                "dependent_branch": by_key[edge["dependent_child_key"]]["branch"],
            }
            for edge in edges
        ],
        "children": [
            {
                "id": child["id"],
                "child_key": child["child_key"],
                "branch": child["branch"],
                "node_kind": child["node_kind"],
                "branching_mode": child["branching_mode"],
                "depends_on_child_keys": child["depends_on_child_keys"],
                "serial_after_child_key": child["serial_after_child_key"],
                "scope_item_ids": child["scope_item_ids"],
            }
            for child in split["children"]
        ],
    }


def _child_metadata(parent_branch: str, child: Mapping[str, Any], by_key: Mapping[str, Mapping[str, Any]], ledger_commit: str) -> dict[str, Any]:
    depends_on_keys = _edge_dependencies(child)
    depends_on_branches = [by_key[key]["branch"] for key in depends_on_keys if key in by_key]
    return {
        "schema": "rfc-lineage-node-v1",
        "id": child["id"],
        "child_key": child["child_key"],
        "parent_branch": parent_branch,
        "ledger_commit": ledger_commit,
        "relation_from_parent": "split-into",
        "split_operator": "AND",
        "node_kind": child["node_kind"],
        "branching_mode": child["branching_mode"],
        "serial_after_child_key": child["serial_after_child_key"],
        "depends_on_child_keys": child["depends_on_child_keys"],
        "depends_on_branches": depends_on_branches,
        "scope_item_ids": child["scope_item_ids"],
        "lifecycle_status": "active",
    }


def _child_rfc(child: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "raw_request": child["summary"],
        "working_title": child["title"],
        "request_type": "feature",
        "problem_or_motivation": child["summary"],
        "intended_users_or_jobs": "Implement the approved RFC technical approach as a bounded lineage child.",
        "desired_outcomes_success": "; ".join(child["acceptance_criteria"]),
        "affected_area_platform": ", ".join(child["systems"]),
        "tech_stack": {
            "build_strategy": "",
            "engine": "",
            "framework": "",
            "language": "",
            "platform": "",
            "rationale": "",
            "provenance": "unspecified",
        },
        "user_experience_requirements": empty_user_experience_requirements(),
        "background_facts": "Generated from the parent lineage ledger.",
        "constraints_assumptions": [],
        "references": [],
        "grounding_provenance": "lineage_b clean-room split",
        "open_questions": [],
        "non_goals_out_of_scope": [],
        "proposal_hint": child["functional_check"],
        "alternatives_considered": [],
    }


def _child_approach(child: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "lineage_child": {
            "id": child["id"],
            "node_kind": child["node_kind"],
            "summary": child["summary"],
            "systems": child["systems"],
            "acceptance_criteria": child["acceptance_criteria"],
            "functional_check": child["functional_check"],
            "ux_acceptance_tests": child["ux_acceptance_tests"],
            "risks": child["risks"],
        }
    }


def _unwrap_approach(view: Mapping[str, Any]) -> Mapping[str, Any]:
    if isinstance(view.get("technical_approach"), Mapping):
        return view["technical_approach"]
    return view


def _scope_items(rfc: Mapping[str, Any], approach: Mapping[str, Any]) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    _append_text_item(items, "goal:rfc.desired_outcomes_success", "goal", rfc.get("desired_outcomes_success"))
    for index, text in enumerate(_find_named_strings(approach, {"goal", "goals", "success_criteria", "success_criterion"}), start=1):
        _append_text_item(items, f"goal:technical_approach:{index}", "goal", text)
    for path, text in _acceptance_tests(rfc, approach):
        _append_text_item(items, f"ux:{path}", "ux_acceptance_test", text)
    patch_plan = _find_first_mapping(approach, "patch_plan")
    if patch_plan:
        _append_text_item(items, "patch_plan:first_playable", "patch_plan", patch_plan.get("first_playable"))
        for index, item in enumerate(_list_of_mappings(patch_plan.get("follow_ups")), start=1):
            _append_text_item(items, f"patch_plan:follow_up:{index}", "patch_plan", item)
        for index, item in enumerate(_list_of_mappings(patch_plan.get("deferred")), start=1):
            _append_text_item(items, f"patch_plan:deferred:{index}", "patch_plan", item)
    for index, risk in enumerate(_risk_items(approach), start=1):
        risk_id = risk.get("id") if isinstance(risk.get("id"), str) and risk["id"] else str(index)
        _append_text_item(items, f"risk:{risk_id}", "must_address_risk", risk)
    deduped: dict[str, dict[str, str]] = {}
    for item in items:
        deduped.setdefault(item["id"], item)
    return list(deduped.values())


def _append_text_item(items: list[dict[str, str]], item_id: str, kind: str, value: Any) -> None:
    text = _compact_text(value)
    if text:
        items.append({"id": item_id, "kind": kind, "text": text})


def _acceptance_tests(rfc: Mapping[str, Any], approach: Mapping[str, Any]) -> list[tuple[str, str]]:
    values: list[tuple[str, str]] = []
    for source_name, source in (("rfc", rfc), ("technical_approach", approach)):
        for ux_index, ux in enumerate(_find_mappings_named(source, "user_experience_requirements"), start=1):
            acceptance = ux.get("acceptance_tests")
            if not isinstance(acceptance, Mapping):
                continue
            for field in sorted(acceptance):
                tests = acceptance[field]
                if isinstance(tests, list):
                    for index, text in enumerate(tests, start=1):
                        if isinstance(text, str) and text.strip():
                            values.append((f"{source_name}:{ux_index}:{field}:{index}", text))
    return values


def _risk_items(value: Any) -> list[Mapping[str, Any]]:
    risks: list[Mapping[str, Any]] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            if key == "risks" and isinstance(child, list):
                risks.extend(item for item in child if isinstance(item, Mapping))
            else:
                risks.extend(_risk_items(child))
    elif isinstance(value, list):
        for child in value:
            risks.extend(_risk_items(child))
    return risks


def _approach_split_view(approach: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "patch_plan": _find_first_mapping(approach, "patch_plan") or {},
        "implementation_systems": _find_named_strings(approach, {"systems", "subsystem", "key_modules", "implementation"}),
        "user_experience_requirements": _find_mappings_named(approach, "user_experience_requirements"),
        "risks": _risk_items(approach),
    }


def _find_first_mapping(value: Any, target_key: str) -> Mapping[str, Any] | None:
    if isinstance(value, Mapping):
        found = value.get(target_key)
        if isinstance(found, Mapping):
            return found
        for child in value.values():
            nested = _find_first_mapping(child, target_key)
            if nested is not None:
                return nested
    elif isinstance(value, list):
        for child in value:
            nested = _find_first_mapping(child, target_key)
            if nested is not None:
                return nested
    return None


def _find_mappings_named(value: Any, target_key: str) -> list[Mapping[str, Any]]:
    found: list[Mapping[str, Any]] = []
    if isinstance(value, Mapping):
        child = value.get(target_key)
        if isinstance(child, Mapping):
            found.append(child)
        for nested in value.values():
            found.extend(_find_mappings_named(nested, target_key))
    elif isinstance(value, list):
        for child in value:
            found.extend(_find_mappings_named(child, target_key))
    return found


def _find_named_strings(value: Any, target_keys: set[str]) -> list[str]:
    strings: list[str] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            if key in target_keys:
                text = _compact_text(child)
                if text:
                    strings.append(text)
            else:
                strings.extend(_find_named_strings(child, target_keys))
    elif isinstance(value, list):
        for child in value:
            strings.extend(_find_named_strings(child, target_keys))
    return strings


def _compact_text(value: Any) -> str:
    if isinstance(value, str):
        return " ".join(value.split())
    if isinstance(value, Mapping) or isinstance(value, list):
        return json.dumps(value, sort_keys=True, ensure_ascii=True)
    return ""


def _list_of_mappings(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _root_serial_id(repo: Path, branch: str) -> str:
    local = _rfc_id(branch)
    if _looks_like_serial_chain(local):
        return local
    return git_wrapper.ensure_serial(repo, branch)


def _looks_like_serial_chain(value: str) -> bool:
    parts = value.split("-")
    return bool(parts) and parts[0].isdigit() and len(parts[0]) == 4 and all(part.isdigit() for part in parts[1:])


def _criteria_for_scope(scope_items: list[Mapping[str, str]], ids: list[str]) -> list[str]:
    by_id = {item["id"]: item["text"] for item in scope_items}
    return [by_id[item] for item in ids if item in by_id]


def _dependency_errors(children: list[Any], child_keys: set[str]) -> list[str]:
    errors: list[str] = []
    for child in children:
        if not isinstance(child, Mapping):
            continue
        child_key = str(child.get("child_key", ""))
        if child.get("branching_mode") == "serial_after_child":
            predecessor = child.get("serial_after_child_key")
            if predecessor not in child_keys:
                errors.append(f"{child_key} serial_after_child_key references unknown child {predecessor}")
            if predecessor == child_key:
                errors.append(f"{child_key} cannot serially follow itself")
        deps = child.get("depends_on_child_keys")
        if isinstance(deps, list):
            for dep in deps:
                if dep not in child_keys:
                    errors.append(f"{child_key} depends_on_child_keys references unknown child {dep}")
                if dep == child_key:
                    errors.append(f"{child_key} cannot depend on itself")
    return sorted(set(errors))


def _edge_dependencies(child: Mapping[str, Any]) -> list[str]:
    deps = list(child.get("depends_on_child_keys", []))
    if child.get("branching_mode") == "serial_after_child":
        predecessor = str(child.get("serial_after_child_key", ""))
        if predecessor and predecessor not in deps:
            deps.append(predecessor)
    return deps


def _dependency_edges(children: list[Any]) -> list[dict[str, str]]:
    edges: list[dict[str, str]] = []
    for child in children:
        if not isinstance(child, Mapping):
            continue
        dependent_key = str(child.get("child_key", ""))
        for dep in _edge_dependencies(child):
            if dep:
                edges.append({"prerequisite_child_key": dep, "dependent_child_key": dependent_key})
    return edges


def _cycle(edges: list[Mapping[str, str]]) -> list[str]:
    graph: dict[str, list[str]] = {}
    for edge in edges:
        graph.setdefault(edge["prerequisite_child_key"], []).append(edge["dependent_child_key"])
    visiting: set[str] = set()
    visited: set[str] = set()
    stack: list[str] = []

    def visit(node: str) -> list[str]:
        if node in visiting:
            return stack[stack.index(node) :] + [node]
        if node in visited:
            return []
        visiting.add(node)
        stack.append(node)
        for next_node in sorted(graph.get(node, [])):
            found = visit(next_node)
            if found:
                return found
        stack.pop()
        visiting.remove(node)
        visited.add(node)
        return []

    for node in sorted(graph):
        found = visit(node)
        if found:
            return found
    return []


def _child_depths(children: list[Mapping[str, Any]]) -> dict[str, int]:
    incoming = {child["child_key"]: 0 for child in children}
    outgoing: dict[str, list[str]] = {child["child_key"]: [] for child in children}
    for edge in _dependency_edges(list(children)):
        if edge["prerequisite_child_key"] in outgoing and edge["dependent_child_key"] in incoming:
            incoming[edge["dependent_child_key"]] += 1
            outgoing[edge["prerequisite_child_key"]].append(edge["dependent_child_key"])
    ready = sorted(key for key, count in incoming.items() if count == 0)
    depths = {key: 0 for key in incoming}
    while ready:
        key = ready.pop(0)
        for child_key in sorted(outgoing[key]):
            incoming[child_key] -= 1
            depths[child_key] = max(depths[child_key], depths[key] + 1)
            if incoming[child_key] == 0:
                ready.append(child_key)
                ready.sort()
    return depths


def _topological_children(children: list[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    by_key = {child["child_key"]: child for child in children}
    incoming = {child["child_key"]: 0 for child in children}
    outgoing: dict[str, list[str]] = {child["child_key"]: [] for child in children}
    for edge in _dependency_edges(list(children)):
        incoming[edge["dependent_child_key"]] += 1
        outgoing[edge["prerequisite_child_key"]].append(edge["dependent_child_key"])
    ready = sorted(key for key, count in incoming.items() if count == 0)
    ordered: list[Mapping[str, Any]] = []
    while ready:
        key = ready.pop(0)
        ordered.append(by_key[key])
        for child_key in sorted(outgoing[key]):
            incoming[child_key] -= 1
            if incoming[child_key] == 0:
                ready.append(child_key)
                ready.sort()
    return ordered


def _first_playable_dependency_errors(children: list[Any]) -> list[str]:
    by_key = {child.get("child_key"): child for child in children if isinstance(child, Mapping)}
    errors: list[str] = []
    for edge in _dependency_edges(children):
        dependent = by_key.get(edge["dependent_child_key"])
        prerequisite = by_key.get(edge["prerequisite_child_key"])
        if not isinstance(dependent, Mapping) or not isinstance(prerequisite, Mapping):
            continue
        if (
            "patch_plan:first_playable" in dependent.get("scope_item_ids", [])
            and (prerequisite.get("declared_node_kind") or prerequisite.get("node_kind")) == "coarse"
        ):
            errors.append(
                "patch_plan:first_playable child cannot depend on a coarse child: "
                f"{dependent['child_key']} depends on {prerequisite['child_key']}"
            )
    return errors


def _validation_feedback(validation: Mapping[str, Any]) -> str:
    return json.dumps({key: value for key, value in validation.items() if key != "ok"}, sort_keys=True, ensure_ascii=True)


def _invalid(reason: str) -> dict[str, Any]:
    return {
        "ok": False,
        "status": "feedback-retry",
        "error": reason,
        "unknown_scope_ids": [],
        "unmapped_scope_ids": [],
        "double_mapped_scope_ids": [],
        "dependency_errors": [],
        "cycle": [],
    }


def _read_json(repo: Path, branch: str, path: str) -> Any:
    raw = git_wrapper.show_file(repo, branch, path)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _read_metadata(repo: Path, branch: str) -> dict[str, Any]:
    data = _read_json(repo, branch, METADATA_PATH)
    return dict(data) if isinstance(data, Mapping) else {}


def _read_ledger_from_first_parent_history(repo: Path, ref: str) -> Any:
    current = ref
    for _ in range(50):
        data = _read_json(repo, current, LEDGER_PATH)
        if isinstance(data, Mapping):
            return data
        parents = git_wrapper.parent_commits(repo, current)
        if not parents:
            return None
        current = parents[0]
    return None


def _has_integration_gate(repo: Path, branch: str) -> bool:
    return git_wrapper.has_subject(repo, branch, "lineage: integration-gate")


def _dependent_branches(repo: Path, parent_branch: str, changed_branch: str) -> list[str]:
    ledger = _read_json(repo, parent_branch, LEDGER_PATH)
    if not isinstance(ledger, Mapping) or not isinstance(ledger.get("children"), list):
        return []
    dependents: list[str] = []
    for child in ledger["children"]:
        if not isinstance(child, Mapping) or child.get("branch") == changed_branch:
            continue
        metadata = _read_metadata(repo, str(child.get("branch", "")))
        deps = metadata.get("depends_on_branches")
        if isinstance(deps, list) and changed_branch in deps:
            dependents.append(str(child["branch"]))
    return dependents


def _rfc_branch(rfc_id_or_branch: str) -> str:
    if rfc_id_or_branch.startswith("refs/heads/"):
        return rfc_id_or_branch.removeprefix("refs/heads/")
    if rfc_id_or_branch.startswith(RFC_PREFIX):
        return rfc_id_or_branch
    return f"{RFC_PREFIX}{rfc_id_or_branch}"


def _rfc_id(rfc_id_or_branch: str) -> str:
    return _rfc_branch(rfc_id_or_branch).removeprefix(RFC_PREFIX)
