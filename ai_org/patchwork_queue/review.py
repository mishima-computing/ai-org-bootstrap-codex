"""patch series direction review: group-internal critique before implementation.

This module models the patch series review round as the project's LKML-thread analogue:
the author posts a patch series plus Technical Approach, reviewers discuss direction
inside the group, and the result is a consensus milestone before patch work. It
is not the binding mainline gate. The fierce independent maintainer review lives
later at the PR/mainline boundary.

The kernel basis is intentionally narrow and concrete:
  - LKML inline quoting maps to objections anchored to existing derivation-tree
    node ids from technical-approach-plan.json.
  - Reviewed-by/Acked-by vocabulary maps to per-axis Direction-reviewed-by when
    serious concerns are raised and no known serious direction issue remains.
  - NAK is reserved for an evidenced decision record that the direction is
    fundamentally unsuitable, not for accumulated nits.

Review critiques; it never authors replacement designs. The author side is
responsible for re-forming and reposting v2 from the review record. Aufheben is
therefore demoted from "write a revised patch series" to consolidation: aggregate
objections, deduplicate by anchor plus claim, resolve reviewer contradictions,
and produce the round verdict.

The direction-review round record is committed on the patch series branch under
patch-series-review-rounds/. The result marker commit carries the full record:
patch_series: direction-ok, patch_series: needs-revision round N, or
patch_series: nak. The legacy .ai-org/review store is read only for migration.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
import importlib
import json
import os
from pathlib import Path
from typing import Any, Mapping

from ai_org.schema_lifecycle import generated_schema_attr
import ai_org.log as org_log
from ai_org import (
    contributor_handoff,
    engineering_precedent_store,
    git_wrapper,
    mailing_list,
    patch_series_bodies,
    review_bodies,
)
import ai_org.patchwork_queue.codex_exec as codex_exec
import ai_org.patchwork_queue.requester_assumptions as requester_assumptions
from ai_org.patchwork_queue.field_registry import (
    WORK_ORDER_VIEW_FIELDS,
    STRING_ARRAY_FIELDS,
    STRING_FIELDS,
    deliverable_is_user_facing,
    validate_tech_stack,
    validate_user_experience_requirements,
)


@dataclass(frozen=True)
class Dimension:
    key: str
    blurb: str


DIMENSIONS: list[Dimension] = [
    Dimension("need", "whether the change is wanted and the problem remains worth solving"),
    Dimension("approach", "whether the selected technical approach and interfaces are right"),
    Dimension("compat", "whether behavior, data, API, config, or user expectations regress"),
    Dimension("scope", "whether the proposed slice, prerequisites, and split are right"),
    Dimension("maintenance", "whether the org itself can own and evolve the artifact across its own cycles"),
]

AXES = [dimension.key for dimension in DIMENSIONS]
OBJECTION_TYPES = ["blocking", "clarification", "nonblocking_suggestion", "style_defer"]
AUTHOR_ACTIONS = ["re_explain", "provide_evidence", "revise_subtree", "split_scope", "withdraw"]
RESOLUTION_AUTHORITIES = ["author", "requester"]
AXIS_VERDICTS = ["Direction-reviewed-by", "objections_pending"]
ROUND_VERDICTS = ["direction-ok", "needs_revision", "nak"]
EVIDENCE_TYPES = ["reference", "prior_decision", "repo_fact", "tree_node", "patch_series_field"]
DEFAULT_MAX_REVIEW_ROUNDS = 5
REVIEW_RECORD_DIR = "patch-series-review-rounds"

# Memento: Log V1 wires receive/pull/Codex boundaries first. Deeper
# review.round.*, review.axis.*, review.aufheben.*, and review.verdict events
# belong here when review history projections are added. Resume and pull logic
# must never read .ai-org/log/runs as state; logs are observability only.


def build_evidence_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["source_type", "citation", "consulted_terms"],
        "properties": {
            "source_type": {"enum": EVIDENCE_TYPES},
            "citation": {"type": "string"},
            "consulted_terms": {"type": "array", "items": {"type": "string"}},
        },
    }

def build_objection_item_schema() -> dict[str, Any]:
    return {
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
            "resolution_authority",
            "status",
            "reviewer_confidence",
            "reviewed_scope",
        ],
        "properties": {
            "objection_id": {"type": "string"},
            "anchor_node_ids": {"type": "array", "items": {"type": "string"}},
            "axis": {"enum": AXES},
            "type": {"enum": OBJECTION_TYPES},
            "claim": {"type": "string"},
            "evidence": {"type": "array", "items": build_evidence_schema()},
            "impact": {"type": "string"},
            "requested_author_action": {"enum": AUTHOR_ACTIONS},
            "resolution_authority": {"enum": RESOLUTION_AUTHORITIES},
            # Brief 27 (Rust: subteam decides): per-objection resolution outcomes
            # for author deferral proposals. Data composition only — the charter
            # text is untouched and the freeze test stays green.
            "status": {"enum": ["open", "deferral_accepted", "deferral_rejected"]},
            # Brief 33 (asymmetric_review_canon, ratified 2026-07-05): the
            # reviewer self-declares confidence and reviewed scope per objection
            # (kernel reviewed-aspects norm; NeurIPS confidence scores). Both
            # are INFORMATIONAL for the author and the consolidation — never a
            # mechanical gate by themselves. Memento: the reviewer is a filter,
            # never a ceiling.
            "reviewer_confidence": {"enum": ["high", "medium", "low"]},
            "reviewed_scope": {
                "type": "string",
                "description": "What the reviewer actually examined to raise this objection (short, factual).",
            },
        },
    }

def build_objection_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["axis", "verdict", "objections"],
        "properties": {
            "axis": {"enum": AXES},
            "verdict": {"enum": AXIS_VERDICTS},
            "objections": {"type": "array", "items": build_objection_item_schema()},
        },
    }

def build_contradiction_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["summary", "resolution", "affected_objection_ids"],
        "properties": {
            "summary": {"type": "string"},
            "resolution": {"type": "string"},
            "affected_objection_ids": {"type": "array", "items": {"type": "string"}},
        },
    }

def build_aufheben_schema() -> dict[str, Any]:
    return {
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
            "deduplicated_objections": {"type": "array", "items": build_objection_item_schema()},
            "contradiction_resolutions": {"type": "array", "items": build_contradiction_schema()},
            "nak_reason": {"type": "string"},
            "evidence": {"type": "array", "items": build_evidence_schema()},
        },
    }


_SCHEMA_BUILDERS = {
    "AUFHEBEN_SCHEMA": build_aufheben_schema,
    "CONTRADICTION_SCHEMA": build_contradiction_schema,
    "EVIDENCE_SCHEMA": build_evidence_schema,
    "OBJECTION_ITEM_SCHEMA": build_objection_item_schema,
    "OBJECTION_SCHEMA": build_objection_schema,
}
_CODEX_OUTPUT_SCHEMA_BUILDERS = dict(_SCHEMA_BUILDERS)


def __getattr__(name: str) -> Any:
    return generated_schema_attr(name, _SCHEMA_BUILDERS)

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
    resolution_authority: str = "author"
    status: str = "open"
    # Brief 33: reviewer self-declaration (informational, never a gate). Empty
    # defaults keep pre-brief-33 committed round records parseable.
    reviewer_confidence: str = ""
    reviewed_scope: str = ""

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
    serial: str = ""


def run_patch_series_review(
    repo: str | Path,
    patch_series_id_or_branch: str,
    patch_series_path: str = patch_series_bodies.LEGACY_COVER_PATH,
    *,
    ctx: org_log.RunContext | None = None,
) -> ReviewResult:
    """Run one group-internal patch series direction review round."""
    repo_path = Path(repo)
    branch = _patch_series_branch(patch_series_id_or_branch)
    patch_series_id = branch.removeprefix("ai-org/patch-series/")
    log_ctx = ctx or org_log.RunContext(repo=repo_path, patch_series_id=patch_series_id, stage="patch_series.review")
    with org_log.span("patch_series.review.round", log_ctx.child(patch_series_id=patch_series_id)) as round_ctx:
        return _run_patch_series_review(repo_path, branch, patch_series_id, patch_series_path, round_ctx)


def _run_patch_series_review(
    repo_path: Path,
    branch: str,
    patch_series_id: str,
    patch_series_path: str,
    ctx: org_log.RunContext,
) -> ReviewResult:
    round_number = _next_round_number(repo_path, branch, patch_series_id)
    reviewed_commit = git_wrapper.head_sha(repo_path, branch) or ""
    base_commit = _review_base_commit(repo_path, branch)
    org_log.emit(
        "patch_series.review.round.selected",
        {"patch_series_id": patch_series_id, "branch": branch, "round": round_number},
        ctx=ctx.child(attempt=round_number),
    )
    # Brief 32: reaching the cap no longer short-circuits judgment. The
    # terminal round below is a REAL hearing of the current tree; only its own
    # verdict decides (see _bounded_rounds_terminal_context for the run-4
    # zero-second-nak Memento and the boundedness arithmetic).
    terminal_backstop = _bounded_rounds_terminal_context(repo_path, patch_series_id, branch)

    root_snapshot = patch_series_bodies.classify_root_generation(
        repo_path, reviewed_commit or branch
    )
    if not root_snapshot.lifecycle_ready:
        return ReviewResult(
            root_snapshot.disposition,
            0,
            {
                "branch": branch,
                "frozen_oid": root_snapshot.frozen_oid,
                "detail": root_snapshot.detail,
                "root_generation": root_snapshot.identity(),
                **root_snapshot.identity(),
            },
            escalation_reason=root_snapshot.diagnostic,
        )
    try:
        root_cover = root_snapshot.cover_letter()
        patch_series_view = (
            _patch_series_to_view(root_cover)
            if _is_patch_series_view(root_cover)
            else None
        )
        approach = root_snapshot.technical_approach()
    except (ValueError, TypeError) as exc:
        patch_series_view = approach = None
        patch_series_error = approach_error = str(exc)
    else:
        patch_series_error = (
            "root generation cover must contain exactly the patch series field registry fields"
            if patch_series_view is None
            else ""
        )
        approach_error = ""
    if patch_series_view is None or approach is None:
        reason = patch_series_error or approach_error or "patch series review input is incomplete"
        record = _error_round_record(patch_series_id, branch, round_number, reason)
        _stamp_round_identity(record, reviewed_commit, base_commit)
        path = round_record_path(round_number)
        marker = _write_marker(repo_path, branch, "patch_series: nak", reason, files={path: record})
        record["git_result_commit"] = marker["commit"]
        _post_round_mail_nonfatal(
            repo_path,
            series_slug=patch_series_id,
            branch=branch,
            round_number=round_number,
            status="nak",
            record_path=path,
            record_commit=marker["commit"],
            record=record,
            ctx=ctx,
        )
        org_log.emit(
            "patch_series.review.input_failed",
            {"status": "nak", "round": round_number, "reason": reason},
            ctx=ctx.child(attempt=round_number),
            severity="error",
        )
        return ReviewResult(
            "nak",
            0,
            "",
            unresolved=[_process_objection("input-contract", reason)],
            history=[record],
            escalation_reason=reason,
            round_record_path=path,
        )

    node_ids = _collect_node_ids(approach)
    if not node_ids:
        reason = "technical-approach-plan.json must contain derivation-tree node ids for review anchors"
        record = _error_round_record(patch_series_id, branch, round_number, reason)
        _stamp_round_identity(record, reviewed_commit, base_commit)
        path = round_record_path(round_number)
        marker = _write_marker(repo_path, branch, "patch_series: nak", reason, files={path: record})
        record["git_result_commit"] = marker["commit"]
        _post_round_mail_nonfatal(
            repo_path,
            series_slug=patch_series_id,
            branch=branch,
            round_number=round_number,
            status="nak",
            record_path=path,
            record_commit=marker["commit"],
            record=record,
            ctx=ctx,
        )
        org_log.emit(
            "patch_series.review.input_failed",
            {"status": "nak", "round": round_number, "reason": reason},
            ctx=ctx.child(attempt=round_number),
            severity="error",
        )
        return ReviewResult(
            "nak",
            0,
            "",
            unresolved=[_process_objection("input-contract", reason)],
            history=[record],
            escalation_reason=reason,
            round_record_path=path,
        )

    # The committed branch records are the binding paper.  Prior objections and
    # author replies are thread memory, not reviewer input: every round prints the
    # current body through the same step-subtree shards.  This blank reading makes
    # resolution a property of the revised text instead of a verdict on a reply.
    continuity = _round_continuity(repo_path, branch, patch_series_id, approach, round_number)
    prior_unresolved = _prior_unresolved_objections(repo_path, branch, patch_series_id)

    axis_reviews = []
    for dimension in DIMENSIONS:
        with org_log.span(
            "patch_series.review.axis",
            ctx.child(stage=f"patch_series.review.{dimension.key}", attempt=round_number),
        ) as axis_ctx:
            axis = _review_dimension_sharded(
                dimension, patch_series_view, approach, node_ids, repo_path, ctx=axis_ctx
            )
            axis_reviews.append(axis)
            org_log.emit(
                "patch_series.review.axis.completed",
                {
                    "axis": dimension.key,
                    "verdict": axis.verdict,
                    "objections": len(axis.objections),
                    "attempts": axis.attempts,
                    "validation_errors": axis.validation_errors,
                },
                ctx=axis_ctx,
            )
    raw_objections = [objection for axis in axis_reviews for objection in axis.objections]
    with org_log.span("patch_series.review.aufheben", ctx.child(stage="patch_series.review.aufheben", attempt=round_number)) as aufheben_ctx:
        consolidation = _aufheben_consolidate(
            patch_series_view,
            approach,
            node_ids,
            raw_objections,
            axis_reviews,
            repo_path,
            ctx=aufheben_ctx,
        )
    final_objections = _dedupe_objections(
        consolidation.deduplicated_objections if consolidation.deduplicated_objections else raw_objections
    )
    # Objection lifecycle bookkeeping is deterministic; the blank reviewer's
    # judgment is already in final_objections. A deferral transition holds only
    # when that fresh reading independently returns the matching id and status
    # and the committed author ledger contains the proposal. The proposal itself
    # never enters reviewer paper (no proposal -> stays open).
    deferral_proposals_by_id = continuity.get("deferral_proposals_by_id", {}) if continuity is not None else {}
    accepted_deferrals: list[Objection] = []
    rejected_deferral_ids: list[str] = []
    normalized_objections: list[Objection] = []
    for objection in final_objections:
        if objection.status == "deferral_accepted":
            if objection.objection_id in deferral_proposals_by_id:
                accepted_deferrals.append(objection)
                normalized_objections.append(objection)
            else:
                normalized_objections.append(replace(objection, status="open"))
        elif objection.status == "deferral_rejected":
            rejected_deferral_ids.append(objection.objection_id)
            normalized_objections.append(replace(objection, status="open"))
        else:
            normalized_objections.append(objection)
    final_objections = normalized_objections
    # Cross-round continuity is bookkeeping performed only after the blank round
    # has produced and consolidated its own objections.  Matching deliberately
    # ignores claim wording: a same-axis objection whose anchors overlap is a
    # conservative fresh counterpart.  No counterpart means the old concern was
    # resolved by construction because a blank reading no longer raised it.
    cross_round_objection_mapping = _cross_round_objection_mapping(
        prior_unresolved, final_objections
    )
    previous_record = _latest_prior_round_record(repo_path, branch, patch_series_id)
    resolved_objection_records = [
        dict(entry)
        for entry in (previous_record or {}).get("resolved_objections", [])
        if isinstance(entry, Mapping)
    ]
    requester_blocking = requester_assumptions.requester_authority_objections(
        [
            _objection_record(objection)
            for objection in final_objections
            if objection.status == "open" and objection.type == "blocking"
        ]
    )
    patch_series_view, _requester_assumption_entries, requester_assumptions_changed = (
        requester_assumptions.record_requester_authority_assumptions(
            patch_series_view,
            approach,
            requester_blocking,
        )
    )
    recorded_assumption_ids = requester_assumptions.recorded_assumption_objection_ids(patch_series_view)
    final_objections = [_with_assumption_status(objection, recorded_assumption_ids) for objection in final_objections]
    consolidation.deduplicated_objections = final_objections
    consolidation_raw_verdict = consolidation.verdict
    status = _derive_verdict(consolidation, final_objections)
    requester_assumption_notes = requester_assumptions.requester_assumption_notes(
        [_objection_record(objection) for objection in final_objections],
        patch_series_view,
    )
    open_blocking = [objection for objection in final_objections if objection.status == "open" and objection.type == "blocking"]
    resolved = [
        axis.axis
        for axis in axis_reviews
        if not any(o.axis == axis.axis and o.type == "blocking" and o.status == "open" for o in final_objections)
    ]

    # Brief 32: at the cap, needs_revision is no longer on the menu - the
    # series must not loop again - but the nak carries THIS round's actual
    # judgment (the axis reviews and objections assembled above), never the
    # previous round's stale list. direction-ok passes through untouched: the
    # respondent was heard, and a terminal round that satisfies the reviewer
    # converges. A genuine consolidation nak also passes through with its own
    # reason - the 03a7319 genuine-nak escape stays closed.
    bounded_backstop: dict[str, Any] | None = None
    if terminal_backstop is not None and status == "needs_revision":
        status = "nak"
        backstop_reason = (
            f"patch series direction review reached the bounded-rounds backstop of "
            f"{terminal_backstop['max_rounds']} rounds with blocking objections still open after a full "
            "terminal-round hearing of the current tree. Kernel practice has no fixed cap; AI Org diverges "
            "here to prevent runaway patch series phase loops."
        )
        consolidation.verdict = "nak"
        consolidation.nak_reason = backstop_reason
        bounded_backstop = {
            "max_rounds": terminal_backstop["max_rounds"],
            "divergence": "Kernel practice has no fixed cap; AI Org adds this runaway backstop.",
            "history": terminal_backstop["history"],
            "terminal_round_reviewed": True,
        }

    marker_subject = _marker_subject(status, round_number, branch=branch, repo=repo_path)
    serial = _serial_from_marker_subject(marker_subject)

    record = {
        "patch_series_id": patch_series_id,
        "branch": branch,
        "round": round_number,
        "reviewed_commit": reviewed_commit,
        "base_commit": base_commit,
        "inputs": {
            "patch_series_path": patch_series_path,
            "technical_approach_path": root_snapshot.source_path,
            "root_generation": root_snapshot.identity(),
            "node_ids": sorted(node_ids),
        },
        "axis_reviews": [_axis_review_record(axis) for axis in axis_reviews],
        "objections": [
            _objection_record(objection)
            for objection in final_objections
        ],
        "resolved_objections": resolved_objection_records,
        "cross_round_objection_mapping": cross_round_objection_mapping,
        "deferral_rejected_ids": rejected_deferral_ids,
        "per_axis_verdicts": {axis.axis: axis.verdict for axis in axis_reviews},
        "consolidation": _consolidation_record(consolidation),
        "consolidation_raw_verdict": consolidation_raw_verdict,
        "requester_assumption_notes": requester_assumption_notes,
        "verdict": status,
        "git_result_marker": marker_subject,
        "serial": serial,
        "deferred": [
            "author-side v2 re-formation and resend loop",
            "Linon PR-gate review",
        ],
    }
    if bounded_backstop is not None:
        record["bounded_rounds_backstop"] = bounded_backstop
    path = round_record_path(round_number)
    files = {path: record}
    if accepted_deferrals and status != "nak":
        gate_module = importlib.import_module("ai_org.patchwork_queue.patch_series_gate")
        files[gate_module.PATCH_MUST_ANSWER_QUESTIONS_PATH] = _questions_the_patch_must_answer(
            repo_path, branch, accepted_deferrals, deferral_proposals_by_id, round_number
        )
    if requester_assumptions_changed and status != "nak":
        cover_path = root_snapshot.cover_path or patch_series_path
        files[cover_path] = patch_series_view
    marker = _write_marker(
        repo_path,
        branch,
        marker_subject,
        _marker_body(consolidation.summary or consolidation.nak_reason, status, axis_reviews),
        files=files,
    )
    if status == "direction-ok":
        serial = git_wrapper.ensure_serial(repo_path, marker["commit"])
        record["serial"] = serial
    record["git_result_commit"] = marker["commit"]
    _post_round_mail_nonfatal(
        repo_path,
        series_slug=patch_series_id,
        branch=branch,
        round_number=round_number,
        status=status,
        record_path=path,
        record_commit=marker["commit"],
        record=record,
        ctx=ctx,
    )
    org_log.emit(
        "patch_series.review.verdict",
        {
            "status": status,
            "round": round_number,
            "open_blocking": len(open_blocking),
            "objections": len(final_objections),
            "git_result_commit": marker["commit"],
            "serial": serial,
            "round_record_path": path,
            **({"bounded_rounds_backstop": True} if bounded_backstop is not None else {}),
        },
        ctx=ctx.child(attempt=round_number),
        severity="warning" if status != "direction-ok" else "info",
    )

    return ReviewResult(
        status,
        round_number,
        patch_series_view,
        resolved=resolved,
        unresolved=open_blocking,
        history=[record],
        escalation_reason=consolidation.nak_reason if status == "nak" else "",
        round_record_path=path,
        serial=serial,
    )


# ==========================================================================================
# COMMITTED CONTINUITY HELPERS. These projections remain useful to author-side
# revision tooling and compatibility bookkeeping, but they are never reviewer
# paper. Memento: paper=binding branch records, list=thread memory; every reviewer
# is blank by design and receives only fresh current-body shards. Cross-round
# resolution is computed after consolidation, by construction, never from replies.
# ==========================================================================================


def _load_revision_delta(repo: Path, patch_series_id: str, branch: str, round_number: int) -> dict[str, Any] | None:
    """Load the reform-committed revision delta ledger for a review round, if any."""
    if round_number <= 0:
        return None
    parsed = review_bodies.read_record(
        repo, branch, revision_delta_record_path(round_number), review_bodies.REVISION_DELTA_RECIPE
    )
    if parsed is None:
        path = repo / ".ai-org" / "review" / patch_series_id / f"round-{round_number:04d}-revision-delta.json"
        if path.exists():
            try:
                parsed = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return None
    return parsed if isinstance(parsed, dict) else None


def _reference_edges(tree: object) -> list[tuple[str, str]]:
    """Id-typed reference edges of the approach tree (deterministic, total).

    The id-typed field contract is owned by receive (_ID_TYPED_REFERENCE_FIELDS);
    imported at call time to stay in sync without a static import edge. Prose
    fields (draws_on, traces_to, citations) are never edges.
    """
    receive_module = importlib.import_module("ai_org.patchwork_queue.receive")
    id_fields = receive_module._ID_TYPED_REFERENCE_FIELDS
    edges: list[tuple[str, str]] = []

    def _walk(value: object, holder: str) -> None:
        if isinstance(value, Mapping):
            node_id = value.get("id")
            current = node_id if isinstance(node_id, str) and node_id else holder
            for field_name in id_fields:
                reference = value.get(field_name)
                if isinstance(reference, str) and reference and current:
                    edges.append((current, reference))
            links = value.get("cross_links")
            if isinstance(links, list):
                for link in links:
                    if isinstance(link, Mapping):
                        source, target = link.get("from"), link.get("to")
                        if isinstance(source, str) and source and isinstance(target, str) and target:
                            edges.append((source, target))
            for key, child in value.items():
                if key != "cross_links":
                    _walk(child, current)
        elif isinstance(value, list):
            for child in value:
                _walk(child, holder)

    _walk(tree, "")
    return edges


def _descendant_node_ids(tree: object) -> dict[str, set[str]]:
    """Map each node id to the set of node ids inside its subtree (itself excluded)."""
    descendants: dict[str, set[str]] = {}

    def _walk(value: object, ancestors: tuple[str, ...]) -> None:
        if isinstance(value, Mapping):
            node_id = value.get("id")
            current = ancestors
            if isinstance(node_id, str) and node_id:
                descendants.setdefault(node_id, set())
                for ancestor in ancestors:
                    descendants[ancestor].add(node_id)
                current = ancestors + (node_id,)
            for child in value.values():
                _walk(child, current)
        elif isinstance(value, list):
            for child in value:
                _walk(child, ancestors)

    _walk(tree, ())
    return descendants


def _scope_from_seeds(approach: Mapping[str, Any], seeds: set[str]) -> dict[str, Any]:
    """Seed set -> review window (shared core of delta scoping and round-1 sharding).

    Scope = seeds + their 1-hop referential neighborhood over id-typed edges: the
    highest-value review catches are cross-node coherence objections (live:
    decision=React vs stack contract=Phaser spans a changed and an unchanged
    node), and a seeds-only scope is blind to exactly those. Neighbors whose
    subtree CONTAINS a seed (structural ancestors like question:approach) are
    excluded — re-sending them would smuggle the whole tree back in. Everything
    outside stays id-manifest-only. Pure graph walk; no judgment anywhere.
    """
    current_ids = _collect_node_ids(approach)
    seeds = seeds & current_ids

    neighborhood: set[str] = set()
    for source, target in _reference_edges(approach):
        if source in seeds and target in current_ids:
            neighborhood.add(target)
        if target in seeds and source in current_ids:
            neighborhood.add(source)
    descendants = _descendant_node_ids(approach)
    neighborhood = {
        node_id
        for node_id in neighborhood - seeds
        if not (descendants.get(node_id, set()) & seeds)
    }

    scope_ids = seeds | neighborhood
    bodies: dict[str, Any] = {}

    def _collect(value: object) -> None:
        if isinstance(value, Mapping):
            node_id = value.get("id")
            if isinstance(node_id, str) and node_id in scope_ids and node_id not in bodies:
                bodies[node_id] = value
                return  # children ride along inside this body
            for child in value.values():
                _collect(child)
        elif isinstance(value, list):
            for child in value:
                _collect(child)

    _collect(approach)
    return {
        "scope_node_ids": sorted(scope_ids),
        "changed_nodes": {node_id: bodies[node_id] for node_id in sorted(bodies)},
        "unchanged_node_ids": sorted(current_ids - scope_ids),
    }


def _delta_review_scope(approach: Mapping[str, Any], delta: Mapping[str, Any]) -> dict[str, Any]:
    """Deterministic delta scope builder (brief 19 component D + neighborhood amendment)."""
    seeds: set[str] = set()
    for key in ("changed_node_ids", "added_node_ids"):
        seeds.update(str(item) for item in delta.get(key, []) if isinstance(item, str))
    for replacement in delta.get("node_replacements", []):
        if isinstance(replacement, Mapping) and isinstance(replacement.get("successor_node_id"), str):
            seeds.add(replacement["successor_node_id"])
    # A changed ANCESTOR whose subtree contains another changed seed is only
    # changed because its child is (content hashes bubble up the ledger): keep
    # the child — the true delta — and drop the ancestor seed, or the whole
    # tree smuggles back into every delta window through "problem". An ancestor
    # changed in its OWN fields with no changed descendant keeps its seat.
    # (Delta seeding only; brief 20 shards must keep their own root nodes.)
    descendants = _descendant_node_ids(approach)
    seeds = {seed for seed in seeds if not (descendants.get(seed, set()) & (seeds - {seed}))}
    return _scope_from_seeds(approach, seeds)


# ==========================================================================================
# ATTENTION-WINDOW MANAGEMENT (brief 20, RATIFIED requester 2026-07-05). Binding framing
# constraint: 「Reviewer厳しすぎるのおかしいが、それは愚かにしろという意味ではない」 —
# the reviewer's sharpness is preserved IN FULL. No leniency language, no objection
# caps, no threshold raises, no charter edits: this machinery manages WINDOW SIZE
# only. Round 1 shards the full tree into step-subtree windows (the round-1 full-tree
# prompt was the largest window, the most saturation, the most missed issues); rounds
# N>1 add a deterministic rotating patrol over unchanged nodes so brief19-D's delta
# scoping cannot lock round-1 misses in permanently.
# ==========================================================================================

PATROL_SLICE_SIZE = 8  # [PROVISIONAL-P1] unchanged-node patrol window per round


def _round_one_shards(approach: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Partition the tree into step-subtree shards (deterministic, no judgment).

    Shard boundaries are the natural step subtrees; the node->step mapping is
    receive's own (_node_step_map / REFORM_STEP_ORDER, imported at call time so
    the two sides cannot drift). Every node is PRIMARY in exactly one shard;
    1-hop neighbors may repeat across shards, and _dedupe_objections absorbs
    duplicate objections. Sharding changes the window size, never what is
    reviewable.
    """
    receive_module = importlib.import_module("ai_org.patchwork_queue.receive")
    step_by_node: dict[str, str] = receive_module._node_step_map(approach)
    step_order = list(receive_module.REFORM_STEP_ORDER)
    step_order += sorted(set(step_by_node.values()) - set(step_order))
    shards: list[dict[str, Any]] = []
    for step in step_order:
        primary = sorted(node_id for node_id, node_step in step_by_node.items() if node_step == step)
        if not primary:
            continue
        shards.append(
            {
                "shard_step": step,
                "primary_node_ids": primary,
                **_scope_from_seeds(approach, set(primary)),
            }
        )
    return shards


def _shard_scope_text(shard: Mapping[str, Any]) -> str:
    # Factual window framing only: what this shard contains and where the rest
    # lives. No judgment instructions beyond the untouched charter above it.
    return (
        f"The current paper is sharded by step subtree to keep each review window small; this call covers the "
        f"{shard.get('shard_step', '')} subtree.\n"
        "This shard's nodes plus their directly referenced neighbors:\n"
        f"{_json_for_prompt(shard.get('changed_nodes', {}))}\n"
        "Other node ids (reviewed by this round's other shards; still valid as anchors):\n"
        f"{_json_for_prompt(shard.get('unchanged_node_ids', []))}\n"
    )


def _patrol_slice(unchanged_ids: list[str], round_number: int) -> list[str]:
    """Deterministic rotating patrol over unchanged nodes for round N>1.

    Bytewise-sorted ids, K consecutive starting at offset ((N-2)*K mod count):
    rotation state is the committed round number — no randomness, no clock.
    Consecutive rounds walk disjoint slices until wraparound, so long-running
    series get progressively deeper re-audit while converging series pay almost
    nothing.
    """
    ids = sorted(unchanged_ids)
    if not ids or round_number < 2:
        return []
    count = len(ids)
    size = min(PATROL_SLICE_SIZE, count)
    offset = ((round_number - 2) * PATROL_SLICE_SIZE) % count
    return [ids[(offset + index) % count] for index in range(size)]


def _latest_prior_round_record(
    repo: Path,
    branch: str,
    patch_series_id: str,
) -> dict[str, Any] | None:
    records = _review_round_records(repo, patch_series_id, branch)
    return records[-1] if records else None


def _prior_unresolved_objections(
    repo: Path,
    branch: str,
    patch_series_id: str,
) -> list[dict[str, Any]]:
    """Return the latest committed round's unresolved objections.

    This history is intentionally kept out of every reviewer paper.  It is read
    only after the fresh round for deterministic consolidation bookkeeping.
    """
    previous = _latest_prior_round_record(repo, branch, patch_series_id)
    if previous is None:
        return []
    return [
        dict(entry)
        for entry in previous.get("objections", [])
        if isinstance(entry, Mapping) and entry.get("status") in {"open", "carried-forward"}
    ]


def _cross_round_objection_mapping(
    prior_unresolved: list[Mapping[str, Any]],
    fresh_objections: list[Objection],
) -> list[dict[str, Any]]:
    """Match prior concerns to a blank round by axis and anchor overlap.

    Claim similarity is deliberately absent.  Recording every overlapping fresh
    id is conservative when more than one current objection touches the old
    anchor, and sorting both sides makes the mapping independent of model order.
    """
    fresh = sorted(
        fresh_objections,
        key=lambda objection: (
            objection.axis,
            objection.objection_id,
            tuple(sorted(set(objection.anchor_node_ids))),
        ),
    )
    mapping: list[dict[str, Any]] = []
    ordered_prior = sorted(
        prior_unresolved,
        key=lambda objection: (
            str(objection.get("axis") or ""),
            str(objection.get("objection_id") or ""),
            tuple(sorted(str(anchor) for anchor in objection.get("anchor_node_ids", []) if isinstance(anchor, str))),
        ),
    )
    for prior in ordered_prior:
        prior_anchors = sorted(
            {anchor for anchor in prior.get("anchor_node_ids", []) if isinstance(anchor, str)}
        )
        anchor_set = set(prior_anchors)
        axis = str(prior.get("axis") or "")
        fresh_ids = sorted(
            {
                objection.objection_id
                for objection in fresh
                if objection.axis == axis and anchor_set.intersection(objection.anchor_node_ids)
            }
        )
        mapping.append(
            {
                "prior_objection_id": str(prior.get("objection_id") or ""),
                "axis": axis,
                "prior_anchor_node_ids": prior_anchors,
                "status": "unresolved" if fresh_ids else "resolved_by_construction",
                "fresh_objection_ids": fresh_ids,
            }
        )
    return mapping


def _round_continuity(
    repo: Path,
    branch: str,
    patch_series_id: str,
    approach: Mapping[str, Any],
    round_number: int,
) -> dict[str, Any] | None:
    """Compose an internal committed-history view, never a reviewer input."""
    records = _review_round_records(repo, patch_series_id, branch)
    if not records:
        return None
    previous = records[-1]
    previous_round = _review_round_number(previous)
    delta = _load_revision_delta(repo, patch_series_id, branch, previous_round)
    if delta is None:
        return None
    responses = {
        str(item.get("objection_id")): item
        for item in delta.get("objection_responses", [])
        if isinstance(item, Mapping)
    }
    superseded: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    for objection in previous.get("objections", []):
        if not isinstance(objection, Mapping) or objection.get("status") not in {"open", "carried-forward"}:
            continue
        entry = dict(objection)
        response = responses.get(str(objection.get("objection_id")))
        if response is not None:
            entry["revision_response"] = {
                "classification": response.get("classification"),
                "successor_node_ids": list(response.get("successor_node_ids", [])),
            }
            if response.get("classification") == "deferral_proposed":
                entry["revision_response"]["executable_check"] = str(response.get("executable_check") or "")
                entry["revision_response"]["defer_reason"] = str(response.get("defer_reason") or "")
            if response.get("classification") == "defended_with_evidence":
                # Preserve the author's words verbatim for author-side continuity
                # and record diagnostics. They never enter blank reviewer paper.
                entry["revision_response"]["defense"] = str(response.get("defense") or "")
                entry["revision_response"]["evidence_citations"] = [
                    str(citation) for citation in response.get("evidence_citations", []) if isinstance(citation, str)
                ]
        if response is not None and response.get("classification") == "superseded_by_replacement":
            # Anchors pruned WITH a declared successor remain historical data.
            # The fresh reviewer independently reads the successor; this entry is
            # never injected into that paper.
            entry["status"] = "superseded"
            entry["successor_node_ids"] = list(response.get("successor_node_ids", []))
            superseded.append(entry)
        else:
            unresolved.append(entry)
    changed_set = {str(item) for item in delta.get("changed_node_ids", []) if isinstance(item, str)}
    changed_set |= {str(item) for item in delta.get("added_node_ids", []) if isinstance(item, str)}
    prior_resolved: list[dict[str, Any]] = []
    for resolved in previous.get("resolved_objections", []):
        if not isinstance(resolved, Mapping):
            continue
        anchors = [anchor for anchor in resolved.get("anchor_node_ids", []) if isinstance(anchor, str)]
        if any(anchor in changed_set for anchor in anchors):
            # Component C: the anchor substantially changed (content hash), so the
            # prior resolved status EXPIRES (canon: GitHub stale-approval dismissal;
            # kernel tag removal on substantial change).
            unresolved.append({**resolved, "status": "open", "resolution_expired": True})
        else:
            prior_resolved.append(dict(resolved))
    scope = _delta_review_scope(approach, delta)
    # Brief 20 component 2: deterministic rotating patrol over unchanged nodes.
    # Without it, brief19-D never revisits unchanged nodes and a round-1 miss is
    # locked in permanently. Patrol nodes are eligible for NEW objections like any
    # reviewed node; sharpness untouched, only the window rotates.
    patrol_nodes: dict[str, Any] = {}
    patrol_ids = _patrol_slice(scope["unchanged_node_ids"], round_number)
    if patrol_ids:
        patrol_scope = _scope_from_seeds(approach, set(patrol_ids))
        delta_scope_ids = set(scope["scope_node_ids"])
        patrol_nodes = {
            node_id: body
            for node_id, body in patrol_scope["changed_nodes"].items()
            if node_id not in delta_scope_ids
        }
        scope = {
            **scope,
            "unchanged_node_ids": sorted(
                set(scope["unchanged_node_ids"]) - set(patrol_scope["scope_node_ids"])
            ),
        }
    deferral_proposals_by_id = {
        str(item.get("objection_id") or ""): {
            "executable_check": str(item.get("executable_check") or ""),
            "defer_reason": str(item.get("defer_reason") or ""),
        }
        for item in delta.get("objection_responses", [])
        if isinstance(item, Mapping) and item.get("classification") == "deferral_proposed"
    }
    return {
        "previous_round": previous_round,
        "delta": dict(delta),
        "unresolved_objections": unresolved,
        "superseded_objections": superseded,
        "prior_resolved_objections": prior_resolved,
        "patrol_node_ids": patrol_ids,
        "patrol_nodes": patrol_nodes,
        "deferral_proposals_by_id": deferral_proposals_by_id,
        **scope,
    }


def _revision_scope_text(revision_scope: Mapping[str, Any]) -> str:
    """Legacy diagnostic rendering of continuity data, never reviewer paper."""
    deferral_note = ""
    if revision_scope.get("deferral_proposals_by_id"):
        deferral_note = (
            "Where an unresolved objection's revision_response proposes a deferral "
            "(classification deferral_proposed, with executable_check): re-raise it with status "
            "deferral_accepted to accept — its executable check becomes a question the patch must answer, "
            "enforced at the merge gate — or with status deferral_rejected to keep it open-blocking.\n"
        )
    defense_note = ""
    if any(
        isinstance(entry, Mapping)
        and isinstance(entry.get("revision_response"), Mapping)
        and entry["revision_response"].get("classification") == "defended_with_evidence"
        for entry in revision_scope.get("unresolved_objections", [])
    ):
        # Brief 33 (canon rule 3: insistence is not evidence). Factual protocol
        # disclosure, not leniency: the exclusion is mechanical and stated as
        # such; upholding through consolidation stays fully available.
        defense_note = (
            "Where an unresolved objection's revision_response is defended_with_evidence: the author "
            "declined to change nodes and defends the existing content with cited evidence. Judge the "
            "defense on its merits; re-raise the objection only with evidence that answers it. A re-raise "
            "whose evidence adds nothing over the prior round is recorded as insisted_without_new_evidence "
            "and does not count as open-blocking by itself; consolidation may still uphold it explicitly "
            "with reasons.\n"
        )
    return (
        f"This is direction-review round {int(revision_scope.get('previous_round', 0)) + 1} of a revised "
        "patch series; the input below is scoped to the revision delta.\n"
        "Unresolved objections from previous rounds, with the author's revision responses. For each one: if "
        "it remains unresolved, re-raise it keeping its exact objection_id and updating anchor_node_ids to "
        "the listed successor node ids where noted; if the revision resolves it, omit it:\n"
        + deferral_note
        + defense_note
        + f"{_json_for_prompt(revision_scope.get('unresolved_objections', []))}\n"
        "Superseded objections: their anchor nodes were replaced by the successor nodes in the changed set "
        "below. Raise a fresh objection against a successor only if the concern still applies to it:\n"
        f"{_json_for_prompt(revision_scope.get('superseded_objections', []))}\n"
        "Changed or added nodes plus their directly referenced neighbors (raise new objections only against "
        "these):\n"
        f"{_json_for_prompt(revision_scope.get('changed_nodes', {}))}\n"
        + (
            "Unchanged nodes included for periodic re-review this round, with their directly referenced "
            "neighbors; they are reviewable for new objections like any node above:\n"
            f"{_json_for_prompt(revision_scope.get('patrol_nodes', {}))}\n"
            if revision_scope.get("patrol_nodes")
            else ""
        )
        + "Unchanged node ids (bodies not re-sent; their standing verdicts hold; still valid as anchors):\n"
        f"{_json_for_prompt(revision_scope.get('unchanged_node_ids', []))}\n"
    )


def _review_one(
    dim: Dimension,
    patch_series_view: Mapping[str, Any],
    approach: Mapping[str, Any],
    node_ids: set[str],
    repo: str | Path,
    *,
    ctx: org_log.RunContext | None = None,
    shard_scope: Mapping[str, Any] | None = None,
    consultations: list[dict[str, Any]] | None = None,
) -> AxisReview:
    # consultations may be precomputed by the round-1 shard loop: the precedent
    # consultation depends on (dimension, view, approach) only, so per-shard
    # recomputation would be identical work repeated.
    if consultations is None:
        consultations = _consult_reference(dim, patch_series_view, approach)
    feedback = ""
    validation_errors: list[str] = []
    retry_state = codex_exec.RejectionRetryState()
    shard_suffix = f"-{shard_scope.get('shard_step', '')}" if shard_scope is not None else ""
    for attempt in range(1, 3):
        prompt = _review_prompt(
            dim,
            patch_series_view,
            approach,
            node_ids,
            consultations,
            feedback,
            shard_scope=shard_scope,
        )
        attempt_ctx = ctx.child(attempt=attempt) if ctx is not None else None
        raw, error = _run_codex(
            repo,
            prompt,
            build_objection_schema(),
            f"patch_series-objection-{dim.key}{shard_suffix}.schema.json",
            f"{dim.key}{shard_suffix}.json",
            ctx=attempt_ctx,
        )
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
        rejection = codex_exec.validation_rejection(f"review.axis.{dim.key}", "; ".join(errors))
        decision = retry_state.classify(rejection, attempt=attempt, ctx=attempt_ctx)
        feedback = codex_exec.format_rejection_feedback(rejection)
        if decision.permanent:
            validation_errors = [codex_exec.permanent_rejection_error(decision)]
            break

    return AxisReview(
        axis=dim.key,
        verdict="objections_pending",
        objections=[_process_objection(dim.key, "; ".join(validation_errors), node_ids=node_ids, consultations=consultations)],
        reference_consultations=consultations,
        attempts=2,
        validation_errors=validation_errors,
    )


def _review_dimension_sharded(
    dim: Dimension,
    patch_series_view: Mapping[str, Any],
    approach: Mapping[str, Any],
    node_ids: set[str],
    repo: str | Path,
    *,
    ctx: org_log.RunContext | None = None,
) -> AxisReview:
    """Fresh review of one dimension as step-subtree shard windows.

    One review call per (dimension x step subtree); every node is reviewed as a
    primary exactly once, shard objections merge through the same anchors+claim
    dedupe, and the charter each shard sees is byte-identical to the full-tree
    round's. Shards are independent; sequential execution here is correctness
    first, latency second (no parallel machinery exists in this flow today).
    """
    shards = _round_one_shards(approach)
    if not shards:
        return _review_one(dim, patch_series_view, approach, node_ids, repo, ctx=ctx)
    consultations = _consult_reference(dim, patch_series_view, approach)
    objections: list[Objection] = []
    validation_errors: list[str] = []
    attempts = 0
    pending = False
    for shard in shards:
        shard_ctx = ctx.child(step=f"shard.{shard['shard_step']}") if ctx is not None else None
        axis = _review_one(
            dim,
            patch_series_view,
            approach,
            node_ids,
            repo,
            ctx=shard_ctx,
            shard_scope=shard,
            consultations=consultations,
        )
        objections.extend(axis.objections)
        validation_errors.extend(axis.validation_errors)
        attempts += axis.attempts
        if axis.verdict != "Direction-reviewed-by":
            pending = True
    merged = _dedupe_objections(objections)
    return AxisReview(
        axis=dim.key,
        verdict="objections_pending" if pending else "Direction-reviewed-by",
        objections=merged,
        reference_consultations=consultations,
        attempts=attempts,
        validation_errors=validation_errors,
    )


def _aufheben_consolidate(
    patch_series_view: Mapping[str, Any],
    approach: Mapping[str, Any],
    node_ids: set[str],
    objections: list[Objection],
    axis_reviews: list[AxisReview],
    repo: str | Path,
    *,
    ctx: org_log.RunContext | None = None,
) -> Consolidation:
    prompt = (
        "You are Aufheben for a patch series direction-review round. Consolidate reviewer critique only. "
        "Do not author a revised patch series, replacement design, patch plan, or implementation. "
        "Aggregate objections across axes, deduplicate by the same anchor_node_ids plus the same claim, "
        "resolve contradictions between reviewers in the contradiction_resolutions field, and produce "
        "the round verdict. direction-ok means zero open blocking objections. needs_revision means open "
        "blocking objections exist and the author must reform and repost later. nak is reserved for an "
        "evidenced decision that the direction is fundamentally unsuitable. Structural rule: "
        "requester-authority objections are recorded as requester assumptions and are non-blocking; "
        "they must not drive needs_revision or nak.\n"
        f"patch series:\n{_json_for_prompt(_patch_series_to_view(dict(patch_series_view)))}\n"
        f"Technical Approach with node ids:\n{_json_for_prompt(approach)}\n"
        + f"Valid node ids:\n{_json_for_prompt(sorted(node_ids))}\n"
        f"Per-axis verdicts:\n{_json_for_prompt({axis.axis: axis.verdict for axis in axis_reviews})}\n"
        f"Objections:\n{_json_for_prompt([_objection_record(objection) for objection in objections])}\n"
        "Return only JSON matching the provided schema."
    )
    raw, error = _run_codex(repo, prompt, build_aufheben_schema(), "patch_series-aufheben.schema.json", "aufheben.json", ctx=ctx)
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
    *,
    ctx: org_log.RunContext | None = None,
) -> tuple[str, str]:
    run = codex_exec.run_json(
        Path(repo),
        schema=schema,
        prompt=prompt,
        schema_filename=schema_filename,
        output_filename=output_filename,
        failure_label="Codex review",
        ctx=ctx,
    )
    if not run["ok"]:
        return "", str(run["error"])
    return str(run["raw"]), ""


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
    errors = _lint_object_schema(parsed, build_objection_schema(), "review")
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
    errors = _lint_object_schema(parsed, build_aufheben_schema(), "aufheben")
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
    errors = _lint_object_schema(item, build_objection_item_schema(), path)
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
        errors.append(f"{path}.evidence must record at least one precedent-store term consulted")
    if item["type"] == "blocking" and not item["impact"].strip():
        errors.append(f"{path}.impact must be non-empty for blocking objections")
    # Brief 33: the self-declaration must actually be declared (completeness
    # lint only — the VALUES never gate anything mechanically).
    if not item["reviewed_scope"].strip():
        errors.append(f"{path}.reviewed_scope must state what was examined for this objection")
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
        resolution_authority=item["resolution_authority"],
        status=item["status"],
        # Brief 33 fields: .get so committed pre-brief-33 round records (which
        # also flow through this parser) stay parseable with empty defaults.
        reviewer_confidence=str(item.get("reviewer_confidence") or ""),
        reviewed_scope=str(item.get("reviewed_scope") or ""),
    )


def _parse_evidence(item: Mapping[str, Any]) -> Evidence:
    return Evidence(
        source_type=item["source_type"],
        citation=item["citation"],
        consulted_terms=list(item["consulted_terms"]),
    )


def _consult_reference(
    dim: Dimension,
    patch_series_view: Mapping[str, Any],
    approach: Mapping[str, Any],
) -> list[dict[str, Any]]:
    context = _precedent_context(patch_series_view)
    terms = _precedent_terms(dim, patch_series_view, approach)
    consultations: list[dict[str, Any]] = []
    for term in terms:
        try:
            hit = engineering_precedent_store.lookup(term, context, kind="design")
        except Exception as exc:  # noqa: BLE001 - review records lookup failures as evidence context.
            consultations.append({"term": term, "kind": "design", "error": str(exc), "hit": False})
            continue
        consultations.append(
            {
                "term": term,
                "kind": "design",
                "hit": bool(hit and hit.get("candidates")),
                "result": _compact_precedent_hit(hit),
            }
        )
    return consultations


def _precedent_terms(dim: Dimension, patch_series_view: Mapping[str, Any], approach: Mapping[str, Any]) -> list[str]:
    candidates = [
        str(patch_series_view.get("working_title") or ""),
        str(patch_series_view.get("affected_area_platform") or ""),
        str(patch_series_view.get("proposal_hint") or ""),
        _approach_decision_label(approach),
        f"{dim.key} review {patch_series_view.get('affected_area_platform') or patch_series_view.get('working_title') or 'patch series'}",
    ]
    cleaned: list[str] = []
    for candidate in candidates:
        term = " ".join(candidate.split()).strip()
        if term and term not in cleaned:
            cleaned.append(term)
    return cleaned[:4]


def _precedent_context(patch_series_view: Mapping[str, Any]) -> dict[str, Any]:
    tech_stack = patch_series_view.get("tech_stack")
    context: dict[str, Any] = {}
    if isinstance(tech_stack, Mapping):
        for key in ("language", "framework", "engine", "platform"):
            value = str(tech_stack.get(key) or "").strip()
            if value:
                context[key] = value
    return context


def _compact_precedent_hit(hit: Mapping[str, Any] | None) -> dict[str, Any]:
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
    patch_series_view: Mapping[str, Any],
    approach: Mapping[str, Any],
    node_ids: set[str],
    consultations: list[dict[str, Any]],
    feedback: str,
    *,
    shard_scope: Mapping[str, Any] | None = None,
) -> str:
    # Reviewer judgment instructions stay fixed while the current Technical
    # Approach is printed in a bounded shard window. Brief 33 (ratified 2026-07-05,
    # requester: レビュワーが制約になるのは絶対にあってはならない): the freeze was LIFTED for
    # exactly one scope — the reviewer self-declaration + citation-verification
    # sentences below (asymmetric_review_canon: kernel reviewed-aspects norm,
    # NeurIPS confidence). Zero-softening still binds: the added sentences are
    # factual duties, no leniency language, and the sharpness sentences
    # ("Blocking objections need concrete impact." etc.) stay byte-identical.
    # Wording verified by blank-context probe (out_a.txt, 2026-07-05): read as
    # honest self-declaration, explicitly NOT as relaxed rigor. Memento: the
    # reviewer is a filter, never a ceiling — knowledge asymmetry is resolved
    # by evidence and defense, not deference.
    if shard_scope is not None:
        approach_payload = _shard_scope_text(shard_scope)
    else:
        approach_payload = f"Technical Approach:\n{_json_for_prompt(approach)}\n"
    # Memento (brief 35, requester ruling 2026-07-07: SIer仕草はReviwerからは外す —
    # remove enterprise-SI ceremony from the reviewer). Live evidence: prod CUE
    # body/lens run rounds 1-3 — a CUE toolchain-lifecycle objection (upgrade
    # ownership, failure policy) survived 3 rounds, alongside record-retention
    # and unified-similarity-threshold demands. None of these is what kernel
    # review asks for. LKML grounding: testing is ambient CI robots + Tested-by
    # attestation + in-tree suites, never a negotiated test plan document;
    # migration is the series itself converting all in-tree consumers within
    # the same series, with bisectability (every commit keeps the tree
    # working) and additive coexistence for external surfaces; Linus deleted
    # feature-removal-schedule.txt in 2012 as ceremony. The doctrine paragraph
    # below states what evidence COUNTS first, then the authority boundary.
    # Amendment (requester ruling 2026-07-07): the enumerated authority exclusions
    # are a stopgap reflecting THIS request's weeks-scale horizon - defaults
    # with a request override (a request that itself calls for a long horizon
    # makes such demands request-derived and legitimate). The trace-to-request
    # clause is the permanent part; the durable fix is a request-derived
    # evidence horizon extracted at grounding time (big-reform scope).
    return (
        f"You are reviewing one patch series direction axis: {dim.key} - {dim.blurb}.\n"
        "Review doctrine (Linux-kernel grounding): the Technical Approach under review is an "
        "intermediate representation between the requester's raw request and code - authoritative "
        "within this patch series cycle, not a deliverable preserved across representation "
        "generations. Evidence that satisfies review: every planned slice leaves the system "
        "working; a change to an internal format converts its consumers within the same series; "
        "correctness is enforced by deterministic checks committed alongside the artifact. Every "
        "objection must trace to the requester's request, a ratified decision, or an internal "
        "contradiction in the submitted tree. Standalone migration or test plan documents, record "
        "retention policies, toolchain upgrade-ownership procedures, long-horizon stewardship "
        "guarantees, and fixed similarity thresholds are outside review authority; if such a "
        "concern seems real, state the concrete failure it causes within this cycle instead of "
        "demanding the ceremony artifact - unless the requester's request itself calls for that "
        "horizon or artifact, in which case cite the request passage that does.\n"
        "This is a group-internal direction conversation before implementation, not the PR gate. "
        "Critique the submitted patch series and Technical Approach. Do not author replacement designs, "
        "do not re-litigate receive's request interpretation, and do not style-police patch details. "
        "Every objection must anchor to existing technical-approach node ids, mirroring LKML inline "
        "quote discipline. Blocking objections need concrete impact. Preferences without evidence and "
        "impact must be nonblocking_suggestion or style_defer. Classify resolution_authority structurally: "
        "author for technical evaluation gaps the author can settle; requester for rights/licensing stance, "
        "product policy, budget, priority, or other requester-only decisions. For every objection, declare "
        "reviewer_confidence (high, medium, or low) and reviewed_scope (what you actually examined to raise "
        "this objection) honestly; both are informational for the author and the consolidation, never a "
        "verdict by themselves. You may verify author-cited evidence with a web search - verification of "
        "presented citations only, not open research; the burden of proof stays with the author.\n"
        f"Valid node ids:\n{_json_for_prompt(sorted(node_ids))}\n"
        f"patch series:\n{_json_for_prompt(_patch_series_to_view(dict(patch_series_view)))}\n"
        + approach_payload
        + f"Precedent-store consultations you must account for in evidence.consulted_terms:\n{_json_for_prompt(consultations)}\n"
        f"{'Feedback from prior invalid output: ' + feedback + chr(10) if feedback else ''}"
        "Return only JSON matching the provided schema."
    )


def _derive_verdict(consolidation: Consolidation, objections: list[Objection]) -> str:
    if (
        consolidation.verdict == "nak"
        and consolidation.nak_reason.strip()
        and consolidation.evidence
        and not _nak_rests_solely_on_requester_authority(objections)
        and not _nak_rests_solely_on_superseded(objections)
    ):
        return "nak"
    if any(objection.status == "open" and objection.type == "blocking" for objection in objections):
        return "needs_revision"
    return "direction-ok"


def _nak_rests_solely_on_requester_authority(objections: list[Objection]) -> bool:
    return bool(objections) and all(objection.resolution_authority == "requester" for objection in objections)


def _nak_rests_solely_on_superseded(objections: list[Objection]) -> bool:
    # Brief 19 [PROVISIONAL-C1]: nak requires unresolved objections that are NOT
    # superseded. A round whose only objections were superseded by declared
    # replacements cannot nak on them — the successor nodes were re-offered to the
    # reviewer, who raised nothing fresh (this is the DQ round-6 trap: dangling
    # anchors -> unresolvable-forever -> NAK). The genuine-nak escape stays closed
    # exactly as 03a7319 left it: any non-superseded objection, or an evidenced
    # nak with no objection list at all, is untouched by this guard.
    return bool(objections) and all(objection.status == "superseded" for objection in objections)


def _evidence_signature(evidence_items: Any) -> tuple[set[str], set[str]]:
    """Normalized (citations, consulted_terms) sets for the insistence check."""
    citations: set[str] = set()
    terms: set[str] = set()
    for item in evidence_items or []:
        if isinstance(item, Mapping):
            citation = str(item.get("citation") or "")
            item_terms = item.get("consulted_terms") or []
        else:
            citation = str(getattr(item, "citation", "") or "")
            item_terms = getattr(item, "consulted_terms", []) or []
        if citation.strip():
            citations.add(citation.strip())
        for term in item_terms:
            term_text = str(term).strip()
            if term_text:
                terms.add(term_text)
    return citations, terms


def _insisted_without_new_evidence_ids(
    final_objections: list[Objection],
    continuity: Mapping[str, Any] | None,
) -> set[str]:
    """Objection ids re-raised after a defense with no new evidence (brief 33).

    Mechanical, id-keyed (re-raise = same objection_id, the established
    continuity vocabulary): the objection was answered defended_with_evidence
    in the prior round's ledger, and this round's re-raise adds NOTHING over
    the prior round's evidence (its citations and consulted_terms are subsets
    of what the reviewer already presented). Canon rule 3: no revision is
    required merely because the reviewer is insistent; rule 4 lists what DOES
    compel — reproducible, rule-based, independently corroborated, or upheld
    by the designated arbiter (here: an explicit evidenced consolidation nak,
    which _derive_verdict honors regardless of this marking).
    """
    if continuity is None:
        return set()
    defended_prior: dict[str, tuple[set[str], set[str]]] = {}
    for entry in continuity.get("unresolved_objections", []):
        if not isinstance(entry, Mapping):
            continue
        response = entry.get("revision_response")
        if isinstance(response, Mapping) and response.get("classification") == "defended_with_evidence":
            defended_prior[str(entry.get("objection_id") or "")] = _evidence_signature(entry.get("evidence", []))
    if not defended_prior:
        return set()
    insisted: set[str] = set()
    for objection in final_objections:
        if objection.status != "open" or objection.type != "blocking":
            continue
        prior = defended_prior.get(objection.objection_id)
        if prior is None:
            continue
        citations, terms = _evidence_signature(objection.evidence)
        if citations <= prior[0] and terms <= prior[1]:
            insisted.add(objection.objection_id)
    return insisted


def _with_assumption_status(objection: Objection, recorded_assumption_ids: set[str]) -> Objection:
    if objection.objection_id not in recorded_assumption_ids:
        return objection
    # replace() instead of a hand-listed constructor call: a hand-listed field
    # set here silently drops newly added dataclass fields (it would have
    # dropped brief 33's reviewer_confidence/reviewed_scope).
    return replace(objection, status="assumption_recorded")


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


def _read_patch_series_from_git(
    repo: Path,
    branch: str,
    path: str,
    *,
    root_snapshot: patch_series_bodies.RootGenerationSnapshot | None = None,
) -> tuple[dict[str, Any] | None, str]:
    if patch_series_bodies.is_root_cover_path(path):
        snapshot = root_snapshot or patch_series_bodies.classify_root_generation(
            repo, branch
        )
        member_only_compatibility = (
            snapshot.disposition == patch_series_bodies.ROOT_DISPOSITION_INVALID
            and snapshot.raw(patch_series_bodies.ROOT_APPROACH_PATH) is None
            and snapshot.raw(patch_series_bodies.LEGACY_ROOT_APPROACH_PATH) is None
        )
        if not snapshot.lifecycle_ready and not member_only_compatibility:
            return None, snapshot.diagnostic
        try:
            parsed = (
                patch_series_bodies.read_cover_letter(
                    repo,
                    snapshot.frozen_oid or branch,
                    root_snapshot=snapshot,
                )
                if member_only_compatibility
                else snapshot.cover_letter()
            )
        except Exception as exc:
            if snapshot.generation == patch_series_bodies.ROOT_GENERATION_HISTORICAL:
                return None, f"{branch}:{path} is invalid JSON: {exc}"
            return None, f"{branch}:{path} failed canonical body validation: {exc}"
        if not _is_patch_series_view(parsed):
            return None, f"{branch}:{path} must contain exactly the patch series field registry fields"
        return _patch_series_to_view(parsed), ""
    raw = git_wrapper.show_file(repo, branch, path)
    if raw is None:
        return None, f"could not read {branch}:{path}"
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        return None, f"{branch}:{path} is invalid JSON: {exc}"
    if not _is_patch_series_view(parsed):
        return None, f"{branch}:{path} must contain exactly the patch series field registry fields"
    return _patch_series_to_view(parsed), ""


def _read_json_from_git(
    repo: Path,
    branch: str,
    path: str,
    *,
    root_snapshot: patch_series_bodies.RootGenerationSnapshot | None = None,
) -> tuple[dict[str, Any] | None, str]:
    if path == patch_series_bodies.LEGACY_ROOT_APPROACH_PATH:
        snapshot = root_snapshot or patch_series_bodies.classify_root_generation(
            repo, branch
        )
        if not snapshot.lifecycle_ready:
            return None, snapshot.diagnostic
        try:
            return snapshot.technical_approach(), ""
        except (ValueError, TypeError) as exc:
            return None, f"{branch}:{path} is invalid JSON: {exc}"
    raw = git_wrapper.show_file(repo, branch, path)
    if raw is None:
        return None, f"could not read {branch}:{path}; patch series review requires technical-approach-plan.json"
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        return None, f"{branch}:{path} is invalid JSON: {exc}"
    if not isinstance(parsed, dict):
        return None, f"{branch}:{path} must contain a JSON object"
    return parsed, ""


def _is_patch_series_view(value: object) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == set(WORK_ORDER_VIEW_FIELDS)
        and all(isinstance(value[field], str) for field in STRING_FIELDS)
        and all(
            isinstance(value[field], list) and all(isinstance(item, str) for item in value[field])
            for field in STRING_ARRAY_FIELDS
        )
        # Brief 34: tech_stack platform vocabulary is scoped by the same view's
        # own UX applicability (deliverable-kind lives in data, not validators).
        and validate_tech_stack(value.get("tech_stack"), user_facing=deliverable_is_user_facing(value))
        and validate_user_experience_requirements(value.get("user_experience_requirements"))
    )


def _patch_series_to_view(patch_series_view: dict[str, Any]) -> dict[str, Any]:
    return requester_assumptions.patch_series_to_view(patch_series_view)


def _patch_series_branch(patch_series_id_or_branch: str) -> str:
    if patch_series_id_or_branch.startswith("refs/heads/"):
        return patch_series_id_or_branch.removeprefix("refs/heads/")
    if patch_series_id_or_branch.startswith("ai-org/patch-series/"):
        return patch_series_id_or_branch
    return f"ai-org/patch-series/{patch_series_id_or_branch}"


def round_record_path(round_number: int) -> str:
    return f"{REVIEW_RECORD_DIR}/round-{round_number:04d}-direction-review-record.cue"


def author_response_record_path(round_number: int, version: int) -> str:
    return f"{REVIEW_RECORD_DIR}/round-{round_number:04d}-author-v{version:04d}-response-record.cue"


def revision_delta_record_path(round_number: int) -> str:
    # Brief 19 component B: machine-readable v(N)->v(N+1) changelog the reform commits
    # in response to review round N (kernel vocabulary: revision delta).
    return f"{REVIEW_RECORD_DIR}/round-{round_number:04d}-revision-delta.cue"


def latest_review_round_record(repo: str | Path, patch_series_id_or_branch: str) -> dict[str, Any] | None:
    repo_path = Path(repo)
    branch = _patch_series_branch(patch_series_id_or_branch)
    patch_series_id = branch.removeprefix("ai-org/patch-series/")
    records = _review_round_records(repo_path, patch_series_id, branch)
    return records[-1] if records else None


def review_round_records(
    repo: str | Path,
    patch_series_id_or_branch: str,
    *,
    ref: str | None = None,
) -> list[dict[str, Any]]:
    """Read registered rounds from ``ref`` while retaining series identity."""

    repo_path = Path(repo)
    branch = _patch_series_branch(patch_series_id_or_branch)
    patch_series_id = branch.removeprefix("ai-org/patch-series/")
    return _review_round_records(repo_path, patch_series_id, ref or branch)


def has_review_round_record(repo: str | Path, patch_series_id_or_branch: str) -> bool:
    return latest_review_round_record(repo, patch_series_id_or_branch) is not None


def _next_round_number(repo: Path, branch: str, patch_series_id: str) -> int:
    existing = [
        _review_round_number(record)
        for record in _review_round_records(repo, patch_series_id, branch)
        if _review_round_number(record) > 0
    ]
    return max(existing, default=0) + 1


def _stamp_round_identity(record: dict[str, Any], reviewed_commit: str, base_commit: str) -> None:
    record["reviewed_commit"] = reviewed_commit
    record["base_commit"] = base_commit


def _review_round_number(record: Mapping[str, Any]) -> int:
    try:
        return int(record.get("round") or record.get("review_round") or 0)
    except (TypeError, ValueError):
        return 0


def _review_base_commit(repo: Path, branch: str) -> str:
    default = git_wrapper.default_branch(repo)
    return git_wrapper.merge_base(repo, default, branch) or git_wrapper.head_sha(repo, default) or ""


def _write_marker(
    repo: Path,
    branch: str,
    subject: str,
    body: str = "",
    *,
    files: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    if files:
        if patch_series_bodies.COVER_PATH in files:
            prepared_root = patch_series_bodies.prepare_patch_series_update(
                repo, branch, files[patch_series_bodies.COVER_PATH]
            )
            files = {**files, **prepared_root.files()}
        aggregate = review_bodies.prepare_marker_tree(files)
        return git_wrapper.commit_files(repo, branch, aggregate.files, subject=subject, body=body)
    return git_wrapper.commit_empty(repo, branch, subject, body=body)


def _post_round_mail_nonfatal(
    repo: Path,
    *,
    series_slug: str,
    branch: str,
    round_number: int,
    status: str,
    record_path: str,
    record_commit: str,
    record: Mapping[str, Any],
    ctx: org_log.RunContext,
) -> None:
    """Best-effort thread post after the binding round record commits."""
    # Memento: the paper is binding on the series branch; the list remembers
    # the thread but is never a decision source. Publication therefore follows
    # the record commit and failure can only warn, never change the round.
    resolved, resolved_by_construction, unresolved = _round_mail_counts(record)
    body_lines = [
        f"resolved: {resolved}",
        f"resolved_by_construction: {resolved_by_construction}",
        f"unresolved: {unresolved}",
        "",
        "Objections:",
    ]
    objection_lines = _round_mail_objection_lines(record)
    body_lines.extend(objection_lines or ["- none"])
    subject = f"{series_slug} round {round_number:04d} {status}"
    refs = {"series": branch, "record": record_path, "commit": record_commit}
    try:
        posted = mailing_list.post(
            repo,
            kind="review",
            subject=subject,
            body="\n".join(body_lines),
            refs=refs,
            ctx=ctx,
        )
        if posted.get("ok"):
            return
        error = str(posted.get("error") or "mailing-list round review post failed")
    except Exception as exc:  # noqa: BLE001 - thread publication must never break review.
        error = str(exc)
    org_log.debug_emit(
        "mailing_list.review.failed",
        {**refs, "round": round_number, "status": status, "error": error},
        ctx=ctx,
        severity="warning",
    )


def _round_mail_counts(record: Mapping[str, Any]) -> tuple[int, int, int]:
    mapping = record.get("cross_round_objection_mapping")
    mapping_entries = mapping if isinstance(mapping, list) else []
    construction_ids = {
        str(entry.get("prior_objection_id") or "")
        for entry in mapping_entries
        if isinstance(entry, Mapping)
        and (entry.get("status") or entry.get("outcome")) == "resolved_by_construction"
    }
    construction_ids.discard("")

    resolved_ids: set[str] = set()
    resolved_entries = record.get("resolved_objections")
    if isinstance(resolved_entries, list):
        for entry in resolved_entries:
            if not isinstance(entry, Mapping):
                continue
            objection_id = str(entry.get("objection_id") or "")
            if not objection_id or objection_id in construction_ids:
                continue
            if entry.get("status") not in {None, "", "resolved"}:
                continue
            resolved_ids.add(objection_id)

    unresolved_ids: set[str] = set()
    objections = record.get("objections")
    if isinstance(objections, list):
        for entry in objections:
            if not isinstance(entry, Mapping):
                continue
            if entry.get("status") == "open" and entry.get("type") == "blocking":
                objection_id = str(entry.get("objection_id") or "")
                if objection_id:
                    unresolved_ids.add(objection_id)
    return len(resolved_ids), len(construction_ids), len(unresolved_ids)


def _round_mail_objection_lines(record: Mapping[str, Any]) -> list[str]:
    lines: list[str] = []
    seen: set[str] = set()
    objections = record.get("objections")
    for entry in objections if isinstance(objections, list) else []:
        if not isinstance(entry, Mapping):
            continue
        objection_id = str(entry.get("objection_id") or "").strip()
        if not objection_id or objection_id in seen:
            continue
        seen.add(objection_id)
        claim = " ".join(str(entry.get("claim") or "").split()) or "(no claim recorded)"
        lines.append(f"- {objection_id}: {claim}")
    return lines


def _marker_body(summary: str, status: str, axis_reviews: list[AxisReview]) -> str:
    if status != "direction-ok":
        return summary
    trailers = _direction_ok_trailers(axis_reviews)
    if not trailers:
        return summary
    return "\n".join([summary.strip(), "", *trailers]).strip()


def _direction_ok_trailers(axis_reviews: list[AxisReview]) -> list[str]:
    axes = {axis.axis for axis in axis_reviews}
    trailers: list[str] = []
    for dimension in DIMENSIONS:
        if dimension.key in axes:
            trailers.append(f"Reviewed-by: AI Org {dimension.key} reviewer <{dimension.key}-reviewer@ai-org.invalid>")
    return trailers


def _marker_subject(status: str, round_number: int, *, branch: str = "", repo: Path | None = None) -> str:
    if status == "direction-ok":
        serial = git_wrapper.serial_for_ref(repo, branch) if repo is not None and branch else None
        serial = serial or (git_wrapper.next_serial(repo) if repo is not None else "")
        suffix = f" serial {serial}" if serial else ""
        return f"patch_series: direction-ok{suffix}"
    if status == "needs_revision":
        return f"patch_series: needs-revision round {round_number}"
    return "patch_series: nak"


def _serial_from_marker_subject(subject: str) -> str:
    marker = " serial "
    if marker not in subject:
        return ""
    return subject.rsplit(marker, 1)[-1].strip()


def _max_review_rounds() -> int:
    raw = os.environ.get("AI_ORG_RFC_MAX_REVIEW_ROUNDS", str(DEFAULT_MAX_REVIEW_ROUNDS))
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_MAX_REVIEW_ROUNDS
    return max(value, 1)


def _bounded_rounds_terminal_context(repo: Path, patch_series_id: str, branch: str) -> dict[str, Any] | None:
    """Backstop context when this round enters at/past the cap, else None.

    Memento (brief 32): a respondent must be heard before judgment. The
    previous form of this backstop nak'd INSTANTLY at review entry, copying
    the latest round's unresolved list verbatim without reviewing the current
    tree. Live: run 4 v6's reform had just revised the exact candidate node
    that round-5's objections anchored on (candidates step finally routed by
    brief 31), and the round-6 "review" nak'd in zero seconds at 19:15:02
    with round-5's stale list - the author responded and was never heard;
    runs 2 and 3 likely died the same way at their round-6 marks
    (2026-07-05). The cap bounds ROUNDS, it does not replace judgment.

    The stale-list instant-nak path is DELETED, not gated. This context only
    marks the round as terminal; the terminal round runs the normal fresh,
    current-body sharded review, and its OWN verdict decides:
    direction-ok proceeds, still-open blocking converts needs_revision into
    the consolidation nak carrying THIS round's judgment.

    Boundedness stays structural, not short-circuited: rounds 1..cap can each
    yield needs_revision (at most cap reforms); the round that enters with
    cap prior records is terminal - its only exits are direction-ok or nak,
    both of which write terminal markers that stop the pull selector
    (_is_terminal_patch_series) - so at most cap+1 reviews and cap reforms
    can ever run.
    """
    max_rounds = _max_review_rounds()
    previous = _review_round_records(repo, patch_series_id, branch)
    if len(previous) < max_rounds:
        return None
    history = [
        {
            "round": item.get("round"),
            "verdict": item.get("verdict"),
            "open_blocking_objection_ids": [
                str(objection.get("objection_id"))
                for objection in item.get("objections", [])
                if isinstance(objection, Mapping)
                and objection.get("type") == "blocking"
                and objection.get("resolution_authority") != "requester"
                and objection.get("status") in {"open", "carried-forward"}
            ],
        }
        for item in previous
    ]
    return {"max_rounds": max_rounds, "history": history}


def _review_round_records(repo: Path, patch_series_id: str, branch: str) -> list[dict[str, Any]]:
    records: list[tuple[int, dict[str, Any]]] = []
    for path in git_wrapper.tree_files(repo, branch, REVIEW_RECORD_DIR):
        name = Path(path).name
        if not (name.endswith("-direction-review-record.cue") or name.endswith("-direction-review-record.json")):
            continue
        try:
            number = int(name.split("-", 2)[1])
        except (IndexError, ValueError):
            continue
        try:
            parsed = review_bodies.read_record(repo, branch, path, review_bodies.CONSOLIDATION_RECIPE)
        except Exception:
            continue
        if isinstance(parsed, dict):
            parsed.setdefault("record_path", path)
            records.append((number, parsed))

    directory = repo / ".ai-org" / "review" / patch_series_id
    for path in directory.glob("round-*.json"):
        if "-author-" in path.stem:
            continue
        try:
            number = int(path.stem.removeprefix("round-"))
        except ValueError:
            continue
        if any(existing == number for existing, _record in records):
            continue
        try:
            parsed = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(parsed, dict):
            parsed.setdefault("record_path", str(path))
            parsed.setdefault("legacy_off_git_record", True)
            records.append((number, parsed))
    return [record for _, record in sorted(records)]


def _error_round_record(patch_series_id: str, branch: str, round_number: int, reason: str) -> dict[str, Any]:
    return {
        "patch_series_id": patch_series_id,
        "branch": branch,
        "round": round_number,
        "inputs": {
            "patch_series_path": patch_series_bodies.LEGACY_COVER_PATH,
            "technical_approach_path": patch_series_bodies.LEGACY_ROOT_APPROACH_PATH,
            "node_ids": [],
        },
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
        "git_result_marker": "patch_series: nak",
        "serial": "",
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
        resolution_authority="author",
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


def _questions_the_patch_must_answer(
    repo: Path,
    branch: str,
    accepted: list[Objection],
    proposals_by_id: Mapping[str, Mapping[str, str]],
    round_number: int,
) -> dict[str, Any]:
    """Committed obligation artifact: the questions the PATCH must answer.

    Memento (brief 27 + amendment, requester ruling): the word "deferred" prompts the
    implementer to deprioritize — names are prompts, and this artifact's primary
    READER is the patch author. Everything the author sees is obligation-form
    (must_resolve_in_patch), never "deferred"; reviewer-perspective bookkeeping
    (deferral_accepted in round records and the delta ledger) stays reviewer-side.
    Every question here is tied to a later gate: the code worker front-loads them
    into item windows and fails typed before submission without outcomes, and the
    acceptance judge is the backstop — deferred-and-forgotten is the documented
    failure mode this closes (Rust postponed-RFC criticism, canon 2026-07-05).
    """
    gate_module = importlib.import_module("ai_org.patchwork_queue.patch_series_gate")
    artifact: dict[str, Any] = {"questions_the_patch_must_answer": []}
    raw = git_wrapper.show_file(repo, branch, gate_module.PATCH_MUST_ANSWER_QUESTIONS_PATH)
    legacy_raw = git_wrapper.show_file(
        repo, branch, gate_module.LEGACY_PATCH_MUST_ANSWER_QUESTIONS_PATH
    )
    if raw is not None and legacy_raw is not None:
        raise contributor_handoff.AmbiguousRepresentation(
            "questions have both canonical and legacy representations"
        )
    raw = raw if raw is not None else legacy_raw
    if raw is not None:
        try:
            parsed = contributor_handoff.parse_questions(raw)
        except Exception:
            parsed = None
        if isinstance(parsed, dict) and isinstance(parsed.get("questions_the_patch_must_answer"), list):
            artifact["questions_the_patch_must_answer"] = [
                dict(entry) for entry in parsed["questions_the_patch_must_answer"] if isinstance(entry, Mapping)
            ]
    known = {str(entry.get("objection_id") or "") for entry in artifact["questions_the_patch_must_answer"]}
    for objection in accepted:
        if objection.objection_id in known:
            continue
        proposal = proposals_by_id.get(objection.objection_id, {})
        artifact["questions_the_patch_must_answer"].append(
            {
                "objection_id": objection.objection_id,
                "status": "must_resolve_in_patch",
                "executable_check": str(proposal.get("executable_check") or ""),
                "why_resolved_in_patch": str(proposal.get("defer_reason") or ""),
                # Amendment 3: elaborated by a dedicated bounded call on the patch
                # side (one question in, one plan out), committed on the
                # contribution branch; empty here by design.
                "if_check_fails_implement": "",
                "claim": objection.claim,
                "anchor_node_ids": list(objection.anchor_node_ids),
                "axis": objection.axis,
                "accepted_in_review_round": round_number,
            }
        )
    return artifact


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
        "resolution_authority": objection.resolution_authority,
        "status": objection.status,
        "reviewer_confidence": objection.reviewer_confidence,
        "reviewed_scope": objection.reviewed_scope,
    }


def _objection_record_with_successors(
    objection: Objection,
    successor_ids_by_objection: Mapping[str, list[str]],
) -> dict[str, Any]:
    record = _objection_record(objection)
    successors = successor_ids_by_objection.get(objection.objection_id)
    if successors:
        # Component C: a superseded objection records where its concern moved
        # (Gerrit preserves the thread across a complete rewrite).
        record["successor_node_ids"] = list(successors)
    return record


def _json_for_prompt(value: object) -> str:
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True)
