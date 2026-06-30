"""RFC decomposition: split oversized RFCs into dependency-ordered child RFCs."""
from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Any, Mapping

from ai_org.rfc.receive import COMMON_8_FIELDS
from ai_org.rfc.receive import _default_branch
from ai_org.rfc.receive import _is_rfc_view
from ai_org.rfc.receive import _rfc_to_view
from ai_org.rfc.receive import _write_rfc_branch


RFC_FIELDS = COMMON_8_FIELDS
CHILD_METADATA_PATH = "rfc-metadata.json"
DECOMPOSITION_PATH = "rfc-decomposition.json"
DEFAULT_MAX_DEPTH = 2

CHILD_FIELDS = (
    "id",
    "title",
    "problem",
    "proposal",
    "alternatives",
    "intended_users",
    "affected_area",
    "impact",
    "context",
    "depends_on",
    "provides",
    "subsystem",
    "owner",
    "change_kind",
    "order",
    "working_state",
)

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
                    "title": {"type": "string"},
                    "problem": {"type": "string"},
                    "proposal": {"type": "string"},
                    "alternatives": {"type": "array", "items": {"type": "string"}},
                    "intended_users": {"type": "string"},
                    "affected_area": {"type": "string"},
                    "impact": {"type": "string"},
                    "context": {"type": "string"},
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
    original = _current_branch(repo_path)
    try:
        return _decompose_branch(repo_path, branch, rfc_path, depth=0, max_depth=max_depth)
    finally:
        if original:
            _git(repo_path, "checkout", original)


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

    base_commit = _base_commit(repo, branch)
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
            "base_commit": base_commit,
            "summary_sentence": result["summary_sentence"],
            "descendants": [],
            "edges": [],
        }

    if depth >= max_depth:
        tracking = _tracking_payload(
            branch,
            base_commit,
            depth,
            max_depth,
            result,
            [],
            [],
            [],
            [_rfc_id(branch)],
        )
        write = _write_tracking(repo, branch, base_commit, rfc["rfc"], rfc_path, tracking)
        return {
            "ok": True,
            "status": "max-depth",
            "branch": branch,
            "id": _rfc_id(branch),
            "base_commit": base_commit,
            "summary_sentence": result["summary_sentence"],
            "children": [],
            "descendants": [],
            "edges": [],
            "tracking_commit": write["commit"],
            "blocked_by_depth_guard": [_rfc_id(branch)],
        }

    children = _normalize_children(_rfc_id(branch), result["children"])
    if not children:
        return {
            "ok": False,
            "error": f"Codex marked {branch} too large but returned no child RFCs",
            "branch": branch,
        }

    child_nodes: list[dict[str, Any]] = []
    descendants: list[dict[str, Any]] = []
    edges = _dependency_edges(children)
    cycle = _cycle(edges)
    if cycle:
        return {"ok": False, "error": "split dependency graph is cyclic", "cycle": cycle, "branch": branch}

    for child in children:
        child_branch = f"ai-org/rfc/{child['id']}"
        metadata = _child_metadata(branch, base_commit, depth + 1, child)
        written = _write_rfc_branch(
            repo,
            child_branch,
            base_commit,
            _rfc_view(child),
            rfc_path=rfc_path,
            extra_files={CHILD_METADATA_PATH: metadata},
            commit_message=f"rfc: decompose child {child['title']}",
        )
        node = _node_payload(child, child_branch, base_commit, depth + 1, written["commit"])
        child_nodes.append(node)
        descendants.append(node)

    blocked_by_depth_guard: list[str] = []
    for child in child_nodes:
        child_result = _decompose_branch(repo, child["branch"], rfc_path, depth=depth + 1, max_depth=max_depth)
        if not child_result.get("ok"):
            return child_result
        descendants.extend(child_result.get("descendants", []))
        edges.extend(child_result.get("edges", []))
        blocked_by_depth_guard.extend(child_result.get("blocked_by_depth_guard", []))

    tracking = _tracking_payload(
        branch,
        base_commit,
        depth,
        max_depth,
        result,
        child_nodes,
        descendants,
        edges,
        blocked_by_depth_guard,
    )
    write = _write_tracking(repo, branch, base_commit, rfc["rfc"], rfc_path, tracking)
    return {
        "ok": True,
        "status": "decomposed",
        "branch": branch,
        "id": _rfc_id(branch),
        "base_commit": base_commit,
        "children": child_nodes,
        "descendants": descendants,
        "edges": edges,
        "tracking_commit": write["commit"],
        "blocked_by_depth_guard": blocked_by_depth_guard,
    }


def _split_with_codex(repo: Path, branch: str, rfc: dict[str, Any]) -> dict[str, Any]:
    temp_dir = Path(tempfile.mkdtemp(prefix="ai-org-rfc-decompose-"))
    schema_file = temp_dir / "rfc-decomposition.schema.json"
    out_file = temp_dir / "rfc-decomposition.json"
    try:
        schema_file.write_text(json.dumps(SPLIT_SCHEMA, indent=2), encoding="utf-8")
        cmd = [
            "codex",
            "exec",
            "--sandbox",
            "read-only",
            "-C",
            str(repo),
            "-o",
            str(out_file),
            "--output-schema",
            str(schema_file),
            _split_prompt(branch, rfc),
        ]
        try:
            completed = subprocess.run(
                cmd,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
            )
        except OSError as exc:
            return {"ok": False, "error": f"Codex decomposition failed: {exc}", "branch": branch}
        if completed.returncode != 0:
            detail = completed.stderr.strip() or (
                "no output file" if not out_file.exists() else "Codex decomposition did not complete successfully."
            )
            return {"ok": False, "error": f"Codex decomposition failed: {detail}", "branch": branch}
        if not out_file.exists():
            return {"ok": False, "error": "Codex decomposition failed: no output file", "branch": branch}
        return _parse_split(out_file.read_text(encoding="utf-8"), branch)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


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
        "provides tokens so Python can write a dependency DAG. depends_on must name provided tokens from "
        "earlier prerequisite children when a dependency exists.\n\n"
        "Decomposition only produces RFC branches and a DAG. Do not schedule execution, decide parallelism, "
        "write patches, or modify files.\n\n"
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
    if not all(isinstance(value[field], str) for field in RFC_FIELDS if field != "alternatives"):
        return "Codex decomposition returned child with invalid RFC string fields"
    if not isinstance(value["id"], str):
        return "Codex decomposition returned child with invalid id"
    if not isinstance(value["alternatives"], list) or not all(isinstance(item, str) for item in value["alternatives"]):
        return "Codex decomposition returned child with invalid alternatives"
    for field in ("depends_on", "provides"):
        if not isinstance(value[field], list) or not all(isinstance(item, str) for item in value[field]):
            return f"Codex decomposition returned child with invalid {field}"
    if value["change_kind"] not in {"prep", "behavior", "integration", "broad_enablement"}:
        return "Codex decomposition returned child with invalid change_kind"
    if not isinstance(value["order"], int):
        return "Codex decomposition returned child with invalid order"
    return ""


def _normalize_children(parent_id: str, children: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    used: set[str] = set()
    for child in sorted(children, key=lambda item: item["order"]):
        copy = dict(child)
        local_id = _slug(copy["id"] or copy["title"])
        prefix = _slug(parent_id)
        base = local_id if local_id.startswith(prefix + "-") else _slug(f"{prefix}-{local_id}")
        copy["id"] = _unique_id(base, used)
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


def _dependency_edges(children: list[dict[str, Any]]) -> list[dict[str, str]]:
    providers: dict[str, str] = {}
    for child in children:
        for token in child["provides"]:
            providers[token] = child["id"]

    edges: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for child in children:
        for token in child["depends_on"]:
            provider = providers.get(token)
            if provider and provider != child["id"]:
                key = (provider, child["id"], token)
                if key not in seen:
                    seen.add(key)
                    edges.append({"from": provider, "to": child["id"], "via": token})
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


def _child_metadata(parent_branch: str, base_commit: str, depth: int, child: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "kind": "rfc-decomposition-child",
        "parent": parent_branch,
        "base_commit": base_commit,
        "depends_on": child["depends_on"],
        "provides": child["provides"],
        "depth": depth,
        "subsystem": child["subsystem"],
        "owner": child["owner"],
        "change_kind": child["change_kind"],
        "order": child["order"],
        "working_state": child["working_state"],
    }


def _node_payload(
    child: dict[str, Any],
    branch: str,
    base_commit: str,
    depth: int,
    commit: str,
) -> dict[str, Any]:
    return {
        "id": child["id"],
        "branch": branch,
        "title": child["title"],
        "depends_on": child["depends_on"],
        "provides": child["provides"],
        "base_commit": base_commit,
        "depth": depth,
        "subsystem": child["subsystem"],
        "owner": child["owner"],
        "change_kind": child["change_kind"],
        "order": child["order"],
        "working_state": child["working_state"],
        "commit": commit,
    }


def _tracking_payload(
    branch: str,
    base_commit: str,
    depth: int,
    max_depth: int,
    split: dict[str, Any],
    children: list[dict[str, Any]],
    descendants: list[dict[str, Any]],
    edges: list[dict[str, str]],
    blocked_by_depth_guard: list[str],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "kind": "rfc-decomposition-tracking",
        "parent": branch,
        "base_commit": base_commit,
        "depth": depth,
        "max_depth": max_depth,
        "right_sized": False,
        "summary_sentence": split["summary_sentence"],
        "sizing_reason": split["sizing_reason"],
        "children": children,
        "descendants": _dedupe_nodes(descendants),
        "edges": _dedupe_edges(edges),
        "blocked_by_depth_guard": sorted(set(blocked_by_depth_guard)),
    }


def _write_tracking(
    repo: Path,
    branch: str,
    base: str,
    rfc: Mapping[str, Any],
    rfc_path: str,
    tracking: dict[str, Any],
) -> dict[str, str]:
    del base
    return _write_rfc_branch(
        repo,
        branch,
        branch,
        rfc,
        rfc_path=rfc_path,
        extra_files={DECOMPOSITION_PATH: tracking},
        commit_message="rfc: decompose tracking",
    )


def _dedupe_nodes(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for node in nodes:
        key = node["id"]
        if key not in seen:
            seen.add(key)
            deduped.append(node)
    return deduped


def _dedupe_edges(edges: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[tuple[str, str, str]] = set()
    deduped: list[dict[str, str]] = []
    for edge in edges:
        key = (edge["from"], edge["to"], edge["via"])
        if key not in seen:
            seen.add(key)
            deduped.append(edge)
    return deduped


def _read_rfc(repo: Path, branch: str, rfc_path: str) -> dict[str, Any]:
    result = _git_run(repo, "show", f"{branch}:{rfc_path}")
    if result.returncode != 0:
        return {"ok": False, "error": f"{rfc_path} missing at {branch}", "stderr": result.stderr}
    try:
        parsed = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return {"ok": False, "error": f"{rfc_path} at {branch} is not parseable JSON: {exc}"}
    if not _is_rfc_view(parsed):
        return {"ok": False, "error": f"{rfc_path} at {branch} must contain exactly the COMMON-8 fields"}
    return {"ok": True, "rfc": _rfc_to_view(parsed)}


def _rfc_view(child: Mapping[str, Any]) -> dict[str, Any]:
    return {field: child[field] for field in RFC_FIELDS}


def _format_rfc(rfc: dict[str, Any]) -> str:
    return (
        f"title: {rfc['title']}\n"
        f"problem: {rfc['problem']}\n"
        f"proposal: {rfc['proposal']}\n"
        f"alternatives: {_format_alternatives(rfc['alternatives'])}\n"
        f"intended_users: {rfc['intended_users']}\n"
        f"affected_area: {rfc['affected_area']}\n"
        f"impact: {rfc['impact']}\n"
        f"context: {rfc['context']}\n"
    )


def _format_alternatives(value: Any) -> str:
    if isinstance(value, list):
        return "\n".join(f"- {item}" for item in value)
    return str(value)


def _base_commit(repo: Path, branch: str) -> str:
    base_ref = _default_branch(repo)
    merge_base = _git_run(repo, "merge-base", branch, base_ref)
    if merge_base.returncode == 0 and merge_base.stdout.strip():
        return merge_base.stdout.strip()
    return _git(repo, "rev-parse", base_ref).strip()


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


def _current_branch(repo: Path) -> str:
    result = _git_run(repo, "symbolic-ref", "--short", "HEAD")
    if result.returncode == 0:
        return result.stdout.strip()
    return ""


def _git(repo: Path, *args: str) -> str:
    result = _git_run(repo, *args)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "git command failed")
    return result.stdout


def _git_run(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
