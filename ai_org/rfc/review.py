"""RFC direction review: group-internal critique before implementation.

This module models the RFC review round as the project's LKML-thread analogue:
the author posts an RFC plus Technical Approach, reviewers discuss direction
inside the group, and the result is a consensus milestone before patch work. It
is not the binding mainline gate. The fierce independent maintainer review lives
later at the PR/mainline boundary.

The kernel basis is intentionally narrow and concrete:
  - LKML inline quoting maps to objections anchored to existing derivation-tree
    node ids from technical-approach.json.
  - Reviewed-by/Acked-by vocabulary maps to per-axis Direction-reviewed-by when
    serious concerns are raised and no known serious direction issue remains.
  - NAK is reserved for an evidenced decision record that the direction is
    fundamentally unsuitable, not for accumulated nits.

Review critiques; it never authors replacement designs. The author side is
responsible for re-forming and reposting v2 from the review record. Aufheben is
therefore demoted from "write a revised RFC" to consolidation: aggregate
objections, deduplicate by anchor plus claim, resolve reviewer contradictions,
and produce the round verdict.

The thread record is off-git at .ai-org/review/<rfc-id>/round-<N>.json. Git
stores only the result marker on the RFC branch: rfc: direction-ok,
rfc: needs-revision round N, or rfc: nak. The v2 resend loop and the later Linon
PR-gate review are separate pieces.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Any, Mapping

from ai_org import git_wrapper, reference
from ai_org.rfc.field_registry import RFC_VIEW_FIELDS, STRING_ARRAY_FIELDS, STRING_FIELDS, validate_tech_stack


@dataclass(frozen=True)
class Dimension:
    key: str
    blurb: str


DIMENSIONS: list[Dimension] = [
    Dimension("need", "whether the change is wanted and the problem remains worth solving"),
    Dimension("approach", "whether the selected technical approach and interfaces are right"),
    Dimension("compat", "whether behavior, data, API, config, or user expectations regress"),
    Dimension("scope", "whether the proposed slice, prerequisites, and split are right"),
    Dimension("maintenance", "whether long-term ownership and cost are justified"),
]

AXES = [dimension.key for dimension in DIMENSIONS]
OBJECTION_TYPES = ["blocking", "clarification", "nonblocking_suggestion", "style_defer"]
AUTHOR_ACTIONS = ["re_explain", "provide_evidence", "revise_subtree", "split_scope", "withdraw"]
AXIS_VERDICTS = ["Direction-reviewed-by", "objections_pending"]
ROUND_VERDICTS = ["direction-ok", "needs_revision", "nak"]
EVIDENCE_TYPES = ["reference", "prior_decision", "repo_fact", "tree_node", "rfc_field"]


EVIDENCE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["source_type", "citation", "consulted_terms"],
    "properties": {
        "source_type": {"enum": EVIDENCE_TYPES},
        "citation": {"type": "string"},
        "consulted_terms": {"type": "array", "items": {"type": "string"}},
    },
}

OBJECTION_ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "objection_id",
        "anchor_node_ids",
        "axis",
        "type",
        "claim",
        "evidence",
        "impact",
        "requested_author_action",
        "status",
    ],
    "properties": {
        "objection_id": {"type": "string"},
        "anchor_node_ids": {"type": "array", "items": {"type": "string"}},
        "axis": {"enum": AXES},
        "type": {"enum": OBJECTION_TYPES},
        "claim": {"type": "string"},
        "evidence": {"type": "array", "items": EVIDENCE_SCHEMA},
        "impact": {"type": "string"},
        "requested_author_action": {"enum": AUTHOR_ACTIONS},
        "status": {"enum": ["open"]},
    },
}

OBJECTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["axis", "verdict", "objections"],
    "properties": {
        "axis": {"enum": AXES},
        "verdict": {"enum": AXIS_VERDICTS},
        "objections": {"type": "array", "items": OBJECTION_ITEM_SCHEMA},
    },
}

CONTRADICTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary", "resolution", "affected_objection_ids"],
    "properties": {
        "summary": {"type": "string"},
        "resolution": {"type": "string"},
        "affected_objection_ids": {"type": "array", "items": {"type": "string"}},
    },
}

AUFHEBEN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "verdict",
        "summary",
        "deduplicated_objections",
        "contradiction_resolutions",
        "nak_reason",
        "evidence",
    ],
    "properties": {
        "verdict": {"enum": ROUND_VERDICTS},
        "summary": {"type": "string"},
        "deduplicated_objections": {"type": "array", "items": OBJECTION_ITEM_SCHEMA},
        "contradiction_resolutions": {"type": "array", "items": CONTRADICTION_SCHEMA},
        "nak_reason": {"type": "string"},
        "evidence": {"type": "array", "items": EVIDENCE_SCHEMA},
    },
}


@dataclass
class Evidence:
    source_type: str
    citation: str
    consulted_terms: list[str] = field(default_factory=list)


@dataclass
class Objection:
    objection_id: str
    anchor_node_ids: list[str]
    axis: str
    type: str
    claim: str
    evidence: list[Evidence]
    impact: str
    requested_author_action: str
    status: str = "open"

    @property
    def dimension(self) -> str:
        return self.axis

    @property
    def has_objection(self) -> bool:
        return True


@dataclass
class AxisReview:
    axis: str
    verdict: str
    objections: list[Objection]
    reference_consultations: list[dict[str, Any]] = field(default_factory=list)
    attempts: int = 1
    validation_errors: list[str] = field(default_factory=list)


@dataclass
class Consolidation:
    verdict: str
    summary: str
    deduplicated_objections: list[Objection]
    contradiction_resolutions: list[dict[str, Any]]
    nak_reason: str
    evidence: list[Evidence]
    validation_errors: list[str] = field(default_factory=list)


@dataclass
class ReviewResult:
    status: str
    rounds: int
    final_view: dict[str, Any] | str
    resolved: list[str] = field(default_factory=list)
    unresolved: list[Objection] = field(default_factory=list)
    history: list[dict[str, Any]] = field(default_factory=list)
    escalation_reason: str = ""
    round_record_path: str = ""


def run_rfc_review(repo: str | Path, rfc_id_or_branch: str, rfc_path: str = "rfc.json") -> ReviewResult:
    """Run one group-internal RFC direction review round."""
    repo_path = Path(repo)
    branch = _rfc_branch(rfc_id_or_branch)
    rfc_id = branch.removeprefix("ai-org/rfc/")
    round_number = _next_round_number(repo_path, rfc_id)

    rfc_view, rfc_error = _read_rfc_from_git(repo_path, branch, rfc_path)
    approach, approach_error = _read_json_from_git(repo_path, branch, "technical-approach.json")
    if rfc_view is None or approach is None:
        reason = rfc_error or approach_error or "RFC review input is incomplete"
        record = _error_round_record(rfc_id, branch, round_number, reason)
        path = _write_round_record(repo_path, rfc_id, round_number, record)
        _write_marker(repo_path, branch, "rfc: nak", reason)
        return ReviewResult(
            "nak",
            0,
            "",
            unresolved=[_process_objection("input-contract", reason)],
            history=[record],
            escalation_reason=reason,
            round_record_path=str(path),
        )

    node_ids = _collect_node_ids(approach)
    if not node_ids:
        reason = "technical-approach.json must contain derivation-tree node ids for review anchors"
        record = _error_round_record(rfc_id, branch, round_number, reason)
        path = _write_round_record(repo_path, rfc_id, round_number, record)
        _write_marker(repo_path, branch, "rfc: nak", reason)
        return ReviewResult(
            "nak",
            0,
            "",
            unresolved=[_process_objection("input-contract", reason)],
            history=[record],
            escalation_reason=reason,
            round_record_path=str(path),
        )

    axis_reviews = [
        _review_one(dimension, rfc_view, approach, node_ids, repo_path)
        for dimension in DIMENSIONS
    ]
    raw_objections = [objection for axis in axis_reviews for objection in axis.objections]
    consolidation = _aufheben_consolidate(
        rfc_view,
        approach,
        node_ids,
        raw_objections,
        axis_reviews,
        repo_path,
    )
    final_objections = _dedupe_objections(
        consolidation.deduplicated_objections if consolidation.deduplicated_objections else raw_objections
    )
    consolidation.deduplicated_objections = final_objections
    status = _derive_verdict(consolidation, final_objections)
    open_blocking = [objection for objection in final_objections if objection.status == "open" and objection.type == "blocking"]
    resolved = [axis.axis for axis in axis_reviews if not any(o.axis == axis.axis and o.type == "blocking" for o in final_objections)]

    record = {
        "rfc_id": rfc_id,
        "branch": branch,
        "round": round_number,
        "inputs": {
            "rfc_path": rfc_path,
            "technical_approach_path": "technical-approach.json",
            "node_ids": sorted(node_ids),
        },
        "axis_reviews": [_axis_review_record(axis) for axis in axis_reviews],
        "objections": [_objection_record(objection) for objection in final_objections],
        "per_axis_verdicts": {axis.axis: axis.verdict for axis in axis_reviews},
        "consolidation": _consolidation_record(consolidation),
        "verdict": status,
        "git_result_marker": _marker_subject(status, round_number),
        "deferred": [
            "author-side v2 re-formation and resend loop",
            "Linon PR-gate review",
        ],
    }
    path = _write_round_record(repo_path, rfc_id, round_number, record)
    _write_marker(repo_path, branch, _marker_subject(status, round_number), consolidation.summary or consolidation.nak_reason)

    return ReviewResult(
        status,
        round_number,
        rfc_view,
        resolved=resolved,
        unresolved=open_blocking,
        history=[record],
        escalation_reason=consolidation.nak_reason if status == "nak" else "",
        round_record_path=str(path),
    )


def _review_one(
    dim: Dimension,
    rfc_view: Mapping[str, Any],
    approach: Mapping[str, Any],
    node_ids: set[str],
    repo: str | Path,
) -> AxisReview:
    consultations = _consult_reference(dim, rfc_view, approach)
    feedback = ""
    validation_errors: list[str] = []
    for attempt in range(1, 3):
        prompt = _review_prompt(dim, rfc_view, approach, node_ids, consultations, feedback)
        raw, error = _run_codex(repo, prompt, OBJECTION_SCHEMA, f"rfc-objection-{dim.key}.schema.json", f"{dim.key}.json")
        if error:
            validation_errors = [error]
            feedback = error
            continue
        axis_review, errors = _parse_axis_review(raw, dim.key, node_ids, consultations, attempt)
        if not errors:
            axis_review.reference_consultations = consultations
            axis_review.validation_errors = validation_errors
            return axis_review
        validation_errors = errors
        feedback = "Reviewer output failed validation. Correct these issues and return the full schema again: " + "; ".join(errors)

    return AxisReview(
        axis=dim.key,
        verdict="objections_pending",
        objections=[_process_objection(dim.key, "; ".join(validation_errors), node_ids=node_ids, consultations=consultations)],
        reference_consultations=consultations,
        attempts=2,
        validation_errors=validation_errors,
    )


def _aufheben_consolidate(
    rfc_view: Mapping[str, Any],
    approach: Mapping[str, Any],
    node_ids: set[str],
    objections: list[Objection],
    axis_reviews: list[AxisReview],
    repo: str | Path,
) -> Consolidation:
    prompt = (
        "You are Aufheben for an RFC direction-review round. Consolidate reviewer critique only. "
        "Do not author a revised RFC, replacement design, patch plan, or implementation. "
        "Aggregate objections across axes, deduplicate by the same anchor_node_ids plus the same claim, "
        "resolve contradictions between reviewers in the contradiction_resolutions field, and produce "
        "the round verdict. direction-ok means zero open blocking objections. needs_revision means open "
        "blocking objections exist and the author must reform and repost later. nak is reserved for an "
        "evidenced decision that the direction is fundamentally unsuitable.\n"
        f"RFC:\n{_json_for_prompt(_rfc_to_view(dict(rfc_view)))}\n"
        f"Technical Approach with node ids:\n{_json_for_prompt(approach)}\n"
        f"Valid node ids:\n{_json_for_prompt(sorted(node_ids))}\n"
        f"Per-axis verdicts:\n{_json_for_prompt({axis.axis: axis.verdict for axis in axis_reviews})}\n"
        f"Objections:\n{_json_for_prompt([_objection_record(objection) for objection in objections])}\n"
        "Return only JSON matching the provided schema."
    )
    raw, error = _run_codex(repo, prompt, AUFHEBEN_SCHEMA, "rfc-aufheben.schema.json", "aufheben.json")
    if error:
        deduped = _dedupe_objections(objections)
        return Consolidation(
            verdict="needs_revision" if any(o.type == "blocking" for o in deduped) else "direction-ok",
            summary=f"Aufheben failed validation and deterministic consolidation was used: {error}",
            deduplicated_objections=deduped,
            contradiction_resolutions=[],
            nak_reason="",
            evidence=[],
            validation_errors=[error],
        )
    consolidation, errors = _parse_consolidation(raw, node_ids)
    if errors:
        deduped = _dedupe_objections(objections)
        return Consolidation(
            verdict="needs_revision" if any(o.type == "blocking" for o in deduped) else "direction-ok",
            summary="Aufheben output failed validation and deterministic consolidation was used.",
            deduplicated_objections=deduped,
            contradiction_resolutions=[],
            nak_reason="",
            evidence=[],
            validation_errors=errors,
        )
    return consolidation


def _run_codex(
    repo: str | Path,
    prompt: str,
    schema: dict[str, Any],
    schema_filename: str,
    output_filename: str,
) -> tuple[str, str]:
    temp_dir = Path(tempfile.mkdtemp(prefix="ai-org-rfc-review-"))
    schema_file = temp_dir / schema_filename
    out_file = temp_dir / output_filename
    try:
        schema_file.write_text(json.dumps(schema, indent=2), encoding="utf-8")
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
            prompt,
        ]
        completed = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True)
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip() or "codex did not complete successfully"
            return "", detail
        if not out_file.exists():
            return "", "codex did not write an output file"
        return out_file.read_text(encoding="utf-8"), ""
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _parse_axis_review(
    raw: str,
    axis: str,
    node_ids: set[str],
    consultations: list[dict[str, Any]],
    attempt: int,
) -> tuple[AxisReview, list[str]]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        return AxisReview(axis, "objections_pending", [], attempts=attempt), [f"invalid JSON: {exc}"]
    errors = _lint_axis_review(parsed, axis, node_ids, consultations)
    if errors:
        return AxisReview(axis, "objections_pending", [], attempts=attempt), errors
    objections = [_parse_objection(item) for item in parsed["objections"]]
    return AxisReview(axis=axis, verdict=parsed["verdict"], objections=objections, attempts=attempt), []


def _parse_consolidation(raw: str, node_ids: set[str]) -> tuple[Consolidation, list[str]]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        return Consolidation("needs_revision", "", [], [], "", []), [f"invalid JSON: {exc}"]
    errors = _lint_consolidation(parsed, node_ids)
    if errors:
        return Consolidation("needs_revision", "", [], [], "", []), errors
    return (
        Consolidation(
            verdict=parsed["verdict"],
            summary=parsed["summary"],
            deduplicated_objections=[_parse_objection(item) for item in parsed["deduplicated_objections"]],
            contradiction_resolutions=list(parsed["contradiction_resolutions"]),
            nak_reason=parsed["nak_reason"],
            evidence=[_parse_evidence(item) for item in parsed["evidence"]],
        ),
        [],
    )


def _lint_axis_review(
    parsed: object,
    axis: str,
    node_ids: set[str],
    consultations: list[dict[str, Any]],
) -> list[str]:
    errors = _lint_object_schema(parsed, OBJECTION_SCHEMA, "review")
    if errors:
        return errors
    assert isinstance(parsed, dict)
    if parsed["axis"] != axis:
        errors.append(f"axis must be {axis}")
    objections = parsed["objections"]
    blocking = [item for item in objections if isinstance(item, dict) and item.get("type") == "blocking"]
    if blocking and parsed["verdict"] != "objections_pending":
        errors.append("blocking objections require objections_pending verdict")
    if not blocking and parsed["verdict"] == "objections_pending":
        errors.append("objections_pending requires at least one blocking objection")
    consulted_terms = {term for consultation in consultations for term in consultation.get("term", "").split("\0") if term}
    for index, item in enumerate(objections):
        errors.extend(_lint_objection(item, node_ids, axis, f"objections[{index}]", consulted_terms))
    return errors


def _lint_consolidation(parsed: object, node_ids: set[str]) -> list[str]:
    errors = _lint_object_schema(parsed, AUFHEBEN_SCHEMA, "aufheben")
    if errors:
        return errors
    assert isinstance(parsed, dict)
    for index, item in enumerate(parsed["deduplicated_objections"]):
        errors.extend(_lint_objection(item, node_ids, None, f"deduplicated_objections[{index}]", set()))
    if parsed["verdict"] == "nak":
        if not str(parsed["nak_reason"]).strip():
            errors.append("nak verdict requires nak_reason")
        if not parsed["evidence"]:
            errors.append("nak verdict requires evidence")
    return errors


def _lint_objection(
    item: object,
    node_ids: set[str],
    expected_axis: str | None,
    path: str,
    consulted_terms: set[str],
) -> list[str]:
    errors = _lint_object_schema(item, OBJECTION_ITEM_SCHEMA, path)
    if errors:
        return errors
    assert isinstance(item, dict)
    if expected_axis is not None and item["axis"] != expected_axis:
        errors.append(f"{path}.axis must be {expected_axis}")
    if not item["objection_id"].strip():
        errors.append(f"{path}.objection_id must be non-empty")
    anchors = item["anchor_node_ids"]
    if not anchors:
        errors.append(f"{path}.anchor_node_ids must contain at least one node id")
    dangling = [anchor for anchor in anchors if anchor not in node_ids]
    if dangling:
        errors.append(f"{path}.anchor_node_ids contains unknown node ids: {', '.join(dangling)}")
    if not item["claim"].strip():
        errors.append(f"{path}.claim must be non-empty")
    evidence = item["evidence"]
    meaningful_evidence = any(e["citation"].strip() or e["consulted_terms"] for e in evidence)
    if consulted_terms and not any(set(e["consulted_terms"]) & consulted_terms for e in evidence):
        errors.append(f"{path}.evidence must record at least one Reference term consulted")
    if item["type"] == "blocking" and not item["impact"].strip():
        errors.append(f"{path}.impact must be non-empty for blocking objections")
    if item["type"] not in {"nonblocking_suggestion", "style_defer"} and (
        not item["impact"].strip() or not meaningful_evidence
    ):
        errors.append(f"{path} must be nonblocking_suggestion or style_defer without evidence and impact")
    return errors


def _lint_object_schema(value: object, schema: Mapping[str, Any], path: str) -> list[str]:
    if not isinstance(value, dict):
        return [f"{path} must be an object"]
    errors: list[str] = []
    expected = set(schema["properties"])
    actual = set(value)
    if actual != expected:
        errors.append(f"{path} keys must be exactly {sorted(expected)}")
        return errors
    for key, child_schema in schema["properties"].items():
        child = value[key]
        child_path = f"{path}.{key}"
        if "enum" in child_schema:
            if child not in child_schema["enum"]:
                errors.append(f"{child_path} must be one of {child_schema['enum']}")
        elif child_schema.get("type") == "string":
            if not isinstance(child, str):
                errors.append(f"{child_path} must be a string")
        elif child_schema.get("type") == "array":
            if not isinstance(child, list):
                errors.append(f"{child_path} must be an array")
            else:
                item_schema = child_schema["items"]
                for index, item in enumerate(child):
                    if item_schema.get("type") == "string":
                        if not isinstance(item, str):
                            errors.append(f"{child_path}[{index}] must be a string")
                    elif item_schema.get("type") == "object":
                        errors.extend(_lint_object_schema(item, item_schema, f"{child_path}[{index}]"))
                    elif "enum" in item_schema and item not in item_schema["enum"]:
                        errors.append(f"{child_path}[{index}] must be one of {item_schema['enum']}")
        elif child_schema.get("type") == "object":
            errors.extend(_lint_object_schema(child, child_schema, child_path))
    return errors


def _parse_objection(item: Mapping[str, Any]) -> Objection:
    return Objection(
        objection_id=item["objection_id"],
        anchor_node_ids=list(item["anchor_node_ids"]),
        axis=item["axis"],
        type=item["type"],
        claim=item["claim"],
        evidence=[_parse_evidence(evidence) for evidence in item["evidence"]],
        impact=item["impact"],
        requested_author_action=item["requested_author_action"],
        status=item["status"],
    )


def _parse_evidence(item: Mapping[str, Any]) -> Evidence:
    return Evidence(
        source_type=item["source_type"],
        citation=item["citation"],
        consulted_terms=list(item["consulted_terms"]),
    )


def _consult_reference(
    dim: Dimension,
    rfc_view: Mapping[str, Any],
    approach: Mapping[str, Any],
) -> list[dict[str, Any]]:
    context = _reference_context(rfc_view)
    terms = _reference_terms(dim, rfc_view, approach)
    consultations: list[dict[str, Any]] = []
    for term in terms:
        try:
            hit = reference.lookup(term, context, kind="design")
        except Exception as exc:  # noqa: BLE001 - review records lookup failures as evidence context.
            consultations.append({"term": term, "kind": "design", "error": str(exc), "hit": False})
            continue
        consultations.append(
            {
                "term": term,
                "kind": "design",
                "hit": bool(hit and hit.get("candidates")),
                "result": _compact_reference_hit(hit),
            }
        )
    return consultations


def _reference_terms(dim: Dimension, rfc_view: Mapping[str, Any], approach: Mapping[str, Any]) -> list[str]:
    candidates = [
        str(rfc_view.get("working_title") or ""),
        str(rfc_view.get("affected_area_platform") or ""),
        str(rfc_view.get("proposal_hint") or ""),
        _approach_decision_label(approach),
        f"{dim.key} review {rfc_view.get('affected_area_platform') or rfc_view.get('working_title') or 'RFC'}",
    ]
    cleaned: list[str] = []
    for candidate in candidates:
        term = " ".join(candidate.split()).strip()
        if term and term not in cleaned:
            cleaned.append(term)
    return cleaned[:4]


def _reference_context(rfc_view: Mapping[str, Any]) -> dict[str, Any]:
    tech_stack = rfc_view.get("tech_stack")
    context: dict[str, Any] = {}
    if isinstance(tech_stack, Mapping):
        for key in ("language", "framework", "engine", "platform"):
            value = str(tech_stack.get(key) or "").strip()
            if value:
                context[key] = value
    return context


def _compact_reference_hit(hit: Mapping[str, Any] | None) -> dict[str, Any]:
    if not hit:
        return {}
    candidates = hit.get("candidates")
    compact: list[dict[str, str]] = []
    if isinstance(candidates, list):
        for candidate in candidates[:3]:
            if isinstance(candidate, Mapping):
                compact.append(
                    {
                        "summary": str(candidate.get("summary") or "")[:300],
                        "source": str(candidate.get("source") or candidate.get("repo") or "")[:200],
                    }
                )
    return {"term": hit.get("term", ""), "candidates": compact}


def _review_prompt(
    dim: Dimension,
    rfc_view: Mapping[str, Any],
    approach: Mapping[str, Any],
    node_ids: set[str],
    consultations: list[dict[str, Any]],
    feedback: str,
) -> str:
    return (
        f"You are reviewing one RFC direction axis: {dim.key} - {dim.blurb}.\n"
        "This is a group-internal direction conversation before implementation, not the PR gate. "
        "Critique the submitted RFC and Technical Approach. Do not author replacement designs, "
        "do not re-litigate receive's request interpretation, and do not style-police patch details. "
        "Every objection must anchor to existing technical-approach node ids, mirroring LKML inline "
        "quote discipline. Blocking objections need concrete impact. Preferences without evidence and "
        "impact must be nonblocking_suggestion or style_defer.\n"
        f"Valid node ids:\n{_json_for_prompt(sorted(node_ids))}\n"
        f"RFC:\n{_json_for_prompt(_rfc_to_view(dict(rfc_view)))}\n"
        f"Technical Approach:\n{_json_for_prompt(approach)}\n"
        f"Reference consultations you must account for in evidence.consulted_terms:\n{_json_for_prompt(consultations)}\n"
        f"{'Feedback from prior invalid output: ' + feedback + chr(10) if feedback else ''}"
        "Return only JSON matching the provided schema."
    )


def _derive_verdict(consolidation: Consolidation, objections: list[Objection]) -> str:
    if consolidation.verdict == "nak" and consolidation.nak_reason.strip() and consolidation.evidence:
        return "nak"
    if any(objection.status == "open" and objection.type == "blocking" for objection in objections):
        return "needs_revision"
    return "direction-ok"


def _dedupe_objections(objections: list[Objection]) -> list[Objection]:
    deduped: list[Objection] = []
    seen: set[tuple[tuple[str, ...], str]] = set()
    for objection in objections:
        key = (tuple(sorted(objection.anchor_node_ids)), " ".join(objection.claim.lower().split()))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(objection)
    return deduped


def _collect_node_ids(value: object) -> set[str]:
    node_ids: set[str] = set()
    if isinstance(value, Mapping):
        node_id = value.get("id")
        if isinstance(node_id, str) and node_id:
            node_ids.add(node_id)
        for child in value.values():
            node_ids.update(_collect_node_ids(child))
    elif isinstance(value, list):
        for child in value:
            node_ids.update(_collect_node_ids(child))
    return node_ids


def _approach_decision_label(approach: Mapping[str, Any]) -> str:
    decision = _find_key(approach, "decision")
    if isinstance(decision, Mapping):
        for key in ("selected_candidate_id", "chosen", "id"):
            value = decision.get(key)
            if isinstance(value, str) and value:
                return value
    return ""


def _find_key(value: object, key: str) -> object | None:
    if isinstance(value, Mapping):
        if key in value:
            return value[key]
        for child in value.values():
            found = _find_key(child, key)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_key(child, key)
            if found is not None:
                return found
    return None


def _read_rfc_from_git(repo: Path, branch: str, path: str) -> tuple[dict[str, Any] | None, str]:
    raw = git_wrapper.show_file(repo, branch, path)
    if raw is None:
        return None, f"could not read {branch}:{path}"
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        return None, f"{branch}:{path} is invalid JSON: {exc}"
    if not _is_rfc_view(parsed):
        return None, f"{branch}:{path} must contain exactly the RFC field registry fields"
    return _rfc_to_view(parsed), ""


def _read_json_from_git(repo: Path, branch: str, path: str) -> tuple[dict[str, Any] | None, str]:
    raw = git_wrapper.show_file(repo, branch, path)
    if raw is None:
        return None, f"could not read {branch}:{path}; RFC review requires technical-approach.json"
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        return None, f"{branch}:{path} is invalid JSON: {exc}"
    if not isinstance(parsed, dict):
        return None, f"{branch}:{path} must contain a JSON object"
    return parsed, ""


def _is_rfc_view(value: object) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == set(RFC_VIEW_FIELDS)
        and all(isinstance(value[field], str) for field in STRING_FIELDS)
        and all(
            isinstance(value[field], list) and all(isinstance(item, str) for item in value[field])
            for field in STRING_ARRAY_FIELDS
        )
        and validate_tech_stack(value.get("tech_stack"))
    )


def _rfc_to_view(rfc_view: dict[str, Any]) -> dict[str, Any]:
    return {field: rfc_view[field] for field in RFC_VIEW_FIELDS}


def _rfc_branch(rfc_id_or_branch: str) -> str:
    if rfc_id_or_branch.startswith("refs/heads/"):
        return rfc_id_or_branch.removeprefix("refs/heads/")
    if rfc_id_or_branch.startswith("ai-org/rfc/"):
        return rfc_id_or_branch
    return f"ai-org/rfc/{rfc_id_or_branch}"


def _next_round_number(repo: Path, rfc_id: str) -> int:
    directory = repo / ".ai-org" / "review" / rfc_id
    existing: list[int] = []
    for path in directory.glob("round-*.json"):
        try:
            existing.append(int(path.stem.removeprefix("round-")))
        except ValueError:
            continue
    return max(existing, default=0) + 1


def _write_round_record(repo: Path, rfc_id: str, round_number: int, record: Mapping[str, Any]) -> Path:
    directory = repo / ".ai-org" / "review" / rfc_id
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"round-{round_number}.json"
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _write_marker(repo: Path, branch: str, subject: str, body: str = "") -> None:
    git_wrapper.commit_empty(repo, branch, subject, body=body)


def _marker_subject(status: str, round_number: int) -> str:
    if status == "direction-ok":
        return "rfc: direction-ok"
    if status == "needs_revision":
        return f"rfc: needs-revision round {round_number}"
    return "rfc: nak"


def _error_round_record(rfc_id: str, branch: str, round_number: int, reason: str) -> dict[str, Any]:
    return {
        "rfc_id": rfc_id,
        "branch": branch,
        "round": round_number,
        "inputs": {"rfc_path": "rfc.json", "technical_approach_path": "technical-approach.json", "node_ids": []},
        "axis_reviews": [],
        "objections": [_objection_record(_process_objection("input-contract", reason))],
        "per_axis_verdicts": {},
        "consolidation": {
            "verdict": "nak",
            "summary": reason,
            "deduplicated_objections": [],
            "contradiction_resolutions": [],
            "nak_reason": reason,
            "evidence": [],
            "validation_errors": [],
        },
        "verdict": "nak",
        "git_result_marker": "rfc: nak",
        "deferred": [
            "author-side v2 re-formation and resend loop",
            "Linon PR-gate review",
        ],
    }


def _process_objection(
    axis: str,
    reason: str,
    *,
    node_ids: set[str] | None = None,
    consultations: list[dict[str, Any]] | None = None,
) -> Objection:
    anchor = "problem"
    if node_ids:
        anchor = "problem" if "problem" in node_ids else sorted(node_ids)[0]
    terms = [str(item.get("term")) for item in consultations or [] if item.get("term")]
    return Objection(
        objection_id=f"{axis}:review-process",
        anchor_node_ids=[anchor],
        axis=axis if axis in AXES else "need",
        type="blocking",
        claim=reason or "Review process failed closed.",
        evidence=[Evidence("repo_fact", reason or "review process failed", terms)],
        impact="The direction review cannot be trusted until this review record is corrected.",
        requested_author_action="re_explain",
    )


def _axis_review_record(axis: AxisReview) -> dict[str, Any]:
    return {
        "axis": axis.axis,
        "verdict": axis.verdict,
        "objections": [_objection_record(objection) for objection in axis.objections],
        "reference_consultations": axis.reference_consultations,
        "attempts": axis.attempts,
        "validation_errors": axis.validation_errors,
    }


def _consolidation_record(consolidation: Consolidation) -> dict[str, Any]:
    return {
        "verdict": consolidation.verdict,
        "summary": consolidation.summary,
        "deduplicated_objections": [_objection_record(objection) for objection in consolidation.deduplicated_objections],
        "contradiction_resolutions": consolidation.contradiction_resolutions,
        "nak_reason": consolidation.nak_reason,
        "evidence": [asdict(evidence) for evidence in consolidation.evidence],
        "validation_errors": consolidation.validation_errors,
    }


def _objection_record(objection: Objection) -> dict[str, Any]:
    return {
        "objection_id": objection.objection_id,
        "anchor_node_ids": list(objection.anchor_node_ids),
        "axis": objection.axis,
        "type": objection.type,
        "claim": objection.claim,
        "evidence": [asdict(evidence) for evidence in objection.evidence],
        "impact": objection.impact,
        "requested_author_action": objection.requested_author_action,
        "status": objection.status,
    }


def _json_for_prompt(value: object) -> str:
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True)
