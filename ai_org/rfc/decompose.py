"""RFC decomposition: split oversized RFCs into topology-encoded child RFCs."""
from __future__ import annotations

import json
from pathlib import Path
import re
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


RFC_FIELDS = RFC_VIEW_FIELDS
DEFAULT_MAX_DEPTH = 2

CHILD_FIELDS = (
    "id",
    *RFC_FIELDS,
    "depends_on",
    "provides",
    "subsystem",
    "owner",
    "change_kind",
    "order",
    "working_state",
)

SEMANTIC_FIELDS = ("change_kind", "subsystem", "owner", "working_state")

SPLIT_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": ["right_sized", "summary_sentence", "sizing_reason", "children"],
    "properties": {
        "right_sized": {"type": "boolean"},
        "summary_sentence": {"type": "string"},
        "sizing_reason": {"type": "string"},
        "children": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": list(CHILD_FIELDS),
                "properties": {
                    "id": {"type": "string"},
                    **rfc_view_schema()["properties"],
                    "depends_on": {"type": "array", "items": {"type": "string"}},
                    "provides": {"type": "array", "items": {"type": "string"}},
                    "subsystem": {"type": "string"},
                    "owner": {"type": "string"},
                    "change_kind": {"enum": ["prep", "behavior", "integration", "broad_enablement"]},
                    "order": {"type": "integer"},
                    "working_state": {"type": "string"},
                },
            },
        },
    },
}


def decompose(
    repo: str | Path,
    rfc_id_or_branch: str,
    *,
    rfc_path: str = "rfc.json",
    max_depth: int = DEFAULT_MAX_DEPTH,
) -> dict[str, Any]:
    """Decompose an oversized RFC into child RFC branches, stopping at right-sized leaves."""
    repo_path = Path(repo).resolve()
    branch = _rfc_branch(rfc_id_or_branch)
    return _decompose_branch(repo_path, branch, rfc_path, depth=0, max_depth=max_depth)


def _decompose_branch(
    repo: Path,
    branch: str,
    rfc_path: str,
    *,
    depth: int,
    max_depth: int,
) -> dict[str, Any]:
    rfc = _read_rfc(repo, branch, rfc_path)
    if not rfc["ok"]:
        return rfc

    split = _split_with_codex(repo, branch, rfc["rfc"])
    if not split["ok"]:
        return split

    result = split["split"]
    if result["right_sized"]:
        return {
            "ok": True,
            "status": "right-sized",
            "branch": branch,
            "id": _rfc_id(branch),
            "summary_sentence": result["summary_sentence"],
            "descendants": [],
            "dependency_graph": [],
        }

    if depth >= max_depth:
        return {
            "ok": True,
            "status": "max-depth",
            "branch": branch,
            "id": _rfc_id(branch),
            "summary_sentence": result["summary_sentence"],
            "children": [],
            "descendants": [],
            "dependency_graph": [],
            "blocked_by_depth_guard": [_rfc_id(branch)],
        }

    children = _normalize_children(_rfc_id(branch), result["children"], rfc["rfc"])
    if not children:
        return {
            "ok": False,
            "error": f"Codex marked {branch} too large but returned no child RFCs",
            "branch": branch,
        }

    input_edges = _input_dependency_edges(children)
    cycle = _cycle(input_edges)
    if cycle:
        return {"ok": False, "error": "split dependency graph is cyclic", "cycle": cycle, "branch": branch}

    ordered_children = _topological_children(children, input_edges)
    inherited_bases = _branch_base_refs(repo, branch)
    token_to_branch = _provider_branches(ordered_children)

    child_nodes: list[dict[str, Any]] = []
    descendants: list[dict[str, Any]] = []
    for child in ordered_children:
        child_branch = f"ai-org/rfc/{child['id']}"
        dependency_bases = _dependency_bases(child, token_to_branch)
        bases = dependency_bases or inherited_bases
        written = git_wrapper.create_branch_with_files(
            repo,
            child_branch,
            bases[0],
            {rfc_path: _rfc_view(child)},
            extra_parents=bases[1:],
            commit_message=f"rfc: decompose child {child['working_title']}",
        )
        git_wrapper.write_semantic(repo, child_branch, _semantic_labels(child))
        node = _node_payload(repo, child, child_branch, written["commit"])
        child_nodes.append(node)
        descendants.append(node)

    blocked_by_depth_guard: list[str] = []
    for child in child_nodes:
        child_result = _decompose_branch(repo, child["branch"], rfc_path, depth=depth + 1, max_depth=max_depth)
        if not child_result.get("ok"):
            return child_result
        descendants.extend(child_result.get("descendants", []))
        blocked_by_depth_guard.extend(child_result.get("blocked_by_depth_guard", []))

    descendants = _dedupe_nodes(descendants)
    graph = git_wrapper.dependency_graph(repo, [node["branch"] for node in descendants])
    return {
        "ok": True,
        "status": "decomposed",
        "branch": branch,
        "id": _rfc_id(branch),
        "children": child_nodes,
        "descendants": descendants,
        "dependency_graph": graph,
        "blocked_by_depth_guard": sorted(set(blocked_by_depth_guard)),
    }


def _split_with_codex(repo: Path, branch: str, rfc: dict[str, Any]) -> dict[str, Any]:
    run = codex_exec.run_json(
        repo,
        schema=SPLIT_SCHEMA,
        prompt=_split_prompt(branch, rfc),
        schema_filename="rfc-split.schema.json",
        output_filename="rfc-split.json",
        failure_label="Codex decomposition",
    )
    if not run["ok"]:
        return {"ok": False, "error": run["error"], "branch": branch}
    return _parse_split(run["raw"], branch)


def _split_prompt(branch: str, rfc: dict[str, Any]) -> str:
    return (
        "You are the RFC decomposition step for AI Org. Judge whether this formed RFC is too large "
        "for one contribution. A right-sized RFC is summarizable in one clear sentence, has a single "
        "concern and owner, and can be implemented as one contribution while leaving the system working.\n\n"
        "If the RFC is right-sized, set right_sized=true, put that one-sentence summary in "
        "summary_sentence, explain the sizing briefly, and return children=[].\n\n"
        "If it is too large, set right_sized=false and split it into child RFCs using Linux kernel "
        "patch-series practice:\n"
        "- Cut by subsystem and ownership first.\n"
        "- Extract PREP as prerequisite child RFCs: refactors, compatibility shims, data-model changes, "
        "APIs, and test harnesses.\n"
        "- Order children as prep before behavior before integration before broad enablement.\n"
        "- Separate mechanical rename or move work from semantic behavior change.\n"
        "- Every child must leave the system working through incremental enablement.\n"
        "- Each child must be a coherent right-sized unit when possible, with explicit depends_on and "
        "provides tokens so Python can choose git branch bases. depends_on must name provided tokens from "
        "earlier prerequisite children when a dependency exists.\n\n"
        "Decomposition only produces RFC branches. Git ancestry is the source of truth for dependencies: "
        "children with depends_on are branched on top of their prerequisites, and independent children "
        "branch from the common base. Do not schedule execution, decide parallelism, write patches, or "
        "modify files.\n\n"
        f"RFC branch: {branch}\n"
        + _format_rfc(rfc)
        + "\nReturn only JSON matching the provided schema."
    )


def _parse_split(raw: str, branch: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        return {"ok": False, "error": f"Codex decomposition returned invalid JSON: {exc}", "branch": branch}
    if not isinstance(parsed, dict):
        return {"ok": False, "error": "Codex decomposition returned non-object JSON", "branch": branch}
    if not isinstance(parsed.get("right_sized"), bool):
        return {"ok": False, "error": "Codex decomposition returned invalid right_sized", "branch": branch}
    if not isinstance(parsed.get("summary_sentence"), str):
        return {"ok": False, "error": "Codex decomposition returned invalid summary_sentence", "branch": branch}
    if not isinstance(parsed.get("sizing_reason"), str):
        return {"ok": False, "error": "Codex decomposition returned invalid sizing_reason", "branch": branch}
    children = parsed.get("children")
    if not isinstance(children, list):
        return {"ok": False, "error": "Codex decomposition returned invalid children", "branch": branch}
    for child in children:
        error = _child_error(child)
        if error:
            return {"ok": False, "error": error, "branch": branch}
    if parsed["right_sized"] and children:
        return {"ok": False, "error": "right-sized decomposition must not include children", "branch": branch}
    return {
        "ok": True,
        "split": {
            "right_sized": parsed["right_sized"],
            "summary_sentence": parsed["summary_sentence"],
            "sizing_reason": parsed["sizing_reason"],
            "children": children,
        },
    }


def _child_error(value: object) -> str:
    if not isinstance(value, dict) or set(value) != set(CHILD_FIELDS):
        return "Codex decomposition returned child with invalid fields"
    if not all(isinstance(value[field], str) for field in STRING_FIELDS):
        return "Codex decomposition returned child with invalid RFC string fields"
    if not isinstance(value["id"], str):
        return "Codex decomposition returned child with invalid id"
    for field in STRING_ARRAY_FIELDS:
        if not isinstance(value[field], list) or not all(isinstance(item, str) for item in value[field]):
            return f"Codex decomposition returned child with invalid {field}"
    if not validate_tech_stack(value["tech_stack"]):
        return "Codex decomposition returned child with invalid tech_stack"
    if not validate_user_experience_requirements(value["user_experience_requirements"]):
        return "Codex decomposition returned child with invalid user_experience_requirements"
    for field in ("depends_on", "provides"):
        if not isinstance(value[field], list) or not all(isinstance(item, str) for item in value[field]):
            return f"Codex decomposition returned child with invalid {field}"
    if value["change_kind"] not in {"prep", "behavior", "integration", "broad_enablement"}:
        return "Codex decomposition returned child with invalid change_kind"
    if not isinstance(value["order"], int):
        return "Codex decomposition returned child with invalid order"
    return ""


def _normalize_children(
    parent_id: str,
    children: list[dict[str, Any]],
    parent_rfc: Mapping[str, Any],
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    used: set[str] = set()
    for child in sorted(children, key=lambda item: item["order"]):
        copy = dict(child)
        local_id = _slug(copy["id"] or copy["working_title"])
        prefix = _slug(parent_id)
        base = local_id if local_id.startswith(prefix + "-") else _slug(f"{prefix}-{local_id}")
        copy["id"] = _unique_id(base, used)
        if isinstance(copy.get("tech_stack"), dict) and copy["tech_stack"].get("provenance") == "unspecified":
            copy["tech_stack"] = dict(parent_rfc["tech_stack"])
        normalized.append(copy)
    return normalized


def _unique_id(base: str, used: set[str]) -> str:
    candidate = base[:80].rstrip("-") or "rfc"
    if candidate not in used:
        used.add(candidate)
        return candidate
    for number in range(2, 1000):
        suffix = f"-{number}"
        candidate = (base[: 80 - len(suffix)].rstrip("-") + suffix) or f"rfc{suffix}"
        if candidate not in used:
            used.add(candidate)
            return candidate
    raise RuntimeError("could not allocate unique child RFC id")


def _input_dependency_edges(children: list[dict[str, Any]]) -> list[dict[str, str]]:
    providers: dict[str, str] = {}
    for child in children:
        for token in child["provides"]:
            providers[token] = child["id"]

    edges: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for child in children:
        for token in child["depends_on"]:
            provider = providers.get(token)
            if provider and provider != child["id"]:
                key = (provider, child["id"])
                if key not in seen:
                    seen.add(key)
                    edges.append({"from": provider, "to": child["id"]})
    return edges


def _cycle(edges: list[dict[str, str]]) -> list[str]:
    graph: dict[str, list[str]] = {}
    for edge in edges:
        graph.setdefault(edge["from"], []).append(edge["to"])

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

    for node in sorted(graph):
        found = visit(node)
        if found:
            return found
    return []


def _topological_children(
    children: list[dict[str, Any]],
    edges: list[dict[str, str]],
) -> list[dict[str, Any]]:
    by_id = {child["id"]: child for child in children}
    incoming = {child["id"]: 0 for child in children}
    outgoing: dict[str, list[str]] = {child["id"]: [] for child in children}
    for edge in edges:
        incoming[edge["to"]] += 1
        outgoing[edge["from"]].append(edge["to"])

    ready = sorted((child for child in children if incoming[child["id"]] == 0), key=lambda item: item["order"])
    ordered: list[dict[str, Any]] = []
    while ready:
        child = ready.pop(0)
        ordered.append(child)
        for next_id in sorted(outgoing[child["id"]], key=lambda item: by_id[item]["order"]):
            incoming[next_id] -= 1
            if incoming[next_id] == 0:
                ready.append(by_id[next_id])
                ready.sort(key=lambda item: item["order"])
    return ordered


def _provider_branches(children: list[dict[str, Any]]) -> dict[str, str]:
    providers: dict[str, str] = {}
    for child in children:
        branch = f"ai-org/rfc/{child['id']}"
        for token in child["provides"]:
            providers[token] = branch
    return providers


def _dependency_bases(child: dict[str, Any], token_to_branch: dict[str, str]) -> list[str]:
    bases: list[str] = []
    seen: set[str] = set()
    for token in child["depends_on"]:
        branch = token_to_branch.get(token)
        if branch and branch not in seen:
            seen.add(branch)
            bases.append(branch)
    return bases


def _branch_base_refs(repo: Path, branch: str) -> list[str]:
    parents = git_wrapper.parent_commits(repo, branch)
    if parents:
        return parents
    default = git_wrapper.default_branch(repo)
    return [git_wrapper.head_sha(repo, default) or default]


def _node_payload(repo: Path, child: dict[str, Any], branch: str, commit: str) -> dict[str, Any]:
    return {
        "id": child["id"],
        "branch": branch,
        "title": child["working_title"],
        "commit": commit,
        **git_wrapper.read_semantic(repo, branch),
    }


def _semantic_labels(child: Mapping[str, Any]) -> dict[str, str]:
    return {field: child[field] for field in SEMANTIC_FIELDS}


def _dedupe_nodes(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for node in nodes:
        key = node["id"]
        if key not in seen:
            seen.add(key)
            deduped.append(node)
    return deduped


def _read_rfc(repo: Path, branch: str, rfc_path: str) -> dict[str, Any]:
    raw = git_wrapper.show_file(repo, branch, rfc_path)
    if raw is None:
        return {"ok": False, "error": f"{rfc_path} missing at {branch}"}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        return {"ok": False, "error": f"{rfc_path} at {branch} is not parseable JSON: {exc}"}
    if not _is_rfc_view(parsed):
        return {"ok": False, "error": f"{rfc_path} at {branch} must contain exactly the RFC field registry fields"}
    return {"ok": True, "rfc": _rfc_to_view(parsed)}


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


def _rfc_to_view(rfc_view: dict[str, Any]) -> dict[str, Any]:
    return {field: rfc_view[field] for field in RFC_FIELDS}


def _rfc_view(child: Mapping[str, Any]) -> dict[str, Any]:
    return {field: child[field] for field in RFC_FIELDS}


def _format_rfc(rfc: dict[str, Any]) -> str:
    lines: list[str] = []
    for field in RFC_FIELDS:
        value = rfc[field]
        if isinstance(value, list):
            rendered = _format_alternatives(value)
        elif isinstance(value, dict):
            rendered = json.dumps(value, sort_keys=True, ensure_ascii=True)
        else:
            rendered = str(value)
        lines.append(f"{field}: {rendered}")
    return "\n".join(lines) + "\n"


def _format_alternatives(value: Any) -> str:
    if isinstance(value, list):
        return "\n".join(f"- {item}" for item in value)
    return str(value)


def _rfc_branch(rfc_id_or_branch: str) -> str:
    if rfc_id_or_branch.startswith("refs/heads/"):
        return rfc_id_or_branch.removeprefix("refs/heads/")
    if rfc_id_or_branch.startswith("ai-org/rfc/"):
        return rfc_id_or_branch
    return f"ai-org/rfc/{rfc_id_or_branch}"


def _rfc_id(rfc_id_or_branch: str) -> str:
    return _rfc_branch(rfc_id_or_branch).removeprefix("ai-org/rfc/")


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return slug[:80] or "rfc"
