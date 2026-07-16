"""patch series LINEAGE: Precedent-informed canonical network node model.

Memento: A/B experiment outcome.
- lineage_b is promoted to the canonical implementation after replacing the
  lineage_a experiment.
- Implementations consult the org precedent store before behavior changes.
- Influencing facets:
  required all props payload artifact tolerance
  (ai-org-bootstrap-codex@871c99a; ai-org-bootstrap-codex@2f5f13b);
  schema field mode naming discipline
  (ai-org-bootstrap-codex@2f5f13b; ai-org-bootstrap-codex@67563c5);
  child branch metadata inheritance hazard
  (ai-org-bootstrap-codex@67563c5; Gerrit NoteDb-style separation);
  group tree merges implementations not doc nodes
  (ai-org-bootstrap-codex@this-commit);
  codex output schema safe subset
  (ai-org-bootstrap-codex@345bc17; tests/test_codex_output_schema_guard.py).

Memento: the Linux mirror — seven phenomena this module lives inside, none of
which network should try to "solve" (evidence: linux_mirror_research REPORT.md,
2026-07-03, grounded in Documentation/process/* and upstream commits). The Linux
kernel community — the most time-tested large engineering organization on this
substrate — has all seven, manages them by convention, and deliberately did NOT
mechanize the painful edges. Attempts to solve a managed-by-convention problem
with machinery is how SAP happens (reproducing ledgers the substrate already
obsoletes). Do not reintroduce.

P1 IDENTITY: no universal logical-change id exists in Linux; Gerrit Change-Id
   was explicitly REJECTED (checkpatch strips it). Weak composable anchors
   (Message-ID, Link:, Fixes:, serial at ratification) are canon. Two-stage
   identity here: nest position while forming, serial at direction-ok. Do not
   build a change-id registry.
P2 DECOMPOSITION: the AUTHOR decomposes (cover letter + self-contained,
   bisectable patches); no central planner hands out sub-orders. Here: the
   network group is the author side; the truth-form is a dependency NETWORK.
   Node directories stay at one level under sub/<child_key>; split children are
   peer nodes, never nested authority. A parent writes each child's outer form
   as a REQUEST (the front-door form — a child must read as a request that
   could have arrived on its own), children deepen their own interior.
P3 PATH OWNERSHIP: MAINTAINERS-style path scope is the default; shared
   prerequisites travel as immutable topic branches/tags both sides merge;
   treewide edits are author-side mechanical changes (Coccinelle-shaped),
   never children hand-editing shared files. Dependency edges are declared only
   in each consumer's `edges` block; code validates and projects that network
   but never derives or merges a second dependency source. Aggregates,
   topological order, readiness, critical path, and nesting views are generated
   projections, never hand-edited.
P4 SERIALIZATION: one final integration point scales via delegation hierarchy
   (13k changesets/release through ~100 subsystem trees). Ordering is computed
   from active dependency edges and current state, not stored as a second
   vocabulary. The open sore is HUMAN review bandwidth — not a network defect
   to fix here; it is the org's differentiation target (mechanism carries
   capability, carriers scale).
P5 STALENESS: in-flight work going stale is the NORMAL state, not an incident.
   Canon: unmerged work continuously rebases onto the moved upstream
   ("developers must update unmerged patches to the current kernel").
   Acknowledgement of a parent contract change = ancestry (merge-base
   --is-ancestor); staleness = merge-base comparison. NO amendment ledger, NO
   acknowledgement protocol — git history is the amendment record. Mutability
   boundary: private child branches rebase freely; history merged into the
   group tree is public and is never rewritten.
P6 AUTHORITY: contributors cannot change conventions unilaterally; disputes
   resolve socially and hierarchically (escalation = synthetic review round;
   root is final arbiter). When trust breaks, resolution is externalization
   (supersedes / abandon), not machinery — bcachefs precedent.
P7 STAMPED UNITS: form validation (field registry) is an intake filter, NOT
   proof of quality — "checkpatch is not always right". Semantic quality of
   template-stamped nodes rests on the stamping author's signer responsibility
   (must understand and defend every stamped node) plus review sampling. Do
   not attempt a semantic validator that "proves" stamped content. The gate is
   total: every instantaneous edge evaluation terminates; unbounded counters
   and string flags live in append-only patchwork-check-events.jsonl logs, and eligibility
   may re-arm whenever projected state changes.
"""
from __future__ import annotations

import copy
import hashlib
import importlib
import json
import re
import shutil
import ast
import subprocess
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, Callable, Mapping

from ai_org.body_codec import BodyCodecClient, CodecFailure
from ai_org.schema_lifecycle import generated_schema_attr
import ai_org.log as org_log
from ai_org import git_wrapper, mailing_list, network_bodies, patch_series_bodies, review_bodies
import ai_org.patch_author.announcements as authoring_announcements
import ai_org.patch_author.producer_lifecycle as producer_lifecycle
import ai_org.patchwork_queue.codex_exec as codex_exec
import ai_org.patchwork_queue.review as review_module
from ai_org import patchwork_check_stream
from ai_org.patchwork_queue.field_registry import empty_user_experience_requirements


WORK_ORDER_PREFIX = "ai-org/patch-series/"
LEDGER_PATH = "series-coverage-ledger.json"
# Brief 27 [PROVISIONAL name]: obligation-form, implementer-perspective — requester:
# "Deferral to kaku to Patch_author mo mushi suru" (a name is a prompt; the
# artifact's primary READER is the patch author, so it reads as work the patch
# MUST do, never as parked questions). Should join the blank-probe vocabulary
# checks later like the other committed names.
PATCH_MUST_ANSWER_QUESTIONS_PATH = "questions-the-patch-must-answer.cue"
LEGACY_PATCH_MUST_ANSWER_QUESTIONS_PATH = "questions-the-patch-must-answer.json"
# Brief 27 amendment 4 [PROVISIONAL name]: implementation experience returning as
# reviewable evidence when a must-answer check failure invalidates committed
# direction nodes (canon: TC39 stage regression; Rust stabilization -> new RFC).
IMPLEMENTATION_EXPERIENCE_REPORT_PATH = "implementation-experience-report.cue"
LEGACY_IMPLEMENTATION_EXPERIENCE_REPORT_PATH = "implementation-experience-report.json"
METADATA_PATH = "patch-series-metadata.json"
NODE_MANIFEST_PATH = "patch-series-manifest.json"
STATUS_PATH = "patch-queue-status-rollup.json"
SUBDIR = "sub"
MAX_RESOLUTION_DEPTH = 64
MAX_WHEN_LENGTH = 4096
MAX_WHEN_NESTING = 128
MAX_WHEN_TERMS = 256
MAX_WHEN_INTEGER_DIGITS = 18
MAX_INGESTION_BYTES = 4 * 1024 * 1024
MAX_JSON_DEPTH = 64
LIFECYCLE_STATES = ("posted_to_mailing_list", "ready_for_patch_authoring", "claimed_by_patch_author", "submitted_for_maintainer_review", "merged_into_subsystem_tree")
LEGACY_ACTIVE_STATES = {
    "active": "ready_for_patch_authoring",
    "request-only": "posted_to_mailing_list",
    # Pre-rename lifecycle vocabulary (naming-is-prompt wave, 2026-07-04):
    # legacy trees keep old names on disk; normalization is migration support.
    "requested": "posted_to_mailing_list",
    "elaborated": "ready_for_patch_authoring",
    "claimed": "claimed_by_patch_author",
    "delivered": "submitted_for_maintainer_review",
    "accepted": "merged_into_subsystem_tree",
    "work_request_submitted": "posted_to_mailing_list",
    "ready_for_claim": "ready_for_patch_authoring",
    "claimed_by_contributor": "claimed_by_patch_author",
    "submitted_for_acceptance": "submitted_for_maintainer_review",
    "acceptance_criteria_passed": "merged_into_subsystem_tree",
}
DECLARED_PATCHWORK_CHECKS_FIELD = "declared_patchwork_checks"
PATCHWORK_CHECK_TYPES = ("counter", "text")
PATCHWORK_CHECK_DEFAULTS = {"counter": 0, "text": ""}
SPINE_CONTRACT_REF_FIELDS = {
    "art_bible_path": "spine/art-bible.json",
    "asset_manifest_schema_path": "spine/asset-manifest.schema.json",
}
MIGRATION_ARTIFACT_LADDERS = {
    NODE_MANIFEST_PATH: ("patch-series-manifest.json", "network-node-manifest.json", "lineage-node.json"),
    "maintainer-series-request.json": ("maintainer-series-request.json", "parent-work-request.json", "request.json"),
    patch_series_bodies.LEGACY_COVER_PATH: (patch_series_bodies.LEGACY_COVER_PATH, "executable-work-order.json", "rfc.json"),
    METADATA_PATH: (METADATA_PATH, "work-order-metadata.json", "rfc-metadata.json"),
    "patchwork-check-events.jsonl": ("patchwork-check-events.jsonl", "state-variable-events.jsonl", "state-events.jsonl"),
    patch_series_bodies.LEGACY_ROOT_APPROACH_PATH: (patch_series_bodies.LEGACY_ROOT_APPROACH_PATH, "technical-approach.json"),
}
MIGRATION_ARTIFACT_KINDS = {
    NODE_MANIFEST_PATH: "manifest",
    "maintainer-series-request.json": "request",
    patch_series_bodies.LEGACY_COVER_PATH: "cover_letter",
    METADATA_PATH: "metadata",
    "patchwork-check-events.jsonl": "patchwork_check_events",
    patch_series_bodies.LEGACY_ROOT_APPROACH_PATH: "technical_approach",
}
MIGRATION_ARTIFACT_ALIASES = {
    alias
    for ladder in MIGRATION_ARTIFACT_LADDERS.values()
    for alias in ladder
}

STATELESS_PRODUCER_PAIR_VET_ROUTES = frozenset(
    {
        "already_refined",
        "right_sized",
        "no_network_discovery",
        "ordinary_refinement",
        "split_preparation",
        "stale_decision",
        "rebaseline_decision",
        "stamping",
        "elaboration_preparation",
        "scope_absent_manifest_fallback",
    }
)

PRODUCER_PAIR_VET_ROUTES = STATELESS_PRODUCER_PAIR_VET_ROUTES | frozenset(
    {
        "producer_promise_initialization",
        "producer_task_binding",
        "producer_code_authoring",
        "producer_feedback_authoring",
        "producer_functional_acceptance",
        "subsystem_integration",
        "mainline_integration",
    }
)

SCOPE_FORMATION_ROUTES = frozenset(
    {"ordinary_refinement", "right_sized", "split_preparation"}
)

# The Migrated Lifecycle Readiness Gate is closed over every matrix delivered
# by the preceding cohorts.  Its exact, immutable result is the durable
# activation boundary: callers cannot opt into, disable, or partially mutate
# the producer-aware route after import.
_MIGRATED_LIFECYCLE_READINESS_NAMES = (
    "formation",
    "closure",
    "lifecycle",
    "acceptance",
    "integration",
    "registry",
    "legacy",
    "atomicity",
)
MIGRATED_LIFECYCLE_READINESS_MATRIX = MappingProxyType(
    {name: True for name in _MIGRATED_LIFECYCLE_READINESS_NAMES}
)

_MIGRATED_LIFECYCLE_GATE_NAMES = (
    "discovery",
    "production",
    "handoff",
    "acceptance",
    "resolution",
    "integration",
)
MIGRATED_LIFECYCLE_GATES = MappingProxyType(
    {name: True for name in _MIGRATED_LIFECYCLE_GATE_NAMES}
)


def _migrated_lifecycle_readiness_gate(
    matrix: Mapping[str, object],
) -> bool:
    """Accept only the exact all-pass readiness matrix for the one cutover."""

    return set(matrix) == set(_MIGRATED_LIFECYCLE_READINESS_NAMES) and all(
        matrix[name] is True for name in _MIGRATED_LIFECYCLE_READINESS_NAMES
    )


if not _migrated_lifecycle_readiness_gate(
    MIGRATED_LIFECYCLE_READINESS_MATRIX
):
    raise RuntimeError("migrated lifecycle readiness gate is incomplete")
if not all(MIGRATED_LIFECYCLE_GATES.values()):
    raise RuntimeError("migrated lifecycle activation must open as one unit")

# Compatibility observation for existing callers.  Publication does not
# branch on this value: the cutover has happened and is not a runtime switch.
DEFAULT_PRODUCER_AWARE_CUTOVER = True

ORDINARY_NETWORK_TRANSITION_ROUTES = frozenset(
    {
        "ordinary_refinement",
        "split_preparation",
        "stamping",
        "elaboration_preparation",
        "stale_marking",
        "rebaseline",
        "stale_revalidation",
    }
)

# Escalation and rebaseline remain one protocol, but they no longer define the
# producer-aware release boundary.  The readiness matrix above activates every
# ordinary lifecycle route together.
MIGRATED_ESCALATION_PUBLICATION_ROUTES = frozenset(
    {"stale_marking", "rebaseline"}
)

_TRANSITION_VET_ROUTE = {
    "ordinary_refinement": "ordinary_refinement",
    "split_preparation": "split_preparation",
    "stamping": "stamping",
    "elaboration_preparation": "elaboration_preparation",
    "stale_marking": "stale_decision",
    "rebaseline": "rebaseline_decision",
    "stale_revalidation": "stale_decision",
}


@dataclass(frozen=True, slots=True)
class MigratedLifecycleActivation:
    """One closed dispatch decision for a frozen root generation.

    The canonical scope body is the durable activation marker.  A complete v2
    cohort without that body is deliberately blocked, while historical roots
    remain wholly on their compatibility dispatcher.  All migrated lifecycle
    gates open together, and no per-caller flag can alter that boundary.
    """

    dispatcher: str
    activated: bool
    authorable: bool
    open_gates: tuple[str, ...] = ()

    @property
    def activation_count(self) -> int:
        """The only durable activation cardinalities are zero and one."""

        return int(self.activated)


def migrated_lifecycle_activation(
    snapshot: patch_series_bodies.RootGenerationSnapshot,
) -> MigratedLifecycleActivation:
    """Select migrated, legacy, or blocked dispatch from one frozen snapshot."""

    if snapshot.generation != patch_series_bodies.ROOT_GENERATION_V2:
        if snapshot.lifecycle_ready:
            return MigratedLifecycleActivation("legacy", False, True)
        return MigratedLifecycleActivation("blocked", False, False)
    if (
        snapshot.disposition != patch_series_bodies.ROOT_DISPOSITION_V2_READY
        or not _migrated_lifecycle_readiness_gate(
            MIGRATED_LIFECYCLE_READINESS_MATRIX
        )
    ):
        return MigratedLifecycleActivation("blocked", False, False)
    return MigratedLifecycleActivation(
        "producer-aware-v2",
        True,
        True,
        tuple(
            name
            for name in _MIGRATED_LIFECYCLE_GATE_NAMES
            if MIGRATED_LIFECYCLE_GATES[name]
        ),
    )


@dataclass(frozen=True, slots=True)
class FrozenNetworkSourceVector:
    """Canonical Network Source Vector for one generation-specific decision."""

    route: str
    branch: str
    frozen_root_oid: str
    root_generation: str
    root_sha256: str = ""
    scope_decomposition_sha256: str = ""
    source_path: str = ""
    context: str = ""
    canonical_digest: str = ""
    root_snapshot: patch_series_bodies.RootGenerationSnapshot | None = field(
        default=None,
        repr=False,
        compare=False,
    )

    def as_dict(self) -> dict[str, str]:
        return {
            "route": self.route,
            "branch": self.branch,
            "frozen_root_oid": self.frozen_root_oid,
            "root_generation": self.root_generation,
            "root_sha256": self.root_sha256,
            "scope_decomposition_sha256": self.scope_decomposition_sha256,
            "representation": self.root_generation,
            "source_path": self.source_path,
            "source_oid": self.frozen_root_oid,
            "context": self.context,
            "canonical_digest": self.canonical_digest,
        }


# Public doctrine name; retain the earlier symbol for compatibility.
CanonicalNetworkSourceVector = FrozenNetworkSourceVector


@dataclass(frozen=True, slots=True)
class PublicationDiagnostic:
    """Stable validation coordinates returned without publishing a tree."""

    route: str
    frozen_root_oid: str
    cue_location: str
    rule: str
    goal_id: str = ""
    obligation_id: str = ""
    field: str = ""
    detail: str = ""
    context: str = ""
    status: str = ""

    def as_dict(self) -> dict[str, str]:
        value = {
            "route": self.route,
            "frozen_root_oid": self.frozen_root_oid,
            "cue_location": self.cue_location,
            "rule": self.rule,
            "goal_id": self.goal_id,
            "obligation_id": self.obligation_id,
            "field": self.field,
            "detail": self.detail,
            "context": self.context,
        }
        if self.status:
            value["status"] = self.status
        return value


@dataclass(frozen=True, slots=True)
class AuthorabilityDecision:
    """Result of the common producer-pair and exact-scope pre-decision vet."""

    vet_passed: bool
    authorable: bool
    source: FrozenNetworkSourceVector
    diagnostic: PublicationDiagnostic | None = None
    transition_allowed: bool = False

    @property
    def blocked(self) -> bool:
        return not self.vet_passed or (
            not self.authorable and not self.transition_allowed
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "vet_passed": self.vet_passed,
            "authorable": self.authorable,
            "source": self.source.as_dict(),
            "diagnostic": self.diagnostic.as_dict() if self.diagnostic else None,
            "transition_allowed": self.transition_allowed,
        }


class _AuthorabilityBlocked(RuntimeError):
    """Carry a typed vet result through an internal projection boundary."""

    def __init__(self, decision: AuthorabilityDecision):
        super().__init__(decision.diagnostic.detail if decision.diagnostic else "")
        self.decision = decision


@dataclass(frozen=True, slots=True)
class PreparedNetworkTransition:
    """One immutable successor tree and its generation-specific release gate."""

    route: str
    source: CanonicalNetworkSourceVector
    publication: git_wrapper.PreparedNetworkBodySet | None
    diagnostic: PublicationDiagnostic | None = None
    prepared_source_vector_sha256: str = ""
    prepared_generation: str = ""

    @property
    def prepared(self) -> bool:
        return self.publication is not None

    @property
    def releasable(self) -> bool:
        return self.publication is not None and self.diagnostic is None


@dataclass(frozen=True, slots=True)
class NetworkTransitionPublicationResult:
    """Structured result of releasing one prepared successor through CAS."""

    route: str
    source: CanonicalNetworkSourceVector
    commit_oid: str = ""
    diagnostic: PublicationDiagnostic | None = None
    expected_ref_oids: tuple[tuple[str, str], ...] = ()
    resulting_ref_oids: tuple[tuple[str, str], ...] = ()

    @property
    def ok(self) -> bool:
        return bool(self.commit_oid) and self.diagnostic is None


# Memento: the child kind enum lived one day. It was unverified
# vibes-classification with zero consumers; as a discriminator it would have
# broken patch series form invariance. Labels must not substitute for content. Routing is
# contention with exit filtering: any patch author may take any open leaf, and
# acceptance proves capability at delivery. Do not reintroduce work-type labels.

def build_lineage_split_schema() -> dict[str, Any]:
    """Projection shape for the deterministic dependency-component split.

    This is no longer a model-output schema. It documents the carrier emitted
    by the graph partition and, critically, carries the component plan slice
    instead of a second string ownership ledger.
    """
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["split_mode", "rationale", "children"],
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
                        "edges",
                        "summary",
                        "acceptance_criteria",
                        "functional_check",
                        "patch_plan",
                        "systems",
                        "ux_acceptance_tests",
                        "risks",
                    ],
                    "properties": {
                        "child_key": {"type": "string", "description": "Stable local key unique within this split. MUST match ^[a-z0-9_]+$ - lowercase letters, digits, and underscores ONLY; NO hyphens, spaces, or uppercase (e.g. authority_closure, not authority-closure)."},
                        "title": {"type": "string", "description": "Human-readable child patch series title."},
                        "stage_name": {"type": "string", "description": "Rolling-wave stage this child belongs to."},
                        "edges": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["type", "to", "with", "state", "or_group", "when", "reason"],
                                "properties": {
                                    "type": {"enum": ["depends", "excludes", "part_of"]},
                                    "to": {"type": "string", "description": "Target node key or serial for depends/part_of; empty otherwise."},
                                    "with": {"type": "string", "description": "Excluded peer key for excludes; empty otherwise."},
                                    "state": {"type": "string", "description": "Lifecycle state or target-declared milestone; empty defaults to accepted for depends."},
                                    "or_group": {"type": "string", "description": "Shared OR group id; empty means AND."},
                                    "when": {"type": "string", "description": "Total state predicate; empty means always active."},
                                    "reason": {"type": "string", "description": "Required reason for depends/excludes; optional for part_of."},
                                },
                            },
                            "description": "Single authoritative dependency network declaration.",
                        },
                        "summary": {"type": "string", "description": "Single-concern scope summary."},
                        "acceptance_criteria": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Child acceptance criteria copied from the deliberated approach.",
                        },
                        "functional_check": {"type": "string", "description": "How functional_check verifies this child."},
                        "patch_plan": {
                            "type": "object",
                            "description": "CUE projection containing only this dependency component's work items.",
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
        },
    }


_SCHEMA_BUILDERS = {
    "LINEAGE_SPLIT_SCHEMA": build_lineage_split_schema,
}
# The split is deterministic; this carrier is not advertised to Codex.
_CODEX_OUTPUT_SCHEMA_BUILDERS: dict[str, Callable[[], dict[str, Any]]] = {}


def __getattr__(name: str) -> Any:
    return generated_schema_attr(name, _SCHEMA_BUILDERS)


def vet_producer_pair_closure(
    repo: str | Path,
    branch: str,
    route: str,
    *,
    technical_approach: Mapping[str, Any] | None = None,
    frozen_root_oid: str | None = None,
) -> AuthorabilityDecision:
    """Vet producer-pair closure and exact scope before a network decision.

    Historical roots without producer fields retain their established network
    behavior.  A root that carries any producer-aware field must satisfy the
    complete canonical validator. A newly formed cohort may only perform the
    scope-forming network transition; canonical scope opens all later routes.
    The function freezes one OID, performs no writes, and returns structured
    decision metadata.
    """

    if route not in PRODUCER_PAIR_VET_ROUTES:
        raise ValueError(f"unknown producer-pair vet route: {route}")
    repo_path = Path(repo).resolve()
    normalized = _patch_series_branch(branch)
    frozen = frozen_root_oid or git_wrapper.head_sha(repo_path, normalized) or ""
    if not frozen:
        source = FrozenNetworkSourceVector(route, normalized, "", "missing")
        return AuthorabilityDecision(
            False,
            False,
            source,
            PublicationDiagnostic(
                route,
                "",
                "root",
                "missing-frozen-root",
                goal_id="root",
                detail="the network decision requires an immutable root OID",
                context=source.context,
            ),
        )

    snapshot = patch_series_bodies.classify_root_generation(repo_path, frozen)
    activation = migrated_lifecycle_activation(snapshot)
    source = FrozenNetworkSourceVector(
        route=route,
        branch=normalized,
        frozen_root_oid=frozen,
        root_generation=snapshot.generation,
        root_sha256=_root_source_sha256(snapshot),
        source_path=snapshot.source_path or "",
        context=snapshot.context or "",
        canonical_digest=snapshot.canonical_digest or "",
        root_snapshot=snapshot,
    )
    # Network-only historical fixtures and imported node trees predate root
    # cohorts entirely.  With no root generation member there is no producer
    # claim to vet; retain their established read-only decision behavior.
    if not snapshot.has_generation_members and technical_approach is None:
        return AuthorabilityDecision(True, True, source)
    candidate: Mapping[str, Any] | None = technical_approach
    if candidate is not None:
        try:
            frozen_candidate = _frozen_technical_approach(snapshot)
        except CodecFailure as exc:
            goal_id, obligation_id = _affected_producer_coordinate(
                None, exc.cue_path or exc.json_pointer
            )
            return AuthorabilityDecision(
                False,
                False,
                source,
                PublicationDiagnostic(
                    route,
                    frozen,
                    exc.cue_path or exc.json_pointer or "root",
                    exc.rule_id or exc.code,
                    goal_id=goal_id,
                    obligation_id=obligation_id,
                    context=exc.context_id or source.context,
                ),
            )
        except (ValueError, TypeError) as exc:
            return AuthorabilityDecision(
                False,
                False,
                source,
                PublicationDiagnostic(
                    route,
                    frozen,
                    "technical-approach-plan",
                    "frozen-root-candidate-unreadable",
                    goal_id="root.problem.goals",
                    detail=str(exc),
                    context=source.context,
                ),
            )
        if candidate != frozen_candidate:
            return AuthorabilityDecision(
                False,
                False,
                source,
                PublicationDiagnostic(
                    route,
                    frozen,
                    "technical-approach-plan",
                    "frozen-root-candidate-mismatch",
                    goal_id="root.problem.goals",
                    detail="decision input does not match the frozen root bytes",
                    context=source.context,
                ),
            )
    if candidate is None and snapshot.generation == patch_series_bodies.ROOT_GENERATION_V2:
        raw = snapshot.raw(patch_series_bodies.ROOT_APPROACH_PATH)
        assert raw is not None
        try:
            parsed = BodyCodecClient().parse(
                patch_series_bodies.ROOT_APPROACH_CONTEXT,
                raw,
                expected=patch_series_bodies.ROOT_APPROACH_CONTRACT,
            )
        except CodecFailure as exc:
            goal_id, obligation_id = _affected_producer_coordinate(
                None, exc.cue_path or exc.json_pointer
            )
            return AuthorabilityDecision(
                False,
                False,
                source,
                PublicationDiagnostic(
                    route,
                    frozen,
                    exc.cue_path or exc.json_pointer or "root",
                    exc.rule_id or exc.code,
                    goal_id=goal_id,
                    obligation_id=obligation_id,
                    context=exc.context_id or source.context,
                ),
            )
        candidate = parsed if isinstance(parsed, Mapping) else None
    elif candidate is None and snapshot.lifecycle_ready:
        try:
            candidate = snapshot.technical_approach()
        except (ValueError, TypeError):
            candidate = None

    if not isinstance(candidate, Mapping):
        if snapshot.lifecycle_ready:
            return AuthorabilityDecision(True, True, source)
        return AuthorabilityDecision(
            False,
            False,
            source,
            PublicationDiagnostic(
                route,
                frozen,
                "root",
                snapshot.disposition,
                goal_id="root",
                detail=snapshot.diagnostic,
                context=source.context,
            ),
        )

    problem = candidate.get("problem")
    producer_problem = problem if isinstance(problem, Mapping) else candidate
    producer_fields = {
        "goals",
        "deliverable_requirements",
        "production_obligations",
        "patch_plan",
    }
    producer_aware = bool(producer_fields.intersection(producer_problem)) and bool(
        {"deliverable_requirements", "production_obligations"}.intersection(
            producer_problem
        )
    )
    if not producer_aware:
        if snapshot.lifecycle_ready:
            return AuthorabilityDecision(True, True, source)
        return AuthorabilityDecision(
            False,
            False,
            source,
            PublicationDiagnostic(
                route,
                frozen,
                "root.problem",
                "producer-pair-required",
                goal_id="root",
                detail="canonical root cohort v2 must carry the complete producer contract",
                context=source.context,
            ),
        )

    # Import dynamically: receive deliberately imports this module only at runtime
    # on other paths, and the common vet must not create an import cycle.
    receive_module = importlib.import_module("ai_org.patchwork_queue.receive")

    try:
        receive_module.validate_canonical_root_technical_approach(candidate)
    except receive_module.CanonicalRootPreviewError as exc:
        return AuthorabilityDecision(
            False,
            False,
            source,
            PublicationDiagnostic(
                route,
                frozen,
                exc.cue_path,
                exc.rule_id,
                goal_id=exc.goal_id,
                obligation_id=exc.obligation_id,
                field=exc.field,
                detail=exc.detail,
                context=source.context,
            ),
        )

    if (
        snapshot.generation == patch_series_bodies.ROOT_GENERATION_V2
        and snapshot.disposition == patch_series_bodies.ROOT_DISPOSITION_V2_NOT_READY
    ):
        if route in SCOPE_FORMATION_ROUTES:
            # This is the sole pre-authorability exception: a vetted
            # three-member root may form the network publication that writes
            # canonical scope. Every later lifecycle route remains blocked.
            return AuthorabilityDecision(
                True, False, source, transition_allowed=True
            )
        return AuthorabilityDecision(True, False, source)

    if snapshot.generation == patch_series_bodies.ROOT_GENERATION_V2:
        if not activation.activated:
            return AuthorabilityDecision(
                False,
                False,
                source,
                PublicationDiagnostic(
                    route,
                    frozen,
                    patch_series_bodies.SCOPE_DECOMPOSITION_PATH,
                    "migrated-lifecycle-readiness-gate",
                    goal_id="root.problem.goals",
                    detail="the complete migrated lifecycle matrix is not active",
                    context=patch_series_bodies.SCOPE_DECOMPOSITION_CONTEXT,
                ),
            )
        try:
            decomposition = patch_series_bodies.project_series_scope_decomposition(
                repo_path, frozen
            )
        except patch_series_bodies.SeriesScopeDecompositionError as exc:
            goal_id, obligation_id = _affected_producer_coordinate(
                candidate, exc.coordinate
            )
            return AuthorabilityDecision(
                False,
                False,
                source,
                PublicationDiagnostic(
                    route,
                    frozen,
                    exc.coordinate,
                    exc.rule_id,
                    goal_id=goal_id,
                    obligation_id=obligation_id,
                    detail=exc.detail,
                    context=patch_series_bodies.SCOPE_DECOMPOSITION_CONTEXT,
                ),
            )
        except CodecFailure as exc:
            goal_id, obligation_id = _affected_producer_coordinate(
                candidate, exc.cue_path or exc.json_pointer
            )
            return AuthorabilityDecision(
                False,
                False,
                source,
                PublicationDiagnostic(
                    route,
                    frozen,
                    exc.cue_path or exc.json_pointer or "series-scope-decomposition",
                    exc.rule_id or exc.code,
                    goal_id=goal_id,
                    obligation_id=obligation_id,
                    context=(
                        exc.context_id
                        or patch_series_bodies.SCOPE_DECOMPOSITION_CONTEXT
                    ),
                ),
            )
        if (
            decomposition.frozen_oid != frozen
            or decomposition.canonical_root_sha256 != source.root_sha256
        ):
            return AuthorabilityDecision(
                False,
                False,
                source,
                PublicationDiagnostic(
                    route,
                    frozen,
                    "series-scope-decomposition.canonical_root_sha256",
                    "frozen-root-digest-mismatch",
                    goal_id="root.problem.goals",
                    detail="scope decomposition was not derived from the frozen canonical root",
                    context=patch_series_bodies.SCOPE_DECOMPOSITION_CONTEXT,
                ),
            )
        source = FrozenNetworkSourceVector(
            route=route,
            branch=normalized,
            frozen_root_oid=frozen,
            root_generation=snapshot.generation,
            root_sha256=decomposition.canonical_root_sha256,
            scope_decomposition_sha256=decomposition.body_sha256,
            source_path=snapshot.source_path or "",
            context=snapshot.context or "",
            canonical_digest=snapshot.canonical_digest or "",
            root_snapshot=snapshot,
        )

    return AuthorabilityDecision(
        True,
        activation.activated,
        source,
    )


def vet_stateless_authorability(
    repo: str | Path,
    branch: str,
    route: str,
    *,
    technical_approach: Mapping[str, Any] | None = None,
    frozen_root_oid: str | None = None,
) -> AuthorabilityDecision:
    """Apply the common vet through the exact stateless route boundary."""

    if route not in STATELESS_PRODUCER_PAIR_VET_ROUTES:
        raise ValueError(f"unknown stateless authorability route: {route}")
    optional: dict[str, Any] = {}
    if technical_approach is not None:
        optional["technical_approach"] = technical_approach
    if frozen_root_oid is not None:
        optional["frozen_root_oid"] = frozen_root_oid
    decision = vet_producer_pair_closure(
        repo,
        branch,
        route,
        **optional,
    )
    normalized = _patch_series_branch(branch)
    source = decision.source
    mismatched_fields = []
    if source.route != route:
        mismatched_fields.append("route")
    if source.branch != normalized:
        mismatched_fields.append("branch")
    if frozen_root_oid is not None and source.frozen_root_oid != frozen_root_oid:
        mismatched_fields.append("frozen_root_oid")
    if not mismatched_fields:
        return decision

    expected_oid = frozen_root_oid or source.frozen_root_oid
    if source.frozen_root_oid == expected_oid:
        bound_source = replace(source, route=route, branch=normalized)
    else:
        # None of the returned digests or the cached classifier snapshot can be
        # evidence for a different OID.  Preserve only the diagnostic context
        # while binding the closed decision to the source the caller requested.
        bound_source = FrozenNetworkSourceVector(
            route=route,
            branch=normalized,
            frozen_root_oid=expected_oid,
            root_generation="",
            context=source.context,
        )
    return AuthorabilityDecision(
        False,
        False,
        bound_source,
        PublicationDiagnostic(
            route,
            expected_oid,
            "authorability_decision.source",
            "stateless-source-vector-binding",
            goal_id="root.problem.goals",
            field=",".join(mismatched_fields),
            detail=(
                "the common producer-pair vet returned a decision that is not "
                "bound to the requested stateless route and frozen root"
            ),
            context=source.context,
        ),
    )


def _root_source_sha256(
    snapshot: patch_series_bodies.RootGenerationSnapshot,
) -> str:
    """Hash the generation-selected approach bytes from the frozen root."""

    for path in (
        patch_series_bodies.ROOT_APPROACH_PATH,
        patch_series_bodies.LEGACY_ROOT_APPROACH_PATH,
    ):
        raw = snapshot.raw(path)
        if raw is not None:
            return hashlib.sha256(raw).hexdigest()
    return ""


def _vetted_root_snapshot(
    repo: str | Path,
    source: FrozenNetworkSourceVector,
) -> patch_series_bodies.RootGenerationSnapshot:
    """Reuse the classifier result bound to a vetted frozen source vector.

    Compatibility callers may construct source vectors directly. Those vectors
    have no in-memory snapshot and are classified once at their first root-read
    boundary. A vector returned by ``vet_producer_pair_closure`` always carries
    the exact snapshot that established its serialized identity.
    """

    snapshot = source.root_snapshot
    if snapshot is None:
        return patch_series_bodies.classify_root_generation(
            repo, source.frozen_root_oid
        )
    if (
        snapshot.frozen_oid != source.frozen_root_oid
        or snapshot.generation != source.root_generation
        or (snapshot.source_path or "") != source.source_path
        or (snapshot.context or "") != source.context
        or (snapshot.canonical_digest or "") != source.canonical_digest
    ):
        raise RuntimeError("vetted root snapshot differs from its source vector")
    return snapshot


def vetted_root_snapshot(
    repo: str | Path,
    source: FrozenNetworkSourceVector,
) -> patch_series_bodies.RootGenerationSnapshot:
    """Return the classifier snapshot retained by a common route decision.

    Integration readers use this public boundary instead of classifying the
    same frozen root a second time.  Directly constructed compatibility source
    vectors retain the established classify-on-first-read behavior.
    """

    return _vetted_root_snapshot(repo, source)


def _frozen_technical_approach(
    snapshot: patch_series_bodies.RootGenerationSnapshot,
) -> dict[str, Any]:
    """Read a supplied candidate's comparison body from its frozen tree.

    A complete v2 preview is intentionally not lifecycle-ready until scope is
    published, so ``RootGenerationSnapshot.technical_approach`` cannot read it.
    The common route vet still has to compare any caller-supplied candidate
    with those canonical bytes before allowing a scope-forming transition.
    """

    if snapshot.generation != patch_series_bodies.ROOT_GENERATION_V2:
        return snapshot.technical_approach()
    raw = snapshot.raw(patch_series_bodies.ROOT_APPROACH_PATH)
    if raw is None:
        raise ValueError("the frozen v2 technical approach is missing")
    value = BodyCodecClient().parse(
        patch_series_bodies.ROOT_APPROACH_CONTEXT,
        raw,
        expected=patch_series_bodies.ROOT_APPROACH_CONTRACT,
    )
    value = patch_series_bodies._legacy_json_value(value)
    if not isinstance(value, Mapping):
        raise TypeError("the frozen v2 technical approach must be an object")
    return dict(value)


def _affected_producer_coordinate(
    candidate: Mapping[str, Any] | None, cue_location: str
) -> tuple[str, str]:
    """Best-effort affected identity without weakening a codec failure."""

    problem = candidate.get("problem") if isinstance(candidate, Mapping) else None
    if isinstance(problem, Mapping):
        for field, identity_field in (
            ("production_obligations", "id"),
            ("deliverable_requirements", "referee_goal_id"),
            ("goals", "id"),
        ):
            values = problem.get(field)
            if not isinstance(values, list):
                continue
            match = re.search(rf"{field}(?:[./]|\[)(\d+)", cue_location)
            if match and int(match.group(1)) < len(values):
                value = values[int(match.group(1))]
                if isinstance(value, Mapping):
                    identity = str(value.get(identity_field) or "")
                    return (identity, "") if field != "production_obligations" else (
                        str(value.get("referee_goal_id") or ""),
                        identity,
                    )
        obligations = problem.get("production_obligations")
        if isinstance(obligations, list):
            for value in obligations:
                if (
                    isinstance(value, Mapping)
                    and value.get("id") == cue_location
                ):
                    return (
                        str(value.get("referee_goal_id") or ""),
                        str(value.get("id") or ""),
                    )
        goals = problem.get("goals")
        if isinstance(goals, list):
            for value in goals:
                if isinstance(value, Mapping) and value.get("id") == cue_location:
                    return str(value.get("id") or ""), ""
    if "obligation" in cue_location:
        return "", "root.problem.production_obligations"
    return "root.problem.goals", ""


def _authorability_failure(
    decision: AuthorabilityDecision, branch: str
) -> dict[str, Any]:
    diagnostic = decision.diagnostic or PublicationDiagnostic(
        decision.source.route,
        decision.source.frozen_root_oid,
        "root.problem",
        patch_series_bodies.ROOT_DISPOSITION_V2_NOT_READY,
        goal_id="root.problem.goals",
        detail="producer-aware authorability awaits canonical scope publication",
        context=decision.source.context,
    )
    return {
        "ok": False,
        "status": (
            "producer-pair-vet-failed"
            if not decision.vet_passed
            else patch_series_bodies.ROOT_DISPOSITION_V2_NOT_READY
        ),
        "branch": branch,
        "frozen_oid": decision.source.frozen_root_oid,
        "route": decision.source.route,
        "diagnostic": diagnostic.as_dict(),
        "authorability_decision": decision.as_dict(),
        "error": diagnostic.detail or diagnostic.rule,
    }


def refine(
    repo: str | Path,
    patch_series_id_or_branch: str,
    *,
    horizon: int = 1,
    ctx: org_log.RunContext | None = None,
) -> dict[str, Any]:
    """Split an approved technical approach into peer network nodes."""
    repo_path = Path(repo).resolve()
    branch = _patch_series_branch(patch_series_id_or_branch)
    log_ctx = ctx or org_log.RunContext(repo=repo_path, patch_series_id=_patch_series_id(branch), stage="patch_series.network.refine")
    with org_log.span("patch_series.network.refine", log_ctx.child(patch_series_id=_patch_series_id(branch), stage="patch_series.network.refine")) as refine_ctx:
        result = _refine(repo_path, branch, horizon=horizon, ctx=refine_ctx)
        org_log.emit("patch_series.network.refine.result", _lineage_result_payload(result), ctx=refine_ctx)
        return result


def _refine(repo_path: Path, branch: str, *, horizon: int, ctx: org_log.RunContext) -> dict[str, Any]:
    frozen_ref = git_wrapper.head_sha(repo_path, branch) or ""
    already_refined_decision: AuthorabilityDecision | None = None
    if _network_file_exists(repo_path, frozen_ref, STATUS_PATH):
        already_refined_decision = vet_stateless_authorability(
            repo_path,
            branch,
            "already_refined",
            frozen_root_oid=frozen_ref,
        )
        if already_refined_decision.blocked:
            return _authorability_failure(already_refined_decision, branch)
        status = _read_json(repo_path, frozen_ref, STATUS_PATH)
        if isinstance(status, Mapping) and status.get("generated_from") == NODE_MANIFEST_PATH:
            return {"ok": True, "status": "already-refined", "branch": branch, "lineage_status": status}
    if _network_file_exists(repo_path, frozen_ref, LEDGER_PATH):
        if already_refined_decision is None:
            already_refined_decision = vet_stateless_authorability(
                repo_path,
                branch,
                "already_refined",
                frozen_root_oid=frozen_ref,
            )
            if already_refined_decision.blocked:
                return _authorability_failure(already_refined_decision, branch)
        ledger = _read_json(repo_path, frozen_ref, LEDGER_PATH)
        if _legacy_branch_child_ledger(ledger):
            return {
                "ok": False,
                "status": "legacy-branch-child-record",
                "branch": branch,
                "error": "legacy child patch series branch records are rejected by the nested network model",
            }
        return {"ok": True, "status": "already-refined", "branch": branch, "ledger": ledger}

    ordinary_decision = vet_stateless_authorability(
        repo_path,
        branch,
        "ordinary_refinement",
        frozen_root_oid=frozen_ref or None,
    )
    if ordinary_decision.blocked:
        return _authorability_failure(ordinary_decision, branch)
    root_snapshot = _vetted_root_snapshot(repo_path, ordinary_decision.source)

    try:
        if ordinary_decision.transition_allowed:
            codec = BodyCodecClient()
            cover_raw = root_snapshot.raw(patch_series_bodies.COVER_PATH)
            approach_raw = root_snapshot.raw(patch_series_bodies.ROOT_APPROACH_PATH)
            if cover_raw is None or approach_raw is None:
                raise ValueError("canonical scope formation root is incomplete")
            patch_series = codec.parse(
                patch_series_bodies.COVER_CONTEXT,
                cover_raw,
                expected=patch_series_bodies.COVER_CONTRACT,
            )
            approach = codec.parse(
                patch_series_bodies.ROOT_APPROACH_CONTEXT,
                approach_raw,
                expected=patch_series_bodies.ROOT_APPROACH_CONTRACT,
            )
            patch_series = patch_series_bodies._legacy_json_value(patch_series)
            approach = patch_series_bodies._legacy_json_value(approach)
        else:
            patch_series = root_snapshot.cover_letter()
            approach = root_snapshot.technical_approach()
    except (ValueError, TypeError):
        patch_series = approach = None
    if not isinstance(patch_series, Mapping) or not isinstance(approach, Mapping):
        return {"ok": False, "status": "missing-input", "branch": branch, "error": "patch-series-cover-letter.json and technical-approach-plan.json are required"}

    # The shortcut predicate is itself a stateless decision route. Vet its
    # immutable inputs before evaluating it, then retain that one decision if
    # either the deterministic or model response selects the shortcut.
    right_sized_decision = vet_stateless_authorability(
        repo_path,
        branch,
        "right_sized",
        technical_approach=approach,
        frozen_root_oid=root_snapshot.frozen_oid,
    )
    deterministic_right_sized = right_sized({"patch_series": patch_series, "technical_approach": approach})
    declared_patchwork_checks = _declared_patchwork_checks_source(patch_series, approach)
    scope_items = _scope_items(patch_series, approach)
    if deterministic_right_sized:
        shortcut = right_sized_decision
        if shortcut.blocked:
            return _authorability_failure(shortcut, branch)
        files = _right_sized_node_files(branch, scope_items, declared_patchwork_checks)
        written = _commit_network_files(
            repo_path,
            branch,
            files,
            subject="network: right-sized",
            source=shortcut.source,
            transition_route="ordinary_refinement",
        )
        return {
            "ok": True,
            "status": "right-sized",
            "branch": branch,
            "rationale": "deterministic right-sized leaf",
            "surplus_children_ignored": 0,
            "ledger_commit": written["commit"],
        }

    split_result = _split_plan_graph(
        repo_path,
        branch,
        patch_series,
        approach,
        scope_items,
        horizon,
        "",
        frozen_root_oid=root_snapshot.frozen_oid,
        ctx=ctx.child(stage="patch_series.network.split"),
    )
    if not split_result["ok"]:
        return split_result
    split = split_result["split"]
    if split["split_mode"] == "right_sized":
        shortcut = right_sized_decision
        if shortcut.blocked:
            return _authorability_failure(shortcut, branch)
        files = _right_sized_node_files(branch, scope_items, declared_patchwork_checks)
        written = _commit_network_files(
            repo_path,
            branch,
            files,
            subject="network: right-sized",
            source=shortcut.source,
            transition_route="ordinary_refinement",
        )
        return {
            "ok": True,
            "status": "right-sized",
            "branch": branch,
            "rationale": split["rationale"],
            "surplus_children_ignored": 0,
            "ledger_commit": written["commit"],
        }

    normalized = _normalize_split(repo_path, branch, split, scope_items, horizon)
    projection = _split_coverage_projection(normalized, scope_items)
    ledger = _ledger(branch, scope_items, normalized, projection)
    root_manifest = _root_manifest(branch, scope_items, normalized, projection, declared_patchwork_checks)
    files = _nested_node_files(repo_path, branch, normalized, ledger, root_manifest, product_contract=_product_contract_from_patch_series(patch_series))
    tx_validation = _validate_nested_file_map(files)
    if not tx_validation["ok"]:
        return {
            "ok": False,
            "status": "nested-artifact-transaction-invalid",
            "branch": branch,
            "validation": tx_validation,
        }
    ledger_commit = _commit_network_files(
        repo_path,
        branch,
        files,
        subject="network: nested nodes",
        source=ordinary_decision.source,
        transition_route="split_preparation",
    )
    org_log.emit(
        "patch_series.network.nested_nodes_written",
        {"branch": branch, "child_count": len(normalized["children"]), "commit": ledger_commit["commit"]},
        ctx=ctx.child(stage="patch_series.network.nested_write"),
    )
    children = _nested_child_results(normalized, ledger_commit["commit"])
    return {
        "ok": True,
        "status": "refined",
        "branch": branch,
        "ledger_commit": ledger_commit["commit"],
        "status_commit": ledger_commit["commit"],
        "children": children,
        "dependency_graph": _dependency_graph_projection(normalized["children"]),
    }


def right_sized(view: Mapping[str, Any]) -> bool:
    """Return whether a network view is one bounded executable leaf."""
    approach = _unwrap_approach(view)
    if approach.get("split_mode") == "right_sized":
        return True
    child = approach.get("lineage_child")
    if isinstance(child, Mapping):
        criteria = child.get("acceptance_criteria")
        return isinstance(criteria, list) and bool(criteria) and isinstance(child.get("functional_check"), str)
    if isinstance(approach.get("lineage_parent"), Mapping) and isinstance(approach.get("approach_slice"), Mapping):
        return True
    patch_plan = _find_first_mapping(approach, "patch_plan")
    if not patch_plan:
        return False
    if _list_of_mappings(patch_plan.get("items")):
        try:
            return len(_weak_components(_plan_graph(approach))) == 1
        except (TypeError, ValueError):
            return False
    if _list_of_mappings(patch_plan.get("follow_ups")) or _list_of_mappings(patch_plan.get("deferred")):
        return False
    systems = _find_named_strings(approach, {"systems", "subsystem", "key_modules", "implementation"})
    if len(systems) > 1:
        return False
    first_proof_moment = patch_plan.get("first_proof_moment")
    if isinstance(first_proof_moment, Mapping):
        return bool(first_proof_moment.get("how_verified"))
    return True


def resolved(repo: str | Path, branch: str) -> bool:
    """Return whether a leaf or parent network node is resolved."""
    repo_path = Path(repo).resolve()
    normalized, node_path = _parse_node_address(branch)
    root_snapshot = patch_series_bodies.classify_root_generation(
        repo_path, normalized
    )
    if root_snapshot.lifecycle_blocked:
        return False
    return _resolved(
        repo_path,
        normalized,
        node_path,
        0,
        root_snapshot=root_snapshot,
    )


def _resolved(
    repo_path: Path,
    normalized: str,
    node_path: str,
    depth: int,
    *,
    root_snapshot: patch_series_bodies.RootGenerationSnapshot,
) -> bool:
    if depth >= MAX_RESOLUTION_DEPTH:
        raise RuntimeError(f"network resolution exceeded maximum depth {MAX_RESOLUTION_DEPTH} at {_node_address(normalized, node_path)}")
    frozen_ref = root_snapshot.frozen_oid or normalized
    if node_path != ".":
        metadata = _read_node_manifest(repo_path, frozen_ref, node_path)
        if not metadata:
            return False
        return _leaf_resolved(
            repo_path,
            frozen_ref,
            node_path,
            metadata,
            root_snapshot=root_snapshot,
        )

    metadata = _read_metadata(repo_path, frozen_ref)
    parent_branch = str(metadata.get("parent_branch", "")) if metadata else ""
    if parent_branch and parent_branch != normalized:
        return _legacy_leaf_resolved(
            repo_path,
            frozen_ref,
            parent_branch,
            series_branch=normalized,
            root_snapshot=root_snapshot,
        )

    ledger = _read_ledger_from_first_parent_history(repo_path, frozen_ref)
    if _legacy_branch_child_ledger(ledger):
        raise RuntimeError("legacy branch-child network records are not supported by nested network resolution")
    if (
        isinstance(ledger, Mapping)
        and ledger.get("parent_branch") == normalized
        and isinstance(ledger.get("children"), list)
    ):
        if ledger.get("relation") == "right-sized" and not ledger["children"]:
            return _legacy_leaf_resolved(
                repo_path,
                frozen_ref,
                normalized,
                series_branch=normalized,
                root_snapshot=root_snapshot,
            )
        child_paths = [
            str(child.get("node_path"))
            for child in ledger["children"]
            if isinstance(child, Mapping) and isinstance(child.get("node_path"), str)
        ]
        return all(
            _resolved(
                repo_path,
                normalized,
                child_path,
                depth + 1,
                root_snapshot=root_snapshot,
            )
            for child_path in child_paths
        ) and _has_integration_gate(
            repo_path,
            frozen_ref,
            root_snapshot=root_snapshot,
        )

    return _legacy_leaf_resolved(
        repo_path,
        frozen_ref,
        parent_branch,
        series_branch=normalized,
        root_snapshot=root_snapshot,
    )


def _leaf_resolved(
    repo: Path,
    parent_ref: str,
    node_path: str,
    manifest: Mapping[str, Any],
    *,
    root_snapshot: patch_series_bodies.RootGenerationSnapshot,
) -> bool:
    if root_snapshot.lifecycle_blocked:
        return False
    contrib_branch = str(manifest.get("contrib_branch") or "")
    accepted = contrib_branch and any(
        _contrib_has_unique_subject(repo, parent_ref, contrib_branch, subject)
        for subject in ("acceptance: passed", "acceptance: reachable")
    )
    if not accepted:
        return False
    return _contrib_merged_into_subsystem_or_mainline(
        repo, contrib_branch, root_generation=root_snapshot.generation
    )


def _contrib_merged_into_subsystem_or_mainline(
    repo: Path,
    contrib_branch: str,
    *,
    root_generation: str = patch_series_bodies.ROOT_GENERATION_HISTORICAL,
) -> bool:
    targets = ["ai-org/subsystem", "ai-org/mainline"]
    try:
        default = git_wrapper.default_branch(repo)
    except RuntimeError:
        default = ""
    if default:
        targets.append(default)
    targets = list(dict.fromkeys(targets))
    if root_generation == patch_series_bodies.ROOT_GENERATION_V2:
        verdict_oid = git_wrapper.head_sha(repo, contrib_branch)
        if verdict_oid is None:
            return False
        # Dynamic import avoids making the lineage module part of the acceptance
        # module's import cycle. Migrated leaves resolve from sealed producer
        # evidence, never from contribution ancestry (the code-only tree
        # deliberately excludes that ancestry).
        evidence = importlib.import_module("ai_org.maintainer_merge.evidence")

        return evidence.has_verified_link(repo, verdict_oid, targets)
    return any(
        git_wrapper.branch_exists(repo, target) and git_wrapper.is_ancestor(repo, contrib_branch, target)
        for target in targets
    )


def _legacy_leaf_resolved(
    repo: Path,
    source_ref: str,
    parent_branch: str = "",
    *,
    series_branch: str,
    root_snapshot: patch_series_bodies.RootGenerationSnapshot,
) -> bool:
    if root_snapshot.lifecycle_blocked:
        return False
    contrib_branch = _contrib_branch_for_patch_series_branch(
        repo, source_ref, series_branch=series_branch
    )
    accepted = any(
        _contrib_has_unique_subject(repo, parent_branch or source_ref, ref, subject)
        for ref in _accepted_source_refs(contrib_branch)
        for subject in ("acceptance: passed", "acceptance: reachable")
    )
    if not accepted:
        return False
    # Memento: the group tree integrates implementation branches, never child
    # patch series doc nodes. Sibling patch series branches all own patch-series-cover-letter.json,
    # technical-approach-plan.json, and patch-series-metadata.json at identical paths, so
    # merging doc nodes into the parent structurally conflicts from the second
    # accepted sibling onward.
    if parent_branch:
        return bool(
            contrib_branch
            and _contrib_merged_into_subsystem_or_mainline(
                repo,
                contrib_branch,
                root_generation=root_snapshot.generation,
            )
        )
    default = git_wrapper.default_branch(repo)
    if contrib_branch and git_wrapper.is_ancestor(repo, contrib_branch, default):
        return True
    return git_wrapper.is_ancestor(repo, source_ref, default)


def _contrib_has_unique_subject(repo: Path, base_ref: str, contrib_branch: str, subject: str) -> bool:
    if not contrib_branch or not git_wrapper.branch_exists(repo, contrib_branch):
        return False
    fork_point = git_wrapper.branch_creation_point(repo, contrib_branch)
    if fork_point and not git_wrapper.is_ancestor(repo, fork_point, contrib_branch):
        fork_point = None
    if not fork_point and base_ref:
        fork_point = git_wrapper.fork_point(repo, base_ref, contrib_branch)
    if not fork_point and base_ref:
        fork_point = git_wrapper.merge_base(repo, base_ref, contrib_branch)
    if not fork_point:
        return git_wrapper.has_subject(repo, contrib_branch, subject)
    return git_wrapper.has_subject_between(repo, fork_point, contrib_branch, subject)


def escalate(
    repo: str | Path,
    child_branch: str,
    evidence: Mapping[str, Any] | str,
    *,
    ctx: org_log.RunContext | None = None,
) -> dict[str, Any]:
    """Record that a child is blocked because the parent scope was invalidated."""
    repo_path = Path(repo).resolve()
    branch, node_path = _parse_node_address(child_branch)
    address = _node_address(branch, node_path)
    log_ctx = ctx or org_log.RunContext(repo=repo_path, patch_series_id=_patch_series_id(branch), stage="patch_series.network.escalate")
    org_log.emit("patch_series.network.escalate.started", {"branch": branch, "node_path": node_path}, ctx=log_ctx)
    decision = vet_stateless_authorability(repo_path, branch, "stale_decision")
    if decision.blocked:
        result = _authorability_failure(decision, branch)
        result["node_path"] = node_path
        org_log.emit("patch_series.network.escalate.result", _lineage_result_payload(result), ctx=log_ctx, severity="warning")
        return result
    metadata = _read_node_manifest(
        repo_path, decision.source.frozen_root_oid, node_path
    ) if node_path != "." else _read_metadata(
        repo_path, decision.source.frozen_root_oid
    )
    parent = str(metadata.get("parent_branch", branch))
    superseded_ledger_commit = (
        str(
            _network_path_last_commit(
                repo_path, decision.source.frozen_root_oid, LEDGER_PATH
            )
            or ""
        )
        if parent and git_wrapper.branch_exists(repo_path, parent)
        else ""
    )
    if parent and git_wrapper.branch_exists(repo_path, parent):
        refusal = _parent_escalation_refusal(
            repo_path,
            parent,
            frozen_ref=decision.source.frozen_root_oid,
        )
        if refusal:
            org_log.emit(
                "patch_series.network.escalate.result",
                {"ok": False, "status": refusal, "branch": branch, "node_path": node_path, "parent_branch": parent},
                ctx=log_ctx,
                severity="warning",
            )
            return {"ok": False, "status": refusal, "branch": branch, "node_path": node_path, "parent_branch": parent}
    stale_addresses: list[str] = []
    frozen_stale_manifests: dict[str, dict[str, Any]] = {}
    if parent and git_wrapper.branch_exists(repo_path, parent):
        if parent != branch:
            raise RuntimeError("cross-ref escalation publication is not supported")
        _validate_frozen_escalation_node(
            repo_path,
            decision.source.frozen_root_oid,
            branch,
            node_path,
            metadata,
            role="child",
        )
        stale_addresses = _nodes_with_ledger_commit(
            repo_path,
            parent,
            superseded_ledger_commit,
            frozen_ref=decision.source.frozen_root_oid,
            exclude={node_path},
        )
        for stale_address in stale_addresses:
            stale_branch, stale_path = _parse_node_address(stale_address)
            if stale_branch != branch:
                raise RuntimeError("escalation cohort crossed its publication ref")
            stale_manifest = _read_node_manifest(
                repo_path, decision.source.frozen_root_oid, stale_path
            )
            _validate_frozen_escalation_node(
                repo_path,
                decision.source.frozen_root_oid,
                branch,
                stale_path,
                stale_manifest,
                role="stale sibling",
            )
            frozen_stale_manifests[stale_address] = stale_manifest
    record = dict(metadata)
    record["lifecycle_status"] = "blocked:parent-invalidated"
    record["escalation_evidence"] = evidence if isinstance(evidence, Mapping) else {"summary": str(evidence)}
    changes: dict[str, Any] = {
        _node_file(
            node_path,
            NODE_MANIFEST_PATH if node_path != "." else METADATA_PATH,
        ): record
    }
    parent_commit: dict[str, str] | None = None
    stale: list[dict[str, Any]] = []
    if parent and git_wrapper.branch_exists(repo_path, parent):
        for stale_address in stale_addresses:
            _stale_branch, stale_path = _parse_node_address(stale_address)
            stale_manifest = dict(frozen_stale_manifests[stale_address])
            if stale_manifest.get("lifecycle_status") != "stale":
                stale_manifest["stale_previous_lifecycle_status"] = str(
                    stale_manifest.get("lifecycle_status") or ""
                )
            stale_manifest["lifecycle_status"] = "stale"
            stale_manifest["stale_reason"] = (
                "parent ledger superseded by child escalation"
            )
            if superseded_ledger_commit:
                stale_manifest["stale_ledger_commit"] = superseded_ledger_commit
            changes[_node_file(stale_path, NODE_MANIFEST_PATH)] = stale_manifest

        review_path, review_record = _write_synthetic_escalation_round(
            repo_path,
            parent,
            address,
            record["escalation_evidence"],
            reviewed_commit=decision.source.frozen_root_oid,
            root_snapshot=_vetted_root_snapshot(repo_path, decision.source),
        )
        _validate_escalation_transition_cohort(
            source=decision.source,
            child_address=address,
            stale_addresses=stale_addresses,
            changes=changes,
            review_path=review_path,
            review_record=review_record,
        )
        parent_commit = _commit_network_files(
            repo_path,
            branch,
            changes,
            subject="network: escalate parent successor cohort",
            source=decision.source,
            transition_route="stale_marking",
            synthetic_reviews={review_path: review_record},
            prepared_validator=lambda transition: _validate_prepared_escalation_successor(
                transition,
                child_address=address,
                stale_addresses=stale_addresses,
                review_path=review_path,
            ),
            required_successor_paths=_escalation_successor_paths(
                source=decision.source,
                changed_paths=tuple(changes),
                review_path=review_path,
            ),
        )
        stale = [
            {
                "branch": branch,
                "node_path": _parse_node_address(item)[1],
                "address": item,
                "commit": parent_commit["commit"],
            }
            for item in stale_addresses
        ]
    else:
        parent_commit = _write_node_manifest(
            repo_path,
            branch,
            record,
            subject="network: blocked parent-invalidated",
            source=decision.source,
            transition_route="stale_marking",
        )
    result = {
        "ok": True,
        "branch": branch,
        "node_path": node_path,
        "address": address,
        "parent_branch": parent,
        "child_commit": parent_commit["commit"],
        "parent_commit": parent_commit,
        "stale": stale,
        # Keep the publication coordinates at the transition boundary, just
        # as rebaseline does.  Callers can prove that the coherent successor
        # was released by one CAS without unpacking an implementation detail.
        "expected_ref_oids": dict(parent_commit.get("expected_ref_oids", {})),
        "resulting_ref_oids": dict(parent_commit.get("resulting_ref_oids", {})),
    }
    org_log.emit("patch_series.network.escalate.result", _lineage_result_payload(result), ctx=log_ctx)
    return result


def mark_stale(
    repo: str | Path,
    branches: list[str],
    reason: str,
    *,
    superseded_ledger_commit: str = "",
    ctx: org_log.RunContext | None = None,
) -> dict[str, Any]:
    """Mark dependent network branches stale after a parent re-baseline."""
    repo_path = Path(repo).resolve()
    log_ctx = ctx or org_log.RunContext(repo=repo_path, stage="patch_series.network.mark_stale")
    org_log.emit(
        "patch_series.network.mark_stale.started",
        {"branches": branches, "reason": reason, "superseded_ledger_commit": superseded_ledger_commit},
        ctx=log_ctx,
    )
    decisions: list[tuple[str, AuthorabilityDecision]] = []
    for item in branches:
        candidate_branch, _node_path = _parse_node_address(item)
        if git_wrapper.branch_exists(repo_path, candidate_branch):
            decisions.append(
                (
                    candidate_branch,
                    vet_stateless_authorability(
                        repo_path, candidate_branch, "stale_decision"
                    ),
                )
            )
    for candidate_branch, decision in decisions:
        if decision.blocked:
            return {
                **_authorability_failure(decision, candidate_branch),
                "branches": [],
            }
    decisions_by_branch = {
        candidate_branch: decision
        for candidate_branch, decision in decisions
    }
    staged: dict[str, dict[str, Any]] = {}
    staged_entries: dict[str, list[dict[str, Any]]] = {}
    for item in branches:
        branch, node_path = _parse_node_address(item)
        if not git_wrapper.branch_exists(repo_path, branch):
            continue
        source = decisions_by_branch[branch].source
        metadata = _read_node_manifest(
            repo_path, source.frozen_root_oid, node_path
        ) if node_path != "." else _read_metadata(
            repo_path, source.frozen_root_oid
        )
        if metadata.get("lifecycle_status") != "stale":
            metadata["stale_previous_lifecycle_status"] = str(
                metadata.get("lifecycle_status") or ""
            )
        metadata["lifecycle_status"] = "stale"
        metadata["stale_reason"] = reason
        if superseded_ledger_commit:
            metadata["stale_ledger_commit"] = superseded_ledger_commit
        staged.setdefault(branch, {})[
            _node_file(
                node_path,
                NODE_MANIFEST_PATH if node_path != "." else METADATA_PATH,
            )
        ] = metadata
        staged_entries.setdefault(branch, []).append(
            {
                "branch": branch,
                "node_path": node_path,
                "address": _node_address(branch, node_path),
            }
        )

    updated: list[dict[str, Any]] = []
    for branch, changes in staged.items():
        source = decisions_by_branch[branch].source
        if _network_file_exists(repo_path, source.frozen_root_oid, LEDGER_PATH):
            written = _commit_network_files(
                repo_path,
                branch,
                changes,
                subject="network: stale",
                source=source,
                transition_route="stale_marking",
            )
            updated.extend(
                {**entry, "commit": written["commit"]}
                for entry in staged_entries[branch]
            )
        else:
            # Historical non-network fixtures retain their established
            # per-node compatibility write. Migrated trees always take the
            # complete prepared/CAS branch above.
            for entry in staged_entries[branch]:
                node_path = entry["node_path"]
                manifest = changes[
                    _node_file(
                        node_path,
                        NODE_MANIFEST_PATH if node_path != "." else METADATA_PATH,
                    )
                ]
                written = _write_node_manifest(
                    repo_path,
                    branch,
                    manifest,
                    subject="network: stale",
                    source=source,
                    transition_route="stale_marking",
                )
                updated.append({**entry, "commit": written["commit"]})
    result = {"ok": True, "branches": updated}
    org_log.emit("patch_series.network.mark_stale.result", _lineage_result_payload(result), ctx=log_ctx)
    return result


def rebaseline_pending(repo: str | Path, branch: str) -> bool:
    """Return whether a parent ledger must be replaced after an escalation v2."""
    repo_path = Path(repo).resolve()
    normalized = _patch_series_branch(branch)
    decision = vet_stateless_authorability(
        repo_path, normalized, "rebaseline_decision"
    )
    if decision.blocked:
        return False
    return _rebaseline_pending_from_source(
        repo_path, normalized, decision.source
    )


def _rebaseline_pending_from_source(
    repo: Path,
    branch: str,
    source: FrozenNetworkSourceVector,
) -> bool:
    """Decide rebaseline readiness entirely from one vetted root snapshot."""

    return _pending_escalation_rebaseline_record_from_source(
        repo, branch, source
    ) is not None


def _pending_escalation_rebaseline_record_from_source(
    repo: Path,
    branch: str,
    source: FrozenNetworkSourceVector,
) -> dict[str, Any] | None:
    """Return the exact unconsumed escalation tuple from one frozen snapshot."""

    frozen_ref = source.frozen_root_oid
    if _vetted_root_snapshot(repo, source).lifecycle_blocked:
        return None
    if _parent_escalation_refusal(repo, branch, frozen_ref=frozen_ref):
        return None
    if _current_patch_series_state(repo, frozen_ref) != "direction-ok":
        return None
    if not _network_file_exists(repo, frozen_ref, LEDGER_PATH):
        return None
    latest = _latest_lineage_escalation_round(
        repo,
        _patch_series_id(branch),
        frozen_ref=frozen_ref,
    )
    if latest is None:
        return None
    ledger = _read_json(repo, frozen_ref, LEDGER_PATH)
    if not isinstance(ledger, Mapping):
        return None
    rebaselined = ledger.get("rebaselined_from_escalation")
    latest_tuple = _escalation_rebaseline_record(
        repo,
        branch,
        latest,
        frozen_ref=frozen_ref,
    )
    if _normalized_escalation_rebaseline_record(rebaselined) == latest_tuple:
        return None
    return latest_tuple


def rebaseline(
    repo: str | Path,
    parent_branch: str,
    *,
    horizon: int = 1,
    ctx: org_log.RunContext | None = None,
) -> dict[str, Any]:
    """Replace a parent network ledger after its author-side v2 is direction-ok."""
    repo_path = Path(repo).resolve()
    branch = _patch_series_branch(parent_branch)
    log_ctx = ctx or org_log.RunContext(repo=repo_path, patch_series_id=_patch_series_id(branch), stage="patch_series.network.rebaseline")
    with org_log.span("patch_series.network.rebaseline", log_ctx.child(patch_series_id=_patch_series_id(branch))) as rebaseline_ctx:
        result = _rebaseline(repo_path, branch, horizon=horizon, ctx=rebaseline_ctx)
        org_log.emit("patch_series.network.rebaseline.result", _lineage_result_payload(result), ctx=rebaseline_ctx)
        return result


def _rebaseline(repo_path: Path, branch: str, *, horizon: int, ctx: org_log.RunContext) -> dict[str, Any]:
    authorability_decision = vet_stateless_authorability(
        repo_path, branch, "rebaseline_decision"
    )
    if authorability_decision.blocked:
        return _authorability_failure(authorability_decision, branch)
    root_snapshot = _vetted_root_snapshot(repo_path, authorability_decision.source)
    if not root_snapshot.lifecycle_ready:
        return _root_generation_failure(root_snapshot, branch)
    frozen_ref = authorability_decision.source.frozen_root_oid
    escalation_rebaseline_record = (
        _pending_escalation_rebaseline_record_from_source(
            repo_path, branch, authorability_decision.source
        )
    )
    if escalation_rebaseline_record is None:
        return {"ok": False, "status": "not-pending", "branch": branch}
    previous_ledger = _read_json(repo_path, frozen_ref, LEDGER_PATH)
    if not isinstance(previous_ledger, Mapping):
        return {"ok": False, "status": "missing-ledger", "branch": branch}
    superseded = _network_path_last_commit(repo_path, frozen_ref, LEDGER_PATH) or ""
    try:
        patch_series = root_snapshot.cover_letter()
        approach = root_snapshot.technical_approach()
    except (ValueError, TypeError) as exc:
        return {
            "ok": False,
            "status": patch_series_bodies.ROOT_DISPOSITION_INVALID,
            "branch": branch,
            "error": str(exc),
            "root_generation": root_snapshot.identity(),
        }
    stale_manifests = _stale_child_manifests(
        repo_path, frozen_ref, previous_ledger
    )
    existing_registry = _root_declared_patchwork_checks_from_git(
        repo_path, frozen_ref
    )
    declared_patchwork_checks = [
        {"name": name, "type": var_type}
        for name, var_type in sorted(existing_registry["registry"].items())
    ]

    scope_items = _scope_items(patch_series, approach)
    split_result = _split_plan_graph(
        repo_path,
        branch,
        patch_series,
        approach,
        scope_items,
        horizon,
        "",
        frozen_root_oid=authorability_decision.source.frozen_root_oid,
        ctx=ctx.child(stage="patch_series.network.split"),
    )
    if not split_result["ok"]:
        return split_result
    split = split_result["split"]
    if split["split_mode"] == "right_sized":
        return {"ok": False, "status": "blocked:parent-rebaseline-changed", "branch": branch, "error": "rebaseline removed the child ledger contract"}
    normalized = _normalize_split(repo_path, branch, split, scope_items, horizon)
    projection = _split_coverage_projection(normalized, scope_items)
    revision_value = previous_ledger.get("ledger_revision", 1) or 1
    ledger_revision = (
        revision_value.as_int_exact()
        if hasattr(revision_value, "as_int_exact")
        else int(revision_value)
    ) + 1
    ledger = _ledger(
        branch,
        scope_items,
        normalized,
        projection,
        ledger_revision=ledger_revision,
        supersedes_ledger_commit=superseded,
        rebaselined_from_escalation=escalation_rebaseline_record,
    )
    root_manifest = _root_manifest(branch, scope_items, normalized, projection, declared_patchwork_checks)
    files = _nested_node_files(repo_path, branch, normalized, ledger, root_manifest, product_contract=_product_contract_from_patch_series(patch_series))
    for node_path, manifest in stale_manifests.items():
        if _node_file(node_path, NODE_MANIFEST_PATH) in files:
            files[_node_file(node_path, NODE_MANIFEST_PATH)] = manifest
    tx_validation = _validate_nested_file_map(files)
    if not tx_validation["ok"]:
        return {
            "ok": False,
            "status": "nested-artifact-transaction-invalid",
            "branch": branch,
            "validation": tx_validation,
        }
    written = _commit_network_files(
        repo_path,
        branch,
        files,
        subject="network: rebaseline",
        source=authorability_decision.source,
        transition_route="rebaseline",
    )
    stale = [
        {
            "branch": branch,
            "node_path": node_path,
            "address": _node_address(branch, node_path),
            "commit": written["commit"],
        }
        for node_path in sorted(stale_manifests)
    ]
    return {
        "ok": True,
        "status": "rebaselined",
        "branch": branch,
        "ledger_commit": written["commit"],
        "supersedes_ledger_commit": superseded,
        "ledger_revision": ledger_revision,
        "stale": stale,
        "expected_ref_oids": dict(written["expected_ref_oids"]),
        "resulting_ref_oids": dict(written["resulting_ref_oids"]),
    }


def stale_revalidation_pending(repo: str | Path, branch: str) -> bool:
    """Return whether a stale branch can be checked against the current parent ledger."""
    repo_path = Path(repo).resolve()
    normalized, node_path = _parse_node_address(branch)
    decision = vet_stateless_authorability(
        repo_path, normalized, "stale_decision"
    )
    if decision.blocked:
        return False
    frozen_ref = decision.source.frozen_root_oid
    metadata = _read_node_manifest(repo_path, frozen_ref, node_path) if node_path != "." else _read_metadata(repo_path, frozen_ref)
    if metadata.get("lifecycle_status") != "stale":
        return False
    parent = str(metadata.get("parent_branch", normalized))
    parent_ref = frozen_ref if parent == normalized else parent
    return bool(parent and git_wrapper.branch_exists(repo_path, parent) and _network_file_exists(repo_path, parent_ref, LEDGER_PATH))


def revalidate_stale(
    repo: str | Path,
    branch: str,
    *,
    ctx: org_log.RunContext | None = None,
) -> dict[str, Any]:
    """Reactivate unchanged stale children or block changed parent contracts."""
    repo_path = Path(repo).resolve()
    normalized, node_path = _parse_node_address(branch)
    address = _node_address(normalized, node_path)
    log_ctx = ctx or org_log.RunContext(repo=repo_path, patch_series_id=_patch_series_id(normalized), stage="patch_series.network.revalidate")
    org_log.emit("patch_series.network.revalidate.started", {"branch": normalized, "node_path": node_path}, ctx=log_ctx)
    decision = vet_stateless_authorability(
        repo_path, normalized, "stale_decision"
    )
    if decision.blocked:
        result = _authorability_failure(decision, normalized)
        result["node_path"] = node_path
        org_log.emit("patch_series.network.revalidate.result", _lineage_result_payload(result), ctx=log_ctx, severity="warning")
        return result
    frozen_ref = decision.source.frozen_root_oid
    metadata = _read_node_manifest(repo_path, frozen_ref, node_path) if node_path != "." else _read_metadata(repo_path, frozen_ref)
    if metadata.get("lifecycle_status") != "stale":
        result = {"ok": False, "status": "not-stale", "branch": normalized, "node_path": node_path}
        org_log.emit("patch_series.network.revalidate.result", _lineage_result_payload(result), ctx=log_ctx, severity="warning")
        return result
    parent = str(metadata.get("parent_branch", normalized))
    parent_ref = frozen_ref if parent == normalized else parent
    ledger = _read_json(repo_path, parent_ref, LEDGER_PATH)
    if not isinstance(ledger, Mapping) or not isinstance(ledger.get("children"), list):
        result = {"ok": False, "status": "missing-parent-ledger", "branch": normalized, "node_path": node_path, "parent_branch": parent}
        org_log.emit("patch_series.network.revalidate.result", _lineage_result_payload(result), ctx=log_ctx, severity="error")
        return result
    child = _ledger_child_for_metadata(ledger, address, metadata)
    ledger_commit = _network_path_last_commit(repo_path, parent_ref, LEDGER_PATH) or git_wrapper.head_sha(repo_path, parent_ref) or ""
    parent_commit = git_wrapper.head_sha(repo_path, parent_ref) or ledger_commit
    if child is not None and _child_contract_unchanged(metadata, child, ledger):
        updated = dict(metadata)
        updated["lifecycle_status"] = str(
            metadata.get("stale_previous_lifecycle_status")
            or _canonical_lifecycle(child)
        )
        updated["ledger_commit"] = ledger_commit
        updated["revalidated_against_parent"] = parent_commit
        updated.pop("stale_reason", None)
        updated.pop("stale_ledger_commit", None)
        updated.pop("stale_previous_lifecycle_status", None)
        written = _write_node_manifest(
            repo_path,
            normalized,
            updated,
            subject="network: revalidated",
            source=decision.source,
            transition_route="stale_revalidation",
        ) if node_path != "." else _commit_network_files(
            repo_path,
            normalized,
            {METADATA_PATH: updated},
            subject="network: revalidated",
            source=decision.source,
            transition_route="stale_revalidation",
        )
        result = {"ok": True, "status": "reactivated", "branch": normalized, "node_path": node_path, "address": address, "commit": written["commit"], "ledger_commit": ledger_commit}
        org_log.emit("patch_series.network.revalidate.result", _lineage_result_payload(result), ctx=log_ctx)
        return result

    updated = dict(metadata)
    updated["lifecycle_status"] = "blocked:parent-rebaseline-changed"
    updated["parent_rebaseline_ledger_commit"] = ledger_commit
    written = _write_node_manifest(
        repo_path,
        normalized,
        updated,
        subject="network: blocked parent-rebaseline-changed",
        source=decision.source,
        transition_route="stale_revalidation",
    ) if node_path != "." else _commit_network_files(
        repo_path,
        normalized,
        {METADATA_PATH: updated},
        subject="network: blocked parent-rebaseline-changed",
        source=decision.source,
        transition_route="stale_revalidation",
    )
    result = {"ok": False, "status": "blocked:parent-rebaseline-changed", "branch": normalized, "node_path": node_path, "address": address, "commit": written["commit"], "ledger_commit": ledger_commit}
    org_log.emit("patch_series.network.revalidate.result", _lineage_result_payload(result), ctx=log_ctx, severity="warning")
    return result


def elaborate(
    repo: str | Path,
    child_branch: str,
    *,
    horizon: int = 1,
    ctx: org_log.RunContext | None = None,
) -> dict[str, Any]:
    """Elaborate a requested child once its declared dependencies are satisfied."""
    repo_path = Path(repo).resolve()
    branch, node_path = _parse_node_address(child_branch)
    address = _node_address(branch, node_path)
    log_ctx = ctx or org_log.RunContext(repo=repo_path, patch_series_id=_patch_series_id(branch), stage="patch_series.network.elaborate")
    org_log.emit("patch_series.network.elaborate.started", {"branch": branch, "node_path": node_path}, ctx=log_ctx)
    decision = vet_stateless_authorability(
        repo_path, branch, "elaboration_preparation"
    )
    if decision.blocked:
        result = _authorability_failure(decision, branch)
        result["node_path"] = node_path
        org_log.emit("patch_series.network.elaborate.result", _lineage_result_payload(result), ctx=log_ctx, severity="warning")
        return result
    root_snapshot = _vetted_root_snapshot(repo_path, decision.source)
    if not root_snapshot.lifecycle_ready:
        return _root_generation_failure(root_snapshot, branch)
    if not _coarse_ready_from_decision(
        repo_path,
        branch,
        node_path,
        decision,
    ):
        result = {"ok": False, "status": "blocked-by-dependencies", "branch": branch, "node_path": node_path}
        org_log.emit("patch_series.network.elaborate.result", _lineage_result_payload(result), ctx=log_ctx, severity="warning")
        return result
    frozen_ref = decision.source.frozen_root_oid
    manifest = _read_node_manifest(repo_path, frozen_ref, node_path)
    if not manifest:
        result = {"ok": False, "status": "missing-node", "branch": branch, "node_path": node_path}
        org_log.emit("patch_series.network.elaborate.result", _lineage_result_payload(result), ctx=log_ctx, severity="error")
        return result
    child = _child_from_manifest_for_elaboration(repo_path, branch, manifest)
    existing_child_approach = network_bodies.read_network_body(
        repo_path,
        frozen_ref,
        _node_file(node_path, patch_series_bodies.LEGACY_ROOT_APPROACH_PATH),
    )
    existing_lineage = (
        existing_child_approach.get("lineage_child")
        if isinstance(existing_child_approach, Mapping)
        else None
    )
    if isinstance(existing_lineage, Mapping) and isinstance(existing_lineage.get("patch_plan"), Mapping):
        child["patch_plan"] = copy.deepcopy(dict(existing_lineage["patch_plan"]))
    child_contract = _product_contract_from_request_envelope(_read_json(repo_path, frozen_ref, _node_file(node_path, "maintainer-series-request.json")))
    child.update(child_contract)
    updated = dict(manifest)
    updated["lifecycle_status"] = "ready_for_patch_authoring"
    existing_spine_contract_paths = _existing_spine_contract_paths(repo_path, frozen_ref)
    try:
        parent_contract = _product_contract_from_patch_series(
            root_snapshot.cover_letter()
        )
    except (ValueError, TypeError) as exc:
        result = _root_generation_failure(root_snapshot, branch)
        result["error"] = str(exc)
        return result
    files = {
        _node_file(node_path, patch_series_bodies.LEGACY_COVER_PATH): _child_patch_series(child, existing_spine_contract_paths, product_contract=parent_contract),
        _node_file(node_path, patch_series_bodies.LEGACY_ROOT_APPROACH_PATH): _child_approach(child),
        _node_file(node_path, METADATA_PATH): _child_metadata(branch, child, {child["child_key"]: child}, ""),
        _node_file(node_path, NODE_MANIFEST_PATH): updated,
    }
    tx_validation = _validate_nested_file_map({_node_file(node_path, "maintainer-series-request.json"): _read_json(repo_path, frozen_ref, _node_file(node_path, "maintainer-series-request.json")), **files})
    if not tx_validation["ok"]:
        result = {"ok": False, "status": "nested-artifact-transaction-invalid", "branch": branch, "node_path": node_path, "validation": tx_validation}
        org_log.emit("patch_series.network.elaborate.result", _lineage_result_payload(result), ctx=log_ctx, severity="error")
        return result
    written = _commit_network_files(
        repo_path,
        branch,
        files,
        subject="network: elaborate nested child",
        source=decision.source,
        transition_route="elaboration_preparation",
    )
    _post_offer_nonfatal(repo_path, branch, node_path, written["commit"], ctx=log_ctx)
    result = {"ok": True, "status": "ready_for_patch_authoring", "branch": branch, "node_path": node_path, "address": address, "commit": written["commit"]}
    org_log.emit("patch_series.network.elaborate.result", _lineage_result_payload(result), ctx=log_ctx)
    return result


def _post_offer_nonfatal(
    repo: Path,
    series_branch: str,
    node_path: str,
    cover_commit: str,
    *,
    ctx: org_log.RunContext,
) -> None:
    # Memento: close the ring as gate offer -> take -> announce ->
    # implement. Taking is competitive and creates no reservation or lock;
    # this scan only keeps the gate's one announcement per (series, node).
    try:
        for post in mailing_list.read(repo, kinds={"offer"}):
            refs = post.get("refs")
            if (
                isinstance(refs, Mapping)
                and refs.get("series") == series_branch
                and refs.get("node") == node_path
            ):
                return
        address = _node_address(series_branch, node_path)
        posted = mailing_list.post(
            repo,
            kind="offer",
            subject=address,
            refs={"series": series_branch, "node": node_path, "cover": cover_commit},
            ctx=ctx,
        )
        if posted.get("ok"):
            return
        error = str(posted.get("error") or "mailing-list offer post failed")
    except Exception as exc:  # noqa: BLE001 - list posting must never break the gate.
        error = str(exc)
    org_log.debug_emit(
        "mailing_list.offer.failed",
        {"series": series_branch, "node": node_path, "error": error},
        ctx=ctx,
        severity="warning",
    )


def split_pending(repo: str | Path, branch: str) -> bool:
    """Return whether a branch has an approved approach but no network decision."""
    repo_path = Path(repo).resolve()
    normalized = _patch_series_branch(branch)
    if not git_wrapper.branch_exists(repo_path, normalized):
        return False
    decision = vet_stateless_authorability(
        repo_path, normalized, "no_network_discovery"
    )
    if decision.blocked:
        return False
    frozen_ref = decision.source.frozen_root_oid
    root_snapshot = _vetted_root_snapshot(repo_path, decision.source)
    if not root_snapshot.lifecycle_ready:
        return False
    if not git_wrapper.has_subject(repo_path, frozen_ref, "patch_series: direction-ok"):
        return False
    if git_wrapper.has_subject(repo_path, frozen_ref, "network: right-sized"):
        return False
    metadata = _read_metadata(repo_path, frozen_ref)
    # precedent facet: child branch metadata inheritance hazard. Child metadata
    # identifies lifecycle-managed network nodes; elaborated nodes go to patch,
    # and requested nodes wait for dependency readiness, so neither re-enters
    # split_pending.
    is_child_metadata = bool(metadata.get("parent_branch") or metadata.get("child_key"))
    if is_child_metadata and metadata.get("lifecycle_status") and _canonical_lifecycle(metadata) in {"posted_to_mailing_list", "ready_for_patch_authoring", "claimed_by_patch_author", "submitted_for_maintainer_review", "merged_into_subsystem_tree"}:
        return False
    network = metadata.get("network")
    if isinstance(network, Mapping) and network.get("horizon_status") in {"leafed", "coarse"}:
        return False
    try:
        approach = root_snapshot.technical_approach()
        patch_series = root_snapshot.cover_letter()
    except (ValueError, TypeError):
        return False
    if right_sized(approach):
        return False
    ledger = _read_json(repo_path, frozen_ref, LEDGER_PATH)
    if (
        isinstance(ledger, Mapping)
        and ledger.get("parent_branch") == normalized
        and isinstance(ledger.get("children"), list)
    ):
        # A stamped ledger is already the network decision. Do not rejudge its
        # scope distribution as though no split had occurred.
        return False
    if isinstance(ledger, Mapping) and isinstance(patch_series, Mapping) and _ledger_covers_current_scope(ledger, _scope_items(patch_series, approach)):
        return False
    return bool(approach)


def _ledger_covers_current_scope(ledger: Mapping[str, Any], scope_items: list[Mapping[str, str]]) -> bool:
    scope_ids = {str(item.get("id")) for item in scope_items if item.get("id")}
    if not scope_ids:
        return False
    covered: set[str] = set()
    for entry in ledger.get("coverage", []):
        if isinstance(entry, Mapping) and entry.get("scope_item_id"):
            covered.add(str(entry["scope_item_id"]))
    retained = ledger.get("parent_retained_scope_ids")
    if isinstance(retained, list):
        covered.update(str(item) for item in retained if isinstance(item, str))
    for child in ledger.get("children", []):
        if isinstance(child, Mapping) and isinstance(child.get("scope_item_ids"), list):
            covered.update(str(item) for item in child["scope_item_ids"] if isinstance(item, str))
    return scope_ids <= covered


def coarse_ready(repo: str | Path, branch: str) -> bool:
    """Return whether a requested child may be elaborated."""
    repo_path = Path(repo).resolve()
    normalized, node_path = _parse_node_address(branch)
    decision = vet_stateless_authorability(
        repo_path,
        normalized,
        "elaboration_preparation",
    )
    if decision.blocked:
        return False
    return _coarse_ready_from_decision(
        repo_path,
        normalized,
        node_path,
        decision,
    )


def _coarse_ready_from_decision(
    repo: Path,
    branch: str,
    node_path: str,
    decision: AuthorabilityDecision,
) -> bool:
    """Evaluate elaboration readiness from one completed stateless vet."""

    frozen_ref = decision.source.frozen_root_oid
    if _vetted_root_snapshot(repo, decision.source).lifecycle_blocked:
        return False
    metadata = _read_node_manifest(repo, frozen_ref, node_path) if node_path != "." else _read_metadata(repo, frozen_ref)
    if not metadata:
        return False
    lifecycle_status = str(metadata.get("lifecycle_status", ""))
    if lifecycle_status == "stale" or lifecycle_status.startswith("blocked:"):
        return False
    if _canonical_lifecycle(metadata) != "posted_to_mailing_list":
        return False
    network = _load_manifest_network_from_git(
        repo,
        branch,
        frozen_root_oid=frozen_ref,
    )
    if network.get("errors"):
        return False
    warnings: list[dict[str, Any]] = []
    registry = network.get("declared_patchwork_checks", {}) if isinstance(network.get("declared_patchwork_checks"), Mapping) else {}
    registry_present = bool(network.get("declared_patchwork_checks_present"))
    state_projection = _project_tree_patchwork_check_events_from_git(repo, frozen_ref, registry=registry, registry_present=registry_present)
    if state_projection.get("errors"):
        return False
    nodes = network.get("nodes", {})
    if not isinstance(nodes, Mapping):
        return False
    key_set = set(nodes)
    serial_set = {
        str(node_manifest.get("serial_id"))
        for node_manifest in nodes.values()
        if isinstance(node_manifest, Mapping) and str(node_manifest.get("serial_id") or "")
    }
    for key, node_manifest in nodes.items():
        if not isinstance(node_manifest, Mapping):
            return False
        manifest_errors, _manifest_warnings = _validate_manifest_edges(
            str(key),
            node_manifest,
            nodes,
            key_set,
            serial_set,
            registry=registry,
            registry_present=registry_present,
        )
        if manifest_errors:
            return False
    state = _state_defaults(registry)
    state.update(state_projection["state"])
    return eligible(_manifest_key(metadata, node_path), state, network, warnings=warnings)


def ready_nested_elaboration(repo: str | Path) -> list[dict[str, Any]]:
    """Return ready request-only peer nodes in deterministic order."""
    repo_path = Path(repo).resolve()
    ready: list[dict[str, Any]] = []
    for branch in sorted(git_wrapper.branches(repo_path, f"{WORK_ORDER_PREFIX}*")):
        decision = vet_stateless_authorability(
            repo_path, branch, "elaboration_preparation"
        )
        if decision.blocked:
            continue
        frozen_ref = decision.source.frozen_root_oid
        status = _read_json(repo_path, frozen_ref, STATUS_PATH)
        if not isinstance(status, Mapping) or status.get("generated_from") != NODE_MANIFEST_PATH:
            continue
        for child in status.get("children", []):
            if not isinstance(child, Mapping):
                continue
            node_path = str(child.get("node_path", ""))
            address = _node_address(branch, node_path)
            if _coarse_ready_from_decision(
                repo_path,
                branch,
                node_path,
                decision,
            ):
                ready.append({"branch": branch, "node_path": node_path, "address": address, "child_key": child.get("child_key", "")})
    return ready


def validate_child_write_scope(repo: str | Path, node_address: str, base_ref: str, candidate_ref: str) -> dict[str, Any]:
    """Fail closed unless candidate changes are contained in the child subtree."""
    repo_path = Path(repo).resolve()
    branch, node_path = _parse_node_address(node_address)
    manifest = _read_node_manifest(repo_path, branch, node_path)
    allowed = str((manifest.get("write_scope") or {}).get("allowed_subtree") or (node_path.rstrip("/") + "/"))
    changes = git_wrapper.changed_paths(repo_path, base_ref, candidate_ref)
    changed_paths = sorted({path for change in changes for path in change.get("paths", []) if isinstance(path, str)})
    offending = [path for path in changed_paths if not path.startswith(allowed)]
    result = {
        "ok": not offending,
        "status": "ok" if not offending else "blocked:write-scope",
        "node_address": _node_address(branch, node_path),
        "allowed_subtree": allowed,
        "changed_paths": changed_paths,
        "offending_paths": offending,
    }
    return result


def validate_spine_ancestry(repo: str | Path, child_ref: str, required_spine_commits: list[str]) -> dict[str, Any]:
    """Check that required parent spine commits are ancestors of child_ref."""
    # Memento/TODO: patch series-0001 leaves `spine/` as a convention directory first.
    # Keep this gate commit-based until a consumer proves typed spine registry
    # fields are needed.
    repo_path = Path(repo).resolve()
    missing = [commit for commit in required_spine_commits if not git_wrapper.is_ancestor(repo_path, commit, child_ref)]
    return {"ok": not missing, "status": "ok" if not missing else "blocked:spine-ancestry", "missing_spine_commits": missing}


def stamp_children(
    repo: str | Path,
    parent_branch: str,
    template_spec: Mapping[str, Any],
    scale_table: list[Mapping[str, Any]],
    *,
    stamping_author: str = "",
    ctx: org_log.RunContext | None = None,
) -> dict[str, Any]:
    """Stamp sibling request-only child nodes from a template and scale table."""
    # Memento/TODO: stamped-node promotion to formation-grade is presumed to be
    # child escalation followed by parent re-authoring. Do not silently mutate a
    # stamped request into a novel formation node here.
    repo_path = Path(repo).resolve()
    branch = _patch_series_branch(parent_branch)
    log_ctx = ctx or org_log.RunContext(repo=repo_path, patch_series_id=_patch_series_id(branch), stage="patch_series.network.stamp_children")
    decision = vet_stateless_authorability(repo_path, branch, "stamping")
    if decision.blocked:
        result = _authorability_failure(decision, branch)
        org_log.emit(
            "patch_series.network.stamp_children.result",
            _lineage_result_payload(result),
            ctx=log_ctx,
            severity="warning",
        )
        return result
    root_snapshot = _vetted_root_snapshot(repo_path, decision.source)
    if not root_snapshot.lifecycle_ready:
        return _root_generation_failure(root_snapshot, branch)
    template_id = str(template_spec.get("id") or template_spec.get("template_id") or "template")
    files: dict[str, Any] = {}
    stamped: list[dict[str, Any]] = []
    stamped_manifests: list[dict[str, Any]] = []
    frozen_ref = decision.source.frozen_root_oid
    root_registry = _root_declared_patchwork_checks_from_git(
        repo_path, frozen_ref
    )
    if root_registry["errors"]:
        return {
            "ok": False,
            "status": "edge-form-invalid",
            "branch": branch,
            "node_path": ".",
            "errors": root_registry["errors"],
        }
    existing_spine_contract_paths = _existing_spine_contract_paths(
        repo_path, frozen_ref
    )
    try:
        product_contract = _product_contract_from_patch_series(
            root_snapshot.cover_letter()
        )
    except (ValueError, TypeError) as exc:
        result = _root_generation_failure(root_snapshot, branch)
        result["error"] = str(exc)
        return result
    for index, row in enumerate(scale_table, start=1):
        raw_child_key = str(row.get("child_key") or row.get("key") or f"child_{index}")
        if not _valid_child_key(raw_child_key):
            result = {
                "ok": False,
                "status": "child-key-invalid",
                "branch": branch,
                "node_path": ".",
                "errors": [{"type": "child_key_invalid", "child_key": raw_child_key, "message": "child_key must match [a-z0-9_]+"}],
            }
            org_log.emit("patch_series.network.stamp_children.result", _lineage_result_payload(result), ctx=log_ctx, severity="error")
            return result
        child_key = raw_child_key
        node_path = f"{SUBDIR}/{child_key}"
        title = str(row.get("title") or template_spec.get("title") or child_key.replace("_", " ").title())
        summary = str(row.get("summary") or template_spec.get("summary") or title)
        child = {
            "id": f"{_patch_series_id(branch)}:{node_path}",
            "serial_id": "",
            "child_key": child_key,
            "node_path": node_path,
            "edges": _normalize_edges(row.get("edges", []), child_key=child_key),
            "scope_item_ids": list(row.get("scope_item_ids", [])) if isinstance(row.get("scope_item_ids"), list) else [],
            "title": title,
            "summary": summary,
            "acceptance_criteria": _stamped_list_field(row, template_spec, "acceptance_criteria", "acceptance criteria not supplied by stamped request content"),
            "functional_check": _stamped_string_field(row, template_spec, "functional_check", "functional check not supplied by stamped request content"),
            "systems": [],
            "ux_acceptance_tests": _stamped_list_field(row, template_spec, "ux_acceptance_tests", "UX acceptance tests not supplied by stamped request content"),
            "risks": [],
            "allowed_subtree": f"{node_path}/",
            "contrib_branch": f"ai-org/contrib/{_patch_series_id(branch)}-{child_key}",
        }
        _copy_stamped_product_contract_overrides(child, row, template_spec)
        manifest = _child_manifest(branch, child, {child_key: child})
        # Stamped children are flat under root; coerce any branch-ref part_of `to`
        # to the parent node key and add the provenance edge if absent.
        _stamp_parent_provenance_edge(manifest["edges"], parent_key="root")
        manifest["lifecycle_status"] = "posted_to_mailing_list"
        manifest["stamping_provenance"] = {
            "template_id": template_id,
            "scale_table_row": dict(row),
            "stamping_author_signature": stamping_author,
        }
        files[f"{node_path}/maintainer-series-request.json"] = _child_request_envelope(branch, child, existing_spine_contract_paths, product_contract=product_contract)
        files[f"{node_path}/{NODE_MANIFEST_PATH}"] = manifest
        stamped.append({"child_key": child_key, "node_path": node_path, "address": _node_address(branch, node_path)})
        stamped_manifests.append(manifest)
        edge_errors = _manifest_edge_form_errors(
            child_key,
            manifest,
            registry=root_registry["registry"],
            registry_present=root_registry["present"],
        )
        if edge_errors:
            return {
                "ok": False,
                "status": "edge-form-invalid",
                "branch": branch,
                "node_path": node_path,
                "errors": edge_errors,
            }
    try:
        files.update(
            _stamped_root_projection_files(
                repo_path,
                branch,
                stamped_manifests,
                source=decision.source,
            )
        )
    except _AuthorabilityBlocked as exc:
        result = _authorability_failure(exc.decision, branch)
        org_log.emit(
            "patch_series.network.stamp_children.result",
            _lineage_result_payload(result),
            ctx=log_ctx,
            severity="warning",
        )
        return result
    tx_validation = _validate_nested_file_map(files)
    if not tx_validation["ok"]:
        return {"ok": False, "status": "nested-artifact-transaction-invalid", "branch": branch, "validation": tx_validation}
    written = _commit_network_files(
        repo_path,
        branch,
        files,
        subject="network: stamp children",
        source=decision.source,
        transition_route="stamping",
    )
    result = {"ok": True, "status": "stamped", "branch": branch, "commit": written["commit"], "children": stamped}
    org_log.emit("patch_series.network.stamp_children.result", _lineage_result_payload(result), ctx=log_ctx)
    return result


def _stamped_list_field(
    row: Mapping[str, Any],
    template_spec: Mapping[str, Any],
    field: str,
    absent_reason: str,
) -> list[str]:
    for source in (row, template_spec):
        value = source.get(field)
        if isinstance(value, list):
            items = [str(item).strip() for item in value if str(item).strip()]
            if items:
                return items
        if isinstance(value, str) and value.strip():
            return [value.strip()]
    return [f"ABSENT: {absent_reason}."]


def _stamped_string_field(
    row: Mapping[str, Any],
    template_spec: Mapping[str, Any],
    field: str,
    absent_reason: str,
) -> str:
    for source in (row, template_spec):
        value = source.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return f"ABSENT: {absent_reason}."


def _copy_stamped_product_contract_overrides(
    child: dict[str, Any],
    row: Mapping[str, Any],
    template_spec: Mapping[str, Any],
) -> None:
    for field in ("tech_stack", "user_experience_requirements"):
        for source in (row, template_spec):
            value = source.get(field)
            if isinstance(value, Mapping):
                child[field] = copy.deepcopy(dict(value))
                break


def _stamped_root_projection_files(
    repo: Path,
    branch: str,
    stamped_manifests: list[Mapping[str, Any]],
    *,
    source: FrozenNetworkSourceVector,
) -> dict[str, Any]:
    frozen = source.frozen_root_oid
    # The stamping preflight already froze the root generation.  Project the
    # fallback from that same tree so a concurrently advanced branch cannot
    # mix new manifests or ledger data into the vetted source vector.
    root_manifest = _read_node_manifest(repo, frozen, ".")
    ledger = _read_json(repo, frozen, LEDGER_PATH)
    if not root_manifest or not isinstance(ledger, Mapping):
        fallback_decision = vet_stateless_authorability(
            repo,
            branch,
            "scope_absent_manifest_fallback",
            frozen_root_oid=frozen,
        )
        if fallback_decision.blocked:
            # Keep the structured decision intact until the public stamping
            # result is formed. Stringifying it here loses the CUE context,
            # rule, affected producer identity, and frozen source vector.
            raise _AuthorabilityBlocked(fallback_decision)
        # This remains a representation fallback only; the exact stamping
        # source is vetted before any default manifest or ledger is formed.
    root_manifest = root_manifest or {
        "schema": "patch_series-network-node-v1",
        "child_key": "root",
        "node_path": ".",
        "branch": branch,
        "lifecycle_status": "active",
        "edges": [],
        "write_scope": {"allowed_subtree": "."},
    }
    root_manifest = dict(root_manifest)
    root_manifest.setdefault("schema", "patch_series-network-node-v1")
    root_manifest.setdefault("child_key", "root")
    root_manifest.setdefault("node_path", ".")
    root_manifest.setdefault("branch", branch)
    root_manifest.setdefault("lifecycle_status", "active")
    root_manifest.setdefault("edges", [])
    children = _projected_root_children_from_git(repo, frozen)
    for manifest in stamped_manifests:
        child_key = str(manifest.get("child_key") or Path(str(manifest.get("node_path") or "")).name)
        children[child_key] = _root_child_projection(manifest)
    root_manifest["children"] = [children[key] for key in sorted(children)]

    existing_ledger_children: dict[str, Mapping[str, Any]] = {}
    if not isinstance(ledger, Mapping):
        ledger = {
            "schema": "series-coverage-ledger-v1",
            "ledger_revision": 1,
            "supersedes_ledger_commit": "",
            "rebaselined_from_escalation": {},
            "parent_branch": branch,
            "relation": "stamped-children",
            "split_operator": "AND",
            "scope_items": [],
            "coverage": [],
            "parent_retained_scope_ids": [],
        }
    else:
        for child in ledger.get("children", []):
            if not isinstance(child, Mapping):
                continue
            key = str(child.get("child_key") or Path(str(child.get("node_path") or "")).name)
            if key:
                existing_ledger_children[key] = child
    ledger = dict(ledger)
    stamped_ledger_children = {
        str(manifest.get("child_key") or Path(str(manifest.get("node_path") or "")).name): manifest
        for manifest in stamped_manifests
    }
    ledger_children: list[dict[str, Any]] = []
    for child in root_manifest["children"]:
        key = str(child.get("child_key") or Path(str(child.get("node_path") or "")).name)
        if key in stamped_ledger_children:
            ledger_children.append(_merge_ledger_child(existing_ledger_children.get(key), stamped_ledger_children[key], prefer_current_rich=True))
        else:
            ledger_children.append(_merge_ledger_child(existing_ledger_children.get(key), child, prefer_current_rich=False))
    ledger["children"] = ledger_children
    return {
        NODE_MANIFEST_PATH: root_manifest,
        LEDGER_PATH: ledger,
        STATUS_PATH: _lineage_status(root_manifest, ledger),
    }


def _projected_root_children_from_git(repo: Path, branch: str) -> dict[str, dict[str, Any]]:
    children: dict[str, dict[str, Any]] = {}
    for path in git_wrapper.tree_files(repo, branch):
        if not _is_direct_node_manifest_path(path):
            continue
        node_path = str(Path(path).parent)
        manifest = _read_node_manifest(repo, branch, node_path)
        if not manifest:
            continue
        key = str(manifest.get("child_key") or Path(node_path).name)
        children[key] = _root_child_projection(manifest)
    return children


def _root_child_projection(manifest: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "child_key": str(manifest.get("child_key") or Path(str(manifest.get("node_path") or "")).name),
        "node_path": str(manifest.get("node_path") or ""),
        "lifecycle_status": _canonical_lifecycle(manifest),
        "edges": list(manifest.get("edges", [])) if isinstance(manifest.get("edges"), list) else [],
    }


def _ledger_child_projection(child: Mapping[str, Any]) -> dict[str, Any]:
    node_path = str(child.get("node_path") or "")
    child_key = str(child.get("child_key") or Path(node_path).name)
    return {
        "id": str(child.get("id") or ""),
        "serial_id": str(child.get("serial_id") or ""),
        "child_key": child_key,
        "node_path": node_path,
        "address": str(child.get("address") or ""),
        "allowed_subtree": str(child.get("allowed_subtree") or f"{node_path.rstrip('/')}/"),
        "contrib_branch": str(child.get("contrib_branch") or ""),
        "lifecycle_status": str(child.get("lifecycle_status") or "posted_to_mailing_list"),
        "edges": list(child.get("edges", [])) if isinstance(child.get("edges"), list) else [],
        "scope_item_ids": list(child.get("scope_item_ids", [])) if isinstance(child.get("scope_item_ids"), list) else [],
    }


def _merge_ledger_child(
    existing: Mapping[str, Any] | None,
    current: Mapping[str, Any],
    *,
    prefer_current_rich: bool,
) -> dict[str, Any]:
    merged = _ledger_child_projection(existing or current)
    current_projection = _ledger_child_projection(current)
    for field in ("child_key", "node_path", "lifecycle_status", "edges"):
        merged[field] = current_projection[field]
    for field in ("id", "serial_id", "address", "allowed_subtree", "contrib_branch"):
        if prefer_current_rich or current_projection[field]:
            merged[field] = current_projection[field]
    if prefer_current_rich or current_projection["scope_item_ids"]:
        merged["scope_item_ids"] = current_projection["scope_item_ids"]
    return merged


def _split_coverage_projection(
    split: Mapping[str, Any], scope_items: list[Mapping[str, str]]
) -> dict[str, Any]:
    """Project compatibility ledger rows from the already-partitioned graph.

    This is not a split validator. Ownership is a deterministic consequence of
    the CUE slices: component work items belong to that leaf and every other
    legacy scope row remains on the root.
    """
    owner_by_id = {
        str(scope_id): str(child.get("child_key") or "root")
        for child in split.get("children", [])
        if isinstance(child, Mapping)
        for scope_id in child.get("scope_item_ids", [])
    }
    retained = [
        str(item["id"])
        for item in scope_items
        if str(item["id"]) not in owner_by_id
    ]
    return {
        "ok": True,
        "coverage": [
            {
                "scope_item_id": str(item["id"]),
                "owner": owner_by_id.get(str(item["id"]), "root"),
            }
            for item in sorted(scope_items, key=lambda value: str(value["id"]))
        ],
        "retained_scope_ids": sorted(retained),
    }


def _lineage_result_payload(result: Mapping[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": bool(result.get("ok")),
        "status": result.get("status"),
        "branch": result.get("branch"),
        "parent_branch": result.get("parent_branch"),
        "ledger_commit": result.get("ledger_commit"),
        "commit": result.get("commit"),
        "error": result.get("error"),
        "route": result.get("route"),
        "frozen_oid": result.get("frozen_oid"),
        "diagnostic": result.get("diagnostic"),
        "authorability_decision": result.get("authorability_decision"),
    }
    for key in ("failure_mode", "rejection_fingerprint", "validator_id", "error_class", "path"):
        if result.get(key):
            payload[key] = result.get(key)
    for key in ("children", "branches", "stale"):
        value = result.get(key)
        if isinstance(value, list):
            payload[f"{key}_count"] = len(value)
    return {key: value for key, value in payload.items() if value not in (None, "")}


def _split_plan_graph(
    repo: Path,
    branch: str,
    patch_series: Mapping[str, Any],
    approach: Mapping[str, Any],
    scope_items: list[Mapping[str, str]],
    horizon: int,
    feedback: str,
    *,
    frozen_root_oid: str | None = None,
    ctx: org_log.RunContext | None = None,
) -> dict[str, Any]:
    """Partition the committed plan graph; no second author or split judge."""
    decision = vet_stateless_authorability(
        repo,
        branch,
        "split_preparation",
        technical_approach=approach,
        frozen_root_oid=frozen_root_oid,
    )
    if decision.blocked:
        return _authorability_failure(decision, branch)
    try:
        split = _partition_plan_graph(approach)
    except (TypeError, ValueError) as exc:
        return {
            "ok": False,
            "status": "invalid-plan-graph",
            "branch": branch,
            "error": str(exc),
        }
    return {"ok": True, "split": split}


def _split_prompt(
    branch: str,
    patch_series: Mapping[str, Any],
    approach: Mapping[str, Any],
    scope_items: list[Mapping[str, str]],
    horizon: int,
    feedback: str,
) -> str:
    """Human-readable trace of the mechanical split (never sent to a model)."""
    graph = _plan_graph(approach)
    return (
        "Deterministic CUE plan partition: weakly connected components become "
        "independent leaves; dependency references are the only graph edges.\n"
        "Scenario discipline: canon/world rules stay at root or spine. Dialogue and quest prose are text "
        "commission requests with string keys, speaker tags, length limits, and flag references. "
        "Flag graph/state machinery is code. Never mix canon, authored text, and flag machinery in one child.\n"
        f"patch-series branch: {branch}\n"
        f"plan graph:\n{json.dumps(graph, indent=2, sort_keys=True, ensure_ascii=True)}"
    )


def _plan_graph(approach: Mapping[str, Any]) -> list[dict[str, Any]]:
    patch_plan = _find_first_mapping(approach, "patch_plan")
    if not patch_plan:
        raise ValueError("technical approach has no patch_plan")
    graph_items = _list_of_mappings(patch_plan.get("items"))
    if graph_items:
        items = [dict(item) for item in graph_items]
    else:
        # Historical positional plans had an implicit total order. Preserve
        # that meaning as one explicit chain at this read-only boundary.
        first = patch_plan.get("first_proof_moment")
        if not isinstance(first, Mapping):
            raise ValueError("patch_plan has no executable work items")
        plan_id = str(patch_plan.get("id") or "patch_plan")
        items = [
            {
                "id": f"{plan_id}#first_proof_moment",
                "objective": _compact_text(first),
                "named_content": list(first.get("named_content", [])),
                "acceptance_criteria": [str(first.get("win_or_progress_condition") or "")],
                "how_verified": str(first.get("how_verified") or ""),
                "depends_on": [],
            }
        ]
        previous_id = items[0]["id"]
        for index, follow_up in enumerate(_list_of_mappings(patch_plan.get("follow_ups")), start=1):
            item_id = f"{plan_id}#follow-up-{index:02d}"
            items.append(
                {
                    "id": item_id,
                    "objective": str(follow_up.get("adds") or _compact_text(follow_up)),
                    "named_content": list(follow_up.get("named_content", [])),
                    "acceptance_criteria": [],
                    "how_verified": str(follow_up.get("how_verified") or ""),
                    "depends_on": [previous_id],
                }
            )
            previous_id = item_id

    ids = [str(item.get("id") or "") for item in items]
    if not all(ids) or len(ids) != len(set(ids)):
        raise ValueError("patch_plan work-item ids must be unique and non-empty")
    known = set(ids)
    for item in items:
        dependencies = item.get("depends_on")
        if not isinstance(dependencies, list) or not all(isinstance(value, str) for value in dependencies):
            raise ValueError(f"patch_plan item {item.get('id')!r} has invalid depends_on references")
        unresolved = sorted(set(dependencies) - known)
        if unresolved:
            raise ValueError(f"patch_plan item {item['id']!r} has unresolved dependencies: {unresolved}")
    return items


def _weak_components(items: list[Mapping[str, Any]]) -> list[list[dict[str, Any]]]:
    by_id = {str(item["id"]): dict(item) for item in items}
    neighbors = {item_id: set() for item_id in by_id}
    for item_id, item in by_id.items():
        for dependency in item.get("depends_on", []):
            neighbors[item_id].add(dependency)
            neighbors[dependency].add(item_id)
    components: list[list[dict[str, Any]]] = []
    seen: set[str] = set()
    order = list(by_id)
    for start in order:
        if start in seen:
            continue
        stack = [start]
        member_ids: set[str] = set()
        while stack:
            current = stack.pop()
            if current in member_ids:
                continue
            member_ids.add(current)
            stack.extend(sorted(neighbors[current] - member_ids, reverse=True))
        seen.update(member_ids)
        components.append([by_id[item_id] for item_id in order if item_id in member_ids])
    return components


def _partition_plan_graph(approach: Mapping[str, Any]) -> dict[str, Any]:
    items = _plan_graph(approach)
    components = _weak_components(items)
    if len(components) == 1:
        return {
            "split_mode": "right_sized",
            "rationale": "one dependency component; keep the chain in one leaf",
            "children": [],
        }
    patch_plan = _find_first_mapping(approach, "patch_plan") or {}
    used_keys: set[str] = set()
    children: list[dict[str, Any]] = []
    for index, component in enumerate(components, start=1):
        first_id = str(component[0]["id"])
        base_key = re.sub(r"[^a-z0-9_]+", "_", first_id.lower()).strip("_") or f"component_{index}"
        child_key = base_key
        suffix = 2
        while child_key in used_keys:
            child_key = f"{base_key}_{suffix}"
            suffix += 1
        used_keys.add(child_key)
        criteria = [
            criterion
            for item in component
            for criterion in item.get("acceptance_criteria", [])
            if isinstance(criterion, str) and criterion.strip()
        ]
        checks = [
            str(item.get("how_verified") or "").strip()
            for item in component
            if str(item.get("how_verified") or "").strip()
        ]
        children.append(
            {
                "child_key": child_key,
                "title": str(component[0].get("objective") or first_id),
                "stage_name": "independent",
                "edges": [],
                "summary": "; ".join(str(item.get("objective") or item["id"]) for item in component),
                "acceptance_criteria": criteria or [f"Complete and verify {first_id}."],
                "functional_check": "; ".join(checks),
                "patch_plan": {
                    **({"id": patch_plan["id"]} if isinstance(patch_plan.get("id"), str) else {}),
                    "items": component,
                    "deferred": [],
                    "open_questions": [],
                },
                "systems": [],
                "ux_acceptance_tests": [],
                "risks": [],
            }
        )
    return {
        "split_mode": "split_into_children",
        "rationale": f"{len(children)} independent dependency components",
        "children": children,
    }


def _normalize_split(
    repo: Path,
    parent_branch: str,
    split: Mapping[str, Any],
    scope_items: list[Mapping[str, str]],
    horizon: int,
) -> dict[str, Any]:
    root_id = _root_serial_id(repo, parent_branch)
    scope_ids = {str(item.get("id") or "") for item in scope_items}
    item_owner: dict[str, str] = {}
    for child in split["children"]:
        if not isinstance(child, Mapping):
            continue
        patch_plan = child.get("patch_plan")
        if not isinstance(patch_plan, Mapping):
            continue
        for item in _list_of_mappings(patch_plan.get("items")):
            item_id = str(item.get("id") or "")
            if item_id:
                item_owner[item_id] = str(child.get("child_key") or "")

    prepared_children: list[dict[str, Any]] = []
    for child in split["children"]:
        copy = dict(child)
        child_key = _safe_child_key(str(child["child_key"]))
        copy["child_key"] = child_key
        edges = list(copy.get("edges", []))
        patch_plan = copy.get("patch_plan")
        component_ids: list[str] = []
        if isinstance(patch_plan, Mapping):
            for item in _list_of_mappings(patch_plan.get("items")):
                item_id = str(item.get("id") or "")
                if item_id:
                    component_ids.append(item_id)
                for dependency in item.get("depends_on", []):
                    owner = item_owner.get(str(dependency))
                    if owner and owner != child_key:
                        edges.append(
                            {
                                "type": "depends",
                                "to": owner,
                                "with": "",
                                "state": "merged_into_subsystem_tree",
                                "or_group": "",
                                "when": "",
                                "reason": f"work item {item_id} builds on {dependency}",
                            }
                        )
        elif isinstance(child.get("scope_item_ids"), list):
            # Read-only compatibility for already-formed historical carriers;
            # the deterministic splitter never emits this field.
            component_ids = [
                str(item_id)
                for item_id in child["scope_item_ids"]
                if isinstance(item_id, str)
            ]
        copy["scope_item_ids"] = [item_id for item_id in component_ids if item_id in scope_ids]
        copy["edges"] = _normalize_edges(edges, child_key=child_key)
        _stamp_parent_provenance_edge(copy["edges"], parent_key="root")
        prepared_children.append(copy)

    depths = _child_depths(prepared_children)
    normalized_children: list[dict[str, Any]] = []
    for index, child in enumerate(prepared_children, start=1):
        copy = dict(child)
        child_key = _safe_child_key(str(child["child_key"]))
        copy["child_key"] = child_key
        # Memento, serial timing: precedent facet "weak composable change
        # identity" plus P1 makes the forming identity the nest position. Child
        # serial sub-numbers are assigned only when a later direction/acceptance
        # boundary needs them; group-merge formation must not mint a registry.
        copy["id"] = f"{root_id}:{SUBDIR}/{child_key}"
        copy["serial_id"] = ""
        copy["node_path"] = f"{SUBDIR}/{child_key}"
        copy["address"] = _node_address(parent_branch, copy["node_path"])
        copy["allowed_subtree"] = f"{SUBDIR}/{child_key}/"
        copy["contrib_branch"] = f"ai-org/contrib/{_patch_series_id(parent_branch)}-{child_key}"
        depth = depths.get(copy["child_key"], 0)
        copy["lifecycle_status"] = "ready_for_patch_authoring" if depth < max(1, horizon) else "posted_to_mailing_list"
        copy["acceptance_criteria"] = list(copy["acceptance_criteria"])
        normalized_children.append(copy)
    child_scope_ids = {
        item_id
        for child in normalized_children
        for item_id in child.get("scope_item_ids", [])
    }
    return {
        "split_mode": split["split_mode"],
        "rationale": split["rationale"],
        "parent_retained_scope_ids": sorted(scope_ids - child_scope_ids),
        "children": normalized_children,
    }


def _nested_node_files(
    repo: Path,
    parent_branch: str,
    split: Mapping[str, Any],
    ledger: Mapping[str, Any],
    root_manifest: Mapping[str, Any],
    *,
    product_contract: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    files: dict[str, Any] = {
        LEDGER_PATH: dict(ledger),
        NODE_MANIFEST_PATH: dict(root_manifest),
        STATUS_PATH: _lineage_status(root_manifest, ledger),
    }
    by_key = {child["child_key"]: child for child in split["children"]}
    existing_spine_contract_paths = _existing_spine_contract_paths(repo, parent_branch)
    for child in _topological_children(split["children"]):
        node_path = str(child["node_path"])
        manifest = _child_manifest(parent_branch, child, by_key)
        files[f"{node_path}/maintainer-series-request.json"] = _child_request_envelope(parent_branch, child, existing_spine_contract_paths, product_contract=product_contract)
        files[f"{node_path}/{NODE_MANIFEST_PATH}"] = manifest
        # The request-only lifecycle remains request-only, but its registered
        # cover/approach/metadata variants publish with the same immutable tree.
        # Presence is representation, not a readiness decision.
        files[f"{node_path}/patch-series-cover-letter.json"] = _child_patch_series(child, existing_spine_contract_paths, product_contract=product_contract)
        files[f"{node_path}/technical-approach-plan.json"] = _child_approach(child)
        files[f"{node_path}/{METADATA_PATH}"] = _child_metadata(parent_branch, child, by_key, "")
    return files


def _validate_nested_file_map(files: Mapping[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    child_paths: set[str] = set()
    for rel_path, payload in files.items():
        if rel_path.startswith("/") or "/../" in f"/{rel_path}/" or rel_path in {"..", "."}:
            errors.append(f"unsafe path: {rel_path}")
        if rel_path.startswith(f"{SUBDIR}/"):
            parts = rel_path.split("/")
            if len(parts) < 3 or not parts[1]:
                errors.append(f"malformed child path: {rel_path}")
            else:
                child_paths.add(f"{SUBDIR}/{parts[1]}")
        if rel_path.endswith(NODE_MANIFEST_PATH):
            if not isinstance(payload, Mapping):
                errors.append(f"{rel_path} must be an object")
            elif str(payload.get("node_path", "")) not in {"", "."} and not rel_path.startswith(str(payload["node_path"])):
                errors.append(f"{rel_path} does not match manifest node_path {payload.get('node_path')}")
        if rel_path.endswith("maintainer-series-request.json") and not _valid_request_envelope(payload):
            errors.append(f"{rel_path} is not a submit.py inbox request envelope")
    for child_path in sorted(child_paths):
        if f"{child_path}/{NODE_MANIFEST_PATH}" not in files:
            errors.append(f"{child_path} missing {NODE_MANIFEST_PATH}")
        if f"{child_path}/maintainer-series-request.json" not in files:
            errors.append(f"{child_path} missing maintainer-series-request.json")
    return {"ok": not errors, "errors": errors}


def _legacy_branch_child_ledger(ledger: Any) -> bool:
    if not isinstance(ledger, Mapping):
        return False
    children = ledger.get("children")
    if not isinstance(children, list):
        return False
    return any(isinstance(child, Mapping) and isinstance(child.get("branch"), str) for child in children)


def _nested_child_results(split: Mapping[str, Any], commit: str) -> list[dict[str, Any]]:
    return [
        {
            "id": child["id"],
            "child_key": child["child_key"],
            "node_path": child["node_path"],
            "address": child["address"],
            "commit": commit,
            "lifecycle_status": child["lifecycle_status"],
            "contrib_branch": child["contrib_branch"],
        }
        for child in split["children"]
    ]


def _removed_branch_child_writer(repo: Path, parent_branch: str, split: Mapping[str, Any], ledger_commit: str) -> list[dict[str, Any]]:
    """Reject the pre-nesting document-branch writer if an old caller reaches it."""
    raise RuntimeError("legacy child patch series branch creation is disabled; write nested sub/<child_key>/ nodes")


def _ledger(
    parent_branch: str,
    scope_items: list[Mapping[str, str]],
    split: Mapping[str, Any],
    validation: Mapping[str, Any],
    *,
    ledger_revision: int = 1,
    supersedes_ledger_commit: str = "",
    rebaselined_from_escalation: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema": "series-coverage-ledger-v1",
        "ledger_revision": ledger_revision,
        "supersedes_ledger_commit": supersedes_ledger_commit,
        "rebaselined_from_escalation": dict(rebaselined_from_escalation or {}),
        "parent_branch": parent_branch,
        "relation": "split-into",
        "split_operator": "AND",
        "scope_items": list(scope_items),
        "coverage": validation["coverage"],
        "parent_retained_scope_ids": validation["retained_scope_ids"],
        "children": [
            {
                "id": child["id"],
                "serial_id": child.get("serial_id", ""),
                "child_key": child["child_key"],
                "node_path": child["node_path"],
                "address": child["address"],
                "allowed_subtree": child["allowed_subtree"],
                "contrib_branch": child["contrib_branch"],
                "lifecycle_status": child["lifecycle_status"],
                "edges": list(child["edges"]),
                "scope_item_ids": child["scope_item_ids"],
            }
            for child in split["children"]
        ],
    }


def _right_sized_node_files(
    parent_branch: str,
    scope_items: list[Mapping[str, str]],
    declared_patchwork_checks: Any = None,
) -> dict[str, Any]:
    children: list[dict[str, Any]] = []
    retained_ids = [item["id"] for item in scope_items]
    ledger = {
        "schema": "series-coverage-ledger-v1",
        "ledger_revision": 1,
        "supersedes_ledger_commit": "",
        "rebaselined_from_escalation": {},
        "parent_branch": parent_branch,
        "relation": "right-sized",
        "split_operator": "LEAF",
        "scope_items": list(scope_items),
        "coverage": [
            {"scope_item_id": item["id"], "owner": "root", "owner_node_path": "."}
            for item in scope_items
        ],
        "parent_retained_scope_ids": retained_ids,
        "children": children,
    }
    split = {"children": children}
    validation = {"retained_scope_ids": retained_ids}
    root_manifest = _root_manifest(parent_branch, scope_items, split, validation, declared_patchwork_checks)
    root_manifest["identity_stage"] = "right-sized-root"
    root_manifest["relation_from_parent"] = "root"
    root_manifest["lifecycle_status"] = "ready_for_patch_authoring"
    return {
        LEDGER_PATH: ledger,
        NODE_MANIFEST_PATH: root_manifest,
        STATUS_PATH: _lineage_status(root_manifest, ledger),
    }


def _dependency_graph_projection(children: list[Mapping[str, Any]]) -> list[dict[str, str]]:
    by_key = {str(child["child_key"]): child for child in children if isinstance(child, Mapping) and child.get("child_key")}
    return [
        {
            "prerequisite_node_path": str(by_key[edge["prerequisite_child_key"]]["node_path"]),
            "dependent_node_path": str(by_key[edge["dependent_child_key"]]["node_path"]),
        }
        for edge in _dependency_edges(list(children))
        if edge["prerequisite_child_key"] in by_key and edge["dependent_child_key"] in by_key
    ]


def _root_manifest(
    parent_branch: str,
    scope_items: list[Mapping[str, str]],
    split: Mapping[str, Any],
    validation: Mapping[str, Any],
    declared_patchwork_checks: Any = None,
) -> dict[str, Any]:
    # Memento: `patch-series-manifest.json` is the authority surface. `patch-queue-status-rollup.json`
    # below is generated only, because the promoted plan named "three competing
    # authorities" as a risk if status, ledger, and manifests all become writable
    # sources of truth.
    manifest = {
        "schema": "patch_series-network-node-v1",
        "identity_stage": "serialized-parent",
        "node_path": ".",
        "branch": parent_branch,
        "serial_id": _patch_series_id(parent_branch) if _looks_like_serial_chain(_patch_series_id(parent_branch)) else "",
        "relation_from_parent": "root",
        "lifecycle_status": "active",
        "ownership": {"request_owner": "requester", "interior_owner": "parent"},
        "write_scope": {"allowed_subtree": "."},
        "scope_item_ids": [item["id"] for item in scope_items],
        "parent_retained_scope_ids": list(validation["retained_scope_ids"]),
        "children": [
            {
                "child_key": child["child_key"],
                "node_path": child["node_path"],
                "lifecycle_status": child["lifecycle_status"],
                "edges": list(child["edges"]),
            }
            for child in split["children"]
        ],
    }
    registry = _registry_entries_for_manifest(declared_patchwork_checks)
    if registry:
        manifest[DECLARED_PATCHWORK_CHECKS_FIELD] = registry
    return manifest


def _declared_patchwork_checks_source(patch_series: Mapping[str, Any], approach: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    for source in (approach, patch_series):
        value = source.get(DECLARED_PATCHWORK_CHECKS_FIELD)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, Mapping)]
    return []


def _registry_entries_for_manifest(value: Any) -> list[dict[str, str]]:
    registry, _errors = _parse_declared_patchwork_checks(value, node="root", present=isinstance(value, list))
    return [{"name": name, "type": registry[name]} for name in sorted(registry)]


def _parse_declared_patchwork_checks(
    value: Any,
    *,
    node: str = "root",
    present: bool = False,
) -> tuple[dict[str, str], list[dict[str, Any]]]:
    if not present:
        return {}, []
    if not isinstance(value, list):
        return {}, [
            {
                "type": "declared_patchwork_checks_invalid",
                "node": node,
                "message": f"{DECLARED_PATCHWORK_CHECKS_FIELD} must be an array",
            }
        ]
    registry: dict[str, str] = {}
    errors: list[dict[str, Any]] = []
    for index, entry in enumerate(value):
        if not isinstance(entry, Mapping):
            errors.append(
                {
                    "type": "declared_patchwork_checks_invalid",
                    "node": node,
                    "index": index,
                    "message": "registry entries must be objects",
                }
            )
            continue
        name = entry.get("name")
        var_type = entry.get("type")
        if not isinstance(name, str) or _PATCHWORK_CHECK_NAME_RE.fullmatch(name) is None:
            errors.append(
                {
                    "type": "declared_patchwork_checks_invalid",
                    "node": node,
                    "index": index,
                    "message": "registry name must match [a-z0-9_]+",
                }
            )
            continue
        if var_type not in PATCHWORK_CHECK_TYPES:
            errors.append(
                {
                    "type": "declared_patchwork_checks_invalid",
                    "node": node,
                    "index": index,
                    "variable": name,
                    "message": "registry type must be counter or text",
                }
            )
            continue
        if name in registry:
            errors.append(
                {
                    "type": "declared_patchwork_checks_invalid",
                    "node": node,
                    "index": index,
                    "variable": name,
                    "message": "registry variable names must be unique",
                }
            )
            continue
        registry[name] = str(var_type)
    return registry, errors


def _state_defaults(registry: Mapping[str, str]) -> dict[str, Any]:
    return {name: PATCHWORK_CHECK_DEFAULTS[var_type] for name, var_type in registry.items()}


def _root_declared_patchwork_checks_from_git(repo: Path, branch: str) -> dict[str, Any]:
    manifest = _read_node_manifest(repo, branch, ".")
    present = DECLARED_PATCHWORK_CHECKS_FIELD in manifest
    registry, errors = _parse_declared_patchwork_checks(
        manifest.get(DECLARED_PATCHWORK_CHECKS_FIELD),
        node="root",
        present=present,
    )
    return {"present": present, "registry": registry, "errors": errors}


def _child_request_envelope(
    parent_branch: str,
    child: Mapping[str, Any],
    existing_spine_contract_paths: set[str],
    *,
    product_contract: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    # Memento: parent-side decomposition patch series means the parent writes a
    # front-door request envelope, not a bespoke contract artifact. The child can
    # be read as if it arrived through submit.py on its own.
    request = _child_patch_series(child, existing_spine_contract_paths, product_contract=product_contract)
    commission = (
        _commission_request(child, existing_spine_contract_paths)
        if _is_commission_request(child)
        else _empty_commission_request()
    )
    return {
        "id": f"{_patch_series_id(parent_branch)}-{child['child_key']}",
        "submitted_at": "",
        "request": request,
        "commission_request": commission,
        "provenance": {
            "requester": "parent",
            "parent_branch": parent_branch,
            "parent_node_path": ".",
            "child_key": child["child_key"],
        },
    }


def _valid_request_envelope(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    request = value.get("request")
    return (
        isinstance(value.get("id"), str)
        and isinstance(value.get("submitted_at"), str)
        and isinstance(request, Mapping)
        and isinstance(request.get("raw_request"), str)
        and isinstance(request.get("working_title"), str)
    )


def _child_metadata(parent_branch: str, child: Mapping[str, Any], by_key: Mapping[str, Mapping[str, Any]], ledger_commit: str) -> dict[str, Any]:
    return {
        "schema": "patch_series-network-node-v1",
        "id": child["id"],
        "serial_id": child.get("serial_id", ""),
        "child_key": child["child_key"],
        "parent_branch": parent_branch,
        "ledger_commit": ledger_commit,
        "relation_from_parent": "split-into",
        "split_operator": "AND",
        "edges": list(child["edges"]),
        "scope_item_ids": child["scope_item_ids"],
        "lifecycle_status": child.get("lifecycle_status", "ready_for_patch_authoring"),
    }


def _child_manifest(parent_branch: str, child: Mapping[str, Any], by_key: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "schema": "patch_series-network-node-v1",
        "identity_stage": "forming",
        "id": child["id"],
        "serial_id": child.get("serial_id", ""),
        "child_key": child["child_key"],
        "node_path": child["node_path"],
        "parent_branch": parent_branch,
        "parent_node_path": ".",
        "relation_from_parent": "split-into",
        "split_operator": "AND",
        "edges": list(child["edges"]),
        "scope_item_ids": child["scope_item_ids"],
        "lifecycle_status": child.get("lifecycle_status", "posted_to_mailing_list"),
        "ownership": {"request_owner": "parent", "interior_owner": "child"},
        "write_scope": {"allowed_subtree": child["allowed_subtree"]},
        "contrib_branch": child["contrib_branch"],
        "title": child.get("title", ""),
        "summary": child.get("summary", ""),
        "acceptance_criteria": list(child.get("acceptance_criteria", [])),
        "functional_check": child.get("functional_check", ""),
        "systems": list(child.get("systems", [])),
        "ux_acceptance_tests": list(child.get("ux_acceptance_tests", [])),
        "risks": list(child.get("risks", [])),
    }


def _child_from_manifest_for_elaboration(repo: Path, branch: str, manifest: Mapping[str, Any]) -> dict[str, Any]:
    child_key = str(manifest.get("child_key") or Path(str(manifest.get("node_path", "child"))).name)
    return {
        "id": str(manifest.get("id") or f"{_patch_series_id(branch)}:{manifest.get('node_path', '')}"),
        "serial_id": str(manifest.get("serial_id") or ""),
        "child_key": child_key,
        "node_path": str(manifest.get("node_path") or f"{SUBDIR}/{child_key}"),
        "lifecycle_status": "ready_for_patch_authoring",
        "title": str(manifest.get("title") or f"{child_key.title()} Child"),
        "summary": str(manifest.get("summary") or f"Elaborate {child_key}."),
        "acceptance_criteria": list(manifest.get("acceptance_criteria", [])),
        "functional_check": str(manifest.get("functional_check") or f"Verify {child_key}."),
        "systems": list(manifest.get("systems", [])),
        "ux_acceptance_tests": list(manifest.get("ux_acceptance_tests", [])),
        "risks": list(manifest.get("risks", [])),
        "edges": _normalize_edges(manifest.get("edges", []), child_key=child_key),
        "scope_item_ids": list(manifest.get("scope_item_ids", [])),
        "contrib_branch": str(manifest.get("contrib_branch") or f"ai-org/contrib/{_patch_series_id(branch)}-{child_key}"),
    }


def _lineage_status(root_manifest: Mapping[str, Any], ledger: Mapping[str, Any]) -> dict[str, Any]:
    children = root_manifest.get("children") if isinstance(root_manifest, Mapping) else []
    # Memento: generated projection only. precedent facet "bounded disposition
    # loop for design review" shaped the visible status loop, but authority stays
    # in node manifests and git ancestry, not this roll-up.
    return {
        "schema": "patch-queue-status-rollup-v1",
        "generated": True,
        "generated_from": NODE_MANIFEST_PATH,
        "ledger_schema": ledger.get("schema", "") if isinstance(ledger, Mapping) else "",
        "node_path": ".",
        "children": list(children) if isinstance(children, list) else [],
        "coverage": list(ledger.get("coverage", [])) if isinstance(ledger, Mapping) else [],
    }


def _child_patch_series(
    child: Mapping[str, Any],
    existing_spine_contract_paths: set[str],
    *,
    product_contract: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    references = _child_references(child, existing_spine_contract_paths)
    contract = product_contract or {}
    request = {
        "raw_request": child["summary"],
        "working_title": child["title"],
        "request_type": "feature",
        "problem_or_motivation": child["summary"],
        "intended_users_or_jobs": "Implement the approved patch series technical approach as a bounded network child.",
        "desired_outcomes_success": "; ".join(child["acceptance_criteria"]),
        "affected_area_platform": ", ".join(child["systems"]),
        "tech_stack": _contract_field(child, contract, "tech_stack", _unspecified_tech_stack()),
        "user_experience_requirements": _contract_field(
            child,
            contract,
            "user_experience_requirements",
            empty_user_experience_requirements(),
        ),
        "background_facts": "Generated from the parent network ledger.",
        "constraints_assumptions": [],
        "references": references,
        "grounding_provenance": "lineage_b clean-room split",
        "open_questions": [],
        "non_goals_out_of_scope": [],
        "proposal_hint": child["functional_check"],
        "alternatives_considered": [],
    }
    return request


def _empty_commission_request() -> dict[str, Any]:
    return {
        "schema": "ai-org-commission-request-v1",
        "spine_contract_refs": [],
        "identity_manifest_fields": [],
        "acceptance_checks": [],
        "contract_fields": [],
    }


def _product_contract_from_patch_series(patch_series: Any) -> dict[str, Any]:
    if not isinstance(patch_series, Mapping):
        return {}
    contract: dict[str, Any] = {}
    for field in ("tech_stack", "user_experience_requirements"):
        value = patch_series.get(field)
        if isinstance(value, Mapping):
            contract[field] = copy.deepcopy(dict(value))
    return contract


def _product_contract_from_request_envelope(envelope: Any) -> dict[str, Any]:
    if not isinstance(envelope, Mapping):
        return {}
    return _product_contract_from_patch_series(envelope.get("request"))


def _contract_field(
    child: Mapping[str, Any],
    product_contract: Mapping[str, Any],
    field: str,
    fallback: Mapping[str, Any],
) -> dict[str, Any]:
    child_value = child.get(field)
    if isinstance(child_value, Mapping):
        return copy.deepcopy(dict(child_value))
    parent_value = product_contract.get(field)
    if isinstance(parent_value, Mapping):
        return copy.deepcopy(dict(parent_value))
    return copy.deepcopy(dict(fallback))


def _unspecified_tech_stack() -> dict[str, str]:
    return {
        "build_strategy": "",
        "engine": "",
        "framework": "",
        "language": "",
        "platform": "",
        "rationale": "",
        "provenance": "unspecified",
    }


def _child_references(child: Mapping[str, Any], existing_spine_contract_paths: set[str]) -> list[str]:
    text = _child_content_text(child)
    return [path for path in SPINE_CONTRACT_REF_FIELDS.values() if path in text and path in existing_spine_contract_paths]


def _mentioned_spine_contract_paths(child: Mapping[str, Any]) -> list[str]:
    text = _child_content_text(child)
    return [path for path in SPINE_CONTRACT_REF_FIELDS.values() if path in text]


def _existing_spine_contract_paths(repo: Path, branch: str) -> set[str]:
    return {path for path in SPINE_CONTRACT_REF_FIELDS.values() if git_wrapper.file_exists(repo, branch, path)}


def _existing_spine_contract_ref_fields(
    existing_spine_contract_paths: set[str],
) -> list[dict[str, str]]:
    return [
        {"role": field, "path": path}
        for field, path in SPINE_CONTRACT_REF_FIELDS.items()
        if path in existing_spine_contract_paths
    ]


def _is_commission_request(child: Mapping[str, Any]) -> bool:
    text = _child_content_text(child)
    cites_shared_contract = set(SPINE_CONTRACT_REF_FIELDS.values()).issubset(set(_mentioned_spine_contract_paths(child)))
    return ("commission" in text or "contract" in text) and cites_shared_contract


def _commission_request(child: Mapping[str, Any], existing_spine_contract_paths: set[str]) -> dict[str, Any]:
    child_key = _commission_child_key(child)
    # precedent facets: "stamped assets are rows plus shared contract" and
    # "commission contract field union". The child request carries only
    # row-thin commission fields; style/canon/manifest weight stays in spine.
    return {
        "schema": "ai-org-commission-request-v1",
        "spine_contract_refs": _existing_spine_contract_ref_fields(existing_spine_contract_paths),
        "identity_manifest_fields": _commission_identity_manifest_fields(child_key),
        "acceptance_checks": _commission_acceptance_checks(child),
        "contract_fields": [
            {"child_key": child_key, **row}
            for row in _commission_contract_rows(child)
        ],
    }


def _commission_acceptance_checks(child: Mapping[str, Any]) -> list[str]:
    checks = [str(item) for item in child.get("acceptance_criteria", []) if str(item).strip()]
    functional = str(child.get("functional_check") or "").strip()
    if functional:
        checks.append(functional)
    return checks or ["commission acceptance is reviewable against the shared spine contract"]


def _commission_contract_rows(child: Mapping[str, Any]) -> list[dict[str, str]]:
    shared = {
        "subject": str(child.get("title") or child.get("child_key") or "commission child"),
        "acceptance_criteria": "; ".join(str(item) for item in child.get("acceptance_criteria", []) if str(item).strip()),
        "usage_license": "project-owned contribution under the parent patch series license policy",
        "revision_limit": "one parent-requested correction round unless escalated",
    }
    return [{"field": key, "value": value} for key, value in shared.items()]


def _commission_identity_manifest_fields(child_key: str) -> list[dict[str, Any]]:
    return [
        {"child_key": child_key, "field": field}
        for field in ("child_key", "license_provenance", "acceptance_checks")
    ]


def _commission_child_key(child: Mapping[str, Any]) -> str:
    child_key = str(child.get("child_key") or "").strip()
    return child_key or "commission_child"


def _child_content_text(child: Mapping[str, Any]) -> str:
    parts: list[str] = []
    for field in ("child_key", "title", "summary", "functional_check"):
        value = child.get(field)
        if isinstance(value, str):
            parts.append(value)
    for field in ("acceptance_criteria", "systems", "ux_acceptance_tests", "risks"):
        value = child.get(field)
        if isinstance(value, list):
            parts.extend(str(item) for item in value)
    return "\n".join(parts).lower()


def _child_approach(child: Mapping[str, Any]) -> dict[str, Any]:
    lineage = {
            "id": child["id"],
            "lifecycle_status": child.get("lifecycle_status", "ready_for_patch_authoring"),
            "summary": child["summary"],
            "systems": child["systems"],
            "acceptance_criteria": child["acceptance_criteria"],
            "functional_check": child["functional_check"],
            "ux_acceptance_tests": child["ux_acceptance_tests"],
            "risks": child["risks"],
        }
    patch_plan = child.get("patch_plan")
    if isinstance(patch_plan, Mapping):
        # This is the CUE projection produced by the partition. It is copied,
        # never re-authored, and therefore contains only this leaf's component.
        lineage["patch_plan"] = copy.deepcopy(dict(patch_plan))
    return {
        "lineage_child": lineage
    }


def _unwrap_approach(view: Mapping[str, Any]) -> Mapping[str, Any]:
    if isinstance(view.get("technical_approach"), Mapping):
        return view["technical_approach"]
    return view


def _scope_items(patch_series: Mapping[str, Any], approach: Mapping[str, Any]) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    _append_text_item(items, "goal:patch_series.desired_outcomes_success", "desired_outcome", patch_series.get("desired_outcomes_success"))
    for index, text in enumerate(_find_named_strings(approach, {"goal", "goals", "success_criteria", "success_criterion"}), start=1):
        _append_text_item(items, f"goal:technical_approach:{index}", "referee_goal", text)
    for path, text in _acceptance_tests(patch_series, approach):
        _append_text_item(items, f"ux:{path}", "ux_acceptance_test", text)
    patch_plan = _find_first_mapping(approach, "patch_plan")
    if patch_plan:
        graph_items = _list_of_mappings(patch_plan.get("items"))
        if graph_items:
            for item in graph_items:
                item_id = str(item.get("id") or "").strip()
                if item_id:
                    _append_text_item(items, item_id, "patch_plan", item)
        else:
            # Historical roots are read-only compatibility inputs. Their
            # positional plan is one dependency chain, never parallel work.
            _append_text_item(items, "patch_plan:first_proof_moment", "patch_plan", patch_plan.get("first_proof_moment"))
            for index, item in enumerate(_list_of_mappings(patch_plan.get("follow_ups")), start=1):
                _append_text_item(items, f"patch_plan:follow_up:{index}", "patch_plan", item)
        for index, item in enumerate(_list_of_mappings(patch_plan.get("deferred")), start=1):
            _append_text_item(items, f"patch_plan:deferred:{index}", "patch_plan", item)
    for index, risk in enumerate(_risk_items(approach), start=1):
        risk_id = risk.get("id") if isinstance(risk.get("id"), str) and risk["id"] else str(index)
        _append_text_item(items, f"risk:{risk_id}", "must_address_risk", risk)
    for aspect in _domain_scope_aspects(approach):
        aspect_id = str(aspect.get("id") or "").strip()
        if aspect_id:
            _append_text_item(items, aspect_id, "domain_specification", aspect)
    deduped: dict[str, dict[str, str]] = {}
    for item in items:
        deduped.setdefault(item["id"], item)
    return list(deduped.values())


def _append_text_item(items: list[dict[str, str]], item_id: str, kind: str, value: Any) -> None:
    text = _compact_text(value)
    if text:
        items.append({"id": item_id, "kind": kind, "text": text})


def _acceptance_tests(patch_series: Mapping[str, Any], approach: Mapping[str, Any]) -> list[tuple[str, str]]:
    values: list[tuple[str, str]] = []
    for source_name, source in (("patch_series", patch_series), ("technical_approach", approach)):
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


def _domain_scope_aspects(value: Any) -> list[Mapping[str, Any]]:
    domain = _find_first_mapping(value, "domain_specification")
    if not domain:
        return []
    aspects = domain.get("aspects")
    if not isinstance(aspects, list):
        return []
    return [
        aspect
        for aspect in aspects
        if isinstance(aspect, Mapping)
        and aspect.get("applicability") == "applies"
        and isinstance(aspect.get("id"), str)
    ]


def _approach_split_view(approach: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "patch_plan": _find_first_mapping(approach, "patch_plan") or {},
        "implementation_systems": _find_named_strings(approach, {"systems", "subsystem", "key_modules", "implementation"}),
        "domain_specification": _domain_split_summary(approach),
        "user_experience_requirements": _find_mappings_named(approach, "user_experience_requirements"),
        "risks": _risk_items(approach),
    }


def _domain_split_summary(approach: Mapping[str, Any]) -> list[dict[str, Any]]:
    summary: list[dict[str, Any]] = []
    for aspect in _domain_scope_aspects(approach):
        tables = aspect.get("tables")
        externalized = aspect.get("externalized_tables")
        table_items = tables if isinstance(tables, list) else []
        summary.append(
            {
                "id": aspect.get("id", ""),
                "aspect_name": aspect.get("aspect_name", ""),
                "specification_body": aspect.get("specification_body", ""),
                "quantities": aspect.get("quantities", []),
                "tables": [
                    {
                        "table_name": table.get("table_name", ""),
                        "columns": table.get("columns", []),
                        "row_count": len(table.get("rows", [])) if isinstance(table, Mapping) else 0,
                    }
                    for table in table_items
                    if isinstance(table, Mapping)
                ],
                "externalized_tables": list(externalized) if isinstance(externalized, list) else [],
            }
        )
    return summary


def _find_first_mapping(value: Any, target_key: str) -> Mapping[str, Any] | None:
    stack: list[Any] = [value]
    while stack:
        current = stack.pop()
        if isinstance(current, Mapping):
            found = current.get(target_key)
            if isinstance(found, Mapping):
                return found
            stack.extend(reversed(list(current.values())))
        elif isinstance(current, list):
            stack.extend(reversed(current))
    return None


def _find_mappings_named(value: Any, target_key: str) -> list[Mapping[str, Any]]:
    found: list[Mapping[str, Any]] = []
    stack: list[Any] = [value]
    while stack:
        current = stack.pop()
        if isinstance(current, Mapping):
            child = current.get(target_key)
            if isinstance(child, Mapping):
                found.append(child)
            stack.extend(reversed(list(current.values())))
        elif isinstance(current, list):
            stack.extend(reversed(current))
    return found


def _find_named_strings(value: Any, target_keys: set[str]) -> list[str]:
    strings: list[str] = []
    stack: list[tuple[str, Any]] = [("visit", value)]
    while stack:
        action, current = stack.pop()
        if action == "emit":
            text = _compact_text(current)
            if text:
                strings.append(text)
            continue
        if isinstance(current, Mapping):
            for key, child in reversed(list(current.items())):
                stack.append(("emit" if key in target_keys else "visit", child))
        elif isinstance(current, list):
            stack.extend(("visit", child) for child in reversed(current))
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
    local = _patch_series_id(branch)
    if _looks_like_serial_chain(local):
        return local
    # Formation is a prepared-tree transition. Serial selection therefore
    # remains read-only here; assigning a tag before network preflight would
    # leak a ref mutation when body validation or the publication CAS fails.
    return git_wrapper.serial_for_ref(repo, branch) or git_wrapper.next_serial(repo)


def _safe_child_key(value: str) -> str:
    return value.strip() or "child"


def _valid_child_key(value: str) -> bool:
    return _VALID_CHILD_KEY_RE.fullmatch(value) is not None


def _manifest_key(manifest: Mapping[str, Any], node_path: str) -> str:
    child_key = manifest.get("child_key")
    if isinstance(child_key, str) and child_key:
        return child_key
    if node_path in {"", "."}:
        return "root"
    return Path(node_path).name or "root"


def _node_address(branch: str, node_path: str) -> str:
    return f"{branch}:{node_path}"


def _parse_node_address(value: str) -> tuple[str, str]:
    if ":" in value and not value.startswith("refs/heads/"):
        branch, node_path = value.split(":", 1)
        return _patch_series_branch(branch), (node_path or ".")
    return _patch_series_branch(value), "."


def _node_file(node_path: str, rel_path: str) -> str:
    return rel_path if node_path in {"", "."} else f"{node_path.rstrip('/')}/{rel_path}"


def _read_node_manifest(
    repo: Path,
    branch: str,
    node_path: str,
    *,
    errors: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    data = _read_json(repo, branch, _node_file(node_path, NODE_MANIFEST_PATH), errors=errors, node=_patchwork_check_event_node_for_path(_node_file(node_path, NODE_MANIFEST_PATH)))
    return dict(data) if isinstance(data, Mapping) else {}


def _write_node_manifest(
    repo: Path,
    branch: str,
    manifest: Mapping[str, Any],
    *,
    subject: str,
    source: FrozenNetworkSourceVector,
    transition_route: str | None = None,
) -> dict[str, str]:
    node_path = str(manifest.get("node_path") or ".")
    changes = {_node_file(node_path, NODE_MANIFEST_PATH): dict(manifest)}
    frozen = source.frozen_root_oid
    if _network_file_exists(repo, frozen, LEDGER_PATH):
        return _commit_network_files(
            repo,
            branch,
            changes,
            subject=subject,
            source=source,
            transition_route=transition_route,
        )
    return git_wrapper.commit_files(repo, branch, changes, subject=subject)


def _looks_like_serial_chain(value: str) -> bool:
    parts = value.split("-")
    return bool(parts) and parts[0].isdigit() and len(parts[0]) == 4 and all(part.isdigit() for part in parts[1:])


def _criteria_for_scope(scope_items: list[Mapping[str, str]], ids: list[str]) -> list[str]:
    by_id = {item["id"]: item["text"] for item in scope_items}
    return [by_id[item] for item in ids if item in by_id]


class PatchSeriesGateError(ValueError):
    """Typed deterministic network gate error."""


class WhenSyntaxError(PatchSeriesGateError):
    """Raised when a conditional edge predicate is malformed."""


_TOKEN_RE = re.compile(
    r"\s*(?:(?P<int>\d+)|(?P<string>\"[^\"\\]*(?:\\.[^\"\\]*)*\"|'[^'\\]*(?:\\.[^'\\]*)*')|"
    r"(?P<op>==|!=|<=|>=|<|>|\(|\))|(?P<ident>[A-Za-z_][A-Za-z0-9_.:-]*)|(?P<bad>.))"
)
_PATH_REF_RE = re.compile(r"\b(?:spine|sub|domain-spec|docs|assets?)/[A-Za-z0-9._/\-]+\b")
_VALID_CHILD_KEY_RE = re.compile(r"^[a-z0-9_]+$")
_PATCHWORK_CHECK_NAME_RE = re.compile(r"^[a-z0-9_]+$")


def evaluate_when(
    expression: str | None,
    patchwork_check_values: Mapping[str, Any],
    *,
    warnings: list[dict[str, Any]] | None = None,
    node: str = "",
    edge_index: int | None = None,
) -> bool:
    """Evaluate the total network `when` mini-language."""
    text = (expression or "").strip()
    if not text:
        return True
    parser = _WhenParser(text)
    ast = parser.parse()
    return bool(_eval_when_ast(ast, patchwork_check_values, warnings=warnings, node=node, edge_index=edge_index, predicate=text))


def project_patchwork_check_events(events: list[Mapping[str, Any]], registry: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Project append-only state event records into current state variables."""
    state: dict[str, Any] = _state_defaults(registry or {})
    for index, event in enumerate(events):
        _apply_patchwork_check_event(state, event, index, registry=registry)
    return state


def computed_patchwork_check_facts(repo: str | Path, branch: str, node_key: str) -> dict[str, Any]:
    """Return the deterministic current patchwork-check facts for one node."""
    repo_path = Path(repo).resolve()
    normalized = _patch_series_branch(branch)
    source_oid = git_wrapper.head_sha(repo_path, normalized)
    if source_oid is None:
        return _gate_errors_present([
            {"type": "root_manifest_missing", "path": NODE_MANIFEST_PATH}
        ])
    gate_report = validate_lineage_gate_from_git(
        repo_path, normalized, _frozen_oid=source_oid
    )
    if gate_report.get("errors"):
        return _gate_errors_present(gate_report["errors"])
    graph = _load_manifest_network_from_git(
        repo_path, source_oid, logical_branch=normalized
    )
    registry = graph.get("declared_patchwork_checks", {}) if isinstance(graph.get("declared_patchwork_checks"), Mapping) else {}
    registry_present = bool(graph.get("declared_patchwork_checks_present"))
    projection = _project_tree_patchwork_check_events_from_git(
        repo_path,
        source_oid,
        registry=registry,
        registry_present=registry_present,
    )
    try:
        return _computed_patchwork_check_facts(graph, projection, node_key)
    except PatchSeriesGateError as exc:
        return _gate_errors_present([{"type": "computed_patchwork_check_facts_invalid", "node": node_key, "message": str(exc)}])


def computed_patchwork_check_facts_from_path(root: str | Path, node_key: str) -> dict[str, Any]:
    """Return deterministic patchwork-check facts for a filesystem network tree."""
    root_path = Path(root)
    gate_report = validate_lineage_gate(root_path)
    if gate_report.get("errors"):
        return _gate_errors_present(gate_report["errors"])
    graph = _load_manifest_network_from_path(root_path)
    registry = graph.get("declared_patchwork_checks", {}) if isinstance(graph.get("declared_patchwork_checks"), Mapping) else {}
    registry_present = bool(graph.get("declared_patchwork_checks_present"))
    projection = _project_tree_patchwork_check_events(
        root_path,
        registry=registry,
        registry_present=registry_present,
    )
    try:
        return _computed_patchwork_check_facts(graph, projection, node_key)
    except PatchSeriesGateError as exc:
        return _gate_errors_present([{"type": "computed_patchwork_check_facts_invalid", "node": node_key, "message": str(exc)}])


def _gate_errors_present(errors: list[dict[str, Any]]) -> dict[str, Any]:
    return {"ok": False, "type": "gate_errors_present", "errors": errors}


def _computed_patchwork_check_facts(
    graph: Mapping[str, Any],
    projection: Mapping[str, Any],
    node_key: str,
) -> dict[str, Any]:
    nodes = graph.get("nodes", {}) if isinstance(graph.get("nodes"), Mapping) else {}
    registry = graph.get("declared_patchwork_checks", {}) if isinstance(graph.get("declared_patchwork_checks"), Mapping) else {}
    resolved_key = _resolve_node_key(node_key, nodes)
    manifest = nodes.get(resolved_key)
    if not isinstance(manifest, Mapping):
        raise PatchSeriesGateError(f"node {node_key} is not present in the patchwork network")

    state = _state_defaults(registry)
    projected_state = projection.get("state", {})
    if isinstance(projected_state, Mapping):
        state.update(projected_state)
    last_events = projection.get("last_events", {})
    if not isinstance(last_events, Mapping):
        last_events = {}

    blocking_edges, satisfied_edges = _computed_edge_facts(resolved_key, manifest, nodes, state)
    return {
        "checks": {
            name: {
                "declared_type": registry[name],
                "current_value": state.get(name, PATCHWORK_CHECK_DEFAULTS[registry[name]]),
                "last_event": last_events.get(name),
            }
            for name in sorted(registry)
        },
        "lifecycle_status": _canonical_lifecycle(manifest),
        "blocking_edges": blocking_edges,
        "satisfied_edges": satisfied_edges,
    }


def _resolve_node_key(node_key: str, nodes: Mapping[str, Any]) -> str:
    wanted = str(node_key or "root")
    if wanted in nodes:
        return wanted
    if wanted in {"", "."} and "root" in nodes:
        return "root"
    for key, manifest in nodes.items():
        if not isinstance(manifest, Mapping):
            continue
        if wanted == str(manifest.get("node_path") or ""):
            return str(key)
        if wanted == str(manifest.get("serial_id") or ""):
            return str(key)
    return wanted


def _computed_edge_facts(
    node_key: str,
    manifest: Mapping[str, Any],
    nodes: Mapping[str, Any],
    state: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    blocking: list[dict[str, Any]] = []
    satisfied: list[dict[str, Any]] = []
    depends_edges = [
        edge
        for edge in _normalize_edges(manifest.get("edges", []), child_key=str(manifest.get("child_key") or node_key))
        if edge.get("type") == "depends"
    ]
    groups: dict[str, list[dict[str, Any]]] = {}
    and_edges: list[dict[str, Any]] = []
    for index, edge in enumerate(depends_edges):
        fact, active = _computed_edge_fact(edge, state, node_key=node_key, edge_index=index)
        entry = {"edge": edge, "fact": fact, "active": active, "target_satisfied": _edge_target_satisfied(edge, nodes, state)}
        group = edge.get("or_group")
        if isinstance(group, str) and group:
            groups.setdefault(group, []).append(entry)
        else:
            and_edges.append(entry)

    for entry in and_edges:
        if not entry["active"] or entry["target_satisfied"]:
            satisfied.append(entry["fact"])
        else:
            blocking.append(entry["fact"])
    for members in groups.values():
        group_satisfied = any(member["active"] and member["target_satisfied"] for member in members)
        target = satisfied if group_satisfied else blocking
        for member in members:
            if not member["active"]:
                satisfied.append(member["fact"])
            else:
                target.append(member["fact"])
    return blocking, satisfied


def _computed_edge_fact(
    edge: Mapping[str, Any],
    state: Mapping[str, Any],
    *,
    node_key: str,
    edge_index: int,
) -> tuple[dict[str, Any], bool]:
    when = edge.get("when")
    if isinstance(when, str) and when:
        try:
            when_currently: bool | str = evaluate_when(when, state, node=node_key, edge_index=edge_index)
        except WhenSyntaxError:
            when_currently = False
        active = bool(when_currently)
    else:
        when_currently = "not-applicable"
        active = True
    return (
        {
            "to": str(edge.get("to") or ""),
            "state": str(edge.get("state") or "merged_into_subsystem_tree"),
            "reason": str(edge.get("reason") or ""),
            "when": str(when or ""),
            "when_currently": when_currently,
        },
        active,
    )


def eligible(
    node_key: str,
    patchwork_check_values: Mapping[str, Any],
    graph: Mapping[str, Any],
    *,
    warnings: list[dict[str, Any]] | None = None,
) -> bool:
    """Return whether a node's active depends edges are satisfied right now."""
    nodes = graph.get("nodes", {}) if isinstance(graph, Mapping) else {}
    if not isinstance(nodes, Mapping) or node_key not in nodes:
        return False
    manifest = nodes[node_key]
    if not isinstance(manifest, Mapping):
        return False
    registry = graph.get("declared_patchwork_checks", {}) if isinstance(graph, Mapping) and isinstance(graph.get("declared_patchwork_checks"), Mapping) else {}
    state = _state_defaults(registry)
    state.update(dict(patchwork_check_values))
    depends = [
        edge for edge in _active_edges(manifest, state, warnings=warnings, node=node_key)
        if edge.get("type") == "depends"
    ]
    and_edges = [edge for edge in depends if not edge.get("or_group")]
    groups: dict[str, list[Mapping[str, Any]]] = {}
    for edge in depends:
        group = edge.get("or_group")
        if isinstance(group, str) and group:
            groups.setdefault(group, []).append(edge)
    return all(_edge_target_satisfied(edge, nodes, state) for edge in and_edges) and all(
        members and any(_edge_target_satisfied(edge, nodes, state) for edge in members)
        for members in groups.values()
    )


def readiness_rollup(graph: Mapping[str, Any], patchwork_check_values: Mapping[str, Any] | None = None) -> dict[str, bool]:
    """Project current per-node eligibility from the declared edge network."""
    nodes = graph.get("nodes", {}) if isinstance(graph, Mapping) else {}
    if not isinstance(nodes, Mapping):
        return {}
    registry = graph.get("declared_patchwork_checks", {}) if isinstance(graph, Mapping) and isinstance(graph.get("declared_patchwork_checks"), Mapping) else {}
    state = _state_defaults(registry)
    state.update(dict(patchwork_check_values or {}))
    return {str(key): eligible(str(key), state, graph) for key in sorted(nodes)}


def topological_order(graph: Mapping[str, Any], patchwork_check_values: Mapping[str, Any] | None = None) -> list[str]:
    """Project deterministic active dependency order from the edge network."""
    nodes = graph.get("nodes", {}) if isinstance(graph, Mapping) else {}
    if not isinstance(nodes, Mapping):
        return []
    registry = graph.get("declared_patchwork_checks", {}) if isinstance(graph, Mapping) and isinstance(graph.get("declared_patchwork_checks"), Mapping) else {}
    state = _state_defaults(registry)
    state.update(dict(patchwork_check_values or {}))
    active = _active_dependency_pairs(nodes, state)
    incoming = {str(key): 0 for key in nodes}
    outgoing: dict[str, list[str]] = {str(key): [] for key in nodes}
    for prerequisite, dependent in active:
        if prerequisite in incoming and dependent in incoming:
            incoming[dependent] += 1
            outgoing[prerequisite].append(dependent)
    ready = sorted(key for key, count in incoming.items() if count == 0)
    ordered: list[str] = []
    while ready:
        key = ready.pop(0)
        ordered.append(key)
        for dependent in sorted(outgoing[key]):
            incoming[dependent] -= 1
            if incoming[dependent] == 0:
                ready.append(dependent)
                ready.sort()
    return ordered


def critical_path(graph: Mapping[str, Any], patchwork_check_values: Mapping[str, Any] | None = None) -> list[str]:
    """Project the longest active dependency path with unit node weights."""
    nodes = graph.get("nodes", {}) if isinstance(graph, Mapping) else {}
    if not isinstance(nodes, Mapping):
        return []
    order = topological_order(graph, patchwork_check_values)
    if len(order) != len(nodes):
        return []
    registry = graph.get("declared_patchwork_checks", {}) if isinstance(graph, Mapping) and isinstance(graph.get("declared_patchwork_checks"), Mapping) else {}
    state = _state_defaults(registry)
    state.update(dict(patchwork_check_values or {}))
    active = _active_dependency_pairs(nodes, state)
    predecessors: dict[str, list[str]] = {str(key): [] for key in nodes}
    for prerequisite, dependent in active:
        if prerequisite in predecessors and dependent in predecessors:
            predecessors[dependent].append(prerequisite)
    best_len: dict[str, int] = {}
    previous: dict[str, str] = {}
    for key in order:
        options = [(best_len.get(pred, 0) + 1, pred) for pred in predecessors[key]]
        if options:
            length, pred = max(options, key=lambda item: (item[0], item[1]))
            best_len[key] = length
            previous[key] = pred
        else:
            best_len[key] = 1
    if not best_len:
        return []
    end = max(best_len, key=lambda key: (best_len[key], key))
    path = [end]
    while end in previous:
        end = previous[end]
        path.append(end)
    return list(reversed(path))


def validate_lineage_gate(root: str | Path, patchwork_check_values: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Run the deterministic network edge gate over a filesystem tree."""
    root_path = Path(root)
    graph = _load_manifest_network_from_path(root_path)
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    errors.extend(graph.get("errors", []))
    registry = graph.get("declared_patchwork_checks", {}) if isinstance(graph.get("declared_patchwork_checks"), Mapping) else {}
    registry_present = bool(graph.get("declared_patchwork_checks_present"))
    projected = _state_defaults(registry)
    projected.update(dict(patchwork_check_values or {}))
    state_projection = _project_tree_patchwork_check_events(root_path, registry=registry, registry_present=registry_present)
    projected.update(state_projection["state"])
    errors.extend(state_projection["errors"])
    nodes = graph["nodes"]
    if not nodes:
        errors.append({"type": "network_empty", "message": "network must contain at least one manifest node"})
    key_set = set(nodes)
    serial_set = {
        str(manifest.get("serial_id"))
        for manifest in nodes.values()
        if isinstance(manifest, Mapping) and str(manifest.get("serial_id") or "")
    }
    mention_index = _node_key_mention_index(key_set)
    for key, manifest in nodes.items():
        manifest_errors, manifest_warnings = _validate_manifest_edges(
            key,
            manifest,
            nodes,
            key_set,
            serial_set,
            registry=registry,
            registry_present=registry_present,
        )
        for error in manifest_errors:
            errors.append(error)
        warnings.extend(manifest_warnings)
        for error in _citation_errors(root_path, key, manifest):
            errors.append(error)
        warnings.extend(_drift_warnings(root_path, key, manifest, key_set, mention_index=mention_index))
    cycle = _instant_cycle(nodes, projected, warnings=warnings)
    if cycle:
        errors.append({"type": "instant_cycle", "cycle": cycle})
    errors.extend(_exclusion_consistency_errors(nodes, projected))
    report = {
        "ok": not errors,
        "summary": {
            "node_count": len(nodes),
            "error_count": len(errors),
            "warning_count": len(warnings),
            "instantaneous_cycle_count": 1 if cycle else 0,
        },
        "nodes": sorted(nodes),
        "errors": errors,
        "warnings": warnings,
        "well_founded": not cycle,
    }
    return report


def validate_lineage_gate_from_git(
    repo: str | Path,
    branch: str,
    patchwork_check_values: Mapping[str, Any] | None = None,
    *,
    _frozen_oid: str | None = None,
) -> dict[str, Any]:
    """Run the deterministic network edge gate over a branch tree."""
    repo_path = Path(repo).resolve()
    normalized = _patch_series_branch(branch)
    source_ref = _frozen_oid or git_wrapper.head_sha(repo_path, normalized) or normalized
    graph = _load_manifest_network_from_git(
        repo_path, source_ref, logical_branch=normalized
    )
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = list(graph.get("warnings", []))
    errors.extend(graph.get("errors", []))
    registry = graph.get("declared_patchwork_checks", {}) if isinstance(graph.get("declared_patchwork_checks"), Mapping) else {}
    registry_present = bool(graph.get("declared_patchwork_checks_present"))
    projected = _state_defaults(registry)
    projected.update(dict(patchwork_check_values or {}))
    state_projection = _project_tree_patchwork_check_events_from_git(
        repo_path,
        source_ref,
        registry=registry,
        registry_present=registry_present,
    )
    projected.update(state_projection["state"])
    errors.extend(state_projection["errors"])
    nodes = graph["nodes"]
    if not nodes:
        errors.append({"type": "network_empty", "message": "network must contain at least one manifest node"})
    key_set = set(nodes)
    serial_set = {
        str(manifest.get("serial_id"))
        for manifest in nodes.values()
        if isinstance(manifest, Mapping) and str(manifest.get("serial_id") or "")
    }
    tree_file_set = set(git_wrapper.tree_files(repo_path, source_ref))
    mention_index = _node_key_mention_index(key_set)
    for key, manifest in nodes.items():
        manifest_errors, manifest_warnings = _validate_manifest_edges(
            key,
            manifest,
            nodes,
            key_set,
            serial_set,
            registry=registry,
            registry_present=registry_present,
        )
        errors.extend(manifest_errors)
        warnings.extend(manifest_warnings)
        errors.extend(
            _citation_errors_from_git(repo_path, source_ref, key, manifest, tree_file_set)
        )
        warnings.extend(
            _drift_warnings_from_git(
                repo_path,
                source_ref,
                key,
                manifest,
                key_set,
                mention_index=mention_index,
            )
        )
    cycle = _instant_cycle(nodes, projected, warnings=warnings)
    if cycle:
        errors.append({"type": "instant_cycle", "cycle": cycle})
    errors.extend(_exclusion_consistency_errors(nodes, projected))
    return {
        "ok": not errors,
        "summary": {
            "node_count": len(nodes),
            "error_count": len(errors),
            "warning_count": len(warnings),
            "instantaneous_cycle_count": 1 if cycle else 0,
        },
        "nodes": sorted(nodes),
        "errors": errors,
        "warnings": warnings,
        "well_founded": not cycle,
    }


def migrate_legacy_tree_to_edges(source: str | Path, destination: str | Path | None = None) -> dict[str, Any]:
    """Copy a legacy network tree and rewrite manifests to the declared edges form."""
    source_path = Path(source)
    dest_path = Path(destination) if destination is not None else source_path
    source_manifests = _migration_manifest_sources(source_path)
    if not source_manifests:
        return {
            "ok": False,
            "status": "source_not_a_legacy_tree",
            "destination": str(dest_path),
            "nodes_migrated": 0,
            "files_translated": _empty_migration_file_counts(),
            "errors": [{"type": "source_not_a_legacy_tree", "source": str(source_path)}],
        }
    if destination is not None:
        source_resolved = source_path.resolve()
        dest_resolved = dest_path.resolve()
        if source_resolved == dest_resolved:
            raise PatchSeriesGateError(f"migration destination must differ from source: {dest_path}")
        if dest_path.exists():
            if any(dest_path.iterdir()):
                raise PatchSeriesGateError(f"migration destination already exists and is not empty: {dest_path}")
        _copy_legacy_tree_with_current_names(source_path, dest_path)
        _make_tree_writable(dest_path)
    else:
        _rewrite_legacy_artifact_names_in_place(source_path)
    migrated: list[str] = []
    file_counts = _migration_file_counts(dest_path)
    for path in sorted(dest_path.rglob(NODE_MANIFEST_PATH)):
        manifest = _read_json_file(path)
        if manifest is None:
            continue
        if not isinstance(manifest, dict):
            continue
        converted = migrate_legacy_manifest_edges(manifest)
        path.write_text(json.dumps(converted, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        migrated.append(str(path.relative_to(dest_path)))
    report = validate_lineage_gate(dest_path)
    write_gate_reports(dest_path, report)
    mermaid = generate_mermaid_projection(dest_path)
    (dest_path / "series-dependency-diagram.md").write_text(mermaid, encoding="utf-8")
    return {
        "ok": report["ok"],
        "destination": str(dest_path),
        "migrated_manifests": migrated,
        "nodes_migrated": len(migrated),
        "files_translated": file_counts,
        "gate_report": report,
    }


def migrate_legacy_manifest_edges(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Convert old dependency fields into explicit edge declarations without reconciling drift."""
    converted = dict(manifest)
    child_key = str(converted.get("child_key") or Path(str(converted.get("node_path") or "")).name)
    edges = _normalize_edges(converted.get("edges", []), child_key=child_key)
    seen_dep_targets = {str(edge.get("to")) for edge in edges if edge.get("type") == "depends" and edge.get("to")}
    legacy_declared_targets: set[str] = set()
    legacy_keys = converted.get("depends_on_child_keys", [])
    if isinstance(legacy_keys, list):
        for dep in legacy_keys:
            if isinstance(dep, str) and dep:
                legacy_declared_targets.add(dep)
                edge = _depends_edge(dep, reason="migrated: declared")
                if str(edge["to"]) not in seen_dep_targets:
                    edges.append(edge)
                    seen_dep_targets.add(str(edge["to"]))
    serial_after = converted.get("serial_after_child_key")
    if isinstance(serial_after, str) and serial_after:
        legacy_declared_targets.add(serial_after)
        edge = _depends_edge(serial_after, reason="migrated: serial-after")
        if str(edge["to"]) not in seen_dep_targets:
            edges.append(edge)
            seen_dep_targets.add(str(edge["to"]))
    path_targets = converted.get("depends_on_node_paths", [])
    if isinstance(path_targets, list):
        for item in path_targets:
            if not isinstance(item, str) or not item:
                continue
            dep = Path(item).name
            if dep not in seen_dep_targets:
                edges.append(_depends_edge(dep, reason="migrated: code-derived"))
                seen_dep_targets.add(dep)
    parent = converted.get("part_of") or converted.get("parent_node_path") or "root"
    if parent in {"", "."}:
        parent = "root"
    if not any(edge.get("type") == "part_of" for edge in edges):
        edges.append({"type": "part_of", "to": str(parent or "root"), "with": None, "state": None, "or_group": None, "when": None, "reason": ""})
    converted["edges"] = edges
    for key in ("depends_on_child_keys", "depends_on_node_paths", "serial_after_child_key", "branching_mode", "node_kind"):
        converted.pop(key, None)
    converted["lifecycle_status"] = _canonical_lifecycle(converted)
    return converted


def _migration_manifest_sources(source: Path) -> list[Path]:
    return [
        path
        for path in _migration_artifact_sources(source, NODE_MANIFEST_PATH)
        if path.is_file()
    ]


def _migration_artifact_sources(source: Path, current_name: str) -> list[Path]:
    if not source.is_dir():
        return []
    sources: list[Path] = []
    for directory in _migration_directories(source):
        chosen = _migration_artifact_source(directory, current_name)
        if chosen is not None:
            sources.append(chosen)
    return sources


def _migration_directories(source: Path) -> list[Path]:
    if not source.is_dir():
        return []
    subdir = source / SUBDIR
    node_dirs = [path for path in sorted(subdir.iterdir()) if path.is_dir()] if subdir.is_dir() else []
    return [source, *node_dirs]


def _migration_artifact_source(directory: Path, current_name: str) -> Path | None:
    for name in MIGRATION_ARTIFACT_LADDERS[current_name]:
        path = directory / name
        if path.is_file():
            return path
    return None


def _copy_legacy_tree_with_current_names(source: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    migration_sources = {
        path.resolve()
        for directory in _migration_directories(source)
        for current_name in MIGRATION_ARTIFACT_LADDERS
        for path in [_migration_artifact_source(directory, current_name)]
        if path is not None
    }
    for path in sorted(source.rglob("*")):
        if not path.is_file() or path.resolve() in migration_sources:
            continue
        target = destination / path.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    for directory in _migration_directories(source):
        rel_dir = directory.relative_to(source)
        for current_name in MIGRATION_ARTIFACT_LADDERS:
            chosen = _migration_artifact_source(directory, current_name)
            if chosen is None:
                continue
            target = destination / rel_dir / current_name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(chosen, target)


def _rewrite_legacy_artifact_names_in_place(root: Path) -> None:
    for directory in _migration_directories(root):
        for current_name in MIGRATION_ARTIFACT_LADDERS:
            chosen = _migration_artifact_source(directory, current_name)
            if chosen is None:
                continue
            target = directory / current_name
            if chosen != target:
                shutil.copy2(chosen, target)
            for alias in MIGRATION_ARTIFACT_LADDERS[current_name]:
                alias_path = directory / alias
                if alias_path.is_file() and alias_path != target:
                    alias_path.unlink()


def _empty_migration_file_counts() -> dict[str, int]:
    return {kind: 0 for kind in MIGRATION_ARTIFACT_KINDS.values()}


def _migration_file_counts(root: Path) -> dict[str, int]:
    return {
        kind: sum(1 for _ in root.rglob(current_name))
        for current_name, kind in MIGRATION_ARTIFACT_KINDS.items()
    }


def _make_tree_writable(root: Path) -> None:
    for path in [root, *root.rglob("*")]:
        try:
            path.chmod(path.stat().st_mode | 0o200)
        except OSError:
            continue


def write_gate_reports(root: str | Path, report: Mapping[str, Any]) -> None:
    root_path = Path(root)
    (root_path / "gate-report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    lines = [
        "# Network Edge Gate Report",
        "",
        f"- ok: {bool(report.get('ok'))}",
        f"- nodes: {report.get('summary', {}).get('node_count', 0)}",
        f"- errors: {report.get('summary', {}).get('error_count', 0)}",
        f"- warnings: {report.get('summary', {}).get('warning_count', 0)}",
        f"- well_founded: {bool(report.get('well_founded'))}",
        "",
        "## Errors",
    ]
    errors = report.get("errors", [])
    if isinstance(errors, list) and errors:
        lines.extend(f"- `{item.get('type', 'error')}`: {json.dumps(item, sort_keys=True, ensure_ascii=True)}" for item in errors if isinstance(item, Mapping))
    else:
        lines.append("- none")
    lines.append("")
    lines.append("## Warnings")
    warnings = report.get("warnings", [])
    if isinstance(warnings, list) and warnings:
        lines.extend(f"- `{item.get('type', 'warning')}`: {json.dumps(item, sort_keys=True, ensure_ascii=True)}" for item in warnings if isinstance(item, Mapping))
    else:
        lines.append("- none")
    (root_path / "gate-report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def generate_mermaid_projection(root: str | Path, patchwork_check_values: Mapping[str, Any] | None = None) -> str:
    graph = _load_manifest_network_from_path(Path(root))
    nodes = graph["nodes"]
    lines = ["# Network Network", "", "```mermaid", "flowchart TD"]
    for key in sorted(nodes):
        state = _canonical_lifecycle(nodes[key])
        lines.append(f'  { _mermaid_id(key) }["{key}<br/>{state}"]')
    for key in sorted(nodes):
        for edge in _normalize_edges(nodes[key].get("edges", []), child_key=key):
            if edge["type"] == "depends":
                label = f"depends:{edge.get('state') or 'accepted'}"
                if edge.get("or_group"):
                    label += f" OR {edge['or_group']}"
                lines.append(f"  {_mermaid_id(str(edge['to']))} -->|{label}| {_mermaid_id(key)}")
            elif edge["type"] == "excludes":
                lines.append(f"  {_mermaid_id(key)} -. excludes .- {_mermaid_id(str(edge['with']))}")
            elif edge["type"] == "part_of":
                lines.append(f"  {_mermaid_id(key)} -->|part_of| {_mermaid_id(str(edge['to']))}")
    lines.extend(["```", ""])
    return "\n".join(lines)


def _edge_form_error(edge: object) -> str:
    if not isinstance(edge, Mapping):
        return "edge entries must be objects"
    if edge.get("type") not in {"depends", "excludes", "part_of"}:
        return "edge type is invalid"
    if edge.get("type") in {"depends", "part_of"} and not isinstance(edge.get("to"), str):
        return "depends/part_of edge to must be a string"
    if edge.get("type") == "excludes" and not isinstance(edge.get("with"), str):
        return "excludes edge with must be a string"
    for optional in ("state", "or_group", "when", "reason"):
        if optional in edge and edge.get(optional) is not None and not isinstance(edge.get(optional), str):
            return f"edge {optional} must be a string or null"
    if edge.get("type") in {"depends", "excludes"} and not str(edge.get("reason") or "").strip():
        return "depends/excludes edge reason is required"
    when = edge.get("when")
    if isinstance(when, str) and when.strip():
        try:
            _WhenParser(when).parse()
        except WhenSyntaxError as exc:
            return f"edge when is malformed: {exc}"
    return ""


def _when_static_errors(
    key: str,
    edge: Mapping[str, Any],
    *,
    registry: Mapping[str, str],
    registry_present: bool,
) -> list[dict[str, Any]]:
    when = edge.get("when")
    if not isinstance(when, str) or not when.strip():
        return []
    try:
        ast_value = _WhenParser(when).parse()
    except WhenSyntaxError:
        return []
    if not registry_present:
        return [
            {
                "type": "declared_patchwork_checks_missing",
                "node": key,
                "edge": edge,
                "predicate": when,
                "message": f"{DECLARED_PATCHWORK_CHECKS_FIELD} is required when predicates are present",
            }
        ]

    errors: list[dict[str, Any]] = []
    identifiers = sorted(_when_identifiers(ast_value))
    for identifier in identifiers:
        if identifier not in registry:
            errors.append(
                {
                    "type": "when_undeclared_variable",
                    "node": key,
                    "edge": edge,
                    "predicate": when,
                    "variable": identifier,
                    "message": f"when variable {identifier} is not declared in {DECLARED_PATCHWORK_CHECKS_FIELD}",
                }
            )
    if errors:
        return errors
    for left_type, right_type in _when_comparison_type_pairs(ast_value, registry):
        if left_type is not None and right_type is not None and left_type != right_type:
            errors.append(
                {
                    "type": "when_type_error",
                    "node": key,
                    "edge": edge,
                    "predicate": when,
                    "message": f"when comparison mixes {left_type} and {right_type}",
                }
            )
    return errors


def _when_identifiers(ast_value: Any) -> set[str]:
    identifiers: set[str] = set()
    stack = [ast_value]
    while stack:
        current = stack.pop()
        op = _ast_op(current)
        if op == "ident":
            identifiers.add(str(current[1]))
        elif op in {"not"}:
            stack.append(current[1])
        elif op in {"and", "or"}:
            stack.append(current[1])
            stack.append(current[2])
        elif op == "cmp":
            stack.append(current[2])
            stack.append(current[3])
    return identifiers


def _when_comparison_type_pairs(ast_value: Any, registry: Mapping[str, str]) -> list[tuple[str | None, str | None]]:
    pairs: list[tuple[str | None, str | None]] = []
    stack = [ast_value]
    while stack:
        current = stack.pop()
        op = _ast_op(current)
        if op == "cmp":
            pairs.append((_when_operand_type(current[2], registry), _when_operand_type(current[3], registry)))
            stack.append(current[2])
            stack.append(current[3])
        elif op == "not":
            stack.append(current[1])
        elif op in {"and", "or"}:
            stack.append(current[1])
            stack.append(current[2])
    return pairs


def _when_operand_type(ast_value: Any, registry: Mapping[str, str]) -> str | None:
    op = _ast_op(ast_value)
    if op == "ident":
        return registry.get(str(ast_value[1]))
    if op == "literal":
        value = ast_value[1]
        if isinstance(value, int) and not isinstance(value, bool):
            return "counter"
        if isinstance(value, str):
            return "text"
    return None


def _normalize_edges(value: Any, *, child_key: str = "") -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    edges: list[dict[str, Any]] = []
    for edge in value:
        if not isinstance(edge, Mapping):
            continue
        edge_type = edge.get("type")
        normalized = {
            "type": str(edge_type or ""),
            "to": _blank_to_none(edge.get("to")),
            "with": _blank_to_none(edge.get("with")),
            "state": _blank_to_none(edge.get("state")),
            "or_group": _blank_to_none(edge.get("or_group")),
            "when": _blank_to_none(edge.get("when")),
            "reason": _blank_to_none(edge.get("reason")),
        }
        if normalized["type"] == "depends" and not normalized["state"]:
            normalized["state"] = "merged_into_subsystem_tree"
        edges.append(normalized)
    return edges


def _blank_to_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _coerce_part_of_target(to: Any, *, parent_key: str = "root") -> str:
    # Memento / canonical invariant: a part_of edge's `to` is the PARENT NODE KEY
    # ("root", or a child_key/serial_id for nested parents) -- never the series
    # BRANCH ref. The split model recurrently emits the parent branch name
    # (e.g. "ai-org/patch-series/<id>") as `to`, which fails edge validation
    # (_manifest_edge_errors -> target_missing) because a branch ref is not a
    # node key. A branch ref is the only legitimate part_of target that can
    # contain "/", so map any slash-bearing (or empty) target back to the parent
    # node key. This runs at the WRITE site: post-processing alone let the bug
    # recur on every re-refine, so refine/stamp must emit correct edges directly.
    text = str(to or "").strip()
    if not text or "/" in text:
        return parent_key
    return text


def _stamp_parent_provenance_edge(edges: list[dict[str, Any]], *, parent_key: str = "root") -> list[dict[str, Any]]:
    part_of_present = False
    for edge in edges:
        if edge.get("type") == "part_of":
            edge["to"] = _coerce_part_of_target(edge.get("to"), parent_key=parent_key)
            part_of_present = True
    if not part_of_present:
        edges.append({"type": "part_of", "to": parent_key, "with": None, "state": None, "or_group": None, "when": None, "reason": ""})
    return edges


def _depends_edge(to: str, *, reason: str, state: str = "merged_into_subsystem_tree") -> dict[str, Any]:
    return {"type": "depends", "to": to, "with": None, "state": state, "or_group": None, "when": None, "reason": reason}


def _canonical_lifecycle(manifest: Mapping[str, Any]) -> str:
    raw = str(manifest.get("lifecycle_status") or "")
    raw = LEGACY_ACTIVE_STATES.get(raw, raw)
    if raw in LIFECYCLE_STATES:
        return raw
    if raw.startswith("blocked:") or raw == "stale":
        return raw
    # Legacy compatibility is explicit migration support, not authority.
    if manifest.get("node_kind") == "coarse":
        return "posted_to_mailing_list"
    if manifest.get("node_kind") == "leaf":
        return "ready_for_patch_authoring"
    return "posted_to_mailing_list"


def _active_edges(
    manifest: Mapping[str, Any],
    patchwork_check_values: Mapping[str, Any],
    *,
    warnings: list[dict[str, Any]] | None = None,
    node: str = "",
) -> list[dict[str, Any]]:
    active: list[dict[str, Any]] = []
    for index, edge in enumerate(_normalize_edges(manifest.get("edges", []), child_key=str(manifest.get("child_key", "")))):
        when = edge.get("when")
        try:
            is_active = when is None or evaluate_when(when, patchwork_check_values, warnings=warnings, node=node, edge_index=index)
        except WhenSyntaxError as exc:
            if warnings is not None:
                warnings.append({"type": "when_syntax_warning", "node": node, "edge_index": index, "predicate": when, "message": str(exc)})
            is_active = False
        if is_active:
            active.append(edge)
    return active


def _edge_target_satisfied(edge: Mapping[str, Any], nodes: Mapping[str, Any], patchwork_check_values: Mapping[str, Any]) -> bool:
    target = str(edge.get("to") or "")
    manifest = _resolve_node(target, nodes)
    if not manifest:
        return False
    required = str(edge.get("state") or "merged_into_subsystem_tree")
    required = LEGACY_ACTIVE_STATES.get(required, required)
    if required in LIFECYCLE_STATES:
        current = _canonical_lifecycle(manifest)
        if current not in LIFECYCLE_STATES:
            return False
        return LIFECYCLE_STATES.index(current) >= LIFECYCLE_STATES.index(required)
    milestones = manifest.get("milestones")
    if isinstance(milestones, Mapping) and required in milestones:
        return _milestone_state_value(target, required, patchwork_check_values) in {True, "true", "set", "done", "merged_into_subsystem_tree", 1}
    declared = manifest.get("declared_milestones")
    if isinstance(declared, list) and required in declared:
        return _milestone_state_value(target, required, patchwork_check_values) in {True, "true", "set", "done", "merged_into_subsystem_tree", 1}
    return False


def _milestone_state_value(target: str, required: str, patchwork_check_values: Mapping[str, Any]) -> Any:
    registry_name = f"{target}_{required}"
    if registry_name in patchwork_check_values:
        return patchwork_check_values.get(registry_name)
    return patchwork_check_values.get(f"{target}.{required}")


def _resolve_node(target: str, nodes: Mapping[str, Any]) -> Mapping[str, Any] | None:
    node = nodes.get(target)
    if isinstance(node, Mapping):
        return node
    for manifest in nodes.values():
        if isinstance(manifest, Mapping) and target and target == manifest.get("serial_id"):
            return manifest
    return None


def _load_manifest_network_from_path(root: Path) -> dict[str, Any]:
    nodes: dict[str, dict[str, Any]] = {}
    sources: dict[str, list[str]] = {}
    errors: list[dict[str, Any]] = []
    registry: dict[str, str] = {}
    registry_present = False

    def add_node(key: str, manifest: Mapping[str, Any], source: str) -> None:
        sources.setdefault(key, []).append(source)
        if key not in nodes:
            nodes[key] = dict(manifest)

    root_manifest = root / NODE_MANIFEST_PATH
    if root_manifest.exists():
        data = _read_json_file(root_manifest, errors=errors, node="root", report_path=NODE_MANIFEST_PATH)
        if isinstance(data, Mapping):
            registry_present = DECLARED_PATCHWORK_CHECKS_FIELD in data
            registry, registry_errors = _parse_declared_patchwork_checks(
                data.get(DECLARED_PATCHWORK_CHECKS_FIELD),
                node="root",
                present=registry_present,
            )
            errors.extend(registry_errors)
            add_node(_manifest_key(data, "."), data, NODE_MANIFEST_PATH)
    else:
        errors.append({"type": "root_manifest_missing", "path": NODE_MANIFEST_PATH})
    for path in sorted((root / SUBDIR).glob(f"*/{NODE_MANIFEST_PATH}")):
        source = str(path.relative_to(root))
        node_path = str(path.parent.relative_to(root))
        data = _read_json_file(path, errors=errors, node=_patchwork_check_event_node_for_path(source), report_path=source)
        if not isinstance(data, Mapping):
            continue
        key = _manifest_key(data, node_path)
        copy = dict(data)
        copy.setdefault("child_key", key)
        copy.setdefault("node_path", node_path)
        if copy.get("contrib_branch") and _canonical_lifecycle(copy) == "submitted_for_maintainer_review":
            copy["lifecycle_status"] = "merged_into_subsystem_tree"
        add_node(key, copy, source)
    for key, paths in sorted(sources.items()):
        if len(paths) > 1:
            errors.append({"type": "child_key_collision", "child_key": key, "paths": sorted(paths)})
    return {
        "nodes": nodes,
        "errors": errors,
        "declared_patchwork_checks": registry,
        "declared_patchwork_checks_present": registry_present,
    }


def _load_manifest_network_from_git(
    repo: Path,
    branch: str,
    *,
    logical_branch: str | None = None,
    frozen_root_oid: str | None = None,
) -> dict[str, Any]:
    source_oid = frozen_root_oid or git_wrapper.head_sha(repo, branch)
    lifecycle_branch = logical_branch or branch
    nodes: dict[str, dict[str, Any]] = {}
    sources: dict[str, list[str]] = {}
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    registry: dict[str, str] = {}
    registry_present = False

    root_snapshot = patch_series_bodies.classify_root_generation(
        repo, frozen_root_oid or branch
    )
    if root_snapshot.lifecycle_blocked:
        return {
            "nodes": {},
            "errors": [
                {
                    "type": root_snapshot.disposition,
                    "path": ".",
                    "frozen_oid": root_snapshot.frozen_oid,
                    "message": root_snapshot.diagnostic,
                }
            ],
            "warnings": [],
            "declared_patchwork_checks": {},
            "declared_patchwork_checks_present": False,
        }
    frozen_ref = frozen_root_oid or root_snapshot.frozen_oid or branch

    def add_node(key: str, manifest: Mapping[str, Any], source: str) -> None:
        sources.setdefault(key, []).append(source)
        if key not in nodes:
            nodes[key] = dict(manifest)

    if source_oid is None:
        return {
            "nodes": nodes,
            "errors": [{"type": "root_manifest_missing", "path": NODE_MANIFEST_PATH}],
            "warnings": warnings,
            "declared_patchwork_checks": registry,
            "declared_patchwork_checks_present": registry_present,
        }

    root_manifest_paths = {
        NODE_MANIFEST_PATH, str(Path(NODE_MANIFEST_PATH).with_suffix(".cue"))
    }
    manifest_paths = [
        path for path in git_wrapper.tree_files(repo, frozen_ref)
        if path in root_manifest_paths or _is_direct_node_manifest_path(path)
    ]
    if not root_manifest_paths.intersection(manifest_paths):
        errors.append({"type": "root_manifest_missing", "path": NODE_MANIFEST_PATH})
    for manifest_path in sorted(manifest_paths):
        node_path = "." if manifest_path in root_manifest_paths else str(Path(manifest_path).parent)
        manifest = _read_node_manifest(repo, frozen_ref, node_path, errors=errors)
        if manifest:
            if node_path == ".":
                registry_present = DECLARED_PATCHWORK_CHECKS_FIELD in manifest
                registry, registry_errors = _parse_declared_patchwork_checks(
                    manifest.get(DECLARED_PATCHWORK_CHECKS_FIELD),
                    node="root",
                    present=registry_present,
                )
                errors.extend(registry_errors)
            key = _manifest_key(manifest, node_path)
            manifest = dict(manifest)
            manifest.setdefault("child_key", key)
            manifest.setdefault("node_path", node_path)
            if _leaf_resolved(
                repo,
                frozen_ref,
                node_path,
                manifest,
                root_snapshot=root_snapshot,
            ):
                manifest = {**manifest, "lifecycle_status": "merged_into_subsystem_tree"}
            elif node_path != "." and manifest.get("contrib_branch") and _canonical_lifecycle(manifest) == "merged_into_subsystem_tree":
                warnings.append({"type": "lifecycle_merge_evidence_missing", "node": key, "node_path": node_path})
            else:
                authoring_projected = _authoring_projected_lifecycle(
                    repo, lifecycle_branch, node_path, manifest
                )
                if authoring_projected:
                    manifest = {**manifest, "lifecycle_status": authoring_projected}
            add_node(key, manifest, manifest_path)
    for key, paths in sorted(sources.items()):
        if len(paths) > 1:
            errors.append({"type": "child_key_collision", "child_key": key, "paths": sorted(paths)})
    return {
        "nodes": nodes,
        "errors": errors,
        "warnings": warnings,
        "declared_patchwork_checks": registry,
        "declared_patchwork_checks_present": registry_present,
    }


def _is_direct_node_manifest_path(path: str) -> bool:
    parts = Path(path).parts
    return (
        len(parts) == 3
        and parts[0] == SUBDIR
        and parts[2] in {NODE_MANIFEST_PATH, Path(NODE_MANIFEST_PATH).with_suffix(".cue").name}
    )


def _authoring_projected_lifecycle(repo: Path, branch: str, node_path: str, manifest: Mapping[str, Any]) -> str:
    """Project claimed/submitted lifecycle states from announcement refs, never writes.

    Memento: claimed_by_patch_author and submitted_for_maintainer_review are
    REF-DERIVED projections following the merged-state precedent directly
    above (a stored write would create a second authority over what git
    topology already states). Under the no-lock law (brief 23 addendum) the
    ratified kernel vocabulary KEEPS its names but changes its input:
    claimed_by_patch_author = "some author announced and began authoring"
    (at least one authoring announcement for this address, no contribution
    branch published yet); submitted = any announcing author's contribution
    branch exists on this repo. This projection is VISIBILITY for maintainer
    dashboards and merge selection — it is never an admission gate: discovery
    reads the STORED lifecycle, so an announced series still lists as open
    (家族間重複=競争). An invalid or withdrawn (deleted) announcement projects
    nothing.
    """
    if _canonical_lifecycle(manifest) != "ready_for_patch_authoring":
        return ""
    series_id = _patch_series_id(branch)
    child_key = "" if node_path in {"", "."} else Path(node_path).name
    authoring = [
        evaluation["record"]
        for evaluation in authoring_announcements.list_for_address(repo, series_id, child_key)
        if evaluation.get("state") == "authoring" and isinstance(evaluation.get("record"), Mapping)
    ]
    if not authoring:
        return ""
    for record in authoring:
        contrib_branch = str(record.get("contrib_branch") or "")
        if (
            contrib_branch
            and git_wrapper.branch_exists(repo, contrib_branch)
            and producer_lifecycle.has_implementation_submission(repo, contrib_branch)
            and authoring_announcements.contribution_author_matches(
                repo, contrib_branch, record
            )
        ):
            return "submitted_for_maintainer_review"
    return "claimed_by_patch_author"


def _project_tree_patchwork_check_events(
    root: Path,
    *,
    registry: Mapping[str, str] | None = None,
    registry_present: bool = False,
) -> dict[str, Any]:
    state: dict[str, Any] = {}
    last_events: dict[str, dict[str, Any]] = {}
    errors: list[dict[str, Any]] = []
    canonical = {
        _patchwork_check_logical_path(path.relative_to(root).as_posix()): path
        for path in root.rglob(patchwork_check_stream.CANONICAL_PATH)
    }
    historical = {
        path.relative_to(root).as_posix(): path
        for path in root.rglob(patchwork_check_stream.LEGACY_PATH)
    }
    for logical_path in sorted(set(canonical) | set(historical)):
        node = _patchwork_check_event_node_for_path(logical_path)
        canonical_path = canonical.get(logical_path)
        historical_path = historical.get(logical_path)
        selected_path = canonical_path if canonical_path is not None else historical_path
        if not patchwork_check_stream.is_permitted_source_path(logical_path):
            assert selected_path is not None
            errors.append({
                "type": "patchwork_check_stream_invalid",
                "node": node,
                "path": selected_path.relative_to(root).as_posix(),
                "message": "source-path-anchor",
            })
            continue
        if canonical_path is not None and historical_path is not None:
            errors.append({
                "type": "patchwork_check_stream_invalid",
                "node": node,
                "path": canonical_path.relative_to(root).as_posix(),
                "message": "ambiguous-stream-coordinate",
            })
            continue
        if canonical_path is not None:
            rel_path = canonical_path.relative_to(root).as_posix()
            if not registry_present:
                errors.append({"type": "patchwork_check_stream_invalid", "node": node, "path": rel_path, "message": "declared_patchwork_checks is required"})
                continue
            try:
                raw = canonical_path.read_bytes()
            except OSError as exc:
                errors.append({"type": "ingestion_invalid", "node": node, "path": rel_path, "message": str(exc)})
                continue
            _project_patchwork_check_stream_bytes(
                raw, state, last_events, errors,
                node=node, canonical_path=rel_path, source_path=logical_path,
                registry=registry or {},
            )
            continue
        assert historical_path is not None
        try:
            raw = historical_path.read_bytes()
        except OSError as exc:
            errors.append({"type": "ingestion_invalid", "node": node, "path": logical_path, "message": str(exc)})
            continue
        # Historical JSONL predates manifest-typed registration. Preserve its
        # established record-level diagnostics when the registry is absent;
        # newly admitted canonical bodies remain fail-closed above.
        _project_patchwork_check_legacy_bytes(
            raw, state, last_events, errors,
            node=node, source_oid="0" * 40, source_path=logical_path,
            registry=registry or {},
        )
    return {"state": state, "last_events": last_events, "errors": errors}


def _project_tree_patchwork_check_events_from_git(
    repo: Path,
    branch: str,
    *,
    registry: Mapping[str, str] | None = None,
    registry_present: bool = False,
) -> dict[str, Any]:
    state: dict[str, Any] = {}
    last_events: dict[str, dict[str, Any]] = {}
    errors: list[dict[str, Any]] = []
    source_oid = git_wrapper.head_sha(repo, branch)
    if source_oid is None:
        return {
            "state": state,
            "last_events": last_events,
            "errors": [_ingestion_error("root", branch, "missing frozen source OID")],
        }
    tree_paths = sorted(git_wrapper.tree_files(repo, source_oid))
    canonical = {
        _patchwork_check_logical_path(path): path
        for path in tree_paths
        if path == patchwork_check_stream.CANONICAL_PATH
        or path.endswith("/" + patchwork_check_stream.CANONICAL_PATH)
    }
    historical = {
        path: path
        for path in tree_paths
        if path == patchwork_check_stream.LEGACY_PATH
        or path.endswith("/" + patchwork_check_stream.LEGACY_PATH)
    }
    for logical_path in sorted(set(canonical) | set(historical)):
        node = _patchwork_check_event_node_for_path(logical_path)
        canonical_path = canonical.get(logical_path)
        historical_path = historical.get(logical_path)
        selected_path = canonical_path if canonical_path is not None else historical_path
        if not patchwork_check_stream.is_permitted_source_path(logical_path):
            assert selected_path is not None
            errors.append({
                "type": "patchwork_check_stream_invalid",
                "node": node,
                "path": selected_path,
                "message": "source-path-anchor",
            })
            continue
        if canonical_path is not None and historical_path is not None:
            errors.append({
                "type": "patchwork_check_stream_invalid",
                "node": node,
                "path": canonical_path,
                "message": "ambiguous-stream-coordinate",
            })
            continue
        if canonical_path is not None:
            if not registry_present:
                errors.append({"type": "patchwork_check_stream_invalid", "node": node, "path": canonical_path, "message": "declared_patchwork_checks is required"})
                continue
            raw, read_error = _git_show_file_bytes(repo, source_oid, canonical_path)
            if raw is None:
                errors.append(_ingestion_error(node, canonical_path, read_error or "missing canonical stream"))
                continue
            _project_patchwork_check_stream_bytes(
                raw, state, last_events, errors,
                node=node, canonical_path=canonical_path, source_path=logical_path,
                registry=registry or {},
            )
            continue
        assert historical_path is not None
        raw, read_error = _git_show_file_bytes(repo, source_oid, historical_path)
        if raw is None:
            errors.append(_ingestion_error(node, logical_path, read_error or "missing legacy stream"))
            continue
        _project_patchwork_check_legacy_bytes(
            raw, state, last_events, errors,
            node=node, source_oid=source_oid, source_path=logical_path,
            registry=registry or {},
        )
    return {"state": state, "last_events": last_events, "errors": errors}


def _project_patchwork_check_stream_bytes(
    raw: bytes,
    state: dict[str, Any],
    last_events: dict[str, dict[str, Any]],
    errors: list[dict[str, Any]],
    *,
    node: str,
    canonical_path: str,
    source_path: str,
    registry: Mapping[str, str],
) -> None:
    """Validate the enclosing CUE body before dispatching its raw stream."""
    from ai_org.body_codec import BodyCodecClient

    try:
        body = patchwork_check_stream.parse_canonical_body(BodyCodecClient(), raw)
        # A canonical body may retain the historical JSONL coordinate it was
        # imported from or identify its current canonical coordinate. Both
        # anchors must still resolve to this exact root/direct-child node.
        if body.get("source_path") not in {source_path, canonical_path}:
            raise patchwork_check_stream.StreamFailure(
                "GIT_IDENTITY", "/body/source_path", "source-path-anchor"
            )
        projection = patchwork_check_stream.replay(
            body, registry, initial_state=state
        )
    except Exception as exc:
        errors.append({"type": "patchwork_check_stream_invalid", "node": node, "path": canonical_path, "message": str(exc)})
        return
    for name, anchor in projection.last_events.items():
        state[name] = projection.state[name]
        last_events[name] = dict(anchor)
    for diagnostic in projection.diagnostics:
        value = diagnostic.as_dict()
        value["node"] = node
        errors.append(value)


def _project_patchwork_check_legacy_bytes(
    raw: bytes,
    state: dict[str, Any],
    last_events: dict[str, dict[str, Any]],
    errors: list[dict[str, Any]],
    *,
    node: str,
    source_oid: str,
    source_path: str,
    registry: Mapping[str, str],
) -> None:
    """Replay legacy bytes exactly, retaining pre-registry record diagnostics."""

    try:
        body = patchwork_check_stream.import_legacy_jsonl(
            raw, source_oid=source_oid, source_path=source_path
        )
        projection = patchwork_check_stream.replay(
            body, registry, initial_state=state
        )
    except Exception as exc:
        errors.append({
            "type": "patchwork_check_stream_invalid",
            "node": node,
            "path": source_path,
            "message": str(exc),
        })
        return
    for name, anchor in projection.last_events.items():
        state[name] = projection.state[name]
        last_events[name] = dict(anchor)
    for diagnostic in projection.diagnostics:
        value = diagnostic.as_dict()
        value["node"] = node
        errors.append(value)


def _patchwork_check_logical_path(path: str) -> str:
    item = PurePosixPath(path)
    return str(item.with_name(patchwork_check_stream.LEGACY_PATH))


def _project_patchwork_check_event_file(
    path: Path,
    state: dict[str, Any],
    last_events: dict[str, dict[str, Any]],
    errors: list[dict[str, Any]],
    *,
    node: str,
    report_path: str,
    registry: Mapping[str, str] | None = None,
    registry_present: bool = False,
) -> None:
    text = _read_jsonl_file(path, errors=errors, node=node, report_path=report_path)
    if text is not None:
        _project_patchwork_check_event_text(
            text,
            state,
            last_events,
            errors,
            node=node,
            path=report_path,
            registry=registry,
            registry_present=registry_present,
        )


def _project_patchwork_check_event_text(
    text: str,
    state: dict[str, Any],
    last_events: dict[str, dict[str, Any]],
    errors: list[dict[str, Any]],
    *,
    node: str,
    path: str = "patchwork-check-events.jsonl",
    registry: Mapping[str, str] | None = None,
    registry_present: bool = False,
) -> None:
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        parsed, error = _parse_ingested_json_line(line, node, path, line_number)
        if error is not None:
            errors.append(error)
            continue
        if not isinstance(parsed, Mapping):
            errors.append({"type": "patchwork_check_event_invalid", "node": node, "line": line_number, "message": "state event must be an object"})
            continue
        try:
            _apply_patchwork_check_event(state, parsed, line_number, registry=registry if registry_present else {})
            last_events[str(parsed["var"])] = {"path": path, "line": line_number}
        except PatchSeriesGateError as exc:
            errors.append({"type": "patchwork_check_event_invalid", "node": node, "line": line_number, "message": str(exc)})


def _patchwork_check_event_node_for_path(path: str) -> str:
    parent = Path(path).parent
    if str(parent) in {"", "."}:
        return "root"
    return parent.name


def _apply_patchwork_check_event(
    state: dict[str, Any],
    event: Mapping[str, Any],
    index: int,
    *,
    registry: Mapping[str, str] | None = None,
) -> None:
    if not isinstance(event, Mapping):
        raise PatchSeriesGateError(f"state event {index} must be an object")
    var = event.get("var")
    op = event.get("op")
    if not isinstance(var, str) or not var:
        raise PatchSeriesGateError(f"state event {index} has invalid var")
    declared_type = registry.get(var) if registry is not None else None
    if registry is not None and declared_type is None:
        raise PatchSeriesGateError(f"state event {index} writes undeclared variable {var}")
    if op == "set":
        value = event.get("value")
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            raise PatchSeriesGateError(f"state event {index} set value must be integer or string")
        if declared_type == "counter" and not isinstance(value, int):
            raise PatchSeriesGateError(f"state event {index} set value for counter {var} must be integer")
        if declared_type == "text" and not isinstance(value, str):
            raise PatchSeriesGateError(f"state event {index} set value for text {var} must be string")
        state[var] = value
    elif op in {"inc", "dec"}:
        if declared_type is not None and declared_type != "counter":
            raise PatchSeriesGateError(f"state event {index} cannot {op} non-counter var {var}")
        current = state.get(var, 0)
        if isinstance(current, bool) or not isinstance(current, int):
            raise PatchSeriesGateError(f"state event {index} cannot {op} non-integer var {var}")
        delta = event.get("value", 1)
        if isinstance(delta, bool) or not isinstance(delta, int):
            raise PatchSeriesGateError(f"state event {index} {op} value must be integer")
        state[var] = current + delta if op == "inc" else current - delta
    else:
        raise PatchSeriesGateError(f"state event {index} op must be set, inc, or dec")


def _validate_manifest_edges(
    key: str,
    manifest: Mapping[str, Any],
    nodes: Mapping[str, Any],
    key_set: set[str],
    serial_set: set[str],
    *,
    registry: Mapping[str, str] | None = None,
    registry_present: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    declared_key = manifest.get("child_key")
    if isinstance(declared_key, str) and declared_key and key != "root" and not _valid_child_key(declared_key):
        errors.append({"type": "child_key_invalid", "node": key, "child_key": declared_key, "message": "child_key must match [a-z0-9_]+"})
    edges = _normalize_edges(manifest.get("edges", []), child_key=key)
    if not isinstance(manifest.get("edges", []), list):
        errors.append({"type": "edge_schema", "node": key, "message": "edges must be an array"})
        return errors, warnings
    for edge in edges:
        form_error = _edge_form_error(edge)
        if form_error:
            errors.append({"type": "edge_schema", "node": key, "edge": edge, "message": form_error})
            continue
        errors.extend(_when_static_errors(key, edge, registry=registry or {}, registry_present=registry_present))
        if edge["type"] in {"depends", "part_of"}:
            target = str(edge.get("to") or "")
            if target != "root" and target not in key_set and target not in serial_set:
                errors.append({"type": "target_missing", "node": key, "target": target})
        if edge["type"] == "excludes":
            target = str(edge.get("with") or "")
            if target not in key_set and target not in serial_set:
                errors.append({"type": "target_missing", "node": key, "target": target})
        if edge["type"] == "depends":
            target = str(edge.get("to") or "")
            target_manifest = _resolve_node(target, nodes)
            state = str(edge.get("state") or "merged_into_subsystem_tree")
            state = LEGACY_ACTIVE_STATES.get(state, state)
            if target_manifest and state not in LIFECYCLE_STATES and not _target_declares_milestone(target_manifest, state):
                errors.append({"type": "state_missing", "node": key, "target": target, "state": state})
    groups: dict[str, list[dict[str, Any]]] = {}
    for edge in edges:
        if edge.get("type") == "depends" and edge.get("or_group"):
            groups.setdefault(str(edge["or_group"]), []).append(edge)
    for group, members in sorted(groups.items()):
        targets = {str(edge.get("to") or "") for edge in members}
        if len(members) == 1:
            warnings.append({"type": "or_group_singleton", "node": key, "or_group": group})
        if len(members) > 1 and len(targets) == 1:
            warnings.append({"type": "or_group_same_target", "node": key, "or_group": group, "target": next(iter(targets))})
    return errors, warnings


def _manifest_edge_form_errors(
    key: str,
    manifest: Mapping[str, Any],
    *,
    registry: Mapping[str, str] | None = None,
    registry_present: bool = False,
) -> list[dict[str, Any]]:
    raw_edges = manifest.get("edges", [])
    if not isinstance(raw_edges, list):
        return [{"type": "edge_schema", "node": key, "message": "edges must be an array"}]
    errors: list[dict[str, Any]] = []
    for edge in raw_edges:
        form_error = _edge_form_error(edge)
        if form_error:
            errors.append({"type": "edge_schema", "node": key, "edge": edge, "message": form_error})
            continue
        if isinstance(edge, Mapping):
            errors.extend(_when_static_errors(key, edge, registry=registry or {}, registry_present=registry_present))
    return errors


def _target_declares_milestone(manifest: Mapping[str, Any], milestone: str) -> bool:
    declared = manifest.get("declared_milestones")
    milestones = manifest.get("milestones")
    return (
        isinstance(declared, list) and milestone in declared
    ) or (
        isinstance(milestones, Mapping) and milestone in milestones
    )


def _citation_errors(root: Path, key: str, manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    node_path = str(manifest.get("node_path") or ("." if key == "root" else f"{SUBDIR}/{key}"))
    request_path = root / _node_file(node_path, "maintainer-series-request.json")
    request_errors: list[dict[str, Any]] = []
    request = _read_json_file(request_path, errors=request_errors, node=key, report_path=_node_file(node_path, "maintainer-series-request.json"))
    return request_errors + _citation_errors_for_refs(
        key,
        _path_refs(request),
        lambda ref: (root / ref).is_file(),
        lambda ref: (root / ref).exists(),
    )


def _citation_errors_from_git(
    repo: Path,
    branch: str,
    key: str,
    manifest: Mapping[str, Any],
    tree_file_set: set[str],
) -> list[dict[str, Any]]:
    node_path = str(manifest.get("node_path") or ("." if key == "root" else f"{SUBDIR}/{key}"))
    request_errors: list[dict[str, Any]] = []
    request_path = _node_file(node_path, "maintainer-series-request.json")
    request = _read_json(repo, branch, request_path, errors=request_errors, node=key)
    tree_prefixes = {
        prefix
        for path in tree_file_set
        for prefix in _parent_tree_paths(path)
    }
    return request_errors + _citation_errors_for_refs(
        key,
        _path_refs(request),
        lambda ref: ref in tree_file_set,
        lambda ref: ref in tree_file_set or ref in tree_prefixes,
    )


# Memento / canonical invariant: a slash-bearing token harvested by _path_refs
# only counts as a path CITATION if it plausibly names a real repo path --
# either its last segment carries a recognizable file EXTENSION (e.g.
# "reference/cue_requirements.md", "ai_org/patch_author/code_worker.py") OR it
# resolves to a committed file/dir on the branch. Extension-less, non-resolving
# tokens are prose, not paths (e.g. "docs/config/mechanical" enumerating the
# docs/config/mechanical item TYPES), and MUST NOT raise citation_missing.
# Genuine dangling citations (extension present but no such file) stay flagged.
_PATH_CITATION_EXT_RE = re.compile(r"\.[A-Za-z0-9_]+$")


def _has_path_extension(ref: str) -> bool:
    return bool(_PATH_CITATION_EXT_RE.search(Path(ref).name))


def _parent_tree_paths(path: str) -> tuple[str, ...]:
    parts = PurePosixPath(path).parts
    return tuple(str(PurePosixPath(*parts[:index])) for index in range(1, len(parts)))


def _citation_errors_for_refs(
    key: str, refs: set[str], is_file: Any, exists: Any | None = None
) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    path_exists = exists or is_file
    for ref in sorted(refs):
        if _invalid_citation_ref(ref):
            errors.append({"type": "citation_invalid", "node": key, "path": ref, "message": "citation path must stay inside the network tree"})
        elif is_file(ref):
            continue
        elif path_exists(ref) or _has_path_extension(ref):
            errors.append({"type": "citation_missing", "node": key, "path": ref})
        # else: extension-less, non-resolving token -> prose, not a citation
    return errors


# Memento / canonical invariant: citation_missing exists to catch DANGLING
# EVIDENCE citations -- paths a request relies on as pre-existing (context,
# dependency, reference). It must NOT require the series' OWN to-be-produced
# OUTPUTS to already exist. `desired_outcomes_success` enumerates the series'
# declared success outputs (for split children it is join(acceptance_criteria)),
# so paths there are forward-references the series will CREATE, not evidence it
# cites. Skip that field when harvesting citation refs; every other field
# (raw_request, problem_or_motivation, references, background_facts,
# constraints_assumptions, proposal_hint, ...) is still checked, so genuine
# dangling evidence citations remain flagged.
_OUTPUT_DECLARATION_FIELDS = frozenset({"desired_outcomes_success"})


def _path_refs(value: Any) -> set[str]:
    refs: set[str] = set()
    stack = [value]
    while stack:
        current = stack.pop()
        if isinstance(current, str):
            refs.update(match.group(0).rstrip(".,);") for match in _PATH_REF_RE.finditer(current))
        elif isinstance(current, Mapping):
            stack.extend(sub for key, sub in current.items() if key not in _OUTPUT_DECLARATION_FIELDS)
        elif isinstance(current, list):
            stack.extend(current)
    return refs


def _invalid_citation_ref(ref: str) -> bool:
    parts = Path(ref).parts
    return not ref or ref.startswith("/") or ".." in parts


def _drift_warnings(
    root: Path,
    key: str,
    manifest: Mapping[str, Any],
    key_set: set[str],
    *,
    mention_index: tuple[re.Pattern[str] | None, dict[str, set[str]]] | None = None,
) -> list[dict[str, Any]]:
    node_path = str(manifest.get("node_path") or f"{SUBDIR}/{key}")
    request = _read_json_file(root / _node_file(node_path, "maintainer-series-request.json"))
    return _drift_warnings_for_request(key, manifest, key_set, request, mention_index=mention_index)


def _drift_warnings_from_git(
    repo: Path,
    branch: str,
    key: str,
    manifest: Mapping[str, Any],
    key_set: set[str],
    *,
    mention_index: tuple[re.Pattern[str] | None, dict[str, set[str]]] | None = None,
) -> list[dict[str, Any]]:
    node_path = str(manifest.get("node_path") or f"{SUBDIR}/{key}")
    request = _read_json(repo, branch, _node_file(node_path, "maintainer-series-request.json"))
    return _drift_warnings_for_request(key, manifest, key_set, request, mention_index=mention_index)


def _drift_warnings_for_request(
    key: str,
    manifest: Mapping[str, Any],
    key_set: set[str],
    request: Any,
    *,
    mention_index: tuple[re.Pattern[str] | None, dict[str, set[str]]] | None = None,
) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    declared = {str(edge.get("to")) for edge in _depends_edges(manifest)}
    declared_sources = {
        str(edge.get("to"))
        for edge in _depends_edges(manifest)
        if edge.get("reason") != "migrated: code-derived"
    }
    code_derived = {
        str(edge.get("to"))
        for edge in _depends_edges(manifest)
        if edge.get("reason") == "migrated: code-derived" and str(edge.get("to")) not in declared_sources
    }
    if code_derived:
        warnings.append({"type": "migration_divergence", "node": key, "code_derived_only": sorted(code_derived)})
    request_text = json.dumps(request, sort_keys=True, ensure_ascii=True).lower()
    mentioned = _mentioned_node_keys(request_text, key_set, mention_index=mention_index) - {key}
    missing = sorted(mentioned - declared)
    over_declared = sorted(target for target in declared if target in key_set and target not in mentioned and target not in code_derived)
    if missing:
        warnings.append({"type": "drift_missing_edge", "node": key, "targets": missing})
    if over_declared:
        warnings.append({"type": "drift_over_declared_edge", "node": key, "targets": over_declared})
    return warnings


def _node_key_mention_index(key_set: set[str]) -> tuple[re.Pattern[str] | None, dict[str, set[str]]]:
    lower_to_keys: dict[str, set[str]] = {}
    for key in key_set:
        if not _valid_child_key(key):
            continue
        lower_to_keys.setdefault(key.lower(), set()).add(key)
    if not lower_to_keys:
        return None, lower_to_keys
    alternatives = "|".join(re.escape(key) for key in sorted(lower_to_keys, key=lambda value: (-len(value), value)))
    return re.compile(rf"(?<![A-Za-z0-9_-])({alternatives})(?![A-Za-z0-9_-])"), lower_to_keys


def _mentioned_node_keys(
    text: str,
    key_set: set[str],
    *,
    mention_index: tuple[re.Pattern[str] | None, dict[str, set[str]]] | None = None,
) -> set[str]:
    pattern, lower_to_keys = mention_index or _node_key_mention_index(key_set)
    if pattern is None:
        return set()
    mentioned: set[str] = set()
    for match in pattern.finditer(text):
        mentioned.update(lower_to_keys.get(match.group(1), set()))
    return mentioned


def _mentions_node_key(text: str, key: str) -> bool:
    escaped = re.escape(key.lower())
    return re.search(rf"(?<![A-Za-z0-9_-]){escaped}(?![A-Za-z0-9_-])", text) is not None


def _instant_cycle(
    nodes: Mapping[str, Any],
    patchwork_check_values: Mapping[str, Any],
    *,
    warnings: list[dict[str, Any]] | None = None,
) -> list[str]:
    edges = [
        {"prerequisite_child_key": prerequisite, "dependent_child_key": dependent}
        for prerequisite, dependent in _blocking_dependency_pairs(nodes, patchwork_check_values, warnings=warnings)
    ]
    return _cycle(edges)


def _active_dependency_pairs(nodes: Mapping[str, Any], patchwork_check_values: Mapping[str, Any]) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for key, manifest in nodes.items():
        if not isinstance(manifest, Mapping):
            continue
        for edge in _active_edges(manifest, patchwork_check_values, node=str(key)):
            if edge.get("type") == "depends":
                target = str(edge.get("to") or "")
                if target in nodes:
                    pairs.append((target, str(key)))
    return pairs


def _blocking_dependency_pairs(
    nodes: Mapping[str, Any],
    patchwork_check_values: Mapping[str, Any],
    *,
    warnings: list[dict[str, Any]] | None = None,
) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for key, manifest in nodes.items():
        if not isinstance(manifest, Mapping):
            continue
        depends = [edge for edge in _active_edges(manifest, patchwork_check_values, warnings=warnings, node=str(key)) if edge.get("type") == "depends"]
        and_edges = [edge for edge in depends if not edge.get("or_group")]
        for edge in and_edges:
            target = str(edge.get("to") or "")
            if target in nodes and not _edge_target_satisfied(edge, nodes, patchwork_check_values):
                pairs.append((target, str(key)))
        groups: dict[str, list[Mapping[str, Any]]] = {}
        for edge in depends:
            group = edge.get("or_group")
            if isinstance(group, str) and group:
                groups.setdefault(group, []).append(edge)
        for members in groups.values():
            if members and not any(_edge_target_satisfied(edge, nodes, patchwork_check_values) for edge in members):
                for edge in members:
                    target = str(edge.get("to") or "")
                    if target in nodes:
                        pairs.append((target, str(key)))
    return pairs


def _exclusion_consistency_errors(nodes: Mapping[str, Any], patchwork_check_values: Mapping[str, Any]) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    exclusions: set[tuple[str, str]] = set()
    for key, manifest in nodes.items():
        if not isinstance(manifest, Mapping):
            continue
        for edge in _normalize_edges(manifest.get("edges", []), child_key=str(key)):
            if edge.get("type") == "excludes" and edge.get("with"):
                exclusions.add((str(key), str(edge["with"])))
    for left, right in sorted(exclusions):
        if (right, left) not in exclusions:
            errors.append({"type": "exclusion_asymmetry", "node": left, "with": right})
        if _holds_claim_or_acceptance(nodes.get(left, {})) and _holds_claim_or_acceptance(nodes.get(right, {})):
            errors.append({"type": "exclusion_conflict", "nodes": [left, right]})
    return errors


def _holds_claim_or_acceptance(manifest: Any) -> bool:
    if not isinstance(manifest, Mapping):
        return False
    state = _canonical_lifecycle(manifest)
    return state in {"claimed_by_patch_author", "submitted_for_maintainer_review", "merged_into_subsystem_tree"}


def _read_json_file(
    path: Path,
    *,
    errors: list[dict[str, Any]] | None = None,
    node: str = "root",
    report_path: str | None = None,
) -> Any:
    value, error = _ingest_json_from_file(path, node=node, report_path=report_path or str(path))
    if error is not None and errors is not None:
        errors.append(error)
    return value


def _read_jsonl_file(
    path: Path,
    *,
    errors: list[dict[str, Any]] | None = None,
    node: str = "root",
    report_path: str | None = None,
) -> str | None:
    text, error = _ingest_text_from_file(path, node=node, report_path=report_path or str(path))
    if error is not None and errors is not None:
        errors.append(error)
    return text


def _read_jsonl_from_git(
    repo: Path,
    branch: str,
    path: str,
    *,
    errors: list[dict[str, Any]] | None = None,
    node: str = "root",
    report_path: str | None = None,
) -> str | None:
    raw, read_error = _git_show_file_bytes(repo, branch, path)
    if raw is None:
        if read_error is not None and errors is not None:
            errors.append(_ingestion_error(node, report_path or path, read_error))
        return None
    text, error = _ingest_text(raw, node=node, path=report_path or path)
    if error is not None and errors is not None:
        errors.append(error)
    return text


def _ingest_json_from_file(path: Path, *, node: str, report_path: str) -> tuple[Any, dict[str, Any] | None]:
    text, error = _ingest_text_from_file(path, node=node, report_path=report_path)
    if error is not None or text is None:
        return None, error
    return _parse_ingested_json(text, node, report_path)


def _ingest_text_from_file(path: Path, *, node: str, report_path: str) -> tuple[str | None, dict[str, Any] | None]:
    try:
        if not path.exists():
            return None, None
        if not path.is_file():
            return None, _ingestion_error(node, report_path, "path is not a regular file")
        raw = path.read_bytes()
    except OSError as exc:
        return None, _ingestion_error(node, report_path, str(exc))
    return _ingest_text(raw, node=node, path=report_path)


def _ingest_text(raw: bytes, *, node: str, path: str) -> tuple[str | None, dict[str, Any] | None]:
    # Memento: all committed content enters through here; totality is enforced at
    # the boundary, not per symptom.
    if len(raw) > MAX_INGESTION_BYTES:
        return None, _ingestion_error(node, path, f"file exceeds {MAX_INGESTION_BYTES} byte ingestion cap")
    try:
        return raw.decode("utf-8"), None
    except UnicodeDecodeError as exc:
        return None, _ingestion_error(node, path, f"invalid UTF-8: {exc}")


def _parse_ingested_json(text: str, node: str, path: str) -> tuple[Any, dict[str, Any] | None]:
    try:
        value = json.loads(text)
    except ValueError as exc:
        return None, _ingestion_error(node, path, f"invalid JSON: {exc}")
    if _json_depth_exceeds(value, MAX_JSON_DEPTH):
        return None, _ingestion_error(node, path, f"JSON depth exceeds {MAX_JSON_DEPTH}")
    return value, None


def _parse_ingested_json_line(text: str, node: str, path: str, line: int) -> tuple[Any, dict[str, Any] | None]:
    try:
        value = json.loads(text)
    except ValueError as exc:
        error = _ingestion_error(node, path, f"invalid JSON on line {line}: {exc}")
        error["line"] = line
        return None, error
    if _json_depth_exceeds(value, MAX_JSON_DEPTH):
        error = _ingestion_error(node, path, f"JSON depth exceeds {MAX_JSON_DEPTH} on line {line}")
        error["line"] = line
        return None, error
    return value, None


def _json_depth_exceeds(value: Any, limit: int) -> bool:
    stack: list[tuple[Any, int]] = [(value, 1)]
    while stack:
        current, depth = stack.pop()
        if depth > limit:
            return True
        if isinstance(current, Mapping):
            stack.extend((child, depth + 1) for child in current.values())
        elif isinstance(current, list):
            stack.extend((child, depth + 1) for child in current)
    return False


def _ingestion_error(node: str, path: str, message: str) -> dict[str, Any]:
    return {"type": "ingestion_invalid", "node": node or "root", "path": path, "message": message}


def _git_show_file_bytes(repo: Path, ref: str, path: str) -> tuple[bytes | None, str | None]:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "show", f"{ref}:{path}"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError as exc:
        return None, str(exc)
    if result.returncode != 0:
        return None, None
    return result.stdout, None


def _mermaid_id(value: str) -> str:
    return "n_" + re.sub(r"[^A-Za-z0-9_]", "_", value)


class _WhenParser:
    def __init__(self, text: str) -> None:
        if len(text) > MAX_WHEN_LENGTH:
            raise WhenSyntaxError(f"expression exceeds {MAX_WHEN_LENGTH} characters")
        self.tokens = self._tokens(text)
        self.index = 0
        self.term_count = 0

    def parse(self) -> Any:
        expr = self._or()
        if self._peek()[0] != "eof":
            raise WhenSyntaxError(f"unexpected token {self._peek()[1]!r}")
        return expr

    def _tokens(self, text: str) -> list[tuple[str, Any]]:
        tokens: list[tuple[str, Any]] = []
        pos = 0
        depth = 0
        while pos < len(text):
            match = _TOKEN_RE.match(text, pos)
            if not match:
                raise WhenSyntaxError("invalid token")
            pos = match.end()
            if match.group("bad"):
                raise WhenSyntaxError(f"invalid token {match.group('bad')!r}")
            if match.group("int"):
                literal = match.group("int")
                if len(literal) > MAX_WHEN_INTEGER_DIGITS:
                    raise WhenSyntaxError(f"integer literal exceeds {MAX_WHEN_INTEGER_DIGITS} digits")
                try:
                    tokens.append(("literal", int(literal)))
                except ValueError as exc:
                    raise WhenSyntaxError("invalid integer literal") from exc
            elif match.group("string"):
                try:
                    tokens.append(("literal", ast.literal_eval(match.group("string"))))
                except (SyntaxError, ValueError) as exc:
                    raise WhenSyntaxError("invalid string literal") from exc
            elif match.group("op"):
                op = match.group("op")
                if op == "(":
                    depth += 1
                    if depth > MAX_WHEN_NESTING:
                        raise WhenSyntaxError("expression nesting is too deep")
                elif op == ")":
                    depth -= 1
                    if depth < 0:
                        raise WhenSyntaxError("unbalanced parentheses")
                tokens.append((op, op))
            elif match.group("ident"):
                ident = match.group("ident")
                if ident in {"and", "or", "not"}:
                    tokens.append((ident, ident))
                else:
                    tokens.append(("ident", ident))
        tokens.append(("eof", ""))
        return tokens

    def _or(self) -> Any:
        left = self._and()
        while self._accept("or"):
            left = ("or", left, self._and())
        return left

    def _and(self) -> Any:
        left = self._not()
        while self._accept("and"):
            left = ("and", left, self._not())
        return left

    def _not(self) -> Any:
        count = 0
        while self._accept("not"):
            count += 1
            if count > MAX_WHEN_NESTING:
                raise WhenSyntaxError("not nesting is too deep")
        expr = self._comparison()
        self.term_count += 1
        if self.term_count > MAX_WHEN_TERMS:
            raise WhenSyntaxError(f"expression exceeds {MAX_WHEN_TERMS} terms")
        for _ in range(count):
            expr = ("not", expr)
        return expr

    def _comparison(self) -> Any:
        left = self._primary()
        token = self._peek()[0]
        if token in {"==", "!=", "<", "<=", ">", ">="}:
            self.index += 1
            return ("cmp", token, left, self._primary())
        return left

    def _primary(self) -> Any:
        token, value = self._peek()
        if token == "(":
            self.index += 1
            expr = self._or()
            self._expect(")")
            return expr
        if token in {"ident", "literal"}:
            self.index += 1
            return (token, value)
        raise WhenSyntaxError(f"expected expression, got {value!r}")

    def _peek(self) -> tuple[str, Any]:
        return self.tokens[self.index]

    def _accept(self, token: str) -> bool:
        if self._peek()[0] == token:
            self.index += 1
            return True
        return False

    def _expect(self, token: str) -> None:
        if not self._accept(token):
            raise WhenSyntaxError(f"expected {token!r}")


def _eval_when_ast(
    ast: Any,
    patchwork_check_values: Mapping[str, Any],
    *,
    warnings: list[dict[str, Any]] | None = None,
    node: str = "",
    edge_index: int | None = None,
    predicate: str = "",
) -> Any:
    op = ast[0]
    if op == "literal":
        return ast[1]
    if op == "ident":
        # Memento: gate-validated trees supply registry defaults before
        # evaluation, so this fallback is only for adversarial or legacy direct
        # evaluator calls. Do not turn it into a committing-path warning.
        return patchwork_check_values.get(ast[1], 0)
    if op == "not":
        negate = False
        current = ast
        while _ast_op(current) == "not":
            negate = not negate
            current = current[1]
        value = bool(_eval_when_ast(current, patchwork_check_values, warnings=warnings, node=node, edge_index=edge_index, predicate=predicate))
        return not value if negate else value
    if op == "and":
        for operand in _logical_operands(ast, "and"):
            if not bool(_eval_when_ast(operand, patchwork_check_values, warnings=warnings, node=node, edge_index=edge_index, predicate=predicate)):
                return False
        return True
    if op == "or":
        for operand in _logical_operands(ast, "or"):
            if bool(_eval_when_ast(operand, patchwork_check_values, warnings=warnings, node=node, edge_index=edge_index, predicate=predicate)):
                return True
        return False
    if op == "cmp":
        _, cmp_op, left_ast, right_ast = ast
        left = _eval_when_ast(left_ast, patchwork_check_values, warnings=warnings, node=node, edge_index=edge_index, predicate=predicate)
        right = _eval_when_ast(right_ast, patchwork_check_values, warnings=warnings, node=node, edge_index=edge_index, predicate=predicate)
        if type(left) is not type(right):
            # Memento: unreachable for gate-validated trees because
            # declared_patchwork_checks statically checks identifier/literal and
            # identifier/identifier type consistency. Keep this defensive guard
            # for adversarial committed content and direct evaluator calls.
            if warnings is not None:
                warning: dict[str, Any] = {"type": "when_type_warning", "node": node, "predicate": predicate}
                if edge_index is not None:
                    warning["edge_index"] = edge_index
                warnings.append(warning)
            return False
        if cmp_op == "==":
            return left == right
        if cmp_op == "!=":
            return left != right
        if cmp_op == "<":
            return left < right
        if cmp_op == "<=":
            return left <= right
        if cmp_op == ">":
            return left > right
        if cmp_op == ">=":
            return left >= right
    raise WhenSyntaxError("invalid expression")


def _ast_op(value: Any) -> str:
    if isinstance(value, tuple) and value:
        return str(value[0])
    return ""


def _logical_operands(ast: Any, op: str) -> list[Any]:
    operands: list[Any] = []
    stack = [ast]
    while stack:
        current = stack.pop()
        if _ast_op(current) == op:
            stack.append(current[2])
            stack.append(current[1])
        else:
            operands.append(current)
    return operands


def _dependency_errors(children: list[Any], child_keys: set[str]) -> list[str]:
    errors: list[str] = []
    for child in children:
        if not isinstance(child, Mapping):
            continue
        child_key = str(child.get("child_key", ""))
        for edge in _depends_edges(child):
            dep = str(edge.get("to") or "")
            if dep not in child_keys:
                errors.append(f"{child_key} depends edge references unknown child {dep}")
            if dep == child_key:
                errors.append(f"{child_key} cannot depend on itself")
    return sorted(set(errors))


def _depends_edges(child: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    edges = child.get("edges", [])
    if not isinstance(edges, list):
        return []
    return [edge for edge in edges if isinstance(edge, Mapping) and edge.get("type") == "depends"]


def _dependency_edges(children: list[Any]) -> list[dict[str, str]]:
    edges: list[dict[str, str]] = []
    for child in children:
        if not isinstance(child, Mapping):
            continue
        dependent_key = str(child.get("child_key", ""))
        for edge in _depends_edges(child):
            dep = str(edge.get("to") or "")
            if dep:
                edges.append({"prerequisite_child_key": dep, "dependent_child_key": dependent_key})
    return edges


def _cycle(edges: list[Mapping[str, str]]) -> list[str]:
    graph: dict[str, list[str]] = {}
    for edge in edges:
        graph.setdefault(edge["prerequisite_child_key"], []).append(edge["dependent_child_key"])
    visiting: set[str] = set()
    visited: set[str] = set()
    path: list[str] = []
    positions: dict[str, int] = {}

    for node in sorted(graph):
        if node in visited:
            continue
        stack: list[tuple[str, list[str], int]] = [(node, sorted(graph.get(node, [])), 0)]
        while stack:
            current, neighbors, index = stack[-1]
            if current not in visiting and current not in visited:
                visiting.add(current)
                positions[current] = len(path)
                path.append(current)
            if index >= len(neighbors):
                stack.pop()
                if current in visiting:
                    visiting.remove(current)
                    visited.add(current)
                    positions.pop(current, None)
                    if path and path[-1] == current:
                        path.pop()
                continue
            next_node = neighbors[index]
            stack[-1] = (current, neighbors, index + 1)
            if next_node in visiting:
                start = positions[next_node]
                return path[start:] + [next_node]
            if next_node not in visited:
                stack.append((next_node, sorted(graph.get(next_node, [])), 0))
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


def _root_generation_failure(
    snapshot: patch_series_bodies.RootGenerationSnapshot, branch: str
) -> dict[str, Any]:
    """Project one classifier decision into a mutation-free consumer failure."""

    return {
        "ok": False,
        "status": snapshot.disposition,
        "branch": branch,
        "frozen_oid": snapshot.frozen_oid,
        "error": snapshot.diagnostic,
        "root_generation": snapshot.identity(),
        **snapshot.identity(),
    }


def _read_json(
    repo: Path,
    branch: str,
    path: str,
    *,
    errors: list[dict[str, Any]] | None = None,
    node: str = "root",
) -> Any:
    if patch_series_bodies.is_root_cover_path(path):
        root_snapshot = patch_series_bodies.classify_root_generation(repo, branch)
        if root_snapshot.lifecycle_ready:
            try:
                return root_snapshot.cover_letter()
            except Exception as exc:
                if errors is not None:
                    errors.append(_ingestion_error(node, path, str(exc)))
                return None
        # Low-level cover-only imports predate the approach pair. Preserve that
        # member-read compatibility, but never permit a recognized v2 or a
        # mixed tree containing either approach representation to fall back.
        if (
            root_snapshot.disposition == patch_series_bodies.ROOT_DISPOSITION_V2_NOT_READY
            or root_snapshot.raw(patch_series_bodies.ROOT_APPROACH_PATH) is not None
            or root_snapshot.raw(patch_series_bodies.LEGACY_ROOT_APPROACH_PATH) is not None
        ):
            if errors is not None:
                errors.append(
                    _ingestion_error(
                        node,
                        path,
                        root_snapshot.diagnostic,
                    )
                )
            return None
        try:
            return patch_series_bodies.read_cover_letter(
                repo, branch, root_snapshot=root_snapshot
            )
        except Exception as exc:
            if errors is not None:
                errors.append(_ingestion_error(node, path, str(exc)))
            return None
    if path == patch_series_bodies.LEGACY_ROOT_APPROACH_PATH:
        root_snapshot = patch_series_bodies.classify_root_generation(repo, branch)
        if not root_snapshot.lifecycle_ready:
            if errors is not None:
                errors.append(
                    _ingestion_error(
                        node,
                        path,
                        root_snapshot.diagnostic,
                    )
                )
            return None
        try:
            return root_snapshot.technical_approach()
        except Exception as exc:
            if errors is not None:
                errors.append(_ingestion_error(node, path, str(exc)))
            return None
    classified_network_path = network_bodies.classify_path(path)
    if classified_network_path is not None and git_wrapper.file_exists(
        repo, branch, classified_network_path[1]
    ):
        try:
            return network_bodies.read_network_body(repo, branch, path)
        except Exception as exc:
            # Preserve the one pre-admission legacy discriminator so malformed
            # branch-child ledgers are rejected by their established topology
            # rule instead of disappearing as a generic missing body.
            if path == LEDGER_PATH:
                legacy_raw = git_wrapper.show_file(repo, branch, LEDGER_PATH)
                if legacy_raw is not None:
                    try:
                        legacy_value = json.loads(legacy_raw)
                    except (TypeError, ValueError, json.JSONDecodeError):
                        legacy_value = None
                    if _legacy_branch_child_ledger(legacy_value):
                        return legacy_value
            if errors is not None:
                errors.append(_ingestion_error(node, path, str(exc)))
            return None
    if classified_network_path is not None:
        # A tree without the canonical coordinate is historical input. Keep it
        # on the established strict JSON ingestion path so immutable legacy
        # manifests retain their former shape and limit policy; canonical CUE
        # remains the only current-write format.
        path = classified_network_path[2]
    raw, read_error = _git_show_file_bytes(repo, branch, path)
    if raw is None:
        if read_error is not None and errors is not None:
            errors.append(_ingestion_error(node, path, read_error))
        return None
    text, text_error = _ingest_text(raw, node=node, path=path)
    if text_error is not None:
        if errors is not None:
            errors.append(text_error)
        return None
    value, parse_error = _parse_ingested_json(text, node, path)
    if parse_error is not None and errors is not None:
        errors.append(parse_error)
    return value


def _network_file_exists(repo: Path, ref: str, path: str) -> bool:
    """Check one logical network coordinate across its strict representations."""

    classified = network_bodies.classify_path(path)
    if classified is None:
        return git_wrapper.file_exists(repo, ref, path)
    _role, canonical, historical = classified
    return git_wrapper.file_exists(repo, ref, canonical) or git_wrapper.file_exists(
        repo, ref, historical
    )


def _network_path_last_commit(repo: Path, ref: str, path: str) -> str | None:
    """Resolve freshness through the representation present at the frozen ref."""

    classified = network_bodies.classify_path(path)
    if classified is None:
        return git_wrapper.path_last_commit(repo, ref, path)
    _role, canonical, historical = classified
    selected = canonical if git_wrapper.file_exists(repo, ref, canonical) else historical
    return git_wrapper.path_last_commit(repo, ref, selected)


def prepare_network_transition(
    repo: str | Path,
    branch: str,
    changes: Mapping[str, Any],
    *,
    route: str,
    source: FrozenNetworkSourceVector | None = None,
    synthetic_reviews: Mapping[str, Mapping[str, Any]] | None = None,
) -> PreparedNetworkTransition:
    """Prepare one generation-specific complete successor without moving a ref."""

    if route not in ORDINARY_NETWORK_TRANSITION_ROUTES:
        raise ValueError(f"unknown ordinary network transition route: {route}")
    repo_path = Path(repo).resolve()
    normalized = _patch_series_branch(branch)
    # A supplied source vector pins the immutable tree to inspect; it is not a
    # capability token that can bypass producer-pair or exact-scope closure.
    # Re-vet every preparation route against that same OID before reading any
    # network member or constructing a successor tree.
    decision = vet_stateless_authorability(
        repo_path,
        normalized,
        _TRANSITION_VET_ROUTE[route],
        frozen_root_oid=source.frozen_root_oid if source is not None else None,
    )
    if source is None:
        source = decision.source
    if decision.blocked:
        return PreparedNetworkTransition(
            route,
            _transition_source(decision.source, route),
            None,
            decision.diagnostic or PublicationDiagnostic(
                decision.source.route,
                decision.source.frozen_root_oid,
                "root",
                patch_series_bodies.ROOT_DISPOSITION_V2_NOT_READY,
                goal_id="root.problem.goals",
                detail="ordinary migrated production is not enabled",
                context=decision.source.context,
            ),
        )
    source = _transition_source(source, route)
    parent = source.frozen_root_oid
    if not parent:
        return _network_transition_failure(
            route, source, "ref", "missing-frozen-root",
            f"network publication branch is missing: {normalized}",
        )
    snapshot = _vetted_root_snapshot(repo_path, decision.source)
    if normalized != source.branch:
        return _network_transition_failure(
            route, source, "branch", "source-branch-mismatch",
            "network publication source branch does not match the target",
        )
    if snapshot.generation != source.root_generation:
        return _network_transition_failure(
            route, source, "root_generation", "source-generation-mismatch",
            "the frozen tree no longer has the vetted root generation",
        )
    if source.root_sha256 != _root_source_sha256(snapshot):
        return _network_transition_failure(
            route, source, "canonical_digest", "source-digest-mismatch",
            "the frozen tree does not match the canonical source vector",
        )
    if (
        source.source_path != (snapshot.source_path or "")
        or source.context != (snapshot.context or "")
        or source.canonical_digest != (snapshot.canonical_digest or "")
    ):
        return _network_transition_failure(
            route,
            source,
            "canonical_digest",
            "source-canonical-identity-mismatch",
            "the canonical source identity does not match the frozen tree",
        )

    tree_paths = set(git_wrapper.tree_files(repo_path, parent))
    canonical_network_paths: list[tuple[str, str]] = []
    historical_network_paths: list[str] = []
    for tree_path in sorted(tree_paths):
        classified = network_bodies.classify_path(tree_path)
        if classified is None:
            continue
        _role, canonical, historical = classified
        if tree_path == canonical:
            canonical_network_paths.append((canonical, historical))
        elif tree_path == historical:
            historical_network_paths.append(historical)
        if canonical in tree_paths and historical in tree_paths:
            return _network_transition_failure(
                route,
                source,
                historical,
                "coexisting-representations",
                f"both {canonical} and {historical} exist in the frozen tree",
            )
    if canonical_network_paths and historical_network_paths:
        canonical, logical_coordinate = canonical_network_paths[0]
        historical = historical_network_paths[0]
        return _network_transition_failure(
            route,
            source,
            logical_coordinate,
            "coexisting-representations",
            (
                "the frozen network tree mixes generations across coordinates: "
                f"{canonical} and {historical}"
            ),
        )

    files: dict[str, Any] = {}
    seen: set[str] = set()
    try:
        for tree_path in sorted(tree_paths):
            classified = network_bodies.classify_path(tree_path)
            if classified is None:
                continue
            _role, _canonical, historical = classified
            if historical in seen:
                continue
            seen.add(historical)
            body = network_bodies.read_network_body(
                repo_path, parent, historical
            )
            if body is not None:
                files[historical] = body
        changed_targets: set[str] = set()
        for path, value in changes.items():
            classified = network_bodies.classify_path(path)
            if classified is None:
                return _network_transition_failure(
                    route, source, path, "unregistered-path",
                    "network transition contains an unregistered coordinate",
                )
            historical = classified[2]
            if historical in changed_targets:
                return _network_transition_failure(
                    route, source, historical, "coexisting-transition-inputs",
                    "the transition supplies old and new forms of one coordinate",
                )
            changed_targets.add(historical)
            files[historical] = value
        _complete_network_unit(repo_path, normalized, files)

        if snapshot.generation == patch_series_bodies.ROOT_GENERATION_V2:
            root_raw = snapshot.raw(patch_series_bodies.ROOT_APPROACH_PATH)
            ledger = files.get(LEDGER_PATH)
            if root_raw is None or not isinstance(ledger, Mapping):
                return _network_transition_failure(
                    route, source, patch_series_bodies.SCOPE_DECOMPOSITION_PATH,
                    "series-scope-decomposition-required",
                    "canonical v2 publication requires root and ledger",
                )
            if snapshot.disposition == patch_series_bodies.ROOT_DISPOSITION_V2_READY:
                decomposition = patch_series_bodies.project_series_scope_decomposition(
                    repo_path, parent
                )
                if (
                    decomposition.frozen_oid != parent
                    or decomposition.canonical_root_sha256 != source.root_sha256
                    or not source.scope_decomposition_sha256
                    or decomposition.body_sha256
                    != source.scope_decomposition_sha256
                ):
                    return _network_transition_failure(
                        route, source,
                        patch_series_bodies.SCOPE_DECOMPOSITION_PATH,
                        "frozen-scope-vector-mismatch",
                        "SeriesScopeDecomposition does not match the frozen source vector",
                    )
            elif route not in SCOPE_FORMATION_ROUTES:
                return _network_transition_failure(
                    route, source, patch_series_bodies.SCOPE_DECOMPOSITION_PATH,
                    patch_series_bodies.ROOT_DISPOSITION_V2_NOT_READY,
                    "only scope formation may prepare an incomplete v2 root",
                )
            root = BodyCodecClient().parse(
                patch_series_bodies.ROOT_APPROACH_CONTEXT,
                root_raw,
                expected=patch_series_bodies.ROOT_APPROACH_CONTRACT,
            )
            root = patch_series_bodies._legacy_json_value(root)
            ledger = patch_series_bodies._legacy_json_value(ledger)
            if not isinstance(root, Mapping):
                raise ValueError("canonical producer root must be an object")
            if not isinstance(ledger, Mapping):
                raise ValueError("canonical coverage ledger must be an object")
            scope, _prepared_scope = (
                patch_series_bodies.prepare_series_scope_decomposition(
                    parent, root_raw, root, ledger
                )
            )
            files["series-scope-decomposition.json"] = scope

        ref = f"refs/heads/{normalized}"
        prepared = git_wrapper.prepare_network_publication(
            repo_path,
            ref,
            files,
            parent=parent,
            expected_ref_oid=parent,
            synthetic_reviews=synthetic_reviews,
        )
    except Exception as exc:
        details = [str(exc)]
        cause = exc.__cause__
        while cause is not None:
            details.append(str(cause))
            cause = cause.__cause__
        return _network_transition_failure(
            route,
            source,
            "/",
            "network-publication-preflight",
            ": ".join(item for item in details if item),
        )

    source_vector_sha256 = _network_source_vector_sha256(source)
    return PreparedNetworkTransition(
        route=route,
        source=source,
        publication=prepared,
        prepared_source_vector_sha256=source_vector_sha256,
        prepared_generation=snapshot.generation,
    )


def _transition_source(
    source: FrozenNetworkSourceVector, route: str
) -> FrozenNetworkSourceVector:
    """Retain one frozen vector while naming the concrete transition."""

    return FrozenNetworkSourceVector(
        route=route,
        branch=source.branch,
        frozen_root_oid=source.frozen_root_oid,
        root_generation=source.root_generation,
        root_sha256=source.root_sha256,
        scope_decomposition_sha256=source.scope_decomposition_sha256,
        source_path=source.source_path,
        context=source.context,
        canonical_digest=source.canonical_digest,
        root_snapshot=source.root_snapshot,
    )


def _network_source_vector_sha256(source: FrozenNetworkSourceVector) -> str:
    """Seal the complete source vector used to prepare one successor tree."""

    encoded = json.dumps(
        source.as_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _network_transition_failure(
    route: str,
    source: FrozenNetworkSourceVector,
    location: str,
    rule: str,
    detail: str,
) -> PreparedNetworkTransition:
    return PreparedNetworkTransition(
        route,
        source,
        None,
        PublicationDiagnostic(
            route,
            source.frozen_root_oid,
            location,
            rule,
            detail=detail,
            context=source.context,
            status="invalid_network_cohort",
        ),
    )


def _commit_network_files(
    repo: Path,
    branch: str,
    changes: Mapping[str, Any],
    *,
    subject: str,
    source: FrozenNetworkSourceVector | None = None,
    transition_route: str | None = None,
    synthetic_reviews: Mapping[str, Mapping[str, Any]] | None = None,
    prepared_validator: Callable[[PreparedNetworkTransition], None] | None = None,
    required_successor_paths: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Release one fully prepared successor through compare-and-swap."""

    route = transition_route or (
        (
            source.route
            if source.route in ORDINARY_NETWORK_TRANSITION_ROUTES
            else {
                "right_sized": "ordinary_refinement",
                "stale_decision": "stale_marking",
                "rebaseline_decision": "rebaseline",
            }.get(source.route, "ordinary_refinement")
        )
        if source is not None
        else "ordinary_refinement"
    )
    transition = prepare_network_transition(
        repo,
        branch,
        changes,
        route=route,
        source=source,
        synthetic_reviews=synthetic_reviews,
    )
    if transition.prepared and prepared_validator is not None:
        prepared_validator(transition)
    _validate_prepared_successor_members(
        repo, transition, required_successor_paths
    )
    released = publish_network_transition(repo, transition)
    if not released.ok:
        diagnostic = released.diagnostic
        detail = (
            f"{diagnostic.rule}: {diagnostic.detail}"
            if diagnostic is not None and diagnostic.detail
            else diagnostic.rule if diagnostic is not None
            else "not-prepared"
        )
        phase = "publication" if transition.releasable else "publication preflight"
        raise RuntimeError(f"network {phase} failed ({subject}): {detail}")
    return {
        "commit": released.commit_oid,
        "route": transition.route,
        "expected_ref_oids": dict(released.expected_ref_oids),
        "resulting_ref_oids": dict(released.resulting_ref_oids),
        **transition.source.as_dict(),
    }


def _validate_prepared_successor_members(
    repo: Path,
    transition: PreparedNetworkTransition,
    required_paths: tuple[str, ...],
) -> None:
    """Reject a prepared cohort missing any transition-owned tree member."""

    if not required_paths or transition.publication is None:
        return
    commit_oid = transition.publication.commit_oid
    missing: list[str] = []
    for path in required_paths:
        classified = network_bodies.classify_path(path)
        tree_path = classified[1] if classified is not None else path
        if not git_wrapper.file_exists(repo, commit_oid, tree_path):
            missing.append(tree_path)
    if missing:
        raise RuntimeError(
            "network publication preflight failed: prepared successor is "
            f"missing required cohort members: {', '.join(sorted(set(missing)))}"
        )


def publish_network_transition(
    repo: str | Path,
    transition: PreparedNetworkTransition,
    *,
    inject_failure: bool = False,
) -> NetworkTransitionPublicationResult:
    """Release a prepared tree once, preserving preflight and CAS diagnostics."""

    if not transition.releasable:
        diagnostic = transition.diagnostic or PublicationDiagnostic(
            transition.route,
            transition.source.frozen_root_oid,
            "network-publication",
            "successor-not-prepared",
            detail="the ordinary network successor did not pass preflight",
            context=transition.source.context,
        )
        return NetworkTransitionPublicationResult(
            transition.route,
            transition.source,
            diagnostic=diagnostic,
            expected_ref_oids=(
                (
                    transition.publication.ref,
                    transition.publication.expected_ref_oid,
                ),
            )
            if transition.publication is not None
            else (),
        )

    assert transition.publication is not None
    binding_diagnostic = _prepared_network_transition_binding_diagnostic(
        transition
    )
    if binding_diagnostic is not None:
        return NetworkTransitionPublicationResult(
            transition.route,
            transition.source,
            diagnostic=binding_diagnostic,
            expected_ref_oids=(
                (
                    f"refs/heads/{transition.source.branch}",
                    transition.source.frozen_root_oid,
                ),
            ),
        )
    result = git_wrapper.publish_network_publication(
        repo, transition.publication, inject_failure=inject_failure
    )
    if result.ok and result.commit_oid:
        return NetworkTransitionPublicationResult(
            transition.route,
            transition.source,
            commit_oid=result.commit_oid,
            expected_ref_oids=result.expected_ref_oids,
            resulting_ref_oids=result.resulting_ref_oids,
        )

    failure = result.failure
    is_cas = failure is not None and failure.code == "GIT_CAS"
    diagnostic = PublicationDiagnostic(
        transition.route,
        transition.source.frozen_root_oid,
        transition.publication.ref,
        (
            "network-publication-compare-and-swap"
            if is_cas
            else failure.rule if failure is not None
            else "network-publication-rejected"
        ),
        detail=(
            f"{failure.code}: {failure}"
            if failure is not None
            else result.status
        ),
        context=transition.source.context,
        status=(
            "network_publication_conflict"
            if is_cas
            else "invalid_network_cohort"
        ),
    )
    return NetworkTransitionPublicationResult(
        transition.route,
        transition.source,
        diagnostic=diagnostic,
        expected_ref_oids=result.expected_ref_oids,
        resulting_ref_oids=result.resulting_ref_oids,
    )


def _prepared_network_transition_binding_diagnostic(
    transition: PreparedNetworkTransition,
) -> PublicationDiagnostic | None:
    """Reject a prepared tree detached from its preflighted source vector."""

    publication = transition.publication
    assert publication is not None
    source = transition.source
    if (
        transition.route != source.route
        or transition.prepared_generation != source.root_generation
        or transition.prepared_source_vector_sha256
        != _network_source_vector_sha256(source)
    ):
        return PublicationDiagnostic(
            transition.route,
            source.frozen_root_oid,
            "network-publication",
            "prepared-source-vector-mismatch",
            detail="the prepared successor is not sealed to this source vector",
        )

    expected_ref = f"refs/heads/{source.branch}"
    if (
        publication.ref != expected_ref
        or publication.parent_oid != source.frozen_root_oid
        or publication.expected_ref_oid != source.frozen_root_oid
    ):
        return PublicationDiagnostic(
            transition.route,
            source.frozen_root_oid,
            publication.ref,
            "prepared-publication-coordinate-mismatch",
            detail=(
                "the prepared successor ref, parent, or compare-and-swap "
                "expectation differs from the frozen source vector"
            ),
        )
    return None


def _complete_network_unit(repo: Path, branch: str, files: dict[str, Any]) -> None:
    """Fill registered variants and refresh projections without lifecycle choice."""

    root = files.get(NODE_MANIFEST_PATH)
    ledger = files.get(LEDGER_PATH)
    if not isinstance(root, Mapping) or not isinstance(ledger, Mapping):
        raise ValueError("network publication requires root manifest and coverage ledger")
    root = dict(root)
    ledger = dict(ledger)
    # Historical stamping synthesized two convenience keys that were never
    # part of root authority. Canonical root identity is expressed by the
    # root-only coordinates below.
    root.pop("child_key", None)
    root.pop("edges", None)
    root.setdefault("identity_stage", "serialized-parent")
    root.setdefault("branch", branch)
    root.setdefault("relation_from_parent", "root")
    root.setdefault("ownership", {"request_owner": "requester", "interior_owner": "parent"})
    root.setdefault("write_scope", {"allowed_subtree": "."})
    root.setdefault("scope_item_ids", [
        str(item.get("id")) for item in ledger.get("scope_items", [])
        if isinstance(item, Mapping) and item.get("id")
    ])

    child_manifests: dict[str, dict[str, Any]] = {}
    for path, value in list(files.items()):
        classified = network_bodies.classify_path(path)
        if classified is None or classified[0] != "child_manifest" or not isinstance(value, Mapping):
            continue
        child_manifests[str(PurePosixPath(path).parent)] = dict(value)

    existing_spine = _existing_spine_contract_paths(repo, branch)
    ledger_by_key = {
        str(child.get("child_key") or ""): child
        for child in ledger.get("children", [])
        if isinstance(child, Mapping)
    }
    root_by_key = {
        str(child.get("child_key") or ""): child
        for child in root.get("children", [])
        if isinstance(child, Mapping)
    }
    root_children: list[dict[str, Any]] = []
    ledger_children: list[dict[str, Any]] = []
    for directory, manifest in sorted(
        child_manifests.items(), key=lambda item: str(item[1].get("child_key") or item[0])
    ):
        key = str(manifest.get("child_key") or Path(directory).name)
        manifest["edges"] = _network_representation_edges(manifest.get("edges", []))
        files[f"{directory}/{NODE_MANIFEST_PATH}"] = manifest
        child = _child_from_manifest_for_elaboration(repo, branch, manifest)
        child["lifecycle_status"] = str(
            manifest.get("lifecycle_status") or "posted_to_mailing_list"
        )
        request_path = f"{directory}/maintainer-series-request.json"
        request = files.get(request_path)
        if not isinstance(request, Mapping):
            request = _child_request_envelope(branch, child, existing_spine)
            files[request_path] = request
        cover_path = f"{directory}/patch-series-cover-letter.json"
        request_body = request.get("request") if isinstance(request, Mapping) else None
        if not isinstance(files.get(cover_path), Mapping) and isinstance(request_body, Mapping):
            files[cover_path] = dict(request_body)
        files.setdefault(f"{directory}/technical-approach-plan.json", _child_approach(child))
        metadata_path = f"{directory}/{METADATA_PATH}"
        metadata = dict(files.get(metadata_path) or _child_metadata(branch, child, {key: child}, ""))
        metadata["lifecycle_status"] = child["lifecycle_status"]
        metadata["edges"] = _network_representation_edges(metadata.get("edges", []))
        files[metadata_path] = metadata
        approach_path = f"{directory}/technical-approach-plan.json"
        approach = files.get(approach_path)
        if isinstance(approach, Mapping) and isinstance(approach.get("lineage_child"), Mapping):
            approach = copy.deepcopy(dict(approach))
            approach["lineage_child"]["lifecycle_status"] = child["lifecycle_status"]
            files[approach_path] = approach
        if manifest.get("lifecycle_status") == "stale" and key in root_by_key and key in ledger_by_key:
            # Staleness is child state; the root/ledger retain the proposed
            # parent contract against which revalidation compares the child.
            retained_root = dict(root_by_key[key])
            retained_root["edges"] = _network_representation_edges(retained_root.get("edges", []))
            retained_ledger = dict(ledger_by_key[key])
            retained_ledger["edges"] = _network_representation_edges(retained_ledger.get("edges", []))
            root_children.append(retained_root)
            ledger_children.append(retained_ledger)
        else:
            root_children.append(_root_child_projection(manifest))
            ledger_children.append(
                _merge_ledger_child(ledger_by_key.get(key), manifest, prefer_current_rich=True)
            )

    node_path_by_owner = {
        str(child.get("child_key") or ""): str(child.get("node_path") or "")
        for child in ledger_children
    }
    coverage: list[dict[str, Any]] = []
    for item in ledger.get("coverage", []):
        if not isinstance(item, Mapping):
            continue
        projected = dict(item)
        owner = str(projected.get("owner") or "")
        projected["owner_node_path"] = str(
            projected.get("owner_node_path")
            or ("." if owner == "root" else node_path_by_owner.get(owner, ""))
        )
        coverage.append(projected)
    ledger["coverage"] = coverage
    root["children"] = root_children
    ledger["children"] = ledger_children
    files[NODE_MANIFEST_PATH] = root
    files[LEDGER_PATH] = ledger
    files[STATUS_PATH] = _lineage_status(root, ledger)
    if not isinstance(files.get(METADATA_PATH), Mapping):
        files[METADATA_PATH] = {
            "schema": "patch_series-network-node-v1",
            "lifecycle_status": str(root.get("lifecycle_status") or "active"),
            "ledger_commit": "",
        }


def _network_representation_edges(value: Any) -> list[dict[str, Any]]:
    """Encode nullable in-memory edge coordinates as canonical body strings."""

    if not isinstance(value, list):
        return []
    result: list[dict[str, Any]] = []
    for edge in value:
        if not isinstance(edge, Mapping):
            continue
        encoded = dict(edge)
        for field in ("to", "with", "state", "or_group", "when", "reason"):
            if encoded.get(field) is None:
                encoded[field] = ""
        result.append(encoded)
    return result


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


def _has_integration_gate(
    repo: Path,
    frozen_ref: str,
    *,
    root_snapshot: patch_series_bodies.RootGenerationSnapshot,
) -> bool:
    if root_snapshot.lifecycle_blocked:
        return False
    return git_wrapper.has_subject(repo, frozen_ref, "network: integration-gate")


def _parent_escalation_refusal(
    repo: Path,
    parent_branch: str,
    *,
    frozen_ref: str | None = None,
) -> str:
    if not git_wrapper.branch_exists(repo, parent_branch):
        return ""
    decision_ref = frozen_ref or parent_branch
    if _current_patch_series_state(repo, decision_ref) == "nak":
        return "parent-terminal-nak"
    default = git_wrapper.default_branch(repo)
    if parent_branch != default and git_wrapper.is_ancestor(repo, decision_ref, default):
        return "parent-merged-to-default"
    return ""


def _current_patch_series_state(repo: Path, branch: str) -> str:
    subjects = git_wrapper.log_subjects(repo, branch)
    if any("patch_series: nak" in subject for subject in subjects):
        return "nak"
    for subject in subjects:
        if "patch_series: needs-revision round " in subject:
            return "needs_revision"
        if subject.startswith("patch_series v"):
            return "reformed"
        if "patch_series: direction-ok" in subject:
            return "direction-ok"
    return "open"


def _write_synthetic_escalation_round(
    repo: Path,
    parent_branch: str,
    child_address: str,
    evidence: Mapping[str, Any],
    *,
    reviewed_commit: str,
    root_snapshot: patch_series_bodies.RootGenerationSnapshot | None = None,
) -> tuple[str, dict[str, Any]]:
    """Prepare the synthetic review member for the enclosing network CAS."""

    patch_series_id = _patch_series_id(parent_branch)
    round_number = _next_review_round(
        repo, patch_series_id, frozen_ref=reviewed_commit
    )
    root_snapshot = root_snapshot or patch_series_bodies.classify_root_generation(
        repo, reviewed_commit
    )
    if root_snapshot.frozen_oid != reviewed_commit:
        raise RuntimeError("synthetic escalation review crossed its root snapshot")
    root_snapshot.require_lifecycle_ready()
    approach = root_snapshot.technical_approach()
    node_ids = _collect_node_ids(approach)
    anchor = "problem" if "problem" in node_ids else (sorted(node_ids)[0] if node_ids else "problem")
    evidence_citation = json.dumps(evidence, sort_keys=True, ensure_ascii=True)
    objection = {
        "objection_id": f"network:parent-invalidated:{child_address}",
        "anchor_node_ids": [anchor],
        "axis": "scope",
        "type": "blocking",
        "claim": "A child network branch invalidated the parent scope contract.",
        "evidence": [{"source_type": "repo_fact", "citation": evidence_citation, "consulted_terms": []}],
        "impact": "The parent direction and network ledger must be re-authored before dependent children can proceed.",
        "requested_author_action": "revise_subtree",
        "resolution_authority": "author",
        "status": "open",
    }
    marker_subject = f"patch_series: needs-revision round {round_number}"
    base_commit = (
        git_wrapper.merge_base(repo, git_wrapper.default_branch(repo), reviewed_commit)
        or ""
    )
    record = {
        "patch_series_id": patch_series_id,
        "branch": parent_branch,
        "round": round_number,
        "reviewed_commit": reviewed_commit,
        "base_commit": base_commit,
        "inputs": {
            "patch_series_path": (
                patch_series_bodies.COVER_PATH
                if root_snapshot.generation
                in {
                    patch_series_bodies.ROOT_GENERATION_CURRENT,
                    patch_series_bodies.ROOT_GENERATION_V2,
                }
                else patch_series_bodies.LEGACY_COVER_PATH
            ),
            "technical_approach_path": root_snapshot.source_path,
            "root_generation": root_snapshot.identity(),
            "node_ids": sorted(node_ids),
        },
        "axis_reviews": [
            {
                "axis": "scope",
                "verdict": "objections_pending",
                "objections": [objection],
                "reference_consultations": [],
                "attempts": 1,
                "validation_errors": [],
            }
        ],
        "objections": [objection],
        "per_axis_verdicts": {"scope": "objections_pending"},
        "consolidation": {
            "verdict": "needs_revision",
            "summary": "Network child escalation requires parent author re-formation.",
            "deduplicated_objections": [objection],
            "contradiction_resolutions": [],
            "nak_reason": "",
            "evidence": [{"source_type": "repo_fact", "citation": evidence_citation, "consulted_terms": []}],
            "validation_errors": [],
        },
        "verdict": "needs_revision",
        "git_result_marker": marker_subject,
        "serial": git_wrapper.serial_for_ref(repo, reviewed_commit) or "",
        "lineage_escalation": True,
        "escalated_child_branch": child_address,
        "escalated_child_address": child_address,
        "deferred": [
            "author-side v2 re-formation and resend loop",
            "network ledger rebaseline after renewed direction-ok",
        ],
    }
    record_path = review_module.round_record_path(round_number)
    # The containing commit cannot be embedded in its own tree without a hash
    # cycle. The publication boundary validates this value now; rebaseline
    # later derives the containing OID from Git using this exact record path.
    review_bodies.prepare_marker_tree({record_path: record})
    return record_path, record


def _validate_escalation_transition_cohort(
    *,
    source: FrozenNetworkSourceVector,
    child_address: str,
    stale_addresses: list[str],
    changes: Mapping[str, Any],
    review_path: str,
    review_record: Mapping[str, Any],
) -> None:
    """Bind every escalation member to one frozen same-ref successor."""

    child_branch, child_path = _parse_node_address(child_address)
    if child_branch != source.branch:
        raise RuntimeError("escalation cohort child is outside the frozen parent ref")
    if len(stale_addresses) != len(set(stale_addresses)):
        raise RuntimeError("escalation cohort contains a duplicate stale sibling")

    child_change_path = _node_file(
        child_path,
        NODE_MANIFEST_PATH if child_path != "." else METADATA_PATH,
    )
    expected_paths = {child_change_path}
    for stale_address in stale_addresses:
        stale_branch, stale_path = _parse_node_address(stale_address)
        if stale_branch != source.branch or stale_path == child_path:
            raise RuntimeError("escalation cohort contains an invalid stale sibling")
        expected_paths.add(_node_file(stale_path, NODE_MANIFEST_PATH))
    if set(changes) != expected_paths:
        raise RuntimeError("escalation cohort does not contain the exact affected nodes")

    child_record = changes[child_change_path]
    if (
        not isinstance(child_record, Mapping)
        or child_record.get("lifecycle_status") != "blocked:parent-invalidated"
    ):
        raise RuntimeError("escalation cohort does not contain the blocked child")
    for stale_address in stale_addresses:
        stale_path = _node_file(
            _parse_node_address(stale_address)[1], NODE_MANIFEST_PATH
        )
        stale_record = changes.get(stale_path)
        if (
            not isinstance(stale_record, Mapping)
            or stale_record.get("lifecycle_status") != "stale"
        ):
            raise RuntimeError("escalation cohort does not contain every stale sibling")

    _validate_synthetic_escalation_review_binding(
        parent_branch=source.branch,
        frozen_root_oid=source.frozen_root_oid,
        child_address=child_address,
        record_path=review_path,
        record=review_record,
    )


def _validate_frozen_escalation_node(
    repo: Path,
    frozen_root_oid: str,
    parent_branch: str,
    node_path: str,
    manifest: Mapping[str, Any],
    *,
    role: str,
) -> None:
    """Admit an escalation node only from the frozen parent ledger/tree pair."""

    if node_path in {"", "."} or not isinstance(manifest, Mapping) or not manifest:
        raise RuntimeError(f"escalation {role} is absent from the frozen tree")
    ledger = _read_json(repo, frozen_root_oid, LEDGER_PATH)
    if not isinstance(ledger, Mapping):
        raise RuntimeError("escalation cohort has no frozen parent ledger")
    declared = [
        child
        for child in ledger.get("children", [])
        if isinstance(child, Mapping) and str(child.get("node_path") or "") == node_path
    ]
    if len(declared) != 1:
        raise RuntimeError(f"escalation {role} is absent from the frozen ledger")
    ledger_child = declared[0]
    if (
        str(manifest.get("node_path") or "") != node_path
        or str(manifest.get("child_key") or "")
        != str(ledger_child.get("child_key") or "")
        or str(manifest.get("parent_branch") or "") != parent_branch
    ):
        raise RuntimeError(f"escalation {role} does not match the frozen ledger")


def _escalation_successor_paths(
    *,
    source: FrozenNetworkSourceVector,
    changed_paths: tuple[str, ...],
    review_path: str,
) -> tuple[str, ...]:
    """Return every member that must coexist in the escalation commit tree."""

    required = [*changed_paths, review_path, LEDGER_PATH, STATUS_PATH]
    if source.root_generation == patch_series_bodies.ROOT_GENERATION_V2:
        required.append(patch_series_bodies.SCOPE_DECOMPOSITION_PATH)
    return tuple(required)


def _validate_synthetic_escalation_review_binding(
    *,
    parent_branch: str,
    frozen_root_oid: str,
    child_address: str,
    record_path: str,
    record: Mapping[str, Any],
) -> None:
    """Require a registered review to name its exact root and escalation."""

    review_round = _round_number(record)
    expected_path = review_module.round_record_path(review_round)
    expected_marker = f"patch_series: needs-revision round {review_round}"
    child_parent, _child_path = _parse_node_address(child_address)
    if review_round < 1 or record_path != expected_path:
        raise RuntimeError("synthetic escalation review has an invalid registered path")
    if not git_wrapper.GIT_OBJECT_ID_RE.fullmatch(frozen_root_oid):
        raise RuntimeError("synthetic escalation review has an invalid frozen root")
    if child_parent != parent_branch:
        raise RuntimeError("synthetic escalation review crossed its parent ref")
    if (
        record.get("lineage_escalation") is not True
        or str(record.get("branch") or "") != parent_branch
        or str(record.get("patch_series_id") or "") != _patch_series_id(parent_branch)
        or str(record.get("reviewed_commit") or "") != frozen_root_oid
        or str(record.get("escalated_child_branch") or "") != child_address
        or str(record.get("escalated_child_address") or "") != child_address
        or str(record.get("git_result_marker") or "") != expected_marker
    ):
        raise RuntimeError("synthetic escalation review is not bound to the frozen cohort")


def _validate_prepared_escalation_successor(
    transition: PreparedNetworkTransition,
    *,
    child_address: str,
    stale_addresses: list[str],
    review_path: str,
) -> None:
    """Require every escalation cohort member in the immutable prepared tree."""

    publication = transition.publication
    if publication is None:
        raise RuntimeError("escalation successor was not prepared")
    source = transition.source
    if (
        publication.ref != f"refs/heads/{source.branch}"
        or publication.parent_oid != source.frozen_root_oid
        or publication.expected_ref_oid != source.frozen_root_oid
    ):
        raise RuntimeError("prepared escalation successor crossed its frozen ref")

    required_historical_paths = {
        LEDGER_PATH,
        STATUS_PATH,
    }
    if source.root_generation == patch_series_bodies.ROOT_GENERATION_V2:
        required_historical_paths.add(
            patch_series_bodies.SCOPE_DECOMPOSITION_PATH
        )
    for address in [child_address, *stale_addresses]:
        branch, node_path = _parse_node_address(address)
        if branch != source.branch:
            raise RuntimeError("prepared escalation successor crossed its parent ref")
        required_historical_paths.add(_node_file(node_path, NODE_MANIFEST_PATH))

    required_canonical_paths = set()
    for path in required_historical_paths:
        classified = network_bodies.classify_path(path)
        if classified is None:
            raise RuntimeError(f"unregistered escalation successor path: {path}")
        required_canonical_paths.add(classified[1])
    prepared_paths = {
        member.canonical_path for member in publication.publication.members
    }
    missing = sorted(required_canonical_paths - prepared_paths)
    if missing:
        raise RuntimeError(
            "prepared escalation successor is incomplete: " + ", ".join(missing)
        )

    companion_paths = {path for path, _content in publication.companion_files}
    if companion_paths != {review_path}:
        raise RuntimeError(
            "prepared escalation successor has an incomplete synthetic review"
        )


def _next_review_round(
    repo: Path, patch_series_id: str, *, frozen_ref: str | None = None
) -> int:
    rounds = [
        _round_number(record)
        for record in _review_records(repo, patch_series_id, frozen_ref=frozen_ref)
    ]
    return max(rounds, default=0) + 1


def _latest_lineage_escalation_round(
    repo: Path,
    patch_series_id: str,
    *,
    frozen_ref: str | None = None,
) -> dict[str, Any] | None:
    records = [
        record
        for record in _review_records(
            repo, patch_series_id, frozen_ref=frozen_ref
        )
        if record.get("lineage_escalation") is True
    ]
    if not records:
        return None
    return sorted(records, key=_round_number)[-1]


def _review_records(
    repo: Path, patch_series_id: str, *, frozen_ref: str | None = None
) -> list[dict[str, Any]]:
    return review_module.review_round_records(
        repo, patch_series_id, ref=frozen_ref
    )


def _round_number(record: Mapping[str, Any]) -> int:
    try:
        return int(record.get("round", 0))
    except (TypeError, ValueError):
        return 0


def _escalation_rebaseline_record(
    repo: Path,
    branch: str,
    record: Mapping[str, Any],
    *,
    frozen_ref: str | None = None,
) -> dict[str, Any]:
    review_round = _round_number(record)
    expected_record_path = review_module.round_record_path(review_round)
    record_path = str(
        record.get("record_path")
        or expected_record_path
    )
    if record_path != expected_record_path:
        raise RuntimeError("synthetic escalation review has an invalid registered path")
    reviewed_commit = str(record.get("reviewed_commit") or "")
    child_address = str(record.get("escalated_child_branch") or "")
    _validate_synthetic_escalation_review_binding(
        parent_branch=branch,
        frozen_root_oid=reviewed_commit,
        child_address=child_address,
        record_path=record_path,
        record=record,
    )
    containing_commit = git_wrapper.path_last_commit(
        repo, frozen_ref or branch, record_path
    ) or ""
    if not git_wrapper.GIT_OBJECT_ID_RE.fullmatch(containing_commit):
        raise RuntimeError("synthetic escalation review has no containing Git commit")
    _validate_escalation_containing_commit(
        repo,
        branch,
        record,
        containing_commit=containing_commit,
    )
    return {
        "review_round": review_round,
        "child_branch": str(record.get("escalated_child_branch", "")),
        "git_result_commit": containing_commit,
    }


def _validate_escalation_containing_commit(
    repo: Path,
    branch: str,
    record: Mapping[str, Any],
    *,
    containing_commit: str,
) -> None:
    """Prove that a review OID names the complete atomic successor tree."""

    reviewed_commit = str(record.get("reviewed_commit") or "")
    if git_wrapper.parent_commits(repo, containing_commit) != [reviewed_commit]:
        raise RuntimeError(
            "synthetic escalation review is not contained by its direct successor"
        )

    child_address = str(record.get("escalated_child_branch") or "")
    child_branch, child_path = _parse_node_address(child_address)
    if child_branch != branch or child_path in {"", "."}:
        raise RuntimeError("synthetic escalation review has an invalid child address")

    superseded_ledger_commit = (
        _network_path_last_commit(repo, reviewed_commit, LEDGER_PATH) or ""
    )
    affected_addresses = _nodes_with_ledger_commit(
        repo,
        branch,
        superseded_ledger_commit,
        frozen_ref=reviewed_commit,
        exclude={child_path},
    )
    cohort = [(child_address, "blocked:parent-invalidated")]
    cohort.extend((address, "stale") for address in affected_addresses)
    for address, expected_lifecycle in cohort:
        cohort_branch, node_path = _parse_node_address(address)
        if cohort_branch != branch:
            raise RuntimeError("synthetic escalation successor crossed its parent ref")
        frozen_manifest = _read_node_manifest(repo, reviewed_commit, node_path)
        _validate_frozen_escalation_node(
            repo,
            reviewed_commit,
            branch,
            node_path,
            frozen_manifest,
            role="child" if address == child_address else "stale sibling",
        )
        manifest_path = _node_file(node_path, NODE_MANIFEST_PATH)
        successor_manifest = _read_node_manifest(
            repo, containing_commit, node_path
        )
        if (
            successor_manifest.get("lifecycle_status") != expected_lifecycle
            or _network_path_last_commit(repo, containing_commit, manifest_path)
            != containing_commit
        ):
            raise RuntimeError(
                "synthetic escalation review does not contain its complete node cohort"
            )

    required_paths = [LEDGER_PATH, STATUS_PATH]
    if git_wrapper.file_exists(
        repo, reviewed_commit, patch_series_bodies.SCOPE_DECOMPOSITION_PATH
    ):
        required_paths.append(patch_series_bodies.SCOPE_DECOMPOSITION_PATH)
    codec = BodyCodecClient()
    for path in required_paths:
        if not _network_file_exists(repo, containing_commit, path):
            raise RuntimeError(
                "synthetic escalation review does not contain the complete successor projections"
            )
        # Re-read through the registered codec boundary so an extant but
        # malformed projection cannot authorize rebaseline.
        network_bodies.read_network_body(
            repo, containing_commit, path, client=codec
        )


def _normalized_escalation_rebaseline_record(value: Any) -> dict[str, Any]:
    fields = {"review_round", "child_branch", "git_result_commit"}
    if not isinstance(value, Mapping) or set(value) != fields:
        return {}
    round_value = value.get("review_round", 0)
    child_branch = value.get("child_branch")
    git_result_commit = value.get("git_result_commit")
    try:
        if isinstance(round_value, bool):
            return {}
        if isinstance(round_value, int):
            review_round = round_value
        elif callable(getattr(round_value, "as_int_exact", None)):
            review_round = round_value.as_int_exact()
        else:
            return {}
    except (ArithmeticError, TypeError, ValueError):
        return {}
    if (
        isinstance(review_round, bool)
        or not isinstance(review_round, int)
        or review_round < 1
        or not isinstance(child_branch, str)
        or not child_branch
        or not isinstance(git_result_commit, str)
        or not git_wrapper.GIT_OBJECT_ID_RE.fullmatch(git_result_commit)
    ):
        return {}
    return {
        "review_round": review_round,
        "child_branch": child_branch,
        "git_result_commit": git_result_commit,
    }


def _collect_node_ids(value: object) -> set[str]:
    node_ids: set[str] = set()
    stack: list[object] = [value]
    while stack:
        current = stack.pop()
        if isinstance(current, Mapping):
            node_id = current.get("id")
            if isinstance(node_id, str) and node_id:
                node_ids.add(node_id)
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)
    return node_ids


def _branches_with_ledger_commit(repo: Path, ledger_commit: str, *, exclude: set[str] | None = None) -> list[str]:
    if not ledger_commit:
        return []
    excluded = exclude or set()
    branches: list[str] = []
    for branch in sorted(git_wrapper.branches(repo, f"{WORK_ORDER_PREFIX}*")):
        if branch in excluded:
            continue
        metadata = _read_metadata(repo, branch)
        status = str(metadata.get("lifecycle_status", ""))
        if status.startswith("blocked:"):
            continue
        if metadata.get("ledger_commit") == ledger_commit:
            branches.append(branch)
    return branches


def _nodes_with_ledger_commit(
    repo: Path,
    parent_branch: str,
    ledger_commit: str,
    *,
    frozen_ref: str | None = None,
    exclude: set[str] | None = None,
) -> list[str]:
    if not ledger_commit:
        return []
    source_ref = frozen_ref or parent_branch
    excluded = exclude or set()
    ledger = _read_json(repo, source_ref, LEDGER_PATH)
    if not isinstance(ledger, Mapping):
        return []
    nodes: list[str] = []
    for child in ledger.get("children", []):
        if not isinstance(child, Mapping):
            continue
        node_path = str(child.get("node_path") or "")
        if not node_path or node_path in excluded:
            continue
        manifest = _read_node_manifest(repo, source_ref, node_path)
        status = str(manifest.get("lifecycle_status", ""))
        if status.startswith("blocked:"):
            continue
        last_commit = _network_path_last_commit(
            repo, source_ref, _node_file(node_path, NODE_MANIFEST_PATH)
        )
        if last_commit and git_wrapper.is_ancestor(repo, ledger_commit, last_commit):
            nodes.append(_node_address(parent_branch, node_path))
    return nodes


def _stale_child_manifests(repo: Path, parent_branch: str, ledger: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    stale: dict[str, dict[str, Any]] = {}
    for child in ledger.get("children", []):
        if not isinstance(child, Mapping):
            continue
        node_path = str(child.get("node_path") or "")
        if not node_path:
            continue
        manifest = _read_node_manifest(repo, parent_branch, node_path)
        if manifest.get("lifecycle_status") == "stale":
            stale[node_path] = manifest
    return stale


def _ledger_child_for_metadata(
    ledger: Mapping[str, Any],
    address: str,
    metadata: Mapping[str, Any],
) -> Mapping[str, Any] | None:
    child_key = str(metadata.get("child_key", ""))
    node_path = str(metadata.get("node_path", ""))
    for child in ledger.get("children", []):
        if not isinstance(child, Mapping):
            continue
        if child_key and child.get("child_key") == child_key:
            return child
        if node_path and child.get("node_path") == node_path:
            return child
        if child.get("address") == address:
            return child
    return None


def _child_contract_unchanged(
    metadata: Mapping[str, Any],
    child: Mapping[str, Any],
    ledger: Mapping[str, Any],
) -> bool:
    checks = [
        metadata.get("lifecycle_status") == "stale" or _canonical_lifecycle(metadata) == _canonical_lifecycle(child),
        list(metadata.get("scope_item_ids", [])) == list(child.get("scope_item_ids", [])),
        _normalize_edges(metadata.get("edges", []), child_key=str(metadata.get("child_key", "")))
        == _normalize_edges(child.get("edges", []), child_key=str(child.get("child_key", ""))),
    ]
    return all(checks)


def _accepted_source_refs(contrib_branch: str) -> list[str]:
    return [contrib_branch] if contrib_branch else []


def _contrib_branch_for_patch_series_branch(
    repo: Path, source_ref: str, *, series_branch: str
) -> str:
    metadata = _read_metadata(repo, source_ref)
    child_id = str(metadata.get("id", "")).strip()
    if not child_id:
        child_id = _patch_series_id(series_branch)
    contrib_branch = f"ai-org/contrib/{child_id}"
    return contrib_branch if git_wrapper.branch_exists(repo, contrib_branch) else ""


def _patch_series_branch(patch_series_id_or_branch: str) -> str:
    if patch_series_id_or_branch.startswith("refs/heads/"):
        return patch_series_id_or_branch.removeprefix("refs/heads/")
    if patch_series_id_or_branch.startswith(WORK_ORDER_PREFIX):
        return patch_series_id_or_branch
    return f"{WORK_ORDER_PREFIX}{patch_series_id_or_branch}"


def _patch_series_id(patch_series_id_or_branch: str) -> str:
    return _patch_series_branch(patch_series_id_or_branch).removeprefix(WORK_ORDER_PREFIX)
