"""RFC LINEAGE: split approved RFC plans into reviewable child RFC branches.

Memento: ratified node model.
- Typed relations, never one overloaded "child": version-of (inside a node as
  commits on the same RFC branch; the v2 loop already owns this), split-into[AND]
  for new child nodes, depends-on as a separate DAG where hierarchy does not
  imply order, and supersedes as a rare branch-crossing redirection marker.
  OR-alternatives are resolved at candidate deliberation today; future racing
  alternatives can add an explicit relation.
- Mutability point is Gerrit-style: a node stays revisable until the lineage's
  root PR merges into mainline, not frozen at serial assignment.
- The scarce resources are agent context and verification trust, not human
  attention. A leaf must have one concern, one bounded agent execution, one
  reviewable diff, functional_check verification, kept working state, and
  localizable failure.
- Rolling wave is information-driven: define all stages and exit criteria up
  front, leaf out only the near horizon, and keep later stages as coarse child
  nodes with acceptance criteria until their dependencies resolve.
- 100% rule: a split is valid only when every parent scope item maps to exactly
  one child or an explicit parent-retained integration gate. No child may claim
  scope outside the parent.
- Roll-up: leaf resolved means merged plus acceptance passed; parent resolved
  means all AND children resolved and the parent integration gate passed. The
  root's resolution permits the PR to mainline.
- Escalation: a child that invalidates the parent records
  blocked:parent-invalidated with evidence. The parent re-baselines via the
  existing v2 machinery. Descendants depending on the outdated version receive
  stale markers and must revalidate.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from ai_org import git_wrapper
import ai_org.rfc.codex_exec as codex_exec
from ai_org.rfc.field_registry import (
    RFC_VIEW_FIELDS,
    STRING_ARRAY_FIELDS,
    STRING_FIELDS,
    rfc_view_schema,
    validate_tech_stack,
    validate_user_experience_requirements,
)


RFC_PREFIX = "ai-org/rfc/"
CONTRIB_PREFIX = "ai-org/contrib/"
LEDGER_PATH = "lineage-ledger.json"
METADATA_PATH = "rfc-metadata.json"
APPROACH_PATH = "technical-approach.json"
RFC_FIELDS = RFC_VIEW_FIELDS
MAX_VALIDATION_ATTEMPTS = 2
MAX_RESOLUTION_DEPTH = 64

CHILD_FIELDS = (
    "child_key",
    "working_title",
    "summary",
    "horizon_status",
    "scope_item_ids",
    "acceptance_criteria",
    "relevant_systems",
    "patch_plan_slice",
)

DEPENDENCY_FIELDS = ("dependent_child_key", "prerequisite_child_key", "reason")

LINEAGE_SPLIT_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": [
        "verdict",
        "summary_sentence",
        "sizing_reason",
        "children",
        "parent_gate_scope_item_ids",
        "depends_on",
        "elaboration_notes",
    ],
    "properties": {
        "verdict": {
            "enum": ["split", "already_right_sized"],
            "description": "'already_right_sized' means THE PARENT RFC IS ALREADY ONE RIGHT-SIZED LEAF AND NO CHILD RFCS ARE NEEDED; 'split' means the children array contains the decomposition.",
        },
        "summary_sentence": {"type": "string"},
        "sizing_reason": {"type": "string"},
        "children": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": list(CHILD_FIELDS),
                "properties": {
                    "child_key": {"type": "string"},
                    "working_title": {"type": "string"},
                    "summary": {"type": "string"},
                    "horizon_status": {"enum": ["leafed", "coarse"]},
                    "scope_item_ids": {"type": "array", "items": {"type": "string"}},
                    "acceptance_criteria": {"type": "array", "items": {"type": "string"}},
                    "relevant_systems": {"type": "array", "items": {"type": "string"}},
                    "patch_plan_slice": {"type": "string"},
                },
            },
        },
        "parent_gate_scope_item_ids": {"type": "array", "items": {"type": "string"}},
        "depends_on": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": list(DEPENDENCY_FIELDS),
                "properties": {
                    "dependent_child_key": {
                        "type": "string",
                        "description": "The child_key for the child that cannot start until the prerequisite child is resolved.",
                    },
                    "prerequisite_child_key": {
                        "type": "string",
                        "description": "The child_key for the child that must resolve before the dependent child can start.",
                    },
                    "reason": {"type": "string"},
                },
            },
        },
        "elaboration_notes": {"type": "array", "items": {"type": "string"}},
    },
}


def refine(
    repo: str | Path,
    rfc_id_or_branch: str,
    *,
    horizon: int = 1,
    rfc_path: str = "rfc.json",
    approach_path: str = APPROACH_PATH,
) -> dict[str, Any]:
    """Split a direction-ok, oversized RFC plan into lineage child branches."""
    repo_path = Path(repo).resolve()
    branch = _rfc_branch(rfc_id_or_branch)
    rfc_result = _read_rfc(repo_path, branch, rfc_path)
    if not rfc_result["ok"]:
        return rfc_result
    approach_result = _read_json(repo_path, branch, approach_path)
    if not approach_result["ok"]:
        return approach_result

    rfc_view = rfc_result["rfc"]
    approach = approach_result["json"]
    if right_sized({"rfc": rfc_view, "technical_approach": approach}):
        return {"ok": True, "status": "right-sized", "branch": branch, "id": _lineage_id(repo_path, branch)}

    scope_items = _scope_items(rfc_view, approach)
    if not scope_items:
        return {"ok": False, "status": "failed-closed", "branch": branch, "error": "no parent scope items found"}

    feedback: list[str] = []
    contradiction_retried = False
    parsed: dict[str, Any] | None = None
    validation: dict[str, Any] = {"ok": False, "errors": ["lineage split did not run"]}
    for attempt in range(1, MAX_VALIDATION_ATTEMPTS + 1):
        split_result = _split_with_codex(repo_path, branch, approach, scope_items, horizon, feedback)
        if not split_result["ok"]:
            return split_result
        parsed = split_result["split"]
        if parsed["right_sized"]:
            surplus_children_ignored = parsed.get("surplus_children_ignored", 0)
            if surplus_children_ignored and not contradiction_retried:
                contradiction_retried = True
                feedback = [
                    "Clarification: deterministic pre-check says the parent is not already one right-sized leaf, "
                    "but your verdict was already_right_sized while children were populated. If the children array "
                    "contains a real decomposition, return verdict='split'. Use verdict='already_right_sized' only "
                    "when THE PARENT RFC IS ALREADY ONE RIGHT-SIZED LEAF AND NO CHILD RFCS ARE NEEDED."
                ]
                continue
            return {
                "ok": True,
                "status": "right-sized",
                "branch": branch,
                "id": _lineage_id(repo_path, branch),
                "summary_sentence": parsed["summary_sentence"],
                "surplus_children_ignored": surplus_children_ignored,
            }
        validation = validate_ledger_contract(parsed, scope_items)
        if validation["ok"]:
            break
        feedback = list(validation["errors"])
    else:
        return {
            "ok": False,
            "status": "failed-closed",
            "branch": branch,
            "error": "lineage split failed deterministic 100% coverage validation",
            "validation_errors": validation["errors"],
        }

    assert parsed is not None
    parent_id = _lineage_id(repo_path, branch)
    root_serial = _root_serial(repo_path, branch)
    children = _assign_child_ids(repo_path, parent_id, parsed["children"])
    child_by_key = {child["child_key"]: child for child in children}
    dependency_edges = _dependency_edges(parsed["depends_on"], child_by_key)
    cycle = _cycle(dependency_edges)
    if cycle:
        return {"ok": False, "status": "failed-closed", "branch": branch, "error": "lineage depends-on DAG is cyclic", "cycle": cycle}

    ledger = _ledger(root_serial, parent_id, branch, scope_items, children, dependency_edges, parsed)
    git_wrapper.commit_files(
        repo_path,
        branch,
        {LEDGER_PATH: ledger},
        subject="rfc: lineage split",
    )

    ordered_children = _topological_children(children, dependency_edges)
    child_nodes: list[dict[str, Any]] = []
    for child in ordered_children:
        child_branch = f"{RFC_PREFIX}{child['id']}"
        dependency_ids = _dependency_ids_for(child["id"], dependency_edges)
        base = f"{RFC_PREFIX}{dependency_ids[0]}" if dependency_ids else branch
        files = {
            rfc_path: _child_rfc_view(rfc_view, child),
            approach_path: _child_approach(parent_id, branch, child, approach),
            METADATA_PATH: _child_metadata(root_serial, parent_id, branch, child, dependency_ids),
        }
        written = git_wrapper.create_branch_with_files(
            repo_path,
            child_branch,
            base,
            files,
            commit_message=f"rfc: lineage child {child['id']} {child['working_title']}",
            deletions=[LEDGER_PATH],
        )
        git_wrapper.commit_empty(repo_path, child_branch, "rfc: direction-ok inherited")
        child_nodes.append(
            {
                "id": child["id"],
                "branch": child_branch,
                "title": child["working_title"],
                "horizon_status": child["horizon_status"],
                "commit": written["commit"],
                "depends_on": dependency_ids,
            }
        )

    return {
        "ok": True,
        "status": "split",
        "branch": branch,
        "id": parent_id,
        "root_serial": root_serial,
        "children": child_nodes,
        "ledger": ledger,
    }


def right_sized(rfc_view_or_approach: Mapping[str, Any]) -> bool:
    """Return whether the plan looks like one bounded, verifiable leaf."""
    approach = _unwrap_approach(rfc_view_or_approach)
    patch_plan = _patch_plan(approach)
    implementation = _implementation(approach)

    if not patch_plan:
        return False
    follow_ups = patch_plan.get("follow_ups")
    deferred = patch_plan.get("deferred")
    if isinstance(follow_ups, list) and follow_ups:
        return False
    if isinstance(deferred, list) and deferred:
        return False

    systems = implementation.get("systems") if isinstance(implementation, Mapping) else None
    if isinstance(systems, list) and len(systems) > 1:
        return False

    first_playable = patch_plan.get("first_playable")
    if isinstance(first_playable, Mapping):
        player_can = first_playable.get("player_can")
        if isinstance(player_can, list) and len(player_can) > 4:
            return False
        return bool(first_playable.get("how_verified"))
    return True


def resolved(repo: str | Path, branch: str) -> bool:
    """Return recursive lineage resolution for a leaf or parent branch."""
    repo_path = Path(repo)
    rfc_branch = _rfc_branch(branch)
    return _resolved(repo_path, rfc_branch, 0)


def _resolved(repo_path: Path, rfc_branch: str, depth: int) -> bool:
    if depth >= MAX_RESOLUTION_DEPTH:
        raise RuntimeError(f"lineage resolution exceeded maximum depth {MAX_RESOLUTION_DEPTH} at {rfc_branch}")
    ledger_result = _read_json(repo_path, rfc_branch, LEDGER_PATH)
    if ledger_result["ok"] and ledger_result["json"].get("parent_branch") == rfc_branch:
        ledger = ledger_result["json"]
        child_branches = [child.get("branch") for child in ledger.get("children", []) if isinstance(child, Mapping)]
        if not child_branches or not all(isinstance(item, str) and _resolved(repo_path, item, depth + 1) for item in child_branches):
            return False
        return git_wrapper.has_subject(repo_path, rfc_branch, "rfc: integration-gate passed")
    return _leaf_resolved(repo_path, rfc_branch)


def escalate(repo: str | Path, child_branch: str, evidence: str | Mapping[str, Any]) -> dict[str, str]:
    """Record that a child invalidated its parent plan."""
    body = evidence if isinstance(evidence, str) else json.dumps(evidence, indent=2, sort_keys=True)
    return git_wrapper.commit_empty(
        Path(repo),
        _rfc_branch(child_branch),
        "rfc: blocked:parent-invalidated",
        body=body,
    )


def mark_stale(repo: str | Path, branches: list[str], reason: str) -> list[dict[str, str]]:
    """Mark affected child branches stale after a parent re-baseline."""
    return [
        git_wrapper.commit_empty(Path(repo), _rfc_branch(branch), f"rfc: stale {reason}")
        for branch in branches
    ]


def elaborate(repo: str | Path, coarse_child_branch: str, *, horizon: int = 1) -> dict[str, Any]:
    """Re-elaborate a coarse child once its dependencies are resolved."""
    repo_path = Path(repo)
    branch = _rfc_branch(coarse_child_branch)
    metadata = _read_json(repo_path, branch, METADATA_PATH)
    if not metadata["ok"]:
        return {"ok": False, "status": "failed-closed", "branch": branch, "error": "coarse child metadata missing"}
    lineage = metadata["json"].get("lineage", {})
    if not isinstance(lineage, Mapping) or lineage.get("horizon_status") != "coarse":
        return {"ok": False, "status": "not-coarse", "branch": branch}
    blocked = [dep for dep in lineage.get("depends_on", []) if isinstance(dep, str) and not resolved(repo_path, f"{RFC_PREFIX}{dep}")]
    if blocked:
        return {"ok": False, "status": "blocked", "branch": branch, "blocked_by": blocked}
    return refine(repo_path, branch, horizon=horizon)


def split_pending(repo: str | Path, branch: str) -> bool:
    """Return whether a direction-ok non-coarse node still needs lineage splitting."""
    repo_path = Path(repo)
    rfc_branch = _rfc_branch(branch)
    if not git_wrapper.has_subject(repo_path, rfc_branch, "rfc: direction-ok"):
        return False
    if _root_serial(repo_path, rfc_branch) == "":
        return False
    if git_wrapper.file_exists(repo_path, rfc_branch, LEDGER_PATH):
        return False
    metadata = _read_json(repo_path, rfc_branch, METADATA_PATH)
    if metadata["ok"] and isinstance(metadata["json"].get("lineage"), Mapping):
        if metadata["json"]["lineage"].get("horizon_status") == "coarse":
            return False
    approach = _read_json(repo_path, rfc_branch, APPROACH_PATH)
    rfc = _read_rfc(repo_path, rfc_branch, "rfc.json")
    if not approach["ok"] or not rfc["ok"]:
        return False
    return not right_sized({"rfc": rfc["rfc"], "technical_approach": approach["json"]})


def coarse_ready(repo: str | Path, branch: str) -> bool:
    """Return whether a coarse child can be elaborated now."""
    repo_path = Path(repo)
    rfc_branch = _rfc_branch(branch)
    metadata = _read_json(repo_path, rfc_branch, METADATA_PATH)
    if not metadata["ok"]:
        return False
    lineage = metadata["json"].get("lineage")
    if not isinstance(lineage, Mapping) or lineage.get("horizon_status") != "coarse":
        return False
    if git_wrapper.file_exists(repo_path, rfc_branch, LEDGER_PATH):
        return False
    deps = [dep for dep in lineage.get("depends_on", []) if isinstance(dep, str)]
    return all(resolved(repo_path, f"{RFC_PREFIX}{dep}") for dep in deps)


def validate_ledger_contract(split: Mapping[str, Any], scope_items: list[dict[str, str]]) -> dict[str, Any]:
    """Validate the deterministic 100% coverage rule before writing branches."""
    errors: list[str] = []
    parent_scope_ids = {item["id"] for item in scope_items}
    child_keys: set[str] = set()
    assignments: dict[str, list[str]] = {item_id: [] for item_id in parent_scope_ids}

    children = split.get("children")
    if not isinstance(children, list) or not children:
        errors.append("split must contain children")
        children = []
    for child in children:
        if not isinstance(child, Mapping) or set(child) != set(CHILD_FIELDS):
            errors.append("child has invalid fields")
            continue
        child_key = child["child_key"]
        if not isinstance(child_key, str) or not child_key:
            errors.append("child_key must be a non-empty string")
            continue
        if child_key in child_keys:
            errors.append(f"duplicate child_key: {child_key}")
        child_keys.add(child_key)
        if child["horizon_status"] not in {"leafed", "coarse"}:
            errors.append(f"{child_key}: invalid horizon_status")
        for field in ("scope_item_ids", "acceptance_criteria", "relevant_systems"):
            if not isinstance(child[field], list) or not all(isinstance(item, str) for item in child[field]):
                errors.append(f"{child_key}: invalid {field}")
        if not isinstance(child["working_title"], str) or not isinstance(child["summary"], str):
            errors.append(f"{child_key}: invalid title or summary")
        if not isinstance(child["patch_plan_slice"], str):
            errors.append(f"{child_key}: invalid patch_plan_slice")
        for item_id in child.get("scope_item_ids", []):
            if item_id not in parent_scope_ids:
                errors.append(f"{child_key} claims unknown scope item {item_id}")
                continue
            assignments[item_id].append(child_key)

    parent_gate_ids = split.get("parent_gate_scope_item_ids")
    if not isinstance(parent_gate_ids, list) or not all(isinstance(item, str) for item in parent_gate_ids):
        errors.append("parent_gate_scope_item_ids must be a string array")
        parent_gate_ids = []
    for item_id in parent_gate_ids:
        if item_id not in parent_scope_ids:
            errors.append(f"parent gate claims unknown scope item {item_id}")
            continue
        assignments[item_id].append("parent_gate")

    for item_id, targets in assignments.items():
        if len(targets) == 0:
            errors.append(f"{item_id} is unmapped")
        elif len(targets) > 1:
            errors.append(f"{item_id} is mapped more than once: {', '.join(targets)}")

    depends_on = split.get("depends_on")
    if not isinstance(depends_on, list):
        errors.append("depends_on must be an array")
        depends_on = []
    for edge in depends_on:
        if not isinstance(edge, Mapping) or set(edge) != set(DEPENDENCY_FIELDS):
            errors.append("depends_on edge has invalid fields")
            continue
        dependent = edge["dependent_child_key"]
        prerequisite = edge["prerequisite_child_key"]
        if dependent not in child_keys:
            errors.append(f"depends_on dependent child is unknown: {dependent}")
        if prerequisite not in child_keys:
            errors.append(f"depends_on prerequisite child is unknown: {prerequisite}")
        if dependent == prerequisite:
            errors.append(f"depends_on self-edge is invalid: {dependent}")
        if not isinstance(edge["reason"], str):
            errors.append("depends_on reason must be a string")

    if not isinstance(split.get("elaboration_notes"), list) or not all(
        isinstance(item, str) for item in split.get("elaboration_notes", [])
    ):
        errors.append("elaboration_notes must be a string array")

    if not errors:
        children_by_key = {child["child_key"]: child for child in children}
        for edge in depends_on:
            dependent = children_by_key[edge["dependent_child_key"]]
            prerequisite = children_by_key[edge["prerequisite_child_key"]]
            if (
                "patch_plan:first_playable" in dependent["scope_item_ids"]
                and prerequisite["horizon_status"] == "coarse"
            ):
                errors.append(
                    "patch_plan:first_playable child cannot depend on a coarse child: "
                    f"{dependent['child_key']} depends on {prerequisite['child_key']}"
                )
        edges = [(edge["prerequisite_child_key"], edge["dependent_child_key"]) for edge in depends_on]
        cycle = _cycle(edges)
        if cycle:
            errors.append("depends_on DAG is cyclic: " + " -> ".join(cycle))

    return {"ok": not errors, "errors": errors}


def _split_with_codex(
    repo: Path,
    branch: str,
    approach: Mapping[str, Any],
    scope_items: list[dict[str, str]],
    horizon: int,
    feedback: list[str],
) -> dict[str, Any]:
    run = codex_exec.run_json(
        repo,
        schema=LINEAGE_SPLIT_SCHEMA,
        prompt=_split_prompt(branch, approach, scope_items, horizon, feedback),
        schema_filename="rfc-lineage-split.schema.json",
        output_filename="rfc-lineage-split.json",
        failure_label="Codex lineage split",
    )
    if not run["ok"]:
        return {"ok": False, "error": run["error"], "branch": branch}
    return _parse_split(run["raw"], branch)


def _split_prompt(
    branch: str,
    approach: Mapping[str, Any],
    scope_items: list[dict[str, str]],
    horizon: int,
    feedback: list[str],
) -> str:
    feedback_text = "\n".join(f"- {item}" for item in feedback) if feedback else "(none)"
    return (
        "You are the RFC LINEAGE split step for AI Org.\n"
        "Primary input is the already approved Technical Approach. Map the deliberated patch_plan stages, "
        "implementation systems, UX acceptance tests, success criteria, and must-address risks onto child RFCs. "
        "Do not re-derive a split from RFC prose.\n\n"
        "Rules:\n"
        "- Use split-into[AND]; alternatives were resolved before direction-ok.\n"
        "- Set verdict='already_right_sized' only when THE PARENT RFC IS ALREADY ONE RIGHT-SIZED LEAF AND NO CHILD "
        "RFCS ARE NEEDED.\n"
        "- Set verdict='split' when the children array contains the decomposition of the parent.\n"
        "- Near-horizon stages become right-sized leafed children.\n"
        "- Later stages stay coarse but must have acceptance criteria and exit criteria.\n"
        "- Every parent scope item must appear exactly once, either in one child scope_item_ids or in "
        "parent_gate_scope_item_ids.\n"
        "- Do not invent scope_item_ids outside the provided ledger.\n"
        "- depends_on is a DAG between child_key values. It is not an execution order.\n"
        "- Example: if child 'gate_evidence' builds on child 'first_playable', set "
        "dependent_child_key='gate_evidence' and prerequisite_child_key='first_playable'.\n"
        "- Do not include an integer order field anywhere.\n\n"
        f"RFC branch: {branch}\n"
        f"Near horizon leaf budget: {horizon}\n"
        f"Previous deterministic validation feedback:\n{feedback_text}\n\n"
        f"Parent scope items:\n{json.dumps(scope_items, indent=2, sort_keys=True, ensure_ascii=True)}\n\n"
        f"Approved Technical Approach:\n{json.dumps(approach, indent=2, sort_keys=True, ensure_ascii=True, default=str)}\n\n"
        "Return only JSON matching the provided schema."
    )


def _parse_split(raw: str, branch: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        return {"ok": False, "error": f"Codex lineage split returned invalid JSON: {exc}", "branch": branch}
    if not isinstance(parsed, dict) or set(parsed) != set(LINEAGE_SPLIT_SCHEMA["properties"]):
        return {"ok": False, "error": "Codex lineage split returned invalid fields", "branch": branch}
    if parsed.get("verdict") not in {"split", "already_right_sized"}:
        return {"ok": False, "error": "Codex lineage split returned invalid verdict", "branch": branch}
    if not isinstance(parsed.get("summary_sentence"), str) or not isinstance(parsed.get("sizing_reason"), str):
        return {"ok": False, "error": "Codex lineage split returned invalid sizing text", "branch": branch}
    parsed = {**parsed, "right_sized": parsed["verdict"] == "already_right_sized"}
    if parsed["right_sized"]:
        children = parsed.get("children")
        surplus_children_ignored = len(children) if isinstance(children, list) else 0
        return {
            "ok": True,
            "split": {
                **parsed,
                "children": [],
                "parent_gate_scope_item_ids": [],
                "depends_on": [],
                "elaboration_notes": [],
                "surplus_children_ignored": surplus_children_ignored,
            },
        }
    shape_error = _split_shape_error(parsed)
    if shape_error:
        return {"ok": False, "error": shape_error, "branch": branch}
    return {"ok": True, "split": parsed}


def _split_shape_error(parsed: Mapping[str, Any]) -> str:
    if not isinstance(parsed.get("children"), list):
        return "Codex lineage split returned invalid children"
    for child in parsed["children"]:
        if not isinstance(child, Mapping) or set(child) != set(CHILD_FIELDS):
            return "Codex lineage split returned child with invalid fields"
        if child.get("horizon_status") not in {"leafed", "coarse"}:
            return "Codex lineage split returned invalid horizon_status"
        for field in ("child_key", "working_title", "summary", "patch_plan_slice"):
            if not isinstance(child.get(field), str):
                return f"Codex lineage split returned invalid {field}"
        for field in ("scope_item_ids", "acceptance_criteria", "relevant_systems"):
            if not isinstance(child.get(field), list) or not all(isinstance(item, str) for item in child[field]):
                return f"Codex lineage split returned invalid {field}"
    if not isinstance(parsed.get("parent_gate_scope_item_ids"), list) or not all(
        isinstance(item, str) for item in parsed.get("parent_gate_scope_item_ids", [])
    ):
        return "Codex lineage split returned invalid parent_gate_scope_item_ids"
    if not isinstance(parsed.get("depends_on"), list):
        return "Codex lineage split returned invalid depends_on"
    for edge in parsed["depends_on"]:
        if not isinstance(edge, Mapping) or set(edge) != set(DEPENDENCY_FIELDS):
            return "Codex lineage split returned depends_on edge with invalid fields"
        if not all(isinstance(edge[field], str) for field in DEPENDENCY_FIELDS):
            return "Codex lineage split returned depends_on edge with invalid values"
    if not isinstance(parsed.get("elaboration_notes"), list) or not all(
        isinstance(item, str) for item in parsed.get("elaboration_notes", [])
    ):
        return "Codex lineage split returned invalid elaboration_notes"
    return ""


def _scope_items(rfc_view: Mapping[str, Any], approach: Mapping[str, Any]) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    problem = approach.get("problem") if isinstance(approach, Mapping) else None
    goals = problem.get("goals", []) if isinstance(problem, Mapping) else []
    for index, goal in enumerate(goals):
        if isinstance(goal, Mapping):
            items.append(_scope_item(f"success_criteria:{index}", f"technical_approach.problem.goals[{index}]", goal))
    if not items and isinstance(rfc_view.get("desired_outcomes_success"), str):
        items.append(
            {
                "id": "success_criteria:rfc",
                "source": "rfc.desired_outcomes_success",
                "text": rfc_view["desired_outcomes_success"],
            }
        )

    ux = rfc_view.get("user_experience_requirements")
    acceptance = ux.get("acceptance_tests") if isinstance(ux, Mapping) else None
    if isinstance(acceptance, Mapping):
        for field in sorted(acceptance):
            values = acceptance.get(field)
            if isinstance(values, list):
                for index, value in enumerate(values):
                    if isinstance(value, str):
                        items.append(
                            {
                                "id": f"acceptance_tests:{field}:{index}",
                                "source": f"rfc.user_experience_requirements.acceptance_tests.{field}[{index}]",
                                "text": value,
                            }
                        )

    patch_plan = _patch_plan(approach)
    if isinstance(patch_plan.get("first_playable"), Mapping):
        items.append(_scope_item("patch_plan:first_playable", "technical_approach.patch_plan.first_playable", patch_plan["first_playable"]))
    for field in ("follow_ups", "deferred"):
        values = patch_plan.get(field)
        if isinstance(values, list):
            for index, value in enumerate(values):
                if isinstance(value, Mapping):
                    items.append(_scope_item(f"patch_plan:{field}:{index}", f"technical_approach.patch_plan.{field}[{index}]", value))

    for risk in _risks(approach):
        risk_id = str(risk.get("id") or len(items))
        items.append(_scope_item(f"must_address_risks:{risk_id}", "technical_approach.risks", risk))
    return items


def _scope_item(item_id: str, source: str, value: Mapping[str, Any]) -> dict[str, str]:
    return {"id": item_id, "source": source, "text": json.dumps(value, sort_keys=True, ensure_ascii=True)}


def _assign_child_ids(repo: Path, parent_id: str, children: list[dict[str, Any]]) -> list[dict[str, Any]]:
    assigned: list[dict[str, Any]] = []
    next_index = 1
    for child in children:
        while git_wrapper.branch_exists(repo, f"{RFC_PREFIX}{parent_id}-{next_index}"):
            next_index += 1
        copy = dict(child)
        copy["id"] = f"{parent_id}-{next_index}"
        assigned.append(copy)
        next_index += 1
    return assigned


def _dependency_edges(raw_edges: list[Mapping[str, Any]], child_by_key: Mapping[str, Mapping[str, Any]]) -> list[dict[str, str]]:
    edges: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for edge in raw_edges:
        dependent = child_by_key[edge["dependent_child_key"]]["id"]
        prerequisite = child_by_key[edge["prerequisite_child_key"]]["id"]
        key = (dependent, prerequisite)
        if key in seen:
            continue
        seen.add(key)
        edges.append(
            {
                "dependent_child_id": dependent,
                "prerequisite_child_id": prerequisite,
                "reason": edge["reason"],
            }
        )
    return edges


def _topological_children(children: list[dict[str, Any]], edges: list[dict[str, str]]) -> list[dict[str, Any]]:
    by_id = {child["id"]: child for child in children}
    incoming = {child["id"]: 0 for child in children}
    outgoing: dict[str, list[str]] = {child["id"]: [] for child in children}
    for edge in edges:
        dependent = edge["dependent_child_id"]
        prerequisite = edge["prerequisite_child_id"]
        incoming[dependent] += 1
        outgoing[prerequisite].append(dependent)

    ready = [child["id"] for child in children if incoming[child["id"]] == 0]
    ordered: list[dict[str, Any]] = []
    while ready:
        child_id = ready.pop(0)
        ordered.append(by_id[child_id])
        for next_id in outgoing[child_id]:
            incoming[next_id] -= 1
            if incoming[next_id] == 0:
                ready.append(next_id)
    return ordered


def _cycle(edges: list[Mapping[str, str] | tuple[str, str]]) -> list[str]:
    graph: dict[str, list[str]] = {}
    for edge in edges:
        if isinstance(edge, tuple):
            source, target = edge
        elif "prerequisite_child_id" in edge and "dependent_child_id" in edge:
            source, target = edge["prerequisite_child_id"], edge["dependent_child_id"]
        else:
            source, target = edge["from"], edge["to"]
        graph.setdefault(source, []).append(target)

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
        for next_node in graph.get(node, []):
            found = visit(next_node)
            if found:
                return found
        stack.pop()
        visiting.remove(node)
        visited.add(node)
        return []

    for node in graph:
        found = visit(node)
        if found:
            return found
    return []


def _dependency_ids_for(child_id: str, edges: list[dict[str, str]]) -> list[str]:
    return [edge["prerequisite_child_id"] for edge in edges if edge["dependent_child_id"] == child_id]


def _ledger(
    root_serial: str,
    parent_id: str,
    parent_branch: str,
    scope_items: list[dict[str, str]],
    children: list[dict[str, Any]],
    dependency_edges: list[dict[str, str]],
    split: Mapping[str, Any],
) -> dict[str, Any]:
    coverage: dict[str, str] = {}
    for child in children:
        for item_id in child["scope_item_ids"]:
            coverage[item_id] = child["id"]
    for item_id in split["parent_gate_scope_item_ids"]:
        coverage[item_id] = "parent_gate"
    return {
        "schema_version": 1,
        "relation": "split-into[AND]",
        "root_serial": root_serial,
        "parent_id": parent_id,
        "parent_branch": parent_branch,
        "scope_items": scope_items,
        "coverage": coverage,
        "depends_on": dependency_edges,
        "children": [
            {
                "id": child["id"],
                "branch": f"{RFC_PREFIX}{child['id']}",
                "title": child["working_title"],
                "horizon_status": child["horizon_status"],
                "scope_item_ids": list(child["scope_item_ids"]),
                "acceptance_criteria": list(child["acceptance_criteria"]),
                "elaboration_note": child["summary"] if child["horizon_status"] == "coarse" else "",
            }
            for child in children
        ],
        "parent_gate": {
            "scope_item_ids": list(split["parent_gate_scope_item_ids"]),
            "acceptance_criteria": ["All child branches are resolved and integrated without violating retained parent scope."],
        },
        "elaboration_notes": list(split["elaboration_notes"]),
    }


def _child_metadata(
    root_serial: str,
    parent_id: str,
    parent_branch: str,
    child: Mapping[str, Any],
    dependency_ids: list[str],
) -> dict[str, Any]:
    return {
        "lineage": {
            "node_id": child["id"],
            "root_serial": root_serial,
            "parent_id": parent_id,
            "parent_branch": parent_branch,
            "relation": "split-into[AND]",
            "horizon_status": child["horizon_status"],
            "depends_on": dependency_ids,
            "primary_dependency": dependency_ids[0] if dependency_ids else "",
            "additional_dependencies": dependency_ids[1:],
            "scope_item_ids": list(child["scope_item_ids"]),
            "acceptance_criteria": list(child["acceptance_criteria"]),
            "elaboration_note": child["summary"] if child["horizon_status"] == "coarse" else "",
        }
    }


def _child_rfc_view(parent: Mapping[str, Any], child: Mapping[str, Any]) -> dict[str, Any]:
    view = {field: parent[field] for field in RFC_FIELDS}
    view["raw_request"] = f"{parent['raw_request']}\n\nLineage child {child['id']}: {child['summary']}"
    view["working_title"] = child["working_title"]
    view["problem_or_motivation"] = child["summary"]
    view["desired_outcomes_success"] = " ".join(child["acceptance_criteria"]) or child["summary"]
    view["constraints_assumptions"] = list(parent.get("constraints_assumptions", [])) + [
        f"Lineage scope item ids: {', '.join(child['scope_item_ids'])}",
        "Child direction is inherited from the parent direction-ok review.",
    ]
    view["proposal_hint"] = child["patch_plan_slice"]
    return view


def _child_approach(
    parent_id: str,
    parent_branch: str,
    child: Mapping[str, Any],
    approach: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "lineage_parent": {"id": parent_id, "branch": parent_branch},
        "scope_item_ids": list(child["scope_item_ids"]),
        "approach_slice": {
            "summary": child["summary"],
            "horizon_status": child["horizon_status"],
            "acceptance_criteria": list(child["acceptance_criteria"]),
            "relevant_systems": list(child["relevant_systems"]),
            "patch_plan_slice": child["patch_plan_slice"],
        },
        "parent_patch_plan": _patch_plan(approach),
    }


def _leaf_resolved(repo: Path, rfc_branch: str) -> bool:
    rfc_id = rfc_branch.removeprefix(RFC_PREFIX)
    contrib = f"{CONTRIB_PREFIX}{rfc_id}"
    accepted = git_wrapper.has_subject(repo, contrib, "acceptance: reachable") or git_wrapper.has_subject(
        repo, rfc_branch, "acceptance: reachable"
    )
    merged = (
        git_wrapper.branch_exists(repo, contrib)
        and (
            git_wrapper.is_ancestor(repo, contrib, "ai-org/subsystem")
            or git_wrapper.is_ancestor(repo, contrib, "ai-org/mainline")
            or git_wrapper.is_ancestor(repo, contrib, git_wrapper.default_branch(repo))
        )
    ) or git_wrapper.has_subject(repo, rfc_branch, "rfc: merged")
    return accepted and merged


def _unwrap_approach(value: Mapping[str, Any]) -> Mapping[str, Any]:
    if isinstance(value.get("technical_approach"), Mapping):
        return value["technical_approach"]
    return value


def _implementation(approach: Mapping[str, Any]) -> Mapping[str, Any]:
    decision = _decision(approach)
    implementation = decision.get("implementation") if isinstance(decision, Mapping) else None
    return implementation if isinstance(implementation, Mapping) else {}


def _patch_plan(approach: Mapping[str, Any]) -> Mapping[str, Any]:
    implementation = _implementation(approach)
    patch_plan = implementation.get("patch_plan") if isinstance(implementation, Mapping) else None
    return patch_plan if isinstance(patch_plan, Mapping) else {}


def _risks(approach: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    risks: list[Mapping[str, Any]] = []
    decision = _decision(approach)
    if isinstance(decision.get("risks"), list):
        risks.extend(item for item in decision["risks"] if isinstance(item, Mapping))
    implementation = _implementation(approach)
    if isinstance(implementation.get("risks"), list):
        risks.extend(item for item in implementation["risks"] if isinstance(item, Mapping))
    return risks


def _decision(approach: Mapping[str, Any]) -> Mapping[str, Any]:
    problem = approach.get("problem") if isinstance(approach, Mapping) else None
    question = problem.get("question") if isinstance(problem, Mapping) else None
    decision = question.get("decision") if isinstance(question, Mapping) else None
    return decision if isinstance(decision, Mapping) else {}


def _read_rfc(repo: Path, branch: str, rfc_path: str) -> dict[str, Any]:
    raw = git_wrapper.show_file(repo, branch, rfc_path)
    if raw is None:
        return {"ok": False, "error": f"{rfc_path} missing at {branch}", "branch": branch}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        return {"ok": False, "error": f"{rfc_path} at {branch} is not parseable JSON: {exc}", "branch": branch}
    if not _is_rfc_view(parsed):
        return {"ok": False, "error": f"{rfc_path} at {branch} must contain exactly the RFC field registry fields", "branch": branch}
    return {"ok": True, "rfc": {field: parsed[field] for field in RFC_FIELDS}}


def _read_json(repo: Path, branch: str, path: str) -> dict[str, Any]:
    raw = git_wrapper.show_file(repo, branch, path)
    if raw is None:
        return {"ok": False, "error": f"{path} missing at {branch}", "branch": branch}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        return {"ok": False, "error": f"{path} at {branch} is not parseable JSON: {exc}", "branch": branch}
    if not isinstance(parsed, dict):
        return {"ok": False, "error": f"{path} at {branch} must contain a JSON object", "branch": branch}
    return {"ok": True, "json": parsed}


def _is_rfc_view(value: object) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == set(RFC_FIELDS)
        and all(isinstance(value[field], str) for field in STRING_FIELDS)
        and all(
            isinstance(value[field], list) and all(isinstance(item, str) for item in value[field])
            for field in STRING_ARRAY_FIELDS
        )
        and validate_tech_stack(value.get("tech_stack"))
        and validate_user_experience_requirements(value.get("user_experience_requirements"))
    )


def _root_serial(repo: Path, branch: str) -> str:
    serial = git_wrapper.serial_for_ref(repo, branch)
    return serial or ""


def _lineage_id(repo: Path, branch: str) -> str:
    rfc_id = _rfc_id(branch)
    serial = _root_serial(repo, branch)
    if serial and (rfc_id == serial or rfc_id.startswith(serial + "-")):
        return rfc_id
    return serial or rfc_id


def _rfc_branch(rfc_id_or_branch: str) -> str:
    if rfc_id_or_branch.startswith("refs/heads/"):
        return rfc_id_or_branch.removeprefix("refs/heads/")
    if rfc_id_or_branch.startswith(RFC_PREFIX):
        return rfc_id_or_branch
    return f"{RFC_PREFIX}{rfc_id_or_branch}"


def _rfc_id(rfc_id_or_branch: str) -> str:
    return _rfc_branch(rfc_id_or_branch).removeprefix(RFC_PREFIX)
