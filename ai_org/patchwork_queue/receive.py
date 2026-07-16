# receive.py — the INTAKE GATE: judges whether an incoming REQUEST may become a patch series.
# This is NOT a dumb loader/translator. A request is discussed and can be SENT BACK (send-back).
# The real entrance is:
#     submit -> off-git inbox -> patch_series pull runs intake 1/2/3/4 -> promoted patch series branch | inbox result.
# Raw requests never create their own git branches. The off-git inbox is the intake queue; request
# handling creates the shared git-native event medium before the receive gate runs.
#     inbox request --[receive gate]--> promote to patch series | send back for revision | reject.
# Only requests that PASS this gate become grounded patch seriess. The patch series phase has two parts:
#     1) receive : intake — can this REQUEST become a patch series at all? (this file)
#     2) review  : debate the direction of an already-formed patch series. (review.py)
# The patch series phase starts by taking a raw request and forming a grounded patch series view. That promotion is
# real work, not a load. It mirrors the Linux early-stage process (kernel.org process/3.Early-stage):
#     1) Specify the problem   — what must be solved, who is affected, where the system falls short
#     2) Early discussion      — surface objections / alternatives BEFORE implementation
#     3) Who do you talk to    — route to the right reviewers/maintainers (the right subsystem)
#     4) When to post          — the problem + intended approach are stated well enough to act on
#     5) Get buy-in            — go / no-go approval to proceed
# Receive owns the grounding and send-back gate. Review owns only the later direction debate.
#
# DECISION: these 5 processes happen INSIDE the patch series formation — one codex-driven stage, like review's internal
# 5-reviewer + Aufheben loop — NOT as 5 separate git stages/branches/commits. Git stores ONLY the result:
# the promoted, contributor-takeable patch series (ai-org/patch-series/<id>: patch-series-cover-letter.json plus technical-approach-plan.json). Needs-work
# and rejected requests stay in the processed inbox record, not in git. Doing the 5 processes inside the patch series
# (not in git) keeps the git state from exploding.
#
# Input/output field contract:
#   entrance REQUEST (rough) requires only raw_request.
#   grounded patch series view = the research-derived field registry after correction and repository-context enrichment.
#   context was replaced by background_facts, references, and grounding_provenance. Every registry field carries
#   role/belongs/must_not/owner/required_at descriptions; must_not is the anti-dumping boundary that keeps
#   research prose out of requirement-bearing fields.
#
# Shape (to match the other stages): pull reads one inbox request -> validate the request -> codex grounds it ->
# git-write only the promoted patch series (ai-org/patch-series/<id>: patch-series-cover-letter.json), or write the send-back/reject result to the inbox.
#
# ==========================================================================================================
# CANONICAL RECEIVE FLOW - 1/2/3/4 (requester 2026-07-02, CONFIRMED). Read this before touching the precedent-store wiring.
# This block is a "Memento tattoo": the correct flow lives HERE, in the code, because ADR/notes get missed.
#   0. submit                  -> write raw request to the off-git inbox; no branch, no git artifact.
#   1. validate + ground       -> grounded patch series (what to build: problem + proposal).
#                                  Provenance discipline: grounding never deliberates the stack. It may only mark
#                                  tech_stack requester_specified when the original request names the stack, or
#                                  unspecified otherwise. Franchise/domain stack precedent is background_facts
#                                  evidence, never a stack decision rule.
#   2. engineering_precedent_store.build_from_patch_series -> POPULATE the single org-level precedent store with THIS patch series's concepts.
#                                  DESIGN facets are synchronous: the patch series WAITS because 3 consumes them.
#                                  IMPLEMENTATION facets are background: the patch series does NOT wait; this warms patch.
#   3. form_technical_approach  -> CONSUME the precedent store by lookup to propose HOW to build it
#                                  (precedent patterns + repo context + requester approach). Fidelity precedent
#                                  remains evidence only, never a stack decision rule; requester-specified stacks
#                                  are authoritative.
#   4. promote patch series (with Technical Approach) -> the first git artifact, then review.
#
# Dogfood enters through submit + pull. Do not reintroduce throwaway drivers that call intake out-of-band.
#
# produce_patch_series now executes the flow after confident grounding: it synchronously builds DESIGN precedent facets,
# starts IMPLEMENTATION precedent research in the background, forms the Technical Approach with the exact design
# terms from the build, and commits that approach as technical-approach-plan.json beside the registry-shaped patch-series-cover-letter.json. Review
# therefore receives a promoted patch series that already carries its Technical Approach.
#
# WHY DESIGN ② PRECEDES ③ PRIOR ART (do not cross that join): ③'s prior-art map READS the precedent store.
# If design ② has not populated it, ③ reads an empty well and every prior_art node degrades to
# facet_kind='none'. The earlier normalize -> constraints branch has no RAW dependency on the design build, so
# receive overlaps those fronts and joins them immediately before prior art. Implementation ② remains the one
# background build for the later Contributor/patch stage.
#
# THE 0/6 BUG THIS PREVENTS (root cause — do NOT reintroduce): ② and ③ must key off the SAME concept list.
# The old code ran neither ② here NOR shared keys: build_from_patch_series extracted terms from _patch_series_text(patch_series_view),
# while ③ re-extracted terms from a DIFFERENT string (patch series + accumulated approach tree) -> DIFFERENT terms ->
# every lookup MISSED the store -> facet_kind='none' for all 6/6 prior_art nodes, and ③ papered over it with an
# inline engineering_precedent_store.expand() band-aid. FIX (current): ② runs first and RETURNS its exact ordered term list;
# form_technical_approach threads THOSE terms into ③, which CONSUMES them by lookup (no re-extract). precedent store
# BUILD keys == precedent-store READ keys, by construction. Inline expand survives ONLY as a fallback when ② is skipped.
#
# Memento: the store answers only the terms asked. Component extraction researches what the patch series mentions;
# domain specification consumes content-aware precedent hits derived from the patch series, not a type-level template.
#
# SEARCH TIMEOUT (correctness, not speed): a single codex precedent-store search once hung 36 minutes and stalled the
# whole pipeline. reference search subprocess calls are bounded by AI_ORG_REFERENCE_SEARCH_TIMEOUT (default 180s);
# on timeout that one concept degrades to empty/failed and the build CONTINUES — a hung search can never wedge ③.
#
# COST NOTE (implemented timing split): ② is heavy, so only DESIGN research is synchronous. IMPLEMENTATION
# research is fired through engineering_precedent_store.start_background_build(..., kinds=("implementation",)) and is not awaited
# by receive; it warms the same single append-only WAL SQLite precedent store for Contributor/patch. Patch/tests can
# drain it with engineering_precedent_store.await_background_builds(), and patch still uses expand-on-miss for gaps. WAL plus short
# write transactions keep concurrent foreground reads and background writes safe. The hooks remain: pass
# reference_terms=<prebuilt> or skip_precedent_build=True to skip the internal build entirely.
# ==========================================================================================================
#
# ==========================================================================================================
# TO-BE (UNIMPLEMENTED, ratified requester 2026-07-08). receive needs two capabilities it does NOT yet have.
# We ABOLISHED ORG_BUILDER_PROFILE — a context-blind, accreted "must_not" list (prohibition-accretion) — because
# it was the wrong shape and it polluted every goal (garbage-in at the intake -> garbage-out downstream).
# Abolition was ONLY the removal. The affirmative successor is JUDGMENT, not prohibition, and it is NOT built
# yet. Do NOT "fix" this by re-adding any must_not list — that would just re-grow the same accretion.
#
#   1. CONTAMINATION-RESISTANCE. When receive would pull in outside material (archived docs, a prior design,
#      reference content, an adjacent repo), it must NOT hardcode "read" or "don't read". PREPROCESS each item:
#      first judge whether it even needs to be read; then, if reading it would pollute the core design, exclude
#      it; if it is genuinely useful, include it. Inclusion is a per-item, judged value-vs-contamination
#      decision — never a blanket rule. (Real failures this must stop: an archived Shagiri reference bleeding
#      into a search and confusing the model; a prior design's ORG_BUILDER residue being copied forward.)
#
#   2. UNCONSCIOUS-BIAS-RESISTANCE. A latent bias (e.g. "for a game, reach for Unreal Engine") must be neither a
#      silent default NOR a banned word. Surface the bias, ARTICULATE its real reasoning in words ("full-scope
#      DQ fidelity implies commercial-grade 3D -> Unreal"), place it OPENLY on the judgment table as a candidate,
#      and let reasoned judgment REJECT it out loud. The org must be able to say "I am pulled toward X, here is
#      exactly why, and here is why I reject it" — not slide into X unconsciously, and not be forbidden from ever
#      weighing X. Rejection is an argued decision on the record, not a hidden default and not a taboo.
#
#   3. HOLD-THE-UNKNOWN (negative capability). When intake cannot know something (intended scope, a
#      requester-private intent, a genuinely open tradeoff), do NOT collapse it into a false certainty —
#      a silent default or a guess (the Unreal-for-DQ reflex) — and do NOT block on it (stop-and-ask kills
#      autonomy). The third way: carry the unknown forward AS an explicit unknown and proceed —
#      provisionally, reversibly, honestly marked (never green-wash an unknown), resolved at the cheapest
#      later point, escalated only when it becomes a load-bearing fork. Proceeding under uncertainty without
#      forcing premature resolution is what makes autonomy principled instead of reckless guessing.
#
# (1) and (2) replace prohibition with explicit, rejectable JUDGMENT; (3) replaces premature resolution with
# negative capability. All three are the unbuilt maturity of intake — the affirmative successor to the abolished
# ORG_BUILDER_PROFILE. Until built, receive stays vulnerable to contamination, to silent bias, and to forced
# false-certainty, and the org's build-capability grounding is ABSENT (a user-facing app/game build can now pick
# a stack this org cannot actually build). Build the judgment and the negative capability, not another prohibition.
# ==========================================================================================================
#
# TECHNICAL APPROACH — where the design is FORMED (BUILT: form_technical_approach; grounded in Linux/patch series review):
# "propose the approach" and "review the approach" are DISTINCT. Review CRITIQUES a submitted design; it must
# NOT be the first place the approach is created (Rust patch seriess / PEPs / IETF I-Ds all post a design that review
# reshapes). So the patch series must arrive at review already carrying a Technical Approach. That approach is formed
# HERE, at receive, and it is the BOUNDARY between the requester and the AI Org:
#     Technical Approach present in the request?
#       YES -> use it as the basis (ground/refine with the precedent store + repo context; do NOT discard the
#              requester's approach). Partial -> fill only the gaps.
#       NO  -> the AI Org GENERATES it (precedent-store-driven: use the precedent store's implementation knowledge to
#              propose HOW to build it, labelled as PROPOSAL, not fact).
# GOAL: usable by a layperson (an amateur will not supply a Technical Approach -> the AI Org generates it).
# But even for an amateur, receive does NOT silently decide everything: when it generates the approach it
# also ASKS QUESTIONS BACK — surface the pivotal decisions/assumptions and return them for confirm/correct
# (this is the existing needs_confirmation "propose a guess, ask 'is this right?'" path, EXTENDED to the
# technical-approach decisions). The amateur steers by ANSWERING (intent/preferences), not by AUTHORING.
# The formed Technical Approach carries: problem/impact, precedent-derived prior-art, implementation strategy,
# alternatives-with-why-not, compatibility/migration, testing plan, scope/patch plan, open questions.
#
# TECHNICAL APPROACH — formation procedure (grounded in patch series/PEP + ADR + ATAM + senior-dev practice; BUILT as a
# derivation tree, see form_technical_approach). Codex STRUCTURES the reasoning and exposes evidence/trade-offs; it must NOT emit a one-shot
# design claim. Weighting stays judgment-heavy (final priorities / risk tolerance / architectural taste are human):
#   1. Normalize the problem: problem, affected users/systems, current inadequacy, success criteria, non-goals.
#   2. Extract constraints: hard constraints + soft preferences (repo architecture, compatibility, data/API
#      contracts, performance/security/reliability, test constraints, delivery scope).
#   3. Build a prior-art map from the PRECEDENT STORE (design + implementation facets) + repo context: 3-6 patterns,
#      each {pattern, where-seen, when-applies, tradeoffs, adopt|adapt|reject}. This is where e.g. an engine like
#      Godot lands as a CANDIDATE — put on the table and judged on merit/fit, NOT on how often it appeared.
#   4. Generate 2-3 candidate approaches: always a minimal/local one, a repo-native/precedent-aligned one, and a
#      more-general/architectural one when plausible; optionally do-nothing/defer when requirements are weak.
#   5. Evaluate candidates on a compact matrix: problem fit, repo fit, complexity, quality attributes,
#      compat/migration, testability, operability, reversibility, risk, evidence.
#   6. Select with rationale: "Choose X because ... under constraints ..., accepting tradeoff F. Reject Y/Z because."
#   7. Implementation strategy: main code changes, affected modules, data/API/config changes, migration/compat,
#      testing plan, observability/operability where relevant.
#   8. Right-size the patch plan: first safe slice, follow-up slices, explicitly deferred work + why safe to defer
#      (YAGNI, unless the deferred decision is hard to reverse or affects major quality attributes).
#   9. Surface risks: attach risk nodes to candidate, decision, or implementation parents.
#  10. Emit the Technical Approach section: chosen approach, alternatives-with-why-not, prior-art rationale,
#      trade-off analysis, implementation plan, compat/migration, testing plan, scoped patch plan, risks/open Qs.
# Question-back to the requester (needs_confirmation extended to approach decisions) is deferred — build it LAST.
#
# USER EXPERIENCE REQUIREMENTS MEMENTO: the requester may give only one line. Grounding derives the
# graphics/UI/UX contract from research, named-reference identity, genre conventions, PLAY/HEP usability
# heuristics, game-feel feedback, UI layer taxonomy, JRPG readability conventions when relevant, and Game
# Accessibility Guidelines basics. Keep altitude split: patch series records observable behavior, states, feedback
# channels, conventions, and tests; art bible owns exact palettes/sprite dimensions/typography; implementation
# owns component and file names. Anti-decorative principle: No presentation element may obscure, contradict, or
# masquerade as game state. Every consequential mechanic must have an observable signifier, every consequential
# action timely feedback, and every persistent state change persistent evidence. A real external build failed
# exactly where this spec was silent: mechanically complete state existed, but the player could not perceive it.
"""patch series receive — validate and ground an entrance request into a patch series."""
from __future__ import annotations

import concurrent.futures
from copy import deepcopy
from dataclasses import dataclass, field
import hashlib
import importlib
import inspect
import json
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
from types import MappingProxyType
from typing import Any, Mapping
import unicodedata

from ai_org.schema_lifecycle import generated_schema_attr
import ai_org.log as org_log
from ai_org.codex_reset import (
    TRANSIENT_RETRIES_EXHAUSTED,
    codex_resume_command,
    run_with_reset_retries,
    transient_retries_exhausted,
)
from ai_org import deterministic_structure_gadgets as structure_gadgets
from ai_org import contributor_handoff
from ai_org import git_wrapper
from ai_org import engineering_precedent_store
from ai_org import mailing_list
from ai_org import network_bodies
from ai_org import patch_series_bodies
from ai_org import review_bodies
from ai_org.body_codec import BodyCodecClient, CodecFailure, strict_json_loads
from ai_org.patch_author import contract as patch_author_contract
import ai_org.patchwork_queue.codex_exec as codex_exec
import ai_org.patchwork_queue.requester_assumptions as requester_assumptions
import ai_org.patchwork_queue.spine as spine
from ai_org.patchwork_queue.field_registry import (
    ENTRANCE_REQUIRED_FIELDS,
    FIELD_REGISTRY,
    OPTIONAL_FIELDS,
    WORK_ORDER_HANDOFF_REQUIRED_FIELDS,
    WORK_ORDER_VIEW_FIELDS,
    STRING_ARRAY_FIELDS,
    STRING_FIELDS,
    TECH_STACK_CONCRETE_BUILD_STRATEGIES,
    TECH_STACK_FIELDS,
    USER_EXPERIENCE_REQUIREMENTS_FIELDS,
    deliverable_is_user_facing,
    entrance_defaults,
    explain_tech_stack_violation,
    patch_series_view_schema,
    validate_tech_stack,
    validate_user_experience_requirements,
)


LOGGER = logging.getLogger(__name__)

COMMON_8_FIELDS = WORK_ORDER_VIEW_FIELDS
REQUIRED_FIELDS = ENTRANCE_REQUIRED_FIELDS
OPTIONAL_STRING_FIELDS = STRING_FIELDS
OPTIONAL_LIST_FIELDS = STRING_ARRAY_FIELDS

def build_request_schema() -> dict[str, Any]:
    return {
        "recognized_fields": list(WORK_ORDER_VIEW_FIELDS),
        "field_registry": {entry.name: entry.description for entry in FIELD_REGISTRY},
        "required": list(REQUIRED_FIELDS),
        "optional": list(OPTIONAL_FIELDS),
        "additional_properties": True,
    }

def build_grounding_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "confident",
            "proposed_patch_series",
            "ascii_working_slug",
            "assumptions",
            "questions",
            "grounding_notes",
        ],
        "properties": {
            "confident": {"type": "boolean"},
            "proposed_patch_series": patch_series_view_schema(),
            "ascii_working_slug": {
                "type": "string",
                "description": "Lowercase ASCII branch slug using only a-z, 0-9, and hyphen, from 3 to 48 characters.",
            },
            "assumptions": {"type": "array", "items": {"type": "string"}},
            "questions": {"type": "array", "items": {"type": "string"}},
            "grounding_notes": {"type": "string"},
        },
    }

def build_grounding_verdict_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": ["faithful_specific", "full_scope", "non_legal", "latest_default", "reasons"],
        "properties": {
            "faithful_specific": {"type": "boolean"},
            "full_scope": {"type": "boolean"},
            "non_legal": {"type": "boolean"},
            "latest_default": {"type": "boolean"},
            "reasons": {"type": "array", "items": {"type": "string"}},
        },
    }

NORMALIZED_PROBLEM_FIELDS = (
    "problem",
    "affected",
    "current_inadequacy",
    "success_criteria",
    "non_goals",
    "open_questions",
)
PRODUCER_DETERMINATION_FIELD = "requires_deliverable"
REJECTED_PRODUCER_DETERMINATION_ALIAS = "deliverable_required"
# One receive-owned authority makes field drift across the preparation stages
# mechanically observable.  The values intentionally repeat the canonical
# field: each key names a distinct consumer whose contract must move in lockstep.
PRODUCER_PREPARATION_CONSUMER_FIELDS: Mapping[str, str] = MappingProxyType(
    {
        "schema": PRODUCER_DETERMINATION_FIELD,
        "prompt": PRODUCER_DETERMINATION_FIELD,
        "parser": PRODUCER_DETERMINATION_FIELD,
        "lint": PRODUCER_DETERMINATION_FIELD,
        "reconstruction": PRODUCER_DETERMINATION_FIELD,
        "identity_continuity": PRODUCER_DETERMINATION_FIELD,
        "successor_lineage": PRODUCER_DETERMINATION_FIELD,
        "assignment_generation": PRODUCER_DETERMINATION_FIELD,
    }
)
PRODUCER_PREPARED_ROOT_SHAPE: Mapping[str, str] = MappingProxyType(
    {
        "goal_determination_field": PRODUCER_DETERMINATION_FIELD,
        "requirement_field": "deliverable_requirements",
        "obligation_field": "production_obligations",
        "assignment_field": "production_obligation_ids",
    }
)
SUCCESS_CRITERION_FIELDS = (
    "actor",
    "capability",
    "verifiable_outcome",
    "verification",
    "ux_trace",
    PRODUCER_DETERMINATION_FIELD,
)
PRODUCER_AWARE_GOAL_FIELDS = (
    "id",
    *SUCCESS_CRITERION_FIELDS,
)
DELIVERABLE_REQUIREMENT_FIELDS = (
    "id",
    "referee_goal_id",
    "production_obligation_id",
    "deliverable",
)
OBLIGATION_REPLACEMENT_LINK_FIELDS = (
    "replaces_obligation_id",
    "replacement_reason",
)
PRODUCTION_OBLIGATION_FIELDS = (
    "id",
    "referee_goal_id",
    "deliverable_requirement_id",
    "deliverable",
    "eligibility_predicate",
    "replacement_links",
)
ROOT_PATCH_PLAN_ASSIGNMENT_FIELDS = (
    "item_id",
    "production_obligation_ids",
)
CANONICAL_ROOT_PREVIEW_FIELDS = (
    "goals",
    "deliverable_requirements",
    "production_obligations",
    "patch_plan",
)
PREVIEW_PRODUCER_AWARE_GOAL_FIELDS = (
    "id",
    PRODUCER_DETERMINATION_FIELD,
    "actor",
    "capability_action",
    "capability_preconditions",
    "outcome_expected_state",
    "outcome_evidence",
    "verification_method",
    "verification_check",
    "ux_trace",
)
PREVIEW_PRODUCTION_OBLIGATION_FIELDS = (
    "id",
    "referee_goal_id",
    "deliverable_requirement_id",
    "deliverable",
    "eligibility_predicate",
    "replaces_obligation_id",
    "replacement_reason",
)
SUCCESS_CRITERION_CAPABILITY_FIELDS = ("action", "preconditions")
SUCCESS_CRITERION_OUTCOME_FIELDS = ("expected_state", "evidence")
SUCCESS_CRITERION_VERIFICATION_FIELDS = ("method", "check")
SUCCESS_CRITERION_VERIFICATION_METHODS = ("automated_test", "manual_check", "metric")
MAX_NORMALIZE_PROBLEM_REGENERATIONS = 2
MAX_TECHNICAL_APPROACH_NODE_REGENERATIONS = 2
MAX_SCHEMA_ENUM_REFERENCES = 120

def build_success_criterion_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(SUCCESS_CRITERION_FIELDS),
        "properties": {
            "actor": {"type": "string"},
            "capability": {
                "type": "object",
                "additionalProperties": False,
                "required": list(SUCCESS_CRITERION_CAPABILITY_FIELDS),
                "properties": {
                    "action": {"type": "string"},
                    "preconditions": {"type": "array", "items": {"type": "string"}},
                },
            },
            "verifiable_outcome": {
                "type": "object",
                "additionalProperties": False,
                "required": list(SUCCESS_CRITERION_OUTCOME_FIELDS),
                "properties": {
                    "expected_state": {"type": "string"},
                    "evidence": {"type": "string"},
                },
            },
            "verification": {
                "type": "object",
                "additionalProperties": False,
                "required": list(SUCCESS_CRITERION_VERIFICATION_FIELDS),
                "properties": {
                    "method": {"type": "string", "enum": list(SUCCESS_CRITERION_VERIFICATION_METHODS)},
                    "check": {"type": "string"},
                },
            },
            "ux_trace": {
                "type": "string",
                "description": (
                    "Dotted path rooted at user_experience_requirements for the UX section this criterion "
                    "traces to, or exactly none when the criterion is not UX-facing."
                ),
            },
            PRODUCER_DETERMINATION_FIELD: {
                "type": "boolean",
                "description": (
                    "Producer Eligibility Predicate: true exactly when satisfying this Canonical Referee "
                    "Criterion requires a produced deliverable."
                ),
            },
        },
    }


def build_producer_aware_goal_schema() -> dict[str, Any]:
    """Purpose-built, depth-bounded goal carrier for v2 prepublication.

    The live formation schema intentionally remains unchanged until the final
    coordinated cutover. Nested referee fields are flattened only at this
    transient model-output boundary so the schema remains within the
    structured-output safe subset; the parser restores the canonical shape
    before deterministic validation and CUE rendering.
    """

    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(PREVIEW_PRODUCER_AWARE_GOAL_FIELDS),
        "properties": {
            "id": {
                "type": "string",
                "description": "Stable referee-goal identity supplied by canonical root preparation.",
            },
            PRODUCER_DETERMINATION_FIELD: {
                "type": "boolean",
                "description": "Explicitly true when this referee criterion requires a produced deliverable.",
            },
            "actor": {"type": "string"},
            "capability_action": {"type": "string"},
            "capability_preconditions": {
                "type": "array",
                "items": {"type": "string"},
            },
            "outcome_expected_state": {"type": "string"},
            "outcome_evidence": {"type": "string"},
            "verification_method": {
                "type": "string",
                "enum": list(SUCCESS_CRITERION_VERIFICATION_METHODS),
            },
            "verification_check": {"type": "string"},
            "ux_trace": {"type": "string"},
        },
    }


def build_deliverable_requirement_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(DELIVERABLE_REQUIREMENT_FIELDS),
        "properties": {
            "id": {
                "type": "string",
                "description": "Stable identity of this deliverable requirement.",
            },
            "referee_goal_id": {
                "type": "string",
                "description": "Identity of the one referee goal requiring this deliverable.",
            },
            "production_obligation_id": {
                "type": "string",
                "description": "Identity of the one cross-linked production obligation.",
            },
            "deliverable": {
                "type": "string",
                "description": "Canonical deliverable text shared with the production obligation.",
            },
        },
    }


def build_obligation_replacement_link_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(OBLIGATION_REPLACEMENT_LINK_FIELDS),
        "properties": {
            "replaces_obligation_id": {
                "type": "string",
                "description": "Exact predecessor obligation identity replaced by this successor.",
            },
            "replacement_reason": {
                "type": "string",
                "description": "Non-empty factual reason why the predecessor deliverable changed.",
            },
        },
    }


def build_production_obligation_schema() -> dict[str, Any]:
    """Return the flat safe-subset carrier restored by the preview parser."""

    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(PREVIEW_PRODUCTION_OBLIGATION_FIELDS),
        "properties": {
            "id": {
                "type": "string",
                "description": "Stable producer-obligation identity.",
            },
            "referee_goal_id": {
                "type": "string",
                "description": "Identity of the paired referee goal.",
            },
            "deliverable_requirement_id": {
                "type": "string",
                "description": "Identity of the paired deliverable requirement.",
            },
            "deliverable": {
                "type": "string",
                "description": "Canonical deliverable text shared with the requirement.",
            },
            "eligibility_predicate": {
                "type": "string",
                "description": "Predicate a prospective producer can evaluate for itself.",
            },
            "replaces_obligation_id": {
                "type": "string",
                "description": "Empty for an initial obligation; otherwise the exact predecessor identity.",
            },
            "replacement_reason": {
                "type": "string",
                "description": "Empty for an initial obligation; otherwise the factual replacement reason.",
            },
        },
    }


def build_root_patch_plan_assignment_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(ROOT_PATCH_PLAN_ASSIGNMENT_FIELDS),
        "properties": {
            "item_id": {
                "type": "string",
                "description": "Stable identity of one executable root patch-plan item.",
            },
            "production_obligation_ids": {
                "type": "array",
                "description": "The exact production obligations assigned to this item.",
                "items": {"type": "string"},
            },
        },
    }


def build_canonical_root_preview_schema() -> dict[str, Any]:
    """Return the small structured-output carrier used to prepare a preview.

    Cross-field cardinality and lineage are deliberately enforced by the
    deterministic parser and CUE body contract, because Codex's supported JSON
    Schema subset cannot express those conditionals safely.
    """

    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": list(CANONICAL_ROOT_PREVIEW_FIELDS),
        "properties": {
            "goals": {
                "type": "array",
                "description": "Canonical referee goals with explicit deliverable determinations.",
                "items": build_producer_aware_goal_schema(),
            },
            "deliverable_requirements": {
                "type": "array",
                "description": "One requirement for each goal whose determination is true.",
                "items": build_deliverable_requirement_schema(),
            },
            "production_obligations": {
                "type": "array",
                "description": "One producer obligation cross-linked to each deliverable requirement.",
                "items": build_production_obligation_schema(),
            },
            "patch_plan": {
                "type": "array",
                "description": "Executable root items whose obligation arrays form an exact partition.",
                "items": build_root_patch_plan_assignment_schema(),
            },
        },
    }

def build_normalize_problem_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": list(NORMALIZED_PROBLEM_FIELDS),
        "properties": {
            "problem": {"type": "string"},
            "affected": {"type": "string"},
            "current_inadequacy": {"type": "string"},
            "success_criteria": {"type": "array", "items": build_success_criterion_schema()},
            "non_goals": {"type": "array", "items": {"type": "string"}},
            "open_questions": {"type": "array", "items": {"type": "string"}},
        },
    }

CONSTRAINT_DERIVATION_VALUES = ("problem", "success_criteria", "non_goals", "repo", "domain")
CONSTRAINT_DERIVATION_FIELDS = ("from", "trace")
CONSTRAINT_IMPLICATION_FIELDS = ("must", "must_not")
CONSTRAINT_ITEM_FIELDS = ("statement", "derivation", "implication")
PREFERENCE_ITEM_FIELDS = ("statement", "derivation", "rationale")
EXTRACT_CONSTRAINTS_FIELDS = ("hard_constraints", "soft_preferences")
MAX_EXTRACT_CONSTRAINTS_REGENERATIONS = 2

def build_extract_constraints_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": list(EXTRACT_CONSTRAINTS_FIELDS),
        "properties": {
            "hard_constraints": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": list(CONSTRAINT_ITEM_FIELDS),
                    "properties": {
                        "statement": {"type": "string"},
                        "derivation": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": list(CONSTRAINT_DERIVATION_FIELDS),
                            "properties": {
                                "from": {"type": "string", "enum": list(CONSTRAINT_DERIVATION_VALUES)},
                                "trace": {"type": "string"},
                            },
                        },
                        "implication": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": list(CONSTRAINT_IMPLICATION_FIELDS),
                            "properties": {
                                "must": {"type": "string"},
                                "must_not": {"type": "string"},
                            },
                        },
                    },
                },
            },
            "soft_preferences": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": list(PREFERENCE_ITEM_FIELDS),
                    "properties": {
                        "statement": {"type": "string"},
                        "derivation": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": list(CONSTRAINT_DERIVATION_FIELDS),
                            "properties": {
                                "from": {"type": "string", "enum": list(CONSTRAINT_DERIVATION_VALUES)},
                                "trace": {"type": "string"},
                            },
                        },
                        "rationale": {"type": "string"},
                    },
                },
            },
        },
    }

PRIOR_ART_PATTERN_FIELDS = (
    "name",
    "source",
    "when_applies",
    "tradeoffs",
    "disposition",
    "traces_to",
)
PRIOR_ART_SOURCE_FIELDS = (
    "reference_concept",
    "facet_kind",
    "where",
)
PRIOR_ART_FACET_KINDS = ("design", "implementation", "none")
PRIOR_ART_TRADEOFF_FIELDS = (
    "pros",
    "cons",
)
PRIOR_ART_DISPOSITION_FIELDS = (
    "choice",
    "why",
)
PRIOR_ART_DISPOSITIONS = ("adopt", "adapt", "reject")
PRIOR_ART_MAP_FIELDS = ("patterns",)
MAX_PRIOR_ART_REGENERATIONS = 2
MAX_PRIOR_ART_REFERENCE_EXPANSIONS = 8

def build_prior_art_map_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": list(PRIOR_ART_MAP_FIELDS),
        "properties": {
            "patterns": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": list(PRIOR_ART_PATTERN_FIELDS),
                    "properties": {
                        "name": {"type": "string"},
                        "source": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": list(PRIOR_ART_SOURCE_FIELDS),
                            "properties": {
                                "reference_concept": {"type": "string"},
                                "facet_kind": {"type": "string", "enum": list(PRIOR_ART_FACET_KINDS)},
                                "where": {"type": "string"},
                            },
                        },
                        "when_applies": {"type": "string"},
                        "tradeoffs": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": list(PRIOR_ART_TRADEOFF_FIELDS),
                            "properties": {
                                "pros": {"type": "array", "items": {"type": "string"}},
                                "cons": {"type": "array", "items": {"type": "string"}},
                            },
                        },
                        "disposition": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": list(PRIOR_ART_DISPOSITION_FIELDS),
                            "properties": {
                                "choice": {"type": "string", "enum": list(PRIOR_ART_DISPOSITIONS)},
                                "why": {"type": "string"},
                            },
                        },
                        "traces_to": {"type": "array", "items": {"type": "string"}},
                    },
                },
            },
        },
    }

CANDIDATE_APPROACH_KINDS = (
    "minimal_local",
    "repo_native",
    "general_architectural",
    "do_nothing_defer",
)
CANDIDATE_APPROACH_FIELDS = (
    "id",
    "name",
    "kind",
    "summary",
    "stack_requirement",
    "first_proof_moment",
    "core_systems",
    "draws_on",
)
CANDIDATE_STACK_REQUIREMENT_FIELDS = (
    "build_strategy",
    "engine",
    "framework",
    "language",
    "platform",
    "authoring_model",
    "verification_model",
)
CANDIDATE_AUTHORING_MODELS = ("text_first", "gui_editor", "binary_assets")
CANDIDATE_VERIFICATION_MODELS = ("headless_ci", "gui_required")
CANDIDATE_FIRST_PROOF_FIELDS = (
    "user_actions",
    "named_content",
    "win_or_progress_condition",
)
NAMED_CONTENT_ITEM_FIELDS = (
    "name",
    "kind",
)
def build_named_content_schema() -> dict[str, Any]:
    return {
        "type": "array",
        "items": {
            "type": "object",
            "additionalProperties": False,
            "required": list(NAMED_CONTENT_ITEM_FIELDS),
            "properties": {
                "name": {"type": "string", "description": "Concrete named item, module, dataset, entity, screen, file, or other content in the deliverable."},
                "kind": {"type": "string", "description": "Open deliverable-specific category chosen by the model, such as location, enemy, module, dataset, workflow, or API."},
            },
        },
    }
def build_candidate_approach_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": list(CANDIDATE_APPROACH_FIELDS),
        "properties": {
            "id": {"type": "string"},
            "name": {"type": "string"},
            "kind": {"type": "string", "enum": list(CANDIDATE_APPROACH_KINDS)},
            "summary": {"type": "string"},
            "stack_requirement": {
                "type": "object",
                "description": "Structured stack this candidate actually requires. Prose comparisons are not requirements.",
                "additionalProperties": False,
                "required": list(CANDIDATE_STACK_REQUIREMENT_FIELDS),
                "properties": {
                    "build_strategy": {
                        "type": "string",
                        "enum": list(TECH_STACK_CONCRETE_BUILD_STRATEGIES),
                        "description": "engine_based, framework_based, or from_scratch for this candidate.",
                    },
                    "engine": {
                        "type": "string",
                        "description": "Required existing engine product, empty unless build_strategy is engine_based.",
                    },
                    "framework": {
                        "type": "string",
                        "description": "Required existing framework product, empty unless build_strategy is framework_based.",
                    },
                    "language": {"type": "string", "description": "Primary authoring language."},
                    "platform": {
                        "type": "string",
                        "description": "User-facing runtime target, such as browser or lightweight desktop.",
                    },
                    "authoring_model": {
                        "type": "string",
                        "enum": list(CANDIDATE_AUTHORING_MODELS),
                        "description": "text_first when Codex can author source text; gui_editor or binary_assets when required.",
                    },
                    "verification_model": {
                        "type": "string",
                        "enum": list(CANDIDATE_VERIFICATION_MODELS),
                        "description": "headless_ci when the first verification path is command-driven; gui_required otherwise.",
                    },
                },
            },
            "first_proof_moment": {
                "type": "object",
                "additionalProperties": False,
                "required": list(CANDIDATE_FIRST_PROOF_FIELDS),
                "properties": {
                    "user_actions": {"type": "array", "items": {"type": "string"}},
                    "named_content": build_named_content_schema(),
                    "win_or_progress_condition": {"type": "string"},
                },
            },
            "core_systems": {"type": "array", "items": {"type": "string"}},
            "draws_on": {"type": "array", "items": {"type": "string"}},
        },
    }

def build_generate_candidates_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": ["candidates"],
        "properties": {
            "candidates": {
                "type": "array",
                "items": build_candidate_approach_schema(),
            },
        },
    }

CANDIDATE_EVALUATION_SCORE_FIELDS = (
    "problem_fit",
    "repo_fit",
    "complexity",
    "quality_attributes",
    "compat_migration",
    "testability",
    "operability",
    "reversibility",
    "risk",
)
EVALUATION_SCORE_FIELDS = ("rating", "reason")
CANDIDATE_EVALUATION_FIELDS = (
    "candidate_id",
    "scores",
)
def build_evaluate_candidate_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": list(CANDIDATE_EVALUATION_FIELDS),
        "properties": {
            "candidate_id": {"type": "string"},
            "scores": {
                "type": "object",
                "additionalProperties": False,
                "required": list(CANDIDATE_EVALUATION_SCORE_FIELDS),
                "properties": {
                    field: {
                        "type": "object",
                        "additionalProperties": False,
                        "required": list(EVALUATION_SCORE_FIELDS),
                        "properties": {
                            "rating": {"type": "string"},
                            "reason": {"type": "string"},
                        },
                    }
                    for field in CANDIDATE_EVALUATION_SCORE_FIELDS
                },
            },
        },
    }
def build_evaluate_candidates_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": ["evaluations"],
        "properties": {
            "evaluations": {"type": "array", "items": build_evaluate_candidate_schema()},
        },
    }
TOULMIN_ARGUMENT_ROLES = ("support", "objection")
TOULMIN_ARGUMENT_FIELDS = (
    "role",
    "about_candidate_id",
    "claim",
    "grounds",
    "warrant",
    "backing",
    "rebuttal",
)
DECISION_RATIONALE_FIELDS = (
    "because",
    "under_constraints",
    "accepting_tradeoffs",
)
STACK_DECISION_AXIS_FIELDS = (
    "fidelity_precedent",
)
STACK_DECISION_AXIS_SLOT_FIELDS = ("evidence", "judgment")
REJECTED_APPROACH_FIELDS = (
    "candidate_id",
    "objection",
)
SELECT_APPROACH_FIELDS = (
    "selected_candidate_id",
    "arguments",
    "rationale",
    "stack_axes",
    "rejected",
    "open_questions",
)

def build_select_approach_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": list(SELECT_APPROACH_FIELDS),
        "properties": {
            "selected_candidate_id": {"type": "string"},
            "arguments": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": list(TOULMIN_ARGUMENT_FIELDS),
                    "properties": {
                        "role": {"type": "string", "enum": list(TOULMIN_ARGUMENT_ROLES)},
                        "about_candidate_id": {"type": "string"},
                        "claim": {"type": "string"},
                        "grounds": {"type": "string"},
                        "warrant": {"type": "string"},
                        "backing": {"type": "string"},
                        "rebuttal": {"type": "string"},
                    },
                },
            },
            "rationale": {
                "type": "object",
                "additionalProperties": False,
                "required": list(DECISION_RATIONALE_FIELDS),
                "properties": {
                    "because": {"type": "array", "items": {"type": "string"}},
                    "under_constraints": {"type": "array", "items": {"type": "string"}},
                    "accepting_tradeoffs": {"type": "array", "items": {"type": "string"}},
                },
            },
            "stack_axes": {
                "type": "object",
                "additionalProperties": False,
                "required": list(STACK_DECISION_AXIS_FIELDS),
                "properties": {
                    axis: {
                        "type": "object",
                        "additionalProperties": False,
                        "required": list(STACK_DECISION_AXIS_SLOT_FIELDS),
                        "properties": {
                            "evidence": {"type": "string"},
                            "judgment": {"type": "string"},
                        },
                    }
                    for axis in STACK_DECISION_AXIS_FIELDS
                },
            },
            "rejected": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": list(REJECTED_APPROACH_FIELDS),
                    "properties": {
                        "candidate_id": {"type": "string"},
                        "objection": {"type": "string"},
                    },
                },
            },
            "open_questions": {"type": "array", "items": {"type": "string"}},
        },
    }
IMPLEMENTATION_STRATEGY_FIELDS = (
    "systems",
    "persistence",
)
IMPLEMENTATION_SYSTEM_FIELDS = (
    "system_name",
    "observable_behavior",
    "observable_effect",
    "named_content",
    "key_modules",
)
IMPLEMENTATION_PERSISTENCE_FIELDS = ("saved_fields",)

def build_implementation_strategy_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": list(IMPLEMENTATION_STRATEGY_FIELDS),
        "properties": {
            "systems": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": list(IMPLEMENTATION_SYSTEM_FIELDS),
                    "properties": {
                        "system_name": {"type": "string"},
                        "observable_behavior": {"type": "string"},
                        "named_content": build_named_content_schema(),
                        "key_modules": {"type": "array", "items": {"type": "string"}},
                        "observable_effect": {"type": "string"},
                    },
                },
            },
            "persistence": {
                "type": "object",
                "additionalProperties": False,
                "required": list(IMPLEMENTATION_PERSISTENCE_FIELDS),
                "properties": {
                    "saved_fields": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
    }
DOMAIN_SPECIFICATION_FIELDS = ("aspects",)
DOMAIN_ASPECT_FIELDS = (
    "aspect_name",
    "applicability",
    "specification_body",
    "quantities",
    "tables",
    "sources",
)
DOMAIN_APPLICABILITY_VALUES = ("applies", "not_applicable")
DOMAIN_QUANTITY_FIELDS = ("name", "value", "unit")
DOMAIN_TABLE_FIELDS = ("table_name", "columns", "rows")
DOMAIN_SPEC_TABLE_EXTERNALIZE_ROW_THRESHOLD = 12

def build_domain_specification_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": list(DOMAIN_SPECIFICATION_FIELDS),
        "properties": {
            "aspects": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": list(DOMAIN_ASPECT_FIELDS),
                    "properties": {
                        "aspect_name": {"type": "string"},
                        "applicability": {
                            "type": "string",
                            "enum": list(DOMAIN_APPLICABILITY_VALUES),
                            "description": "applies means the patch series must specify this aspect; not_applicable means specification_body is the reason it does not apply.",
                        },
                        "specification_body": {"type": "string"},
                        "quantities": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": list(DOMAIN_QUANTITY_FIELDS),
                                "properties": {
                                    "name": {"type": "string"},
                                    "value": {"type": "string"},
                                    "unit": {"type": "string"},
                                },
                            },
                        },
                        "tables": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": list(DOMAIN_TABLE_FIELDS),
                                "properties": {
                                    "table_name": {"type": "string"},
                                    "columns": {"type": "array", "items": {"type": "string"}},
                                    "rows": {
                                        "type": "array",
                                        "items": {"type": "array", "items": {"type": "string"}},
                                    },
                                },
                            },
                        },
                        "sources": {"type": "array", "items": {"type": "string"}},
                    },
                },
            },
        },
    }
PATCH_PLAN_DEFERRED_FIELDS = (
    "item",
    "why_safe_to_defer",
)
# Historical parser-only shape. New authoring never advertises or emits this
# positional skeleton, but already-published roots remain readable.
PATCH_PLAN_FOLLOW_UP_FIELDS = (
    "adds",
    "named_content",
)
PATCH_PLAN_FIRST_PROOF_FIELDS = (
    "user_can",
    "named_content",
    "presentation_baseline",
    "win_or_progress_condition",
    "how_verified",
)
PATCH_PLAN_WORK_ITEM_FIELDS = (
    "id",
    "objective",
    "named_content",
    "acceptance_criteria",
    "how_verified",
    "depends_on",
)
RIGHT_SIZE_PATCH_PLAN_FIELDS = (
    "items",
    "deferred",
    "open_questions",
)

def build_right_size_patch_plan_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": list(RIGHT_SIZE_PATCH_PLAN_FIELDS),
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": list(PATCH_PLAN_WORK_ITEM_FIELDS),
                    "properties": {
                        "id": {
                            "type": "string",
                            "description": "Stable work-item identity; keep it unchanged across revisions.",
                        },
                        "objective": {"type": "string"},
                        "named_content": build_named_content_schema(),
                        "acceptance_criteria": {"type": "array", "items": {"type": "string"}},
                        "how_verified": {"type": "string"},
                        "depends_on": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Stable ids of work items whose runtime output this item builds on. "
                                "Leave empty for truly independent work; never add ordering-only references."
                            ),
                        },
                    },
                },
            },
            "deferred": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": list(PATCH_PLAN_DEFERRED_FIELDS),
                    "properties": {
                        "item": {"type": "string"},
                        "why_safe_to_defer": {"type": "string"},
                    },
                },
            },
            "open_questions": {"type": "array", "items": {"type": "string"}},
        },
    }
SURFACED_RISK_FIELDS = (
    "id",
    "risk",
    "mitigation",
    "attaches_to",
    "target_id",
)
RISK_ATTACHES_TO = ("candidate", "decision", "implementation", "patch_plan")
SURFACE_RISKS_FIELDS = ("risks",)

def build_surface_risks_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": list(SURFACE_RISKS_FIELDS),
        "properties": {
            "risks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": list(SURFACED_RISK_FIELDS),
                    "properties": {
                        "id": {"type": "string"},
                        "risk": {"type": "string"},
                        "mitigation": {"type": "string"},
                        "attaches_to": {"type": "string", "enum": list(RISK_ATTACHES_TO)},
                        "target_id": {"type": "string"},
                    },
                },
            },
        },
    }


_SCHEMA_BUILDERS = {
    "CANONICAL_ROOT_PREVIEW_SCHEMA": build_canonical_root_preview_schema,
    "REQUEST_SCHEMA": build_request_schema,
    "CANDIDATE_APPROACH_SCHEMA": build_candidate_approach_schema,
    "DOMAIN_SPECIFICATION_SCHEMA": build_domain_specification_schema,
    "EVALUATE_CANDIDATES_SCHEMA": build_evaluate_candidates_schema,
    "EVALUATE_CANDIDATE_SCHEMA": build_evaluate_candidate_schema,
    "EXTRACT_CONSTRAINTS_SCHEMA": build_extract_constraints_schema,
    "GENERATE_CANDIDATES_SCHEMA": build_generate_candidates_schema,
    "GROUNDING_SCHEMA": build_grounding_schema,
    "GROUNDING_VERDICT_SCHEMA": build_grounding_verdict_schema,
    "IMPLEMENTATION_STRATEGY_SCHEMA": build_implementation_strategy_schema,
    "NAMED_CONTENT_SCHEMA": build_named_content_schema,
    "NORMALIZE_PROBLEM_SCHEMA": build_normalize_problem_schema,
    "PRIOR_ART_MAP_SCHEMA": build_prior_art_map_schema,
    "RIGHT_SIZE_PATCH_PLAN_SCHEMA": build_right_size_patch_plan_schema,
    "SELECT_APPROACH_SCHEMA": build_select_approach_schema,
    "SUCCESS_CRITERION_SCHEMA": build_success_criterion_schema,
    "SURFACE_RISKS_SCHEMA": build_surface_risks_schema,
}
_CODEX_OUTPUT_SCHEMA_BUILDERS = {
    "CANONICAL_ROOT_PREVIEW_SCHEMA": build_canonical_root_preview_schema,
    "CANDIDATE_APPROACH_SCHEMA": build_candidate_approach_schema,
    "DOMAIN_SPECIFICATION_SCHEMA": build_domain_specification_schema,
    "EVALUATE_CANDIDATES_SCHEMA": build_evaluate_candidates_schema,
    "EVALUATE_CANDIDATE_SCHEMA": build_evaluate_candidate_schema,
    "EXTRACT_CONSTRAINTS_SCHEMA": build_extract_constraints_schema,
    "GENERATE_CANDIDATES_SCHEMA": build_generate_candidates_schema,
    "GROUNDING_SCHEMA": build_grounding_schema,
    "GROUNDING_VERDICT_SCHEMA": build_grounding_verdict_schema,
    "IMPLEMENTATION_STRATEGY_SCHEMA": build_implementation_strategy_schema,
    "NAMED_CONTENT_SCHEMA": build_named_content_schema,
    "NORMALIZE_PROBLEM_SCHEMA": build_normalize_problem_schema,
    "PRIOR_ART_MAP_SCHEMA": build_prior_art_map_schema,
    "RIGHT_SIZE_PATCH_PLAN_SCHEMA": build_right_size_patch_plan_schema,
    "SELECT_APPROACH_SCHEMA": build_select_approach_schema,
    "SUCCESS_CRITERION_SCHEMA": build_success_criterion_schema,
    "SURFACE_RISKS_SCHEMA": build_surface_risks_schema,
}


def __getattr__(name: str) -> Any:
    return generated_schema_attr(name, _SCHEMA_BUILDERS)


class CanonicalRootPreviewError(ValueError):
    """Field-specific failure from the non-publishing producer-pair vet."""

    __slots__ = (
        "rule_id",
        "field",
        "json_pointer",
        "cue_path",
        "goal_id",
        "obligation_id",
        "detail",
    )

    def __init__(
        self,
        rule_id: str,
        field: str,
        json_pointer: str,
        *,
        goal_id: str = "",
        obligation_id: str = "",
        detail: str = "",
    ) -> None:
        self.rule_id = rule_id
        self.field = field
        self.json_pointer = json_pointer
        self.cue_path = _json_pointer_to_cue_path(json_pointer)
        self.goal_id = goal_id
        self.obligation_id = obligation_id
        self.detail = detail
        coordinates = [f"field={field}", f"rule={rule_id}", f"path={json_pointer}"]
        if goal_id:
            coordinates.append(f"goal={goal_id}")
        if obligation_id:
            coordinates.append(f"obligation={obligation_id}")
        message = "canonical root preview rejected (" + ", ".join(coordinates) + ")"
        if detail:
            message += f": {detail}"
        super().__init__(message)

    def as_dict(self) -> dict[str, str]:
        return {
            "rule_id": self.rule_id,
            "field": self.field,
            "json_pointer": self.json_pointer,
            "cue_path": self.cue_path,
            "goal_id": self.goal_id,
            "obligation_id": self.obligation_id,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class CanonicalRootTechnicalApproachPreview:
    """Canonical bytes and metadata produced without invoking any Git writer."""

    technical_approach: Mapping[str, Any]
    canonical_cue: str
    body_sha256: str
    cohort_files: Mapping[str, str]
    # These are properties of this receive-owned type, not caller-selectable
    # state.  In particular, a preparation caller must not be able to turn an
    # in-memory preview into an authorable or published result by supplying
    # different constructor metadata.
    lifecycle_status: str = field(default="preview_only", init=False)
    authorable: bool = field(default=False, init=False)

    @property
    def body_digest(self) -> str:
        return self.body_sha256

    @property
    def preview_artifact(self) -> str:
        """Return the dormant artifact identity without making it authorable."""

        return patch_series_bodies.ROOT_APPROACH_PATH

    @property
    def complete_cohort(self) -> bool:
        """Whether preparation produced exactly Patch-Series Root Cohort v2."""

        return tuple(self.cohort_files) == patch_series_bodies.ROOT_COHORT_V2_PATHS

    @property
    def preparation_publishes(self) -> bool:
        """Preparation is an in-memory receive transition, never a Git writer."""

        return False

    @property
    def consumer_fields(self) -> Mapping[str, str]:
        """Return the closed consumer-to-determination-field contract."""

        return PRODUCER_PREPARATION_CONSUMER_FIELDS

    @property
    def prepared_root_shape(self) -> Mapping[str, str]:
        """Return the already-final producer-aware root member names."""

        return PRODUCER_PREPARED_ROOT_SHAPE

    @property
    def historical_writer_path(self) -> str:
        """Name the durable writer that preparation itself leaves untouched."""

        return patch_series_bodies.LEGACY_ROOT_APPROACH_PATH


def normalize_deliverable(value: Any) -> str:
    """Return the sole v1 deliverable normalization used for identity.

    The type check is intentional: a future typed deliverable contract must
    select a new identity domain rather than silently equating (for example)
    the integer ``1`` with the string ``"1"``.
    """

    if not isinstance(value, str):
        raise TypeError("deliverable must be a string in root preview v1")
    normalized = re.sub(r"\s+", " ", unicodedata.normalize("NFC", value).strip())
    if not normalized:
        raise ValueError("deliverable must be non-empty after normalization")
    return normalized


def production_obligation_identity(referee_goal_id: str, deliverable: Any) -> str:
    """Apply the type-sensitive Obligation Identity Projection."""

    if not isinstance(referee_goal_id, str) or not referee_goal_id.strip():
        raise ValueError("referee_goal_id must be a non-empty string")
    normalized = normalize_deliverable(deliverable)
    identity = {
        "deliverable_type": "string",
        "deliverable": normalized,
        "referee_goal_id": referee_goal_id,
    }
    from ai_org.body_codec import strict_json_dumps

    return hashlib.sha256(strict_json_dumps(identity)).hexdigest()


def parse_canonical_root_preview(
    raw: str | bytes | bytearray | Mapping[str, Any],
    *,
    base_candidate: Mapping[str, Any] | None = None,
    previous: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Parse and vet the purpose-built carrier, optionally merging a full plan.

    ``base_candidate`` is the supplied new-plan or reform tree whose producer
    overlay is being proposed. Keeping that tree separate from the small
    Codex-safe carrier avoids advertising the unsupported full-root schema.
    """

    if isinstance(raw, Mapping):
        value: Any = dict(raw)
    else:
        try:
            value = strict_json_loads(raw)
        except (TypeError, ValueError) as exc:
            raise CanonicalRootPreviewError(
                "preview-json",
                "preview",
                "",
                detail="preview carrier is not valid strict UTF-8 JSON",
            ) from exc
    if not isinstance(value, Mapping):
        raise CanonicalRootPreviewError(
            "preview-object", "preview", "", detail="preview carrier must be an object"
        )
    if set(value) != set(CANONICAL_ROOT_PREVIEW_FIELDS):
        _raise_preview_fields(
            value,
            CANONICAL_ROOT_PREVIEW_FIELDS,
            "",
            rule_id="preview-fields",
        )
    overlay = _expand_canonical_root_preview_carrier(value)
    if base_candidate is None:
        candidate = overlay
    else:
        if not isinstance(base_candidate, Mapping):
            raise CanonicalRootPreviewError(
                "base-root-object",
                "base_candidate",
                "",
                detail="base candidate must be an object",
            )
        candidate = deepcopy(dict(base_candidate))
        problem, _ = _producer_problem(candidate)
        problem.update(overlay)
    return validate_canonical_root_technical_approach(candidate, previous=previous)


def _expand_canonical_root_preview_carrier(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    """Restore the canonical nested records from the safe-subset carrier."""

    raw_goals = value.get("goals")
    if not isinstance(raw_goals, list):
        _raise_preview("producer-root-list", "goals", "/goals")
    goals: list[dict[str, Any]] = []
    for index, raw_goal in enumerate(raw_goals):
        pointer = f"/goals/{index}"
        if not isinstance(raw_goal, Mapping):
            _raise_preview("goal-object", "goals", pointer)
        if REJECTED_PRODUCER_DETERMINATION_ALIAS in raw_goal:
            _raise_preview(
                "requires-deliverable-alias",
                REJECTED_PRODUCER_DETERMINATION_ALIAS,
                f"{pointer}/{REJECTED_PRODUCER_DETERMINATION_ALIAS}",
                goal_id=str(raw_goal.get("id") or ""),
                detail=f"use only {PRODUCER_DETERMINATION_FIELD}",
            )
        if PRODUCER_DETERMINATION_FIELD not in raw_goal:
            _raise_preview(
                "requires-deliverable-required",
                PRODUCER_DETERMINATION_FIELD,
                f"{pointer}/{PRODUCER_DETERMINATION_FIELD}",
                goal_id=str(raw_goal.get("id") or ""),
                detail="omission is not a false determination",
            )
        _raise_preview_fields(
            raw_goal,
            PREVIEW_PRODUCER_AWARE_GOAL_FIELDS,
            pointer,
            rule_id="preview-goal-fields",
            goal_id=str(raw_goal.get("id") or ""),
        )
        goals.append(
            {
                "id": raw_goal["id"],
                PRODUCER_DETERMINATION_FIELD: raw_goal[
                    PRODUCER_DETERMINATION_FIELD
                ],
                "actor": raw_goal["actor"],
                "capability": {
                    "action": raw_goal["capability_action"],
                    "preconditions": raw_goal["capability_preconditions"],
                },
                "verifiable_outcome": {
                    "expected_state": raw_goal["outcome_expected_state"],
                    "evidence": raw_goal["outcome_evidence"],
                },
                "verification": {
                    "method": raw_goal["verification_method"],
                    "check": raw_goal["verification_check"],
                },
                "ux_trace": raw_goal["ux_trace"],
            }
        )

    raw_obligations = value.get("production_obligations")
    if not isinstance(raw_obligations, list):
        _raise_preview(
            "producer-root-list", "production_obligations", "/production_obligations"
        )
    obligations: list[dict[str, Any]] = []
    for index, raw_obligation in enumerate(raw_obligations):
        pointer = f"/production_obligations/{index}"
        if not isinstance(raw_obligation, Mapping):
            _raise_preview(
                "obligation-object", "production_obligations", pointer
            )
        _raise_preview_fields(
            raw_obligation,
            PREVIEW_PRODUCTION_OBLIGATION_FIELDS,
            pointer,
            rule_id="preview-obligation-fields",
            goal_id=str(raw_obligation.get("referee_goal_id") or ""),
            obligation_id=str(raw_obligation.get("id") or ""),
        )
        replaces = raw_obligation["replaces_obligation_id"]
        reason = raw_obligation["replacement_reason"]
        if replaces == "" and reason == "":
            links: list[dict[str, Any]] = []
        elif (
            isinstance(replaces, str)
            and bool(replaces.strip())
            and isinstance(reason, str)
            and bool(reason.strip())
        ):
            links = [
                {
                    "replaces_obligation_id": replaces,
                    "replacement_reason": reason,
                }
            ]
        else:
            field_name = (
                "replaces_obligation_id"
                if not isinstance(replaces, str) or not replaces.strip()
                else "replacement_reason"
            )
            _raise_preview(
                "replacement-link-partial",
                field_name,
                f"{pointer}/{field_name}",
                goal_id=str(raw_obligation.get("referee_goal_id") or ""),
                obligation_id=str(raw_obligation.get("id") or ""),
                detail="predecessor identity and replacement reason must both be empty or both non-empty",
            )
        obligations.append(
            {
                field_name: raw_obligation[field_name]
                for field_name in PRODUCTION_OBLIGATION_FIELDS
                if field_name != "replacement_links"
            }
        )
        obligations[-1]["replacement_links"] = links

    return {
        "goals": goals,
        "deliverable_requirements": deepcopy(value.get("deliverable_requirements")),
        "production_obligations": obligations,
        "patch_plan": deepcopy(value.get("patch_plan")),
    }


def validate_canonical_root_technical_approach(
    candidate: Mapping[str, Any],
    *,
    previous: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a normalized copy after pair, lineage, and partition closure.

    ``candidate`` may be the full Technical Approach tree or the small preview
    carrier.  The function performs no I/O and mutates neither input.
    """

    if not isinstance(candidate, Mapping):
        raise CanonicalRootPreviewError(
            "root-object", "technical_approach", "", detail="candidate must be an object"
        )
    normalized = deepcopy(dict(candidate))
    problem, problem_pointer = _producer_problem(normalized)
    previous_problem: Mapping[str, Any] | None = None
    if previous is not None:
        if not isinstance(previous, Mapping):
            raise CanonicalRootPreviewError(
                "previous-root-object",
                "previous",
                "",
                detail="previous candidate must be an object",
            )
        previous_problem, _ = _producer_problem(previous)

    goals = _require_preview_list(problem, "goals", problem_pointer)
    requirements = _require_preview_list(problem, "deliverable_requirements", problem_pointer)
    obligations = _require_preview_list(problem, "production_obligations", problem_pointer)
    assignments, assignment_pointer = _root_patch_plan_assignments(normalized, problem, problem_pointer)

    goal_by_id: dict[str, dict[str, Any]] = {}
    for index, value in enumerate(goals):
        pointer = f"{problem_pointer}/goals/{index}"
        if not isinstance(value, dict):
            _raise_preview("goal-object", "goals", pointer, detail="goal must be an object")
        if REJECTED_PRODUCER_DETERMINATION_ALIAS in value:
            _raise_preview(
                "requires-deliverable-alias",
                REJECTED_PRODUCER_DETERMINATION_ALIAS,
                f"{pointer}/{REJECTED_PRODUCER_DETERMINATION_ALIAS}",
                goal_id=str(value.get("id") or ""),
                detail=f"use only {PRODUCER_DETERMINATION_FIELD}",
            )
        if PRODUCER_DETERMINATION_FIELD not in value:
            _raise_preview(
                "requires-deliverable-required",
                PRODUCER_DETERMINATION_FIELD,
                f"{pointer}/{PRODUCER_DETERMINATION_FIELD}",
                goal_id=str(value.get("id") or ""),
                detail="omission is not a false determination",
            )
        _raise_preview_fields(value, PRODUCER_AWARE_GOAL_FIELDS, pointer, rule_id="goal-fields")
        goal_id = _preview_nonempty_string(value.get("id"), "id", f"{pointer}/id")
        if goal_id in goal_by_id:
            _raise_preview(
                "goal-id-unique", "id", f"{pointer}/id", goal_id=goal_id, detail="duplicate goal id"
            )
        if not isinstance(value.get(PRODUCER_DETERMINATION_FIELD), bool):
            _raise_preview(
                "requires-deliverable-boolean",
                PRODUCER_DETERMINATION_FIELD,
                f"{pointer}/{PRODUCER_DETERMINATION_FIELD}",
                goal_id=goal_id,
            )
        referee = {field: value[field] for field in SUCCESS_CRITERION_FIELDS}
        if _parse_success_criterion(referee) is None:
            _raise_preview(
                "referee-criterion-shape",
                "goals",
                pointer,
                goal_id=goal_id,
                detail="existing referee criterion fields are invalid",
            )
        goal_by_id[goal_id] = value
    if not goal_by_id:
        _raise_preview("goals-nonempty", "goals", f"{problem_pointer}/goals")

    requirement_by_goal: dict[str, dict[str, Any]] = {}
    requirement_ids: set[str] = set()
    for index, value in enumerate(requirements):
        pointer = f"{problem_pointer}/deliverable_requirements/{index}"
        if not isinstance(value, dict):
            _raise_preview("requirement-object", "deliverable_requirements", pointer)
        _raise_preview_fields(
            value, DELIVERABLE_REQUIREMENT_FIELDS, pointer, rule_id="requirement-fields"
        )
        requirement_id = _preview_nonempty_string(value.get("id"), "id", f"{pointer}/id")
        goal_id = _preview_nonempty_string(
            value.get("referee_goal_id"), "referee_goal_id", f"{pointer}/referee_goal_id"
        )
        if requirement_id in requirement_ids:
            _raise_preview(
                "requirement-id-unique", "id", f"{pointer}/id", goal_id=goal_id
            )
        requirement_ids.add(requirement_id)
        if goal_id in requirement_by_goal:
            _raise_preview(
                "requirement-one-per-goal",
                "referee_goal_id",
                f"{pointer}/referee_goal_id",
                goal_id=goal_id,
            )
        try:
            value["deliverable"] = normalize_deliverable(value.get("deliverable"))
        except (TypeError, ValueError) as exc:
            _raise_preview(
                "deliverable-normalization",
                "deliverable",
                f"{pointer}/deliverable",
                goal_id=goal_id,
                detail=str(exc),
            )
        requirement_by_goal[goal_id] = value

    obligation_by_goal: dict[str, dict[str, Any]] = {}
    obligation_ids: set[str] = set()
    for index, value in enumerate(obligations):
        pointer = f"{problem_pointer}/production_obligations/{index}"
        if not isinstance(value, dict):
            _raise_preview("obligation-object", "production_obligations", pointer)
        _raise_preview_fields(
            value, PRODUCTION_OBLIGATION_FIELDS, pointer, rule_id="obligation-fields"
        )
        obligation_id = _preview_nonempty_string(value.get("id"), "id", f"{pointer}/id")
        goal_id = _preview_nonempty_string(
            value.get("referee_goal_id"), "referee_goal_id", f"{pointer}/referee_goal_id"
        )
        _preview_nonempty_string(
            value.get("deliverable_requirement_id"),
            "deliverable_requirement_id",
            f"{pointer}/deliverable_requirement_id",
        )
        _preview_nonempty_string(
            value.get("eligibility_predicate"),
            "eligibility_predicate",
            f"{pointer}/eligibility_predicate",
        )
        if obligation_id in obligation_ids:
            _raise_preview(
                "obligation-id-unique",
                "id",
                f"{pointer}/id",
                goal_id=goal_id,
                obligation_id=obligation_id,
            )
        obligation_ids.add(obligation_id)
        if goal_id in obligation_by_goal:
            _raise_preview(
                "obligation-one-per-goal",
                "referee_goal_id",
                f"{pointer}/referee_goal_id",
                goal_id=goal_id,
                obligation_id=obligation_id,
            )
        try:
            value["deliverable"] = normalize_deliverable(value.get("deliverable"))
        except (TypeError, ValueError) as exc:
            _raise_preview(
                "deliverable-normalization",
                "deliverable",
                f"{pointer}/deliverable",
                goal_id=goal_id,
                obligation_id=obligation_id,
                detail=str(exc),
            )
        _validate_replacement_links(value, pointer, goal_id, obligation_id)
        obligation_by_goal[goal_id] = value

    for goal_id, goal in goal_by_id.items():
        requirement = requirement_by_goal.get(goal_id)
        obligation = obligation_by_goal.get(goal_id)
        requires = goal[PRODUCER_DETERMINATION_FIELD]
        if requires and (requirement is None or obligation is None):
            missing = "deliverable_requirements" if requirement is None else "production_obligations"
            _raise_preview(
                "producer-pair-required",
                missing,
                f"{problem_pointer}/{missing}",
                goal_id=goal_id,
                detail="requires_deliverable true requires exactly one complete pair",
            )
        if not requires and (requirement is not None or obligation is not None):
            present = "deliverable_requirements" if requirement is not None else "production_obligations"
            _raise_preview(
                "producer-pair-forbidden",
                present,
                f"{problem_pointer}/{present}",
                goal_id=goal_id,
                detail="requires_deliverable false permits neither pair member",
            )
        if not requires:
            continue
        assert requirement is not None and obligation is not None
        obligation_id = str(obligation["id"])
        if obligation["deliverable_requirement_id"] != requirement["id"]:
            _raise_preview(
                "pair-cross-link",
                "deliverable_requirement_id",
                f"{problem_pointer}/production_obligations",
                goal_id=goal_id,
                obligation_id=obligation_id,
                detail="obligation does not name its one requirement",
            )
        if requirement["production_obligation_id"] != obligation["id"]:
            _raise_preview(
                "pair-cross-link",
                "production_obligation_id",
                f"{problem_pointer}/deliverable_requirements",
                goal_id=goal_id,
                obligation_id=obligation_id,
                detail="requirement does not name its one production obligation",
            )
        if obligation["deliverable"] != requirement["deliverable"]:
            _raise_preview(
                "pair-deliverable-equality",
                "deliverable",
                f"{problem_pointer}/production_obligations",
                goal_id=goal_id,
                obligation_id=obligation_id,
                detail="normalized requirement and obligation deliverables differ",
            )

    for goal_id in sorted(set(requirement_by_goal) | set(obligation_by_goal)):
        if goal_id not in goal_by_id:
            record = obligation_by_goal.get(goal_id) or requirement_by_goal[goal_id]
            _raise_preview(
                "pair-goal-closure",
                "referee_goal_id",
                problem_pointer,
                goal_id=goal_id,
                obligation_id=str(record.get("id") or "") if isinstance(record, Mapping) else "",
                detail="pair member references an unknown referee goal",
            )

    _validate_obligation_lineage(
        obligation_by_goal,
        requirement_by_goal,
        previous_problem,
        problem_pointer,
    )
    _validate_patch_plan_partition(assignments, assignment_pointer, obligation_ids)
    return normalized


def preview_canonical_root_technical_approach(
    candidate: Mapping[str, Any],
    *,
    carrier: str | bytes | bytearray | Mapping[str, Any] | None = None,
    previous: Mapping[str, Any] | None = None,
    cover_letter: Mapping[str, Any] | None = None,
    request_provenance: Mapping[str, Any] | None = None,
    client: Any | None = None,
) -> CanonicalRootTechnicalApproachPreview:
    """Prepare a canonical producer-aware root preview without Git mutation."""

    if carrier is None:
        normalized = validate_canonical_root_technical_approach(
            candidate, previous=previous
        )
    else:
        normalized = parse_canonical_root_preview(
            carrier, base_candidate=candidate, previous=previous
        )
    if (cover_letter is None) != (request_provenance is None):
        raise ValueError("cover_letter and request_provenance must be supplied together")
    if cover_letter is not None and request_provenance is not None:
        prepared = patch_series_bodies.prepare_root_technical_approach_preview(
            cover_letter,
            request_provenance,
            normalized,
            client=client,
        )
        approach = prepared.technical_approach
        files = prepared.files()
    else:
        approach = patch_series_bodies.prepare_root_technical_approach(normalized, client=client)
        files = {
            patch_series_bodies.ROOT_APPROACH_PATH: approach.canonical.data.decode(
                "utf-8", errors="strict"
            )
        }
    canonical = approach.canonical
    return CanonicalRootTechnicalApproachPreview(
        technical_approach=normalized,
        canonical_cue=canonical.data.decode("utf-8", errors="strict"),
        body_sha256=canonical.sha256,
        cohort_files=files,
    )


def prepare_producer_aware_formation(
    formation: Mapping[str, Any],
    repo: str | Path,
    *,
    carrier: str | bytes | bytearray | Mapping[str, Any] | None = None,
    cover_letter: Mapping[str, Any] | None = None,
    request_provenance: Mapping[str, Any] | None = None,
    client: Any | None = None,
    ctx: org_log.RunContext | None = None,
) -> CanonicalRootTechnicalApproachPreview:
    """Prepare the already-final root cohort after formation.

    ``formation`` may be the full result returned by
    :func:`form_technical_approach` or its ``technical_approach`` value.  This
    preparation boundary performs no Git mutation.  The receive writer may
    publish the returned, completely preflighted CUE cohort only as one unit.
    """

    candidate, normalized_problem = _producer_preparation_input(formation)
    if normalized_problem is not None:
        _validate_normalized_referee_source(candidate, normalized_problem)
    return _prepare_producer_aware_root(
        candidate,
        repo,
        transition="formation",
        normalized_problem=normalized_problem,
        carrier=carrier,
        previous=None,
        cover_letter=cover_letter,
        request_provenance=request_provenance,
        client=client,
        ctx=ctx,
    )


def prepare_producer_aware_reform(
    reform: Mapping[str, Any],
    previous: Mapping[str, Any],
    repo: str | Path,
    *,
    carrier: str | bytes | bytearray | Mapping[str, Any] | None = None,
    cover_letter: Mapping[str, Any] | None = None,
    request_provenance: Mapping[str, Any] | None = None,
    client: Any | None = None,
    ctx: org_log.RunContext | None = None,
) -> CanonicalRootTechnicalApproachPreview:
    """Prepare a successor root with closed lineage, without publishing it."""

    candidate, normalized_problem = _producer_preparation_input(reform)
    if normalized_problem is not None:
        _validate_normalized_referee_source(candidate, normalized_problem)
    validated_previous = _validate_admitted_canonical_root_predecessor(previous)
    return _prepare_producer_aware_root(
        candidate,
        repo,
        transition="reform",
        normalized_problem=normalized_problem,
        carrier=carrier,
        previous=validated_previous,
        cover_letter=cover_letter,
        request_provenance=request_provenance,
        client=client,
        ctx=ctx,
    )


def _validate_admitted_canonical_root_predecessor(
    previous: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate one admitted root without erasing its predecessor authority.

    A reform predecessor can itself be the result of an earlier successor
    transition.  Comparing the root with itself deliberately exercises all
    closed-shape, pair, partition, identity, and retained-lineage checks while
    treating that already-admitted lineage as history.  Validating it as a new
    formation would incorrectly reject every second and later reform.
    """

    return validate_canonical_root_technical_approach(previous, previous=previous)


def _prepare_producer_aware_root(
    candidate: Mapping[str, Any],
    repo: str | Path,
    *,
    transition: str,
    normalized_problem: Mapping[str, Any] | None,
    carrier: str | bytes | bytearray | Mapping[str, Any] | None,
    previous: Mapping[str, Any] | None,
    cover_letter: Mapping[str, Any] | None,
    request_provenance: Mapping[str, Any] | None,
    client: Any | None,
    ctx: org_log.RunContext | None,
) -> CanonicalRootTechnicalApproachPreview:
    """Run the receive-owned producer preparation transition in memory."""

    if transition not in {"formation", "reform"}:
        raise ValueError(f"unknown producer preparation transition: {transition}")
    item_ids = _canonical_patch_plan_item_ids(candidate)
    raw_carrier: str | bytes | bytearray | Mapping[str, Any]
    if carrier is None:
        run = codex_exec.run_json(
            Path(repo).resolve(),
            schema=build_canonical_root_preview_schema(),
            prompt=_producer_aware_preparation_prompt(
                candidate,
                normalized_problem,
                item_ids,
                transition=transition,
                previous=previous,
            ),
            schema_filename=f"patch_series-producer-aware-{transition}.schema.json",
            output_filename=f"patch_series-producer-aware-{transition}.json",
            failure_label=f"Codex producer-aware {transition} preparation",
            ctx=ctx,
        )
        if not run.get("ok"):
            _raise_preview(
                "producer-preparation-run",
                "preview",
                "",
                detail=str(run.get("error") or "producer preparation failed"),
            )
        raw_carrier = str(run["raw"])
    else:
        raw_carrier = carrier

    value = _load_canonical_root_preview_carrier(raw_carrier)
    reconstructed = _reconstruct_producer_aware_carrier(
        value,
        candidate,
        item_ids,
        previous=previous,
        transition=transition,
    )
    return preview_canonical_root_technical_approach(
        candidate,
        carrier=reconstructed,
        previous=previous,
        cover_letter=cover_letter,
        request_provenance=request_provenance,
        client=client,
    )


def _producer_preparation_input(
    value: Mapping[str, Any],
) -> tuple[Mapping[str, Any], Mapping[str, Any] | None]:
    if not isinstance(value, Mapping):
        _raise_preview(
            "producer-preparation-input",
            "technical_approach",
            "",
            detail="formation or reform input must be an object",
        )
    candidate = value.get("technical_approach")
    if not isinstance(candidate, Mapping):
        candidate = value
    normalized_problem: Mapping[str, Any] | None = None
    steps = value.get("steps")
    if isinstance(steps, Mapping) and isinstance(steps.get("normalize_problem"), Mapping):
        normalized_problem = steps["normalize_problem"]
    return candidate, normalized_problem


def _validate_normalized_referee_source(
    candidate: Mapping[str, Any], normalized_problem: Mapping[str, Any]
) -> None:
    """Keep formation's referee criteria byte-stable from normalization.

    Normalization owns the Producer Eligibility Predicate.  The later assembly
    step adds goal identities and the rest of the technical-approach tree, but it
    must not be able to omit, alias, or redetermine ``requires_deliverable`` (or
    any of the criterion that determination qualifies) before preparation.
    """

    problem, problem_pointer = _producer_problem(candidate)
    candidate_goals = problem.get("goals")
    source_goals = normalized_problem.get("success_criteria")
    if not isinstance(candidate_goals, list) or not isinstance(source_goals, list):
        _raise_preview(
            "normalized-referee-source",
            "goals",
            f"{problem_pointer}/goals",
            detail=(
                "formation preparation requires both normalized success criteria "
                "and assembled referee goals"
            ),
        )
    if len(candidate_goals) != len(source_goals):
        _raise_preview(
            "normalized-referee-source",
            "goals",
            f"{problem_pointer}/goals",
            detail="assembled referee-goal coverage differs from normalization",
        )
    for index, (candidate_goal, source_goal) in enumerate(
        zip(candidate_goals, source_goals, strict=True)
    ):
        pointer = f"{problem_pointer}/goals/{index}"
        if not isinstance(candidate_goal, Mapping) or not isinstance(
            source_goal, Mapping
        ):
            _raise_preview(
                "normalized-referee-source",
                "goals",
                pointer,
                detail="normalized and assembled referee criteria must be objects",
            )
        mismatched_field = next(
            (
                field_name
                for field_name in SUCCESS_CRITERION_FIELDS
                if candidate_goal.get(field_name) != source_goal.get(field_name)
            ),
            None,
        )
        if mismatched_field is not None:
            _raise_preview(
                "normalized-referee-source",
                mismatched_field,
                f"{pointer}/{mismatched_field}",
                goal_id=str(candidate_goal.get("id") or ""),
                detail=(
                    "assembled Canonical Referee Criterion differs from the "
                    "normalization result"
                ),
            )


def _has_explicit_producer_determinations(value: Mapping[str, Any]) -> bool:
    """Return whether normalization has made every producer decision explicit.

    Historical technical-approach trees predate this field and remain on their
    established writer path only when none of their goals claims either
    producer-determination spelling.  Once any goal claims the canonical field,
    every goal must carry an explicit boolean; partial normalization must not be
    misclassified as historical and bypass producer-aware preparation.
    """

    if not isinstance(value, Mapping):
        return False
    candidate = value.get("technical_approach")
    if isinstance(candidate, Mapping):
        value = candidate
    problem = value.get("problem")
    goals = problem.get("goals") if isinstance(problem, Mapping) else None
    if not isinstance(goals, list) or not goals:
        return False

    canonical_count = 0
    for index, goal in enumerate(goals):
        pointer = f"/problem/goals/{index}"
        if not isinstance(goal, Mapping):
            if canonical_count:
                _raise_preview("goal-object", "goals", pointer)
            continue
        goal_id = str(goal.get("id") or "")
        if REJECTED_PRODUCER_DETERMINATION_ALIAS in goal:
            _raise_preview(
                "requires-deliverable-alias",
                REJECTED_PRODUCER_DETERMINATION_ALIAS,
                f"{pointer}/{REJECTED_PRODUCER_DETERMINATION_ALIAS}",
                goal_id=goal_id,
                detail=f"use only {PRODUCER_DETERMINATION_FIELD}",
            )
        if PRODUCER_DETERMINATION_FIELD not in goal:
            continue
        canonical_count += 1
        if not isinstance(goal[PRODUCER_DETERMINATION_FIELD], bool):
            _raise_preview(
                "requires-deliverable-boolean",
                PRODUCER_DETERMINATION_FIELD,
                f"{pointer}/{PRODUCER_DETERMINATION_FIELD}",
                goal_id=goal_id,
            )

    if canonical_count == 0:
        return False
    if canonical_count != len(goals):
        missing_index = next(
            index
            for index, goal in enumerate(goals)
            if not isinstance(goal, Mapping)
            or PRODUCER_DETERMINATION_FIELD not in goal
        )
        missing = goals[missing_index]
        _raise_preview(
            "requires-deliverable-required",
            PRODUCER_DETERMINATION_FIELD,
            f"/problem/goals/{missing_index}/{PRODUCER_DETERMINATION_FIELD}",
            goal_id=str(missing.get("id") or "") if isinstance(missing, Mapping) else "",
            detail="partial normalization cannot fall back to the historical writer path",
        )
    return True


def _producer_preparation_transition(
    candidate: Mapping[str, Any],
    *,
    previous: Mapping[str, Any] | None = None,
) -> str:
    """Select the mandatory producer-aware preparation transition.

    The durable cutover removes the historical writer as a choice for new
    formation and explicit reform. Missing determinations therefore select
    canonical preparation and fail there with typed coordinates; they can no
    longer opt a newly written root into the legacy representation. Only an
    untouched historical ref remains eligible for historical dispatch.
    """

    candidate_is_producer_aware = _has_explicit_producer_determinations(candidate)
    if previous is None:
        return "formation"

    previous_is_producer_aware = _has_explicit_producer_determinations(previous)
    if previous_is_producer_aware and not candidate_is_producer_aware:
        value = candidate.get("technical_approach")
        if isinstance(value, Mapping):
            candidate = value
        problem = candidate.get("problem")
        goals = problem.get("goals") if isinstance(problem, Mapping) else None
        first_goal = goals[0] if isinstance(goals, list) and goals else None
        _raise_preview(
            "requires-deliverable-required",
            PRODUCER_DETERMINATION_FIELD,
            f"/problem/goals/0/{PRODUCER_DETERMINATION_FIELD}"
            if first_goal is not None
            else "/problem/goals",
            goal_id=(
                str(first_goal.get("id") or "")
                if isinstance(first_goal, Mapping)
                else ""
            ),
            detail=(
                "a producer-aware predecessor cannot reform through the "
                "historical writer fallback"
            ),
        )
    # Explicitly reforming a historical root is its producer-aware formation;
    # reforming an admitted producer-aware root preserves successor lineage.
    return "reform" if previous_is_producer_aware else "formation"


def _load_canonical_root_preview_carrier(
    raw: str | bytes | bytearray | Mapping[str, Any],
) -> dict[str, Any]:
    if isinstance(raw, Mapping):
        value: Any = deepcopy(dict(raw))
    else:
        try:
            value = strict_json_loads(raw)
        except (TypeError, ValueError) as exc:
            _raise_preview(
                "preview-json",
                "preview",
                "",
                detail="preview carrier is not valid strict UTF-8 JSON",
            )
    if not isinstance(value, dict):
        _raise_preview(
            "preview-object", "preview", "", detail="preview carrier must be an object"
        )
    if set(value) != set(CANONICAL_ROOT_PREVIEW_FIELDS):
        _raise_preview_fields(
            value,
            CANONICAL_ROOT_PREVIEW_FIELDS,
            "",
            rule_id="preview-fields",
        )
    return value


def _reconstruct_producer_aware_carrier(
    carrier: dict[str, Any],
    candidate: Mapping[str, Any],
    item_ids: list[str],
    *,
    previous: Mapping[str, Any] | None,
    transition: str,
) -> dict[str, Any]:
    """Own IDs, lineage, and assignment coordinates after semantic output."""

    candidate_problem, _ = _producer_problem(candidate)
    candidate_goals = candidate_problem.get("goals")
    carrier_goals = carrier.get("goals")
    if not isinstance(candidate_goals, list) or not isinstance(carrier_goals, list):
        _raise_preview("producer-root-list", "goals", "/goals")
    if len(candidate_goals) != len(carrier_goals):
        _raise_preview(
            "referee-goal-coverage",
            "goals",
            "/goals",
            detail="preparation must preserve every supplied referee criterion",
        )

    candidate_by_id = {
        str(goal.get("id")): goal
        for goal in candidate_goals
        if isinstance(goal, Mapping) and isinstance(goal.get("id"), str)
    }
    for index, supplied in enumerate(candidate_goals):
        if (
            isinstance(supplied, Mapping)
            and REJECTED_PRODUCER_DETERMINATION_ALIAS in supplied
        ):
            _raise_preview(
                "requires-deliverable-alias",
                REJECTED_PRODUCER_DETERMINATION_ALIAS,
                f"/problem/goals/{index}/{REJECTED_PRODUCER_DETERMINATION_ALIAS}",
                goal_id=str(supplied.get("id") or ""),
                detail="the supplied candidate cannot use the rejected alias",
            )
    for index, goal in enumerate(carrier_goals):
        pointer = f"/goals/{index}"
        if not isinstance(goal, Mapping):
            _raise_preview("goal-object", "goals", pointer)
        if REJECTED_PRODUCER_DETERMINATION_ALIAS in goal:
            _raise_preview(
                "requires-deliverable-alias",
                REJECTED_PRODUCER_DETERMINATION_ALIAS,
                f"{pointer}/{REJECTED_PRODUCER_DETERMINATION_ALIAS}",
                goal_id=str(goal.get("id") or ""),
                detail=f"use only {PRODUCER_DETERMINATION_FIELD}",
            )
        if PRODUCER_DETERMINATION_FIELD not in goal:
            _raise_preview(
                "requires-deliverable-required",
                PRODUCER_DETERMINATION_FIELD,
                f"{pointer}/{PRODUCER_DETERMINATION_FIELD}",
                goal_id=str(goal.get("id") or ""),
                detail="omission is not a false determination",
            )
        supplied = candidate_by_id.get(str(goal.get("id") or ""))
        if supplied is None:
            _raise_preview(
                "referee-goal-identity",
                "id",
                f"{pointer}/id",
                goal_id=str(goal.get("id") or ""),
            )
        restored = _preview_goal_to_referee(goal, pointer)
        if any(restored[field] != supplied.get(field) for field in SUCCESS_CRITERION_FIELDS):
            _raise_preview(
                "canonical-referee-byte-stable",
                "goals",
                pointer,
                goal_id=str(goal.get("id") or ""),
                detail=(
                    "preparation must preserve the referee criterion and its normalized "
                    "requires_deliverable determination"
                ),
            )
    if [str(goal.get("id")) for goal in carrier_goals] != [
        str(goal.get("id")) for goal in candidate_goals
    ]:
        _raise_preview(
            "referee-goal-order",
            "goals",
            "/goals",
            detail="preparation must retain supplied goal order",
        )

    requirements = carrier.get("deliverable_requirements")
    obligations = carrier.get("production_obligations")
    assignments = carrier.get("patch_plan")
    if not isinstance(requirements, list):
        _raise_preview("producer-root-list", "deliverable_requirements", "/deliverable_requirements")
    if not isinstance(obligations, list):
        _raise_preview("producer-root-list", "production_obligations", "/production_obligations")
    if not isinstance(assignments, list):
        _raise_preview("producer-root-list", "patch_plan", "/patch_plan")

    previous_problem: Mapping[str, Any] = {}
    if previous is not None:
        previous_problem, _ = _producer_problem(previous)
    previous_requirements = {
        str(item.get("referee_goal_id")): item
        for item in previous_problem.get("deliverable_requirements", [])
        if isinstance(item, Mapping)
    }
    previous_obligations = {
        str(item.get("referee_goal_id")): item
        for item in previous_problem.get("production_obligations", [])
        if isinstance(item, Mapping)
    }
    previous_obligations_by_id = {
        str(item.get("id")): item for item in previous_obligations.values()
    }

    requirement_by_goal = _one_preview_record_per_goal(
        requirements, "deliverable_requirements"
    )
    obligation_by_goal = _one_preview_record_per_goal(
        obligations, "production_obligations"
    )
    requirement_index_by_goal = {
        str(item.get("referee_goal_id") or ""): index
        for index, item in enumerate(requirements)
        if isinstance(item, Mapping)
    }
    obligation_index_by_goal = {
        str(item.get("referee_goal_id") or ""): index
        for index, item in enumerate(obligations)
        if isinstance(item, Mapping)
    }
    rewritten_ids: dict[str, str] = {}
    claimed_predecessor_ids: set[str] = set()
    for goal in carrier_goals:
        goal_id = str(goal["id"])
        if not goal[PRODUCER_DETERMINATION_FIELD]:
            continue
        requirement = requirement_by_goal.get(goal_id)
        obligation = obligation_by_goal.get(goal_id)
        if requirement is None or obligation is None:
            _raise_preview(
                "producer-pair-required",
                "deliverable_requirements" if requirement is None else "production_obligations",
                "/deliverable_requirements" if requirement is None else "/production_obligations",
                goal_id=goal_id,
            )
        try:
            requirement_deliverable = normalize_deliverable(
                requirement.get("deliverable")
            )
        except (TypeError, ValueError) as exc:
            _raise_preview(
                "deliverable-normalization",
                "deliverable",
                (
                    f"/deliverable_requirements/"
                    f"{requirement_index_by_goal[goal_id]}/deliverable"
                ),
                goal_id=goal_id,
                detail=str(exc),
            )
        try:
            obligation_deliverable = normalize_deliverable(
                obligation.get("deliverable")
            )
        except (TypeError, ValueError) as exc:
            _raise_preview(
                "deliverable-normalization",
                "deliverable",
                (
                    f"/production_obligations/"
                    f"{obligation_index_by_goal[goal_id]}/deliverable"
                ),
                goal_id=goal_id,
                obligation_id=str(obligation.get("id") or ""),
                detail=str(exc),
            )
        if requirement_deliverable != obligation_deliverable:
            _raise_preview(
                "pair-deliverable-equality",
                "deliverable",
                "/production_obligations",
                goal_id=goal_id,
            )

        old_obligation_id = str(obligation.get("id") or "")
        predecessor_obligation = previous_obligations.get(goal_id)
        predecessor_requirement = previous_requirements.get(goal_id)
        if predecessor_obligation is None:
            declared_predecessor_id = str(
                obligation.get("replaces_obligation_id") or ""
            )
            if declared_predecessor_id:
                predecessor_obligation = previous_obligations_by_id.get(
                    declared_predecessor_id
                )
                if predecessor_obligation is None:
                    _raise_preview(
                        "successor-predecessor-link",
                        "replaces_obligation_id",
                        "/production_obligations",
                        goal_id=goal_id,
                        detail="changed-goal continuity must name an exact prior obligation ID",
                    )
                predecessor_goal_id = str(
                    predecessor_obligation.get("referee_goal_id") or ""
                )
                if predecessor_goal_id in candidate_by_id:
                    _raise_preview(
                        "goal-continuity-conflict",
                        "replaces_obligation_id",
                        "/production_obligations",
                        goal_id=goal_id,
                        obligation_id=declared_predecessor_id,
                        detail="a successor cannot replace an obligation whose goal survives",
                    )
                predecessor_requirement = previous_requirements.get(
                    predecessor_goal_id
                )
        if predecessor_obligation is not None:
            predecessor_id = str(predecessor_obligation.get("id") or "")
            if predecessor_id in claimed_predecessor_ids:
                _raise_preview(
                    "predecessor-obligation-unique",
                    "replaces_obligation_id",
                    "/production_obligations",
                    goal_id=goal_id,
                    obligation_id=predecessor_id,
                    detail="one predecessor cannot establish continuity for multiple goals",
                )
            claimed_predecessor_ids.add(predecessor_id)
        same_projection = (
            predecessor_obligation is not None
            and predecessor_obligation.get("referee_goal_id") == goal_id
            and normalize_deliverable(predecessor_obligation.get("deliverable"))
            == obligation_deliverable
        )
        if same_projection:
            assert predecessor_obligation is not None and predecessor_requirement is not None
            obligation_id = str(predecessor_obligation["id"])
            requirement_id = str(predecessor_requirement["id"])
            links = deepcopy(predecessor_obligation.get("replacement_links", []))
        else:
            digest = production_obligation_identity(goal_id, obligation_deliverable)
            obligation_id = f"obligation:{digest}"
            requirement_id = f"requirement:{digest}"
            if predecessor_obligation is None:
                links = []
                if obligation.get("replaces_obligation_id") or obligation.get("replacement_reason"):
                    _raise_preview(
                        "initial-replacement-links-empty",
                        "replacement_links",
                        "/production_obligations",
                        goal_id=goal_id,
                        obligation_id=obligation_id,
                    )
            else:
                if predecessor_requirement is None:
                    _raise_preview(
                        "previous-pair-closure",
                        "deliverable_requirements",
                        "/production_obligations",
                        goal_id=goal_id,
                        obligation_id=str(predecessor_obligation.get("id") or ""),
                    )
                reason = obligation.get("replacement_reason")
                if not isinstance(reason, str) or not reason.strip():
                    _raise_preview(
                        "successor-replacement-reason",
                        "replacement_reason",
                        "/production_obligations",
                        goal_id=goal_id,
                        obligation_id=obligation_id,
                    )
                links = [{
                    "replaces_obligation_id": str(predecessor_obligation["id"]),
                    "replacement_reason": reason,
                }]
        rewritten_ids[old_obligation_id] = obligation_id
        requirement.update(
            {
                "id": requirement_id,
                "production_obligation_id": obligation_id,
                "deliverable": requirement_deliverable,
            }
        )
        obligation.update(
            {
                "id": obligation_id,
                "deliverable_requirement_id": requirement_id,
                "deliverable": obligation_deliverable,
                "replaces_obligation_id": (
                    links[0]["replaces_obligation_id"] if links else ""
                ),
                "replacement_reason": links[0]["replacement_reason"] if links else "",
            }
        )

    actual_item_ids = [
        str(item.get("item_id")) for item in assignments if isinstance(item, Mapping)
    ]
    if actual_item_ids != item_ids:
        _raise_preview(
            "patch-plan-item-coverage",
            "item_id",
            "/patch_plan",
            detail=f"expected exact ordered patch items {item_ids!r}",
        )
    for assignment in assignments:
        values = assignment.get("production_obligation_ids")
        if not isinstance(values, list):
            _raise_preview(
                "patch-plan-obligation-array",
                "production_obligation_ids",
                "/patch_plan",
            )
        assignment["production_obligation_ids"] = [
            rewritten_ids.get(str(value), str(value)) for value in values
        ]
    return carrier


def _preview_goal_to_referee(
    goal: Mapping[str, Any], pointer: str
) -> dict[str, Any]:
    _raise_preview_fields(
        goal,
        PREVIEW_PRODUCER_AWARE_GOAL_FIELDS,
        pointer,
        rule_id="preview-goal-fields",
        goal_id=str(goal.get("id") or ""),
    )
    return {
        "actor": goal["actor"],
        "capability": {
            "action": goal["capability_action"],
            "preconditions": goal["capability_preconditions"],
        },
        "verifiable_outcome": {
            "expected_state": goal["outcome_expected_state"],
            "evidence": goal["outcome_evidence"],
        },
        "verification": {
            "method": goal["verification_method"],
            "check": goal["verification_check"],
        },
        "ux_trace": goal["ux_trace"],
        PRODUCER_DETERMINATION_FIELD: goal[PRODUCER_DETERMINATION_FIELD],
    }


def _one_preview_record_per_goal(
    values: list[Any], field_name: str
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for index, value in enumerate(values):
        if not isinstance(value, dict):
            _raise_preview(f"{field_name}-object", field_name, f"/{field_name}/{index}")
        goal_id = str(value.get("referee_goal_id") or "")
        if goal_id in result:
            _raise_preview(
                f"{field_name}-one-per-goal",
                "referee_goal_id",
                f"/{field_name}/{index}/referee_goal_id",
                goal_id=goal_id,
            )
        result[goal_id] = value
    return result


def _canonical_patch_plan_item_ids(candidate: Mapping[str, Any]) -> list[str]:
    problem, pointer = _producer_problem(candidate)
    direct = problem.get("patch_plan")
    if isinstance(direct, list):
        ids = [
            str(item.get("item_id") or "")
            for item in direct
            if isinstance(item, Mapping)
        ]
        if len(ids) == len(direct) and all(ids):
            return ids

    question = problem.get("question")
    decision = question.get("decision") if isinstance(question, Mapping) else None
    implementation = decision.get("implementation") if isinstance(decision, Mapping) else None
    patch_plan = implementation.get("patch_plan") if isinstance(implementation, Mapping) else None
    if not isinstance(patch_plan, Mapping):
        _raise_preview(
            "patch-plan-required",
            "patch_plan",
            f"{pointer}/question/decision/implementation/patch_plan",
        )
    graph_items = patch_plan.get("items")
    if isinstance(graph_items, list):
        ids = [
            str(item.get("id") or "")
            for item in graph_items
            if isinstance(item, Mapping)
        ]
        if len(ids) != len(graph_items) or not all(ids) or len(ids) != len(set(ids)):
            _raise_preview(
                "patch-plan-item-identity",
                "items",
                f"{pointer}/question/decision/implementation/patch_plan/items",
            )
        return ids

    plan_id = _preview_nonempty_string(
        patch_plan.get("id"),
        "id",
        f"{pointer}/question/decision/implementation/patch_plan/id",
    )
    follow_ups = patch_plan.get("follow_ups")
    if not isinstance(patch_plan.get("first_proof_moment"), Mapping) or not isinstance(
        follow_ups, list
    ):
        _raise_preview(
            "patch-plan-shape",
            "patch_plan",
            f"{pointer}/question/decision/implementation/patch_plan",
        )
    return [
        f"{plan_id}#first_proof_moment",
        *[
            f"{plan_id}#follow-up-{index:02d}"
            for index in range(1, len(follow_ups) + 1)
        ],
    ]


def _producer_aware_preparation_prompt(
    candidate: Mapping[str, Any],
    normalized_problem: Mapping[str, Any] | None,
    item_ids: list[str],
    *,
    transition: str,
    previous: Mapping[str, Any] | None,
) -> str:
    previous_text = (
        json.dumps(previous, indent=2, sort_keys=True, ensure_ascii=True, default=str)
        if previous is not None
        else "none (initial formation)"
    )
    return (
        f"Prepare the receive-owned, non-publishing producer-aware {transition} carrier. "
        "This is a formal preparation step, not a Git writer. Return only JSON matching the schema.\n\n"
        "Preserve every supplied Canonical Referee Criterion byte-for-byte and in order, including its already "
        f"self-evaluated Producer Eligibility Predicate {PRODUCER_DETERMINATION_FIELD}. Never omit, rename, or redetermine "
        "that field. True means the criterion needs a material "
        "produced deliverable; false means it is referee-only.\n"
        "For every true goal emit exactly one deliverable requirement and one production obligation with identical "
        "deliverable text after whitespace normalization. Emit neither record for a false goal. The eligibility_predicate "
        "must be a concrete predicate a prospective producer can evaluate about itself. IDs are temporary carrier "
        "coordinates: receive reconstructs deterministic identities with the Obligation Identity Projection. For "
        "initial formation leave both replacement "
        "strings empty. For a changed reform deliverable provide a non-empty factual replacement_reason; receive owns "
        "the exact same-goal predecessor link. When a goal identity itself changes, establish normalized-goal "
        "continuity first by supplying the exact prior obligation ID and a non-empty reason; never infer continuity "
        "from paraphrase similarity.\n"
        "Emit one patch_plan assignment for each supplied item id, in this exact order, and assign every production "
        "obligation exactly once across them.\n\n"
        f"Exact patch item ids:\n{json.dumps(item_ids, indent=2)}\n\n"
        f"Normalization result from formation (context only):\n{json.dumps(normalized_problem or {}, indent=2, sort_keys=True, ensure_ascii=True, default=str)}\n\n"
        f"Supplied historical candidate:\n{json.dumps(candidate, indent=2, sort_keys=True, ensure_ascii=True, default=str)}\n\n"
        f"Previous canonical root for lineage:\n{previous_text}\n"
    )


def _producer_problem(value: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
    problem = value.get("problem")
    if problem is None:
        if not isinstance(value, dict):
            value = dict(value)
        return value, ""
    if not isinstance(problem, dict):
        _raise_preview("problem-object", "problem", "/problem", detail="problem must be an object")
    return problem, "/problem"


def _require_preview_list(problem: Mapping[str, Any], field_name: str, prefix: str) -> list[Any]:
    if field_name not in problem:
        _raise_preview(
            "producer-root-field-required",
            field_name,
            f"{prefix}/{field_name}",
            detail=f"{field_name} is required in the canonical root preview",
        )
    value = problem[field_name]
    if not isinstance(value, list):
        _raise_preview("producer-root-list", field_name, f"{prefix}/{field_name}")
    return value


def _root_patch_plan_assignments(
    root: Mapping[str, Any],
    problem: Mapping[str, Any],
    problem_pointer: str,
) -> tuple[list[Mapping[str, Any]], str]:
    direct = problem.get("patch_plan")
    if isinstance(direct, list):
        return direct, f"{problem_pointer}/patch_plan"
    if problem_pointer == "" and isinstance(root.get("patch_plan"), list):
        return root["patch_plan"], "/patch_plan"

    question = problem.get("question")
    decision = question.get("decision") if isinstance(question, Mapping) else None
    implementation = decision.get("implementation") if isinstance(decision, Mapping) else None
    legacy = implementation.get("patch_plan") if isinstance(implementation, Mapping) else None
    pointer = f"{problem_pointer}/question/decision/implementation/patch_plan"
    if isinstance(legacy, list):
        projection = [
            {
                field_name: item[field_name]
                for field_name in ROOT_PATCH_PLAN_ASSIGNMENT_FIELDS
                if isinstance(item, Mapping) and field_name in item
            }
            if isinstance(item, Mapping)
            else item
            for item in legacy
        ]
        if isinstance(problem, dict):
            problem["patch_plan"] = projection
        return projection, pointer
    if isinstance(legacy, Mapping):
        graph_items = legacy.get("items")
        if isinstance(graph_items, list):
            projection = []
            for index, item in enumerate(graph_items):
                if not isinstance(item, Mapping) or not isinstance(item.get("id"), str):
                    _raise_preview(
                        "patch-plan-item-object",
                        "patch_plan",
                        f"{pointer}/items/{index}",
                    )
                projection.append(
                    {
                        "item_id": item["id"],
                        "production_obligation_ids": list(
                            item.get("production_obligation_ids", [])
                        ),
                    }
                )
            if isinstance(problem, dict):
                problem["patch_plan"] = projection
            return projection, pointer
        first = legacy.get("first_proof_moment")
        follow_ups = legacy.get("follow_ups")
        if not isinstance(first, Mapping) or not isinstance(follow_ups, list):
            _raise_preview(
                "patch-plan-shape",
                "patch_plan",
                pointer,
                detail="patch plan must expose executable items",
            )
        values: list[Mapping[str, Any]] = [first]
        for index, item in enumerate(follow_ups):
            if not isinstance(item, Mapping):
                _raise_preview(
                    "patch-plan-item-object",
                    "patch_plan",
                    f"{pointer}/follow_ups/{index}",
                )
            values.append(item)
        projection = [
            {
                field_name: item[field_name]
                for field_name in ROOT_PATCH_PLAN_ASSIGNMENT_FIELDS
                if field_name in item
            }
            for item in values
        ]
        if isinstance(problem, dict):
            problem["patch_plan"] = projection
        return projection, pointer
    _raise_preview(
        "patch-plan-required",
        "patch_plan",
        f"{problem_pointer}/patch_plan",
        detail="canonical root assignment array is required",
    )


def _validate_patch_plan_partition(
    assignments: list[Mapping[str, Any]],
    pointer: str,
    obligation_ids: set[str],
) -> None:
    item_ids: set[str] = set()
    assigned: set[str] = set()
    for index, item in enumerate(assignments):
        item_pointer = f"{pointer}/{index}"
        if not isinstance(item, Mapping):
            _raise_preview("patch-plan-item-object", "patch_plan", item_pointer)
        missing_fields = [
            field_name
            for field_name in ROOT_PATCH_PLAN_ASSIGNMENT_FIELDS
            if field_name not in item
        ]
        if missing_fields:
            _raise_preview(
                "patch-plan-assignment-required",
                missing_fields[0],
                f"{item_pointer}/{missing_fields[0]}",
            )
        _raise_preview_fields(
            item,
            ROOT_PATCH_PLAN_ASSIGNMENT_FIELDS,
            item_pointer,
            rule_id="patch-plan-assignment-fields",
        )
        item_id = _preview_nonempty_string(item.get("item_id"), "item_id", f"{item_pointer}/item_id")
        if item_id in item_ids:
            _raise_preview(
                "patch-plan-item-id-unique", "item_id", f"{item_pointer}/item_id"
            )
        item_ids.add(item_id)
        values = item.get("production_obligation_ids")
        if not isinstance(values, list) or not all(
            isinstance(value, str) and bool(value.strip()) for value in values
        ):
            _raise_preview(
                "patch-plan-obligation-array",
                "production_obligation_ids",
                f"{item_pointer}/production_obligation_ids",
            )
        if len(values) != len(set(values)):
            _raise_preview(
                "patch-plan-item-obligation-unique",
                "production_obligation_ids",
                f"{item_pointer}/production_obligation_ids",
            )
        for obligation_id in values:
            if obligation_id in assigned:
                _raise_preview(
                    "production-obligation-exactly-once",
                    "production_obligation_ids",
                    f"{item_pointer}/production_obligation_ids",
                    obligation_id=obligation_id,
                    detail="obligation is assigned more than once",
                )
            assigned.add(obligation_id)
    if assigned != obligation_ids:
        missing = sorted(obligation_ids - assigned)
        surplus = sorted(assigned - obligation_ids)
        target = missing[0] if missing else surplus[0] if surplus else ""
        _raise_preview(
            "production-obligation-exact-partition",
            "production_obligation_ids",
            pointer,
            obligation_id=target,
            detail=f"missing={missing!r} surplus={surplus!r}",
        )


def _validate_replacement_links(
    obligation: Mapping[str, Any],
    pointer: str,
    goal_id: str,
    obligation_id: str,
) -> None:
    links = obligation.get("replacement_links")
    if not isinstance(links, list):
        _raise_preview(
            "replacement-links-array",
            "replacement_links",
            f"{pointer}/replacement_links",
            goal_id=goal_id,
            obligation_id=obligation_id,
        )
    if len(links) > 1:
        _raise_preview(
            "replacement-links-cardinality",
            "replacement_links",
            f"{pointer}/replacement_links",
            goal_id=goal_id,
            obligation_id=obligation_id,
            detail="only an initial [] or one successor link is valid",
        )
    for index, link in enumerate(links):
        link_pointer = f"{pointer}/replacement_links/{index}"
        if not isinstance(link, Mapping):
            _raise_preview(
                "replacement-link-object",
                "replacement_links",
                link_pointer,
                goal_id=goal_id,
                obligation_id=obligation_id,
            )
        _raise_preview_fields(
            link,
            OBLIGATION_REPLACEMENT_LINK_FIELDS,
            link_pointer,
            rule_id="replacement-link-fields",
            goal_id=goal_id,
            obligation_id=obligation_id,
        )
        _preview_nonempty_string(
            link.get("replaces_obligation_id"),
            "replaces_obligation_id",
            f"{link_pointer}/replaces_obligation_id",
            goal_id=goal_id,
            obligation_id=obligation_id,
        )
        _preview_nonempty_string(
            link.get("replacement_reason"),
            "replacement_reason",
            f"{link_pointer}/replacement_reason",
            goal_id=goal_id,
            obligation_id=obligation_id,
        )


def _validate_obligation_lineage(
    current: Mapping[str, Mapping[str, Any]],
    current_requirements: Mapping[str, Mapping[str, Any]],
    previous_problem: Mapping[str, Any] | None,
    pointer: str,
) -> None:
    previous_obligations: dict[str, Mapping[str, Any]] = {}
    previous_requirements: dict[str, Mapping[str, Any]] = {}
    if previous_problem is not None:
        raw_obligations = previous_problem.get("production_obligations")
        raw_requirements = previous_problem.get("deliverable_requirements")
        if not isinstance(raw_obligations, list) or not isinstance(raw_requirements, list):
            _raise_preview(
                "previous-pair-shape",
                "previous",
                pointer,
                detail="previous root must contain pair arrays",
            )
        previous_obligations = {
            str(item.get("referee_goal_id")): item
            for item in raw_obligations
            if isinstance(item, Mapping)
        }
        previous_requirements = {
            str(item.get("referee_goal_id")): item
            for item in raw_requirements
            if isinstance(item, Mapping)
        }
    previous_obligation_ids = {
        str(item.get("id")): goal_id for goal_id, item in previous_obligations.items()
    }
    previous_requirement_ids = {
        str(item.get("id")): goal_id for goal_id, item in previous_requirements.items()
    }
    current_goal_ids = set(current)
    claimed_predecessor_ids: set[str] = set()
    for goal_id, obligation in current.items():
        obligation_id = str(obligation["id"])
        links = obligation["replacement_links"]
        if previous_problem is None and links:
            _raise_preview(
                "initial-obligation-lineage",
                "replacement_links",
                f"{pointer}/production_obligations",
                goal_id=goal_id,
                obligation_id=obligation_id,
                detail="initial obligations cannot claim a predecessor",
            )
        predecessor = previous_obligations.get(goal_id)
        requirement = current_requirements[goal_id]
        requirement_id = str(requirement["id"])
        previous_obligation_owner = previous_obligation_ids.get(obligation_id)
        if previous_obligation_owner is not None and previous_obligation_owner != goal_id:
            _raise_preview(
                "obligation-id-projection",
                "id",
                f"{pointer}/production_obligations",
                goal_id=goal_id,
                obligation_id=obligation_id,
                detail=f"identity already belongs to predecessor goal {previous_obligation_owner}",
            )
        previous_requirement_owner = previous_requirement_ids.get(requirement_id)
        if previous_requirement_owner is not None and previous_requirement_owner != goal_id:
            _raise_preview(
                "requirement-id-projection",
                "id",
                f"{pointer}/deliverable_requirements",
                goal_id=goal_id,
                obligation_id=obligation_id,
                detail=f"identity already belongs to predecessor goal {previous_requirement_owner}",
            )
        predecessor_goal_id = goal_id
        if predecessor is None and links:
            linked_id = str(links[0].get("replaces_obligation_id") or "")
            predecessor_goal_id = previous_obligation_ids.get(linked_id, "")
            predecessor = previous_obligations.get(predecessor_goal_id)
            if predecessor is None:
                _raise_preview(
                    "successor-predecessor-link",
                    "replacement_links",
                    f"{pointer}/production_obligations",
                    goal_id=goal_id,
                    obligation_id=obligation_id,
                    detail="changed-goal continuity must name an exact prior obligation ID",
                )
            if predecessor_goal_id in current_goal_ids:
                _raise_preview(
                    "goal-continuity-conflict",
                    "replacement_links",
                    f"{pointer}/production_obligations",
                    goal_id=goal_id,
                    obligation_id=obligation_id,
                    detail="a successor cannot replace an obligation whose goal survives",
                )
        if predecessor is None:
            continue
        predecessor_id = str(predecessor.get("id") or "")
        if predecessor_id in claimed_predecessor_ids:
            _raise_preview(
                "predecessor-obligation-unique",
                "replacement_links",
                f"{pointer}/production_obligations",
                goal_id=goal_id,
                obligation_id=obligation_id,
            )
        claimed_predecessor_ids.add(predecessor_id)
        predecessor_deliverable = normalize_deliverable(predecessor.get("deliverable"))
        same_projection = (
            predecessor_goal_id == goal_id
            and predecessor_deliverable == obligation["deliverable"]
        )
        predecessor_requirement = previous_requirements.get(predecessor_goal_id)
        if predecessor_requirement is None:
            _raise_preview(
                "previous-pair-closure",
                "deliverable_requirements",
                pointer,
                goal_id=goal_id,
                obligation_id=obligation_id,
            )
        if same_projection:
            if obligation_id != predecessor.get("id") or requirement["id"] != predecessor_requirement.get("id"):
                _raise_preview(
                    "retained-identity-byte-stable",
                    "id",
                    f"{pointer}/production_obligations",
                    goal_id=goal_id,
                    obligation_id=obligation_id,
                    detail="unchanged identity projection must retain both IDs",
                )
            if links != predecessor.get("replacement_links"):
                _raise_preview(
                    "retained-lineage-byte-stable",
                    "replacement_links",
                    f"{pointer}/production_obligations",
                    goal_id=goal_id,
                    obligation_id=obligation_id,
                )
            continue
        if obligation_id == predecessor.get("id") or requirement["id"] == predecessor_requirement.get("id"):
            _raise_preview(
                "changed-deliverable-new-identity",
                "id",
                f"{pointer}/production_obligations",
                goal_id=goal_id,
                obligation_id=obligation_id,
                detail="changed identity projection must mint requirement and obligation successors",
            )
        if len(links) != 1 or links[0].get("replaces_obligation_id") != predecessor.get("id"):
            _raise_preview(
                "successor-predecessor-link",
                "replacement_links",
                f"{pointer}/production_obligations",
                goal_id=goal_id,
                obligation_id=obligation_id,
                detail="successor must contain exactly the mapped predecessor obligation ID",
            )


def _preview_nonempty_string(
    value: Any,
    field_name: str,
    pointer: str,
    *,
    goal_id: str = "",
    obligation_id: str = "",
) -> str:
    if not isinstance(value, str) or not value.strip():
        _raise_preview(
            "nonempty-string",
            field_name,
            pointer,
            goal_id=goal_id,
            obligation_id=obligation_id,
        )
    return value


def _raise_preview_fields(
    value: Mapping[str, Any],
    expected: tuple[str, ...],
    pointer: str,
    *,
    rule_id: str,
    goal_id: str = "",
    obligation_id: str = "",
) -> None:
    actual = set(value)
    wanted = set(expected)
    if actual == wanted:
        return
    missing = sorted(wanted - actual)
    surplus = sorted(actual - wanted)
    field_name = missing[0] if missing else surplus[0]
    _raise_preview(
        rule_id,
        field_name,
        f"{pointer}/{field_name}" if pointer else f"/{field_name}",
        goal_id=goal_id,
        obligation_id=obligation_id,
        detail=f"missing={missing!r} surplus={surplus!r}",
    )


def _raise_preview(
    rule_id: str,
    field_name: str,
    pointer: str,
    *,
    goal_id: str = "",
    obligation_id: str = "",
    detail: str = "",
) -> None:
    raise CanonicalRootPreviewError(
        rule_id,
        field_name,
        pointer,
        goal_id=goal_id,
        obligation_id=obligation_id,
        detail=detail,
    )


def _json_pointer_to_cue_path(pointer: str) -> str:
    if not pointer:
        return "root"
    parts = pointer.lstrip("/").split("/")
    result = "root"
    for part in parts:
        decoded = part.replace("~1", "/").replace("~0", "~")
        result += f"[{decoded}]" if decoded.isdigit() else f".{decoded}"
    return result

def _known_precedent_ids(values: list[str] | tuple[str, ...] | set[str]) -> list[str]:
    return sorted({value for value in values if isinstance(value, str) and value})


def _known_precedent_string_schema(values: list[str] | tuple[str, ...] | set[str]) -> dict[str, Any]:
    # Memento: references to a known finite id set are enums, never free strings.
    # Two live failures came from free-reference transcription: prose-overlap
    # tracing and invented risk anchors.
    ids = _known_precedent_ids(values)
    schema: dict[str, Any] = {"type": "string"}
    if ids and len(ids) <= MAX_SCHEMA_ENUM_REFERENCES:
        schema["enum"] = ids
    return schema


def _closed_precedent_fallback_prompt(label: str, values: list[str] | tuple[str, ...] | set[str]) -> str:
    ids = _known_precedent_ids(values)
    if len(ids) <= MAX_SCHEMA_ENUM_REFERENCES:
        return ""
    ids_text = json.dumps(ids, indent=2, ensure_ascii=True)
    return (
        f"\nValid {label} values are a closed list. Choose exact strings only from this list; "
        f"do not invent, transform, summarize, or infer ids:\n{ids_text}\n"
    )


def _evaluate_candidate_schema(candidate_id: str) -> dict[str, Any]:
    schema = build_evaluate_candidate_schema()
    schema["properties"]["candidate_id"] = _known_precedent_string_schema([candidate_id])
    return schema


def _select_approach_schema(candidate_ids: list[str]) -> dict[str, Any]:
    schema = build_select_approach_schema()
    candidate_id_schema = _known_precedent_string_schema(candidate_ids)
    schema["properties"]["selected_candidate_id"] = deepcopy(candidate_id_schema)
    schema["properties"]["arguments"]["items"]["properties"]["about_candidate_id"] = deepcopy(candidate_id_schema)
    schema["properties"]["rejected"]["items"]["properties"]["candidate_id"] = deepcopy(candidate_id_schema)
    return schema


def _evaluation_repair_schema(missing_candidate_ids: list[str]) -> dict[str, Any]:
    # Per-call specialization of the snapshotted step-5 builder (same pattern as
    # _evaluate_candidate_schema): the repair round may only evaluate the missing ids.
    schema = build_evaluate_candidates_schema()
    schema["properties"]["evaluations"]["items"]["properties"]["candidate_id"] = _known_precedent_string_schema(
        missing_candidate_ids
    )
    return schema


def _surface_risks_schema(target_ids: list[str]) -> dict[str, Any]:
    schema = build_surface_risks_schema()
    schema["properties"]["risks"]["items"]["properties"]["target_id"] = _known_precedent_string_schema(target_ids)
    return schema


# ==========================================================================================
# REVISION CONTINUITY (brief 19, RATIFIED) — node identity contract for reform steps.
# Memento: three faces of one disease — LLM-renamed node ids x non-tracking references:
#   * brief15: reform-regenerated candidate ids met old-generation evaluations;
#   * pomodoro round-2: candidate cross_links pointed at renamed prior_art ids;
#   * DQ round-6: objection anchors dangled after regeneration -> unresolvable -> NAK.
# Cure: id-bearing reform steps receive the previous generation WITH ids as the explicit
# starting point (canon: Gerrit Change-Id survives complete rewrites; kernel subject as
# identifier), and replacing a node requires an explicit declaration (kernel major reroll
# is declared prominently with a changelog). The contract governs identity bookkeeping
# ONLY — never which technical choice to make.
# ==========================================================================================

REVISION_DECLARATION_FIELDS = ("replaces", "replacement_reason")


def _revision_declaration_properties() -> dict[str, Any]:
    # [PROVISIONAL-A1] declaration placement: two flat string fields on each node output.
    # Codex output-schema safe subset: type/description only; value discipline (exact
    # previous id, reason required when set) is enforced by prompt + parser, not schema.
    return {
        "replaces": {
            "type": "string",
            "description": (
                "Exact previous-generation node id this node REPLACES; empty for a "
                "surviving or genuinely new node."
            ),
        },
        "replacement_reason": {
            "type": "string",
            "description": "Short factual reason for the replacement; empty unless replaces is set.",
        },
    }


def _revision_candidate_schema() -> dict[str, Any]:
    # Per-call specialization of the snapshotted builder (same pattern as
    # _evaluate_candidate_schema): formation schemas stay byte-identical.
    schema = build_candidate_approach_schema()
    schema["properties"].update(_revision_declaration_properties())
    schema["required"] = list(schema["required"]) + list(REVISION_DECLARATION_FIELDS)
    return schema


def _revision_prior_art_schema() -> dict[str, Any]:
    schema = build_prior_art_map_schema()
    items = schema["properties"]["patterns"]["items"]
    items["properties"].update(_revision_declaration_properties())
    items["required"] = list(items["required"]) + list(REVISION_DECLARATION_FIELDS)
    return schema


def _revision_surface_risks_schema(target_ids: list[str]) -> dict[str, Any]:
    schema = _surface_risks_schema(target_ids)
    items = schema["properties"]["risks"]["items"]
    items["properties"].update(_revision_declaration_properties())
    items["required"] = list(items["required"]) + list(REVISION_DECLARATION_FIELDS)
    return schema


def _step_revision_baseline(previous_nodes: Any) -> dict[str, Any] | None:
    nodes = [dict(node) for node in previous_nodes or [] if isinstance(node, Mapping)]
    return {"previous_nodes": nodes} if nodes else None


def _revision_contract_text(revision: Mapping[str, Any] | None, id_rule: str = "") -> str:
    if not revision:
        return ""
    previous_text = json.dumps(
        _json_safe(revision.get("previous_nodes", [])), indent=2, sort_keys=True, ensure_ascii=True, default=str
    )
    return (
        "\nREVISION CONTRACT (identity bookkeeping only; it never constrains which technical choice to make):\n"
        "This step is a REVISION of the existing nodes below, not a new founding.\n"
        "- A concept that survives this revision keeps its exact previous node id, even when its content is "
        "heavily rewritten (a Gerrit Change-Id survives a complete rewrite of the same logical concern; a "
        "kernel patch keeps its subject across rerolls).\n"
        "- Replacing a previous node with a different concept must be declared explicitly: set replaces to the "
        "exact previous node id being replaced and replacement_reason to a short factual reason (a kernel "
        "major reroll is declared prominently with a changelog).\n"
        "- A genuinely new node with no predecessor leaves replaces and replacement_reason empty.\n"
        "- Never silently drop a previous id: every previous id must either survive in this output or be "
        "declared replaced by some node's replaces field.\n"
        + id_rule
        + f"\nPrevious-generation nodes (the explicit starting point for this revision):\n{previous_text}\n"
    )



_PRIOR_ART_REVISION_ID_RULE = (
    "- Prior-art node ids derive from the pattern name (prior_art:<slugified-name>): a surviving pattern "
    "keeps its exact previous name; renaming a pattern is a replacement and must be declared with replaces "
    "set to the previous prior_art node id.\n"
)
_PRIOR_ART_STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "that",
    "this",
    "from",
    "into",
    "before",
    "after",
    "only",
    "must",
    "should",
    "would",
    "could",
    "needs",
    "need",
    "use",
    "uses",
    "using",
    "patch_series",
    "view",
    "step",
    "common",
    "problem",
    "proposal",
    "context",
    "impact",
    "users",
    "area",
    "approach",
    "technical",
    "formation",
}

_WORKING_TITLE_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "be",
    "for",
    "from",
    "in",
    "is",
    "it",
    "need",
    "needed",
    "needs",
    "of",
    "on",
    "or",
    "that",
    "the",
    "to",
    "with",
}
_WORKING_TITLE_ABBREVIATIONS = {"ai", "api", "cli", "css", "html", "json", "patch_series", "ui", "ux"}
_WORKING_TITLE_VERBS = (
    "add",
    "build",
    "create",
    "fix",
    "implement",
    "improve",
    "make",
    "replace",
    "support",
    "update",
)

MAX_REGROUNDS = 2

LEGAL_KEYWORDS = (
    "trademark",
    "copyright",
    " ip ",
    "intellectual property",
    "legal",
    "licensing",
    "license",
    "material usage",
    "rights holder",
)

@dataclass
class GroundingResult:
    patch_series_view: dict[str, Any]
    grounding_notes: str = ""
    confident: bool = True
    assumptions: list[str] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)
    failure_mode: str = ""
    ascii_working_slug: str = ""
    # Non-empty when the grounding output never became a candidate view (parse or
    # shape failure): patch_series_view is then the fail-closed FALLBACK scaffold,
    # and retry feedback must carry this reason, not scaffold lint symptoms.
    parse_failure: str = ""


def _require_confirmation() -> bool:
    return os.environ.get("AI_ORG_REQUIRE_CONFIRMATION", "").strip().lower() in {"1", "true", "yes"}


# [PROVISIONAL] Drain budget: one full reference-search window
# (AI_ORG_REFERENCE_SEARCH_TIMEOUT defaults to 180s — the store's own per-search
# bound, see the module header) plus a 60s scheduling/WAL-write margin: a build
# that is mid-search can finish its current term and land its writes, while a
# wedged thread can never hold the pull hostage.
BACKGROUND_BUILD_DRAIN_TIMEOUT_SECONDS = 240.0


def _drain_background_precedent_builds(
    background_build_future: Any,
    patch_series_view: Mapping[str, Any],
    ctx: org_log.RunContext | None,
) -> None:
    """Drain the warmed implementation lane before the pull process exits.

    Memento (requester wiring directive haisen-mo-yoroshiku, 2026-07-05):
    await_background_builds had ZERO callers.
    In production each pull process exits after its work, so undrained
    implementation-lane background builds died mid-flight as orphan threads —
    banked knowledge lost, and the live flake diagnosis (2026-07-05) was exactly
    a straggler background-build thread writing after its test ended. Warmed-lane
    writes must land before the process exits. Drain-or-record: bounded wait,
    then an event naming the undrained build; never block forever, never raise.
    """
    drain_ctx = ctx.child(stage="engineering_precedent_store.drain") if ctx is not None else None
    try:
        engineering_precedent_store.await_background_builds(timeout=BACKGROUND_BUILD_DRAIN_TIMEOUT_SECONDS)
        undrained = (
            background_build_future is not None
            and hasattr(background_build_future, "done")
            and not background_build_future.done()
        )
        if undrained:
            org_log.emit(
                "engineering_precedent_store.background_drain.timeout",
                {
                    "timeout_seconds": BACKGROUND_BUILD_DRAIN_TIMEOUT_SECONDS,
                    # The store derives its research terms during the build, so the
                    # launch identity is what can be named deterministically here.
                    "undrained_build": {
                        "working_title": str(patch_series_view.get("working_title") or ""),
                        "kinds": ["implementation"],
                    },
                },
                ctx=drain_ctx,
                severity="warning",
            )
    except Exception as exc:  # noqa: BLE001 - drain must never take the pull down
        try:
            org_log.emit(
                "engineering_precedent_store.background_drain.failed",
                {"error": str(exc)},
                ctx=drain_ctx,
                severity="warning",
            )
        except Exception:  # noqa: BLE001 - totality: reporting must not take the pull down either
            pass


def _patch_series_with_non_blocking_grounding_uncertainty(grounding: GroundingResult) -> dict[str, Any]:
    patch_series = dict(grounding.patch_series_view)
    if grounding.questions:
        patch_series["open_questions"] = _append_unique_strings(patch_series.get("open_questions"), grounding.questions)
    if grounding.assumptions:
        patch_series["constraints_assumptions"] = _append_unique_strings(patch_series.get("constraints_assumptions"), grounding.assumptions)

    uncertainty_notes: list[str] = []
    if not grounding.confident:
        uncertainty_notes.append("Grounding was not fully confident; best-guess patch series promoted without blocking.")
    if grounding.assumptions:
        uncertainty_notes.append("Non-blocking assumptions: " + "; ".join(grounding.assumptions))
    if grounding.questions:
        uncertainty_notes.append("Non-blocking open questions are preserved in open_questions.")

    if uncertainty_notes:
        existing = str(patch_series.get("grounding_provenance") or "").strip()
        appended = " ".join(uncertainty_notes)
        patch_series["grounding_provenance"] = f"{existing}\n\n{appended}" if existing else appended
    return patch_series


def _append_unique_strings(existing: Any, additions: list[str]) -> list[str]:
    values = list(existing) if isinstance(existing, list) else []
    seen = {value for value in values if isinstance(value, str)}
    for addition in additions:
        if addition and addition not in seen:
            values.append(addition)
            seen.add(addition)
    return values


def _string_list(value: Any) -> list[str]:
    return [item for item in value] if isinstance(value, list) and all(isinstance(item, str) for item in value) else []


def receive(source: str | Path | Mapping[str, Any]) -> dict[str, Any]:
    """Load and validate a raw request from a dict or a JSON file path."""
    if isinstance(source, Mapping):
        data = dict(source)
    elif isinstance(source, (str, Path)):
        path = Path(source)
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise ValueError(f"Could not read request JSON file {path}: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise ValueError(f"Request JSON file {path} is invalid JSON: {exc}") from exc
        if not isinstance(loaded, dict):
            raise ValueError(f"Request JSON file {path} must contain a JSON object.")
        data = loaded
    else:
        raise TypeError("receive(source) expects a dict or a path to a JSON file.")

    if "raw_request" not in data:
        raw_request = _raw_request_from_legacy_entrance(data)
        if raw_request:
            data["raw_request"] = raw_request

    for field in REQUIRED_FIELDS:
        _required_string_field(data, field)

    for field in OPTIONAL_STRING_FIELDS:
        if field in data:
            _optional_string_field(data, field)
    for field in OPTIONAL_LIST_FIELDS:
        if field in data:
            _optional_list_field(data, field)
    if "tech_stack" in data and not validate_tech_stack(
        data["tech_stack"], require_choice=False, user_facing=deliverable_is_user_facing(data)
    ):
        raise ValueError("Request field 'tech_stack' must contain the structured tech stack sub-tags.")
    if "user_experience_requirements" in data and not validate_user_experience_requirements(
        data["user_experience_requirements"]
    ):
        raise ValueError("Request field 'user_experience_requirements' must contain the structured UX sub-tags.")

    return data


def intake(
    source: str | Path | Mapping[str, Any],
    repo: str | Path,
    progress_path: str | Path | None = None,
    ctx: org_log.RunContext | None = None,
    request_provenance: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate and ground a raw request, then promote it only when grounding is confident."""
    _ensure_receive_list(repo, ctx=ctx)
    try:
        request = receive(source)
        return produce_patch_series(
            request,
            repo,
            progress_path=progress_path,
            ctx=ctx,
            request_provenance=request_provenance,
        )
    except ValueError as exc:
        return {"status": "rejected", "error": str(exc)}


def _ensure_receive_list(repo: str | Path, *, ctx: org_log.RunContext | None) -> None:
    """Best-effort five-seconds-ago creation of the shared event medium."""
    try:
        result = mailing_list.ensure_list(repo)
        if result.get("ok"):
            return
        error = str(result.get("error") or "mailing-list genesis failed")
    except Exception as exc:  # noqa: BLE001 - list genesis must never break receive.
        error = str(exc)
    warning_ctx = ctx or org_log.RunContext(repo=repo, stage="receive")
    org_log.debug_emit(
        "mailing_list.genesis.failed",
        {"error": error},
        ctx=warning_ctx,
        severity="warning",
    )


def _post_receive_rfc(
    repo: str | Path,
    *,
    series_slug: str,
    branch: str,
    commit: str,
    ctx: org_log.RunContext,
) -> None:
    """Best-effort publication of the RFC that opens a promoted series."""
    # Memento: intake and normalization stay off-list. The promoted RFC is the
    # first public event for a series, after its technical approach exists.
    try:
        result = mailing_list.post(
            repo,
            kind="rfc",
            subject=series_slug,
            refs={"series": branch, "commit": commit},
            ctx=ctx,
        )
        if result.get("ok"):
            return
        error = str(result.get("error") or "mailing-list RFC post failed")
    except Exception as exc:  # noqa: BLE001 - list publication must never break promotion.
        error = str(exc)
    org_log.debug_emit(
        "mailing_list.rfc.failed",
        {"series": branch, "commit": commit, "error": error},
        ctx=ctx,
        severity="warning",
    )


def _build_receive_precedent_front(
    patch_series: Mapping[str, Any],
    approach_context: Mapping[str, Any],
    run_ctx: org_log.RunContext,
) -> tuple[list[str], Any]:
    """Build the receive precedent front and schedule its existing background tail."""
    with org_log.span(
        "engineering_precedent_store.design_build",
        run_ctx.child(stage="engineering_precedent_store.design"),
    ) as ref_ctx:
        design_build = _call_with_optional_ctx_kw(
            engineering_precedent_store.build_from_patch_series,
            patch_series,
            approach_context,
            kinds=("design",),
            ctx=ref_ctx,
        )
        design_terms = _precedent_terms_from_build_result(design_build)
        org_log.emit(
            "engineering_precedent_store.design_build.completed",
            {
                "terms": design_terms,
                "ok": not (
                    isinstance(design_build, Mapping)
                    and design_build.get("ok") is False
                ),
            },
            ctx=ref_ctx,
        )
    with org_log.span(
        "engineering_precedent_store.implementation_background_scheduled",
        run_ctx.child(stage="engineering_precedent_store.implementation"),
    ):
        background_build_future = _call_with_optional_ctx_kw(
            engineering_precedent_store.start_background_build,
            patch_series,
            approach_context,
            kinds=("implementation",),
            ctx=run_ctx.child(stage="engineering_precedent_store.implementation"),
        )
    return design_terms, background_build_future


def produce_patch_series(
    validated_request: Mapping[str, Any],
    repo: str | Path,
    progress_path: str | Path | None = None,
    ctx: org_log.RunContext | None = None,
    request_provenance: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Ground a request and publish its default generation as one root cohort."""
    repo_path = Path(repo).resolve()
    log_ctx = ctx or org_log.RunContext(
        repo=repo_path,
        request_id=str(validated_request.get("id") or validated_request.get("request_id") or ""),
        stage="receive",
    )
    with org_log.span("patch_series.produce", log_ctx) as run_ctx:
        raw_patch_series = _entrance_request(validated_request)
        with org_log.span("grounding", run_ctx.child(stage="grounding")) as grounding_ctx:
            grounding = _call_ground_with_contract(repo_path, raw_patch_series, grounding_ctx)
        if grounding.violations:
            org_log.emit(
                "grounding.violations",
                {"violations": grounding.violations, "grounding_notes": grounding.grounding_notes},
                ctx=grounding_ctx,
                severity="warning",
            )
            failure_result = {
                "ok": False,
                "status": "needs_work",
                "error": "Grounding contract violations remain unresolved after verification.",
                "failed_step": "grounding",
                "proposed_patch_series": grounding.patch_series_view,
                "grounding_notes": grounding.grounding_notes,
                "violations": grounding.violations,
            }
            if grounding.failure_mode:
                failure_result["failure_mode"] = grounding.failure_mode
            return failure_result
        # Memento: needs_confirmation is default-off because autonomy is the AI Org's differentiator; the
        # confirm-back loop is deferred to the roadmap end. Preserve assumptions/open questions non-blocking.
        if not grounding.confident and _require_confirmation():
            result = {
                "status": "needs_confirmation",
                "proposed_patch_series": grounding.patch_series_view,
                "assumptions": grounding.assumptions,
                "questions": grounding.questions,
                "grounding_notes": grounding.grounding_notes,
            }
            if grounding.violations:
                result["violations"] = grounding.violations
            return result

        patch_series = (
            grounding.patch_series_view
            if grounding.confident
            else _patch_series_with_non_blocking_grounding_uncertainty(grounding)
        )
        approach_context = _technical_approach_context(None, repo_path)
        # The grounded patch series is immutable until approach selection.  Run
        # the independent design-precedent front beside normalize -> constraints,
        # then join in form_technical_approach immediately before prior art.  One
        # worker plus the caller is the entire bounded diamond.
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="receive-precedent-front",
        ) as executor:
            precedent_front_future = executor.submit(
                _build_receive_precedent_front,
                deepcopy(patch_series),
                deepcopy(approach_context),
                run_ctx,
            )
            # Keep the entrypoint from pinning a stack; hardcoded language,
            # environment, or version forecloses engine/platform alternatives.
            approach = form_technical_approach(
                patch_series,
                repo_path,
                context=approach_context,
                precedent_front_future=precedent_front_future,
                progress_path=progress_path,
                ctx=run_ctx.child(stage="approach"),
            )
            # Resolve even when normalization failed so precedent failures keep
            # their original priority over approach needs_work results.
            _design_terms, background_build_future = precedent_front_future.result()
        if approach.get("ok") is False:
            failure_result = {
                "ok": False,
                "status": "needs_work",
                "error": approach.get("error", "Technical Approach formation failed"),
                "failed_step": approach.get("failed_step"),
                "proposed_patch_series": patch_series,
                "grounding_notes": grounding.grounding_notes,
            }
            if approach.get("failure_mode"):
                failure_result["failure_mode"] = approach.get("failure_mode")
            _drain_background_precedent_builds(background_build_future, patch_series, run_ctx)
            return failure_result

        provenance_record = _request_provenance_record(
            validated_request, request_provenance
        )
        producer_root: CanonicalRootTechnicalApproachPreview
        try:
            # Every new formation crosses the final producer-aware boundary.
            # All three canonical members are preflighted before the public
            # writer may create or move the ref; there is no legacy opt-out.
            _producer_preparation_transition(approach)
            producer_root = prepare_producer_aware_formation(
                approach,
                repo_path,
                cover_letter=patch_series,
                request_provenance=provenance_record,
                ctx=run_ctx.child(stage="producer_aware_formation"),
            )
            approach["technical_approach"] = dict(producer_root.technical_approach)
            if not producer_root.complete_cohort:
                raise ValueError(
                    "producer-aware formation requires the exact root cohort v2"
                )
        except (CanonicalRootPreviewError, CodecFailure, ValueError, TypeError) as exc:
            _drain_background_precedent_builds(
                background_build_future, patch_series, run_ctx
            )
            return {
                "ok": False,
                "status": "needs_work",
                "error": str(exc),
                "failed_step": "producer_aware_formation",
                "proposed_patch_series": patch_series,
            }

        patch_series_id = _branch_slug_from_grounding(grounding, patch_series)
        branch = _unique_patch_series_branch(repo_path, patch_series_id)
        patch_series_id = branch.removeprefix("ai-org/patch-series/")
        base = _default_branch(repo_path)
        spine_artifacts = spine.derive_artifacts(
            patch_series,
            approach["technical_approach"],
            context=approach_context,
        )
        if spine_artifacts:
            org_log.emit(
                "spine.artifacts.derived",
                {"paths": sorted(spine_artifacts), "patch_series_id": patch_series_id},
                ctx=run_ctx.child(stage="spine", patch_series_id=patch_series_id),
            )
        with org_log.span("patch_series.promote", run_ctx.child(stage="promote", patch_series_id=patch_series_id)):
            written = _write_patch_series_branch(
                repo_path,
                branch,
                base,
                patch_series,
                patch_series_path=patch_series_bodies.COVER_PATH,
                extra_files={
                    patch_series_bodies.PROVENANCE_PATH: provenance_record,
                    patch_series_bodies.ROOT_APPROACH_PATH: producer_root.canonical_cue,
                    **approach.get("external_files", {}),
                    **spine_artifacts,
                },
                commit_message=f"patch_series: receive {patch_series['working_title']}",
            )
            _post_receive_rfc(
                repo_path,
                series_slug=patch_series_id,
                branch=branch,
                commit=written["commit"],
                ctx=run_ctx.child(stage="promote", patch_series_id=patch_series_id),
            )
            if spine_artifacts:
                org_log.emit(
                    "spine.artifacts.emitted",
                    {"paths": sorted(spine_artifacts), "branch": branch, "commit": written["commit"]},
                    ctx=run_ctx.child(stage="spine", patch_series_id=patch_series_id),
                )
            org_log.emit(
                "patch_series.promoted",
                {"patch_series_id": patch_series_id, "branch": branch, "commit": written["commit"]},
                ctx=run_ctx.child(stage="promote", patch_series_id=patch_series_id),
            )
        _drain_background_precedent_builds(background_build_future, patch_series, run_ctx)
        return {
            "ok": True,
            "status": "promoted",
            "id": patch_series_id,
            "branch": branch,
            "commit": written["commit"],
            "technical_approach_path": patch_series_bodies.ROOT_APPROACH_PATH,
            "spine_artifact_paths": sorted(spine_artifacts),
            "grounding_notes": grounding.grounding_notes,
        }


REFORM_STEP_ORDER = [
    "problem",
    "constraints",
    "prior_art",
    "candidates",
    "decision",
    "implementation",
    "domain_specification",
    "patch_plan",
    "risks",
]


IMPLEMENTATION_EXPERIENCE_OBJECTION_PREFIX = "implementation-experience:"


def implementation_experience_pending(repo: str | Path, patch_series_id_or_branch: str) -> bool:
    """Unprocessed implementation-experience reports exist on the series branch."""
    repo_path = Path(repo).resolve()
    branch = _patch_series_branch(patch_series_id_or_branch)
    return bool(_unprocessed_experience_reports(repo_path, branch))


def _unprocessed_experience_reports(repo: Path, branch: str) -> list[dict[str, Any]]:
    gate_module = importlib.import_module("ai_org.patchwork_queue.patch_series_gate")
    raw = git_wrapper.show_file(repo, branch, gate_module.IMPLEMENTATION_EXPERIENCE_REPORT_PATH)
    legacy_raw = git_wrapper.show_file(
        repo, branch, gate_module.LEGACY_IMPLEMENTATION_EXPERIENCE_REPORT_PATH
    )
    if raw is not None and legacy_raw is not None:
        raise contributor_handoff.AmbiguousRepresentation(
            "implementation experience has both canonical and legacy representations"
        )
    raw = raw if raw is not None else legacy_raw
    if raw is None:
        return []
    try:
        parsed = contributor_handoff.parse_experience(raw)
    except Exception:
        return []
    entries = parsed.get("implementation_experience_reports") if isinstance(parsed, Mapping) else None
    return [
        dict(entry)
        for entry in entries or []
        if isinstance(entry, Mapping) and not entry.get("processed_in_author_version")
    ]


def _implementation_experience_objections(reports: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Objection-grade records from experience reports (amendment 5: pure routing).

    Memento — the full circle this closes: paper decides -> the question routes
    to code -> the executable check answers it -> on "no" with direction
    contamination, Plan B returns HERE as reviewable evidence -> brief25
    targeted revision seeded on exactly the named nodes -> brief19-D delta
    review of exactly Plan B's consequences -> back to code. Every field below
    is committed evidence assembled verbatim (check, failure output, Plan B);
    code fabricates no judgment.
    """
    objections: list[dict[str, Any]] = []
    for report in reports:
        objection_id = f"{IMPLEMENTATION_EXPERIENCE_OBJECTION_PREFIX}{report.get('objection_id')}"
        anchors = [node_id for node_id in report.get("invalidated_node_ids", []) if isinstance(node_id, str)]
        if not anchors:
            continue
        claim = (
            "Implementation experience: executable check "
            f"{str(report.get('executable_check') or '')!r} FAILED "
            f"({str(report.get('check_failure_evidence') or '')[:500]}). "
            f"Proposal under consideration (Plan B, verbatim): {str(report.get('contingency_plan') or '')}"
        )
        objections.append(
            {
                "objection_id": objection_id,
                "anchor_node_ids": anchors,
                "axis": str(report.get("axis") or "approach"),
                "type": "blocking",
                "claim": claim,
                "evidence": [
                    {
                        "source_type": "repo_fact",
                        "citation": "implementation-experience-report.json on the series branch",
                        "consulted_terms": [],
                    }
                ],
                "impact": "A committed direction node is invalidated by running-code evidence.",
                "requested_author_action": "revise_subtree",
                "resolution_authority": "author",
                "status": "open",
            }
        )
    return objections


def reform_patch_series(
    repo: str | Path,
    patch_series_id_or_branch: str,
    *,
    ctx: org_log.RunContext | None = None,
) -> dict[str, Any]:
    """Author-side v2 re-formation for a needs-revision patch series on the same branch.

    Memento: reviewers only object; the author reworks and reposts v2 on the same
    patch series branch with a Changes-since body. Review round records are immutable, so
    author answers are appended as separate response records.
    """
    repo_path = Path(repo).resolve()
    branch = _patch_series_branch(patch_series_id_or_branch)
    patch_series_id = branch.removeprefix("ai-org/patch-series/")
    log_ctx = ctx or org_log.RunContext(
        repo=repo_path,
        patch_series_id=patch_series_id,
        stage="patch_series.reform",
    )
    root_snapshot = patch_series_bodies.classify_root_generation(repo_path, branch)
    if not root_snapshot.lifecycle_ready:
        return {
            "ok": False,
            "status": root_snapshot.disposition,
            "branch": branch,
            "frozen_oid": root_snapshot.frozen_oid,
            "error": root_snapshot.diagnostic,
            "root_generation": root_snapshot.identity(),
            **root_snapshot.identity(),
        }
    latest = _latest_review_round_record(repo_path, patch_series_id)
    if latest is None:
        return {"ok": False, "status": "no_review_round", "error": f"No review round found for {patch_series_id}."}

    # Memento: the round record consumed here may carry human requester
    # interventions — "[REQUESTER NOTE (...)]" text appended inside a claim
    # (guidance that reaches the revision prompt verbatim) and demotions via
    # resolution_authority="requester". See the requester_assumptions module
    # docstring for the two intervention ports and their rules.
    open_blocking = requester_assumptions.open_blocking_objections(latest)
    experience_reports = _unprocessed_experience_reports(repo_path, branch)
    experience_objections = _implementation_experience_objections(experience_reports)
    open_blocking = open_blocking + experience_objections
    if not open_blocking:
        return {"ok": False, "status": "no_open_blocking_objections", "round": latest.get("round")}
    requester_blocking = requester_assumptions.requester_authority_objections(open_blocking)
    author_blocking = [
        objection for objection in open_blocking if objection.get("resolution_authority") != "requester"
    ]

    try:
        patch_series_view = root_snapshot.cover_letter()
        approach = root_snapshot.technical_approach()
    except (ValueError, TypeError) as exc:
        return {
            "ok": False,
            "status": "needs_work",
            "branch": branch,
            "error": str(exc),
        }
    if not isinstance(patch_series_view, dict) or not _is_patch_series_view(patch_series_view):
        return {"ok": False, "status": "needs_work", "error": f"{branch}:patch-series-cover-letter.json is not a grounded patch series view."}
    if not isinstance(approach, dict):
        return {"ok": False, "status": "needs_work", "error": f"{branch}:technical-approach-plan.json is missing or invalid."}

    previous_patch_series = _json_safe(patch_series_view)
    previous_approach = _json_safe(approach)
    affected_steps = _affected_reform_steps(approach, author_blocking)
    # Brief 22 amendment: legacy inherited danglings are healed EXCLUSIVELY by the
    # bounded reconciliation round at the continuity gate (reactive by error type,
    # gadget doctrine). Brief 21's in-prompt escape and owning-step force-inclusion
    # were proactive scaffolding inside saturated prompts and drowned (725KB live
    # prompt, zero declarations twice); both are removed.
    revised_patch_series, requester_assumption_entries, _requester_assumptions_changed = (
        requester_assumptions.record_requester_authority_assumptions(
            patch_series_view,
            approach,
            requester_blocking,
        )
    )
    if any(str(objection.get("axis")) == "need" for objection in author_blocking):
        grounded = _ground_with_contract(repo_path, _patch_series_with_reform_feedback(revised_patch_series, author_blocking))
        if grounded.violations:
            return {
                "ok": False,
                "status": "needs_work",
                "error": "patch series re-grounding contract violations remain unresolved.",
                "failed_step": "patch_series_grounding",
                "violations": grounded.violations,
            }
        revised_patch_series = grounded.patch_series_view
        if requester_blocking:
            revised_patch_series, reapplied, _reapplied_changed = (
                requester_assumptions.record_requester_authority_assumptions(
                    revised_patch_series,
                    approach,
                    requester_blocking,
                )
            )
            if not requester_assumption_entries:
                requester_assumption_entries = reapplied
    reform = _reform_technical_approach(
        repo_path,
        revised_patch_series,
        approach,
        author_blocking,
        affected_steps,
        branch=branch,
        log_ctx=log_ctx,
    )
    if not reform.get("ok", False):
        return reform
    revised_approach = reform["technical_approach"]
    # Brief 21: root-held references (top-level cross_links) have no owning step;
    # remap dead endpoints through the replacement map BEFORE the continuity gate.
    # The gate stays fail-closed for anything the map cannot repair.
    revised_approach = _remap_root_reference_ids(previous_approach, revised_approach)
    # Memento: reform re-derives every view field that formation derives from the
    # decision (formation seam: _fill_ai_deliberated_tech_stack directly after
    # select_approach). A stale derived contract makes the reviewer deterministically
    # re-object and the series can never converge — live loop: v3e rounds 3->4 kept
    # objecting "decision selects React but tech_stack still says Phaser 3" while the
    # decision drifted Phaser->React->Svelte (2026-07-05). Derived-field audit
    # 2026-07-05: this fill is the ONLY formation mutation of patch_series_view after
    # select_approach.
    if {"candidates", "decision"} & set(affected_steps):
        reformed_components = _approach_components(revised_approach)
        _fill_ai_deliberated_tech_stack(
            revised_patch_series,
            reformed_components["selected"],
            reformed_components["candidates"],
        )
    producer_root: CanonicalRootTechnicalApproachPreview
    try:
        preparation_transition = _producer_preparation_transition(
            revised_approach,
            previous=previous_approach,
        )
        provenance = patch_series_bodies.read_request_provenance(
            repo_path,
            root_snapshot.frozen_oid or branch,
            root_snapshot=root_snapshot,
        )
        if provenance is None:
            # Some untouched historical roots predate durable request
            # provenance. Explicit reform is the migration boundary, so bind a
            # deterministic provenance member to the admitted historical cover
            # instead of publishing only two canonical members.
            provenance = _request_provenance_record(
                patch_series_view,
                {
                    "request_id": f"historical-reform:{patch_series_id}",
                    "payload": patch_series_view,
                },
            )
        preview_cohort = (
            {
                "cover_letter": revised_patch_series,
                "request_provenance": provenance,
            }
            if isinstance(provenance, Mapping)
            else {}
        )
        if preparation_transition == "reform":
            producer_root = prepare_producer_aware_reform(
                {
                    "technical_approach": revised_approach,
                    "steps": reform.get("steps", {}),
                },
                previous_approach,
                repo_path,
                **preview_cohort,
                ctx=log_ctx.child(stage="producer_aware_reform"),
            )
        else:
            # An explicit reform of historical state is the only operation
            # that migrates it; untouched refs continue to dispatch as legacy.
            producer_root = prepare_producer_aware_formation(
                {"technical_approach": revised_approach},
                repo_path,
                **preview_cohort,
                ctx=log_ctx.child(stage="producer_aware_reform"),
            )
        revised_approach = dict(producer_root.technical_approach)
    except (CanonicalRootPreviewError, CodecFailure, ValueError, TypeError) as exc:
        return {
            "ok": False,
            "status": "needs_work",
            "failed_step": "producer_aware_reform",
            "error": str(exc),
        }
    patch_series_changed = _canonical_json(revised_patch_series) != _canonical_json(previous_patch_series)
    changed_nodes = reform["changed_nodes"]
    changed = patch_series_changed or _canonical_json(revised_approach) != _canonical_json(previous_approach)
    if patch_series_changed:
        changed_nodes = [patch_series_bodies.LEGACY_COVER_PATH, *changed_nodes]

    # Memento (brief 19): revision continuity gate, fail-closed BEFORE anything is
    # committed. A reformed tree whose node-id references or open objection anchors
    # no longer resolve (and were not declared replaced) is the exact disease that
    # produced brief15, pomodoro round-2, and the DQ round-6 NAK. The violation list
    # names the exact references; nothing here is a model judgment.
    continuity_violations = _revision_continuity_violations(previous_approach, revised_approach, open_blocking)
    reconciliation_no_successor: list[dict[str, str]] = []
    if continuity_violations:
        # Memento (brief 22): a conditional instruction buried in a saturated prompt
        # is not a contract — the pomodoro v4 retry carried the UNRESOLVED block
        # inside a 725,666-char prompt and produced zero declarations twice. ONE
        # bounded reconciliation round puts the identity judgment in a tiny window
        # (reviewer-side twin: brief 20); a second failure surfaces the existing
        # typed needs_work with the exact references.
        reconciled = _reconcile_dangling_references(
            repo_path, previous_approach, revised_approach, open_blocking, log_ctx=log_ctx
        )
        if reconciled is not None:
            revised_approach, reconciliation_no_successor = reconciled
            continuity_violations = _revision_continuity_violations(
                previous_approach, revised_approach, open_blocking
            )
    if continuity_violations:
        failure = {
            "ok": False,
            "status": "needs_work",
            "error": "revision continuity violations: " + "; ".join(continuity_violations),
            "failed_step": "revision_continuity",
            "violations": continuity_violations,
        }
        if reconciliation_no_successor:
            # Auditable even though nothing commits on this path: the delta ledger
            # only lands with a successful reform, so the no-successor judgments
            # ride the needs_work result record here (and the ledger field below
            # when a later round succeeds).
            failure["reconciliation_no_successor"] = reconciliation_no_successor
        return failure

    previous_version = _review_round_number(latest)
    next_version = previous_version + 1
    answers = [
        *_requester_assumption_answers(requester_assumption_entries),
        *_author_answers(author_blocking, changed_nodes, changed),
    ]
    body = _changes_since_body(previous_version, answers)
    if not producer_root.complete_cohort:
        return {
            "ok": False,
            "status": "needs_work",
            "failed_step": "producer_aware_reform",
            "error": "producer-aware reform requires the exact root cohort v2",
        }
    files = dict(reform.get("external_files", {}))
    # Explicit reform replaces the complete root cohort in one commit. Scope
    # is removed in that same commit because it is bound to the predecessor;
    # the network gate must publish successor scope before authorability opens.
    files.update(producer_root.cohort_files)
    if patch_series_changed:
        changed_nodes = [
            patch_series_bodies.COVER_PATH
            if node == patch_series_bodies.LEGACY_COVER_PATH
            else node
            for node in changed_nodes
        ]
    response_path, response_record = _author_response_record(
        patch_series_id,
        latest,
        next_version,
        answers,
        affected_steps,
        changed_nodes,
        requester_assumption_entries,
    )
    files[response_path] = response_record
    routed_questions = [
        closure for closure in reform.get("closed_questions", []) if closure.get("closure_kind") == "routed_to_patch"
    ]
    if routed_questions:
        # Brief 28 item 3: the question-shaped must-answer record reuses the
        # brief 27 obligation artifact directly (objection-independent variant).
        # Ratification rides the cycle: patch authoring starts only after
        # direction-ok, so an unobjected closure is group-ratified by then.
        gate_module = importlib.import_module("ai_org.patchwork_queue.patch_series_gate")
        files[gate_module.PATCH_MUST_ANSWER_QUESTIONS_PATH] = _questions_artifact_with_routed_closures(
            repo_path, branch, routed_questions, next_version
        )
    if experience_reports:
        # Amendment 5: consumed experience reports are marked processed in the
        # SAME reform commit (never reconsumed; the trail stays auditable).
        files[importlib.import_module("ai_org.patchwork_queue.patch_series_gate").IMPLEMENTATION_EXPERIENCE_REPORT_PATH] = {
            "implementation_experience_reports": [
                {**report, "processed_in_author_version": next_version} for report in experience_reports
            ]
        }
    # The machine-readable revision delta ledger is committed WITH the reform.
    # It is binding author history and deterministic bookkeeping, never reviewer
    # paper; its path lives beside the round records (kernel vocabulary: delta).
    review_module = importlib.import_module("ai_org.patchwork_queue.review")
    revision_delta_path = review_module.revision_delta_record_path(previous_version)
    files[revision_delta_path] = _revision_delta_record(
        patch_series_id,
        previous_version,
        next_version,
        previous_approach,
        revised_approach,
        open_blocking,
        reconciliation_no_successor=reconciliation_no_successor,
        deferral_proposals=reform.get("deferral_proposals", []),
        closed_questions=reform.get("closed_questions", []),
        defenses=reform.get("defenses", []),
    )
    if isinstance(files.get(patch_series_bodies.COVER_PATH), Mapping):
        prepared_root = patch_series_bodies.prepare_patch_series_update(
            repo_path, root_snapshot.frozen_oid or branch, files[patch_series_bodies.COVER_PATH]
        )
        files = {**files, **prepared_root.files()}
    aggregate = review_bodies.prepare_reform_tree(files)
    publishes_root_v2 = all(
        path in producer_root.cohort_files
        for path in patch_series_bodies.ROOT_COHORT_V2_PATHS
    )
    written = git_wrapper.commit_files(
        repo_path,
        branch,
        aggregate.files,
        subject=f"patch_series v{next_version}: {revised_patch_series['working_title']}",
        body=body,
        allow_empty=True,
        remove_paths=(
            (
                patch_series_bodies.LEGACY_COVER_PATH,
                patch_series_bodies.LEGACY_PROVENANCE_PATH,
                patch_series_bodies.LEGACY_ROOT_APPROACH_PATH,
                patch_series_bodies.SCOPE_DECOMPOSITION_PATH,
            )
            if publishes_root_v2
            else ()
        ),
    )
    return {
        "ok": True,
        "status": "reformed",
        "id": patch_series_id,
        "branch": branch,
        "commit": written["commit"],
        "version": next_version,
        "review_round": previous_version,
        "affected_steps": affected_steps,
        "changed_nodes": changed_nodes,
        "author_response_path": response_path,
        "revision_delta_path": revision_delta_path,
        "root_generation": root_snapshot.identity(),
    }


revise_patch_series = reform_patch_series


def _patch_series_branch(patch_series_id_or_branch: str) -> str:
    if patch_series_id_or_branch.startswith("refs/heads/"):
        return patch_series_id_or_branch.removeprefix("refs/heads/")
    if patch_series_id_or_branch.startswith("ai-org/patch-series/"):
        return patch_series_id_or_branch
    return f"ai-org/patch-series/{patch_series_id_or_branch}"


def _latest_review_round_record(repo: Path, patch_series_id: str) -> dict[str, Any] | None:
    review_module = importlib.import_module("ai_org.patchwork_queue.review")

    return review_module.latest_review_round_record(repo, patch_series_id)


def _review_round_number(record: Mapping[str, Any]) -> int:
    try:
        return int(record.get("round", 0))
    except (TypeError, ValueError):
        return 0


def _read_json_from_branch(repo: Path, branch: str, path: str) -> Any:
    if patch_series_bodies.is_root_cover_path(path):
        return patch_series_bodies.read_cover_letter(repo, branch)
    raw = git_wrapper.show_file(repo, branch, path)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _patch_series_with_reform_feedback(patch_series_view: Mapping[str, Any], objections: list[Mapping[str, Any]]) -> dict[str, Any]:
    revised = _patch_series_to_view(dict(patch_series_view))
    feedback = "; ".join(
        f"{objection.get('objection_id')}: {objection.get('claim')}"
        for objection in objections
        if str(objection.get("axis")) == "need"
    )
    if feedback:
        revised["grounding_provenance"] = (
            str(revised.get("grounding_provenance", "")).strip()
            + f" Review feedback for author re-formation: {feedback}"
        ).strip()
    return revised


def _affected_reform_steps(
    approach: Mapping[str, Any],
    objections: list[Mapping[str, Any]],
) -> list[str]:
    node_steps = _node_step_map(approach)
    affected: list[str] = []
    for objection in objections:
        anchors = objection.get("anchor_node_ids", [])
        if not isinstance(anchors, list):
            anchors = []
        for anchor in anchors:
            step = node_steps.get(str(anchor)) or _step_for_node_id(str(anchor))
            if step not in affected:
                affected.append(step)
        if str(objection.get("axis")) == "need" and "problem" not in affected:
            affected.insert(0, "problem")
    return [step for step in REFORM_STEP_ORDER if step in affected]


def _node_step_map(value: Any, inherited_step: str = "problem") -> dict[str, str]:
    # Memento (brief 31): a node's step comes from its TREE POSITION - the
    # step inherited through the container key path - never from guessing at
    # its id prefix. Candidate ids are unprefixed LLM slugs by long-standing
    # vocabulary, so the previous per-node _step_for_node_id override silently
    # rerouted every real candidate node to "problem": the identical
    # candidate-anchored blocking objection survived reform rounds 4 AND 5
    # untouched because "candidates" never entered affected_steps (run 4,
    # 2026-07-05). Tree position outranks name guessing - the network is the
    # truth-form. _step_for_node_id survives ONLY as the fallback for anchor
    # ids that are not in the tree at all (pruned nodes, dead references).
    node_map: dict[str, str] = {}
    if isinstance(value, Mapping):
        node_id = value.get("id")
        if isinstance(node_id, str) and node_id:
            node_map[node_id] = inherited_step
        for key, child in value.items():
            child_step = _step_for_tree_key(str(key), inherited_step)
            node_map.update(_node_step_map(child, child_step))
    elif isinstance(value, list):
        for child in value:
            node_map.update(_node_step_map(child, inherited_step))
    return node_map


def _step_for_tree_key(key: str, inherited_step: str) -> str:
    return {
        "constraints": "constraints",
        "prior_art": "prior_art",
        # The question node is the candidates-step container: its own id
        # (question:approach) and everything under it that is not claimed by
        # a more specific key below belongs to the candidates step.
        "question": "candidates",
        "candidates": "candidates",
        "evaluation": "candidates",
        "decision": "decision",
        "implementation": "implementation",
        "domain_specification": "domain_specification",
        "patch_plan": "patch_plan",
        "risks": "risks",
    }.get(key, inherited_step)


def _step_for_node_id(node_id: str) -> str:
    # Memento (brief 31): FALLBACK ONLY - for anchor ids that do not resolve
    # in the tree-position map (pruned nodes, dead references). Never let a
    # prefix guess override tree position for a node that exists in the tree.
    if node_id == "problem" or node_id.startswith("goal:"):
        return "problem"
    if node_id.startswith("constraint:"):
        return "constraints"
    if node_id.startswith("prior_art:"):
        return "prior_art"
    if node_id == "question:approach" or node_id.startswith("candidate:") or node_id.startswith("evaluation:"):
        return "candidates"
    if node_id.startswith("decision:"):
        return "decision"
    if node_id.startswith("implementation:"):
        return "implementation"
    if node_id == "domain_specification" or node_id.startswith("domain_specification:"):
        return "domain_specification"
    if node_id.startswith("patch_plan:"):
        return "patch_plan"
    if node_id.startswith("risk:"):
        return "risks"
    # Unprefixed ids are candidate slugs by the long-standing id vocabulary
    # (brief 21 routes unprefixed reference families to candidates for the
    # same reason). Defaulting to "problem" here converted candidate
    # objections into problem-step churn for two rounds (run 4, 2026-07-05).
    return "candidates"


def _author_self_history(repo: Path, branch: str) -> dict[str, Any]:
    """Project the author's committed response and decision history oldest first."""
    # Memento: the paper is binding (branch records); the list is thread memory,
    # never a decision source. Reviewers stay blank by design, while the author
    # reads its own complete series history so candidate/framework choices do not
    # drift between rounds. Entries are bounded by the series and never truncated.
    review_module = importlib.import_module("ai_org.patchwork_queue.review")
    round_numbers = sorted(
        {
            _review_round_number(record)
            for record in review_module.review_round_records(repo, branch)
            if _review_round_number(record) > 0
        }
    )
    rounds: list[dict[str, Any]] = []
    for round_number in round_numbers:
        delta_path = review_module.revision_delta_record_path(round_number)
        delta = review_bodies.read_record(
            repo, branch, delta_path, review_bodies.REVISION_DELTA_RECIPE
        )
        if not isinstance(delta, Mapping):
            continue
        reform_commit = git_wrapper.path_last_commit(repo, branch, delta_path)
        if reform_commit is None:
            reform_commit = git_wrapper.path_last_commit(
                repo, branch, review_bodies.legacy_path(delta_path)
            )
        if reform_commit is None:
            continue
        historical_approach = _read_json_from_branch(
            repo, reform_commit, "technical-approach-plan.json"
        )
        if not isinstance(historical_approach, Mapping):
            continue
        problem = historical_approach.get("problem")
        question = problem.get("question") if isinstance(problem, Mapping) else None
        candidates = question.get("candidates") if isinstance(question, Mapping) else []
        decision = question.get("decision") if isinstance(question, Mapping) else {}
        decision_quote = (
            {
                key: _json_safe(value)
                for key, value in decision.items()
                if key not in {"implementation", "risks"}
            }
            if isinstance(decision, Mapping)
            else {}
        )
        rounds.append(
            {
                "review_round": round_number,
                "revision_responses": [
                    _json_safe(response)
                    for response in delta.get("objection_responses", [])
                    if isinstance(response, Mapping)
                ],
                "candidate_framework_decisions": {
                    "candidates": [
                        _json_safe(candidate)
                        for candidate in candidates
                        if isinstance(candidate, Mapping)
                    ]
                    if isinstance(candidates, list)
                    else [],
                    "decision": decision_quote,
                },
            }
        )
    return {
        "binding_source": branch,
        "window": "all completed author revisions in this patch series; complete entries, oldest first",
        "rounds": rounds,
    }


def _reform_technical_approach(
    repo: Path,
    patch_series_view: dict[str, Any],
    approach: Mapping[str, Any],
    objections: list[Mapping[str, Any]],
    affected_steps: list[str],
    *,
    branch: str = "",
    log_ctx: org_log.RunContext | None = None,
) -> dict[str, Any]:
    if not affected_steps:
        return {
            "ok": True,
            "technical_approach": dict(approach),
            "changed_nodes": [],
            "steps": {},
        }
    components = _approach_components(approach)
    # Memento: rustc fix-it pattern (ratified). The reviewer's objections travel back
    # to the author WITH applicable gadget hints attached; the reviewer itself is never
    # touched. Matching runs code-side on typed enum fields only, after the review
    # response is parsed; hints are non-binding structure info (tool + what it
    # generates), never advice on what the answer should be.
    hinted_objections = _objections_with_gadget_hints(objections)
    author_self_history = (
        _author_self_history(repo, branch)
        if branch
        else {
            "binding_source": "",
            "window": "no committed series history was supplied to this compatibility call",
            "rounds": [],
        }
    )
    context = _technical_approach_context(
        {
            "review_feedback": _json_safe(hinted_objections),
            "review_feedback_by_step": _feedback_by_step(approach, hinted_objections, affected_steps),
            "AUTHOR SELF-HISTORY": author_self_history,
            # Brief 34: deliverable shape as DATA for candidate validators/prompts.
            "deliverable_user_facing": _deliverable_is_user_facing(patch_series_view),
            **_review_gadget_hint_lines(hinted_objections),
        },
        repo,
    )
    changed_nodes: list[str] = []
    preparation_steps: dict[str, Any] = {}
    external_files: dict[str, Any] = {}
    # Brief 25: derive targeted revision windows mechanically from the COMMITTED
    # tree (targets are committed node ids the objections anchor).
    revision_plans = _targeted_revision_plan(approach, hinted_objections, affected_steps)
    deferral_proposals: list[dict[str, Any]] = []
    closed_questions: list[dict[str, Any]] = []
    defenses: list[dict[str, Any]] = []

    for step in affected_steps:
        before = _canonical_json(_component_node_snapshot(components, step))
        evaluations_before = components["evaluations"] if step == "decision" else None
        step_ctx = log_ctx.child(step=f"reform.{step}") if log_ctx is not None else None
        result = _run_reform_step(
            step, repo, patch_series_view, components, context,
            log_ctx=step_ctx,
            revision_plan=revision_plans.get(step),
            deferral_collector=deferral_proposals,
            closure_collector=closed_questions,
            defense_collector=defenses,
        )
        failure = _technical_approach_step_failure(step, result)
        if failure:
            return {"ok": False, "status": "needs_work", **failure}
        if step == "problem":
            # Producer preparation must compare the assembled reform against
            # the exact normalization result, not repeat the assembly to itself.
            preparation_steps["normalize_problem"] = deepcopy(dict(result))
        if step == "domain_specification":
            result = _externalize_domain_specification(result, external_files)
        _store_reform_step_result(components, step, result)
        if step == "decision":
            # Memento: selection consumed the refreshed evaluation matrix, so the
            # tree must carry it too — and the mutation must be VISIBLE: pruned,
            # added, and updated evaluation nodes go into changed_nodes so the
            # round record and commit reflect them (brief 16 discipline: a
            # persisted-but-unaccounted fix is silent). An unaudited evaluation is
            # a deterministic provide_evidence objection (live: round-3 objected
            # "the comparative evaluation is not auditable", 2026-07-05).
            changed_nodes.extend(_changed_evaluation_node_ids(evaluations_before, components["evaluations"]))
        if _canonical_json(_component_node_snapshot(components, step)) != before:
            changed_nodes.extend(_changed_node_ids_for_step(components, step))

    if closed_questions:
        # Memento (brief 28, three round-6 NAKs 2026-07-05): append-only open
        # questions turned the autonomy canon into a contradiction generator
        # under review dynamics — a DECIDED question that cannot retire is an
        # unresolvable objection (live: skip/reset was decided, the question
        # 「both?」 could never leave, the reviewer correctly flagged the
        # normative-vs-open contradiction every round until cap-NAK). Closure
        # is judgment (declared above, byte-matched); retirement is code: the
        # mature document DROPS the question (W3C formal-address, Rust
        # tracking-issue closure), and the disposition record lives in the
        # revision-delta ledger — our minutes/disposition equivalent.
        retired = {closure["question"] for closure in closed_questions}
        problem = components["problem"]
        problem["open_questions"] = [
            question for question in problem.get("open_questions", []) if question not in retired
        ]
        changed_nodes.append("problem")
    technical_approach = _assemble_from_components(components)
    return {
        "ok": True,
        "technical_approach": technical_approach,
        "changed_nodes": _dedupe(changed_nodes),
        "steps": preparation_steps,
        "external_files": external_files,
        "deferral_proposals": deferral_proposals,
        "closed_questions": closed_questions,
        "defenses": defenses,
    }


def _feedback_by_step(
    approach: Mapping[str, Any],
    objections: list[Mapping[str, Any]],
    steps: list[str],
) -> dict[str, list[dict[str, Any]]]:
    node_steps = _node_step_map(approach)
    routed = {step: [] for step in steps}
    for objection in objections:
        objection_steps: set[str] = set()
        anchors = objection.get("anchor_node_ids", [])
        if isinstance(anchors, list):
            objection_steps.update(node_steps.get(str(anchor)) or _step_for_node_id(str(anchor)) for anchor in anchors)
        if str(objection.get("axis")) == "need":
            objection_steps.add("problem")
        for step in steps:
            if step in objection_steps:
                routed[step].append(dict(objection))
    return routed


def _objections_with_gadget_hints(objections: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Attach optional gadget_hint to objections on the review return path.

    Memento: mechanical reading only. The registry matches the objection's typed
    enum fields (requested_author_action etc.); claim/evidence/impact prose is
    invisible to matching by construction, so a prose-only objection stays
    hint-free — silence, never a guess. Review round records on git are never
    rewritten; hints decorate only the copies handed to the author's reform run.
    """
    annotated: list[dict[str, Any]] = []
    for objection in objections:
        item = dict(objection)
        matched = structure_gadgets.match_gadget_hints(item)
        if matched.get("ok") and matched["hints"]:
            item["gadget_hint"] = matched["hints"][0]
        annotated.append(item)
    return annotated


def _review_gadget_hint_lines(objections: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Render matched hints as short structural lines for the author's reform brief.

    One line per matched gadget: tool name + what it generates. No skeleton is
    materialized in the reform prompt — skeletons still appear only in repair
    rounds after typed failures. Empty when nothing matched, so the reform
    context stays byte-identical to the pre-hint shape.
    """
    lines: list[str] = []
    for objection in objections:
        hint = objection.get("gadget_hint")
        if not isinstance(hint, Mapping):
            continue
        rendered = structure_gadgets.render_gadget_hint_line(hint)
        if rendered.get("ok") and rendered["line"] not in lines:
            lines.append(rendered["line"])
    return {"review_gadget_hints": lines} if lines else {}


def _approach_components(approach: Mapping[str, Any]) -> dict[str, Any]:
    problem = dict(approach.get("problem", {})) if isinstance(approach.get("problem"), Mapping) else {}
    question = dict(problem.get("question", {})) if isinstance(problem.get("question"), Mapping) else {}
    decision = dict(question.get("decision", {})) if isinstance(question.get("decision"), Mapping) else {}
    implementation = (
        dict(decision.get("implementation", {})) if isinstance(decision.get("implementation"), Mapping) else {}
    )
    domain_specification = (
        dict(implementation.get("domain_specification", {}))
        if isinstance(implementation.get("domain_specification"), Mapping)
        else {"aspects": []}
    )
    patch_plan = dict(implementation.get("patch_plan", {})) if isinstance(implementation.get("patch_plan"), Mapping) else {}
    candidates = {"candidates": [dict(item) for item in question.get("candidates", []) if isinstance(item, Mapping)]}
    evaluations = {
        "evaluations": [
            dict(candidate["evaluation"])
            for candidate in candidates["candidates"]
            if isinstance(candidate.get("evaluation"), Mapping)
        ]
    }
    selected = {key: value for key, value in decision.items() if key not in {"id", "implementation", "risks"}}
    if "selected_candidate_id" not in selected and isinstance(decision.get("selected_candidate_id"), str):
        selected["selected_candidate_id"] = decision["selected_candidate_id"]
    risks = {"risks": _collect_risks(problem)}
    return {
        "problem": problem,
        "constraints": _constraints_from_tree(problem.get("constraints")),
        "prior_art": {"patterns": [dict(item) for item in problem.get("prior_art", []) if isinstance(item, Mapping)]},
        "candidates": candidates,
        "evaluations": evaluations,
        "selected": selected,
        "implementation": {
            key: value
            for key, value in implementation.items()
            if key not in {"id", "domain_specification", "patch_plan", "risks"}
        },
        "domain_specification": {
            "aspects": [
                {key: value for key, value in item.items() if key != "id"}
                for item in domain_specification.get("aspects", [])
                if isinstance(item, Mapping)
            ]
        },
        "patch_plan": {key: value for key, value in patch_plan.items() if key != "id"},
        "risks": risks,
        "source": "reformed",
        "provided_approach": None,
    }


def _constraints_from_tree(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {"hard_constraints": [], "soft_preferences": []}
    return {
        "hard_constraints": [dict(item) for item in value.get("hard", []) if isinstance(item, Mapping)],
        "soft_preferences": [dict(item) for item in value.get("soft", []) if isinstance(item, Mapping)],
    }


def _collect_risks(value: Any) -> list[dict[str, Any]]:
    risks: list[dict[str, Any]] = []
    if isinstance(value, Mapping):
        child = value.get("risks")
        if isinstance(child, list):
            risks.extend(dict(item) for item in child if isinstance(item, Mapping))
        for nested in value.values():
            if nested is not child:
                risks.extend(_collect_risks(nested))
    elif isinstance(value, list):
        for nested in value:
            risks.extend(_collect_risks(nested))
    return risks


def _run_reform_step(
    step: str,
    repo: Path,
    patch_series_view: dict[str, Any],
    components: Mapping[str, Any],
    context: Mapping[str, Any],
    *,
    log_ctx: org_log.RunContext | None = None,
    revision_plan: Mapping[str, Any] | None = None,
    deferral_collector: list[dict[str, Any]] | None = None,
    closure_collector: list[dict[str, Any]] | None = None,
    defense_collector: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    # Brief 25: a step with a targeted revision plan runs in a bounded window and
    # merges fragments code-side; whole-step regeneration remains for new steps,
    # root/majority-anchored steps, decision, and single-object steps.
    if revision_plan is not None and step in _TARGETED_REVISION_STEPS:
        return _revise_step_nodes(
            step,
            repo,
            components,
            revision_plan,
            patch_series_view=patch_series_view,
            author_self_history=context.get("AUTHOR SELF-HISTORY"),
            deferral_collector=deferral_collector,
            closure_collector=closure_collector,
            defense_collector=defense_collector,
            log_ctx=log_ctx,
        )
    # Memento: reform steps run UNDER the pull's RunContext. Before log_ctx was
    # threaded here, the decision step's codex calls (including the evaluation
    # repair round, run-20260704T141927Z) executed as orphan run dirs behind an
    # adapter_boundary warning, invisible under the pull that triggered them.
    if step == "problem":
        return _call_with_optional_log_ctx(_normalize_problem, patch_series_view, context, log_ctx=log_ctx)
    if step == "constraints":
        return _call_with_optional_log_ctx(
            _extract_constraints, patch_series_view, repo, context, _assemble_from_components(components), log_ctx=log_ctx
        )
    if step == "prior_art":
        # Memento (brief 19): id-bearing reform steps run under the REVISION CONTRACT -
        # previous generation with ids as the explicit starting point, replacements
        # declared, never silent renames (brief15 evaluations x candidates; pomodoro
        # round-2 cross_links x renamed prior_art ids; DQ round-6 dangling anchors).
        accumulated = _assemble_from_components(components)
        return _call_with_optional_log_ctx(
            _build_prior_art_map,
            patch_series_view,
            repo,
            context,
            accumulated,
            log_ctx=log_ctx,
            revision=_step_revision_baseline(components["prior_art"].get("patterns")),
        )
    if step == "candidates":
        accumulated = _assemble_from_components(components)
        return _call_with_optional_log_ctx(
            _generate_candidates,
            _normalized_problem_from_tree(components["problem"]),
            components["constraints"],
            components["prior_art"],
            context,
            accumulated,
            log_ctx=log_ctx,
            revision=_step_revision_baseline(components["candidates"].get("candidates")),
        )
    if step == "decision":
        evaluations = _reform_decision_evaluations(components, context, log_ctx=log_ctx)
        failure = _technical_approach_step_failure("decision", evaluations)
        if failure:
            return failure
        selected = _call_with_optional_log_ctx(
            _select_approach,
            components["candidates"],
            evaluations,
            components["constraints"],
            context,
            _assemble_from_components(components),
            log_ctx=log_ctx,
        )
        if isinstance(selected, Mapping) and selected.get("ok") is False:
            return selected
        # Memento: selection consumed the refreshed (pruned + delta-evaluated +
        # merged) matrix — the reformed tree must carry it too, in formation's
        # exact shape (the shared _question_tree nests it per candidate). A
        # decision persisted without its evaluation basis is a deterministic
        # provide_evidence objection: round-3 (2026-07-05) objected "the
        # comparative evaluation is not auditable" against exactly that gap.
        return {"selected": selected, "evaluations": evaluations}
    if step == "implementation":
        return _call_with_optional_log_ctx(
            _implementation_strategy,
            components["selected"],
            components["prior_art"],
            components["constraints"],
            patch_series_view,
            repo,
            context,
            _assemble_from_components(components),
            log_ctx=log_ctx,
        )
    if step == "domain_specification":
        # Memento (brief 26): reform derived facets FROM the committed aspects, so
        # an empty committed subtree regenerated empty forever (GIGO bootstrap —
        # live: pomodoro run 3, rounds 2-3 flagged the same empty subtree across a
        # reform that had this step in affected_steps). [PROVISIONAL: union, not
        # fallback-only] committed-aspect facets come first (revision continuity —
        # the committed generation keeps its identity), then formation-equivalent
        # facets (view/UX surfaces + store hits) fill what the committed
        # generation missed; when committed aspects are empty this degenerates to
        # exactly the formation source. The window stays bounded (brief 25 law):
        # facets are compact named surfaces, never the tree.
        profile_facets = _domain_profile_facets(components.get("domain_specification", {}).get("aspects", []))
        formation_facets, formation_lookups = _domain_precedent_facets_from_patch_series(
            patch_series_view, components["implementation"], context
        )
        seen_aspects = {str(facet.get("aspect_name") or "").strip().lower() for facet in profile_facets}
        for facet in formation_facets:
            aspect_key = str(facet.get("aspect_name") or "").strip().lower()
            if aspect_key and aspect_key not in seen_aspects:
                seen_aspects.add(aspect_key)
                profile_facets.append(facet)
        return _call_with_optional_log_ctx(
            _domain_specification,
            profile_facets,
            {**formation_lookups, **_domain_precedent_lookups(profile_facets, context)},
            components["implementation"],
            components["constraints"],
            patch_series_view,
            context,
            _assemble_from_components(components),
            log_ctx=log_ctx,
        )
    if step == "patch_plan":
        return _call_with_optional_log_ctx(
            _right_size_patch_plan,
            components["selected"],
            components["implementation"],
            components["constraints"],
            context,
            _assemble_from_components(components),
            log_ctx=log_ctx,
        )
    if step == "risks":
        accumulated = _assemble_from_components(components)
        risks = _call_with_optional_log_ctx(
            _surface_risks,
            components["selected"],
            components["implementation"],
            components["patch_plan"],
            components["constraints"],
            context,
            accumulated,
            log_ctx=log_ctx,
            revision=_step_revision_baseline(components["risks"].get("risks")),
        )
        if isinstance(risks, Mapping) and risks.get("ok") is False:
            return risks
        return risks
    return {"ok": False, "error": f"unknown re-formation step {step}"}


def _reform_decision_evaluations(
    components: Mapping[str, Any],
    context: Mapping[str, Any],
    *,
    log_ctx: org_log.RunContext | None = None,
) -> dict[str, Any]:
    """Return a current-generation evaluation matrix for the reform decision step.

    Memento: committed evaluations are generation-stamped by their candidate ids.
    Candidate regeneration invalidates COVERAGE, never emptiness. The old guard
    (`if not evaluations.get("evaluations")`) passed a non-empty previous-generation
    matrix straight into _select_approach whenever reform regenerated candidates
    (fresh LLM-named ids vs old-generation evaluations) and cost three identical
    run failures — PULL 5, PULL 6, rescue-1, all 2026-07-04 — before diagnosis.
    Guard with the same total id comparisons the gate uses (_candidate_ids /
    _evaluation_ids); on mismatch prune dead-generation evaluations code-side (no
    LLM) and evaluate ONLY the candidates that lack an evaluation, at NORMAL
    effort: these are genuinely new judgments, not low-effort re-fills of
    already-spent thinking. Surviving evaluations are never overwritten and never
    re-evaluated. Fail-closed is unchanged: uncovered coverage after the delta is
    a typed needs_work, and the _select_approach repair round stays the second
    line of defense for slot drops WITHIN a matched generation.
    """
    candidates = components["candidates"]
    evaluations = components["evaluations"]
    candidate_ids = _candidate_ids(candidates)
    if set(_evaluation_ids(evaluations)) == set(candidate_ids):
        # Coverage matches (also the no-candidates degenerate case, which
        # _select_approach rejects with its own typed error): unchanged behavior,
        # no evaluation call at all.
        return evaluations
    evaluation_items = evaluations.get("evaluations")
    if not isinstance(evaluation_items, list):
        evaluation_items = []
    current_ids = set(candidate_ids)
    surviving = [
        dict(evaluation)
        for evaluation in evaluation_items
        if isinstance(evaluation, Mapping) and evaluation.get("candidate_id") in current_ids
    ]
    surviving_ids = {evaluation["candidate_id"] for evaluation in surviving}
    missing_ids = [candidate_id for candidate_id in candidate_ids if candidate_id not in surviving_ids]
    fresh: list[dict[str, Any]] = []
    if missing_ids:
        missing_subset = {
            "candidates": [
                dict(candidate)
                for candidate in candidates.get("candidates", [])
                if isinstance(candidate, Mapping) and candidate.get("id") in set(missing_ids)
            ]
        }
        delta = _evaluate_candidates(
            missing_subset,
            _normalized_problem_from_tree(components["problem"]),
            components["constraints"],
            context,
            _assemble_from_components(components),
            log_ctx=log_ctx,
        )
        if delta.get("ok", True) is False:
            return delta
        fresh = list(delta.get("evaluations", []))
    merged = {"evaluations": [*surviving, *fresh]}
    if set(_evaluation_ids(merged)) != set(candidate_ids):
        return _candidate_evaluation_error(
            "reform decision evaluations do not cover the current candidate generation"
        )
    return merged


def _store_reform_step_result(components: dict[str, Any], step: str, result: Mapping[str, Any]) -> None:
    if step == "problem":
        previous = components["problem"]
        replacement = _problem_root_from_normalized(result)
        replacement["constraints"] = previous.get("constraints", {"hard": [], "soft": []})
        replacement["prior_art"] = previous.get("prior_art", [])
        replacement["question"] = previous.get("question", {"id": "question:approach", "candidates": [], "decision": {}})
        replacement["open_questions"] = previous.get("open_questions", [])
        components["problem"] = replacement
    elif step == "constraints":
        components["constraints"] = dict(result)
        components["problem"]["constraints"] = _constraint_tree_nodes(result)
    elif step == "prior_art":
        components["prior_art"] = dict(result)
        components["problem"]["prior_art"] = _prior_art_tree_nodes(result)
    elif step == "candidates":
        components["candidates"] = dict(result)
    elif step == "decision":
        # The decision step returns a compound result: the selection AND the
        # refreshed evaluation matrix it was derived from. Both persist, so the
        # assembled tree carries the auditable evaluation basis of the decision.
        components["selected"] = dict(result["selected"])
        components["evaluations"] = {
            "evaluations": [
                dict(item)
                for item in result["evaluations"].get("evaluations", [])
                if isinstance(item, Mapping)
            ]
        }
    elif step == "implementation":
        components["implementation"] = dict(result)
    elif step == "domain_specification":
        components["domain_specification"] = dict(result)
    elif step == "patch_plan":
        components["patch_plan"] = dict(result)
    elif step == "risks":
        components["risks"] = dict(result)


def _assemble_from_components(components: Mapping[str, Any]) -> dict[str, Any]:
    cross_links: list[dict[str, str]] = []
    problem = dict(components["problem"])
    problem["constraints"] = _constraint_tree_nodes(components["constraints"])
    problem["prior_art"] = _prior_art_tree_nodes(components["prior_art"])
    problem["question"] = _question_tree(
        components["selected"],
        components["candidates"],
        components["evaluations"],
        components["implementation"],
        components["domain_specification"],
        components["patch_plan"],
        components["risks"],
        cross_links,
        provided_approach=components.get("provided_approach"),
        prior_art_link_targets=_prior_art_link_targets(problem["prior_art"]),
    )
    problem.setdefault("id", "problem")
    problem.setdefault("open_questions", [])
    return {"problem": problem, "cross_links": cross_links}


def _normalized_problem_from_tree(problem: Mapping[str, Any]) -> dict[str, Any]:
    goals = problem.get("goals", [])
    success_criteria = [dict(goal) for goal in goals if isinstance(goal, Mapping)]
    return {
        "problem": str(problem.get("problem") or problem.get("summary") or ""),
        "affected": str(problem.get("affected") or ""),
        "current_inadequacy": str(problem.get("current_inadequacy") or ""),
        "success_criteria": success_criteria,
        "non_goals": list(problem.get("non_goals", [])) if isinstance(problem.get("non_goals"), list) else [],
        "open_questions": _string_list(problem.get("open_questions")),
    }


def _component_node_snapshot(components: Mapping[str, Any], step: str) -> Any:
    if step == "problem":
        return {
            key: value
            for key, value in components["problem"].items()
            if key not in {"constraints", "prior_art", "question", "open_questions"}
        }
    key = {
        "decision": "selected",
        "risks": "risks",
    }.get(step, step)
    return components.get(key)


def _changed_node_ids_for_step(components: Mapping[str, Any], step: str) -> list[str]:
    return sorted(
        node_id
        for node_id, node_step in _node_step_map(_assemble_from_components(components)).items()
        if node_step == step
    )


def _changed_evaluation_node_ids(before_matrix: Any, after_matrix: Any) -> list[str]:
    """Tree node ids of evaluations that were pruned, added, or updated.

    Uses the assembled-tree id form (evaluation:<candidate_id>). Pruned nodes no
    longer exist in the tree but their ids are still reported: the round record
    must show that the dead-generation evaluation was removed, not just that new
    ones appeared.
    """

    def indexed(matrix: Any) -> dict[str, str]:
        items = matrix.get("evaluations", []) if isinstance(matrix, Mapping) else []
        return {
            item["candidate_id"]: _canonical_json(item)
            for item in items
            if isinstance(item, Mapping) and isinstance(item.get("candidate_id"), str)
        }

    before = indexed(before_matrix)
    after = indexed(after_matrix)
    return sorted(
        f"evaluation:{candidate_id}"
        for candidate_id in set(before) | set(after)
        if before.get(candidate_id) != after.get(candidate_id)
    )


# ==========================================================================================
# REVISION DELTA LEDGER + DANGLING-REFERENCE LINT (brief 19, RATIFIED).
# Memento: three faces of one disease — LLM-renamed node ids x non-tracking references
# (brief15 evaluations x candidates; pomodoro round-2 cross_links x renamed prior_art;
# DQ round-6 dangling objection anchors -> unresolvable-forever -> NAK). The ledger is
# the kernel v(N)->v(N+1) changelog made machine-readable; the lint is the fail-closed
# gate that refuses to commit a reformed tree whose references no longer resolve.
# All code below is deterministic, total, and never fabricates judgment content.
# ==========================================================================================

# Node-id families whose ids are DERIVED singletons (id embeds the selected candidate):
# when the selection moves, the old id is mechanically replaced by the new one.
_DERIVED_SINGLETON_ID_PREFIXES = ("decision:", "implementation:", "patch_plan:")
# Node-id families whose ids are POSITIONAL / code-indexed (constraint:hard:N, goal:N,
# domain_specification aspects): the model has no declaration mechanism for them, so a
# pruned anchor in these families is classified as change (the family was restructured),
# never as an undeclared-prune violation. [PROVISIONAL] fail-closed only where the
# contract CAN be honored; anything else would recreate the unresolvable-forever trap.
_REINDEXED_FAMILY_ID_PREFIXES = ("constraint:", "goal:", "domain_specification")
# Cross-link endpoints synthesized at assembly that are not tree nodes by construction.
_SYNTHETIC_CROSS_LINK_PREFIXES = ("argument:",)
# Fields whose contract says "node id". Prose-citation fields (draws_on, traces_to,
# consulted_terms, citations) are NEVER matched: learn from the noisy detector.
_ID_TYPED_REFERENCE_FIELDS = ("candidate_id", "about_candidate_id", "selected_candidate_id", "target_id")


def _tree_node_contents(tree: Any) -> dict[str, str]:
    """Map every id-bearing node in an approach tree to its canonical content."""
    contents: dict[str, str] = {}

    def _walk(value: Any) -> None:
        if isinstance(value, Mapping):
            node_id = value.get("id")
            if isinstance(node_id, str) and node_id and node_id not in contents:
                contents[node_id] = _canonical_json(value)
            for child in value.values():
                _walk(child)
        elif isinstance(value, list):
            for child in value:
                _walk(child)

    _walk(tree)
    return contents


def _tree_replacement_declarations(tree: Any) -> list[dict[str, str]]:
    """Collect the node replacements carried in the tree itself.

    Two channels, both tree-sticky so the map survives every reassembly:
    - replaces/replacement_reason: model-declared in a revision step output;
    - reconciled_replaces: code-written by the brief 22 reconciliation round
      (a list, because several legacy ids may map to one successor — a single
      model-facing replaces string cannot carry that).
    """
    declarations: list[dict[str, str]] = []

    def _walk(value: Any) -> None:
        if isinstance(value, Mapping):
            replaced = value.get("replaces")
            node_id = value.get("id")
            if (
                isinstance(replaced, str)
                and replaced.strip()
                and isinstance(node_id, str)
                and node_id
            ):
                declarations.append(
                    {
                        "replaced_node_id": replaced,
                        "successor_node_id": node_id,
                        "reason": str(value.get("replacement_reason") or ""),
                    }
                )
            reconciled = value.get("reconciled_replaces")
            if isinstance(reconciled, list) and isinstance(node_id, str) and node_id:
                for item in reconciled:
                    if (
                        isinstance(item, Mapping)
                        and isinstance(item.get("replaced_node_id"), str)
                        and item["replaced_node_id"].strip()
                    ):
                        declarations.append(
                            {
                                "replaced_node_id": item["replaced_node_id"],
                                "successor_node_id": node_id,
                                "reason": str(item.get("reason") or ""),
                            }
                        )
            for child in value.values():
                _walk(child)
        elif isinstance(value, list):
            for child in value:
                _walk(child)

    _walk(tree)
    return sorted(declarations, key=lambda item: (item["replaced_node_id"], item["successor_node_id"]))


def _implied_replacement_declarations(
    before_ids: set[str],
    after_ids: set[str],
    declared: list[dict[str, str]],
) -> list[dict[str, str]]:
    """Mechanically derive replacements for DERIVED node ids (structure, not judgment).

    decision:/implementation:/patch_plan: ids embed the selected candidate: when the
    selection moves, the old derived id is replaced by the new one. evaluation:<id>
    follows a declared candidate replacement. The model cannot declare these (the ids
    are assigned at assembly), so the code derives them with the same rules assembly
    uses.
    """
    implied: list[dict[str, str]] = []
    for declaration in declared:
        old_evaluation = f"evaluation:{declaration['replaced_node_id']}"
        new_evaluation = f"evaluation:{declaration['successor_node_id']}"
        if old_evaluation in before_ids and new_evaluation in after_ids:
            implied.append(
                {
                    "replaced_node_id": old_evaluation,
                    "successor_node_id": new_evaluation,
                    "reason": "derived: evaluation node follows its declared candidate replacement",
                }
            )
    for prefix in _DERIVED_SINGLETON_ID_PREFIXES:
        removed = sorted(node_id for node_id in before_ids if node_id.startswith(prefix) and node_id not in after_ids)
        introduced = sorted(node_id for node_id in after_ids if node_id.startswith(prefix) and node_id not in before_ids)
        if len(removed) == 1 and len(introduced) == 1:
            implied.append(
                {
                    "replaced_node_id": removed[0],
                    "successor_node_id": introduced[0],
                    "reason": "derived: singleton node id follows the selected candidate",
                }
            )
    return implied


def _revision_replacements(previous_tree: Any, revised_tree: Any) -> tuple[list[dict[str, str]], dict[str, list[str]]]:
    declared = _tree_replacement_declarations(revised_tree)
    before_ids = set(_tree_node_contents(previous_tree))
    after_ids = set(_tree_node_contents(revised_tree))
    replacements = declared + _implied_replacement_declarations(before_ids, after_ids, declared)
    successors: dict[str, list[str]] = {}
    for declaration in replacements:
        successors.setdefault(declaration["replaced_node_id"], []).append(declaration["successor_node_id"])
    return replacements, successors


def _id_typed_tree_references(tree: Any) -> list[dict[str, str]]:
    """Collect every node-id-typed reference in the tree with its holder context."""
    references: list[dict[str, str]] = []

    def _walk(value: Any, holder: str) -> None:
        if isinstance(value, Mapping):
            node_id = value.get("id")
            current = node_id if isinstance(node_id, str) and node_id else holder
            for field in _ID_TYPED_REFERENCE_FIELDS:
                reference = value.get(field)
                if isinstance(reference, str) and reference:
                    references.append({"field": field, "reference": reference, "holder": current})
            links = value.get("cross_links")
            if isinstance(links, list):
                for link in links:
                    if not isinstance(link, Mapping):
                        continue
                    for endpoint in ("from", "to"):
                        reference = link.get(endpoint)
                        if (
                            isinstance(reference, str)
                            and reference
                            and not reference.startswith(_SYNTHETIC_CROSS_LINK_PREFIXES)
                        ):
                            references.append(
                                {"field": f"cross_links.{endpoint}", "reference": reference, "holder": current or "root"}
                            )
            for key, child in value.items():
                if key == "cross_links":
                    continue
                _walk(child, current)
        elif isinstance(value, list):
            for child in value:
                _walk(child, holder)

    _walk(tree, "")
    return references


def _revision_continuity_violations(
    previous_tree: Any,
    revised_tree: Any,
    prior_open_objections: list[Mapping[str, Any]],
) -> list[str]:
    """Deterministic dangling-reference lint at reform commit time (total, no exceptions).

    Any node-id-typed reference to a non-existent id without a declared (or derived)
    replacement, and any open objection anchor pruned without a declared replacement,
    is named exactly. Prose fields never match; reindexed positional families never
    raise undeclared-prune (the model cannot declare successors for code-indexed ids).
    """
    known = set(_tree_node_contents(revised_tree))
    _replacements, successors = _revision_replacements(previous_tree, revised_tree)
    violations: list[str] = []
    for reference in _id_typed_tree_references(revised_tree):
        target = reference["reference"]
        if target in known or target in successors:
            continue
        violations.append(
            f"dangling node reference: {reference['field']}={target!r} held by "
            f"{reference['holder'] or 'tree root'} resolves to no node id and no declared replacement"
        )
    for objection in prior_open_objections:
        objection_id = str(objection.get("objection_id") or "")
        anchors = objection.get("anchor_node_ids", [])
        if not isinstance(anchors, list):
            continue
        for anchor in anchors:
            if not isinstance(anchor, str) or not anchor:
                continue
            if anchor in known or anchor in successors:
                continue
            if anchor.startswith(_REINDEXED_FAMILY_ID_PREFIXES):
                continue
            violations.append(
                f"undeclared prune: open objection {objection_id!r} anchors {anchor!r}, which was removed "
                "without a declared replacement (declare replaces on the successor node or keep the node)"
            )
    return _dedupe(violations)


def _remap_root_reference_ids(previous_tree: Any, revised_tree: dict[str, Any]) -> dict[str, Any]:
    """Mechanically rewrite root-held references through the replacement map.

    Memento (brief 21): root-held references have no owning step — the top-level
    cross_links array is regenerated at assembly from node prose (candidate
    draws_on slugs), so when prior_art/candidate ids change across generations
    NOTHING can ever repair the root links, and with the continuity gate
    fail-closed a series carrying stale root links is permanently stuck at
    needs_work (live deadlock: pomodoro v4, all 49 cross_links root-held,
    2026-07-05). The replacement map (declared + derived, brief19-B) is their
    only maintenance path: the lint is the backstop, this remap is the plumbing.
    Pure id swap on dead endpoints with exactly ONE successor; the remap never
    guesses. Undeclared endpoints are left for the lint (fail-closed stays).
    An AMBIGUOUS endpoint (two nodes declaring the same predecessor) is left
    unrewritten but is a declared replacement, so the lint excuses it per the
    ratified rule; the ledger records both claimants and the next reform's
    owning step is re-fed the dangling id. Links are never dropped. Root holder inventory: cross_links is the ONLY root-held
    id-typed holder — every other id-typed field lives inside an id-bearing
    node (asserted by test against the reference walker).
    """
    links = revised_tree.get("cross_links")
    if not isinstance(links, list):
        return revised_tree
    known = set(_tree_node_contents(revised_tree))
    _declarations, successors = _revision_replacements(previous_tree, revised_tree)
    remapped: list[Any] = []
    changed = False
    for link in links:
        if not isinstance(link, Mapping):
            remapped.append(link)
            continue
        entry = dict(link)
        for endpoint in ("from", "to"):
            target = entry.get(endpoint)
            if (
                isinstance(target, str)
                and target
                and target not in known
                and not target.startswith(_SYNTHETIC_CROSS_LINK_PREFIXES)
                and len(successors.get(target, [])) == 1
            ):
                entry[endpoint] = successors[target][0]
                changed = True
        remapped.append(entry)
    if not changed:
        return revised_tree
    return {**revised_tree, "cross_links": remapped}


def _dangling_reference_targets(tree: Any) -> set[str]:
    """Ids referenced somewhere in the tree that resolve to no node (total)."""
    known = set(_tree_node_contents(tree))
    return {
        reference["reference"]
        for reference in _id_typed_tree_references(tree)
        if reference["reference"] not in known
    }


def _unresolved_reference_families(tree: Any) -> dict[str, list[str]]:
    """Route dangling reference ids to the reform step that owns their family.

    prior_art:/risk: prefixes map to their steps; unprefixed ids are candidate
    ids. Derived families (decision:/implementation:/patch_plan:/evaluation:)
    follow their sources and reindexed positional families have no declaration
    mechanism, so neither routes anywhere. The owning step's revision contract
    then carries the ids so the model can declare the correspondence.
    """
    families: dict[str, list[str]] = {}
    for target in sorted(_dangling_reference_targets(tree)):
        family = _route_reference_family(target)
        if family is not None:
            families.setdefault(family, []).append(target)
    return families


def _route_reference_family(target: str) -> str | None:
    """Owning-step family of a dead node id, or None when nothing can declare it."""
    if target.startswith(_SYNTHETIC_CROSS_LINK_PREFIXES) or target.startswith(_REINDEXED_FAMILY_ID_PREFIXES):
        return None
    if target.startswith(_DERIVED_SINGLETON_ID_PREFIXES) or target.startswith("evaluation:"):
        return None
    if target.startswith("prior_art:"):
        return "prior_art"
    if target.startswith("risk:"):
        return "risks"
    return "candidates"


# ==========================================================================================
# REFERENCE RECONCILIATION REPAIR ROUND (brief 22).
# Memento: a conditional instruction buried in a saturated prompt is not a contract.
# Live case (pomodoro v4 retry, 2026-07-05): the prior_art revision prompt DID carry
# the UNRESOLVED INHERITED REFERENCES block with the correct instruction — inside a
# 725,666-char prompt. The model produced zero declarations twice (identical payloads,
# no validation feedback): author-side attention saturation, the same disease brief 20
# fixed reviewer-side. Bounded windows are how judgment gets attention: when the
# continuity gate fails on dangling references, ONE focused codex call sees ONLY the
# dead ids and the same-family candidates (id + name + one-line summary), and maps
# identity. Identity mapping is genuine judgment -> NORMAL effort (the low-effort
# override stays reserved for re-filling slots of already-spent thinking). One round;
# a second failure surfaces the existing typed needs_work. This is an auto-repaired
# site: deliberately ABSENT from the gadget hint registry (decision-step precedent —
# never double-hint an auto-repaired failure).
# ==========================================================================================

_RECONCILIATION_FAMILY_SUMMARY_FIELDS = ("summary", "when_applies", "risk")


def _reconciliation_families(
    previous_tree: Any,
    revised_tree: Any,
    prior_open_objections: list[Mapping[str, Any]],
) -> dict[str, list[str]]:
    """Dead ids still lacking a successor, grouped by owning family (deterministic)."""
    _declarations, successors = _revision_replacements(previous_tree, revised_tree)
    known = set(_tree_node_contents(revised_tree))
    families: dict[str, set[str]] = {}
    for family, targets in _unresolved_reference_families(revised_tree).items():
        for target in targets:
            if target not in successors:
                families.setdefault(family, set()).add(target)
    for objection in prior_open_objections:
        anchors = objection.get("anchor_node_ids", [])
        if not isinstance(anchors, list):
            continue
        for anchor in anchors:
            if not isinstance(anchor, str) or not anchor or anchor in known or anchor in successors:
                continue
            family = _route_reference_family(anchor)
            if family is not None:
                families.setdefault(family, set()).add(anchor)
    return {family: sorted(targets) for family, targets in sorted(families.items())}


def _family_node_summaries(tree: Any, family: str) -> list[dict[str, str]]:
    """Current same-family nodes as id + name + one-line summary (never bodies)."""
    problem = tree.get("problem", {}) if isinstance(tree, Mapping) else {}
    question = problem.get("question", {}) if isinstance(problem, Mapping) else {}
    if family == "prior_art":
        items = problem.get("prior_art", []) if isinstance(problem, Mapping) else []
    elif family == "candidates":
        items = question.get("candidates", []) if isinstance(question, Mapping) else []
    elif family == "risks":
        items = [
            node
            for node in _tree_node_subtrees(tree)
            if isinstance(node.get("id"), str) and node["id"].startswith("risk:")
        ]
    else:
        items = []
    summaries: list[dict[str, str]] = []
    for item in items:
        if not isinstance(item, Mapping) or not isinstance(item.get("id"), str) or not item["id"]:
            continue
        summary = next(
            (str(item[field]) for field in _RECONCILIATION_FAMILY_SUMMARY_FIELDS if isinstance(item.get(field), str) and item[field]),
            "",
        )
        summaries.append({"id": item["id"], "name": str(item.get("name") or ""), "summary": summary})
    return sorted(summaries, key=lambda entry: entry["id"])


def _tree_node_subtrees(tree: Any) -> list[Mapping[str, Any]]:
    nodes: list[Mapping[str, Any]] = []

    def _walk(value: Any) -> None:
        if isinstance(value, Mapping):
            if isinstance(value.get("id"), str) and value["id"]:
                nodes.append(value)
            for child in value.values():
                _walk(child)
        elif isinstance(value, list):
            for child in value:
                _walk(child)

    _walk(tree)
    return nodes


def _reference_reconciliation_schema(dangling_ids: list[str]) -> dict[str, Any]:
    # Codex output-schema safe subset (mirror of the evaluation-repair pattern):
    # the dangling-id key field is a closed enum; successor value discipline
    # (same family, existing id, empty when no_successor) is enforced by prompt
    # plus code-side validation, never by forbidden schema keywords.
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": ["reconciliations"],
        "properties": {
            "reconciliations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["dangling_id", "successor_id", "no_successor", "reason"],
                    "properties": {
                        "dangling_id": _known_precedent_string_schema(dangling_ids),
                        "successor_id": {
                            "type": "string",
                            "description": "Exact current same-family node id that is the same logical concern; empty when no_successor is true.",
                        },
                        "no_successor": {
                            "type": "boolean",
                            "description": "True when no current node corresponds to this dangling id.",
                        },
                        "reason": {"type": "string", "description": "One-sentence reason for the mapping or its absence."},
                    },
                },
            }
        },
    }


def _reference_reconciliation_prompt(
    families: Mapping[str, list[str]],
    current_nodes: Mapping[str, list[dict[str, str]]],
) -> str:
    payload = json.dumps(
        {"dangling_ids_by_family": dict(families), "current_nodes_by_family": dict(current_nodes)},
        indent=2,
        sort_keys=True,
        ensure_ascii=True,
    )
    return (
        "You are reconciling node identity across patch series revisions.\n"
        "The committed tree still references the dangling previous-generation node ids listed below. The "
        "current nodes of the same family are listed with id, name, and a one-line summary.\n"
        "For EACH dangling id return exactly one entry: either successor_id set to the exact current node id "
        "(same family only) that is the same logical concern with no_successor false, or no_successor true "
        "with successor_id empty. Give a one-sentence reason either way. Do not invent ids; do not skip any "
        "dangling id.\n\n"
        f"{payload}\n"
        "\nReturn only JSON matching the provided schema."
    )


def _with_reconciled_replacements(
    revised_tree: dict[str, Any],
    successor_map: Mapping[str, list[dict[str, str]]],
) -> dict[str, Any]:
    """Write reconciliation outcomes onto their successor nodes (tree-sticky)."""
    updated = deepcopy(revised_tree)

    def _walk(value: Any) -> None:
        if isinstance(value, dict):
            node_id = value.get("id")
            if isinstance(node_id, str) and node_id in successor_map:
                existing = value.get("reconciled_replaces")
                entries = list(existing) if isinstance(existing, list) else []
                already = {
                    entry.get("replaced_node_id")
                    for entry in entries
                    if isinstance(entry, Mapping)
                }
                for item in successor_map[node_id]:
                    if item["replaced_node_id"] not in already:
                        entries.append(dict(item))
                value["reconciled_replaces"] = entries
            for child in value.values():
                _walk(child)
        elif isinstance(value, list):
            for child in value:
                _walk(child)

    _walk(updated)
    return updated


def _reconcile_dangling_references(
    repo: str | Path,
    previous_tree: Any,
    revised_tree: dict[str, Any],
    prior_open_objections: list[Mapping[str, Any]],
    *,
    log_ctx: org_log.RunContext | None = None,
) -> tuple[dict[str, Any], list[dict[str, str]]] | None:
    """ONE bounded reconciliation round for dangling references (brief 22).

    Returns (revised tree carrying the reconciled declarations, remapped;
    no_successor records) or None when reconciliation is not applicable or the
    round failed — the caller then surfaces the existing typed needs_work.
    """
    families = _reconciliation_families(previous_tree, revised_tree, prior_open_objections)
    current_nodes = {family: _family_node_summaries(revised_tree, family) for family in families}
    # A family with no current nodes has nothing to map to: those ids can only be
    # no_successor, which the fail-closed lint already expresses. Ask only where
    # a mapping is possible.
    families = {family: targets for family, targets in families.items() if current_nodes.get(family)}
    current_nodes = {family: current_nodes[family] for family in families}
    if not families:
        return None
    dangling_ids = sorted(target for targets in families.values() for target in targets)
    family_by_id = {target: family for family, targets in families.items() for target in targets}
    current_ids_by_family = {
        family: {entry["id"] for entry in entries} for family, entries in current_nodes.items()
    }

    run = codex_exec.run_json(
        Path(repo),
        schema=_reference_reconciliation_schema(dangling_ids),
        prompt=_reference_reconciliation_prompt(families, current_nodes),
        schema_filename="patch_series-reference-reconciliation.schema.json",
        output_filename="patch_series-reference-reconciliation.json",
        failure_label="Codex reference reconciliation",
        ctx=log_ctx.child(step="reference_reconciliation") if log_ctx is not None else None,
        # NORMAL effort on purpose: identity mapping is genuine judgment, not a
        # low-effort slot refill (ratified effort split).
    )
    if not run["ok"]:
        return None
    try:
        parsed = json.loads(run["raw"])
    except json.JSONDecodeError:
        return None
    entries = parsed.get("reconciliations") if isinstance(parsed, dict) else None
    if not isinstance(entries, list):
        return None
    successor_map: dict[str, list[dict[str, str]]] = {}
    no_successor_records: list[dict[str, str]] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, Mapping):
            return None
        dangling_id = entry.get("dangling_id")
        successor_id = entry.get("successor_id")
        no_successor = entry.get("no_successor")
        reason = entry.get("reason")
        if dangling_id not in family_by_id or dangling_id in seen:
            return None
        if not isinstance(successor_id, str) or not isinstance(no_successor, bool) or not isinstance(reason, str):
            return None
        if not reason.strip():
            return None
        seen.add(dangling_id)
        if no_successor:
            if successor_id.strip():
                return None
            no_successor_records.append({"dangling_id": dangling_id, "reason": reason})
            continue
        if successor_id not in current_ids_by_family[family_by_id[dangling_id]]:
            return None
        successor_map.setdefault(successor_id, []).append(
            {"replaced_node_id": dangling_id, "reason": reason}
        )
    if seen != set(dangling_ids):
        # Per-id coverage is the contract; a partial answer is a failed round.
        return None

    reconciled_tree = _with_reconciled_replacements(revised_tree, successor_map) if successor_map else revised_tree
    reconciled_tree = _remap_root_reference_ids(previous_tree, reconciled_tree)
    return reconciled_tree, no_successor_records


# ==========================================================================================
# TARGETED NODE REVISION (brief 25) — the author-side twin of brief19-D.
# Memento: whole-step regeneration converts a one-fact demand into tree-wide rewording
# churn. Two series died at the round cap before this fix (DQ 2026-07-05 round 6;
# pomodoro 2026-07-05 round 6, python-version-support-window: "define the Python
# version floor and its verification" survived 5 generations of addressed_by_change
# while round-0004's ledger showed 72/81 nodes changed and review delta scoping
# degenerated to full-tree). And the ~725KB whole-step windows are where a buried
# per-item instruction got zero engagement (brief 22). Bounded windows are how a
# demanded fact lands in the demanded node; byte-preserving every non-target node is
# what lets review delta scoping engage at all.
# ==========================================================================================

# Steps whose committed output is an id-keyed item collection with an itemwise
# parser: targeted fragment revision is well-defined for exactly these.
# [PROVISIONAL] problem/implementation/domain_specification/patch_plan stay
# whole-step: their outputs are single objects (a fragment IS the whole object),
# and decision is whole-step by ratified rule (selection is inherently whole-step
# but small).
_TARGETED_REVISION_STEPS = ("constraints", "prior_art", "candidates", "risks")
# [PROVISIONAL] majority threshold: strictly more than half of the step's primary
# nodes anchored -> the objections cover the step, not a spot in it; regenerating
# the whole step is then honest (revising most nodes "in place" would be the same
# work with a merge risk and no byte-preservation payoff).
_WHOLE_STEP_ANCHOR_MAJORITY = 0.5


def _step_item_primaries(approach: Mapping[str, Any], step: str, step_by_node: Mapping[str, str]) -> list[str]:
    """The id-keyed items a targeted-capable step authors (committed generation)."""
    problem = approach.get("problem", {}) if isinstance(approach, Mapping) else {}
    question = problem.get("question", {}) if isinstance(problem, Mapping) else {}
    if step == "candidates":
        items = question.get("candidates", []) if isinstance(question, Mapping) else []
        return sorted(str(item["id"]) for item in items if isinstance(item, Mapping) and item.get("id"))
    if step == "prior_art":
        items = problem.get("prior_art", []) if isinstance(problem, Mapping) else []
        return sorted(str(item["id"]) for item in items if isinstance(item, Mapping) and item.get("id"))
    if step == "constraints":
        return sorted(node_id for node_id, node_step in step_by_node.items() if node_step == "constraints" and node_id.startswith("constraint:"))
    if step == "risks":
        return sorted(node_id for node_id, node_step in step_by_node.items() if node_step == "risks" and node_id.startswith("risk:"))
    return []


def _targeted_revision_plan(
    approach: Mapping[str, Any],
    objections: list[Mapping[str, Any]],
    affected_steps: list[str],
) -> dict[str, dict[str, Any]]:
    """Derive per-step target node sets mechanically (code owns structure).

    Targets = nodes anchored by the unresolved objections routed to a step,
    plus their 1-hop id-typed neighborhood restricted to the step's subtree
    (review's _scope_from_seeds, shared via runtime import — same semantics as
    brief19-D review scoping, not a fork). Whole-step regeneration remains only
    when the step has no committed generation, is structurally whole-step, or
    the anchors cover the step's root or a majority of its primaries.
    """
    review_module = importlib.import_module("ai_org.patchwork_queue.review")
    step_by_node = _node_step_map(approach)
    subtree_by_id: dict[str, Mapping[str, Any]] = {}
    for node in _tree_node_subtrees(approach):
        subtree_by_id.setdefault(str(node.get("id")), node)
    routed = _feedback_by_step(approach, objections, list(affected_steps))

    plans: dict[str, dict[str, Any]] = {}
    for step in affected_steps:
        if step not in _TARGETED_REVISION_STEPS:
            continue
        step_objections = routed.get(step, [])
        # Majority is judged against the step's ITEM primaries — the nodes the
        # step actually authors — never containers (question:approach) or
        # derived evaluation nodes, which would dilute the ratio (anchoring the
        # only candidate is 1/1 anchored, not 1/3).
        primaries = _step_item_primaries(approach, step, step_by_node)
        anchored: set[str] = set()
        container_anchored = False
        for objection in step_objections:
            for anchor in objection.get("anchor_node_ids", []):
                if not isinstance(anchor, str) or step_by_node.get(anchor) != step:
                    continue
                normalized = anchor
                if step == "candidates" and anchor.startswith("evaluation:"):
                    normalized = anchor.removeprefix("evaluation:")
                if normalized in primaries:
                    anchored.add(normalized)
                else:
                    container_anchored = True  # in-step anchor outside the items = step container/root
        if not primaries or not anchored or container_anchored:
            continue  # no committed generation, nothing item-anchored, or root covered -> whole step
        if len(anchored) > _WHOLE_STEP_ANCHOR_MAJORITY * len(primaries):
            continue  # anchors cover the step's majority -> whole step
        anchored = set(sorted(anchored))
        scope = review_module._scope_from_seeds(approach, set(anchored))
        in_step_scope = [node_id for node_id in scope["scope_node_ids"] if node_id in set(primaries)]
        neighborhood = sorted(set(in_step_scope) - anchored)
        out_of_step_anchors = sorted(
            {
                anchor
                for objection in step_objections
                for anchor in objection.get("anchor_node_ids", [])
                if isinstance(anchor, str) and anchor in subtree_by_id and step_by_node.get(anchor) != step
            }
        )
        plans[step] = {
            "step": step,
            "target_ids": sorted(anchored),
            "open_questions": [
                question
                for question in (approach.get("problem", {}) or {}).get("open_questions", [])
                if isinstance(question, str)
            ]
            if isinstance(approach.get("problem"), Mapping)
            else [],
            "neighborhood_ids": neighborhood,
            "manifest_ids": sorted(set(primaries) - anchored - set(neighborhood)),
            "target_bodies": {node_id: subtree_by_id[node_id] for node_id in sorted(anchored) if node_id in subtree_by_id},
            "neighborhood_bodies": {node_id: subtree_by_id[node_id] for node_id in neighborhood if node_id in subtree_by_id},
            "out_of_step_anchor_bodies": {node_id: subtree_by_id[node_id] for node_id in out_of_step_anchors},
            "objections": [
                {
                    "objection_id": str(objection.get("objection_id") or ""),
                    "claim": str(objection.get("claim") or ""),
                    "requested_author_action": str(objection.get("requested_author_action") or ""),
                    "anchor_node_ids": [a for a in objection.get("anchor_node_ids", []) if isinstance(a, str)],
                    "axis": str(objection.get("axis") or ""),
                    "resolution_authority": str(objection.get("resolution_authority") or ""),
                }
                for objection in step_objections
            ],
        }
    return plans


_CONSTRAINT_ID_PREFIXES = {"hard": "constraint:hard:", "soft": "constraint:soft:"}


def _deferral_proposals_schema(objection_ids: list[str]) -> dict[str, Any]:
    # Brief 27 (Rust RFC three-way split, deferred_to_code_canon 2026-07-05): the
    # author may PROPOSE routing an objection to code — a structured option, never
    # a code decision. Safe subset; objection ids enum-closed.
    return {
        "type": "array",
        "items": {
            "type": "object",
            "additionalProperties": False,
            "required": ["objection_id", "executable_check", "defer_reason"],
            "properties": {
                "objection_id": _known_precedent_string_schema(objection_ids),
                "executable_check": {
                    "type": "string",
                    "description": (
                        "Concrete probe the first implementation will run — command-shaped (a shell command "
                        "the patch worktree can execute) is strongly preferred; exit 0 answers the question."
                    ),
                },
                "defer_reason": {
                    "type": "string",
                    "description": "One-sentence reason this is an empirical question best answered by running code.",
                },
            },
        },
    }


def _defenses_schema(objection_ids: list[str]) -> dict[str, Any]:
    # Brief 33 (asymmetric_review_canon, ratified 2026-07-05): author defense is
    # a FIRST-CLASS response — explicit "no node change; here is why the
    # objection is mistaken/inapplicable" (kernel explain-duty; review comments
    # that do not cause code changes should become explanation). Memento: the
    # reviewer is a filter, never a ceiling. Safe subset; ids enum-closed.
    return {
        "type": "array",
        "items": {
            "type": "object",
            "additionalProperties": False,
            "required": ["objection_id", "defense", "evidence_citations"],
            "properties": {
                "objection_id": _known_precedent_string_schema(objection_ids),
                "defense": {
                    "type": "string",
                    "description": (
                        "Why the objection is mistaken or inapplicable to the current content - the answer "
                        "to the strongest version of the concern, not a restatement of the design."
                    ),
                },
                "evidence_citations": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Citations grounding the defense: tree node ids, repository files, or external "
                        "sources. At least one is required - an uncited defense is insistence too."
                    ),
                },
            },
        },
    }


def _targeted_revision_schema(step: str, plan: Mapping[str, Any]) -> dict[str, Any]:
    """Fragment schema per step (codex safe subset; target ids enum-closed)."""
    target_ids = list(plan["target_ids"])
    if step == "candidates":
        schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "additionalProperties": False,
            "required": ["candidates"],
            "properties": {"candidates": {"type": "array", "items": _revision_candidate_schema()}},
        }
    elif step == "prior_art":
        schema = _revision_prior_art_schema()
    elif step == "risks":
        schema = _revision_surface_risks_schema([])
    elif step == "constraints":
        schema = build_extract_constraints_schema()
        for kind, field_name in (("hard", "hard_constraints"), ("soft", "soft_preferences")):
            prefix = _CONSTRAINT_ID_PREFIXES[kind]
            kind_targets = [target for target in target_ids if target.startswith(prefix)]
            items = schema["properties"][field_name]["items"]
            items["properties"]["id"] = _known_precedent_string_schema(kind_targets) if kind_targets else {"type": "string"}
            items["properties"]["id"]["description"] = "Exact target constraint node id being revised."
            items["required"] = ["id"] + list(items["required"])
    else:
        raise ValueError(f"step {step} does not support targeted revision")
    objection_ids = [str(objection.get("objection_id") or "") for objection in plan.get("objections", [])]
    schema["properties"]["deferral_proposals"] = _deferral_proposals_schema([oid for oid in objection_ids if oid])
    schema["properties"]["closed_questions"] = _closed_questions_schema(
        [question for question in plan.get("open_questions", []) if isinstance(question, str)]
    )
    schema["properties"]["defenses"] = _defenses_schema([oid for oid in objection_ids if oid])
    schema["required"] = list(schema["required"]) + ["deferral_proposals", "closed_questions", "defenses"]
    return schema


def _closed_questions_schema(open_questions: list[str]) -> dict[str, Any]:
    # Brief 28 (open_question_lifecycle_canon 2026-07-05): closure is judgment,
    # retirement is code. W3C formal-address / Rust tracking-issue closure: the
    # mature document DROPS the question; the disposition record lives in the
    # public ledger (our revision-delta ledger).
    return {
        "type": "array",
        "items": {
            "type": "object",
            "additionalProperties": False,
            "required": ["question", "resolution", "closure_kind", "executable_check"],
            "properties": {
                "question": _known_precedent_string_schema(open_questions),
                "resolution": {
                    "type": "string",
                    "description": "The decision that settles this question, with its rationale (one short paragraph).",
                },
                "closure_kind": {
                    "type": "string",
                    "enum": ["settled_on_paper", "routed_to_patch"],
                    "description": (
                        "settled_on_paper: decided here and now. routed_to_patch: the question becomes a "
                        "question the patch must answer, with executable_check as its probe."
                    ),
                },
                "executable_check": {
                    "type": "string",
                    "description": (
                        "REQUIRED non-empty for routed_to_patch (command-shaped probe, exit 0 answers it); "
                        "MUST be empty for settled_on_paper."
                    ),
                },
            },
        },
    }


def _targeted_revision_prompt(
    step: str,
    plan: Mapping[str, Any],
    author_self_history: Any | None = None,
) -> str:
    id_rule = _PRIOR_ART_REVISION_ID_RULE if step == "prior_art" else ""
    contract = _revision_contract_text(
        _step_revision_baseline(list(plan["target_bodies"].values())), id_rule
    )
    objections_text = json.dumps(plan["objections"], indent=2, sort_keys=True, ensure_ascii=True, default=str)
    targets_text = json.dumps(_json_safe(plan["target_bodies"]), indent=2, sort_keys=True, ensure_ascii=True, default=str)
    neighborhood_text = json.dumps(_json_safe(plan["neighborhood_bodies"]), indent=2, sort_keys=True, ensure_ascii=True, default=str)
    outside_text = json.dumps(_json_safe(plan["out_of_step_anchor_bodies"]), indent=2, sort_keys=True, ensure_ascii=True, default=str)
    manifest_text = json.dumps(plan["manifest_ids"], indent=2, ensure_ascii=True)
    constraint_rule = (
        "Each returned item carries id set to the exact target node id it revises.\n"
        if step == "constraints"
        else "Each returned item either keeps a target node id (revision in place) or declares replaces "
        "with a target node id (replacement).\n"
    )
    closure_rule = (
        "Open-question closure: if this revision DECIDES one of the tree's open questions listed below, "
        "declare it in closed_questions with the question string EXACTLY as listed, a short resolution, and "
        "closure_kind settled_on_paper (decided here) or routed_to_patch (becomes a question the patch must "
        "answer; then executable_check is a required command-shaped probe, exit 0 answers it; leave "
        "executable_check empty for settled_on_paper). The engine retires closed questions from the living "
        "tree and records the disposition in the revision ledger. A normative decision that leaves its open "
        "question standing is a contradiction reviewers must flag. Leave closed_questions empty otherwise.\n"
        + (
            "Current open questions:\n"
            + json.dumps(plan.get("open_questions", []), indent=2, ensure_ascii=True)
            + "\n"
            if plan.get("open_questions")
            else ""
        )
    )
    # Brief 33 (canon Part B: SWAY counterfactual near-zero result; obedient
    # -author live kill: run 2 abandoned a defensible framework selection under
    # a provide_evidence objection, 2026-07-05). Wording verified by
    # blank-context probe (out_b.txt): revise on a factually correct objection,
    # defend on a mistaken one — neither obstinacy nor over-compliance.
    # Memento: the reviewer is a filter, never a ceiling.
    defense_rule = (
        "Defend-or-revise: for each objection, first decide whether to defend or to revise. Revise only if "
        "the concern survives the counterfactual check: would this change still be right if the objection "
        "asserted the opposite? A revision caused solely by reviewer insistence is wrong - if the objection "
        "is mistaken or inapplicable, defend instead: add {objection_id, defense, evidence_citations} to "
        "defenses, stating why it is mistaken with citations (tree node ids, repository files, or external "
        "sources). The revised paper must make the defense stand on its own for the next blank reading. "
        "Leave defenses empty when every objection warrants revision.\n"
    )
    deferral_rule = (
        "Deferral option (decide on paper vs resolve in the patch): when an objection is an EMPIRICAL "
        "question best answered by running code (performance, ergonomics, implementability, interop, "
        "testability), you may propose routing it into the patch instead of revising nodes: add "
        "{objection_id, executable_check, defer_reason} to deferral_proposals, where executable_check is a "
        "concrete probe the first implementation will run (command-shaped, exit 0 answers it). The proposal "
        "is committed to author self-history but is not shown to the next blank reviewer; if that fresh "
        "reading independently returns the matching accepted status, it becomes a question the patch MUST "
        "answer, enforced at the "
        "merge gate, with a contingency plan elaborated separately on the patch side. "
        "Public-contract questions (requester-authority decisions, a requester-specified stack, hard "
        "compatibility commitments) are not deferrable and are rejected mechanically. Leave "
        "deferral_proposals empty otherwise.\n"
    )
    return (
        f"You are revising the {step} step of an existing Technical Approach in response to review "
        "objections. This is a TARGETED revision: return revised or replacement fragments for the target "
        "nodes only. Do not restate, rework, or return any other node; every non-target node is preserved "
        "byte-for-byte by the engine.\n"
        + constraint_rule
        + defense_rule
        + deferral_rule
        + closure_rule
        + contract
        + f"\nObjections to address (verbatim):\n{objections_text}\n"
        + f"\nTarget nodes (revise these):\n{targets_text}\n"
        + f"\nIn-step neighbor nodes (context only; do not revise):\n{neighborhood_text}\n"
        + f"\nObjection-anchored nodes outside this step (context only):\n{outside_text}\n"
        + f"\nOther node ids in this step (unchanged; valid as references):\n{manifest_text}\n"
        + "\nAUTHOR SELF-HISTORY (binding branch records; verbatim entries, oldest first):\n"
        + json.dumps(
            author_self_history or {"rounds": []},
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
            default=str,
        )
        + "\n"
        + "\nReturn only JSON matching the provided schema."
    )


def _parse_targeted_fragments(
    step: str, raw: str, plan: Mapping[str, Any], *, user_facing: bool = True
) -> dict[str, Any]:
    """Parse and lint fragment output itemwise; every fragment must address a target."""
    targets = set(plan["target_ids"])
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        return {"ok": False, "error": f"Codex targeted revision returned invalid JSON: {exc}"}
    if not isinstance(parsed, dict):
        return {"ok": False, "error": "Codex targeted revision returned non-object JSON"}
    parsed = dict(parsed)
    proposals_or_error = _parse_deferral_proposals(parsed.pop("deferral_proposals", []), plan)
    if isinstance(proposals_or_error, dict):
        return proposals_or_error
    deferral_proposals = proposals_or_error
    closures_or_error = _parse_closed_questions(parsed.pop("closed_questions", []), plan)
    if isinstance(closures_or_error, dict):
        return closures_or_error
    closed_questions = closures_or_error
    defenses_or_error = _parse_defenses(parsed.pop("defenses", []), plan)
    if isinstance(defenses_or_error, dict):
        return defenses_or_error
    defenses = defenses_or_error
    # An objection is answered ONE way: defending it and simultaneously
    # proposing to defer it are contradictory responses.
    contradictory = {entry["objection_id"] for entry in defenses} & {
        entry["objection_id"] for entry in deferral_proposals
    }
    if contradictory:
        return {
            "ok": False,
            "error": (
                "objections cannot be both defended and deferred in one revision: "
                + ", ".join(sorted(contradictory))
            ),
        }

    def _fragment_target(item: Mapping[str, Any]) -> str | None:
        replaced = item.get("replaces")
        if isinstance(replaced, str) and replaced.strip():
            return replaced if replaced in targets else None
        if step == "prior_art":
            derived = f"prior_art:{_slug(str(item.get('name') or ''))}"
            return derived if derived in targets else None
        item_id = item.get("id")
        return item_id if isinstance(item_id, str) and item_id in targets else None

    if step == "candidates":
        items = parsed.get("candidates")
        if set(parsed) != {"candidates"} or not isinstance(items, list):
            return {"ok": False, "error": "Codex targeted revision returned invalid candidates fragment"}
        fragments: list[dict[str, Any]] = []
        for item in items:
            candidate = _parse_candidate_approach(json.dumps(item, ensure_ascii=True, default=str))
            if not candidate.get("ok", True):
                return {"ok": False, "error": candidate["error"]}
            lint_errors = _candidate_empty_slot_lints(candidate)
            lint_errors.extend(_lint_candidate_stack_requirement(candidate, user_facing=user_facing))
            if lint_errors:
                return {"ok": False, "error": "; ".join(lint_errors)}
            if _fragment_target(candidate) is None:
                return {"ok": False, "error": f"candidate fragment {candidate.get('id')!r} addresses no target node"}
            fragments.append(candidate)
        return {"fragments": fragments, "deferral_proposals": deferral_proposals, "closed_questions": closed_questions, "defenses": defenses}
    if step == "prior_art":
        items = parsed.get("patterns")
        if set(parsed) != {"patterns"} or not isinstance(items, list):
            return {"ok": False, "error": "Codex targeted revision returned invalid patterns fragment"}
        fragments = []
        for item in items:
            pattern, error = _parse_prior_art_pattern(item)
            if pattern is None:
                return {"ok": False, "error": error}
            lint_errors = _lint_empty_slots(pattern)
            if lint_errors:
                return {"ok": False, "error": "; ".join(lint_errors)}
            if _fragment_target(pattern) is None:
                return {"ok": False, "error": f"pattern fragment {pattern.get('name')!r} addresses no target node"}
            fragments.append(pattern)
        return {"fragments": fragments, "deferral_proposals": deferral_proposals, "closed_questions": closed_questions, "defenses": defenses}
    if step == "risks":
        # Brief 29: parse the POPPED envelope like every other targeted branch —
        # re-parsing the raw string here rejected the brief 27 envelope keys
        # (deferral_proposals/closed_questions) that the step's own schema made
        # REQUIRED. Schema and parser are one contract; this drift turned a
        # schema-honoring model output into a permanent_rejection (run 4 v3,
        # fingerprint f3ca460972fdd57e, 2026-07-05).
        risks = _parse_surface_risks(json.dumps(parsed, ensure_ascii=True, default=str))
        if not risks.get("ok", True):
            return risks
        fragments = []
        for item in risks["risks"]:
            lint_errors = _lint_empty_slots(item)
            if lint_errors:
                return {"ok": False, "error": "; ".join(lint_errors)}
            if _fragment_target(item) is None:
                return {"ok": False, "error": f"risk fragment {item.get('id')!r} addresses no target node"}
            fragments.append(item)
        return {"fragments": fragments, "deferral_proposals": deferral_proposals, "closed_questions": closed_questions, "defenses": defenses}
    if step == "constraints":
        if set(parsed) != set(EXTRACT_CONSTRAINTS_FIELDS):
            return {"ok": False, "error": "Codex targeted revision returned invalid constraints fragment"}
        fragments = []
        for kind, field_name, item_fields in (
            ("hard", "hard_constraints", ("id",) + CONSTRAINT_ITEM_FIELDS),
            ("soft", "soft_preferences", ("id", "statement", "derivation", "rationale")),
        ):
            raw_items = parsed.get(field_name)
            if not isinstance(raw_items, list):
                return {"ok": False, "error": f"Codex targeted revision returned invalid {field_name}"}
            stripped = [
                {key: value for key, value in item.items() if key != "id"}
                for item in raw_items
                if isinstance(item, Mapping)
            ]
            if len(stripped) != len(raw_items):
                return {"ok": False, "error": f"Codex targeted revision returned invalid {field_name} item"}
            parsed_items = _parse_constraint_items(stripped, field=field_name, item_fields=item_fields[1:], kind=kind)
            if isinstance(parsed_items, dict):
                return {"ok": False, "error": parsed_items["error"]}
            for source_item, parsed_item in zip(raw_items, parsed_items):
                item_id = source_item.get("id")
                if (
                    not isinstance(item_id, str)
                    or not item_id.startswith(_CONSTRAINT_ID_PREFIXES[kind])
                    or item_id not in targets
                ):
                    return {"ok": False, "error": f"constraint fragment {item_id!r} addresses no target node"}
                lint_errors = _lint_empty_slots(parsed_item)
                if lint_errors:
                    return {"ok": False, "error": "; ".join(lint_errors)}
                fragments.append({"id": item_id, "kind": kind, "item": parsed_item})
        return {"fragments": fragments, "deferral_proposals": deferral_proposals, "closed_questions": closed_questions, "defenses": defenses}
    return {"ok": False, "error": f"step {step} does not support targeted revision"}


def _parse_deferral_proposals(value: Any, plan: Mapping[str, Any]) -> list[dict[str, str]] | dict[str, Any]:
    """Validate author deferral proposals (brief 27). Returns entries or an error dict."""
    if value in (None, ""):
        value = []
    if not isinstance(value, list):
        return {"ok": False, "error": "Codex targeted revision returned invalid deferral_proposals"}
    objection_ids = {str(objection.get("objection_id") or "") for objection in plan.get("objections", [])}
    entries: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, Mapping):
            return {"ok": False, "error": "Codex targeted revision returned invalid deferral proposal item"}
        objection_id = item.get("objection_id")
        executable_check = item.get("executable_check")
        defer_reason = item.get("defer_reason")
        if (
            not isinstance(objection_id, str)
            or objection_id not in objection_ids
            or objection_id in seen
            or not isinstance(executable_check, str)
            or not executable_check.strip()
            or not isinstance(defer_reason, str)
            or not defer_reason.strip()
        ):
            return {"ok": False, "error": f"deferral proposal for {objection_id!r} is invalid or duplicates"}
        seen.add(objection_id)
        # Amendment 3: the proposal stays LEAN (no inline plan B) — an inline
        # contingency field would re-inflate the revision output schema, the
        # output-side cognitive-debt disease itself. The contingency is a
        # dedicated bounded call on the worker side (one question in, one plan
        # out), scheduled in parallel with Plan-A coding.
        entries.append(
            {"objection_id": objection_id, "executable_check": executable_check, "defer_reason": defer_reason}
        )
    return entries


def _parse_defenses(value: Any, plan: Mapping[str, Any]) -> list[dict[str, Any]] | dict[str, Any]:
    """Validate author defenses (brief 33). Returns entries or an error dict.

    A defense must name a known objection id, carry a non-empty defense text,
    and cite at least one non-empty evidence citation — an uncited defense is
    insistence too, and the same evidence discipline binds both sides of the
    conversation (zero-softening is symmetric). [PROVISIONAL] the >=1 citation
    floor; citations may be tree node ids, repository files, or external
    sources — free strings, judged by the reviewer, never resolved by code.
    """
    if value in (None, ""):
        value = []
    if not isinstance(value, list):
        return {"ok": False, "error": "Codex targeted revision returned invalid defenses"}
    objection_ids = {str(objection.get("objection_id") or "") for objection in plan.get("objections", [])}
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, Mapping):
            return {"ok": False, "error": "Codex targeted revision returned an invalid defense item"}
        objection_id = item.get("objection_id")
        defense = item.get("defense")
        citations = item.get("evidence_citations")
        clean_citations = [
            citation.strip()
            for citation in (citations if isinstance(citations, list) else [])
            if isinstance(citation, str) and citation.strip()
        ]
        if (
            not isinstance(objection_id, str)
            or objection_id not in objection_ids
            or objection_id in seen
            or not isinstance(defense, str)
            or not defense.strip()
            or not clean_citations
        ):
            return {
                "ok": False,
                "error": (
                    f"defense for {objection_id!r} is invalid or duplicates: it must name a known objection "
                    "id, state why the objection is mistaken or inapplicable, and cite at least one evidence "
                    "citation (tree node id, repository file, or external source)"
                ),
            }
        seen.add(objection_id)
        entries.append(
            {"objection_id": objection_id, "defense": defense, "evidence_citations": clean_citations}
        )
    return entries


def _parse_closed_questions(value: Any, plan: Mapping[str, Any]) -> list[dict[str, str]] | dict[str, Any]:
    """Validate author closure declarations (brief 28). Returns entries or an error.

    Byte-match only: an unknown or paraphrased question string is a typed reject
    whose feedback names the current open questions verbatim — no silent drop,
    no fuzzy guess. routed_to_patch requires a non-empty executable_check
    (the question-shaped must-answer record); settled_on_paper forbids one.
    """
    if value in (None, ""):
        value = []
    if not isinstance(value, list):
        return {"ok": False, "error": "Codex targeted revision returned invalid closed_questions"}
    open_questions = [question for question in plan.get("open_questions", []) if isinstance(question, str)]
    entries: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, Mapping):
            return {"ok": False, "error": "Codex targeted revision returned an invalid closed question item"}
        question = item.get("question")
        resolution = item.get("resolution")
        closure_kind = item.get("closure_kind")
        executable_check = item.get("executable_check")
        if not isinstance(question, str) or question not in open_questions or question in seen:
            candidates = json.dumps(open_questions, ensure_ascii=True)
            return {
                "ok": False,
                "error": (
                    f"closed question does not byte-match an open question (or duplicates): {question!r}; "
                    f"current open questions (use one VERBATIM): {candidates}"
                ),
            }
        if not isinstance(resolution, str) or not resolution.strip():
            return {"ok": False, "error": f"closed question {question!r} is missing a resolution"}
        if not isinstance(executable_check, str):
            return {"ok": False, "error": f"closed question {question!r} has an invalid executable_check"}
        if closure_kind == "routed_to_patch":
            if not executable_check.strip():
                return {"ok": False, "error": f"closed question {question!r} routed_to_patch requires a non-empty executable_check"}
        elif closure_kind == "settled_on_paper":
            if executable_check.strip():
                return {"ok": False, "error": f"closed question {question!r} settled_on_paper must not carry an executable_check"}
        else:
            return {"ok": False, "error": f"closed question {question!r} has an invalid closure_kind"}
        seen.add(question)
        entries.append(
            {
                "question": question,
                "resolution": resolution,
                "closure_kind": closure_kind,
                "executable_check": executable_check,
            }
        )
    return entries


def _non_deferrable_surfaces(objection: Mapping[str, Any], patch_series_view: Mapping[str, Any]) -> list[str]:
    """Public-contract surfaces that mechanically reject a deferral proposal.

    Canon (deferred_to_code_canon 2026-07-05): paper decides public contracts;
    code resolves empirical uncertainty. Hard-blocked ONLY where mechanically
    certain — everything else goes to the reviewer (when in doubt, the reviewer
    decides rather than a hard block):
    - requester-authority objections: requester decisions are paper decisions by
      definition (existing enum, no prose reading);
    - a requester-specified tech_stack with the objection anchored on the
      decision family: the stack choice is the requester's public contract;
    - [PROVISIONAL] compat-axis objections anchored on HARD constraint nodes:
      hard constraints are the compatibility commitments the cover letter
      exposes (soft preferences are not commitments). Live pair this encodes:
      "skip/reset in the first public CLI contract" (compat x hard constraint)
      is not deferrable; "non-TTY status cadence" (ergonomics, anchored
      elsewhere) is.
    """
    reasons: list[str] = []
    anchors = [anchor for anchor in objection.get("anchor_node_ids", []) if isinstance(anchor, str)]
    if str(objection.get("resolution_authority") or "") == "requester":
        reasons.append("requester-authority decision (paper decides)")
    tech_stack = patch_series_view.get("tech_stack") if isinstance(patch_series_view, Mapping) else None
    if (
        isinstance(tech_stack, Mapping)
        and tech_stack.get("provenance") == "requester_specified"
        and any(anchor.startswith("decision:") for anchor in anchors)
    ):
        reasons.append("requester-specified tech_stack decision surface")
    if str(objection.get("axis") or "") == "compat" and any(
        anchor.startswith("constraint:hard:") for anchor in anchors
    ):
        reasons.append("compat-axis hard-constraint commitment (public compatibility contract)")
    return reasons


def _merge_step_fragments(step: str, committed: Mapping[str, Any], fragments: list[dict[str, Any]]) -> dict[str, Any]:
    """Merge fragments into the committed step component; non-targets byte-preserved.

    Memento (brief 25): this merge seam is the byte-preservation invariant. A
    revised fragment replaces exactly its target item (or, with a declared
    replacement, removes the target and appends the successor); every other item
    is carried through as the SAME object, so its canonical content — and hence
    the revision delta ledger and review delta scoping — sees zero change.
    """
    if step == "constraints":
        merged_constraints = {
            "hard_constraints": list(committed.get("hard_constraints", [])),
            "soft_preferences": list(committed.get("soft_preferences", [])),
        }
        field_by_kind = {"hard": "hard_constraints", "soft": "soft_preferences"}
        for fragment in fragments:
            kind = fragment["kind"]
            index = int(fragment["id"].rsplit(":", 1)[-1]) - 1
            items = merged_constraints[field_by_kind[kind]]
            if not 0 <= index < len(items):
                continue  # enum-closed ids make this unreachable; stay total
            items[index] = fragment["item"]
        extra_keys = {key: value for key, value in committed.items() if key not in merged_constraints}
        return {**extra_keys, **merged_constraints}

    collection_key = {"prior_art": "patterns", "candidates": "candidates", "risks": "risks"}[step]
    items = list(committed.get(collection_key, []))

    def _committed_item_id(item: Any) -> str:
        if not isinstance(item, Mapping):
            return ""
        item_id = item.get("id")
        if isinstance(item_id, str) and item_id:
            return item_id
        if step == "prior_art":
            return f"prior_art:{_slug(str(item.get('name') or ''))}"
        return ""

    for fragment in fragments:
        replaced = fragment.get("replaces")
        if step == "prior_art":
            fragment_id = f"prior_art:{_slug(str(fragment.get('name') or ''))}"
        else:
            fragment_id = str(fragment.get("id") or "")
        if isinstance(replaced, str) and replaced.strip():
            items = [item for item in items if _committed_item_id(item) != replaced]
            items.append(fragment)
            continue
        replaced_in_place = False
        for index, item in enumerate(items):
            if _committed_item_id(item) == fragment_id:
                items[index] = fragment
                replaced_in_place = True
                break
        if not replaced_in_place:
            items.append(fragment)
    merged = {key: value for key, value in committed.items() if key != collection_key}
    merged[collection_key] = items
    return merged


def _revise_step_nodes(
    step: str,
    repo: str | Path,
    components: Mapping[str, Any],
    plan: Mapping[str, Any],
    *,
    patch_series_view: Mapping[str, Any] | None = None,
    author_self_history: Any | None = None,
    deferral_collector: list[dict[str, Any]] | None = None,
    closure_collector: list[dict[str, Any]] | None = None,
    defense_collector: list[dict[str, Any]] | None = None,
    log_ctx: org_log.RunContext | None = None,
) -> dict[str, Any]:
    """Run one targeted step revision in a bounded window and merge code-side."""
    committed_key = {"constraints": "constraints", "prior_art": "prior_art", "candidates": "candidates", "risks": "risks"}[step]
    committed = components[committed_key]
    feedback: list[str] = []
    last_error = ""
    retry_state = codex_exec.RejectionRetryState()
    for attempt in range(MAX_TECHNICAL_APPROACH_NODE_REGENERATIONS + 1):
        attempt_ctx = log_ctx.child(attempt=attempt + 1) if log_ctx is not None else None
        prompt = _targeted_revision_prompt(step, plan, author_self_history)
        if feedback:
            prompt += "\nPrevious output failed deterministic validation. Fix these issues:\n" + "\n".join(
                f"- {item}" for item in feedback
            )
        run = codex_exec.run_json(
            Path(repo),
            schema=_targeted_revision_schema(step, plan),
            prompt=prompt,
            schema_filename=f"patch_series-revise-{step}.schema.json",
            output_filename=f"patch_series-revised-{step}.json",
            failure_label=f"Codex targeted {step} revision",
            ctx=attempt_ctx,
        )
        if not run["ok"]:
            return {"ok": False, "error": run["error"]}
        parsed = _parse_targeted_fragments(
            step, run["raw"], plan, user_facing=_deliverable_is_user_facing(patch_series_view)
        )
        if parsed.get("ok", True):
            # Brief 27: routing is judgment, bookkeeping is code. Author-proposed
            # deferrals pass the mechanical public-contract guard (allowed ones go
            # to the reviewer via the ledger; blocked ones are recorded, never
            # silently dropped), then the fragment merge proceeds unchanged.
            if deferral_collector is not None:
                objections_by_id = {
                    str(objection.get("objection_id") or ""): objection for objection in plan.get("objections", [])
                }
                for proposal in parsed.get("deferral_proposals", []):
                    objection = objections_by_id.get(proposal["objection_id"], {})
                    blocked = _non_deferrable_surfaces(objection, patch_series_view or {})
                    entry = {**proposal, "step": step}
                    if blocked:
                        entry["non_deferrable"] = blocked
                    deferral_collector.append(entry)
            if closure_collector is not None:
                for closure in parsed.get("closed_questions", []):
                    closure_collector.append({**closure, "step": step})
            if defense_collector is not None:
                # Brief 33: a defense is a first-class response; it flows to the
                # ledger (defended_with_evidence) and travels verbatim into the
                # next review round's input. Memento: the reviewer is a filter,
                # never a ceiling.
                for defense in parsed.get("defenses", []):
                    defense_collector.append({**defense, "step": step})
            return _merge_step_fragments(step, committed, parsed["fragments"])
        last_error = parsed["error"]
        feedback, permanent = _classify_validation_retry(
            retry_state,
            validator_id=f"receive.revise_{step}",
            errors=last_error,
            attempt=attempt + 1,
            ctx=attempt_ctx,
        )
        if permanent is not None:
            return {"ok": False, "error": permanent["error"]} | permanent
    return {
        "ok": False,
        "error": f"Codex targeted {step} revision remained invalid after "
        f"{MAX_TECHNICAL_APPROACH_NODE_REGENERATIONS + 1} attempts: {last_error}",
    }


def _prose_provenance_draws_on(tree: Any) -> list[str]:
    """draws_on entries reclassified as prose provenance (no link fabricated).

    Brief 24 audit trail: a slug that resolves to neither a current prior_art
    node nor declared lineage never existed as a node in any generation, so
    assembly withholds the link; the ledger records the withheld slug ids so
    the refusal to FABRICATE is never silent.
    """
    problem = tree.get("problem", {}) if isinstance(tree, Mapping) else {}
    question = problem.get("question", {}) if isinstance(problem, Mapping) else {}
    targets = _prior_art_link_targets(problem.get("prior_art") if isinstance(problem, Mapping) else [])
    withheld: set[str] = set()
    candidates = question.get("candidates", []) if isinstance(question, Mapping) else []
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            continue
        for draw in candidate.get("draws_on", []):
            if isinstance(draw, str):
                slug_id = f"prior_art:{_slug(draw)}"
                if slug_id not in targets:
                    withheld.add(slug_id)
    return sorted(withheld)


def _questions_artifact_with_routed_closures(
    repo: Path,
    branch: str,
    routed_questions: list[Mapping[str, Any]],
    author_version: int,
) -> dict[str, Any]:
    """Append question-shaped must-answer entries (brief 28 routed_to_patch)."""
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
        if isinstance(parsed, Mapping) and isinstance(parsed.get("questions_the_patch_must_answer"), list):
            artifact["questions_the_patch_must_answer"] = [
                dict(entry) for entry in parsed["questions_the_patch_must_answer"] if isinstance(entry, Mapping)
            ]
    known = {str(entry.get("objection_id") or "") for entry in artifact["questions_the_patch_must_answer"]}
    for closure in routed_questions:
        entry_id = f"open-question:{_slug(str(closure.get('question') or ''))[:48]}"
        if entry_id in known:
            continue
        artifact["questions_the_patch_must_answer"].append(
            {
                "objection_id": entry_id,
                "status": "must_resolve_in_patch",
                "executable_check": str(closure.get("executable_check") or ""),
                "why_resolved_in_patch": str(closure.get("resolution") or ""),
                "if_check_fails_implement": "",
                "claim": str(closure.get("question") or ""),
                "anchor_node_ids": [],
                "axis": "approach",
                "accepted_in_review_round": author_version - 1,
            }
        )
    return artifact


def _revision_delta_record(
    patch_series_id: str,
    review_round: int,
    author_version: int,
    previous_tree: Any,
    revised_tree: Any,
    prior_open_objections: list[Mapping[str, Any]],
    reconciliation_no_successor: list[Mapping[str, str]] | None = None,
    deferral_proposals: list[Mapping[str, Any]] | None = None,
    closed_questions: list[Mapping[str, Any]] | None = None,
    defenses: list[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Machine-readable v(N)->v(N+1) changelog committed with the reform (component B).

    Everything here is derived mechanically: node-set and content-hash diffs, the
    replacement declarations the model carried in its step outputs, and per-objection
    response classification from anchors x delta. Code never writes judgment content;
    responses remain binding author self-history and deterministic bookkeeping.
    """
    before = _tree_node_contents(previous_tree)
    after = _tree_node_contents(revised_tree)
    added = sorted(set(after) - set(before))
    pruned = sorted(set(before) - set(after))
    changed = sorted(node_id for node_id in set(before) & set(after) if before[node_id] != after[node_id])
    replacements, successors = _revision_replacements(previous_tree, revised_tree)
    changed_or_added = set(changed) | set(added)
    pruned_set = set(pruned)
    allowed_proposals = {
        str(item.get("objection_id") or ""): item
        for item in deferral_proposals or []
        if not item.get("non_deferrable")
    }
    blocked_proposals = [dict(item) for item in deferral_proposals or [] if item.get("non_deferrable")]
    defenses_by_id = {str(item.get("objection_id") or ""): item for item in defenses or []}
    responses: list[dict[str, Any]] = []
    for objection in prior_open_objections:
        anchors = [anchor for anchor in objection.get("anchor_node_ids", []) if isinstance(anchor, str) and anchor]
        successor_ids = sorted(
            {successor for anchor in anchors if anchor in pruned_set for successor in successors.get(anchor, [])}
        )
        defense = defenses_by_id.get(str(objection.get("objection_id") or ""))
        if successor_ids:
            classification = "superseded_by_replacement"
        elif any(anchor in changed_or_added or (anchor in pruned_set and anchor.startswith(_REINDEXED_FAMILY_ID_PREFIXES)) for anchor in anchors):
            classification = "addressed_by_change"
        elif defense is not None:
            # Brief 33 (asymmetric_review_canon): explicit "no node change;
            # here is why the objection is mistaken/inapplicable" is a
            # first-class answer (kernel explain-duty), not an unaddressed
            # objection. The defense text and citations travel verbatim in the
            # author's committed self-history, never into blank reviewer paper.
            classification = "defended_with_evidence"
        else:
            classification = "unaddressed"
        response = {
            "objection_id": str(objection.get("objection_id") or ""),
            "classification": classification,
            "anchor_node_ids": anchors,
            "successor_node_ids": successor_ids,
        }
        if defense is not None:
            # Attached whatever the classification: a defense alongside an
            # incidental anchor change is still the author's answer on record.
            response["defense"] = str(defense.get("defense") or "")
            response["evidence_citations"] = [
                str(citation) for citation in defense.get("evidence_citations", []) if isinstance(citation, str)
            ]
        if response["objection_id"].startswith(IMPLEMENTATION_EXPERIENCE_OBJECTION_PREFIX) and response[
            "classification"
        ] in ("addressed_by_change", "superseded_by_replacement"):
            # Amendment 5 item 4: the revision consumed running-code evidence.
            response["classification"] = "revised_from_implementation_experience"
        # Brief 27: an author deferral proposal overrides the anchors-x-delta
        # classification — the response to this objection IS the proposal. It
        # remains author history; a blank reviewer never receives this reply.
        proposal = allowed_proposals.get(response["objection_id"])
        if proposal is not None:
            response["classification"] = "deferral_proposed"
            response["executable_check"] = str(proposal.get("executable_check") or "")
            response["defer_reason"] = str(proposal.get("defer_reason") or "")
        responses.append(response)
    return {
        "patch_series_id": patch_series_id,
        "review_round": review_round,
        "author_version": author_version,
        "added_node_ids": added,
        "changed_node_ids": changed,
        "pruned_node_ids": pruned,
        "node_replacements": replacements,
        "prose_provenance_not_linked": _prose_provenance_draws_on(revised_tree),
        "reconciliation_no_successor": [dict(item) for item in reconciliation_no_successor or []],
        "deferral_rejected_non_deferrable": blocked_proposals,
        # Brief 28: the public disposition record (W3C minutes / Rust tracking
        # issue equivalent) — the question left the living tree; its closure
        # lives here, group-ratified by riding the next delta review.
        "closed_questions": [
            {
                "question": str(closure.get("question") or ""),
                "resolution": str(closure.get("resolution") or ""),
                "closure_kind": str(closure.get("closure_kind") or ""),
                "executable_check": str(closure.get("executable_check") or ""),
                "step": str(closure.get("step") or ""),
                "decided_after_review_round": review_round,
            }
            for closure in closed_questions or []
        ],
        "objection_responses": responses,
        "memento": [
            "Kernel v(N)->v(N+1) changelog, machine-readable: what changed, what was replaced, and how each",
            "prior objection was answered structurally. Author history reads it; blank reviewers do not.",
        ],
    }


def _author_answers(
    objections: list[Mapping[str, Any]],
    changed_nodes: list[str],
    changed: bool,
) -> list[dict[str, Any]]:
    answers: list[dict[str, Any]] = []
    for objection in objections:
        objection_id = str(objection.get("objection_id", ""))
        anchors = [str(anchor) for anchor in objection.get("anchor_node_ids", []) if isinstance(anchor, str)]
        # Brief 31 sweep: this consumer deliberately keeps the vocabulary
        # router on BOTH sides instead of a tree lookup - anchors may point at
        # pruned nodes (old tree) and changed_nodes at newly added ones (new
        # tree), so no single tree resolves them all; same-router symmetry is
        # what makes the step comparison meaningful. With the corrected
        # unprefixed default, candidate churn now answers candidate anchors
        # truthfully; the old "problem" default let problem-step churn
        # spuriously classify candidate-anchored objections as answered.
        touched = [node for node in changed_nodes if node == patch_series_bodies.LEGACY_COVER_PATH or node in anchors or _step_for_node_id(node) in {_step_for_node_id(anchor) for anchor in anchors}]
        if changed and touched:
            answers.append(
                {
                    "objection_id": objection_id,
                    "status": "answered",
                    "answer": "changed",
                    "changed_node_ids": touched,
                    "justification": "",
                }
            )
        else:
            answers.append(
                {
                    "objection_id": objection_id,
                    "status": "carried-forward",
                    "answer": "unchanged-but-justified",
                    "changed_node_ids": [],
                    "justification": (
                        "The author re-ran the anchored formation step with the objection as feedback; "
                        "the resulting derivation remained unchanged, so this is carried forward for reviewer judgment."
                    ),
                }
            )
    return answers


def _requester_assumption_answers(entries: list[Mapping[str, str]]) -> list[dict[str, Any]]:
    return [
        {
            "objection_id": entry["objection_id"],
            "status": "assumption_recorded",
            "answer": "requester-authority-assumption-recorded",
            "changed_node_ids": [patch_series_bodies.LEGACY_COVER_PATH],
            "assumption": dict(entry),
            "justification": "Requester-authority objection recorded as a non-blocking assumption and mirrored to open_questions.",
        }
        for entry in entries
    ]


def _changes_since_body(previous_version: int, answers: list[Mapping[str, Any]]) -> str:
    lines = [f"Changes since v{previous_version}:"]
    for answer in answers:
        changed = ", ".join(answer.get("changed_node_ids", [])) or "none"
        detail = str(answer.get("justification") or changed)
        lines.append(f"- {answer.get('objection_id')}: {answer.get('answer')} ({detail})")
    return "\n".join(lines)


def _author_response_record(
    patch_series_id: str,
    round_record: Mapping[str, Any],
    version: int,
    answers: list[Mapping[str, Any]],
    affected_steps: list[str],
    changed_nodes: list[str],
    requester_assumptions: list[Mapping[str, str]],
) -> tuple[str, dict[str, Any]]:
    review_module = importlib.import_module("ai_org.patchwork_queue.review")

    round_number = _review_round_number(round_record)
    path = review_module.author_response_record_path(round_number, version)
    status_by_id = {str(answer.get("objection_id")): answer for answer in answers}
    objections = []
    for objection in round_record.get("objections", []):
        if not isinstance(objection, Mapping):
            continue
        updated = dict(objection)
        answer = status_by_id.get(str(updated.get("objection_id")))
        if answer is not None:
            updated["status"] = answer["status"]
            updated["author_answer"] = dict(answer)
        objections.append(updated)
    record = {
        "patch_series_id": patch_series_id,
        "review_round": round_number,
        "author_version": version,
        "affected_steps": affected_steps,
        "changed_node_ids": changed_nodes,
        "requester_assumption_notes": [dict(entry) for entry in requester_assumptions],
        "objections": objections,
        "memento": [
            "LKML author-reworks rule: reviewers object; author reforms and reposts.",
            "Same patch series branch vN+1 commit with Changes since vN body; no new branch.",
            "Review round records remain immutable; this author response is append-only.",
        ],
    }
    return path, record


def _canonical_json(value: Any) -> str:
    return json.dumps(_json_safe(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _classify_validation_retry(
    state: codex_exec.RejectionRetryState,
    *,
    validator_id: str,
    errors: str | list[str],
    attempt: int,
    ctx: org_log.RunContext | None,
) -> tuple[list[str], dict[str, Any] | None]:
    error_items = [errors] if isinstance(errors, str) else list(errors)
    rejection = codex_exec.validation_rejection(validator_id, [str(item) for item in error_items])
    decision = state.classify(rejection, attempt=attempt, ctx=ctx)
    if decision.permanent:
        return [], {
            "ok": False,
            "error": codex_exec.permanent_rejection_error(decision),
            **codex_exec.permanent_rejection_payload(decision),
        }
    return [codex_exec.format_rejection_feedback(rejection)], None


def _normalize_problem(
    patch_series_view: dict[str, Any],
    context: Mapping[str, Any] | None = None,
    *,
    log_ctx: org_log.RunContext | None = None,
) -> dict[str, Any]:
    """Form step 1 of the documented 10-step Technical Approach procedure."""
    if not _is_patch_series_view(patch_series_view):
        return _normalized_problem_error("normalize_problem requires a grounded registry patch series view")

    repo = _repo_from_context(context)
    feedback: list[str] = []
    last_lint_error = ""
    retry_state = codex_exec.RejectionRetryState()
    for attempt in range(MAX_NORMALIZE_PROBLEM_REGENERATIONS + 1):
        attempt_ctx = log_ctx.child(attempt=attempt + 1) if log_ctx is not None else None
        run = codex_exec.run_json(
            repo,
            schema=build_normalize_problem_schema(),
            prompt=_normalize_problem_prompt(patch_series_view, context, feedback if attempt else None),
            schema_filename="patch_series-normalize-problem.schema.json",
            output_filename="patch_series-normalized-problem.json",
            failure_label="Codex problem normalization",
            ctx=attempt_ctx,
        )
        if not run["ok"]:
            return _normalized_problem_error(run["error"])
        parsed = _parse_normalized_problem(run["raw"])
        if not parsed.get("ok", True):
            return parsed

        lint_errors = _lint_normalized_problem(parsed, patch_series_view)
        if not lint_errors:
            return parsed
        last_lint_error = "; ".join(lint_errors)
        feedback, permanent = _classify_validation_retry(
            retry_state,
            validator_id="receive.normalize_problem",
            errors=lint_errors,
            attempt=attempt + 1,
            ctx=attempt_ctx,
        )
        if permanent is not None:
            return _normalized_problem_error(permanent["error"]) | permanent

    return _normalized_problem_error(
        "Codex problem normalization remained unmeasurable after "
        f"{MAX_NORMALIZE_PROBLEM_REGENERATIONS + 1} attempts: {last_lint_error}"
    )


def _extract_constraints(
    patch_series_view: dict[str, Any],
    repo: str | Path,
    context: Mapping[str, Any] | None = None,
    approach: Mapping[str, Any] | None = None,
    *,
    log_ctx: org_log.RunContext | None = None,
) -> dict[str, Any]:
    """Form step 2 of the documented 10-step Technical Approach procedure."""
    # Step 2 of 10: attach constraints to the accumulated approach before approach generation.
    if not _is_patch_series_view(patch_series_view):
        return _constraints_error("extract_constraints requires a grounded registry patch series view")

    repo_path = Path(repo).resolve()
    accumulated_approach = _constraints_approach_context(approach)
    feedback: list[str] = []
    last_error = ""
    retry_state = codex_exec.RejectionRetryState()
    for attempt in range(MAX_EXTRACT_CONSTRAINTS_REGENERATIONS + 1):
        attempt_ctx = log_ctx.child(attempt=attempt + 1) if log_ctx is not None else None
        run = codex_exec.run_json(
            repo_path,
            schema=build_extract_constraints_schema(),
            prompt=_extract_constraints_prompt(
                patch_series_view,
                repo_path,
                context,
                accumulated_approach,
                feedback if attempt else None,
            ),
            schema_filename="patch_series-extract-constraints.schema.json",
            output_filename="patch_series-extracted-constraints.json",
            failure_label="Codex constraint extraction",
            ctx=attempt_ctx,
        )
        if not run["ok"]:
            return _constraints_error(run["error"])
        parsed = _parse_constraints(run["raw"])
        if not parsed.get("ok", True):
            last_error = parsed["error"]
            feedback, permanent = _classify_validation_retry(
                retry_state,
                validator_id="receive.extract_constraints",
                errors=last_error,
                attempt=attempt + 1,
                ctx=attempt_ctx,
            )
            if permanent is not None:
                return _constraints_error(permanent["error"]) | permanent
            continue
        lint_errors = _lint_constraints(parsed, accumulated_approach)
        if not lint_errors:
            return parsed
        last_error = "; ".join(lint_errors)
        feedback, permanent = _classify_validation_retry(
            retry_state,
            validator_id="receive.extract_constraints",
            errors=lint_errors,
            attempt=attempt + 1,
            ctx=attempt_ctx,
        )
        if permanent is not None:
            return _constraints_error(permanent["error"]) | permanent

    return _constraints_error(
        "Codex constraint extraction remained invalid after "
        f"{MAX_EXTRACT_CONSTRAINTS_REGENERATIONS + 1} attempts: {last_error}"
    )


def _build_prior_art_map(
    patch_series_view: dict[str, Any],
    repo: str | Path,
    context: Mapping[str, Any] | None = None,
    approach: Mapping[str, Any] | None = None,
    reference_terms: Any | None = None,
    *,
    log_ctx: org_log.RunContext | None = None,
    revision: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Form step 3 of the documented 10-step Technical Approach procedure."""
    # Step 3 of 10: attach prior art to the accumulated approach before candidate generation.
    if not _is_patch_series_view(patch_series_view):
        return _prior_art_error("build_prior_art_map requires a grounded registry patch series view")

    repo_path = Path(repo).resolve()
    accumulated_approach = _prior_art_approach_context(approach)
    concepts = (
        _precedent_terms_for_prior_art(reference_terms)
        if reference_terms is not None
        else _prior_art_key_concepts(patch_series_view, context, accumulated_approach)
    )
    try:
        reference_facets = _read_prior_art_precedent_facets(
            concepts,
            context,
            allow_expand=reference_terms is None,
        )
    except Exception as exc:
        return _prior_art_error(f"precedent-store prior-art read failed: {exc}")

    feedback: list[str] = []
    last_error = ""
    retry_state = codex_exec.RejectionRetryState()
    for attempt in range(MAX_PRIOR_ART_REGENERATIONS + 1):
        attempt_ctx = log_ctx.child(attempt=attempt + 1) if log_ctx is not None else None
        run = codex_exec.run_json(
            repo_path,
            schema=_revision_prior_art_schema() if revision else build_prior_art_map_schema(),
            prompt=_prior_art_map_prompt(
                patch_series_view,
                repo_path,
                concepts,
                reference_facets,
                context,
                accumulated_approach,
                feedback if attempt else None,
                revision=revision,
            ),
            schema_filename="patch_series-prior-art-map.schema.json",
            output_filename="patch_series-prior-art-map.json",
            failure_label="Codex prior-art mapping",
            ctx=attempt_ctx,
        )
        if not run["ok"]:
            return _prior_art_error(run["error"])
        parsed = _parse_prior_art_map(run["raw"])
        if not parsed.get("ok", True):
            last_error = parsed["error"]
            feedback, permanent = _classify_validation_retry(
                retry_state,
                validator_id="receive.build_prior_art_map",
                errors=last_error,
                attempt=attempt + 1,
                ctx=attempt_ctx,
            )
            if permanent is not None:
                return _prior_art_error(permanent["error"]) | permanent
            continue

        lint_errors = _lint_prior_art_map(parsed, reference_facets, accumulated_approach)
        if not lint_errors:
            _attach_prior_art_precedent_facets(parsed, reference_facets)
            return parsed
        last_error = "; ".join(lint_errors)
        feedback, permanent = _classify_validation_retry(
            retry_state,
            validator_id="receive.build_prior_art_map",
            errors=lint_errors,
            attempt=attempt + 1,
            ctx=attempt_ctx,
        )
        if permanent is not None:
            return _prior_art_error(permanent["error"]) | permanent

    return _prior_art_error(
        "Codex prior-art mapping remained invalid after "
        f"{MAX_PRIOR_ART_REGENERATIONS + 1} attempts: {last_error}"
    )


def _generate_candidates(
    normalized_problem: Mapping[str, Any],
    constraints: Mapping[str, Any],
    prior_art_map: Mapping[str, Any],
    context: Mapping[str, Any] | None = None,
    accumulated_approach: Mapping[str, Any] | None = None,
    *,
    log_ctx: org_log.RunContext | None = None,
    revision: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Form step 4 of the documented 10-step Technical Approach procedure."""
    # Step 4 of 10: generate candidate approaches from the already-normalized problem, constraints, and prior art.
    if not all(isinstance(value, Mapping) for value in (normalized_problem, constraints, prior_art_map)):
        return _candidate_generation_error("generate_candidates requires outputs from steps 1-3")

    repo = _repo_from_context(context)
    candidates: list[dict[str, Any]] = []
    pruned_candidates: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    seen_kinds: set[str] = set()
    for kind in ("minimal_local", "repo_native", "general_architectural"):
        parsed = _generate_candidate_node(
            repo,
            normalized_problem,
            constraints,
            prior_art_map,
            kind,
            expected_kind=kind,
            candidates=candidates,
            pruned_candidates=pruned_candidates,
            seen_ids=seen_ids,
            seen_kinds=seen_kinds,
            context=context,
            accumulated_approach=accumulated_approach,
            log_ctx=log_ctx,
            revision=revision,
        )
        if not parsed.get("ok", True):
            return parsed

    retry_feedback = _candidate_prune_feedback(pruned_candidates)
    for attempt in range(MAX_TECHNICAL_APPROACH_NODE_REGENERATIONS + 1):
        if len(candidates) >= 2:
            break
        parsed = _generate_candidate_node(
            repo,
            normalized_problem,
            constraints,
            prior_art_map,
            "additional_feasible",
            expected_kind=None,
            candidates=candidates,
            pruned_candidates=pruned_candidates,
            seen_ids=seen_ids,
            seen_kinds=seen_kinds,
            context=context,
            accumulated_approach=accumulated_approach,
            initial_feedback=retry_feedback,
            schema_suffix=f"additional-{attempt + 1}",
            log_ctx=log_ctx,
            revision=revision,
        )
        if not parsed.get("ok", True):
            return parsed
        retry_feedback = _candidate_prune_feedback(pruned_candidates)

    if not candidates:
        details = "; ".join(item["objection"] for item in pruned_candidates)
        return _candidate_generation_error(
            "no feasible candidates were generated" + (f": {details}" if details else "")
        )

    result: dict[str, Any] = {"candidates": candidates}
    if pruned_candidates:
        result["pruned_candidates"] = pruned_candidates
    if len(candidates) < 2:
        result["degradation_notes"] = [
            "Candidate generation proceeded with one feasible candidate after bounded retries because no "
            "additional distinct feasible alternatives were produced."
        ]
    return result


def _generate_candidate_node(
    repo: Path,
    normalized_problem: Mapping[str, Any],
    constraints: Mapping[str, Any],
    prior_art_map: Mapping[str, Any],
    candidate_kind: str,
    *,
    expected_kind: str | None,
    candidates: list[dict[str, Any]],
    pruned_candidates: list[dict[str, str]],
    seen_ids: set[str],
    seen_kinds: set[str],
    context: Mapping[str, Any] | None,
    accumulated_approach: Mapping[str, Any] | None,
    initial_feedback: list[str] | None = None,
    schema_suffix: str | None = None,
    log_ctx: org_log.RunContext | None = None,
    revision: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    feedback: list[str] = list(initial_feedback or [])
    last_error = ""
    parsed: dict[str, Any] | None = None
    retry_state = codex_exec.RejectionRetryState()
    for attempt in range(MAX_TECHNICAL_APPROACH_NODE_REGENERATIONS + 1):
        attempt_ctx = log_ctx.child(attempt=attempt + 1) if log_ctx is not None else None
        run = codex_exec.run_json(
            repo,
            schema=_revision_candidate_schema() if revision else build_candidate_approach_schema(),
            prompt=_generate_candidate_prompt(
                normalized_problem,
                constraints,
                prior_art_map,
                candidate_kind,
                candidates,
                context,
                accumulated_approach,
                feedback if feedback else None,
                revision=revision,
            ),
            schema_filename=f"patch_series-candidate-{schema_suffix or candidate_kind}.schema.json",
            output_filename=f"patch_series-candidate-{schema_suffix or candidate_kind}.json",
            failure_label="Codex candidate generation",
            ctx=attempt_ctx,
        )
        if not run["ok"]:
            return _candidate_generation_error(run["error"])
        parsed = _parse_candidate_approach(run["raw"], expected_kind=expected_kind)
        if not parsed.get("ok", True):
            last_error = parsed["error"]
            feedback, permanent = _classify_validation_retry(
                retry_state,
                validator_id=f"receive.generate_candidate.{candidate_kind}",
                errors=last_error,
                attempt=attempt + 1,
                ctx=attempt_ctx,
            )
            if permanent is not None:
                return _candidate_generation_error(permanent["error"]) | permanent
            continue

        lint_errors = _candidate_empty_slot_lints(parsed)
        lint_errors.extend(
            _lint_candidate_stack_requirement(parsed, user_facing=_deliverable_user_facing_from_context(context))
        )
        if parsed["id"] in seen_ids or _candidate_id_was_pruned(parsed["id"], pruned_candidates):
            lint_errors.append(f"id duplicates an earlier candidate: {parsed['id']}")
        if parsed["kind"] in seen_kinds:
            lint_errors.append(f"kind duplicates an earlier candidate: {parsed['kind']}")
        if not lint_errors:
            seen_ids.add(parsed["id"])
            seen_kinds.add(parsed["kind"])
            candidates.append(parsed)
            return {"ok": True}
        last_error = "; ".join(lint_errors)
        feedback, permanent = _classify_validation_retry(
            retry_state,
            validator_id=f"receive.generate_candidate.{candidate_kind}",
            errors=lint_errors,
            attempt=attempt + 1,
            ctx=attempt_ctx,
        )
        if permanent is not None:
            return _candidate_generation_error(permanent["error"]) | permanent
        parsed = None

    return _candidate_generation_error(
        "Codex candidate generation remained invalid after "
        f"{MAX_TECHNICAL_APPROACH_NODE_REGENERATIONS + 1} attempts: {last_error}"
    )


def _evaluate_candidates(
    candidates: Mapping[str, Any],
    normalized_problem: Mapping[str, Any],
    constraints: Mapping[str, Any],
    context: Mapping[str, Any] | None = None,
    accumulated_approach: Mapping[str, Any] | None = None,
    *,
    log_ctx: org_log.RunContext | None = None,
) -> dict[str, Any]:
    """Form step 5 of the documented 10-step Technical Approach procedure."""
    # Step 5 of 10: evaluate candidate approaches on the compact decision matrix without selecting one.
    if not all(isinstance(value, Mapping) for value in (candidates, normalized_problem, constraints)):
        return _candidate_evaluation_error("evaluate_candidates requires outputs from steps 1, 2, and 4")

    candidate_ids = _candidate_ids(candidates)
    if not candidate_ids:
        return _candidate_evaluation_error("evaluate_candidates requires named candidates from step 4")

    repo = _repo_from_context(context)
    evaluations: list[dict[str, Any]] = []
    for candidate in candidates["candidates"]:
        feedback: list[str] = []
        last_error = ""
        parsed: dict[str, Any] | None = None
        candidate_id = candidate["id"]
        retry_state = codex_exec.RejectionRetryState()
        for attempt in range(MAX_TECHNICAL_APPROACH_NODE_REGENERATIONS + 1):
            attempt_ctx = log_ctx.child(attempt=attempt + 1) if log_ctx is not None else None
            run = codex_exec.run_json(
                repo,
                schema=_evaluate_candidate_schema(str(candidate_id)),
                prompt=_evaluate_candidate_prompt(
                    candidate,
                    candidates,
                    normalized_problem,
                    constraints,
                    context,
                    accumulated_approach,
                    feedback if attempt else None,
                ),
                schema_filename=f"patch_series-evaluate-candidate-{candidate_id}.schema.json",
                output_filename=f"patch_series-candidate-evaluation-{candidate_id}.json",
                failure_label="Codex candidate evaluation",
                ctx=attempt_ctx,
            )
            if not run["ok"]:
                return _candidate_evaluation_error(run["error"])
            parsed = _parse_candidate_evaluation(run["raw"], candidate_id)
            if not parsed.get("ok", True):
                last_error = parsed["error"]
                feedback, permanent = _classify_validation_retry(
                    retry_state,
                    validator_id=f"receive.evaluate_candidate.{candidate_id}",
                    errors=last_error,
                    attempt=attempt + 1,
                    ctx=attempt_ctx,
                )
                if permanent is not None:
                    return _candidate_evaluation_error(permanent["error"]) | permanent
                continue
            lint_errors = _lint_empty_slots(parsed)
            if not lint_errors:
                break
            last_error = "; ".join(lint_errors)
            feedback, permanent = _classify_validation_retry(
                retry_state,
                validator_id=f"receive.evaluate_candidate.{candidate_id}",
                errors=lint_errors,
                attempt=attempt + 1,
                ctx=attempt_ctx,
            )
            if permanent is not None:
                return _candidate_evaluation_error(permanent["error"]) | permanent
            parsed = None
        if parsed is None or not parsed.get("ok", True):
            return _candidate_evaluation_error(
                "Codex candidate evaluation remained invalid after "
                f"{MAX_TECHNICAL_APPROACH_NODE_REGENERATIONS + 1} attempts: {last_error}"
            )
        evaluations.append(parsed)

    if {evaluation["candidate_id"] for evaluation in evaluations} != set(candidate_ids):
        return _candidate_evaluation_error("Codex candidate evaluation returned incomplete candidate coverage")
    return {"evaluations": evaluations}


def _select_approach(
    candidates: Mapping[str, Any],
    evaluations: Mapping[str, Any],
    constraints: Mapping[str, Any],
    context: Mapping[str, Any] | None = None,
    accumulated_approach: Mapping[str, Any] | None = None,
    *,
    log_ctx: org_log.RunContext | None = None,
) -> dict[str, Any]:
    """Form step 6 of the documented 10-step Technical Approach procedure."""
    # Step 6 of 10: select the best evaluated candidate with rationale and explicit trade-offs.
    if not all(isinstance(value, Mapping) for value in (candidates, evaluations, constraints)):
        return _approach_selection_error("select_approach requires outputs from steps 2, 4, and 5")

    candidate_ids = _candidate_ids(candidates)
    if not candidate_ids:
        return _approach_selection_error("select_approach requires named candidates from step 4")

    evaluation_ids = _evaluation_ids(evaluations)
    if set(evaluation_ids) != set(candidate_ids):
        # Memento: STRUCTURE/JUDGMENT split (see ai_org/deterministic_structure_gadgets).
        # This exact contract failure once discarded a 38-minute revision. Gadgets deploy
        # REACTIVELY by error type, never as always-on scaffolding: the first attempt is
        # free-form and unchanged; only after this typed failure do we run ONE bounded
        # structural repair round (code-computed fragment request for the missing slots,
        # model fills only those slots, code-side merge, re-validate). If repair does not
        # restore coverage, fall back to today's typed needs_work below.
        repaired = _repair_evaluation_matrix_coverage(
            candidates, evaluations, constraints, context, log_ctx=log_ctx
        )
        if repaired is None:
            return _approach_selection_error("select_approach requires one evaluation per candidate from step 5")
        evaluations = repaired

    repo = _repo_from_context(context)
    feedback: list[str] = []
    last_error = ""
    retry_state = codex_exec.RejectionRetryState()
    for attempt in range(MAX_TECHNICAL_APPROACH_NODE_REGENERATIONS + 1):
        attempt_ctx = log_ctx.child(attempt=attempt + 1) if log_ctx is not None else None
        run = codex_exec.run_json(
            repo,
            schema=_select_approach_schema(candidate_ids),
            prompt=_select_approach_prompt(
                candidates,
                evaluations,
                constraints,
                context,
                accumulated_approach,
                feedback if attempt else None,
            ),
            schema_filename="patch_series-select-approach.schema.json",
            output_filename="patch_series-selected-approach.json",
            failure_label="Codex approach selection",
            ctx=attempt_ctx,
        )
        if not run["ok"]:
            return _approach_selection_error(run["error"])
        parsed = _parse_approach_selection(run["raw"], candidate_ids)
        if not parsed.get("ok", True):
            last_error = parsed["error"]
            feedback, permanent = _classify_validation_retry(
                retry_state,
                validator_id="receive.select_approach",
                errors=last_error,
                attempt=attempt + 1,
                ctx=attempt_ctx,
            )
            if permanent is not None:
                return _approach_selection_error(permanent["error"]) | permanent
            continue
        lint_errors = _lint_empty_slots(parsed)
        if not lint_errors:
            _merge_candidate_generation_rejections(parsed, candidates)
            return parsed
        last_error = "; ".join(lint_errors)
        feedback, permanent = _classify_validation_retry(
            retry_state,
            validator_id="receive.select_approach",
            errors=lint_errors,
            attempt=attempt + 1,
            ctx=attempt_ctx,
        )
        if permanent is not None:
            return _approach_selection_error(permanent["error"]) | permanent

    return _approach_selection_error(
        "Codex approach selection remained invalid after "
        f"{MAX_TECHNICAL_APPROACH_NODE_REGENERATIONS + 1} attempts: {last_error}"
    )


def _repair_evaluation_matrix_coverage(
    candidates: Mapping[str, Any],
    evaluations: Mapping[str, Any],
    constraints: Mapping[str, Any],
    context: Mapping[str, Any] | None,
    *,
    log_ctx: org_log.RunContext | None = None,
) -> dict[str, Any] | None:
    """ONE bounded structural repair round for incomplete step-5 evaluation coverage.

    Memento: code owns STRUCTURE, the model owns JUDGMENT (deterministic_structure_gadgets).
    The gadget computes WHICH evaluation slots are missing/malformed and hands the model a
    code-generated skeleton for ONLY those slots; the model contributes only the judgment
    that fills them. Merge is code-side and cannot clobber already-valid slots. Returns the
    merged evaluations mapping when repair restores one-evaluation-per-candidate coverage,
    or None so the caller falls back to the pre-gadget typed failure. Exactly one round:
    a second failure must surface as needs_work, not loop.
    """
    candidate_ids = _candidate_ids(candidates)
    skeleton_result = structure_gadgets.evaluation_matrix_skeleton(
        candidate_ids,
        score_fields=CANDIDATE_EVALUATION_SCORE_FIELDS,
        score_leaf_fields=EVALUATION_SCORE_FIELDS,
    )
    if not skeleton_result.get("ok"):
        return None
    delta_result = structure_gadgets.repair_delta(
        {
            "validator_id": "receive.select_approach",
            "errors": ["select_approach requires one evaluation per candidate from step 5"],
        },
        evaluations,
        skeleton_result["skeleton"],
    )
    if not delta_result.get("ok"):
        return None
    delta = delta_result["delta"]
    fragment_request = delta.get("fragment_request", {})
    requested_slots = fragment_request.get("evaluations")
    if not isinstance(requested_slots, list) or not requested_slots:
        # Nothing repairable by supplying slots (e.g. only stray extra evaluations): fall back.
        return None
    requested_ids = [slot["candidate_id"] for slot in requested_slots]
    requested_candidates = [
        candidate
        for candidate in candidates.get("candidates", [])
        if isinstance(candidate, Mapping) and candidate.get("id") in set(requested_ids)
    ]
    if {candidate.get("id") for candidate in requested_candidates} != set(requested_ids):
        return None

    # Memento: repair rounds run at LOW reasoning effort by ratified decision.
    # The model already thought at its limit to produce the matrix; full-effort
    # re-derivation of judgment already spent is waste. The skeleton carries the
    # structure, so the fragment needs only local judgment.
    run = codex_exec.run_json(
        _repo_from_context(context),
        schema=_evaluation_repair_schema(requested_ids),
        prompt=_evaluation_repair_prompt(requested_candidates, fragment_request, constraints),
        schema_filename="patch_series-evaluation-repair.schema.json",
        output_filename="patch_series-evaluation-repair.json",
        failure_label="Codex evaluation matrix repair",
        ctx=log_ctx.child(step="evaluation_matrix_repair") if log_ctx is not None else None,
        reasoning_effort="low",
    )
    if not run["ok"]:
        return None
    try:
        fragment = json.loads(run["raw"])
    except json.JSONDecodeError:
        return None
    if not isinstance(fragment, dict) or not isinstance(fragment.get("evaluations"), list):
        return None
    validated: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for item in fragment["evaluations"]:
        if not isinstance(item, Mapping):
            return None
        item_id = item.get("candidate_id")
        if item_id not in set(requested_ids) or item_id in seen_ids:
            return None
        # Reuse the step-5 parser so a repaired slot obeys the exact same contract
        # (field set, score objects) plus the empty-slot lint as a first-pass slot.
        parsed = _parse_candidate_evaluation(json.dumps(item, ensure_ascii=True, default=str), item_id)
        if not parsed.get("ok", True) or _lint_empty_slots(parsed):
            return None
        seen_ids.add(item_id)
        validated.append(parsed)

    merge_result = structure_gadgets.merge_fragment(evaluations, {"evaluations": validated}, delta)
    if not merge_result.get("ok"):
        return None
    merged = merge_result["merged"]
    if set(_evaluation_ids(merged)) != set(candidate_ids):
        return None
    return merged


def _implementation_strategy(
    chosen: Mapping[str, Any],
    prior_art_map: Mapping[str, Any],
    constraints: Mapping[str, Any],
    patch_series_view: dict[str, Any],
    repo: str | Path,
    context: Mapping[str, Any] | None = None,
    accumulated_approach: Mapping[str, Any] | None = None,
    *,
    log_ctx: org_log.RunContext | None = None,
) -> dict[str, Any]:
    """Form step 7 of the documented 10-step Technical Approach procedure."""
    # Step 7 of 10: expand the selected approach into an implementation strategy without planning slices.
    if not all(isinstance(value, Mapping) for value in (chosen, prior_art_map, constraints)):
        return _implementation_strategy_error("implementation_strategy requires outputs from steps 2, 3, and 6")
    if not _is_patch_series_view(patch_series_view):
        return _implementation_strategy_error("implementation_strategy requires a grounded registry patch series view")

    repo_path = Path(repo).resolve()
    feedback: list[str] = []
    last_error = ""
    retry_state = codex_exec.RejectionRetryState()
    for attempt in range(MAX_TECHNICAL_APPROACH_NODE_REGENERATIONS + 1):
        attempt_ctx = log_ctx.child(attempt=attempt + 1) if log_ctx is not None else None
        run = codex_exec.run_json(
            repo_path,
            schema=build_implementation_strategy_schema(),
            prompt=_implementation_strategy_prompt(
                chosen,
                prior_art_map,
                constraints,
                patch_series_view,
                repo_path,
                context,
                accumulated_approach,
                feedback if attempt else None,
            ),
            schema_filename="patch_series-implementation-strategy.schema.json",
            output_filename="patch_series-implementation-strategy.json",
            failure_label="Codex implementation strategy",
            ctx=attempt_ctx,
        )
        if not run["ok"]:
            return _implementation_strategy_error(run["error"])
        parsed = _parse_implementation_strategy(run["raw"])
        if not parsed.get("ok", True):
            last_error = parsed["error"]
            feedback, permanent = _classify_validation_retry(
                retry_state,
                validator_id="receive.implementation_strategy",
                errors=last_error,
                attempt=attempt + 1,
                ctx=attempt_ctx,
            )
            if permanent is not None:
                return _implementation_strategy_error(permanent["error"]) | permanent
            continue
        lint_errors = _lint_empty_slots(parsed)
        lint_errors.extend(_lint_observable_effects(parsed))
        if not lint_errors:
            return parsed
        last_error = "; ".join(lint_errors)
        feedback, permanent = _classify_validation_retry(
            retry_state,
            validator_id="receive.implementation_strategy",
            errors=lint_errors,
            attempt=attempt + 1,
            ctx=attempt_ctx,
        )
        if permanent is not None:
            return _implementation_strategy_error(permanent["error"]) | permanent

    return _implementation_strategy_error(
        "Codex implementation strategy remained invalid after "
        f"{MAX_TECHNICAL_APPROACH_NODE_REGENERATIONS + 1} attempts: {last_error}"
    )


def _right_size_patch_plan(
    chosen: Mapping[str, Any],
    implementation_strategy: Mapping[str, Any],
    constraints: Mapping[str, Any],
    context: Mapping[str, Any] | None = None,
    accumulated_approach: Mapping[str, Any] | None = None,
    *,
    log_ctx: org_log.RunContext | None = None,
) -> dict[str, Any]:
    """Form step 8 of the documented 10-step Technical Approach procedure."""
    # Step 8 of 10: right-size the strategy into incremental slices without surfacing later-step risks.
    if not all(isinstance(value, Mapping) for value in (chosen, implementation_strategy, constraints)):
        return _right_size_patch_plan_error("right_size_patch_plan requires outputs from steps 2, 6, and 7")

    repo = _repo_from_context(context)
    feedback: list[str] = []
    last_error = ""
    retry_state = codex_exec.RejectionRetryState()
    for attempt in range(MAX_TECHNICAL_APPROACH_NODE_REGENERATIONS + 1):
        attempt_ctx = log_ctx.child(attempt=attempt + 1) if log_ctx is not None else None
        run = codex_exec.run_json(
            repo,
            schema=build_right_size_patch_plan_schema(),
            prompt=_right_size_patch_plan_prompt(
                chosen,
                implementation_strategy,
                constraints,
                context,
                accumulated_approach,
                feedback if attempt else None,
            ),
            schema_filename="patch_series-right-size-patch-plan.schema.json",
            output_filename="patch_series-right-sized-patch-plan.json",
            failure_label="Codex patch plan right-sizing",
            ctx=attempt_ctx,
        )
        if not run["ok"]:
            return _right_size_patch_plan_error(run["error"])
        parsed = _parse_right_size_patch_plan(run["raw"])
        if not parsed.get("ok", True):
            last_error = parsed["error"]
            feedback, permanent = _classify_validation_retry(
                retry_state,
                validator_id="receive.right_size_patch_plan",
                errors=last_error,
                attempt=attempt + 1,
                ctx=attempt_ctx,
            )
            if permanent is not None:
                return _right_size_patch_plan_error(permanent["error"]) | permanent
            continue
        lint_errors = _lint_empty_slots(parsed)
        if not lint_errors:
            return parsed
        last_error = "; ".join(lint_errors)
        feedback, permanent = _classify_validation_retry(
            retry_state,
            validator_id="receive.right_size_patch_plan",
            errors=lint_errors,
            attempt=attempt + 1,
            ctx=attempt_ctx,
        )
        if permanent is not None:
            return _right_size_patch_plan_error(permanent["error"]) | permanent

    return _right_size_patch_plan_error(
        "Codex patch plan right-sizing remained invalid after "
        f"{MAX_TECHNICAL_APPROACH_NODE_REGENERATIONS + 1} attempts: {last_error}"
    )


def _surface_risks(
    chosen: Mapping[str, Any],
    implementation_strategy: Mapping[str, Any],
    patch_plan: Mapping[str, Any],
    constraints: Mapping[str, Any],
    context: Mapping[str, Any] | None = None,
    accumulated_approach: Mapping[str, Any] | None = None,
    *,
    log_ctx: org_log.RunContext | None = None,
    revision: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Form step 9 of the documented 10-step Technical Approach procedure."""
    # Step 9 of 10: surface risk nodes and attach each one to its parent node.
    if not all(isinstance(value, Mapping) for value in (chosen, implementation_strategy, patch_plan, constraints)):
        return _surface_risks_error("surface_risks requires outputs from steps 2, 6, 7, and 8")

    repo = _repo_from_context(context)
    valid_target_ids = _surface_risk_target_ids(chosen, accumulated_approach)
    feedback: list[str] = []
    last_error = ""
    retry_state = codex_exec.RejectionRetryState()
    for attempt in range(MAX_TECHNICAL_APPROACH_NODE_REGENERATIONS + 1):
        attempt_ctx = log_ctx.child(attempt=attempt + 1) if log_ctx is not None else None
        run = codex_exec.run_json(
            repo,
            schema=_revision_surface_risks_schema(valid_target_ids) if revision else _surface_risks_schema(valid_target_ids),
            prompt=_surface_risks_prompt(
                chosen,
                implementation_strategy,
                patch_plan,
                constraints,
                context,
                accumulated_approach,
                valid_target_ids,
                feedback if attempt else None,
                revision=revision,
            ),
            schema_filename="patch_series-surface-risks.schema.json",
            output_filename="patch_series-surfaced-risks.json",
            failure_label="Codex risk surfacing",
            ctx=attempt_ctx,
        )
        if not run["ok"]:
            return _surface_risks_error(run["error"])
        parsed = _parse_surface_risks(run["raw"])
        if not parsed.get("ok", True):
            last_error = parsed["error"]
            feedback, permanent = _classify_validation_retry(
                retry_state,
                validator_id="receive.surface_risks",
                errors=last_error,
                attempt=attempt + 1,
                ctx=attempt_ctx,
            )
            if permanent is not None:
                return _surface_risks_error(permanent["error"]) | permanent
            continue
        lint_errors = _lint_empty_slots(parsed)
        if not lint_errors:
            return parsed
        last_error = "; ".join(lint_errors)
        feedback, permanent = _classify_validation_retry(
            retry_state,
            validator_id="receive.surface_risks",
            errors=lint_errors,
            attempt=attempt + 1,
            ctx=attempt_ctx,
        )
        if permanent is not None:
            return _surface_risks_error(permanent["error"]) | permanent

    return _surface_risks_error(
        "Codex risk surfacing remained invalid after "
        f"{MAX_TECHNICAL_APPROACH_NODE_REGENERATIONS + 1} attempts: {last_error}"
    )


def form_technical_approach(
    patch_series_view: dict[str, Any],
    repo: str | Path,
    context: Mapping[str, Any] | None = None,
    provided_approach: Any | None = None,
    progress_path: str | Path | None = None,
    reference_terms: Any | None = None,
    precedent_front_future: (
        concurrent.futures.Future[tuple[list[str], Any]] | None
    ) = None,
    skip_precedent_build: bool = False,
    ctx: org_log.RunContext | None = None,
) -> dict[str, Any]:
    """Form step 10 of the documented 10-step Technical Approach procedure."""
    # Step 10 of 10: the orchestrator + requester/AI-Org boundary.
    if not _is_patch_series_view(patch_series_view):
        return {
            "ok": False,
            "error": "form_technical_approach requires a grounded registry patch series view",
            "failed_step": "input",
        }

    repo_path = Path(repo).resolve()
    approach_context = _technical_approach_context(context, repo_path)
    # Brief 34: deliverable shape rides the context as DATA so every candidate
    # validator and prompt sees the series' own applicability.
    approach_context["deliverable_user_facing"] = _deliverable_is_user_facing(patch_series_view)
    effective_provided_approach = provided_approach
    if effective_provided_approach is None:
        effective_provided_approach = _provided_approach_from_tech_stack(patch_series_view)
    steps: dict[str, Any] = {}
    step_order = _technical_approach_step_order(effective_provided_approach)
    steps_completed: list[dict[str, Any]] = []
    external_files: dict[str, Any] = {}

    def start_step(step: str) -> dict[str, Any]:
        step_ctx = ctx.child(step=step) if ctx is not None else None
        event = None
        if step_ctx is not None:
            event = org_log.emit("approach.step.started", {"step": step}, ctx=step_ctx)
        return {
            "step": step,
            "started_at": time.monotonic() if (progress_path is not None or step_ctx is not None) else None,
            "ctx": step_ctx,
            "event_id": event["event_id"] if event else None,
        }

    def mark_step_completed(step_state: Mapping[str, Any], tree: Mapping[str, Any]) -> None:
        step = str(step_state["step"])
        started_at = step_state.get("started_at")
        if started_at is None:
            return
        seconds = time.monotonic() - float(started_at)
        current_step = _next_technical_approach_step(step_order, step)
        steps_completed.append({"step": step, "seconds": seconds})
        step_ctx = step_state.get("ctx")
        if isinstance(step_ctx, org_log.RunContext):
            org_log.emit(
                "approach.step.completed",
                {
                    "step": step,
                    "duration_seconds": seconds,
                    "technical_approach": _approach_snapshot(tree),
                    "current_step": current_step,
                },
                ctx=step_ctx,
                causation_event_id=step_state.get("event_id"),
            )
        if progress_path is not None:
            _write_technical_approach_progress(
                progress_path,
                tree,
                steps_completed,
                current_step,
            )

    def mark_step_failed(step_state: Mapping[str, Any], failure: Mapping[str, Any]) -> dict[str, Any]:
        step_ctx = step_state.get("ctx")
        started_at = step_state.get("started_at")
        if isinstance(step_ctx, org_log.RunContext) and started_at is not None:
            org_log.emit(
                "approach.step.failed",
                {
                    "step": step_state["step"],
                    "duration_seconds": time.monotonic() - float(started_at),
                    "error": failure.get("error", f"{step_state['step']} failed"),
                    "failure_mode": failure.get("failure_mode"),
                    "fingerprint": failure.get("rejection_fingerprint"),
                    "validator_id": failure.get("validator_id"),
                    "error_class": failure.get("error_class"),
                    "path": failure.get("path"),
                },
                ctx=step_ctx,
                severity="error",
                causation_event_id=step_state.get("event_id"),
            )
        return dict(failure)

    step_state = start_step("normalize_problem")
    step_ctx = step_state.get("ctx") if isinstance(step_state.get("ctx"), org_log.RunContext) else None
    normalized = _call_with_optional_log_ctx(_normalize_problem, patch_series_view, approach_context, log_ctx=step_ctx)
    failure = _technical_approach_step_failure("normalize_problem", normalized)
    if failure:
        return mark_step_failed(step_state, failure)
    steps["normalize_problem"] = normalized
    partial_tree: dict[str, Any] = {"problem": _problem_root_from_normalized(normalized)}
    mark_step_completed(step_state, partial_tree)

    step_state = start_step("extract_constraints")
    step_ctx = step_state.get("ctx") if isinstance(step_state.get("ctx"), org_log.RunContext) else None
    constraints = _call_with_optional_log_ctx(
        _extract_constraints,
        patch_series_view,
        repo_path,
        approach_context,
        dict(partial_tree),
        log_ctx=step_ctx,
    )
    failure = _technical_approach_step_failure("extract_constraints", constraints)
    if failure:
        return mark_step_failed(step_state, failure)
    steps["extract_constraints"] = constraints
    partial_tree["problem"]["constraints"] = _constraint_tree_nodes(constraints)
    mark_step_completed(step_state, partial_tree)

    prior_art_precedent_terms = reference_terms
    if prior_art_precedent_terms is None and precedent_front_future is not None:
        # Canonical join: consume only the ordered design terms here.  The
        # worker never mutates partial_tree/steps, so completion order cannot
        # affect the emitted approach body.
        prior_art_precedent_terms, _background_build_future = (
            precedent_front_future.result()
        )
    elif prior_art_precedent_terms is None and not skip_precedent_build:
        design_build = _call_with_optional_ctx_kw(
            engineering_precedent_store.build_from_patch_series,
            patch_series_view,
            approach_context,
            kinds=("design",),
            ctx=ctx.child(stage="engineering_precedent_store.design") if ctx is not None else None,
        )
        prior_art_precedent_terms = _precedent_terms_from_build_result(design_build)
        steps["build_precedent_from_patch_series"] = _json_safe(design_build)
        _call_with_optional_ctx_kw(
            engineering_precedent_store.start_background_build,
            patch_series_view,
            approach_context,
            kinds=("implementation",),
            ctx=ctx.child(stage="engineering_precedent_store.implementation") if ctx is not None else None,
        )

    step_state = start_step("build_prior_art_map")
    step_ctx = step_state.get("ctx") if isinstance(step_state.get("ctx"), org_log.RunContext) else None
    prior_art = _call_with_optional_log_ctx(
        _build_prior_art_map,
        patch_series_view,
        repo_path,
        approach_context,
        dict(partial_tree),
        prior_art_precedent_terms,
        log_ctx=step_ctx,
    )
    failure = _technical_approach_step_failure("build_prior_art_map", prior_art)
    if failure:
        return mark_step_failed(step_state, failure)
    steps["build_prior_art_map"] = prior_art
    partial_tree["problem"]["prior_art"] = _prior_art_tree_nodes(prior_art)
    mark_step_completed(step_state, partial_tree)

    if effective_provided_approach is None:
        step_state = start_step("generate_candidates")
        step_ctx = step_state.get("ctx") if isinstance(step_state.get("ctx"), org_log.RunContext) else None
        candidates = _call_with_optional_log_ctx(
            _generate_candidates,
            normalized,
            constraints,
            prior_art,
            approach_context,
            _approach_snapshot(partial_tree),
            log_ctx=step_ctx,
        )
        failure = _technical_approach_step_failure("generate_candidates", candidates)
        if failure:
            return mark_step_failed(step_state, failure)
        steps["generate_candidates"] = candidates
        partial_tree["problem"]["question"] = _partial_question_tree(candidates=candidates)
        mark_step_completed(step_state, partial_tree)

        step_state = start_step("evaluate_candidates")
        step_ctx = step_state.get("ctx") if isinstance(step_state.get("ctx"), org_log.RunContext) else None
        evaluations = _call_with_optional_log_ctx(
            _evaluate_candidates,
            candidates,
            normalized,
            constraints,
            approach_context,
            _approach_snapshot(partial_tree),
            log_ctx=step_ctx,
        )
        failure = _technical_approach_step_failure("evaluate_candidates", evaluations)
        if failure:
            return mark_step_failed(step_state, failure)
        steps["evaluate_candidates"] = evaluations
        partial_tree["problem"]["question"] = _partial_question_tree(candidates=candidates, evaluations=evaluations)
        mark_step_completed(step_state, partial_tree)

        step_state = start_step("select_approach")
        step_ctx = step_state.get("ctx") if isinstance(step_state.get("ctx"), org_log.RunContext) else None
        selected = _call_with_optional_log_ctx(
            _select_approach,
            candidates,
            evaluations,
            constraints,
            approach_context,
            _approach_snapshot(partial_tree),
            log_ctx=step_ctx,
        )
        failure = _technical_approach_step_failure("select_approach", selected)
        if failure:
            return mark_step_failed(step_state, failure)
        steps["select_approach"] = selected
        partial_tree["problem"]["question"] = _partial_question_tree(
            candidates=candidates,
            evaluations=evaluations,
            selected=selected,
        )
        mark_step_completed(step_state, partial_tree)
        source = "generated"
        _fill_ai_deliberated_tech_stack(patch_series_view, selected, candidates)
    else:
        step_state = start_step("select_approach")
        steps["provided_approach"] = _json_safe(effective_provided_approach)
        candidates = None
        evaluations = None
        selected = _provided_approach_selection(effective_provided_approach)
        steps["select_approach"] = selected
        partial_tree["problem"]["question"] = _partial_question_tree(
            selected=selected,
            provided_approach=effective_provided_approach,
        )
        mark_step_completed(step_state, partial_tree)
        source = "requester_provided_refined"

    step_state = start_step("implementation_strategy")
    step_ctx = step_state.get("ctx") if isinstance(step_state.get("ctx"), org_log.RunContext) else None
    implementation = _call_with_optional_log_ctx(
        _implementation_strategy,
        selected,
        prior_art,
        constraints,
        patch_series_view,
        repo_path,
        approach_context,
        _approach_snapshot(partial_tree),
        log_ctx=step_ctx,
    )
    failure = _technical_approach_step_failure("implementation_strategy", implementation)
    if failure:
        return mark_step_failed(step_state, failure)
    steps["implementation_strategy"] = implementation
    partial_tree["problem"]["question"] = _partial_question_tree(
        candidates=candidates,
        evaluations=evaluations,
        selected=selected,
        implementation=implementation,
        provided_approach=effective_provided_approach,
    )
    mark_step_completed(step_state, partial_tree)

    domain_facets, domain_precedent_lookups = _domain_precedent_facets_from_patch_series(
        patch_series_view,
        implementation,
        approach_context,
    )
    step_state = start_step("domain_specification")
    step_ctx = step_state.get("ctx") if isinstance(step_state.get("ctx"), org_log.RunContext) else None
    domain_specification = _call_with_optional_log_ctx(
        _domain_specification,
        domain_facets,
        domain_precedent_lookups,
        implementation,
        constraints,
        patch_series_view,
        approach_context,
        _approach_snapshot(partial_tree),
        log_ctx=step_ctx,
    )
    failure = _technical_approach_step_failure("domain_specification", domain_specification)
    if failure:
        return mark_step_failed(step_state, failure)
    domain_specification = _externalize_domain_specification(domain_specification, external_files)
    steps["domain_specification"] = domain_specification
    partial_tree["problem"]["question"] = _partial_question_tree(
        candidates=candidates,
        evaluations=evaluations,
        selected=selected,
        implementation=implementation,
        domain_specification=domain_specification,
        provided_approach=effective_provided_approach,
    )
    mark_step_completed(step_state, partial_tree)

    step_state = start_step("right_size_patch_plan")
    step_ctx = step_state.get("ctx") if isinstance(step_state.get("ctx"), org_log.RunContext) else None
    patch_plan = _call_with_optional_log_ctx(
        _right_size_patch_plan,
        selected,
        implementation,
        constraints,
        approach_context,
        _approach_snapshot(partial_tree),
        log_ctx=step_ctx,
    )
    failure = _technical_approach_step_failure("right_size_patch_plan", patch_plan)
    if failure:
        return mark_step_failed(step_state, failure)
    steps["right_size_patch_plan"] = patch_plan
    partial_tree["problem"]["question"] = _partial_question_tree(
        candidates=candidates,
        evaluations=evaluations,
        selected=selected,
        implementation=implementation,
        domain_specification=domain_specification,
        patch_plan=patch_plan,
        provided_approach=effective_provided_approach,
    )
    mark_step_completed(step_state, partial_tree)

    step_state = start_step("surface_risks")
    step_ctx = step_state.get("ctx") if isinstance(step_state.get("ctx"), org_log.RunContext) else None
    risks = _call_with_optional_log_ctx(
        _surface_risks,
        selected,
        implementation,
        patch_plan,
        constraints,
        approach_context,
        _approach_snapshot(partial_tree),
        log_ctx=step_ctx,
    )
    failure = _technical_approach_step_failure("surface_risks", risks)
    if failure:
        return mark_step_failed(step_state, failure)
    steps["surface_risks"] = risks

    risk_target_failure = _validate_risk_targets(risks, candidates, selected)
    if risk_target_failure:
        return mark_step_failed(step_state, {
            "ok": False,
            "error": risk_target_failure,
            "failed_step": "surface_risks",
        })

    partial_tree["problem"]["question"] = _question_tree(
        selected,
        candidates,
        evaluations,
        implementation,
        domain_specification,
        patch_plan,
        risks,
        [],
        provided_approach=effective_provided_approach,
        prior_art_link_targets=_prior_art_link_targets(_prior_art_tree_nodes(prior_art)),
    )
    mark_step_completed(step_state, partial_tree)

    return {
        "ok": True,
        "technical_approach": _assemble_technical_approach(
            normalized,
            constraints,
            selected,
            candidates,
            evaluations,
            prior_art,
            implementation,
            domain_specification,
            patch_plan,
            risks,
            source,
            provided_approach=effective_provided_approach,
            patch_series_view=patch_series_view,
        ),
        "steps": steps,
        "external_files": external_files,
    }


def _ground_with_contract(
    repo: str | Path,
    patch_series_view: dict[str, Any],
    *,
    ctx: org_log.RunContext | None = None,
) -> GroundingResult:
    best_grounding: GroundingResult | None = None
    previous_violations: list[str] = []
    retry_state = codex_exec.RejectionRetryState()
    for attempt in range(MAX_REGROUNDS + 1):
        attempt_ctx = ctx.child(attempt=attempt + 1) if ctx is not None else None
        org_log.emit(
            "grounding.attempt.started",
            {"attempt": attempt + 1, "previous_violations": previous_violations},
            ctx=attempt_ctx,
        ) if attempt_ctx is not None else None
        grounding = _ground_request(repo, patch_series_view, previous_violations if attempt else None, ctx=attempt_ctx)
        best_grounding = grounding
        verification = _verify_grounding(patch_series_view, grounding.patch_series_view, grounding, ctx=attempt_ctx)
        violations = list(verification["violations"])
        failure_mode = str(verification.get("failure_mode") or "")
        if attempt_ctx is not None:
            org_log.emit(
                "grounding.attempt.completed",
                {
                    "attempt": attempt + 1,
                    "confident": grounding.confident,
                    "violations": violations,
                    "grounding_notes": grounding.grounding_notes,
                    "failure_mode": failure_mode,
                },
                ctx=attempt_ctx,
                severity="warning" if violations else "info",
            )
        if failure_mode == TRANSIENT_RETRIES_EXHAUSTED:
            grounding.confident = False
            grounding.violations = violations
            grounding.failure_mode = TRANSIENT_RETRIES_EXHAUSTED
            if not grounding.parse_failure:
                grounding.grounding_notes = _with_unresolved_violations(
                    grounding.grounding_notes,
                    violations,
                )
            return grounding
        if not violations:
            return grounding
        feedback, permanent = _classify_validation_retry(
            retry_state,
            validator_id="receive.grounding_contract",
            errors=violations,
            attempt=attempt + 1,
            ctx=attempt_ctx,
        )
        if permanent is not None:
            return GroundingResult(
                grounding.patch_series_view,
                _with_unresolved_violations(grounding.grounding_notes, violations),
                False,
                grounding.assumptions + [permanent["error"]],
                grounding.questions + ["Can you confirm or correct the proposed patch series interpretation?"],
                violations,
                str(permanent.get("failure_mode") or ""),
            )
        previous_violations = feedback

    assert best_grounding is not None
    return GroundingResult(
        best_grounding.patch_series_view,
        _with_unresolved_violations(best_grounding.grounding_notes, previous_violations),
        False,
        best_grounding.assumptions
        + ["Grounding contract violations remain unresolved after verification."],
        best_grounding.questions + ["Can you confirm or correct the proposed patch series interpretation?"],
        previous_violations,
    )


def _call_ground_with_contract(
    repo: str | Path,
    patch_series_view: dict[str, Any],
    ctx: org_log.RunContext,
) -> GroundingResult:
    try:
        parameters = inspect.signature(_ground_with_contract).parameters
    except (TypeError, ValueError):
        parameters = {}
    if "ctx" in parameters:
        return _ground_with_contract(repo, patch_series_view, ctx=ctx)
    return _ground_with_contract(repo, patch_series_view)


def _call_with_optional_ctx_kw(func, *args: Any, ctx: org_log.RunContext | None, **kwargs: Any) -> Any:
    try:
        parameters = inspect.signature(func).parameters
    except (TypeError, ValueError):
        parameters = {}
    if ctx is not None and "ctx" in parameters:
        kwargs["ctx"] = ctx
    return func(*args, **kwargs)


def _call_with_optional_log_ctx(func, *args: Any, log_ctx: org_log.RunContext | None, **kwargs: Any) -> Any:
    try:
        parameters = inspect.signature(func).parameters
    except (TypeError, ValueError):
        parameters = {}
    if log_ctx is not None and "log_ctx" in parameters:
        kwargs["log_ctx"] = log_ctx
    return func(*args, **kwargs)


def _ground_request(
    repo: str | Path,
    patch_series_view: dict[str, Any],
    previous_violations: list[str] | None = None,
    *,
    ctx: org_log.RunContext | None = None,
) -> GroundingResult:
    """Research and correct a rough request before it becomes a patch series branch."""
    repo_path = Path(repo).resolve()
    prompt = _grounding_prompt(
        patch_series_view,
        previous_violations,
        _repository_history_context(repo_path),
        _repository_constitution_context(repo_path),
    )
    temp_dir = Path(tempfile.mkdtemp(prefix="ai-org-patch_series-grounding-"))
    schema_file = temp_dir / "patch_series-grounding.schema.json"
    out_file = temp_dir / "grounded-patch-series-cover-letter.json"
    try:
        schema_file.write_text(json.dumps(build_grounding_schema(), indent=2), encoding="utf-8")
        cmd = [
            "codex",
            "exec",
            "--sandbox",
            "read-only",
            "-C",
            str(repo_path),
            "-o",
            str(out_file),
            "--enable",
            "web_search",
            "--output-schema",
            str(schema_file),
            "--json",
            prompt,
        ]
        log_ctx = ctx or org_log.RunContext(repo=repo_path, stage="grounding")
        try:
            def invoke(command: list[str]) -> subprocess.CompletedProcess[str]:
                return org_log.logged_subprocess(
                    command,
                    ctx=log_ctx,
                    capture_policy="head_tail",
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                )

            completed = run_with_reset_retries(
                lambda: invoke(cmd),
                ctx=log_ctx,
                event_name="patchwork_queue.codex_reset_wait",
                resume_once=lambda session_id: invoke(codex_resume_command(cmd, session_id)),
            )
        except OSError as exc:
            return _grounding_fail_closed(patch_series_view, f"Grounding failed: {exc}")

        if completed.returncode != 0:
            detail = completed.stderr.strip() or (
                "no output file" if not out_file.exists() else "Codex grounding did not complete successfully."
            )
            if transient_retries_exhausted(completed):
                return _grounding_fail_closed(
                    patch_series_view,
                    f"Grounding failed: {detail} [{TRANSIENT_RETRIES_EXHAUSTED}]",
                    failure_mode=TRANSIENT_RETRIES_EXHAUSTED,
                )
            return _grounding_fail_closed(patch_series_view, f"Grounding failed: {detail}")
        if not out_file.exists():
            return _grounding_fail_closed(patch_series_view, "Grounding failed: no output file")
        return _parse_grounding_result(out_file.read_text(encoding="utf-8"), patch_series_view)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _grounding_prompt(
    patch_series_view: dict[str, Any],
    previous_violations: list[str] | None = None,
    repository_history: Mapping[str, Any] | None = None,
    repository_constitution: Mapping[str, Any] | None = None,
) -> str:
    # Grounding faithfully renders the request: the specific named thing at full scope.
    # It never generalizes to the category, never shrinks scope, and is not the legal
    # department: no IP, trademark, or copyright analysis.
    reground_instruction = ""
    if previous_violations:
        reground_instruction = (
            "Your previous grounding violated the executable grounding contract. Fix these violations and "
            "re-render the patch series without repeating them:\n"
            + "\n".join(f"- {violation}" for violation in previous_violations)
            + "\n\n"
        )

    registry_text = _field_registry_prompt()
    history_text = json.dumps(repository_history or {}, indent=2, sort_keys=True, ensure_ascii=True, default=str)
    constitution_text = json.dumps(
        repository_constitution or {},
        indent=2,
        sort_keys=True,
        ensure_ascii=True,
        default=str,
    )
    return (
        "You are the patch series intake grounding step for AI Org.\n"
        "Your job is to turn a rough, vague, or even wrong request into the right well-grounded patch series view "
        "before a patch series branch is created.\n\n"
        + reground_instruction
        + "Use web search when the request names or implies a real product, game, genre, paper, standard, "
        "company, library, tool, or other external reference. Determine what the reference actually is, "
        "including its concrete defining signatures, core mechanics, interaction rhythms, structure, tone, "
        "progression cadence, characteristic look, conventions, and prior art. Correct misconceptions in the "
        "request. Also inspect the target repository read-only to identify existing code, patterns, "
        "constraints, affected areas, the repository history context below, and the current constitution "
        "projection below. Commit messages are design record evidence for background facts and prior art; "
        "they must not fabricate requester intent. The constitution projection is current project constraint "
        "and philosophy evidence from file headers; it also must not fabricate requester intent. Do not "
        "modify files.\n\n"
        "Faithfully render the request's specific identity. When the request names a specific thing, ground "
        "down to that named thing and preserve its full identity in proposed_patch_series, including the title; never "
        "generalize up to a broad category, genre, archetype, or safer-sounding substitute. proposed_patch_series must "
        "read as 'faithfully reproduce <the specific named thing>', and a reviewer should be able to tell it "
        "apart from a generic genre entry. Do not turn a named specific target into its broad category. Do not rename "
        "a named thing as 'an original X-style' work.\n\n"
        "Preserve the request's full scope. Do not reduce the request to a vertical slice, short demo, one "
        "area, 10-minute experience, prototype, MVP, first iteration, or other smaller deliverable. Commit "
        "the proposed_patch_series to the complete requested deliverable at the fidelity implied by the named thing. "
        "Downstream phases may decompose and iterate toward that full scope; intake must not hand down a "
        "smaller goal. Do not put start-small, minimal, slice, demo, or MVP hedging in proposed_patch_series or "
        "assumptions.\n\n"
        "Grounding is not legal review. Do not perform IP, trademark, copyright, or licensing risk analysis; "
        "do not add legal disclaimers; do not spend proposed_patch_series, assumptions, or grounding_notes on legal "
        "concerns. Do not avoid perceived IP risk by renaming, generalizing, or shrinking the named thing. "
        "Your job here is only to understand and faithfully render what to build.\n\n"
        "Do not deliberate or choose the technical stack in grounding. proposed_patch_series.tech_stack.provenance may "
        "only be requester_specified when the original raw_request or proposal_hint explicitly names a "
        "requester-chosen build strategy, engine, framework, or language (for example Godot, React, Unreal, "
        "Unity, or Python), or unspecified otherwise. requester_specified requires a concrete build_strategy. "
        "A surface-only pin such as CLI, terminal, browser, or mobile is not a stack choice: record that "
        "surface in affected_area_platform and constraints_assumptions, and leave tech_stack unspecified with "
        "every choice field empty for later org deliberation. "
        "Never set provenance to ai_deliberated in grounding. When provenance is unspecified, leave "
        "build_strategy, engine, framework, language, platform, and rationale empty. If research shows the named "
        "domain, franchise, or product currently uses a stack, preserve that as evidence in background_facts, not as a "
        "tech_stack decision unless the requester explicitly asked for that stack.\n\n"
        "Default to the latest or current version, conventions, and best practices of the named thing unless "
        "the request explicitly asks for a retro, classic, old, vintage, specific past version, or specific "
        "past year target. This applies across domains: games should target the current experience, modern "
        "graphics, scope, and conventions; security should target current standards and patches; SaaS should "
        "use current stacks and practices; libraries should use current APIs. Do not gratuitously target an "
        "outdated incarnation when the latest sensible target is available.\n\n"
        "Derive proposed_patch_series.user_experience_requirements yourself. The requester may give only one line; do "
        "not ask them to supply graphics/UI/UX requirements when research and conventions can derive them. For "
        "named, franchise, or genre requests use this five-step derivation: (1) reference identity: capture the "
        "named thing's researched look and interaction conventions, such as Dragon Quest-style status windows "
        "with LV/HP/MP/gold, command menus, visible low-HP signals, explicit doors/stairs/chests/talk/search "
        "verbs, and NPC-clue navigation when that reference applies; (2) genre contract: add the genre's "
        "readability and feedback conventions; (3) presentation obligations: every NPC, exit, gate, boss, flag, "
        "and objective must be visually represented and visibly change when state changes; (4) accessibility "
        "baseline: readable text, contrast, non-color-only communication, remappable or simple controls, and "
        "player-paced dialog; (5) verification: screenshot, interaction, and playtest criteria proving mechanics "
        "are perceivable, not merely implemented. Presentation fidelity is part of the named thing's identity; "
        "do not strip the look while keeping only mechanics. Empty or vague UX sections for a user_facing "
        "deliverable are contract violations and will fail closed. When the deliverable is not user-facing, "
        "set applicability to not_user_facing with a concrete reason and leave every UX section body empty; "
        "operator, CLI, or observability notes belong in desired_outcomes_success or background_facts, not in "
        "UX sections.\n\n"
        "Always do the research and commit to the most-likely interpretation as proposed_patch_series, even when the "
        "request is ambiguous or under-specified. Do not send back blank open questions instead of grounding. "
        "If you are confident, set confident=true and make proposed_patch_series the grounded registry-shaped patch series view "
        "that should be promoted. If you are not fully confident, set confident=false and still return the "
        "best-guess grounded registry-shaped proposed_patch_series for requester confirmation: this is what I think you "
        "mean, right? "
        "List the specific inferences you made in assumptions, each phrased so the requester can confirm or "
        "correct it later, such as 'I assumed X because <research>'. questions/open_questions records material "
        "NON-BLOCKING uncertainties: decisions that could go another way, assumptions that need later "
        "validation, or deferred design choices. It is never a gate that blocks progress. For substantial work, "
        "2-5 entries are expected; empty is acceptable only when genuinely nothing is uncertain. "
        "grounding_notes must briefly state what you researched, what you corrected, and cite web references "
        "when used. Also return ascii_working_slug as a short branch slug for the grounded working title: "
        "lowercase ASCII only, using a-z, 0-9, and hyphen, 3-48 characters. For non-ASCII titles, transliterate "
        "or translate the title into a meaningful ASCII slug; do not fall back to a generic slug.\n\n"
        "Fill proposed_patch_series from this registry. Each field's must_not is an anti-dumping gate: content matching "
        "must_not belongs elsewhere or nowhere. Research prose and audit trail go in grounding_provenance, not "
        "requirement fields; bounded domain facts go in background_facts; requester solution ideas go in "
        "proposal_hint; material non-blocking uncertainties go in open_questions. Every field marked "
        "required_at=patch_series_handoff must be filled with a non-empty value when it is a string. In particular, produce a concise "
        "working_title derived from the request, such as a short noun phrase naming the deliverable.\n\n"
        f"{registry_text}\n"
        + "\nDesign record over time (repository history; commit messages are the repo's design record):\n"
        + history_text
        + "\n\nConstitution, current (org constitution projected from module Memento headers):\n"
        + constitution_text
        + "\n"
        + _format_patch_series("Current request registry view", _patch_series_to_view(patch_series_view))
        + "\nReturn only JSON matching the provided schema."
    )


def _field_registry_prompt() -> str:
    lines = ["Field registry:"]
    for entry in FIELD_REGISTRY:
        lines.append(
            f"- {entry.name}: role={entry.role}; belongs={entry.belongs}; must_not={entry.must_not}; "
            f"owner={entry.owner}; required_at={entry.required_at}"
        )
    return "\n".join(lines)


def _normalize_problem_prompt(
    patch_series_view: dict[str, Any],
    context: Mapping[str, Any] | None = None,
    feedback: list[str] | None = None,
) -> str:
    context_text = json.dumps(context or {}, indent=2, sort_keys=True, default=str)
    feedback_text = ""
    if feedback:
        feedback_text = (
            "\nPrevious output failed deterministic validation. Regenerate the whole object and fix these "
            "measurability issues:\n"
            + "\n".join(f"- {item}" for item in feedback)
            + "\n"
        )
    return (
        "You are forming step 1 of AI Org's 10-step Technical Approach procedure: normalize the problem.\n"
        "Use the grounded registry patch series view and repository context only to restate the problem clearly. "
        "Do not propose an implementation approach, alternatives, patch plan, reviewer decision, or later "
        "Technical Approach steps.\n\n"
        "Write English only. Distill problem and current_inadequacy; do not copy any patch series field verbatim. "
        "Derive success criteria from the patch series intent, but do not copy patch series feature-list bullets as criteria. "
        "For user_facing deliverables, include at least one player/user criterion with ux_trace set to a "
        "real dotted path inside user_experience_requirements, not none. Use none only for criteria that "
        "are not UX-facing. Example: a criterion that proves HP appears in the battle HUD uses "
        "ux_trace=\"user_experience_requirements.core_status_surfaces.player_status\".\n\n"
        "Return these fields:\n"
        "- problem: the core problem, restated crisply.\n"
        "- affected: affected users, operators, contributors, systems, modules, or workflows.\n"
        "- current_inadequacy: what is missing or where the current state falls short.\n"
        "- success_criteria: measurable nested objects. Each criterion must include actor, capability "
        "{action, preconditions}, verifiable_outcome {expected_state, evidence}, verification "
        "{method, check}. action must be observable; preconditions must be concrete; expected_state must "
        "name the end state that proves success; evidence must state what is observed or measured; method "
        "must be automated_test, manual_check, or metric; check must be the concrete verification performed; "
        "ux_trace must be a user_experience_requirements dotted path or exactly none. Each object is the "
        "Canonical Referee Criterion for one goal and must self-evaluate the Producer Eligibility Predicate "
        f"{PRODUCER_DETERMINATION_FIELD}: true exactly when satisfaction requires a produced artifact, code, data, or "
        "other deliverable; false when satisfaction is purely explanatory or observational. Use only "
        f"{PRODUCER_DETERMINATION_FIELD} and do not defer this determination to a later step.\n"
        "- non_goals: explicit boundaries that should remain out of scope for this patch series.\n\n"
        "- open_questions: material NON-BLOCKING uncertainties about the normalized problem, such as decisions "
        "that could go another way, assumptions that need later validation, or deferred design choices. Never "
        "block progress on these; 2-5 entries are expected for substantial work, and empty is acceptable only "
        "when genuinely nothing is uncertain.\n\n"
        + _format_patch_series("Grounded registry patch series view", _patch_series_to_view(patch_series_view))
        + f"\nContext:\n{context_text}\n"
        + feedback_text
        + "\nReturn only JSON matching the provided schema."
    )


def _root_success_criteria_text(accumulated_approach: Mapping[str, Any] | None) -> str:
    criteria: Any = []
    if isinstance(accumulated_approach, Mapping):
        problem = accumulated_approach.get("problem")
        if isinstance(problem, Mapping) and isinstance(problem.get("goals"), list):
            criteria = problem["goals"]
        else:
            normalized = accumulated_approach.get("normalized_problem")
            if isinstance(normalized, Mapping) and isinstance(normalized.get("success_criteria"), list):
                criteria = normalized["success_criteria"]
    return json.dumps(criteria, indent=2, sort_keys=True, ensure_ascii=True, default=str)


def _extract_constraints_prompt(
    patch_series_view: dict[str, Any],
    repo: Path,
    context: Mapping[str, Any] | None = None,
    approach: Mapping[str, Any] | None = None,
    feedback: list[str] | None = None,
) -> str:
    context_text = json.dumps(context or {}, indent=2, sort_keys=True, default=str)
    approach_text = json.dumps(
        approach or {},
        indent=2,
        sort_keys=True,
        ensure_ascii=True,
        default=str,
    )
    goals_text = _root_success_criteria_text(approach)
    feedback_text = ""
    if feedback:
        feedback_text = (
            "\nPrevious output failed deterministic validation. Regenerate the whole constraints branch and fix "
            "these issues:\n"
            + "\n".join(f"- {item}" for item in feedback)
            + "\n"
        )
    return (
        "You are forming step 2 of AI Org's 10-step Technical Approach procedure: extract constraints.\n"
        "Inspect the repository read-only as needed from the configured repo root. Use the grounded patch_series field registry "
        "patch series view, accumulated approach document, repository architecture, and supplied context to identify "
        "constraints only. Do not propose candidate approaches, select an approach, create a patch plan, or "
        "perform later Technical Approach steps.\n\n"
        "This step must attach one nested-tag branch to the accumulated approach document. The accumulated "
        "document currently contains step 1 as approach.normalized_problem. Derive constraints from that branch, "
        "including nested success_criteria, and from repository facts. Treat a success criterion's required "
        "observable result as a hard constraint when later approaches could violate it. For example, a criterion "
        "that save reload restores identical state implies a persistence and versioning constraint; a criterion "
        "that battle resolution follows commands and stats implies a deterministic battle-state constraint. Use "
        "the raw patch series only as grounding and ambiguity context, not as an independent restart of problem discovery.\n\n"
        "Write English only. Extract two lists:\n"
        "- hard_constraints: must-satisfy constraints that later approaches cannot violate.\n"
        "- soft_preferences: nice-to-have preferences that should influence trade-offs but may be outweighed.\n\n"
        "Each hard constraint item must include statement, derivation {from, trace}, and implication {must, "
        "must_not}. Each soft preference item must include statement, derivation {from, trace}, and rationale. "
        "Set derivation.from to exactly one of: problem, success_criteria, non_goals, repo, domain. If a "
        "constraint or preference comes from a success criterion, derivation.from must be success_criteria and "
        "derivation.trace must name the specific criterion or nested criterion slot, such as "
        "success_criteria[0].verifiable_outcome.expected_state. Do not leave statement, derivation.trace, "
        "implication.must, implication.must_not, or rationale empty; when a prohibition is implicit, state the "
        "forbidden class of change in must_not.\n\n"
        "Cover these areas when evidence exists:\n"
        "- repository architecture and module boundaries.\n"
        "- backward compatibility and public/interface compatibility.\n"
        "- data, API, schema, protocol, and configuration contracts.\n"
        "- performance, security, reliability, operability, and migration requirements.\n"
        "- test constraints, existing coverage style, and verification expectations.\n"
        "- delivery scope, non-goals, rollout boundaries, and documentation expectations.\n\n"
        "Return an empty list when no defensible items exist for a category; do not invent unsupported "
        "constraints.\n\n"
        + _format_patch_series("Grounded registry patch series view", _patch_series_to_view(patch_series_view))
        + f"\nRoot success_criteria from step 1:\n{goals_text}\n"
        + f"\nAccumulated approach so far:\n{approach_text}\n"
        + f"\nRepository root:\n{repo}\n"
        + f"\nContext:\n{context_text}\n"
        + feedback_text
        + "\nReturn only JSON matching the provided schema."
    )


def _prior_art_map_prompt(
    patch_series_view: dict[str, Any],
    repo: Path,
    concepts: list[str],
    reference_facets: list[dict[str, Any]],
    context: Mapping[str, Any] | None = None,
    approach: Mapping[str, Any] | None = None,
    feedback: list[str] | None = None,
    *,
    revision: Mapping[str, Any] | None = None,
) -> str:
    context_text = json.dumps(context or {}, indent=2, sort_keys=True, ensure_ascii=True, default=str)
    approach_text = json.dumps(approach or {}, indent=2, sort_keys=True, ensure_ascii=True, default=str)
    goals_text = _root_success_criteria_text(approach)
    concepts_text = json.dumps(concepts, indent=2, ensure_ascii=True)
    revision_text = _revision_contract_text(revision, _PRIOR_ART_REVISION_ID_RULE)
    reference_text = json.dumps(reference_facets, indent=2, sort_keys=True, ensure_ascii=True, default=str)
    feedback_text = ""
    if feedback:
        feedback_text = (
            "\nPrevious output failed deterministic validation. Regenerate the whole prior_art branch and fix "
            "these issues:\n"
            + "\n".join(f"- {item}" for item in feedback)
            + "\n"
        )
    return (
        "You are forming step 3 of AI Org's 10-step Technical Approach procedure: build a prior-art map.\n"
        "Inspect the repository read-only as needed from the configured repo root. Use the grounded patch_series field registry "
        "patch series view, accumulated approach document, precedent design facets, precedent implementation facets, "
        "and repository context to synthesize 3 to 6 prior-art patterns. Do not generate candidate approaches, "
        "select an approach, create a patch plan, or perform later Technical Approach steps.\n\n"
        "This step must attach one nested-tag branch to the accumulated approach document. The accumulated "
        "document currently contains approach.normalized_problem and approach.constraints. Trace every pattern "
        "to the normalized problem or constraints it addresses. Use the raw patch series only as grounding and ambiguity "
        "context, not as an independent restart of problem discovery.\n\n"
        "Each pattern must identify a real design or implementation pattern visible in the precedent facets, "
        "the repository, or both. Treat frameworks, engines, and libraries as candidates judged on fit; do not "
        "favor them merely because they appeared often. A candidate such as Godot belongs here only when the "
        "evidence makes it relevant, and its disposition must be adopt, adapt, or reject on merit.\n\n"
        "Be honest about precedent coverage per concept. If precedent facets were returned for a concept, use "
        "their structure, rationale, when_to_use, tradeoffs, and implementation_hooks in at least one pattern "
        "when relevant, and set source.reference_concept to that concept with source.facet_kind design or "
        "implementation. Do not say precedent facets are absent for a concept that appears in the retrieved "
        "facets. Set source.facet_kind to none only for patterns derived solely from the patch series or repository, or "
        "for concepts that genuinely returned no precedent-store entry.\n\n"
        "For each pattern:\n"
        "- name: concise name of the prior-art pattern or candidate.\n"
        "- source: nested object with reference_concept, facet_kind, and where. facet_kind must be design, "
        "implementation, or none.\n"
        "- when_applies: conditions that make the pattern appropriate.\n"
        "- tradeoffs: nested object with pros and cons arrays; include concrete benefits, costs, and failure "
        "modes.\n"
        "- disposition: nested object with choice and why. choice must be adopt, adapt, or reject for this patch series.\n"
        "- traces_to: approach elements this pattern addresses, such as normalized_problem.success_criteria[0] "
        "or constraints.hard_constraints[0].\n\n"
        + revision_text
        + _format_patch_series("Grounded registry patch series view", _patch_series_to_view(patch_series_view))
        + f"\nRepository root:\n{repo}\n"
        + f"\nRoot success_criteria from step 1:\n{goals_text}\n"
        + f"\nAccumulated approach so far:\n{approach_text}\n"
        + f"\nContext:\n{context_text}\n"
        + f"\nReference key concepts queried:\n{concepts_text}\n"
        + f"\nprecedent facets read before this call:\n{reference_text}\n"
        + feedback_text
        + "\nReturn only JSON matching the provided schema."
    )

def _generate_candidate_prompt(
    normalized_problem: Mapping[str, Any],
    constraints: Mapping[str, Any],
    prior_art_map: Mapping[str, Any],
    candidate_kind: str,
    existing_candidates: list[Mapping[str, Any]],
    context: Mapping[str, Any] | None = None,
    accumulated_approach: Mapping[str, Any] | None = None,
    feedback: list[str] | None = None,
    *,
    revision: Mapping[str, Any] | None = None,
) -> str:
    revision_text = _revision_contract_text(revision)
    # Brief 34: the platform guidance is scoped by the deliverable's UX
    # applicability — a not_user_facing deliverable has no user-facing runtime
    # target, and telling the model it must name one steers honest spec
    # candidates into the validator wall (live: body-format spec run,
    # 2026-07-05). Not-user-facing wording verified by blank-context probe
    # (scratchpad/brief34_probes/out_prompt.txt): a blank model answers
    # 'git-committed specification documents', no org verifier vocabulary.
    if _deliverable_user_facing_from_context(context):
        platform_sentence = (
            "Platform is the user-facing runtime target such as browser or lightweight desktop; keep org "
            "verification mechanisms in constraints, not platform."
        )
    else:
        platform_sentence = (
            "Platform is the deliverable's own target surface, such as git-committed specification "
            "documents or repository docs; keep org verification mechanisms in constraints, not platform."
        )
    normalized_problem_text = json.dumps(normalized_problem, indent=2, sort_keys=True, ensure_ascii=True, default=str)
    constraints_text = json.dumps(constraints, indent=2, sort_keys=True, ensure_ascii=True, default=str)
    prior_art_text = json.dumps(prior_art_map, indent=2, sort_keys=True, ensure_ascii=True, default=str)
    existing_text = json.dumps(existing_candidates, indent=2, sort_keys=True, ensure_ascii=True, default=str)
    context_text = json.dumps(context or {}, indent=2, sort_keys=True, ensure_ascii=True, default=str)
    approach_text = json.dumps(accumulated_approach or {}, indent=2, sort_keys=True, ensure_ascii=True, default=str)
    goals_text = _root_success_criteria_text(accumulated_approach)
    feedback_text = ""
    if feedback:
        feedback_text = (
            "\nPrevious output or candidate set needs adjustment. Generate a candidate the org can author as "
            "text in the worktree and verify headlessly with functional_check. Address these issues:\n"
            + "\n".join(f"- {item}" for item in feedback)
            + "\n"
        )
    kind_instruction = (
        "Return exactly one additional feasible candidate. Make it distinct from existing feasible candidates."
        if candidate_kind == "additional_feasible"
        else f"Return exactly one {candidate_kind} candidate. Make it distinct from existing candidates."
    )
    return (
        "You are forming step 4 of AI Org's Technical Approach derivation tree: generate one candidate node.\n"
        "Use the accumulated approach tree containing the root goals, constraints, and prior-art ancestors, "
        "plus read-only repository inspection from the configured repo root. Do not select a winner, evaluate candidates, write an implementation strategy, "
        "create a patch plan, or perform later Technical Approach steps. Do not modify files.\n\n"
        "If patch_series.tech_stack.provenance is unspecified, engine/framework/platform selection is a first-class "
        "candidate axis: compare available engines, frameworks, platform targets, and a from-scratch option "
        "only when justified against those available options. Do not use from_scratch as a silent default.\n\n"
        f"{kind_instruction}\n\n"
        "For each candidate:\n"
        "- id: stable lowercase identifier using letters, numbers, underscores, or hyphens.\n"
        "- name: concise approach name.\n"
        "- kind: exactly one of minimal_local, repo_native, general_architectural, do_nothing_defer.\n"
        "- summary: what this approach would do and why it is materially different.\n"
        "- stack_requirement: the stack this candidate actually requires, not rivals it only mentions. "
        "Set build_strategy to engine_based only for an existing engine/framework product named in engine; "
        "use framework_based only for an existing framework product named in framework; if the runtime is "
        f"hand-rolled on browser standards with no existing engine/framework, use from_scratch. {platform_sentence} "
        "Use authoring_model=text_first and "
        "verification_model=headless_ci only when Codex can author source text and verify by command.\n"
        "- first_proof_moment: name the user_actions, neutral named_content items as {name, kind}, and the "
        "win or progress condition that proves the first end-to-end usable slice.\n"
        "- core_systems: concrete domain, workflow, repository, or runtime systems this candidate would change.\n"
        "- draws_on: prior-art pattern names, repository references, or precedent-store entries this candidate builds on.\n\n"
        + revision_text
        + f"Normalized problem:\n{normalized_problem_text}\n"
        f"\nConstraints:\n{constraints_text}\n"
        f"\nPrior-art map:\n{prior_art_text}\n"
        f"\nExisting candidates:\n{existing_text}\n"
        f"\nRoot success_criteria from step 1:\n{goals_text}\n"
        f"\nAccumulated approach so far:\n{approach_text}\n"
        f"\nContext:\n{context_text}\n"
        + feedback_text
        + "\nReturn only JSON matching the provided schema."
    )


def _evaluate_candidate_prompt(
    candidate: Mapping[str, Any],
    candidates: Mapping[str, Any],
    normalized_problem: Mapping[str, Any],
    constraints: Mapping[str, Any],
    context: Mapping[str, Any] | None = None,
    accumulated_approach: Mapping[str, Any] | None = None,
    feedback: list[str] | None = None,
) -> str:
    candidate_text = json.dumps(candidate, indent=2, sort_keys=True, ensure_ascii=True, default=str)
    candidates_text = json.dumps(candidates, indent=2, sort_keys=True, ensure_ascii=True, default=str)
    normalized_problem_text = json.dumps(normalized_problem, indent=2, sort_keys=True, ensure_ascii=True, default=str)
    constraints_text = json.dumps(constraints, indent=2, sort_keys=True, ensure_ascii=True, default=str)
    context_text = json.dumps(context or {}, indent=2, sort_keys=True, ensure_ascii=True, default=str)
    approach_text = json.dumps(accumulated_approach or {}, indent=2, sort_keys=True, ensure_ascii=True, default=str)
    goals_text = _root_success_criteria_text(accumulated_approach)
    feedback_text = ""
    if feedback:
        feedback_text = (
            "\nPrevious output failed deterministic empty-slot validation. Regenerate this evaluation node and fix "
            "these issues:\n"
            + "\n".join(f"- {item}" for item in feedback)
            + "\n"
        )
    candidate_id_choices = _closed_precedent_fallback_prompt("candidate_id", [str(candidate.get("id") or "")])
    return (
        "You are forming step 5 of AI Org's Technical Approach derivation tree: evaluate one candidate node.\n"
        "Use the accumulated approach tree containing the root goals, constraints, prior-art ancestors, and candidate set, "
        "plus read-only repository inspection from the configured repo root. Do not select a "
        "winner, write an implementation strategy, create a patch plan, or perform later Technical Approach "
        "steps. Do not modify files.\n\n"
        "Evaluate the single candidate on this compact matrix. Each score is an object with rating and reason:\n"
        "- problem_fit: how directly the candidate satisfies the normalized problem and success criteria.\n"
        "- repo_fit: how well it fits existing module boundaries, conventions, and ownership.\n"
        "- complexity: implementation and maintenance complexity.\n"
        "- quality_attributes: performance, security, reliability, usability, or other relevant qualities.\n"
        "- compat_migration: compatibility, migration, schema, API, data, and configuration impact.\n"
        "- testability: how directly the candidate can be verified with the repository's test style.\n"
        "- operability: runtime, rollout, observability, support, or operational impact where relevant.\n"
        "- reversibility: how easy the choice is to undo, narrow, or replace later.\n"
        "- risk: main uncertainty, failure mode, or delivery risk.\n"
        "Use the candidate_id exactly as given.\n\n"
        f"Candidate to evaluate:\n{candidate_text}\n"
        f"\nAll candidate approaches:\n{candidates_text}\n"
        f"\nNormalized problem:\n{normalized_problem_text}\n"
        f"\nConstraints:\n{constraints_text}\n"
        f"\nRoot success_criteria from step 1:\n{goals_text}\n"
        f"\nAccumulated approach so far:\n{approach_text}\n"
        f"\nContext:\n{context_text}\n"
        + candidate_id_choices
        + feedback_text
        + "\nReturn only JSON matching the provided schema."
    )


def _select_approach_prompt(
    candidates: Mapping[str, Any],
    evaluations: Mapping[str, Any],
    constraints: Mapping[str, Any],
    context: Mapping[str, Any] | None = None,
    accumulated_approach: Mapping[str, Any] | None = None,
    feedback: list[str] | None = None,
) -> str:
    candidates_text = json.dumps(candidates, indent=2, sort_keys=True, ensure_ascii=True, default=str)
    evaluations_text = json.dumps(evaluations, indent=2, sort_keys=True, ensure_ascii=True, default=str)
    constraints_text = json.dumps(constraints, indent=2, sort_keys=True, ensure_ascii=True, default=str)
    context_text = json.dumps(context or {}, indent=2, sort_keys=True, ensure_ascii=True, default=str)
    approach_text = json.dumps(accumulated_approach or {}, indent=2, sort_keys=True, ensure_ascii=True, default=str)
    goals_text = _root_success_criteria_text(accumulated_approach)
    candidate_id_choices = _closed_precedent_fallback_prompt("candidate ids", _candidate_ids(candidates))
    feedback_text = ""
    if feedback:
        feedback_text = (
            "\nPrevious output failed deterministic empty-slot validation. Regenerate this decision node and fix "
            "these issues:\n"
            + "\n".join(f"- {item}" for item in feedback)
            + "\n"
        )
    return (
        "You are forming step 6 of AI Org's Technical Approach derivation tree: select the approach.\n"
        "Use the accumulated approach tree containing the root goals, constraints, prior-art, candidate, and evaluation ancestors, "
        "plus read-only repository inspection from the configured repo root. Choose "
        "one candidate. Do not write an implementation strategy, create a patch plan, surface risks, "
        "emit the final Technical Approach section, or perform later Technical Approach steps. Do not modify "
        "files.\n\n"
        "The decision must be a reasoned selection from the evaluation nodes, not a one-shot claim. Use Toulmin "
        "arguments: claim, grounds, warrant, backing, and rebuttal. Include support for the selected candidate "
        "and objections for rejected candidates.\n\n"
        "Return these fields:\n"
        "- selected_candidate_id: the exact id of the selected candidate.\n"
        "- arguments: support or objection Toulmin arguments about candidate ids.\n"
        "- rationale: because, under_constraints, and accepting_tradeoffs arrays. Keep the combined rationale "
        "under roughly 150 words; the harness will preserve what you return rather than silently trimming it.\n"
        "- stack_axes: one non-empty nested record for stack reasoning. fidelity_precedent is what "
        "background_facts or prior art says external exemplars use. It must include "
        "evidence and judgment. Fidelity precedent is evidence only and must not override the "
        "requester-specified stack. If no stack was specified by the requester, this is where the "
        "deliberation records why "
        "the selected feasible stack wins. The selected candidate's tech_stack.platform names the user-facing "
        "runtime target; org verification constraints belong in the constraints tree, not in the deliverable "
        "platform. If from_scratch is selected, explicitly justify it against available engine and framework "
        "options.\n"
        "- rejected: one item for each non-chosen candidate with candidate_id and objection.\n\n"
        "- open_questions: material NON-BLOCKING uncertainties about the selected decision, such as decisions "
        "that could go another way, assumptions that need later validation, or deferred design choices. Never "
        "block progress on these; 2-5 entries are expected for substantial work, and empty is acceptable only "
        "when genuinely nothing is uncertain.\n\n"
        f"Candidate approaches:\n{candidates_text}\n"
        f"\nEvaluation matrix:\n{evaluations_text}\n"
        f"\nConstraints:\n{constraints_text}\n"
        f"\nRoot success_criteria from step 1:\n{goals_text}\n"
        f"\nAccumulated approach so far:\n{approach_text}\n"
        f"\nContext:\n{context_text}\n"
        + candidate_id_choices
        + feedback_text
        + "\nReturn only JSON matching the provided schema."
    )


def _evaluation_repair_prompt(
    requested_candidates: list[Mapping[str, Any]],
    fragment_request: Mapping[str, Any],
    constraints: Mapping[str, Any],
) -> str:
    # Memento: gadgets deploy REACTIVELY — this prompt exists only after the
    # "one evaluation per candidate" contract failure, never on the first attempt.
    # It must reference ONLY the candidates whose evaluation slots are missing or
    # malformed. Including the full candidate set or the accumulated tree here
    # would regenerate judgment that already passed validation upstream. The
    # skeleton is code-generated structure (ids + field names + null placeholders);
    # this prompt must add no content to it.
    skeleton_text = json.dumps(fragment_request, indent=2, sort_keys=True, ensure_ascii=True, default=str)
    candidates_text = json.dumps(
        {"candidates": [dict(candidate) for candidate in requested_candidates]},
        indent=2,
        sort_keys=True,
        ensure_ascii=True,
        default=str,
    )
    constraints_text = json.dumps(constraints, indent=2, sort_keys=True, ensure_ascii=True, default=str)
    return (
        "You are repairing step 5 of AI Org's Technical Approach derivation tree: the evaluation matrix is "
        "missing the evaluation slots below. Evaluate ONLY the listed candidates. Do not select an approach, "
        "re-evaluate any other candidate, or perform later Technical Approach steps. Do not modify files.\n\n"
        "Fill every slot of this code-generated skeleton: replace each null with your judgment and keep the "
        "ids and field names exactly as given.\n"
        f"{skeleton_text}\n"
        f"\nCandidate approaches to evaluate:\n{candidates_text}\n"
        f"\nConstraints:\n{constraints_text}\n"
        "\nReturn only JSON matching the provided schema."
    )


def _implementation_strategy_prompt(
    chosen: Mapping[str, Any],
    prior_art_map: Mapping[str, Any],
    constraints: Mapping[str, Any],
    patch_series_view: dict[str, Any],
    repo: Path,
    context: Mapping[str, Any] | None = None,
    accumulated_approach: Mapping[str, Any] | None = None,
    feedback: list[str] | None = None,
) -> str:
    chosen_text = json.dumps(chosen, indent=2, sort_keys=True, ensure_ascii=True, default=str)
    prior_art_text = json.dumps(prior_art_map, indent=2, sort_keys=True, ensure_ascii=True, default=str)
    constraints_text = json.dumps(constraints, indent=2, sort_keys=True, ensure_ascii=True, default=str)
    context_text = json.dumps(context or {}, indent=2, sort_keys=True, ensure_ascii=True, default=str)
    approach_text = json.dumps(accumulated_approach or {}, indent=2, sort_keys=True, ensure_ascii=True, default=str)
    goals_text = _root_success_criteria_text(accumulated_approach)
    feedback_text = ""
    if feedback:
        feedback_text = (
            "\nPrevious output failed deterministic empty-slot validation. Regenerate this implementation node and fix "
            "these issues:\n"
            + "\n".join(f"- {item}" for item in feedback)
            + "\n"
        )
    return (
        "You are forming step 7 of AI Org's Technical Approach derivation tree: implementation strategy.\n"
        "Use the accumulated approach tree containing the root goals, constraints, prior-art, candidates, "
        "evaluations, and selected decision, the grounded registry patch series view, and read-only repository inspection from the configured repo root. "
        "You may inspect module structure and tests to make the strategy concrete. Do not modify files.\n\n"
        "Expand only the chosen approach into an implementation strategy. Do not generate or re-evaluate "
        "alternatives, create a patch slice plan, surface risks, emit the final Technical "
        "Approach section, or perform later Technical Approach steps.\n\n"
        "Anti-decorative rule: No presentation element may obscure, contradict, or masquerade as deliverable state. "
        "Every consequential mechanic must have an observable signifier, every consequential action timely "
        "feedback, and every persistent state change persistent evidence. For each system, observable_effect "
        "must name what the user can see/hear/read or inspect as evidence; it may not restate internal "
        "observable_behavior.\n\n"
        "Return these fields:\n"
        "- systems: each system has system_name, observable_behavior, observable_effect, named_content as "
        "neutral {name, kind} items, and key_modules. Tie named content and system behavior to specific root "
        "goals where natural.\n"
        "- persistence: saved_fields that must survive reload or handoff; name the field or state explicitly and align it with any success criterion that requires durable state.\n\n"
        + _format_patch_series("Grounded registry patch series view", _patch_series_to_view(patch_series_view))
        + f"\nRepository root:\n{repo}\n"
        + f"\nChosen approach:\n{chosen_text}\n"
        + f"\nPrior-art map:\n{prior_art_text}\n"
        + f"\nConstraints:\n{constraints_text}\n"
        + f"\nRoot success_criteria from step 1:\n{goals_text}\n"
        + f"\nAccumulated approach so far:\n{approach_text}\n"
        + f"\nContext:\n{context_text}\n"
        + feedback_text
        + "\nReturn only JSON matching the provided schema."
    )


def _right_size_patch_plan_prompt(
    chosen: Mapping[str, Any],
    implementation_strategy: Mapping[str, Any],
    constraints: Mapping[str, Any],
    context: Mapping[str, Any] | None = None,
    accumulated_approach: Mapping[str, Any] | None = None,
    feedback: list[str] | None = None,
) -> str:
    chosen_text = json.dumps(chosen, indent=2, sort_keys=True, ensure_ascii=True, default=str)
    strategy_text = json.dumps(implementation_strategy, indent=2, sort_keys=True, ensure_ascii=True, default=str)
    constraints_text = json.dumps(constraints, indent=2, sort_keys=True, ensure_ascii=True, default=str)
    context_text = json.dumps(context or {}, indent=2, sort_keys=True, ensure_ascii=True, default=str)
    approach_text = json.dumps(accumulated_approach or {}, indent=2, sort_keys=True, ensure_ascii=True, default=str)
    goals_text = _root_success_criteria_text(accumulated_approach)
    feedback_text = ""
    if feedback:
        feedback_text = (
            "\nPrevious output failed deterministic empty-slot validation. Regenerate this patch_plan node and fix "
            "these issues:\n"
            + "\n".join(f"- {item}" for item in feedback)
            + "\n"
        )
    return (
        "You are forming step 9 of AI Org's Technical Approach derivation tree: right-size the patch plan.\n"
        "Use the accumulated approach tree containing the root goals, constraints, selected decision, implementation strategy, and domain specification, "
        "plus read-only repository inspection from the configured repo root. Do not modify files.\n\n"
        "Turn the implementation strategy into a graph of stable-ID work items. Every item must leave "
        "the system working and should enable behavior incrementally. Apply YAGNI: defer speculative work unless a deferred decision is hard to "
        "reverse or affects major quality attributes such as security, reliability, compatibility, performance, "
        "operability, or maintainability. Do not surface risks, emit the final Technical "
        "Approach section, or perform later Technical Approach steps.\n\n"
        "Dependency truth belongs on each item. depends_on contains only stable ids of items whose runtime output "
        "the item actually builds on. Do not encode presentation order, preferred sequencing, file proximity, or "
        "contention as a dependency. Two items that can be implemented and verified independently MUST NOT reference "
        "one another. A real dependency chain must be stated explicitly rather than implied by array position.\n\n"
        "Return these fields:\n"
        "- items: stable id, objective, neutral named_content {name, kind} rows, acceptance_criteria, how_verified, "
        "and depends_on ids. Use ids that remain meaningful if independent items are added or reordered. Include "
        "per-item trace text in acceptance_criteria/how_verified so verification maps to specific success criteria.\n"
        "- deferred: intentionally deferred work, each with item and why_safe_to_defer.\n"
        "- open_questions: material NON-BLOCKING uncertainties about patch slicing or deferred choices, such as "
        "decisions that could go another way, assumptions that need later validation, or deferred design "
        "choices. Never block progress on these; 2-5 entries are expected for substantial work, and empty is "
        "acceptable only when genuinely nothing is uncertain.\n"
        f"Chosen approach:\n{chosen_text}\n"
        f"\nImplementation strategy:\n{strategy_text}\n"
        f"\nConstraints:\n{constraints_text}\n"
        f"\nRoot success_criteria from step 1:\n{goals_text}\n"
        f"\nAccumulated approach so far:\n{approach_text}\n"
        f"\nContext:\n{context_text}\n"
        + feedback_text
        + "\nReturn only JSON matching the provided schema."
    )


def _domain_specification_prompt(
    profile_facets: list[dict[str, Any]],
    reference_lookups: Mapping[str, Any],
    implementation_strategy: Mapping[str, Any],
    constraints: Mapping[str, Any],
    patch_series_view: dict[str, Any],
    context: Mapping[str, Any] | None = None,
    accumulated_approach: Mapping[str, Any] | None = None,
    feedback: list[str] | None = None,
) -> str:
    facets_text = json.dumps(profile_facets, indent=2, sort_keys=True, ensure_ascii=True, default=str)
    lookups_text = json.dumps(reference_lookups, indent=2, sort_keys=True, ensure_ascii=True, default=str)
    strategy_text = json.dumps(implementation_strategy, indent=2, sort_keys=True, ensure_ascii=True, default=str)
    constraints_text = json.dumps(constraints, indent=2, sort_keys=True, ensure_ascii=True, default=str)
    context_text = json.dumps(context or {}, indent=2, sort_keys=True, ensure_ascii=True, default=str)
    approach_text = json.dumps(accumulated_approach or {}, indent=2, sort_keys=True, ensure_ascii=True, default=str)
    goals_text = _root_success_criteria_text(accumulated_approach)
    feedback_text = ""
    if feedback:
        feedback_text = (
            "\nPrevious output failed deterministic validation. Regenerate the domain_specification node and fix "
            "these issues:\n"
            + "\n".join(f"- {item}" for item in feedback)
            + "\n"
        )
    return (
        "You are forming step 8 of AI Org's Technical Approach derivation tree: domain specification.\n"
        "Use the content-aware precedent facets as the exact aspect list to declare. For each aspect, use the "
        "precedent-store lookup keyed by the patch series-derived short query, the accumulated approach, and the grounded patch series to declare the "
        "domain quantities, tables, and source trail needed before patch planning. Do not create a patch plan, "
        "surface risks, or perform later Technical Approach steps. Do not modify files.\n\n"
        "Return one block per input aspect. Each block must include aspect_name, applicability, "
        "specification_body, quantities, tables, and sources. Use applicability=applies when the patch series needs a "
        "contractual declaration for the aspect; then specification_body must be non-empty, and the block must "
        "include at least one quantity or table and at least one source. Use applicability=not_applicable when "
        "the aspect is irrelevant; then specification_body is the reason. Tables are first-class receptacles, "
        "not prose: use tables for row-shaped values and quantities for named scalar values. Every table row "
        "must have exactly the same width as columns.\n\n"
        "Write English only. Use aspect_name exactly as supplied so review, network, and reform can anchor "
        "objections to the same aspect.\n\n"
        + _format_patch_series("Grounded registry patch series view", _patch_series_to_view(patch_series_view))
        + f"\nContent-aware precedent facets:\n{facets_text}\n"
        + f"\nprecedent-store lookups keyed by patch series-derived query:\n{lookups_text}\n"
        + f"\nImplementation strategy:\n{strategy_text}\n"
        + f"\nConstraints:\n{constraints_text}\n"
        + f"\nRoot success_criteria from step 1:\n{goals_text}\n"
        + f"\nAccumulated approach so far:\n{approach_text}\n"
        + f"\nContext:\n{context_text}\n"
        + feedback_text
        + "\nReturn only JSON matching the provided schema."
    )


def _surface_risks_prompt(
    chosen: Mapping[str, Any],
    implementation_strategy: Mapping[str, Any],
    patch_plan: Mapping[str, Any],
    constraints: Mapping[str, Any],
    context: Mapping[str, Any] | None = None,
    accumulated_approach: Mapping[str, Any] | None = None,
    valid_target_ids: list[str] | None = None,
    feedback: list[str] | None = None,
    *,
    revision: Mapping[str, Any] | None = None,
) -> str:
    revision_text = _revision_contract_text(revision)
    chosen_text = json.dumps(chosen, indent=2, sort_keys=True, ensure_ascii=True, default=str)
    strategy_text = json.dumps(implementation_strategy, indent=2, sort_keys=True, ensure_ascii=True, default=str)
    patch_plan_text = json.dumps(patch_plan, indent=2, sort_keys=True, ensure_ascii=True, default=str)
    constraints_text = json.dumps(constraints, indent=2, sort_keys=True, ensure_ascii=True, default=str)
    context_text = json.dumps(context or {}, indent=2, sort_keys=True, ensure_ascii=True, default=str)
    approach_text = json.dumps(accumulated_approach or {}, indent=2, sort_keys=True, ensure_ascii=True, default=str)
    goals_text = _root_success_criteria_text(accumulated_approach)
    target_id_choices = _closed_precedent_fallback_prompt("risk target_id", valid_target_ids or [])
    feedback_text = ""
    if feedback:
        feedback_text = (
            "\nPrevious output failed deterministic empty-slot validation. Regenerate this risks node and fix "
            "these issues:\n"
            + "\n".join(f"- {item}" for item in feedback)
            + "\n"
        )
    return (
        "You are forming step 10 of AI Org's Technical Approach derivation tree: surface risks.\n"
        "Use the accumulated approach tree containing the root goals, constraints, selected decision, implementation strategy, domain specification, and right-sized patch "
        "plan, plus read-only repository inspection from the configured "
        "repo root. Do not modify files.\n\n"
        "Surface only delivery or design risks that can be attached to a candidate, decision, implementation, or patch_plan "
        "node. Do not change the chosen approach, rewrite the implementation strategy, create new patch "
        "slices, emit the final Technical Approach section, or perform later Technical Approach steps.\n\n"
        "Return these fields:\n"
        "- risks: each risk node has id, risk, mitigation, attaches_to candidate|decision|implementation|patch_plan, and "
        "target_id naming the node it attaches under. State which success criterion or decision the risk threatens where natural.\n\n"
        + revision_text
        + f"Chosen approach:\n{chosen_text}\n"
        f"\nImplementation strategy:\n{strategy_text}\n"
        f"\nPatch plan:\n{patch_plan_text}\n"
        f"\nConstraints:\n{constraints_text}\n"
        f"\nRoot success_criteria from step 1:\n{goals_text}\n"
        f"\nAccumulated approach so far:\n{approach_text}\n"
        f"\nContext:\n{context_text}\n"
        + target_id_choices
        + feedback_text
        + "\nReturn only JSON matching the provided schema."
    )


def _prior_art_key_concepts(
    patch_series_view: dict[str, Any],
    context: Mapping[str, Any] | None = None,
    approach: Mapping[str, Any] | None = None,
) -> list[str]:
    concepts: list[str] = []
    context_values = dict(context or {})
    reference_context = _precedent_stack_context(context_values)
    for field in ("reference_terms", "key_concepts", "concepts", "terms"):
        _extend_concepts(concepts, context_values.get(field))

    extraction_text = (
        _format_patch_series("Grounded registry patch series view", _patch_series_to_view(patch_series_view))
        + "\nAccumulated approach:\n"
        + json.dumps(approach or {}, indent=2, sort_keys=True, ensure_ascii=True, default=str)
    )
    extracted = engineering_precedent_store._extract_precedent_terms(extraction_text, reference_context)
    _extend_concepts(concepts, extracted)

    if not concepts:
        _add_concept(concepts, patch_series_view.get("working_title", ""))
        _add_concept(concepts, patch_series_view.get("affected_area_platform", ""))
        _extend_concepts(concepts, _explicit_terms(_patch_series_text(patch_series_view)))
        if approach:
            _extend_concepts(concepts, _explicit_terms(json.dumps(approach, sort_keys=True, default=str)))

    if len(concepts) < 6:
        _extend_concepts(concepts, _important_phrases(_patch_series_text(patch_series_view)))
    return concepts[:12]


def _precedent_terms_from_build_result(reference_build: Mapping[str, Any]) -> list[str]:
    processed = reference_build.get("processed_terms")
    terms = _precedent_terms_for_prior_art(processed)
    if terms:
        return terms
    return _precedent_terms_for_prior_art(reference_build.get("terms"))


def _precedent_terms_for_prior_art(values: Any) -> list[str]:
    concepts: list[str] = []
    if isinstance(values, Mapping):
        values = list(values.keys())
    _extend_concepts(concepts, values)
    return concepts


def _extend_concepts(concepts: list[str], values: Any) -> None:
    if isinstance(values, str):
        _add_concept(concepts, values)
    elif isinstance(values, list | tuple | set):
        for value in values:
            _add_concept(concepts, value)


def _add_concept(concepts: list[str], value: Any) -> None:
    concept = re.sub(r"\s+", " ", str(value or "").strip())
    if not concept or len(concept) > 120:
        return
    lowered = concept.lower()
    if lowered not in {existing.lower() for existing in concepts}:
        concepts.append(concept)


def _explicit_terms(text: str) -> list[str]:
    terms = re.findall(r"`([^`]{2,80})`", text)
    terms.extend(re.findall(r'"([^"]{2,80})"', text))
    terms.extend(re.findall(r"'([^']{2,80})'", text))
    return terms


def _important_phrases(text: str) -> list[str]:
    words = [
        word.lower()
        for word in re.findall(r"[A-Za-z][A-Za-z0-9_.-]*", text)
        if len(word) > 2 and word.lower() not in _PRIOR_ART_STOPWORDS
    ]
    phrases: list[str] = []
    for size in (3, 2):
        for index in range(0, max(0, len(words) - size + 1)):
            phrase = " ".join(words[index : index + size])
            if not any(part in _PRIOR_ART_STOPWORDS for part in phrase.split()):
                phrases.append(phrase)
    phrases.extend(words)
    return phrases


def _read_prior_art_precedent_facets(
    concepts: list[str],
    context: Mapping[str, Any] | None = None,
    *,
    allow_expand: bool = True,
) -> list[dict[str, Any]]:
    reference_context = _precedent_stack_context(context)
    facets_by_index: dict[int, dict[str, Any]] = {}
    already_present: list[str] = []
    missing_indexes: list[int] = []
    researched: list[str] = []
    empty_after_research: list[str] = []
    skipped_after_cap: list[str] = []
    failed: dict[str, str] = {}

    initial = _lookup_prior_art_precedent_facets_for_concepts(concepts, reference_context)
    for index, term in enumerate(concepts):
        term_facets = initial.get(index)
        if term_facets is None:
            term_facets = _failed_prior_art_precedent_facets(term, "lookup worker did not return")
        if term_facets.get("status") == "failed":
            facets_by_index[index] = term_facets
            failed[term] = str(term_facets.get("error") or "unknown error")
            continue
        if term_facets["design"] or term_facets["implementation"]:
            term_facets["status"] = "retrieved"
            already_present.append(term)
            facets_by_index[index] = term_facets
            continue
        missing_indexes.append(index)

    if allow_expand:
        research_indexes = missing_indexes[:MAX_PRIOR_ART_REFERENCE_EXPANSIONS]
        capped_indexes = missing_indexes[MAX_PRIOR_ART_REFERENCE_EXPANSIONS:]
    else:
        research_indexes = []
        capped_indexes = []
        for index in missing_indexes:
            term = concepts[index]
            term_facets = initial.get(index) or {"term": term, "design": [], "implementation": []}
            term_facets["status"] = "not_found"
            empty_after_research.append(term)
            facets_by_index[index] = term_facets

    for index in capped_indexes:
        term = concepts[index]
        term_facets = initial.get(index) or {"term": term, "design": [], "implementation": []}
        term_facets["status"] = "not_researched_cap"
        skipped_after_cap.append(term)
        facets_by_index[index] = term_facets

    researched_facets = _research_prior_art_precedent_facets_for_concepts(
        [(index, concepts[index]) for index in research_indexes],
        reference_context,
    )
    for index in research_indexes:
        term = concepts[index]
        term_facets = researched_facets.get(index)
        if term_facets is None:
            term_facets = _failed_prior_art_precedent_facets(term, "research worker did not return")
        if term_facets.get("status") == "failed":
            failed[term] = str(term_facets.get("error") or "unknown error")
        elif term_facets["design"] or term_facets["implementation"]:
            term_facets["status"] = "researched"
            researched.append(term)
        else:
            term_facets["status"] = "not_found"
            empty_after_research.append(term)
        facets_by_index[index] = term_facets

    facets = [facets_by_index[index] for index in range(len(concepts))]
    LOGGER.info(
        "patch series prior-art precedent facets: already_present=%s researched=%s empty_after_research=%s "
        "skipped_after_cap=%s failed=%s expansion_cap=%s",
        already_present,
        researched,
        empty_after_research,
        skipped_after_cap,
        failed,
        MAX_PRIOR_ART_REFERENCE_EXPANSIONS,
    )
    return facets


def _lookup_prior_art_precedent_facets_for_concepts(
    concepts: list[str],
    reference_context: Mapping[str, Any],
) -> dict[int, dict[str, Any]]:
    parallelism = engineering_precedent_store._precedent_parallelism(len(concepts))
    if parallelism <= 1:
        return {
            index: _lookup_prior_art_precedent_facets_safely(term, reference_context)
            for index, term in enumerate(concepts)
        }

    outcomes: dict[int, dict[str, Any]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=parallelism) as executor:
        futures = {
            executor.submit(_lookup_prior_art_precedent_facets_safely, term, reference_context): index
            for index, term in enumerate(concepts)
        }
        for future in concurrent.futures.as_completed(futures):
            outcomes[futures[future]] = future.result()
    return outcomes


def _research_prior_art_precedent_facets_for_concepts(
    indexed_concepts: list[tuple[int, str]],
    reference_context: Mapping[str, Any],
) -> dict[int, dict[str, Any]]:
    parallelism = engineering_precedent_store._precedent_parallelism(len(indexed_concepts))
    if parallelism <= 1:
        return {
            index: _research_prior_art_precedent_facets_safely(term, reference_context)
            for index, term in indexed_concepts
        }

    outcomes: dict[int, dict[str, Any]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=parallelism) as executor:
        futures = {
            executor.submit(_research_prior_art_precedent_facets_safely, term, reference_context): index
            for index, term in indexed_concepts
        }
        for future in concurrent.futures.as_completed(futures):
            outcomes[futures[future]] = future.result()
    return outcomes


def _lookup_prior_art_precedent_facets_safely(
    term: str,
    reference_context: Mapping[str, Any],
) -> dict[str, Any]:
    try:
        return _lookup_prior_art_precedent_facets(term, reference_context)
    except Exception as exc:
        return _failed_prior_art_precedent_facets(term, _format_prior_art_precedent_error(exc))


def _research_prior_art_precedent_facets_safely(
    term: str,
    reference_context: Mapping[str, Any],
) -> dict[str, Any]:
    try:
        expanded = engineering_precedent_store.expand(term, reference_context)
        term_facets = _lookup_prior_art_precedent_facets(term, reference_context)
        if not term_facets["design"]:
            term_facets["design"] = _trim_expanded_precedent_candidates(expanded, "design")
        if not term_facets["implementation"]:
            term_facets["implementation"] = _trim_expanded_precedent_candidates(expanded, "implementation")
        return term_facets
    except Exception as exc:
        return _failed_prior_art_precedent_facets(term, _format_prior_art_precedent_error(exc))


def _failed_prior_art_precedent_facets(term: str, error: str) -> dict[str, Any]:
    return {"term": term, "design": [], "implementation": [], "status": "failed", "error": error}


def _format_prior_art_precedent_error(exc: BaseException) -> str:
    message = str(exc).strip()
    if message:
        return f"{type(exc).__name__}: {message}"
    return type(exc).__name__


def _precedent_stack_context(context: Mapping[str, Any] | None) -> dict[str, str]:
    context_values = dict(context or {})
    stack = context_values.get("stack")
    if isinstance(stack, Mapping):
        context_values = {**context_values, **stack}
    return {
        key: str(value)
        for key in ("language", "environment", "version")
        if (value := context_values.get(key)) is not None and str(value).strip()
    }


def _lookup_prior_art_precedent_facets(term: str, reference_context: Mapping[str, Any]) -> dict[str, Any]:
    term_facets: dict[str, Any] = {"term": term, "design": [], "implementation": []}
    for kind in ("design", "implementation"):
        lookup = engineering_precedent_store.lookup(term, reference_context, kind=kind)
        candidates = lookup.get("candidates", []) if isinstance(lookup, dict) else []
        term_facets[kind] = _trim_precedent_candidates(candidates)
    return term_facets


def _trim_expanded_precedent_candidates(expanded: Any, kind: str) -> list[dict[str, str]]:
    if not isinstance(expanded, Mapping):
        return []
    candidates = expanded.get("candidates")
    if not isinstance(candidates, list):
        return []
    matching = [
        candidate
        for candidate in candidates
        if isinstance(candidate, Mapping) and str(candidate.get("kind") or "").strip().lower() == kind
    ]
    return _trim_precedent_candidates(matching)


def _trim_precedent_candidates(candidates: Any) -> list[dict[str, str]]:
    if not isinstance(candidates, list):
        return []
    trimmed: list[dict[str, str]] = []
    for candidate in candidates[:4]:
        if isinstance(candidate, Mapping):
            trimmed.append(
                {
                    str(key): str(value)
                    for key, value in candidate.items()
                    if key
                    in {
                        "kind",
                        "term",
                        "summary",
                        "snippet",
                        "pitfalls",
                        "structure",
                        "rationale",
                        "when_to_use",
                        "when_not_to_use",
                        "tradeoffs",
                        "alternatives",
                        "implementation_hooks",
                        "quality_attributes",
                        "evidence",
                        "delta_claim",
                        "lang_env_version",
                        "author_level",
                        "source_url",
                        "found_via",
                    }
                }
            )
    return trimmed


def _attach_prior_art_precedent_facets(
    prior_art_map: dict[str, Any],
    reference_facets: list[dict[str, Any]],
) -> None:
    facets_by_term = {
        str(facet.get("term") or "").strip().lower(): facet
        for facet in reference_facets
        if isinstance(facet, Mapping) and str(facet.get("term") or "").strip()
    }
    for pattern in prior_art_map.get("patterns", []):
        if not isinstance(pattern, dict):
            continue
        source = pattern.get("source")
        if not isinstance(source, Mapping):
            continue
        concept = str(source.get("reference_concept") or "").strip().lower()
        facet = facets_by_term.get(concept)
        if facet is None:
            continue
        pattern["reference_facets"] = {
            "term": str(facet.get("term") or ""),
            "status": str(facet.get("status") or ""),
            "design": list(facet.get("design") or []),
            "implementation": list(facet.get("implementation") or []),
        }


def _patch_series_text(patch_series_view: dict[str, Any]) -> str:
    parts: list[str] = []
    for field in WORK_ORDER_VIEW_FIELDS:
        value = patch_series_view.get(field)
        if isinstance(value, list):
            parts.extend(str(item) for item in value)
        else:
            parts.append(str(value or ""))
    return "\n".join(parts)


def _parse_normalized_problem(raw: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        return _normalized_problem_error(f"Codex problem normalization returned invalid JSON: {exc}")

    if not isinstance(parsed, dict):
        return _normalized_problem_error("Codex problem normalization returned non-object JSON")
    if set(parsed) != set(NORMALIZED_PROBLEM_FIELDS):
        return _normalized_problem_error("Codex problem normalization returned invalid fields")
    if not all(isinstance(parsed[field], str) for field in ("problem", "affected", "current_inadequacy")):
        return _normalized_problem_error("Codex problem normalization returned invalid string fields")
    non_goals = parsed.get("non_goals")
    if not isinstance(non_goals, list) or not all(isinstance(item, str) for item in non_goals):
        return _normalized_problem_error("Codex problem normalization returned invalid non_goals")
    open_questions = parsed.get("open_questions")
    if not isinstance(open_questions, list) or not all(isinstance(item, str) for item in open_questions):
        return _normalized_problem_error("Codex problem normalization returned invalid open_questions")
    criteria = parsed.get("success_criteria")
    if not isinstance(criteria, list):
        return _normalized_problem_error("Codex problem normalization returned invalid success_criteria")
    parsed_criteria: list[dict[str, Any]] = []
    for criterion in criteria:
        parsed_criterion = _parse_success_criterion(criterion)
        if parsed_criterion is None:
            return _normalized_problem_error("Codex problem normalization returned invalid success_criteria")
        parsed_criteria.append(parsed_criterion)
    return {
        "problem": parsed["problem"],
        "affected": parsed["affected"],
        "current_inadequacy": parsed["current_inadequacy"],
        "success_criteria": parsed_criteria,
        "non_goals": list(non_goals),
        "open_questions": list(open_questions),
    }


def _normalized_problem_error(reason: str) -> dict[str, Any]:
    return {"ok": False, "error": reason}


def _parse_success_criterion(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict) or set(value) != set(SUCCESS_CRITERION_FIELDS):
        return None
    actor = value.get("actor")
    capability = value.get("capability")
    outcome = value.get("verifiable_outcome")
    verification = value.get("verification")
    ux_trace = value.get("ux_trace")
    requires_deliverable = value.get(PRODUCER_DETERMINATION_FIELD)
    if not isinstance(actor, str):
        return None
    if not isinstance(capability, dict) or set(capability) != set(SUCCESS_CRITERION_CAPABILITY_FIELDS):
        return None
    action = capability.get("action")
    preconditions = capability.get("preconditions")
    if not isinstance(action, str) or not isinstance(preconditions, list):
        return None
    if not all(isinstance(item, str) for item in preconditions):
        return None
    if not isinstance(outcome, dict) or set(outcome) != set(SUCCESS_CRITERION_OUTCOME_FIELDS):
        return None
    expected_state = outcome.get("expected_state")
    evidence = outcome.get("evidence")
    if not isinstance(expected_state, str) or not isinstance(evidence, str):
        return None
    if not isinstance(verification, dict) or set(verification) != set(SUCCESS_CRITERION_VERIFICATION_FIELDS):
        return None
    method = verification.get("method")
    check = verification.get("check")
    if method not in SUCCESS_CRITERION_VERIFICATION_METHODS or not isinstance(check, str):
        return None
    if not isinstance(ux_trace, str):
        return None
    if not isinstance(requires_deliverable, bool):
        return None
    return {
        "actor": actor,
        "capability": {"action": action, "preconditions": list(preconditions)},
        "verifiable_outcome": {"expected_state": expected_state, "evidence": evidence},
        "verification": {"method": method, "check": check},
        "ux_trace": ux_trace,
        PRODUCER_DETERMINATION_FIELD: requires_deliverable,
    }


def _lint_normalized_problem(parsed: Mapping[str, Any], patch_series_view: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    errors.extend(_lint_empty_slots(parsed))

    criteria = parsed.get("success_criteria")
    if not isinstance(criteria, list) or not criteria:
        errors.append("success_criteria must contain at least one nested criterion")
        return errors

    for index, criterion in enumerate(criteria):
        base = f"success_criteria[{index}]"
        if not isinstance(criterion, Mapping):
            errors.append(f"{base} is not a nested criterion object")
            continue
        if REJECTED_PRODUCER_DETERMINATION_ALIAS in criterion:
            errors.append(
                f"{base}.{REJECTED_PRODUCER_DETERMINATION_ALIAS} is an alias; "
                f"use {PRODUCER_DETERMINATION_FIELD}"
            )
        if not isinstance(criterion.get(PRODUCER_DETERMINATION_FIELD), bool):
            errors.append(
                f"{base}.{PRODUCER_DETERMINATION_FIELD} must be an explicit boolean"
            )
        for path in (
            "capability.action",
            "verifiable_outcome.expected_state",
            "verifiable_outcome.evidence",
            "verification.check",
        ):
            value = _nested_string(criterion, path)
            if value is None:
                errors.append(f"{base}.{path} is missing")
                continue
        preconditions = criterion.get("capability", {}).get("preconditions") if isinstance(
            criterion.get("capability"), Mapping
        ) else None
        if not isinstance(preconditions, list) or not preconditions:
            errors.append(f"{base}.capability.preconditions is empty")
    if _patch_series_is_user_facing(patch_series_view):
        errors.extend(_lint_user_facing_ux_success_criteria(criteria, patch_series_view))
    return errors


def _patch_series_is_user_facing(patch_series_view: Mapping[str, Any]) -> bool:
    ux = patch_series_view.get("user_experience_requirements")
    if not isinstance(ux, Mapping):
        return False
    applicability = ux.get("applicability")
    return isinstance(applicability, Mapping) and applicability.get("applicability") == "user_facing"


def _lint_user_facing_ux_success_criteria(criteria: object, patch_series_view: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if not isinstance(criteria, list):
        return ["success_criteria must include at least one player/user criterion with a resolved ux_trace"]
    found = False
    for index, criterion in enumerate(criteria):
        if not isinstance(criterion, Mapping):
            continue
        actor = str(criterion.get("actor", "")).lower()
        if "player" not in actor and "user" not in actor:
            continue
        ux_trace = str(criterion.get("ux_trace", "")).strip()
        if ux_trace == "none":
            continue
        resolved = _resolve_user_experience_path(patch_series_view, ux_trace)
        if resolved is None:
            errors.append(f"success_criteria[{index}].ux_trace does not resolve: {ux_trace}")
            continue
        found = True
    # MEMENTO: UX tracing is declared structurally and resolved mechanically. Never infer it
    # from prose overlap; two live normalize_problem runs failed from that substring-matching class.
    if not found:
        errors.append("success_criteria must include at least one player/user criterion with a resolved ux_trace")
    return errors


def _resolve_user_experience_path(patch_series_view: Mapping[str, Any], dotted_path: str) -> Any | None:
    parts = dotted_path.split(".")
    if len(parts) < 2 or parts[0] != "user_experience_requirements":
        return None
    current: Any = patch_series_view
    for part in parts:
        if isinstance(current, Mapping):
            if part not in current:
                return None
            current = current[part]
        elif isinstance(current, list):
            if not part.isdigit():
                return None
            index = int(part)
            if index >= len(current):
                return None
            current = current[index]
        else:
            return None
    return current if _has_non_empty_value(current) else None


def _has_non_empty_value(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, Mapping):
        return any(_has_non_empty_value(item) for item in value.values())
    if isinstance(value, list):
        return any(_has_non_empty_value(item) for item in value)
    return value is not None


NON_EMPTY_ARRAY_SLOT_NAMES = frozenset(
    {
        "success_criteria",
        "preconditions",
        "user_actions",
        "named_content",
        "core_systems",
        "draws_on",
        "because",
        "under_constraints",
        "accepting_tradeoffs",
        "systems",
        "key_modules",
        "saved_fields",
        "user_can",
    }
)


def _lint_empty_slots(value: Any, path: str = "") -> list[str]:
    errors: list[str] = []
    if isinstance(value, str):
        if not value.strip():
            errors.append(f"{path or 'value'} is empty")
    elif isinstance(value, Mapping):
        for key, item in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            errors.extend(_lint_empty_slots(item, child_path))
    elif isinstance(value, list):
        slot_name = path.rsplit(".", 1)[-1]
        if not value and slot_name in NON_EMPTY_ARRAY_SLOT_NAMES:
            errors.append(f"{path or 'value'} is empty")
        for index, item in enumerate(value):
            errors.extend(_lint_empty_slots(item, f"{path}[{index}]"))
    return errors


def _walk_normalized_problem_strings(value: Any, path: str = "") -> list[tuple[str, str]]:
    strings: list[tuple[str, str]] = []
    if isinstance(value, str):
        strings.append((path, value))
    elif isinstance(value, Mapping):
        for key, item in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            strings.extend(_walk_normalized_problem_strings(item, child_path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            strings.extend(_walk_normalized_problem_strings(item, f"{path}[{index}]"))
    return strings


def _nested_string(value: Mapping[str, Any], dotted_path: str) -> str | None:
    current: Any = value
    for part in dotted_path.split("."):
        if not isinstance(current, Mapping):
            return None
        current = current.get(part)
    return current if isinstance(current, str) else None


def _normalized_patch_series_phrases(patch_series_view: Mapping[str, Any]) -> set[str]:
    phrases: set[str] = set()
    for field in WORK_ORDER_VIEW_FIELDS:
        value = patch_series_view.get(field)
        values = value if isinstance(value, list) else [value]
        for item in values:
            if not isinstance(item, str):
                continue
            for phrase in _split_source_phrases(item):
                canonical = _canonical_phrase(phrase)
                if canonical:
                    phrases.add(canonical)
    return phrases


def _split_source_phrases(value: str) -> list[str]:
    phrases = [value]
    phrases.extend(line.strip(" -*\t") for line in value.splitlines())
    phrases.extend(part.strip() for part in re.split(r"[.;]", value))
    return [phrase for phrase in phrases if phrase.strip()]


def _canonical_phrase(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def _canonical_vague_value(value: str) -> str:
    return re.sub(r"^[\W_]+|[\W_]+$", "", value.strip().lower())


def _contains_japanese_script(value: str) -> bool:
    return bool(re.search(r"[\u3040-\u30ff\u3400-\u9fff]", value))


def _parse_constraints(raw: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        return _constraints_error(f"Codex constraint extraction returned invalid JSON: {exc}")

    if not isinstance(parsed, dict):
        return _constraints_error("Codex constraint extraction returned non-object JSON")
    if set(parsed) != set(EXTRACT_CONSTRAINTS_FIELDS):
        return _constraints_error("Codex constraint extraction returned invalid fields")

    hard_constraints = _parse_constraint_items(
        parsed.get("hard_constraints"),
        field="hard_constraints",
        item_fields=CONSTRAINT_ITEM_FIELDS,
        kind="hard",
    )
    if isinstance(hard_constraints, dict) and hard_constraints.get("ok") is False:
        return hard_constraints

    soft_preferences = _parse_constraint_items(
        parsed.get("soft_preferences"),
        field="soft_preferences",
        item_fields=PREFERENCE_ITEM_FIELDS,
        kind="soft",
    )
    if isinstance(soft_preferences, dict) and soft_preferences.get("ok") is False:
        return soft_preferences

    return {
        "hard_constraints": hard_constraints,
        "soft_preferences": soft_preferences,
    }



def _parse_constraint_items(
    value: Any,
    *,
    field: str,
    item_fields: tuple[str, ...],
    kind: str,
) -> list[dict[str, Any]] | dict[str, Any]:
    if not isinstance(value, list):
        return _constraints_error(f"Codex constraint extraction returned invalid {field}")

    items: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            return _constraints_error(f"Codex constraint extraction returned invalid {field} item")
        if set(item) != set(item_fields):
            return _constraints_error(f"Codex constraint extraction returned invalid {field} item fields")
        statement = item.get("statement")
        derivation = _parse_constraint_derivation(item.get("derivation"))
        if derivation is None:
            return _constraints_error(f"Codex constraint extraction returned invalid {field} derivation")
        if not isinstance(statement, str):
            return _constraints_error(f"Codex constraint extraction returned invalid {field} item values")
        if kind == "hard":
            implication = _parse_constraint_implication(item.get("implication"))
            if implication is None:
                return _constraints_error(f"Codex constraint extraction returned invalid {field} implication")
            items.append(
                {
                    "statement": statement,
                    "derivation": derivation,
                    "implication": implication,
                }
            )
        else:
            rationale = item.get("rationale")
            if not isinstance(rationale, str):
                return _constraints_error(f"Codex constraint extraction returned invalid {field} item values")
            items.append(
                {
                    "statement": statement,
                    "derivation": derivation,
                    "rationale": rationale,
                }
            )
    return items


def _parse_constraint_derivation(value: Any) -> dict[str, str] | None:
    if not isinstance(value, dict) or set(value) != set(CONSTRAINT_DERIVATION_FIELDS):
        return None
    source = value.get("from")
    trace = value.get("trace")
    if source not in CONSTRAINT_DERIVATION_VALUES or not isinstance(trace, str):
        return None
    return {"from": source, "trace": trace}


def _parse_constraint_implication(value: Any) -> dict[str, str] | None:
    if not isinstance(value, dict) or set(value) != set(CONSTRAINT_IMPLICATION_FIELDS):
        return None
    must = value.get("must")
    must_not = value.get("must_not")
    if not isinstance(must, str) or not isinstance(must_not, str):
        return None
    return {"must": must, "must_not": must_not}


def _constraints_approach_context(approach: Mapping[str, Any] | None) -> dict[str, Any]:
    if approach is None:
        return {}
    if "normalized_problem" in approach:
        return dict(approach)
    if set(approach) == set(NORMALIZED_PROBLEM_FIELDS):
        return {"normalized_problem": dict(approach)}
    return dict(approach)


def _lint_constraints(parsed: Mapping[str, Any], approach: Mapping[str, Any]) -> list[str]:
    return _lint_empty_slots(parsed)



# Memento (brief 34, general-artifact-engine): the org is a GENERAL artifact
# engine — deliverable-kind assumptions belong in DATA (the series' UX
# applicability), never hardcoded in validators. Live kill: the body-format
# spec run (request_type=research, UX not_user_facing, correctly grounded)
# honestly wrote platform "git worktree text files" / "git worktree text
# documents" and the product-app assumption in this guard declared it
# permanently invalid twice (fingerprint c34426f8ea41ffc8, 2026-07-05) — the
# brief-18 disease family: honest output strangled by a validator whose
# assumption does not hold.
# Original intent (f2caade) preserved EXACTLY, narrower: platform must not
# echo the org's own verification stack (live then: 'headless
# functional_check target'). functional_check and codex are org names in
# EVERY deliverable. 'worktree' is org-authoring leakage only when the
# deliverable is a user-facing product; for not_user_facing deliverables
# (specification documents, repository docs) it is git vocabulary of the
# deliverable's own target surface.
ORG_INTERNAL_PLATFORM_TOKENS = ("functional_check", "worktree", "codex")
ORG_INTERNAL_PLATFORM_TOKENS_ALL_DELIVERABLES = ("functional_check", "codex")
BROWSER_STANDARDS_STACK_NAMES = (
    "browser",
    "browser standards",
    "web platform",
    "web standards",
    "html css javascript",
    "html/css/javascript",
    "html/css/js",
    "vanilla web",
    "vanilla browser",
)


def _lint_candidate_stack_requirement(candidate: Mapping[str, Any], *, user_facing: bool = True) -> list[str]:
    requirement = candidate.get("stack_requirement")
    if not isinstance(requirement, Mapping):
        return ["stack_requirement is missing"]
    return _lint_stack_requirement_consistency(
        requirement, prefix="candidate stack_requirement", user_facing=user_facing
    )


def _candidate_empty_slot_lints(candidate: Mapping[str, Any]) -> list[str]:
    return [
        error
        for error in _lint_empty_slots(candidate)
        if not _candidate_allows_empty_slot(candidate, error)
    ]


def _candidate_allows_empty_slot(candidate: Mapping[str, Any], error: str) -> bool:
    requirement = candidate.get("stack_requirement")
    if not isinstance(requirement, Mapping):
        return False
    build_strategy = requirement.get("build_strategy")
    allowed_empty = {
        "engine_based": {"stack_requirement.framework is empty"},
        "framework_based": {"stack_requirement.engine is empty"},
        "from_scratch": {"stack_requirement.engine is empty", "stack_requirement.framework is empty"},
    }
    return error in allowed_empty.get(build_strategy, set())


def _lint_stack_requirement_consistency(
    requirement: Mapping[str, Any], *, prefix: str, user_facing: bool = True
) -> list[str]:
    errors: list[str] = []
    build_strategy = str(requirement.get("build_strategy", ""))
    engine = str(requirement.get("engine", "")).strip()
    framework = str(requirement.get("framework", "")).strip()
    platform = str(requirement.get("platform", "")).strip()
    if _platform_contains_org_internal_token(platform, user_facing=user_facing):
        # Both messages verified by blank-context correction probes (brief 34
        # amendment, scratchpad/brief34_probes/out_{uf,nu}.txt): each steered a
        # blank model to a platform value the validator accepts in its mode.
        if user_facing:
            errors.append(
                f"{prefix}.platform must name the user-facing runtime target, not org verifier vocabulary"
            )
        else:
            errors.append(
                f"{prefix}.platform must name the deliverable's own target surface (for example "
                "git-committed specification documents, repository docs); AI Org's own verification "
                "mechanisms (functional_check, codex) are not a platform"
            )
    if build_strategy == "engine_based" and (not engine or _names_browser_standards(engine)):
        errors.append(
            f"{prefix}.build_strategy engine_based requires a real engine/framework product in engine; "
            "hand-rolled browser standards must use from_scratch"
        )
    if build_strategy == "framework_based" and not framework:
        errors.append(f"{prefix}.build_strategy framework_based requires a framework product in framework")
    return errors


def _platform_contains_org_internal_token(platform: str, *, user_facing: bool = True) -> bool:
    # This is a role-scoped lint on one deliverable field. These tokens are AI Org
    # mechanism names, so their presence in platform indicates verifier leakage.
    # Brief 34: the token set is scoped by the deliverable's UX applicability —
    # see the ORG_INTERNAL_PLATFORM_TOKENS Memento for the live spec-run kill.
    canonical = platform.lower()
    tokens = ORG_INTERNAL_PLATFORM_TOKENS if user_facing else ORG_INTERNAL_PLATFORM_TOKENS_ALL_DELIVERABLES
    return any(token in canonical for token in tokens)


def _names_browser_standards(value: str) -> bool:
    canonical = " ".join(value.lower().replace("/", " ").replace("-", " ").split())
    compact = value.strip().lower()
    return canonical in BROWSER_STANDARDS_STACK_NAMES or compact in BROWSER_STANDARDS_STACK_NAMES


def _candidate_prune_feedback(pruned_candidates: list[dict[str, str]]) -> list[str]:
    if not pruned_candidates:
        return [
            "Fewer than two feasible candidates remain. Propose another candidate the org can author as text "
            "in the worktree and verify headlessly with functional_check."
        ]
    feedback = [
        "Fewer than two feasible candidates remain after deterministic pruning. Propose another candidate the "
        "org can author as text in the worktree and verify headlessly with functional_check."
    ]
    feedback.extend(
        f"Pruned {item['candidate_id']}: {item['objection']}"
        for item in pruned_candidates
        if item.get("candidate_id") and item.get("objection")
    )
    return feedback


def _candidate_id_was_pruned(candidate_id: str, pruned_candidates: list[dict[str, str]]) -> bool:
    return any(item.get("candidate_id") == candidate_id for item in pruned_candidates)


def _merge_candidate_generation_rejections(selected: dict[str, Any], candidates: Mapping[str, Any]) -> None:
    rejected = selected.setdefault("rejected", [])
    rejected_ids = {
        item.get("candidate_id")
        for item in rejected
        if isinstance(item, Mapping) and isinstance(item.get("candidate_id"), str)
    }
    for item in candidates.get("pruned_candidates", []) if isinstance(candidates, Mapping) else []:
        if not isinstance(item, Mapping):
            continue
        candidate_id = item.get("candidate_id")
        objection = item.get("objection")
        if not isinstance(candidate_id, str) or not isinstance(objection, str) or candidate_id in rejected_ids:
            continue
        rejected.append({"candidate_id": candidate_id, "objection": objection})
        rejected_ids.add(candidate_id)

    notes = candidates.get("degradation_notes", []) if isinstance(candidates, Mapping) else []
    if not isinstance(notes, list) or not all(isinstance(note, str) for note in notes):
        return
    rationale = selected.get("rationale")
    if not isinstance(rationale, dict):
        return
    tradeoffs = rationale.get("accepting_tradeoffs")
    if isinstance(tradeoffs, list):
        tradeoffs.extend(note for note in notes if note not in tradeoffs)



def _success_criterion_trace_phrases(approach: Mapping[str, Any]) -> set[str]:
    normalized = approach.get("normalized_problem") if isinstance(approach, Mapping) else None
    if not isinstance(normalized, Mapping):
        return set()
    criteria = normalized.get("success_criteria")
    if not isinstance(criteria, list):
        return set()

    phrases: set[str] = set()
    for index, criterion in enumerate(criteria):
        phrases.add(f"success_criteria[{index}]")
        if not isinstance(criterion, Mapping):
            continue
        for _, value in _walk_normalized_problem_strings(criterion):
            canonical = _canonical_phrase(value)
            if canonical:
                phrases.add(canonical)
    return phrases


def _trace_mentions_success_criterion(trace: str, phrases: set[str]) -> bool:
    canonical_trace = _canonical_phrase(trace)
    if "success_criteria[" in trace or "success criterion" in canonical_trace:
        return True
    return any(phrase and phrase in canonical_trace for phrase in phrases)


def _constraints_error(reason: str) -> dict[str, Any]:
    return {"ok": False, "error": reason}


def _parse_prior_art_map(raw: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        return _prior_art_error(f"Codex prior-art mapping returned invalid JSON: {exc}")

    if not isinstance(parsed, dict):
        return _prior_art_error("Codex prior-art mapping returned non-object JSON")
    if set(parsed) != set(PRIOR_ART_MAP_FIELDS):
        return _prior_art_error("Codex prior-art mapping returned invalid fields")

    patterns = parsed.get("patterns")
    if not isinstance(patterns, list) or not 3 <= len(patterns) <= 6:
        return _prior_art_error("Codex prior-art mapping returned invalid patterns")

    parsed_patterns: list[dict[str, Any]] = []
    for pattern in patterns:
        entry, error = _parse_prior_art_pattern(pattern)
        if entry is None:
            return _prior_art_error(error)
        parsed_patterns.append(entry)

    return {"patterns": parsed_patterns}


def _parse_prior_art_pattern(pattern: Any) -> tuple[dict[str, Any] | None, str]:
    """Parse one prior-art pattern (shared by the map parser and brief 25 fragments)."""
    if not isinstance(pattern, dict):
        return None, "Codex prior-art mapping returned invalid pattern item"
    extra_fields = set(pattern) - set(PRIOR_ART_PATTERN_FIELDS)
    if not set(PRIOR_ART_PATTERN_FIELDS) <= set(pattern) or extra_fields - set(REVISION_DECLARATION_FIELDS):
        return None, "Codex prior-art mapping returned invalid pattern fields"
    if not all(isinstance(pattern.get(field, ""), str) for field in REVISION_DECLARATION_FIELDS):
        return None, "Codex prior-art mapping returned invalid replacement declaration"
    source = _parse_prior_art_source(pattern.get("source"))
    if source is None:
        return None, "Codex prior-art mapping returned invalid source"
    tradeoffs = _parse_prior_art_tradeoffs(pattern.get("tradeoffs"))
    if tradeoffs is None:
        return None, "Codex prior-art mapping returned invalid tradeoffs"
    disposition = _parse_prior_art_disposition(pattern.get("disposition"))
    if disposition is None:
        return None, "Codex prior-art mapping returned invalid disposition"
    traces_to = pattern.get("traces_to")
    if not isinstance(traces_to, list) or not all(isinstance(item, str) for item in traces_to):
        return None, "Codex prior-art mapping returned invalid traces_to"
    name = pattern.get("name")
    when_applies = pattern.get("when_applies")
    if not isinstance(name, str) or not isinstance(when_applies, str):
        return None, "Codex prior-art mapping returned invalid pattern values"
    entry: dict[str, Any] = {
        "name": name,
        "source": source,
        "when_applies": when_applies,
        "tradeoffs": tradeoffs,
        "disposition": disposition,
        "traces_to": list(traces_to),
    }
    # Brief 19: carry a replacement declaration only when actually declared
    # (empty-slot lint stays meaningful; formation output shape unchanged).
    if str(pattern.get("replaces", "")).strip():
        entry["replaces"] = pattern["replaces"]
        entry["replacement_reason"] = pattern.get("replacement_reason", "")
    return entry, ""


def _prior_art_error(reason: str) -> dict[str, Any]:
    return {"ok": False, "error": reason}


def _parse_prior_art_source(value: Any) -> dict[str, str] | None:
    if not isinstance(value, dict) or set(value) != set(PRIOR_ART_SOURCE_FIELDS):
        return None
    reference_concept = value.get("reference_concept")
    facet_kind = value.get("facet_kind")
    where = value.get("where")
    if not isinstance(reference_concept, str) or facet_kind not in PRIOR_ART_FACET_KINDS or not isinstance(where, str):
        return None
    return {"reference_concept": reference_concept, "facet_kind": facet_kind, "where": where}


def _parse_prior_art_tradeoffs(value: Any) -> dict[str, list[str]] | None:
    if not isinstance(value, dict) or set(value) != set(PRIOR_ART_TRADEOFF_FIELDS):
        return None
    pros = value.get("pros")
    cons = value.get("cons")
    if not isinstance(pros, list) or not isinstance(cons, list):
        return None
    if not all(isinstance(item, str) for item in pros + cons):
        return None
    return {"pros": list(pros), "cons": list(cons)}


def _parse_prior_art_disposition(value: Any) -> dict[str, str] | None:
    if not isinstance(value, dict) or set(value) != set(PRIOR_ART_DISPOSITION_FIELDS):
        return None
    choice = value.get("choice")
    why = value.get("why")
    if choice not in PRIOR_ART_DISPOSITIONS or not isinstance(why, str):
        return None
    return {"choice": choice, "why": why}


def _prior_art_approach_context(approach: Mapping[str, Any] | None) -> dict[str, Any]:
    if approach is None:
        return {}
    if "normalized_problem" in approach or "constraints" in approach:
        return dict(approach)
    if set(approach) == set(NORMALIZED_PROBLEM_FIELDS):
        return {"normalized_problem": dict(approach)}
    return dict(approach)


def _lint_prior_art_map(
    parsed: Mapping[str, Any],
    reference_facets: list[dict[str, Any]],
    approach: Mapping[str, Any],
) -> list[str]:
    errors = _lint_empty_slots(parsed)
    retrieved = _retrieved_precedent_facet_index(reference_facets)
    for pattern in parsed.get("patterns", []):
        if not isinstance(pattern, Mapping):
            continue
        source = pattern.get("source")
        if not isinstance(source, Mapping):
            continue
        concept = str(source.get("reference_concept") or "").strip().lower()
        facet_kind = str(source.get("facet_kind") or "").strip()
        available_kinds = retrieved.get(concept, set())
        if available_kinds and facet_kind == "none":
            errors.append(
                "prior_art source uses facet_kind none for "
                f"{source.get('reference_concept')!r}, but precedent store returned "
                f"{', '.join(sorted(available_kinds))} facets"
            )
        elif available_kinds and facet_kind not in available_kinds:
            errors.append(
                "prior_art source uses unavailable facet_kind "
                f"{facet_kind!r} for {source.get('reference_concept')!r}; "
                f"available: {', '.join(sorted(available_kinds))}"
            )
    return errors


def _retrieved_precedent_facet_index(reference_facets: list[dict[str, Any]]) -> dict[str, set[str]]:
    retrieved: dict[str, set[str]] = {}
    for facet in reference_facets:
        if not isinstance(facet, Mapping):
            continue
        term = str(facet.get("term") or "").strip().lower()
        if not term:
            continue
        kinds = {kind for kind in ("design", "implementation") if facet.get(kind)}
        if kinds:
            retrieved[term] = kinds
    return retrieved


def _prior_art_trace_targets(approach: Mapping[str, Any]) -> set[str]:
    targets: set[str] = set()
    for root in ("normalized_problem", "constraints"):
        value = approach.get(root) if isinstance(approach, Mapping) else None
        if not isinstance(value, Mapping):
            continue
        targets.add(root)
        for path, _ in _walk_normalized_problem_strings(value):
            if path:
                targets.add(f"{root}.{path}")
    return targets


def _prior_art_trace_is_known(trace: str, targets: set[str]) -> bool:
    canonical = _canonical_phrase(trace)
    return any(canonical == target or canonical.startswith(f"{target}.") or canonical.startswith(f"{target}[") for target in targets)


def _parse_candidate_approach(raw: str, expected_kind: str | None = None) -> dict[str, Any]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        return _candidate_generation_error(f"Codex candidate generation returned invalid JSON: {exc}")

    if not isinstance(parsed, dict):
        return _candidate_generation_error("Codex candidate generation returned non-object JSON")
    extra_fields = set(parsed) - set(CANDIDATE_APPROACH_FIELDS)
    if not set(CANDIDATE_APPROACH_FIELDS) <= set(parsed) or extra_fields - set(REVISION_DECLARATION_FIELDS):
        return _candidate_generation_error("Codex candidate generation returned invalid candidate fields")
    if not all(isinstance(parsed.get(field, ""), str) for field in REVISION_DECLARATION_FIELDS):
        return _candidate_generation_error("Codex candidate generation returned invalid replacement declaration")
    if not all(isinstance(parsed[field], str) for field in ("id", "name", "kind", "summary")):
        return _candidate_generation_error("Codex candidate generation returned invalid candidate string fields")
    if parsed["kind"] not in CANDIDATE_APPROACH_KINDS:
        return _candidate_generation_error("Codex candidate generation returned invalid candidate kind")
    if expected_kind is not None and parsed["kind"] != expected_kind:
        return _candidate_generation_error("Codex candidate generation returned unexpected candidate kind")
    stack_requirement = _parse_candidate_stack_requirement(parsed.get("stack_requirement"))
    if stack_requirement is None:
        return _candidate_generation_error("Codex candidate generation returned invalid stack_requirement")
    first_proof_moment = _parse_candidate_first_proof_moment(parsed.get("first_proof_moment"))
    if first_proof_moment is None:
        return _candidate_generation_error("Codex candidate generation returned invalid first_proof_moment")
    for field in ("core_systems", "draws_on"):
        value = parsed.get(field)
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            return _candidate_generation_error(f"Codex candidate generation returned invalid {field}")
    result = {
        "id": parsed["id"],
        "name": parsed["name"],
        "kind": parsed["kind"],
        "summary": parsed["summary"],
        "stack_requirement": stack_requirement,
        "first_proof_moment": first_proof_moment,
        "core_systems": list(parsed["core_systems"]),
        "draws_on": list(parsed["draws_on"]),
    }
    # Memento (brief 19): declaration keys are carried only when a replacement is
    # actually declared, so the empty-slot lint stays meaningful for every other
    # field and non-replacement outputs stay byte-identical to formation shape.
    # A declared replacement with an empty reason keeps the empty reason visible
    # to the empty-slot lint: a declaration without a reason is rejected.
    if str(parsed.get("replaces", "")).strip():
        result["replaces"] = parsed["replaces"]
        result["replacement_reason"] = parsed.get("replacement_reason", "")
    return result


def _parse_candidate_stack_requirement(value: Any) -> dict[str, str] | None:
    if not isinstance(value, dict) or set(value) != set(CANDIDATE_STACK_REQUIREMENT_FIELDS):
        return None
    if not all(isinstance(value[field], str) for field in CANDIDATE_STACK_REQUIREMENT_FIELDS):
        return None
    if value["build_strategy"] not in TECH_STACK_CONCRETE_BUILD_STRATEGIES:
        return None
    if value["authoring_model"] not in CANDIDATE_AUTHORING_MODELS:
        return None
    if value["verification_model"] not in CANDIDATE_VERIFICATION_MODELS:
        return None
    return {field: value[field] for field in CANDIDATE_STACK_REQUIREMENT_FIELDS}


def _parse_candidate_first_proof_moment(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict) or set(value) != set(CANDIDATE_FIRST_PROOF_FIELDS):
        return None
    user_actions = value.get("user_actions")
    win_or_progress_condition = value.get("win_or_progress_condition")
    named_content = _parse_named_content(value.get("named_content"))
    if not isinstance(user_actions, list) or not all(isinstance(item, str) for item in user_actions):
        return None
    if named_content is None or not isinstance(win_or_progress_condition, str):
        return None
    return {
        "user_actions": list(user_actions),
        "named_content": named_content,
        "win_or_progress_condition": win_or_progress_condition,
    }


def _parse_named_content(value: Any) -> list[dict[str, str]] | None:
    if not isinstance(value, list):
        return None
    parsed: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict) or set(item) != set(NAMED_CONTENT_ITEM_FIELDS):
            return None
        name = item.get("name")
        kind = item.get("kind")
        if not isinstance(name, str) or not isinstance(kind, str):
            return None
        parsed.append({"name": name, "kind": kind})
    return parsed


def _candidate_generation_error(reason: str) -> dict[str, Any]:
    return {"ok": False, "error": reason}


def _candidate_ids(candidates: Mapping[str, Any]) -> list[str]:
    candidate_items = candidates.get("candidates")
    if not isinstance(candidate_items, list):
        return []

    ids: list[str] = []
    for candidate in candidate_items:
        if not isinstance(candidate, Mapping):
            return []
        candidate_id = candidate.get("id")
        if not isinstance(candidate_id, str):
            return []
        ids.append(candidate_id)
    if len(ids) != len(set(ids)):
        return []
    return ids


def _evaluation_ids(evaluations: Mapping[str, Any]) -> list[str]:
    evaluation_items = evaluations.get("evaluations")
    if not isinstance(evaluation_items, list):
        return []

    ids: list[str] = []
    for evaluation in evaluation_items:
        if not isinstance(evaluation, Mapping):
            return []
        candidate_id = evaluation.get("candidate_id")
        if not isinstance(candidate_id, str):
            return []
        ids.append(candidate_id)
    if len(ids) != len(set(ids)):
        return []
    return ids


def _parse_candidate_evaluation(raw: str, candidate_id: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        return _candidate_evaluation_error(f"Codex candidate evaluation returned invalid JSON: {exc}")

    if not isinstance(parsed, dict):
        return _candidate_evaluation_error("Codex candidate evaluation returned non-object JSON")
    if set(parsed) != set(CANDIDATE_EVALUATION_FIELDS):
        return _candidate_evaluation_error("Codex candidate evaluation returned invalid evaluation fields")
    if parsed.get("candidate_id") != candidate_id:
        return _candidate_evaluation_error("Codex candidate evaluation returned wrong candidate_id")
    scores = parsed.get("scores")
    if not isinstance(scores, dict) or set(scores) != set(CANDIDATE_EVALUATION_SCORE_FIELDS):
        return _candidate_evaluation_error("Codex candidate evaluation returned invalid score fields")
    parsed_scores: dict[str, dict[str, str]] = {}
    for field in CANDIDATE_EVALUATION_SCORE_FIELDS:
        score = scores.get(field)
        if not isinstance(score, dict) or set(score) != set(EVALUATION_SCORE_FIELDS):
            return _candidate_evaluation_error("Codex candidate evaluation returned invalid score object")
        rating = score.get("rating")
        reason = score.get("reason")
        if not isinstance(rating, str) or not isinstance(reason, str):
            return _candidate_evaluation_error("Codex candidate evaluation returned invalid score values")
        parsed_scores[field] = {"rating": rating, "reason": reason}
    return {"candidate_id": candidate_id, "scores": parsed_scores}


def _candidate_evaluation_error(reason: str) -> dict[str, Any]:
    return {"ok": False, "error": reason}


def _parse_approach_selection(raw: str, candidate_ids: list[str]) -> dict[str, Any]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        return _approach_selection_error(f"Codex approach selection returned invalid JSON: {exc}")

    if not isinstance(parsed, dict):
        return _approach_selection_error("Codex approach selection returned non-object JSON")
    if set(parsed) != set(SELECT_APPROACH_FIELDS):
        return _approach_selection_error("Codex approach selection returned invalid fields")

    chosen = parsed.get("selected_candidate_id")
    if not isinstance(chosen, str) or chosen not in candidate_ids:
        return _approach_selection_error("Codex approach selection returned unknown chosen candidate")

    arguments = parsed.get("arguments")
    if not isinstance(arguments, list):
        return _approach_selection_error("Codex approach selection returned invalid arguments")
    parsed_arguments: list[dict[str, str]] = []
    for argument in arguments:
        if not isinstance(argument, dict) or set(argument) != set(TOULMIN_ARGUMENT_FIELDS):
            return _approach_selection_error("Codex approach selection returned invalid argument")
        if argument.get("role") not in TOULMIN_ARGUMENT_ROLES:
            return _approach_selection_error("Codex approach selection returned invalid argument role")
        if argument.get("about_candidate_id") not in candidate_ids:
            return _approach_selection_error("Codex approach selection returned argument for unknown candidate")
        if not all(isinstance(argument[field], str) for field in TOULMIN_ARGUMENT_FIELDS):
            return _approach_selection_error("Codex approach selection returned invalid argument values")
        parsed_arguments.append({field: argument[field] for field in TOULMIN_ARGUMENT_FIELDS})

    rationale = parsed.get("rationale")
    if not isinstance(rationale, dict) or set(rationale) != set(DECISION_RATIONALE_FIELDS):
        return _approach_selection_error("Codex approach selection returned invalid rationale")
    parsed_rationale: dict[str, list[str]] = {}
    for field in DECISION_RATIONALE_FIELDS:
        value = rationale.get(field)
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            return _approach_selection_error("Codex approach selection returned invalid rationale values")
        parsed_rationale[field] = list(value)

    stack_axes = _parse_stack_decision_axes(parsed.get("stack_axes"))
    if stack_axes is None:
        return _approach_selection_error("Codex approach selection returned invalid stack_axes")

    rejected = parsed.get("rejected")
    if not isinstance(rejected, list):
        return _approach_selection_error("Codex approach selection returned invalid rejected list")
    open_questions = parsed.get("open_questions")
    if not isinstance(open_questions, list) or not all(isinstance(item, str) for item in open_questions):
        return _approach_selection_error("Codex approach selection returned invalid open_questions")

    parsed_rejected: list[dict[str, str]] = []
    rejected_ids: set[str] = set()
    for rejection in rejected:
        if not isinstance(rejection, dict):
            return _approach_selection_error("Codex approach selection returned invalid rejected item")
        if set(rejection) != set(REJECTED_APPROACH_FIELDS):
            return _approach_selection_error("Codex approach selection returned invalid rejected fields")
        if not all(isinstance(rejection[field], str) for field in REJECTED_APPROACH_FIELDS):
            return _approach_selection_error("Codex approach selection returned invalid rejected values")

        rejected_id = rejection["candidate_id"]
        if rejected_id not in candidate_ids:
            return _approach_selection_error("Codex approach selection returned unknown rejected candidate")
        if rejected_id == chosen:
            return _approach_selection_error("Codex approach selection returned rejection for the chosen candidate")
        if rejected_id in rejected_ids:
            return _approach_selection_error("Codex approach selection returned duplicate rejected candidate")
        rejected_ids.add(rejected_id)
        parsed_rejected.append({"candidate_id": rejected_id, "objection": rejection["objection"]})

    if rejected_ids != (set(candidate_ids) - {chosen}):
        return _approach_selection_error("Codex approach selection returned incomplete rejected candidates")

    return {
        "selected_candidate_id": chosen,
        "arguments": parsed_arguments,
        "rationale": parsed_rationale,
        "stack_axes": stack_axes,
        "rejected": parsed_rejected,
        "open_questions": list(open_questions),
    }


def _approach_selection_error(reason: str) -> dict[str, Any]:
    return {"ok": False, "error": reason}


def _parse_stack_decision_axes(value: Any) -> dict[str, dict[str, str]] | None:
    if not isinstance(value, dict) or set(value) != set(STACK_DECISION_AXIS_FIELDS):
        return None
    parsed: dict[str, dict[str, str]] = {}
    for axis in STACK_DECISION_AXIS_FIELDS:
        slot = value.get(axis)
        if not isinstance(slot, dict) or set(slot) != set(STACK_DECISION_AXIS_SLOT_FIELDS):
            return None
        evidence = slot.get("evidence")
        judgment = slot.get("judgment")
        if not isinstance(evidence, str) or not isinstance(judgment, str):
            return None
        parsed[axis] = {"evidence": evidence, "judgment": judgment}
    return parsed


def _parse_implementation_strategy(raw: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        return _implementation_strategy_error(f"Codex implementation strategy returned invalid JSON: {exc}")

    if not isinstance(parsed, dict):
        return _implementation_strategy_error("Codex implementation strategy returned non-object JSON")
    if set(parsed) != set(IMPLEMENTATION_STRATEGY_FIELDS):
        return _implementation_strategy_error("Codex implementation strategy returned invalid fields")

    systems = parsed.get("systems")
    if not isinstance(systems, list):
        return _implementation_strategy_error("Codex implementation strategy returned invalid systems")
    parsed_systems: list[dict[str, Any]] = []
    for system in systems:
        if not isinstance(system, dict) or set(system) != set(IMPLEMENTATION_SYSTEM_FIELDS):
            return _implementation_strategy_error("Codex implementation strategy returned invalid system")
        named_content = _parse_named_content(system.get("named_content"))
        key_modules = system.get("key_modules")
        if named_content is None:
            return _implementation_strategy_error("Codex implementation strategy returned invalid named_content")
        if not isinstance(key_modules, list) or not all(isinstance(item, str) for item in key_modules):
            return _implementation_strategy_error("Codex implementation strategy returned invalid key_modules")
        if (
            not isinstance(system.get("system_name"), str)
            or not isinstance(system.get("observable_behavior"), str)
            or not isinstance(system.get("observable_effect"), str)
        ):
            return _implementation_strategy_error("Codex implementation strategy returned invalid system strings")
        parsed_systems.append(
            {
                "system_name": system["system_name"],
                "observable_behavior": system["observable_behavior"],
                "observable_effect": system["observable_effect"],
                "named_content": named_content,
                "key_modules": list(key_modules),
            }
        )

    persistence = parsed.get("persistence")
    if not isinstance(persistence, dict) or set(persistence) != set(IMPLEMENTATION_PERSISTENCE_FIELDS):
        return _implementation_strategy_error("Codex implementation strategy returned invalid persistence")
    saved_fields = persistence.get("saved_fields")
    if not isinstance(saved_fields, list) or not all(isinstance(item, str) for item in saved_fields):
        return _implementation_strategy_error("Codex implementation strategy returned invalid saved_fields")
    return {"systems": parsed_systems, "persistence": {"saved_fields": list(saved_fields)}}


def _parse_domain_specification(raw: str, expected_facets: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        return _domain_specification_error(f"Codex domain specification returned invalid JSON: {exc}")

    if not isinstance(parsed, dict):
        return _domain_specification_error("Codex domain specification returned non-object JSON")
    if set(parsed) != set(DOMAIN_SPECIFICATION_FIELDS):
        return _domain_specification_error("Codex domain specification returned invalid fields")
    aspects = parsed.get("aspects")
    if not isinstance(aspects, list):
        return _domain_specification_error("Codex domain specification returned invalid aspects")

    expected_names = [facet["aspect_name"] for facet in expected_facets or [] if isinstance(facet.get("aspect_name"), str)]
    parsed_aspects: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    for index, aspect in enumerate(aspects):
        if not isinstance(aspect, dict) or set(aspect) != set(DOMAIN_ASPECT_FIELDS):
            return _domain_specification_error(f"Codex domain specification returned invalid aspect fields at aspects[{index}]")
        aspect_name = aspect.get("aspect_name")
        applicability = aspect.get("applicability")
        specification_body = aspect.get("specification_body")
        if not isinstance(aspect_name, str) or not isinstance(specification_body, str):
            return _domain_specification_error("Codex domain specification returned invalid aspect strings")
        if applicability not in DOMAIN_APPLICABILITY_VALUES:
            return _domain_specification_error("Codex domain specification returned invalid applicability")
        quantities = _parse_domain_quantities(aspect.get("quantities"))
        if quantities is None:
            return _domain_specification_error("Codex domain specification returned invalid quantities")
        try:
            tables = _parse_domain_tables(aspect.get("tables"))
        except ValueError as exc:
            return _domain_specification_error(str(exc))
        if tables is None:
            return _domain_specification_error("Codex domain specification returned invalid tables")
        sources = aspect.get("sources")
        if not isinstance(sources, list) or not all(isinstance(source, str) for source in sources):
            return _domain_specification_error("Codex domain specification returned invalid sources")
        if aspect_name in seen_names:
            return _domain_specification_error(f"Codex domain specification returned duplicate aspect_name {aspect_name}")
        seen_names.add(aspect_name)
        parsed_aspects.append(
            {
                "aspect_name": aspect_name,
                "applicability": applicability,
                "specification_body": specification_body,
                "quantities": quantities,
                "tables": tables,
                "sources": list(sources),
            }
        )

    if expected_names and seen_names != set(expected_names):
        missing = sorted(set(expected_names) - seen_names)
        extra = sorted(seen_names - set(expected_names))
        return _domain_specification_error(
            "Codex domain specification returned aspect_name mismatch"
            + (f"; missing: {', '.join(missing)}" if missing else "")
            + (f"; extra: {', '.join(extra)}" if extra else "")
        )
    return {"aspects": parsed_aspects}


def _parse_domain_quantities(value: Any) -> list[dict[str, str]] | None:
    if not isinstance(value, list):
        return None
    quantities: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict) or set(item) != set(DOMAIN_QUANTITY_FIELDS):
            return None
        if not all(isinstance(item[field], str) for field in DOMAIN_QUANTITY_FIELDS):
            return None
        quantities.append({field: item[field] for field in DOMAIN_QUANTITY_FIELDS})
    return quantities


def _parse_domain_tables(value: Any) -> list[dict[str, Any]] | None:
    if not isinstance(value, list):
        return None
    tables: list[dict[str, Any]] = []
    for table_index, table in enumerate(value):
        if not isinstance(table, dict) or set(table) != set(DOMAIN_TABLE_FIELDS):
            return None
        table_name = table.get("table_name")
        columns = table.get("columns")
        rows = table.get("rows")
        if not isinstance(table_name, str) or not isinstance(columns, list) or not all(isinstance(column, str) for column in columns):
            return None
        if not isinstance(rows, list):
            return None
        parsed_rows: list[list[str]] = []
        for row_index, row in enumerate(rows):
            if not isinstance(row, list) or not all(isinstance(cell, str) for cell in row):
                return None
            if len(row) != len(columns):
                raise ValueError(
                    f"domain specification table rows width mismatch at tables[{table_index}].rows[{row_index}]"
                )
            parsed_rows.append(list(row))
        tables.append({"table_name": table_name, "columns": list(columns), "rows": parsed_rows})
    return tables


def _lint_domain_specification(parsed: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    for index, aspect in enumerate(parsed.get("aspects", []) if isinstance(parsed, Mapping) else []):
        if not isinstance(aspect, Mapping):
            continue
        prefix = f"aspects[{index}]"
        applicability = aspect.get("applicability")
        aspect_name = str(aspect.get("aspect_name") or "").strip()
        body = str(aspect.get("specification_body") or "").strip()
        quantities = aspect.get("quantities")
        tables = aspect.get("tables")
        sources = aspect.get("sources")
        if not aspect_name:
            errors.append(f"{prefix}.aspect_name is empty")
        if not body:
            errors.append(f"{prefix}.specification_body is empty")
        if applicability == "not_applicable":
            # precedent facet: required all-props payload artifact tolerance. Mode-irrelevant
            # quantities/tables are ignored by consumers and never fail solely for being present.
            continue
        if applicability == "applies":
            errors.extend(_lint_empty_slots(aspect))
            has_quantity = isinstance(quantities, list) and bool(quantities)
            has_table = isinstance(tables, list) and bool(tables)
            if not has_quantity and not has_table:
                errors.append(f"{prefix} applies but has no quantity or table")
            if not isinstance(sources, list) or not any(str(source).strip() for source in sources):
                errors.append(f"{prefix} applies but has no source")
    return errors


def _domain_profile_facets(profile: Any) -> list[dict[str, Any]]:
    if isinstance(profile, Mapping):
        raw_facets = profile.get("facets")
        if not isinstance(raw_facets, list):
            raw_facets = profile.get("aspects")
    else:
        raw_facets = profile
    if not isinstance(raw_facets, list):
        return []
    facets: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(raw_facets, start=1):
        if isinstance(item, Mapping):
            aspect_name = str(item.get("aspect_name") or item.get("name") or item.get("term") or "").strip()
            if not aspect_name:
                aspect_name = f"aspect {index}"
            facet = {str(key): _json_safe(value) for key, value in item.items()}
            facet["aspect_name"] = aspect_name
        else:
            aspect_name = str(item or "").strip() or f"aspect {index}"
            facet = {"aspect_name": aspect_name}
        if aspect_name in seen:
            continue
        seen.add(aspect_name)
        facets.append(facet)
    return facets


DOMAIN_REFERENCE_QUERY_STOPWORDS = {
    "a",
    "an",
    "and",
    "app",
    "build",
    "create",
    "for",
    "from",
    "make",
    "need",
    "needs",
    "of",
    "or",
    "players",
    "the",
    "to",
    "with",
}


def _domain_precedent_facets_from_patch_series(
    patch_series_view: Mapping[str, Any],
    implementation_strategy: Mapping[str, Any],
    context: Mapping[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    # Memento: the completeness gate apparatus was built and removed on ratifier
    # decision: it over-engineered the problem and blocked the deliverable. The
    # surviving requirement is exactly this content-aware store consumption.
    terms = _domain_precedent_query_terms(patch_series_view, implementation_strategy)
    reference_context = _precedent_stack_context(context)
    lookups: dict[str, Any] = {}
    facets: list[dict[str, Any]] = []
    seen_aspects: set[str] = set()
    for term in terms:
        try:
            lookup = engineering_precedent_store.lookup(term, reference_context, kind="design")
        except Exception:
            continue
        if not lookup or not lookup.get("candidates"):
            continue
        lookups[term] = _json_safe(lookup)
        for facet in _domain_facets_from_precedent_lookup(term, lookup):
            aspect_name = str(facet.get("aspect_name") or "").strip()
            key = aspect_name.lower()
            if not aspect_name or key in seen_aspects:
                continue
            seen_aspects.add(key)
            facets.append(facet)
    # Memento (brief 26): precedent-store hits were the ONLY facet source, so a
    # store-miss domain formed an EMPTY domain_specification even with fully
    # populated UX requirements — and _domain_specification short-circuits empty
    # facets to {"aspects": []} without a model call. The committed view is a
    # facet source in its own right: store-derived facets keep priority, UX
    # surfaces fill what the store missed.
    for facet in _ux_requirement_facets(patch_series_view):
        key = str(facet.get("aspect_name") or "").strip().lower()
        if not key or key in seen_aspects:
            continue
        seen_aspects.add(key)
        facets.append(facet)
    return facets, lookups


def _ux_requirement_facets(patch_series_view: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Facets derived from the committed view's user_experience_requirements.

    Memento (brief 26, live: pomodoro run 3, formation commit ccede62 in
    /tmp/practice_product): the CLI series formed an empty domain_specification
    because every precedent-store lookup missed, and reform then re-derived
    facets FROM the empty committed aspects — GIGO bootstrap, empty through
    every reform round (rounds 2 and 3 flagged it; cap-nak trajectory). Each
    populated UX section of a user_facing view becomes one facet naming the
    surface and carrying the committed requirement text VERBATIM. Code supplies
    structure and committed text only — never invented content; the model still
    authors every aspect body.
    """
    ux = patch_series_view.get("user_experience_requirements") if isinstance(patch_series_view, Mapping) else None
    if not isinstance(ux, Mapping):
        return []
    applicability = ux.get("applicability")
    if not isinstance(applicability, Mapping) or applicability.get("applicability") != "user_facing":
        return []
    facets: list[dict[str, Any]] = []
    for field in USER_EXPERIENCE_REQUIREMENTS_FIELDS:
        if field == "applicability":
            continue
        section = ux.get(field)
        if isinstance(section, Mapping):
            populated = any(str(value or "").strip() for value in section.values())
        elif isinstance(section, list):
            populated = bool(section)
        else:
            populated = bool(str(section or "").strip())
        if not populated:
            continue
        facets.append(
            {
                "aspect_name": field.replace("_", " "),
                "source": "patch_series_view.user_experience_requirements",
                "requirement_surface": _json_safe(section),
            }
        )
    return facets


def _domain_precedent_query_terms(
    patch_series_view: Mapping[str, Any],
    implementation_strategy: Mapping[str, Any] | None = None,
    *,
    limit: int = 10,
) -> list[str]:
    qualifier = _domain_precedent_qualifier(patch_series_view)
    text = _domain_precedent_source_text(patch_series_view)
    systems = _implementation_system_names(implementation_strategy or {})
    terms: list[str] = []

    lowered = text.lower()
    if qualifier == "JRPG" and any(word in lowered for word in ("battle", "combat", "enemy", "spell", "level")):
        for phrase in ("JRPG combat formula", "JRPG content budget", "JRPG level curve"):
            _append_short_domain_term(terms, phrase, limit)

    for system_name in systems:
        system = _short_domain_subject(system_name)
        if system:
            _append_short_domain_term(terms, f"{qualifier} {system}", limit)
            if any(word in system for word in ("battle", "combat")):
                _append_short_domain_term(terms, f"{qualifier} combat formula", limit)

    for subject in _domain_subject_phrases(text):
        _append_short_domain_term(terms, f"{qualifier} {subject}", limit)
        if subject in {"battle", "combat"}:
            _append_short_domain_term(terms, f"{qualifier} {subject} numbers", limit)
        if len(terms) >= limit:
            break

    return terms


def _domain_precedent_qualifier(patch_series_view: Mapping[str, Any]) -> str:
    text = _domain_precedent_source_text(patch_series_view).lower()
    if any(value in text for value in ("jrpg", "dragon quest", "command rpg", "command-rpg", "turn based rpg", "turn-based rpg")):
        return "JRPG"
    if "rpg" in text:
        return "RPG"
    if any(value in text for value in ("game", "battle", "combat", "enemy", "spell")):
        return "game"
    kind = str(patch_series_view.get("deliverable_kind") or patch_series_view.get("request_type") or "").strip().replace("_", " ")
    return _short_domain_subject(kind) or "deliverable"


def _domain_precedent_source_text(patch_series_view: Mapping[str, Any]) -> str:
    fields = (
        "working_title",
        "raw_request",
        "request_type",
        "deliverable_kind",
        "problem_or_motivation",
        "desired_outcomes_success",
        "affected_area_platform",
        "background_facts",
        "proposal_hint",
    )
    chunks = [str(patch_series_view.get(field) or "") for field in fields]
    ux = patch_series_view.get("user_experience_requirements")
    if isinstance(ux, Mapping):
        chunks.append(json.dumps(_json_safe(ux), sort_keys=True, ensure_ascii=True, default=str))
    return " ".join(chunk for chunk in chunks if chunk)


def _implementation_system_names(implementation_strategy: Mapping[str, Any]) -> list[str]:
    systems = implementation_strategy.get("systems")
    if not isinstance(systems, list):
        return []
    names: list[str] = []
    for system in systems:
        if isinstance(system, Mapping):
            name = str(system.get("system_name") or "").strip()
            if name:
                names.append(name)
    return names


def _domain_subject_phrases(text: str) -> list[str]:
    words = [
        word
        for word in re.findall(r"[A-Za-z][A-Za-z0-9'-]*", text.lower())
        if len(word) > 2 and word not in DOMAIN_REFERENCE_QUERY_STOPWORDS
    ]
    subjects: list[str] = []
    priority = ("combat", "battle", "level", "enemy", "spell", "inventory", "quest", "dialogue", "asset", "audio", "animation")
    for word in priority:
        if word in words and word not in subjects:
            subjects.append(word)
    for index in range(len(words) - 1):
        if len(subjects) >= 8:
            break
        phrase = f"{words[index]} {words[index + 1]}"
        if phrase not in subjects:
            subjects.append(phrase)
    for word in words:
        if len(subjects) >= 8:
            break
        if word not in subjects:
            subjects.append(word)
    return subjects


def _short_domain_subject(value: str) -> str:
    words = [
        word
        for word in re.findall(r"[A-Za-z][A-Za-z0-9'-]*", str(value).lower())
        if len(word) > 1 and word not in DOMAIN_REFERENCE_QUERY_STOPWORDS
    ]
    return " ".join(words[:3])


def _append_short_domain_term(terms: list[str], phrase: str, limit: int) -> None:
    if len(terms) >= limit:
        return
    cleaned = " ".join(str(phrase or "").split()).strip()
    if not cleaned:
        return
    if not (2 <= len(cleaned.split()) <= 5):
        return
    if cleaned not in terms:
        terms.append(cleaned)


def _domain_facets_from_precedent_lookup(term: str, lookup: Mapping[str, Any]) -> list[dict[str, Any]]:
    candidates = lookup.get("candidates")
    if not isinstance(candidates, list):
        return []
    facets: list[dict[str, Any]] = []
    for index, candidate in enumerate(candidates, start=1):
        if not isinstance(candidate, Mapping):
            continue
        aspect_name = _domain_aspect_name_from_reference(term, candidate, index)
        facet = {
            "aspect_name": aspect_name,
            "query_term": term,
            "source": "engineering_precedent_store.lookup",
            "candidate": _json_safe(candidate),
        }
        materialized = _materialized_domain_aspect_from_candidate(aspect_name, term, candidate)
        if materialized is not None:
            facet.update(materialized)
        facets.append(facet)
    return facets


def _domain_aspect_name_from_reference(term: str, candidate: Mapping[str, Any], index: int) -> str:
    for key in ("aspect_name", "name", "term"):
        value = str(candidate.get(key) or "").strip()
        if value:
            return value[:120]
    value = str(candidate.get("structure") or candidate.get("summary") or term).strip()
    words = re.findall(r"[A-Za-z0-9'-]+", value)
    return (" ".join(words[:5]) or f"{term} facet {index}")[:120]


def _materialized_domain_aspect_from_candidate(
    aspect_name: str,
    term: str,
    candidate: Mapping[str, Any],
) -> dict[str, Any] | None:
    quantities = _parse_domain_quantities(candidate.get("quantities")) if "quantities" in candidate else None
    if quantities is None:
        quantities = []
    try:
        tables = _parse_domain_tables(candidate.get("tables")) if "tables" in candidate else None
    except ValueError:
        tables = None
    if tables is None:
        tables = []
    if not quantities and not tables:
        return None
    body = str(
        candidate.get("specification_body")
        or candidate.get("structure")
        or candidate.get("summary")
        or candidate.get("rationale")
        or term
    ).strip()
    sources = _domain_candidate_sources(candidate, term)
    return {
        "applicability": "applies",
        "specification_body": body,
        "quantities": quantities,
        "tables": tables,
        "sources": sources,
    }


def _domain_candidate_sources(candidate: Mapping[str, Any], term: str) -> list[str]:
    sources: list[str] = []
    for key in ("source_url", "evidence", "source"):
        value = str(candidate.get(key) or "").strip()
        if value and value not in sources:
            sources.append(value)
    if not sources:
        sources.append(f"engineering_precedent_store.lookup:{term}")
    return sources


def _materialized_domain_aspects_from_facets(facets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    aspects: list[dict[str, Any]] = []
    for facet in facets:
        quantities = _parse_domain_quantities(facet.get("quantities")) if "quantities" in facet else None
        if quantities is None:
            quantities = []
        try:
            tables = _parse_domain_tables(facet.get("tables")) if "tables" in facet else None
        except ValueError:
            tables = None
        if tables is None:
            tables = []
        if not quantities and not tables:
            return []
        aspect_name = str(facet.get("aspect_name") or "").strip()
        body = str(facet.get("specification_body") or "").strip()
        sources = facet.get("sources")
        if not aspect_name or not body or not isinstance(sources, list) or not any(str(source).strip() for source in sources):
            return []
        aspects.append(
            {
                "aspect_name": aspect_name,
                "applicability": "applies",
                "specification_body": body,
                "quantities": quantities,
                "tables": tables,
                "sources": [str(source) for source in sources],
            }
        )
    return aspects


def _domain_precedent_lookups(profile_facets: Any, context: Mapping[str, Any] | None = None) -> dict[str, Any]:
    lookups: dict[str, Any] = {}
    reference_context = _precedent_stack_context(context)
    for facet in _domain_profile_facets(profile_facets):
        aspect_name = str(facet.get("aspect_name") or "").strip()
        if not aspect_name:
            continue
        try:
            lookup = engineering_precedent_store.lookup(aspect_name, reference_context, kind="design")
        except Exception as exc:
            lookup = {"status": "failed", "error": _format_prior_art_precedent_error(exc), "candidates": []}
        lookups[aspect_name] = _json_safe(lookup)
    return lookups


def _externalize_domain_specification(
    domain_specification: Mapping[str, Any],
    external_files: dict[str, Any] | None = None,
) -> dict[str, Any]:
    # Memento: hybrid externalization exists because later steps stringify the accumulated
    # tree; tables are first-class receptacles, but large raw tables belong in side files.
    aspects: list[dict[str, Any]] = []
    for aspect in domain_specification.get("aspects", []) if isinstance(domain_specification, Mapping) else []:
        if not isinstance(aspect, Mapping):
            continue
        node = dict(aspect)
        aspect_name = str(node.get("aspect_name") or "")
        aspect_slug = _slug(aspect_name)
        node["id"] = f"domain_specification:{aspect_slug}"
        if node.get("applicability") == "not_applicable":
            node["surplus_quantities_ignored"] = len(node.get("quantities", [])) if isinstance(node.get("quantities"), list) else 0
            node["surplus_tables_ignored"] = len(node.get("tables", [])) if isinstance(node.get("tables"), list) else 0
            node["quantities"] = []
            node["tables"] = []
            aspects.append(node)
            continue
        kept_tables: list[dict[str, Any]] = []
        externalized_tables: list[dict[str, Any]] = []
        for table in node.get("tables", []) if isinstance(node.get("tables"), list) else []:
            if not isinstance(table, Mapping):
                continue
            rows = table.get("rows")
            row_count = len(rows) if isinstance(rows, list) else 0
            if row_count > DOMAIN_SPEC_TABLE_EXTERNALIZE_ROW_THRESHOLD:
                file_ref = f"domain-spec/{aspect_slug}.json"
                externalized_tables.append(
                    {
                        "table_name": str(table.get("table_name") or ""),
                        "columns": list(table.get("columns") or []),
                        "row_count": row_count,
                        "file_ref": file_ref,
                    }
                )
            else:
                kept_tables.append(dict(table))
        if externalized_tables and external_files is not None:
            file_ref = externalized_tables[0]["file_ref"]
            external_files[file_ref] = {
                "aspect_name": aspect_name,
                "aspect_id": node["id"],
                "tables": [
                    dict(table)
                    for table in node.get("tables", [])
                    if isinstance(table, Mapping)
                    and len(table.get("rows", [])) > DOMAIN_SPEC_TABLE_EXTERNALIZE_ROW_THRESHOLD
                ],
            }
        node["tables"] = kept_tables
        if externalized_tables:
            node["externalized_tables"] = externalized_tables
        aspects.append(node)
    return {"aspects": aspects}


def _implementation_strategy_error(reason: str) -> dict[str, Any]:
    return {"ok": False, "error": reason}


def _domain_specification(
    profile_facets: Any,
    reference_lookups: Mapping[str, Any],
    implementation_strategy: Mapping[str, Any],
    constraints: Mapping[str, Any],
    patch_series_view: dict[str, Any],
    context: Mapping[str, Any] | None = None,
    accumulated_approach: Mapping[str, Any] | None = None,
    *,
    log_ctx: org_log.RunContext | None = None,
) -> dict[str, Any]:
    """Form the domain specification receptacle between strategy and patch planning."""
    # Memento: knowledge without a receptacle evaporates into prose; aspect_name is the shared
    # key across profile -> lookup -> block -> ledger -> objection.
    facets = _domain_profile_facets(profile_facets)
    if not facets:
        return {"aspects": []}
    if not all(isinstance(value, Mapping) for value in (reference_lookups, implementation_strategy, constraints)):
        return _domain_specification_error("domain_specification requires profile facets and outputs from steps 2 and 7")
    if not _is_patch_series_view(patch_series_view):
        return _domain_specification_error("domain_specification requires a grounded registry patch series view")
    materialized_aspects = _materialized_domain_aspects_from_facets(facets)
    if materialized_aspects:
        return {"aspects": materialized_aspects}

    repo = _repo_from_context(context)
    feedback: list[str] = []
    last_error = ""
    retry_state = codex_exec.RejectionRetryState()
    for attempt in range(MAX_TECHNICAL_APPROACH_NODE_REGENERATIONS + 1):
        attempt_ctx = log_ctx.child(attempt=attempt + 1) if log_ctx is not None else None
        run = codex_exec.run_json(
            repo,
            schema=build_domain_specification_schema(),
            prompt=_domain_specification_prompt(
                facets,
                reference_lookups,
                implementation_strategy,
                constraints,
                patch_series_view,
                context,
                accumulated_approach,
                feedback if attempt else None,
            ),
            schema_filename="patch_series-domain-specification.schema.json",
            output_filename="patch_series-domain-specification.json",
            failure_label="Codex domain specification",
            ctx=attempt_ctx,
        )
        if not run["ok"]:
            return _domain_specification_error(run["error"])
        parsed = _parse_domain_specification(run["raw"], facets)
        if not parsed.get("ok", True):
            last_error = parsed["error"]
            feedback, permanent = _classify_validation_retry(
                retry_state,
                validator_id="receive.domain_specification",
                errors=last_error,
                attempt=attempt + 1,
                ctx=attempt_ctx,
            )
            if permanent is not None:
                return _domain_specification_error(permanent["error"]) | permanent
            continue
        lint_errors = _lint_domain_specification(parsed)
        if not lint_errors:
            return parsed
        last_error = "; ".join(lint_errors)
        feedback, permanent = _classify_validation_retry(
            retry_state,
            validator_id="receive.domain_specification",
            errors=lint_errors,
            attempt=attempt + 1,
            ctx=attempt_ctx,
        )
        if permanent is not None:
            return _domain_specification_error(permanent["error"]) | permanent

    return _domain_specification_error(
        "Codex domain specification remained invalid after "
        f"{MAX_TECHNICAL_APPROACH_NODE_REGENERATIONS + 1} attempts: {last_error}"
    )


def _domain_specification_error(reason: str) -> dict[str, Any]:
    return {"ok": False, "error": reason}


def _lint_observable_effects(parsed: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    systems = parsed.get("systems")
    if not isinstance(systems, list):
        return errors
    for index, system in enumerate(systems):
        if not isinstance(system, Mapping):
            continue
        effect = str(system.get("observable_effect", "")).strip()
        if not effect:
            errors.append(f"systems[{index}].observable_effect is empty")
            continue
        behavior = str(system.get("observable_behavior", "")).strip()
        if _canonical_phrase(effect) == _canonical_phrase(behavior):
            errors.append(f"systems[{index}].observable_effect must describe user-facing evidence, not repeat observable_behavior")
    return errors


def _parse_right_size_patch_plan(raw: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        return _right_size_patch_plan_error(f"Codex patch plan right-sizing returned invalid JSON: {exc}")

    if not isinstance(parsed, dict):
        return _right_size_patch_plan_error("Codex patch plan right-sizing returned non-object JSON")
    if set(parsed) == {"first_proof_moment", "follow_ups", "deferred", "open_questions"}:
        return _parse_legacy_right_size_patch_plan(parsed)
    if set(parsed) != set(RIGHT_SIZE_PATCH_PLAN_FIELDS):
        return _right_size_patch_plan_error("Codex patch plan right-sizing returned invalid fields")
    raw_items = parsed.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        return _right_size_patch_plan_error("Codex patch plan right-sizing returned invalid items")
    items: list[dict[str, Any]] = []
    item_ids: set[str] = set()
    for item in raw_items:
        if not isinstance(item, dict) or set(item) != set(PATCH_PLAN_WORK_ITEM_FIELDS):
            return _right_size_patch_plan_error("Codex patch plan right-sizing returned invalid work item")
        item_id = item.get("id")
        objective = item.get("objective")
        named_content = _parse_named_content(item.get("named_content"))
        acceptance_criteria = item.get("acceptance_criteria")
        how_verified = item.get("how_verified")
        depends_on = item.get("depends_on")
        if (
            not isinstance(item_id, str)
            or not item_id.strip()
            or item_id in item_ids
            or not isinstance(objective, str)
            or named_content is None
            or not isinstance(acceptance_criteria, list)
            or not all(isinstance(value, str) for value in acceptance_criteria)
            or not isinstance(how_verified, str)
            or not isinstance(depends_on, list)
            or not all(isinstance(value, str) for value in depends_on)
        ):
            return _right_size_patch_plan_error("Codex patch plan right-sizing returned invalid work item values")
        item_ids.add(item_id)
        items.append(
            {
                "id": item_id,
                "objective": objective,
                "named_content": named_content,
                "acceptance_criteria": list(acceptance_criteria),
                "how_verified": how_verified,
                "depends_on": list(depends_on),
            }
        )
    if any(dependency not in item_ids for item in items for dependency in item["depends_on"]):
        return _right_size_patch_plan_error("Codex patch plan right-sizing returned an unresolved dependency reference")
    if any(item["id"] in item["depends_on"] for item in items):
        return _right_size_patch_plan_error("Codex patch plan right-sizing returned a self dependency")

    deferred = parsed.get("deferred")
    if not isinstance(deferred, list):
        return _right_size_patch_plan_error("Codex patch plan right-sizing returned invalid deferred")
    open_questions = parsed.get("open_questions")
    if not isinstance(open_questions, list) or not all(isinstance(item, str) for item in open_questions):
        return _right_size_patch_plan_error("Codex patch plan right-sizing returned invalid open_questions")
    parsed_deferred = _parse_patch_plan_deferred(deferred)
    if parsed_deferred is None:
        return _right_size_patch_plan_error("Codex patch plan right-sizing returned invalid deferred item")
    return {
        "items": items,
        "deferred": parsed_deferred,
        "open_questions": list(open_questions),
    }


def _parse_legacy_right_size_patch_plan(parsed: Mapping[str, Any]) -> dict[str, Any]:
    """Read historical positional plans without advertising them to authors."""
    first_proof_moment = _parse_patch_plan_first_proof_moment(parsed.get("first_proof_moment"))
    if first_proof_moment is None:
        return _right_size_patch_plan_error("Codex patch plan right-sizing returned invalid first_proof_moment")
    follow_ups = parsed.get("follow_ups")
    if not isinstance(follow_ups, list):
        return _right_size_patch_plan_error("Codex patch plan right-sizing returned invalid follow_ups")
    parsed_follow_ups: list[dict[str, Any]] = []
    for item in follow_ups:
        if not isinstance(item, dict) or set(item) != set(PATCH_PLAN_FOLLOW_UP_FIELDS):
            return _right_size_patch_plan_error("Codex patch plan right-sizing returned invalid follow_up")
        named_content = _parse_named_content(item.get("named_content"))
        if named_content is None or not isinstance(item.get("adds"), str):
            return _right_size_patch_plan_error("Codex patch plan right-sizing returned invalid follow_up values")
        parsed_follow_ups.append({"adds": item["adds"], "named_content": named_content})

    deferred = parsed.get("deferred")
    if not isinstance(deferred, list):
        return _right_size_patch_plan_error("Codex patch plan right-sizing returned invalid deferred")
    open_questions = parsed.get("open_questions")
    if not isinstance(open_questions, list) or not all(isinstance(item, str) for item in open_questions):
        return _right_size_patch_plan_error("Codex patch plan right-sizing returned invalid open_questions")

    parsed_deferred = _parse_patch_plan_deferred(deferred)
    if parsed_deferred is None:
        return _right_size_patch_plan_error("Codex patch plan right-sizing returned invalid deferred item")

    return {
        "first_proof_moment": first_proof_moment,
        "follow_ups": parsed_follow_ups,
        "deferred": parsed_deferred,
        "open_questions": list(open_questions),
    }


def _parse_patch_plan_deferred(value: list[Any]) -> list[dict[str, str]] | None:
    parsed: list[dict[str, str]] = []
    for item in value:
        if (
            not isinstance(item, dict)
            or set(item) != set(PATCH_PLAN_DEFERRED_FIELDS)
            or not all(isinstance(item[field], str) for field in PATCH_PLAN_DEFERRED_FIELDS)
        ):
            return None
        parsed.append({"item": item["item"], "why_safe_to_defer": item["why_safe_to_defer"]})
    return parsed


def _parse_patch_plan_first_proof_moment(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict) or set(value) != set(PATCH_PLAN_FIRST_PROOF_FIELDS):
        return None
    user_can = value.get("user_can")
    named_content = _parse_named_content(value.get("named_content"))
    presentation_baseline = value.get("presentation_baseline")
    win_or_progress_condition = value.get("win_or_progress_condition")
    how_verified = value.get("how_verified")
    if not isinstance(user_can, list) or not all(isinstance(item, str) for item in user_can):
        return None
    if (
        named_content is None
        or not isinstance(presentation_baseline, str)
        or not isinstance(win_or_progress_condition, str)
        or not isinstance(how_verified, str)
    ):
        return None
    return {
        "user_can": list(user_can),
        "named_content": named_content,
        "presentation_baseline": presentation_baseline,
        "win_or_progress_condition": win_or_progress_condition,
        "how_verified": how_verified,
    }


def _right_size_patch_plan_error(reason: str) -> dict[str, Any]:
    return {"ok": False, "error": reason}


def _parse_surface_risks(raw: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        return _surface_risks_error(f"Codex risk surfacing returned invalid JSON: {exc}")

    if not isinstance(parsed, dict):
        return _surface_risks_error("Codex risk surfacing returned non-object JSON")
    if set(parsed) != set(SURFACE_RISKS_FIELDS):
        return _surface_risks_error("Codex risk surfacing returned invalid fields")

    risks = parsed.get("risks")
    if not isinstance(risks, list):
        return _surface_risks_error("Codex risk surfacing returned invalid risks")

    parsed_risks: list[dict[str, str]] = []
    for item in risks:
        if not isinstance(item, dict):
            return _surface_risks_error("Codex risk surfacing returned invalid risk item")
        extra_fields = set(item) - set(SURFACED_RISK_FIELDS)
        if not set(SURFACED_RISK_FIELDS) <= set(item) or extra_fields - set(REVISION_DECLARATION_FIELDS):
            return _surface_risks_error("Codex risk surfacing returned invalid risk fields")
        if not all(isinstance(item.get(field, ""), str) for field in list(SURFACED_RISK_FIELDS) + list(REVISION_DECLARATION_FIELDS)):
            return _surface_risks_error("Codex risk surfacing returned invalid risk values")
        if item["attaches_to"] not in RISK_ATTACHES_TO:
            return _surface_risks_error("Codex risk surfacing returned invalid risk attachment")
        risk_entry: dict[str, str] = {
            "id": item["id"],
            "risk": item["risk"],
            "mitigation": item["mitigation"],
            "attaches_to": item["attaches_to"],
            "target_id": item["target_id"],
        }
        # Brief 19: replacement declaration carried only when declared.
        if str(item.get("replaces", "")).strip():
            risk_entry["replaces"] = item["replaces"]
            risk_entry["replacement_reason"] = item.get("replacement_reason", "")
        parsed_risks.append(risk_entry)

    return {"risks": parsed_risks}


def _surface_risks_error(reason: str) -> dict[str, Any]:
    return {"ok": False, "error": reason}




def _deliverable_is_user_facing(patch_series_view: Mapping[str, Any] | None) -> bool:
    # Single source: the registry owns UX applicability vocabulary (brief 34).
    return deliverable_is_user_facing(patch_series_view)


def _deliverable_user_facing_from_context(context: Mapping[str, Any] | None) -> bool:
    return bool((context or {}).get("deliverable_user_facing", True))


def _technical_approach_context(context: Mapping[str, Any] | None, repo: Path) -> dict[str, Any]:
    approach_context = dict(context or {})
    approach_context.setdefault("repo", repo)
    approach_context.setdefault("repo_root", repo)
    approach_context.setdefault("repository_history", _repository_history_context(repo))
    approach_context.setdefault("repository_constitution", _repository_constitution_context(repo))
    return approach_context


def _repository_history_context(repo: Path) -> dict[str, Any]:
    return {
        "label": "design record over time (repository history; commit messages are the repo's design record)",
        "history": git_wrapper.recent_commit_history(repo),
        "provenance_discipline": "History informs background_facts, constraints, and prior art; it must not fabricate requester intent.",
    }


def _repository_constitution_context(repo: Path) -> dict[str, Any]:
    return git_wrapper.repository_constitution_context(repo)


def _technical_approach_step_order(provided_approach: Any | None) -> list[str]:
    middle_steps = (
        ["generate_candidates", "evaluate_candidates", "select_approach"]
        if provided_approach is None
        else ["select_approach"]
    )
    return [
        "normalize_problem",
        "extract_constraints",
        "build_prior_art_map",
        *middle_steps,
        "implementation_strategy",
        "domain_specification",
        "right_size_patch_plan",
        "surface_risks",
    ]


def _provided_approach_from_tech_stack(patch_series_view: Mapping[str, Any]) -> dict[str, Any] | None:
    tech_stack = patch_series_view.get("tech_stack")
    if not isinstance(tech_stack, Mapping):
        return None
    if tech_stack.get("provenance") != "requester_specified":
        return None
    return {
        "source": "tech_stack",
        "tech_stack": {field: tech_stack.get(field, "") for field in TECH_STACK_FIELDS},
        "boundary": "Requester specified this stack; approach formation must depend on it and must not generate alternative engine/framework/platform candidates.",
    }


def _tech_stack_text(tech_stack: Mapping[str, Any]) -> str:
    return " ".join(str(tech_stack.get(field, "")) for field in TECH_STACK_FIELDS).lower()


def _stack_display_name(tech_stack: Mapping[str, Any]) -> str:
    parts = [
        str(tech_stack.get(field, "")).strip()
        for field in ("engine", "framework", "language", "platform")
        if str(tech_stack.get(field, "")).strip()
    ]
    return " / ".join(parts)


def _fill_ai_deliberated_tech_stack(
    patch_series_view: dict[str, Any],
    selected: Mapping[str, Any],
    candidates: Mapping[str, Any] | None = None,
) -> None:
    tech_stack = patch_series_view.get("tech_stack")
    # Memento: "ai_deliberated" is org-owned and must stay RE-derivable — reform
    # re-runs this fill when the decision changes, otherwise the committed cover
    # letter keeps the previous generation's stack and the reviewer deterministically
    # re-objects (live loop: v3e rounds 3->4, decision drifted Phaser->React->Svelte
    # while tech_stack still said Phaser 3, 2026-07-05). Only requester_specified is
    # sovereign and must never be rewritten here.
    if not isinstance(tech_stack, dict) or tech_stack.get("provenance") not in {"unspecified", "ai_deliberated"}:
        return
    rationale = _selected_approach_rationale(selected)
    selected_candidate = _selected_candidate(selected, candidates)
    build_strategy, engine, framework, language, platform = _tech_stack_choice_from_candidate(selected_candidate)
    tech_stack["build_strategy"] = build_strategy
    tech_stack["engine"] = engine
    tech_stack["framework"] = framework
    tech_stack["language"] = language
    tech_stack["platform"] = platform
    tech_stack["provenance"] = "ai_deliberated"
    tech_stack["rationale"] = (
        rationale
        or "No stack was specified by the requester; approach formation selected the build stack after comparing available engine/framework/platform candidates."
    )


def _selected_candidate(selected: Mapping[str, Any], candidates: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    selected_id = selected.get("selected_candidate_id")
    if not isinstance(selected_id, str) or not isinstance(candidates, Mapping):
        return None
    for candidate in candidates.get("candidates", []):
        if isinstance(candidate, Mapping) and candidate.get("id") == selected_id:
            return candidate
    return None


def _tech_stack_choice_from_candidate(candidate: Mapping[str, Any] | None) -> tuple[str, str, str, str, str]:
    default_platform = "browser"
    if not isinstance(candidate, Mapping):
        return ("framework_based", "", "Selected Technical Approach", "text-authored repository code", default_platform)
    requirement = candidate.get("stack_requirement")
    if isinstance(requirement, Mapping):
        return (
            str(requirement.get("build_strategy", "framework_based")),
            str(requirement.get("engine", "")),
            str(requirement.get("framework", "")),
            str(requirement.get("language", "")),
            str(requirement.get("platform", default_platform)),
        )
    text = " ".join(
        str(candidate.get(field, ""))
        for field in ("id", "name", "kind", "summary")
        if isinstance(candidate.get(field, ""), str)
    ).lower()
    if "from scratch" in text or "from_scratch" in text:
        return ("from_scratch", "", "", "text-authored repository code", default_platform)
    name = str(candidate.get("name") or candidate.get("id") or "Selected Technical Approach").strip()
    if "engine" in text or str(candidate.get("kind", "")) == "general_architectural":
        return ("engine_based", name, "", "text-authored repository code", default_platform)
    return ("framework_based", "", name, "text-authored repository code", default_platform)


def _selected_approach_rationale(selected: Mapping[str, Any]) -> str:
    rationale = selected.get("rationale")
    if not isinstance(rationale, Mapping):
        return ""
    parts: list[str] = []
    for key in ("because", "under_constraints", "accepting_tradeoffs"):
        value = rationale.get(key)
        if isinstance(value, list):
            parts.extend(str(item) for item in value if str(item).strip())
    # Memento: silent truncation corrupts artifacts; bounds live in prompts,
    # overflow must be observable.
    return " ".join(parts)


def _next_technical_approach_step(step_order: list[str], completed_step: str) -> str | None:
    try:
        completed_index = step_order.index(completed_step)
    except ValueError:
        return None
    next_index = completed_index + 1
    if next_index >= len(step_order):
        return None
    return step_order[next_index]


def _write_technical_approach_progress(
    progress_path: str | Path,
    partial_tree: Mapping[str, Any],
    steps_completed: list[Mapping[str, Any]],
    current_step: str | None,
) -> None:
    org_log.write_progress_projection(
        progress_path,
        technical_approach=_approach_snapshot(partial_tree),
        steps_completed=steps_completed,
        current_step=current_step,
    )


def _technical_approach_step_failure(step: str, result: Any) -> dict[str, Any] | None:
    if isinstance(result, Mapping) and result.get("ok") is False:
        failure = {
            "ok": False,
            "error": str(result.get("error", f"{step} failed")),
            "failed_step": step,
        }
        for key in ("failure_mode", "rejection_fingerprint", "validator_id", "error_class", "path"):
            if result.get(key):
                failure[key] = result[key]
        # Memento: fix-it hint for typed needs_work errors WITHOUT auto-repair wiring.
        # Matching is exact equality against closed code-emitted error constants in the
        # registry — never prose interpretation. The decision step's evaluation-coverage
        # failure is auto-repaired upstream (_repair_evaluation_matrix_coverage) and is
        # deliberately absent from the registry: never double-hint an auto-repaired
        # failure. Hints are non-binding; the failure stays a typed needs_work either way.
        matched = structure_gadgets.match_gadget_hints({"validator_error": failure["error"]})
        if matched.get("ok") and matched["hints"]:
            failure["gadget_hint"] = matched["hints"][0]
        return failure
    return None


def _provided_approach_selection(provided_approach: Any) -> dict[str, Any]:
    return {
        "selected_candidate_id": "provided_approach",
        "arguments": [
            {
                "role": "support",
                "about_candidate_id": "provided_approach",
                "claim": "Use the requester-provided Technical Approach as the primary design node.",
                "grounds": "The requester supplied an approach to preserve at the patch series boundary.",
                "warrant": "Receive refines and grounds requester intent instead of discarding it.",
                "backing": "The patch series receive procedure treats provided Technical Approach content as the basis.",
                "rebuttal": "Generated alternatives are skipped unless the provided basis is absent.",
            }
        ],
        "rationale": {
            "because": ["Requester intent is preserved as the primary design input."],
            "under_constraints": ["Refinement remains inside ai_org.patchwork_queue and may use the precedent store and repo evidence."],
            "accepting_tradeoffs": ["Generated candidate comparison is not performed for this provided basis."],
        },
        "stack_axes": _requester_specified_stack_axes(provided_approach),
        "rejected": [],
        "open_questions": _string_list(
            provided_approach.get("open_questions") if isinstance(provided_approach, Mapping) else []
        ),
        "requester_approach": _json_safe(provided_approach),
    }


def _requester_specified_stack_axes(provided_approach: Any) -> dict[str, dict[str, str]]:
    tech_stack = {}
    if isinstance(provided_approach, Mapping) and isinstance(provided_approach.get("tech_stack"), Mapping):
        tech_stack = dict(provided_approach["tech_stack"])
    stack_text = _stack_display_name(tech_stack) or "the requester-specified stack"
    return {
        "fidelity_precedent": {
            "evidence": "Requester sovereignty applies before comparing external precedent.",
            "judgment": f"Condition the approach on {stack_text} instead of generating alternatives.",
        },
    }


def _assemble_technical_approach(
    normalized_problem: Mapping[str, Any],
    constraints: Mapping[str, Any],
    selected: Mapping[str, Any],
    candidates: Mapping[str, Any] | None,
    evaluations: Mapping[str, Any] | None,
    prior_art: Mapping[str, Any],
    implementation: Mapping[str, Any],
    domain_specification: Mapping[str, Any],
    patch_plan: Mapping[str, Any],
    risks: Mapping[str, Any],
    source: str,
    *,
    provided_approach: Any | None = None,
    patch_series_view: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    cross_links: list[dict[str, str]] = []
    problem = _problem_root_from_normalized(normalized_problem)
    problem["constraints"] = _constraint_tree_nodes(constraints)
    problem["prior_art"] = _prior_art_tree_nodes(prior_art)
    problem["question"] = _question_tree(
        selected,
        candidates,
        evaluations,
        implementation,
        domain_specification,
        patch_plan,
        risks,
        cross_links,
        provided_approach=provided_approach,
        prior_art_link_targets=_prior_art_link_targets(problem["prior_art"]),
    )
    # Memento: this used to end with a direct assignment of problem.open_questions
    # to an empty list, a one-line silent kill born inside the aa4476e
    # derivation-tree re-architecture and never ratified. Open questions are
    # non-blocking records under the autonomy principle, not terminal
    # confirm-back gates. Do not reintroduce a final reset.
    problem["open_questions"] = _approach_open_questions(normalized_problem, selected, patch_plan)

    _add_cross_link(cross_links, "question:approach", "problem", "derived_from")
    for goal in problem["goals"]:
        _add_cross_link(cross_links, goal["id"], "problem", "satisfies")
    for constraint in problem["constraints"]["hard"] + problem["constraints"]["soft"]:
        _add_cross_link(cross_links, constraint["id"], "problem", "derived_from")
    for pattern in problem["prior_art"]:
        _add_cross_link(cross_links, pattern["id"], "problem", "derived_from")

    return {"problem": problem, "cross_links": cross_links}


def _approach_snapshot(approach: Mapping[str, Any]) -> dict[str, Any]:
    safe = _json_safe(approach)
    return safe if isinstance(safe, dict) else {}


def _approach_open_questions(
    normalized_problem: Mapping[str, Any],
    selected: Mapping[str, Any],
    patch_plan: Mapping[str, Any],
) -> list[str]:
    questions: list[str] = []
    for source in (normalized_problem, selected, patch_plan):
        questions = _append_unique_strings(questions, _string_list(source.get("open_questions")))
    return questions


def _partial_question_tree(
    *,
    candidates: Mapping[str, Any] | None = None,
    evaluations: Mapping[str, Any] | None = None,
    selected: Mapping[str, Any] | None = None,
    implementation: Mapping[str, Any] | None = None,
    domain_specification: Mapping[str, Any] | None = None,
    patch_plan: Mapping[str, Any] | None = None,
    provided_approach: Any | None = None,
) -> dict[str, Any]:
    candidate_items = candidates.get("candidates", []) if isinstance(candidates, Mapping) else []
    if not candidate_items and provided_approach is not None:
        candidate_items = [
            {
                "id": "provided_approach",
                "name": "Requester-provided Technical Approach",
                "kind": "repo_native",
                "summary": "Preserve and refine the requester-provided Technical Approach.",
                "first_proof_moment": {
                    "user_actions": ["Follow the requester-provided approach through implementation planning."],
                    "named_content": [
                        {"name": "Requester-provided scope", "kind": "scope"},
                        {"name": "Requester-provided conflicts", "kind": "constraint"},
                        {"name": "Requester-provided mechanisms", "kind": "mechanism"},
                    ],
                    "win_or_progress_condition": "The provided approach is grounded into implementation and patch-plan nodes.",
                },
                "core_systems": ["patch series receive Technical Approach refinement"],
                "draws_on": ["Requester-provided Technical Approach"],
            }
        ]

    evaluation_items = evaluations.get("evaluations", []) if isinstance(evaluations, Mapping) else []
    evaluation_map = {
        item["candidate_id"]: item
        for item in evaluation_items
        if isinstance(item, Mapping) and isinstance(item.get("candidate_id"), str)
    }
    argument_map: dict[str, list[dict[str, str]]] = {}
    if isinstance(selected, Mapping):
        for argument in selected.get("arguments", []):
            if isinstance(argument, Mapping) and isinstance(argument.get("about_candidate_id"), str):
                argument_map.setdefault(argument["about_candidate_id"], []).append(dict(argument))

    candidate_nodes: list[dict[str, Any]] = []
    for candidate in candidate_items:
        if not isinstance(candidate, Mapping):
            continue
        candidate_id = str(candidate["id"])
        node = dict(candidate)
        if candidate_id in evaluation_map:
            evaluation = dict(evaluation_map[candidate_id])
            evaluation["id"] = f"evaluation:{candidate_id}"
            evaluation["arguments"] = argument_map.get(candidate_id, [])
            node["evaluation"] = evaluation
        candidate_nodes.append(node)

    decision: dict[str, Any] = {}
    if isinstance(selected, Mapping) and isinstance(selected.get("selected_candidate_id"), str):
        selected_id = str(selected["selected_candidate_id"])
        decision = {
            "id": f"decision:{selected_id}",
            "selected_candidate_id": selected_id,
            "arguments": [dict(item) for item in selected.get("arguments", []) if isinstance(item, Mapping)],
            "rationale": dict(selected.get("rationale", {})) if isinstance(selected.get("rationale"), Mapping) else {},
            "stack_axes": dict(selected.get("stack_axes", {})) if isinstance(selected.get("stack_axes"), Mapping) else {},
            "rejected": [dict(item) for item in selected.get("rejected", []) if isinstance(item, Mapping)],
        }
        if isinstance(implementation, Mapping):
            implementation_node = dict(implementation)
            implementation_node["id"] = f"implementation:{selected_id}"
            if isinstance(domain_specification, Mapping):
                implementation_node["domain_specification"] = _domain_specification_tree_node(domain_specification)
            if isinstance(patch_plan, Mapping):
                implementation_node["patch_plan"] = _node_with_id(patch_plan, f"patch_plan:{selected_id}")
            decision["implementation"] = implementation_node

    return {
        "id": "question:approach",
        "text": "Which implementation approach best satisfies the problem under the derived constraints?",
        "candidates": candidate_nodes,
        "decision": decision,
    }


CROSS_LINK_TYPES = (
    "supports",
    "objects_to",
    "satisfies",
    "violates",
    "depends_on",
    "mitigates",
    "implements",
    "tests",
    "derived_from",
    "related_to",
)


def _problem_root_from_normalized(normalized_problem: Mapping[str, Any]) -> dict[str, Any]:
    goals: list[dict[str, Any]] = []
    for index, goal in enumerate(normalized_problem.get("success_criteria", []), start=1):
        if isinstance(goal, Mapping):
            goal_node = dict(goal)
            goal_node["id"] = f"goal:{index}"
            goals.append(goal_node)
    return {
        "id": "problem",
        "problem": normalized_problem.get("problem", ""),
        "affected": normalized_problem.get("affected", ""),
        "current_inadequacy": normalized_problem.get("current_inadequacy", ""),
        "goals": goals,
        "non_goals": list(normalized_problem.get("non_goals", [])),
        "constraints": {"hard": [], "soft": []},
        "prior_art": [],
        "question": {"id": "question:approach", "text": "", "candidates": [], "decision": {}},
        "open_questions": _string_list(normalized_problem.get("open_questions")),
    }


def _constraint_tree_nodes(constraints: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    return {
        "hard": [
            _node_with_id(item, f"constraint:hard:{index}")
            for index, item in enumerate(constraints.get("hard_constraints", []), start=1)
            if isinstance(item, Mapping)
        ],
        "soft": [
            _node_with_id(item, f"constraint:soft:{index}")
            for index, item in enumerate(constraints.get("soft_preferences", []), start=1)
            if isinstance(item, Mapping)
        ],
    }


def _prior_art_tree_nodes(prior_art: Mapping[str, Any]) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    for index, pattern in enumerate(prior_art.get("patterns", []), start=1):
        if not isinstance(pattern, Mapping):
            continue
        name = pattern.get("name")
        node_id = f"prior_art:{_slug(str(name)) or index}"
        nodes.append(_node_with_id(pattern, node_id))
    return nodes


def _prior_art_link_targets(prior_art_nodes: Any) -> set[str]:
    """Resolvable draws_on link targets: current prior_art ids + declared lineage.

    Lineage = replaces / reconciled_replaces predecessors carried tree-sticky on
    the current prior_art nodes: a link to such an id is real (some generation
    held the node) and the root remap rewrites it to the successor.
    """
    targets: set[str] = set()
    for node in prior_art_nodes or []:
        if not isinstance(node, Mapping):
            continue
        node_id = node.get("id")
        if isinstance(node_id, str) and node_id:
            targets.add(node_id)
        replaced = node.get("replaces")
        if isinstance(replaced, str) and replaced.strip():
            targets.add(replaced)
        reconciled = node.get("reconciled_replaces")
        if isinstance(reconciled, list):
            for item in reconciled:
                if isinstance(item, Mapping) and isinstance(item.get("replaced_node_id"), str) and item["replaced_node_id"].strip():
                    targets.add(item["replaced_node_id"])
    return targets


def _question_tree(
    selected: Mapping[str, Any],
    candidates: Mapping[str, Any] | None,
    evaluations: Mapping[str, Any] | None,
    implementation: Mapping[str, Any],
    domain_specification: Mapping[str, Any],
    patch_plan: Mapping[str, Any],
    risks: Mapping[str, Any],
    cross_links: list[dict[str, str]],
    *,
    provided_approach: Any | None = None,
    prior_art_link_targets: set[str] | None = None,
) -> dict[str, Any]:
    candidate_nodes = _candidate_tree_nodes(
        candidates, evaluations, selected, risks, cross_links, provided_approach,
        prior_art_link_targets=prior_art_link_targets,
    )
    decision = _decision_tree_node(selected, implementation, domain_specification, patch_plan, risks, cross_links)
    return {
        "id": "question:approach",
        "text": "Which implementation approach best satisfies the problem under the derived constraints?",
        "candidates": candidate_nodes,
        "decision": decision,
    }


def _candidate_tree_nodes(
    candidates: Mapping[str, Any] | None,
    evaluations: Mapping[str, Any] | None,
    selected: Mapping[str, Any],
    risks: Mapping[str, Any],
    cross_links: list[dict[str, str]],
    provided_approach: Any | None,
    *,
    prior_art_link_targets: set[str] | None = None,
) -> list[dict[str, Any]]:
    candidate_items = candidates.get("candidates", []) if isinstance(candidates, Mapping) else []
    if not candidate_items and provided_approach is not None:
        candidate_items = [
            {
                "id": "provided_approach",
                "name": "Requester-provided Technical Approach",
                "kind": "repo_native",
                "summary": "Preserve and refine the requester-provided Technical Approach.",
                "first_proof_moment": {
                    "user_actions": ["Follow the requester-provided approach through implementation planning."],
                    "named_content": [
                        {"name": "Requester-provided scope", "kind": "scope"},
                        {"name": "Requester-provided conflicts", "kind": "constraint"},
                        {"name": "Requester-provided mechanisms", "kind": "mechanism"},
                    ],
                    "win_or_progress_condition": "The provided approach is grounded into implementation and patch-plan nodes.",
                },
                "core_systems": ["patch series receive Technical Approach refinement"],
                "draws_on": ["Requester-provided Technical Approach"],
            }
        ]
    evaluation_items = evaluations.get("evaluations", []) if isinstance(evaluations, Mapping) else []
    evaluation_map = {
        item["candidate_id"]: item
        for item in evaluation_items
        if isinstance(item, Mapping) and isinstance(item.get("candidate_id"), str)
    }
    argument_map: dict[str, list[dict[str, str]]] = {}
    for argument in selected.get("arguments", []):
        if isinstance(argument, Mapping) and isinstance(argument.get("about_candidate_id"), str):
            argument_map.setdefault(argument["about_candidate_id"], []).append(dict(argument))

    nodes: list[dict[str, Any]] = []
    prior_art_links: dict[str, str] = {}
    for candidate in candidate_items:
        if not isinstance(candidate, Mapping):
            continue
        candidate_id = str(candidate["id"])
        node = dict(candidate)
        evaluation = dict(evaluation_map.get(candidate_id, {"candidate_id": candidate_id, "scores": {}}))
        evaluation["id"] = f"evaluation:{candidate_id}"
        evaluation["arguments"] = argument_map.get(candidate_id, [])
        node["evaluation"] = evaluation
        node["risks"] = _risks_for("candidate", candidate_id, risks)
        nodes.append(node)
        _add_cross_link(cross_links, candidate_id, "question:approach", "depends_on")
        _add_cross_link(cross_links, evaluation["id"], candidate_id, "derived_from")
        for argument in evaluation["arguments"]:
            link_type = "supports" if argument.get("role") == "support" else "objects_to"
            _add_cross_link(cross_links, f"argument:{candidate_id}:{len(cross_links)}", candidate_id, link_type)
        for draw in candidate.get("draws_on", []):
            if isinstance(draw, str):
                # Memento (brief 24): a draws_on entry is a REFERENCE only when its
                # slug resolves to a current prior_art node or to replacement-map
                # lineage (a declared/reconciled predecessor id the remap can carry
                # forward). Anything else is prose provenance — slugifying it here
                # FABRICATED references to nodes that never existed in any
                # generation (live: 6 fabricated prior_art ids from notes like
                # "Repository inspection: HEAD has no tracked files...", pomodoro,
                # 2026-07-05) and, with the continuity gate fail-closed, wedged the
                # series on ids the reconciliation round could only answer
                # no_successor for. Refusing to fabricate is NOT dropping a real
                # reference: the draws_on prose stays verbatim on the candidate;
                # only the link is withheld, and the revision delta ledger records
                # the reclassification (prose_provenance_not_linked).
                prior_id = prior_art_links.setdefault(draw, f"prior_art:{_slug(draw)}")
                if prior_id in (prior_art_link_targets or set()):
                    _add_cross_link(cross_links, candidate_id, prior_id, "derived_from")
        for risk in node["risks"]:
            _add_cross_link(cross_links, risk["id"], candidate_id, "mitigates")
    return nodes


def _decision_tree_node(
    selected: Mapping[str, Any],
    implementation: Mapping[str, Any],
    domain_specification: Mapping[str, Any],
    patch_plan: Mapping[str, Any],
    risks: Mapping[str, Any],
    cross_links: list[dict[str, str]],
) -> dict[str, Any]:
    selected_id = str(selected["selected_candidate_id"])
    decision_id = f"decision:{selected_id}"
    implementation_id = f"implementation:{selected_id}"
    patch_plan_id = f"patch_plan:{selected_id}"
    implementation_node = dict(implementation)
    implementation_node["id"] = implementation_id
    implementation_node["domain_specification"] = _domain_specification_tree_node(domain_specification)
    patch_plan_node = _node_with_id(patch_plan, patch_plan_id)
    patch_plan_node["risks"] = _risks_for("patch_plan", patch_plan_id, risks)
    implementation_node["patch_plan"] = patch_plan_node
    implementation_node["risks"] = _risks_for("implementation", implementation_id, risks)
    decision_node = {
        "id": decision_id,
        "selected_candidate_id": selected_id,
        "arguments": [dict(item) for item in selected.get("arguments", [])],
        "rationale": {
            "because": list(selected.get("rationale", {}).get("because", [])),
            "under_constraints": list(selected.get("rationale", {}).get("under_constraints", [])),
            "accepting_tradeoffs": list(selected.get("rationale", {}).get("accepting_tradeoffs", [])),
        },
        "stack_axes": dict(selected.get("stack_axes", {})) if isinstance(selected.get("stack_axes"), Mapping) else {},
        "rejected": [dict(item) for item in selected.get("rejected", [])],
        "risks": _risks_for("decision", decision_id, risks),
        "implementation": implementation_node,
    }
    _add_cross_link(cross_links, decision_id, "question:approach", "derived_from")
    _add_cross_link(cross_links, decision_id, selected_id, "supports")
    _add_cross_link(cross_links, implementation_id, decision_id, "implements")
    for aspect in implementation_node["domain_specification"].get("aspects", []):
        if isinstance(aspect, Mapping) and isinstance(aspect.get("id"), str):
            _add_cross_link(cross_links, aspect["id"], implementation_id, "derived_from")
    _add_cross_link(cross_links, patch_plan_id, implementation_id, "implements")
    for rejection in decision_node["rejected"]:
        _add_cross_link(cross_links, decision_id, rejection["candidate_id"], "objects_to")
    for risk in decision_node["risks"]:
        _add_cross_link(cross_links, risk["id"], decision_id, "mitigates")
    for risk in implementation_node["risks"]:
        _add_cross_link(cross_links, risk["id"], implementation_id, "mitigates")
    for risk in patch_plan_node["risks"]:
        _add_cross_link(cross_links, risk["id"], patch_plan_id, "mitigates")
    return decision_node


def _risks_for(attachment: str, target_id: str, risks: Mapping[str, Any]) -> list[dict[str, str]]:
    risk_items = risks.get("risks", []) if isinstance(risks, Mapping) else []
    return [
        dict(risk)
        for risk in risk_items
        if isinstance(risk, Mapping) and risk.get("attaches_to") == attachment and risk.get("target_id") == target_id
    ]


def _node_with_id(node: Mapping[str, Any], node_id: str) -> dict[str, Any]:
    copied = dict(node)
    copied["id"] = node_id
    return copied


def _domain_specification_tree_node(domain_specification: Mapping[str, Any]) -> dict[str, Any]:
    aspects: list[dict[str, Any]] = []
    for aspect in domain_specification.get("aspects", []) if isinstance(domain_specification, Mapping) else []:
        if not isinstance(aspect, Mapping):
            continue
        node = dict(aspect)
        aspect_name = str(node.get("aspect_name") or "")
        node.setdefault("id", f"domain_specification:{_slug(aspect_name)}")
        aspects.append(node)
    return {"id": "domain_specification", "aspects": aspects}


def _add_cross_link(cross_links: list[dict[str, str]], from_id: str, to_id: str, link_type: str) -> None:
    if link_type not in CROSS_LINK_TYPES:
        raise ValueError(f"invalid cross_link type: {link_type}")
    link = {"from": from_id, "to": to_id, "type": link_type}
    if link not in cross_links:
        cross_links.append(link)


def _surface_risk_target_ids(
    selected: Mapping[str, Any],
    accumulated_approach: Mapping[str, Any] | None,
) -> list[str]:
    target_ids: set[str] = set()
    if isinstance(accumulated_approach, Mapping):
        problem = accumulated_approach.get("problem")
        question = problem.get("question") if isinstance(problem, Mapping) else None
        if isinstance(question, Mapping):
            for candidate in question.get("candidates", []) if isinstance(question.get("candidates"), list) else []:
                if isinstance(candidate, Mapping) and isinstance(candidate.get("id"), str):
                    target_ids.add(candidate["id"])
            decision = question.get("decision")
            if isinstance(decision, Mapping):
                if isinstance(decision.get("id"), str):
                    target_ids.add(decision["id"])
                implementation = decision.get("implementation")
                if isinstance(implementation, Mapping):
                    if isinstance(implementation.get("id"), str):
                        target_ids.add(implementation["id"])
                    patch_plan = implementation.get("patch_plan")
                    if isinstance(patch_plan, Mapping) and isinstance(patch_plan.get("id"), str):
                        target_ids.add(patch_plan["id"])

    selected_id = str(selected.get("selected_candidate_id") or "").strip()
    if selected_id:
        target_ids.update(
            {
                selected_id,
                f"decision:{selected_id}",
                f"implementation:{selected_id}",
                f"patch_plan:{selected_id}",
            }
        )
    return _known_precedent_ids(target_ids)


def _validate_risk_targets(
    risks: Mapping[str, Any],
    candidates: Mapping[str, Any] | None,
    selected: Mapping[str, Any],
) -> str | None:
    candidate_ids = set(_candidate_ids(candidates)) if isinstance(candidates, Mapping) else {"provided_approach"}
    selected_id = str(selected.get("selected_candidate_id", ""))
    valid_targets = {
        "candidate": candidate_ids,
        "decision": {f"decision:{selected_id}"},
        "implementation": {f"implementation:{selected_id}"},
        "patch_plan": {f"patch_plan:{selected_id}"},
    }
    for risk in risks.get("risks", []) if isinstance(risks, Mapping) else []:
        if not isinstance(risk, Mapping):
            continue
        attachment = risk.get("attaches_to")
        target_id = risk.get("target_id")
        if not isinstance(attachment, str) or not isinstance(target_id, str):
            return "risk target is invalid"
        if target_id not in valid_targets.get(attachment, set()):
            return f"risk {risk.get('id', '')} targets unknown {attachment} node {target_id}"
    return None


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple | set):
        return [_json_safe(item) for item in value]
    try:
        json.dumps(value, ensure_ascii=True)
    except (TypeError, ValueError):
        return str(value)
    return value


def _repo_from_context(context: Mapping[str, Any] | None = None) -> Path:
    if isinstance(context, Mapping):
        repo = context.get("repo_root") or context.get("repo")
        if repo:
            return Path(str(repo)).resolve()
    return Path.cwd().resolve()


def _parse_grounding_result(raw: str, original_patch_series_view: dict[str, Any]) -> GroundingResult:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return _grounding_fail_closed(original_patch_series_view, f"Grounding returned invalid JSON: {raw}")

    if not isinstance(parsed, dict):
        return _grounding_fail_closed(original_patch_series_view, f"Grounding returned non-object JSON: {raw}")

    confident = parsed.get("confident")
    proposed_patch_series = _with_working_title_fallback(parsed.get("proposed_patch_series"), original_patch_series_view)
    ascii_working_slug = parsed.get("ascii_working_slug")
    assumptions = parsed.get("assumptions")
    grounding_notes = parsed.get("grounding_notes")
    questions = parsed.get("questions")
    if not isinstance(confident, bool):
        return _grounding_fail_closed(original_patch_series_view, f"Grounding returned invalid confident: {raw}")
    view_violations = _grounding_candidate_view_violations(proposed_patch_series)
    if view_violations:
        # The failure string codex sees on retry carries the failing component and
        # the violated rule — never the raw dump plus downstream scaffold symptoms.
        return _grounding_fail_closed(
            original_patch_series_view,
            "Grounding returned invalid proposed_patch_series: " + "; ".join(view_violations),
        )
    if not isinstance(assumptions, list) or not all(isinstance(assumption, str) for assumption in assumptions):
        return _grounding_fail_closed(original_patch_series_view, f"Grounding returned invalid assumptions: {raw}")
    if not isinstance(grounding_notes, str) or len(grounding_notes) > 2000:
        return _grounding_fail_closed(original_patch_series_view, f"Grounding returned invalid grounding_notes: {raw}")
    if not isinstance(questions, list) or not all(isinstance(question, str) for question in questions):
        return _grounding_fail_closed(original_patch_series_view, f"Grounding returned invalid questions: {raw}")
    if ascii_working_slug is not None and not _valid_ascii_working_slug(ascii_working_slug):
        return _grounding_fail_closed(original_patch_series_view, f"Grounding returned invalid ascii_working_slug: {raw}")
    return GroundingResult(
        _patch_series_to_view(proposed_patch_series),
        grounding_notes,
        confident,
        assumptions,
        questions,
        ascii_working_slug=_valid_ascii_working_slug(ascii_working_slug),
    )


def _with_working_title_fallback(value: Any, original_patch_series_view: dict[str, Any]) -> Any:
    if not isinstance(value, dict):
        return value
    title = value.get("working_title")
    if not isinstance(title, str) or title.strip():
        return value

    repaired = dict(value)
    repaired["working_title"] = _derive_working_title(repaired, original_patch_series_view)
    return repaired


def _is_grounding_candidate_view(value: object) -> bool:
    return not _grounding_candidate_view_violations(value)


def _grounding_candidate_view_violations(value: object) -> list[str]:
    """Explain WHICH component of a proposed patch series view fails, first failure only.

    Memento: a bare-boolean rejection converts model self-correction into
    permanent_rejection — the retry contract requires the smallest actionable
    invariant (the same law as codex_exec's fingerprinting Memento). Pomodoro run,
    2026-07-05: a complete, honest grounding failed ONLY validate_tech_stack
    (surface-only requester pin), the bare False dumped the raw output and fell
    back to the empty scaffold, C0 then listed the scaffold's empty strings — the
    SYMPTOM — and codex dutifully "fixed" the strings while tech_stack stayed
    wrong: identical fingerprint, permanent_rejection. Honest component+rule
    feedback would have let attempt 2 pass. Components are checked in the same
    order the old boolean chain short-circuited (key set, string fields, string
    arrays, tech_stack, UX), so validation behavior and side effects are
    byte-identical; only the rejection's information content improved.
    """
    if not isinstance(value, dict):
        return ["proposed_patch_series must be a JSON object"]
    if set(value) != set(WORK_ORDER_VIEW_FIELDS):
        missing = sorted(set(WORK_ORDER_VIEW_FIELDS) - set(value))
        extra = sorted(set(value) - set(WORK_ORDER_VIEW_FIELDS))
        parts = []
        if missing:
            parts.append("missing fields: " + ", ".join(missing))
        if extra:
            parts.append("unexpected fields: " + ", ".join(extra))
        return ["proposed_patch_series field set mismatch: " + "; ".join(parts)]
    wrong_strings = [field for field in STRING_FIELDS if not isinstance(value[field], str)]
    if wrong_strings:
        return [
            "proposed_patch_series string fields must be strings: "
            + ", ".join(f"{field} (got {type(value[field]).__name__})" for field in wrong_strings)
        ]
    wrong_arrays = [
        field
        for field in STRING_ARRAY_FIELDS
        if not (isinstance(value[field], list) and all(isinstance(item, str) for item in value[field]))
    ]
    if wrong_arrays:
        return ["proposed_patch_series string-array fields must be arrays of strings: " + ", ".join(wrong_arrays)]
    tech_stack_violation = explain_tech_stack_violation(
        value.get("tech_stack"), user_facing=deliverable_is_user_facing(value)
    )
    if tech_stack_violation:
        return ["proposed_patch_series.tech_stack invalid: " + tech_stack_violation]
    if not validate_user_experience_requirements(
        value.get("user_experience_requirements"),
        require_completeness=False,
    ):
        return [
            "proposed_patch_series.user_experience_requirements does not satisfy the UX contract "
            "(applicability object plus the experience section shape from the request schema)"
        ]
    return []


def _derive_working_title(patch_series_view: Mapping[str, Any], original_patch_series_view: Mapping[str, Any]) -> str:
    raw_request = str(patch_series_view.get("raw_request") or original_patch_series_view.get("raw_request") or "")
    candidates = [
        patch_series_view.get("problem_or_motivation"),
        _named_subject_from_request(raw_request),
        raw_request,
    ]
    for candidate in candidates:
        title = _working_title_phrase(str(candidate or ""))
        if title:
            return title
    return "Grounded patch series"


def _named_subject_from_request(raw_request: str) -> str:
    pattern = r"\b(?:" + "|".join(_WORKING_TITLE_VERBS) + r")\s+(?:a|an|the)?\s*([^.\n:;]+)"
    match = re.search(pattern, raw_request, flags=re.IGNORECASE)
    if match:
        return match.group(1)
    return ""


def _working_title_phrase(text: str) -> str:
    words = [
        word.strip("'")
        for word in re.findall(r"[A-Za-z0-9][A-Za-z0-9_'-]*", text.replace("_", " "))
    ]
    selected: list[str] = []
    for word in words:
        normalized = word.lower().strip("-'")
        if not normalized or normalized in _WORKING_TITLE_STOPWORDS:
            continue
        selected.append(_working_title_word(normalized))
        if len(selected) == 6:
            break
    return " ".join(selected)


def _working_title_word(word: str) -> str:
    if word in _WORKING_TITLE_ABBREVIATIONS:
        return word.upper()
    if any(char.isdigit() for char in word):
        return word.upper()
    return word.capitalize()


def _verify_grounding(
    request: dict[str, Any],
    patch_series_view: dict[str, Any],
    grounding_result: GroundingResult,
    *,
    ctx: org_log.RunContext | None = None,
) -> dict[str, Any]:
    # Memento: when grounding output never became a candidate view, patch_series_view
    # here is the fail-closed FALLBACK scaffold — not the model's output. Linting the
    # scaffold reports symptoms of the fallback (its empty required strings), and
    # retry feedback built from those symptoms sends the model chasing the wrong
    # fields with an unchanged fingerprint. Live case (pomodoro CLI run, 2026-07-05):
    # the real defect was one tech_stack rule; C0 symptom feedback made codex "fill
    # required handoff strings", tech_stack stayed wrong, same fingerprint ->
    # permanent_rejection. Same law as codex_exec's fingerprinting Memento: feed
    # back the smallest actionable invariant — here, the parse failure itself.
    if grounding_result.parse_failure:
        result = {
            "ok": False,
            "violations": [grounding_result.parse_failure],
        }
        if grounding_result.failure_mode:
            result["failure_mode"] = grounding_result.failure_mode
        return result
    violations = _lint_grounding(request, patch_series_view, grounding_result)
    verdict = _run_grounding_verifier(request, patch_series_view, grounding_result, ctx=ctx)
    failure_mode = str(verdict.get("failure_mode") or "")

    if not verdict.get("ok", False):
        violations.append(str(verdict.get("violation", "C0 verifier: verifier failed closed")))
    else:
        semantic = verdict["verdict"]
        semantic_checks = (
            ("faithful_specific", "C1 faithfulness/specificity"),
            ("full_scope", "C2 full scope"),
            ("non_legal", "C3 non-legal"),
            ("latest_default", "C4 latest-default"),
        )
        reasons = "; ".join(semantic.get("reasons", []))
        for field, label in semantic_checks:
            if semantic.get(field) is not True:
                detail = f": {reasons}" if reasons else ""
                violations.append(f"{label}: verifier marked {field}=false{detail}")

    violations = _dedupe(violations)
    result = {"ok": not violations, "violations": violations}
    if failure_mode:
        result["failure_mode"] = failure_mode
    return result


def _lint_grounding(
    request: dict[str, Any],
    patch_series_view: dict[str, Any],
    grounding_result: GroundingResult,
) -> list[str]:
    text = _grounding_text(patch_series_view, grounding_result)
    violations: list[str] = []

    empty_required_fields = [
        field
        for field in WORK_ORDER_HANDOFF_REQUIRED_FIELDS
        if field in STRING_FIELDS and isinstance(patch_series_view.get(field), str) and not patch_series_view[field].strip()
    ]
    if empty_required_fields:
        violations.append(
            "C0 required-field completeness lint: patch_series_handoff string fields must be non-empty: "
            + ", ".join(empty_required_fields)
        )

    # Memento, requester 2026-07-03: the org's own redesign request died at its own
    # gate because C1/C2 read meaning from marker strings. All four flagged
    # markers were legitimate vocabulary: failures should NOT be generic,
    # "minimal touchpoints" boundary wording, "patch-series-style" workflow
    # vocabulary, and "Gerrit-style" inside must_not_resemble. Across 278
    # archived runs, the marker lints had zero genuine catches and two false
    # positive incidents. This was the fourth recorded case of the
    # read-meaning-from-strings disease; do not reintroduce marker lints.
    #
    # ALT 1, ADOPTED: faithfulness and full-scope are semantic judgments and live
    # in the verifier layer, fail-closed. precedent facets:
    # "mechanical versus judgment completeness checks" says deterministic gates
    # check existence/shape/links while humans or semantic reviewers judge
    # adequacy; "deterministic validator around stochastic generator" says
    # natural-language meaning is not a fatal CI predicate until represented in a
    # machine-checkable form.
    #
    # ALT 2, DEFERRED, not rejected: structured contract_trace, where grounding
    # self-declares specific_identity_relation / scope_relation enums plus JSON
    # pointers; the mechanical layer validates enums/paths and fails on
    # self-admitted downgrade, while the verifier judges the declaration's truth.
    # WAKE CONDITION: adopt Alt 2 if a hidden downgrade is ever OBSERVED slipping
    # past the Alt 1 verifier in a live run. Do not build it preemptively;
    # substrate razor: no machinery ahead of an observed need.
    #
    # ALT 3, REJECTED: contextual/polarity-aware marker regexes. That is
    # exception-accreting regex judgment and repeats the prohibition-accretion
    # failure mode.

    legal_hits = _keyword_hit_count(text, LEGAL_KEYWORDS)
    word_count = max(1, len(re.findall(r"\w+", text)))
    if legal_hits >= 3 and legal_hits / word_count >= 0.015:
        violations.append("C3 non-legal lint: legal/IP/trademark/copyright language dominates grounding output")

    # Memento: deterministic gates stay only where they are sound. Datedness is
    # semantic and unbounded, so C4 belongs to the verifier. Never bake
    # test-subject tokens into this generic mechanism.

    violations.extend(_lint_grounding_tech_stack_provenance(request, patch_series_view))
    violations.extend(_lint_grounding_user_experience_requirements(patch_series_view))

    return violations


def _lint_grounding_user_experience_requirements(patch_series_view: Mapping[str, Any]) -> list[str]:
    ux = patch_series_view.get("user_experience_requirements")
    if not isinstance(ux, Mapping):
        return ["C0 user-experience completeness lint: user_experience_requirements is not a structured object"]
    if not validate_user_experience_requirements(ux):
        return [
            "C0 user-experience completeness lint: user_facing deliverables require non-empty UX sections, "
            "action feedback rows, and screenshot/interaction/playtest checks; not_user_facing deliverables require a reason"
        ]
    return []


def _lint_grounding_tech_stack_provenance(request: Mapping[str, Any], patch_series_view: Mapping[str, Any]) -> list[str]:
    tech_stack = patch_series_view.get("tech_stack")
    if not isinstance(tech_stack, Mapping):
        return ["C5 tech-stack provenance lint: tech_stack is not a structured object"]
    provenance = tech_stack.get("provenance")
    if provenance == "ai_deliberated":
        return [
            "C5 tech-stack provenance lint: grounding may not set provenance=ai_deliberated; only form_technical_approach may deliberate the stack"
        ]
    if provenance == "requester_specified" and not _original_request_names_stack(request, tech_stack):
        return [
            "C5 tech-stack provenance lint: grounding claimed requester_specified, but the original raw_request/proposal_hint did not name that stack"
        ]
    if provenance not in {"requester_specified", "unspecified"}:
        return [f"C5 tech-stack provenance lint: grounding returned invalid provenance {provenance!r}"]
    return []


def _original_request_names_stack(request: Mapping[str, Any], tech_stack: Mapping[str, Any]) -> bool:
    source_text = " ".join(
        str(request.get(field, ""))
        for field in ("raw_request", "proposal_hint")
        if isinstance(request.get(field, ""), str)
    ).lower()
    if not source_text.strip():
        return False
    for phrase in _stack_name_phrases(tech_stack):
        if phrase and phrase in source_text:
            return True
    return False


def _stack_name_phrases(tech_stack: Mapping[str, Any]) -> list[str]:
    phrases: list[str] = []
    for field in ("engine", "framework", "language", "platform"):
        value = str(tech_stack.get(field, "")).strip().lower()
        if not value:
            continue
        phrases.append(value)
        pieces = [piece for piece in re.split(r"[^a-z0-9+#.]+", value) if len(piece) >= 2]
        phrases.extend(pieces)
    aliases = {
        "unreal engine 5": ["unreal", "ue5"],
        "unreal engine": ["unreal", "ue"],
        "react app": ["react"],
        "react": ["react"],
        "godot": ["godot"],
        "unity": ["unity"],
    }
    expanded: list[str] = []
    for phrase in phrases:
        expanded.append(phrase)
        expanded.extend(aliases.get(phrase, []))
    return _dedupe(expanded)


def _run_grounding_verifier(
    request: dict[str, Any],
    patch_series_view: dict[str, Any],
    grounding_result: GroundingResult,
    *,
    ctx: org_log.RunContext | None = None,
) -> dict[str, Any]:
    prompt = (
        "You are an adversarial read-only verifier for a patch series grounding contract.\n"
        "Given the original request and the grounded patch series view, judge these four checks. C1 and C2 are semantic "
        "meaning checks, not substring checks; natural-language marker words are evidence only when the grounded "
        "meaning actually downgrades the request.\n"
        "C1 faithful_specific: return false only when a named specific target is watered down to a broad category, "
        "renamed as an original X-style work, or otherwise loses its requested identity. A real failure is "
        "Dragon Quest grounded as 'a generic RPG-style game inspired by Dragon Quest'.\n"
        "C2 full_scope: return false only when the requested complete/full deliverable is shrunk without requester "
        "instruction to a slice, demo, MVP, prototype, first area, one town, or first iteration. A real failure is "
        "a complete-game request grounded as 'minimal MVP vertical slice / one town demo'.\n"
        "C3 non_legal: IP, trademark, copyright, licensing, or legal risk content does not dominate the output.\n"
        "C4 latest_default: using the Original request registry view below as the authority for requester intent, "
        "return true when the request explicitly asked for retro, old, classic, vintage, or a specific past "
        "version/year; otherwise return true only when the patch series targets the latest/current version, conventions, "
        "and best practices of the named thing.\n"
        "Do not treat domain vocabulary, negative comparisons, must_not_resemble examples, prohibitions such as "
        "'failures must not be generic', or boundary wording about unrelated modules and minimal touchpoints as "
        "C1/C2 violations. Be strict about actual semantic downgrades. Return false for any check that is "
        "semantically violated, even if wording tries to hide it.\n\n"
        + _format_patch_series("Original request registry view", _patch_series_to_view(request))
        + "\n"
        + _format_patch_series("Grounded registry patch series view", _patch_series_to_view(patch_series_view))
        + f"\ngrounding_notes: {grounding_result.grounding_notes}\n"
        + "\nReturn only JSON matching the provided schema."
    )
    temp_dir = Path(tempfile.mkdtemp(prefix="ai-org-patch_series-grounding-verify-"))
    schema_file = temp_dir / "patch_series-grounding-verdict.schema.json"
    out_file = temp_dir / "grounding-verdict.json"
    try:
        schema_file.write_text(json.dumps(build_grounding_verdict_schema(), indent=2), encoding="utf-8")
        cmd = [
            "codex",
            "exec",
            "--sandbox",
            "read-only",
            "-o",
            str(out_file),
            "--output-schema",
            str(schema_file),
            "--json",
            prompt,
        ]
        try:
            verifier_ctx = (
                ctx.child(stage="grounding.verifier")
                if ctx is not None
                else org_log.RunContext(repo=Path.cwd(), stage="grounding.verifier")
            )
            def invoke(command: list[str]) -> subprocess.CompletedProcess[str]:
                return org_log.logged_subprocess(
                    command,
                    ctx=verifier_ctx,
                    capture_policy="head_tail",
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                )

            completed = run_with_reset_retries(
                lambda: invoke(cmd),
                ctx=verifier_ctx,
                event_name="patchwork_queue.codex_reset_wait",
                resume_once=lambda session_id: invoke(codex_resume_command(cmd, session_id)),
            )
        except OSError as exc:
            return {"ok": False, "violation": f"C0 verifier: grounding verifier failed: {exc}"}

        if completed.returncode != 0:
            detail = completed.stderr.strip() or (
                "no output file" if not out_file.exists() else "Codex verifier did not complete successfully."
            )
            if transient_retries_exhausted(completed):
                return {
                    "ok": False,
                    "violation": (
                        f"C0 verifier: grounding verifier failed: {detail} "
                        f"[{TRANSIENT_RETRIES_EXHAUSTED}]"
                    ),
                    "failure_mode": TRANSIENT_RETRIES_EXHAUSTED,
                }
            return {"ok": False, "violation": f"C0 verifier: grounding verifier failed: {detail}"}
        if not out_file.exists():
            return {"ok": False, "violation": "C0 verifier: grounding verifier failed: no output file"}

        return _parse_grounding_verdict(out_file.read_text(encoding="utf-8"))
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _parse_grounding_verdict(raw: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {"ok": False, "violation": f"C0 verifier: grounding verifier returned invalid JSON: {raw}"}

    if not isinstance(parsed, dict):
        return {"ok": False, "violation": f"C0 verifier: grounding verifier returned non-object JSON: {raw}"}

    expected = ("faithful_specific", "full_scope", "non_legal", "latest_default")
    if not all(isinstance(parsed.get(field), bool) for field in expected):
        return {"ok": False, "violation": f"C0 verifier: grounding verifier returned invalid booleans: {raw}"}
    reasons = parsed.get("reasons")
    if not isinstance(reasons, list) or not all(isinstance(reason, str) for reason in reasons):
        return {"ok": False, "violation": f"C0 verifier: grounding verifier returned invalid reasons: {raw}"}
    return {"ok": True, "verdict": parsed}


def _grounding_text(patch_series_view: dict[str, Any], grounding_result: GroundingResult) -> str:
    parts: list[str] = []
    for field in WORK_ORDER_VIEW_FIELDS:
        value = patch_series_view[field]
        if isinstance(value, list):
            parts.extend(str(item) for item in value)
        elif isinstance(value, dict):
            parts.append(json.dumps(value, sort_keys=True, ensure_ascii=True))
        else:
            parts.append(str(value))
    parts.append(grounding_result.grounding_notes)
    return f" {' '.join(parts).lower()} "


def _keyword_hit_count(text: str, keywords: tuple[str, ...]) -> int:
    return sum(text.count(keyword.lower()) for keyword in keywords)


def _with_unresolved_violations(notes: str, violations: list[str]) -> str:
    suffix = " Unresolved grounding contract violations: " + "; ".join(violations)
    return notes + suffix


def _dedupe(values: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value not in seen:
            seen.add(value)
            deduped.append(value)
    return deduped


def _grounding_fail_closed(
    patch_series_view: dict[str, Any],
    reason: str,
    *,
    failure_mode: str = "",
) -> GroundingResult:
    assumption = "I assumed the current registry-shaped request is the closest available interpretation because grounding failed before it could produce a researched proposal."
    question = "Can you confirm or correct the proposed patch series interpretation?"
    return GroundingResult(
        _patch_series_to_view(patch_series_view),
        reason,
        False,
        [assumption],
        [question],
        failure_mode=failure_mode,
        parse_failure=reason,
    )


def _required_string_field(data: Mapping[str, Any], field: str) -> None:
    value = data.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Request field {field!r} is required and must be a non-empty string.")


def _optional_string_field(data: dict[str, Any], field: str) -> None:
    value = data.setdefault(field, "")
    if not isinstance(value, str):
        raise ValueError(f"Request field {field!r} must be a string when provided.")


def _optional_list_field(data: dict[str, Any], field: str) -> None:
    value = data.setdefault(field, [])
    if not isinstance(value, list):
        raise ValueError(f"Request field {field!r} must be a list when provided.")
    if not all(isinstance(item, str) for item in value):
        raise ValueError(f"Request field {field!r} must contain only strings.")


def _raw_request_from_legacy_entrance(data: Mapping[str, Any]) -> str:
    pieces = []
    for field in ("title", "problem", "proposal"):
        value = data.get(field)
        if isinstance(value, str) and value.strip():
            pieces.append(value.strip())
    return "\n".join(pieces)


def _entrance_request(data: Mapping[str, Any]) -> dict[str, Any]:
    patch_series = entrance_defaults(data)
    _required_string_field(patch_series, "raw_request")
    return patch_series


def _registry_patch_series(data: Mapping[str, Any]) -> dict[str, Any]:
    if set(data) != set(WORK_ORDER_VIEW_FIELDS):
        missing = sorted(set(WORK_ORDER_VIEW_FIELDS) - set(data))
        extra = sorted(set(data) - set(WORK_ORDER_VIEW_FIELDS))
        detail = []
        if missing:
            detail.append("missing " + ", ".join(missing))
        if extra:
            detail.append("extra " + ", ".join(extra))
        suffix = ": " + "; ".join(detail) if detail else ""
        raise ValueError(f"patch series handoff must contain exactly the registry fields{suffix}.")
    for field in STRING_FIELDS:
        if not isinstance(data[field], str):
            raise ValueError(f"patch series field {field!r} must be a string.")
    for field in STRING_ARRAY_FIELDS:
        value = data[field]
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise ValueError(f"patch series field {field!r} must be a list of strings.")
    if not validate_tech_stack(data["tech_stack"], user_facing=deliverable_is_user_facing(data)):
        raise ValueError("patch series field 'tech_stack' must contain valid structured sub-tags.")
    if not validate_user_experience_requirements(data["user_experience_requirements"]):
        raise ValueError("patch series field 'user_experience_requirements' must contain valid structured sub-tags.")
    for field in WORK_ORDER_HANDOFF_REQUIRED_FIELDS:
        value = data[field]
        if isinstance(value, str) and not value.strip():
            raise ValueError(f"patch series handoff field {field!r} is required and must be non-empty.")
    return {field: data[field] for field in WORK_ORDER_VIEW_FIELDS}


def _is_patch_series_view(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    try:
        _registry_patch_series(value)
    except ValueError:
        return False
    return True


def _patch_series_to_view(patch_series_view: dict[str, Any]) -> dict[str, Any]:
    return requester_assumptions.patch_series_to_view(patch_series_view)


def _format_patch_series(label: str, view: dict[str, Any]) -> str:
    lines = [f"{label}:"]
    for field in WORK_ORDER_VIEW_FIELDS:
        value = view[field]
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


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return slug[:80] or "patch_series"


def _valid_ascii_working_slug(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    slug = value.strip()
    if 3 <= len(slug) <= 48 and re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
        return slug
    return ""


def _branch_slug_from_grounding(grounding: GroundingResult, patch_series: Mapping[str, Any]) -> str:
    return _valid_ascii_working_slug(grounding.ascii_working_slug) or _slug(str(patch_series["working_title"]))


def _request_provenance_record(
    validated_request: Mapping[str, Any],
    request_provenance: Mapping[str, Any] | None,
) -> dict[str, Any]:
    provenance = request_provenance or {}
    payload = provenance.get("payload") if isinstance(provenance.get("payload"), Mapping) else validated_request
    payload_record = _json_safe(dict(payload))
    request_id = str(
        provenance.get("request_id")
        or validated_request.get("request_id")
        or validated_request.get("id")
        or ""
    )
    from ai_org.body_codec import strict_json_dumps

    canonical_payload = strict_json_dumps(payload_record).decode("utf-8")
    return {
        "request_id": request_id,
        "payload_sha256": hashlib.sha256(canonical_payload.encode("utf-8")).hexdigest(),
        "raw_request": str(payload_record.get("raw_request") or validated_request.get("raw_request") or ""),
        "request_payload": payload_record,
        "memento": (
            "The off-git inbox is ingress only. Accepted request provenance is committed on the "
            "patch series branch so a pushed ref reconstructs where the series came from."
        ),
    }


def _unique_patch_series_branch(repo: Path, patch_series_id: str) -> str:
    base = f"ai-org/patch-series/{patch_series_id}"
    if not git_wrapper.branch_exists(repo, base):
        return base
    index = 2
    while True:
        candidate = f"{base}-{index}"
        if not git_wrapper.branch_exists(repo, candidate):
            return candidate
        index += 1


def _write_patch_series_branch(
    repo: Path,
    branch: str,
    base: str,
    patch_series: Mapping[str, Any],
    *,
    patch_series_path: str = patch_series_bodies.LEGACY_COVER_PATH,
    extra_files: Mapping[str, Any] | None = None,
    commit_message: str | None = None,
) -> dict[str, str]:
    patch_series_view = _registry_patch_series(patch_series)
    # The entire admitted cohort and target schema are selected and
    # preflighted before checkout, index mutation, or the existing single
    # commit. Canonical member paths cannot be injected around this boundary.
    prepared_cohort, pending_extra = patch_series_bodies.prepare_promotion_cohort(
        patch_series_path,
        patch_series_view,
        extra_files or {},
    )
    # Prepare every contributor-handoff authority before checkout -B can
    # create or reset the target ref. A codec rejection therefore publishes
    # none of the contract/questions/experience cohort.
    committed_files = contributor_handoff.prepare_series_promotion_files(pending_extra)
    original = _current_branch(repo)
    try:
        _git(repo, "checkout", "-B", branch, base)
        canonical_promotion = prepared_cohort is not None
        stored_patch_series_path = patch_series_bodies.COVER_PATH if canonical_promotion else patch_series_path
        path = repo / stored_patch_series_path
        path.parent.mkdir(parents=True, exist_ok=True)
        if prepared_cohort is not None:
            path.write_text(prepared_cohort.files()[patch_series_bodies.COVER_PATH], encoding="utf-8")
        else:
            path.write_text(json.dumps(patch_series_view, indent=2) + "\n", encoding="utf-8")
        add_paths = [stored_patch_series_path]
        # Patch-author contract committed at promotion: a cross-clone patch
        # author that never runs this repository's implement.py must learn the
        # implementation-result.json shape from the series branch itself
        # (boundary-needs-an-interface), not from a prompt it never saw.
        if prepared_cohort is not None:
            committed_files.update(
                {
                    member_path: content
                    for member_path, content in prepared_cohort.files().items()
                    if member_path != patch_series_bodies.COVER_PATH
                }
            )
        for rel_path, payload in committed_files.items():
            extra_path = repo / rel_path
            extra_path.parent.mkdir(parents=True, exist_ok=True)
            content = payload if isinstance(payload, str) else json.dumps(payload, indent=2) + "\n"
            extra_path.write_text(content, encoding="utf-8")
            add_paths.append(rel_path)
        _git(repo, "add", *add_paths)
        if (
            canonical_promotion
            and patch_series_bodies.ROOT_APPROACH_PATH in committed_files
        ):
            _git(
                repo,
                "rm",
                "--ignore-unmatch",
                "--",
                patch_series_bodies.LEGACY_COVER_PATH,
                patch_series_bodies.LEGACY_PROVENANCE_PATH,
                patch_series_bodies.LEGACY_ROOT_APPROACH_PATH,
            )
        # Engine identity is explicit; ambient git config is forbidden (see git_wrapper).
        _git(repo, *git_wrapper.identity_config_args(), "commit", "--allow-empty", "-m", commit_message or f"patch_series: write {patch_series_view['working_title']}")
        commit = _git(repo, "rev-parse", "HEAD").strip()
        return {"branch": branch, "commit": commit}
    finally:
        if original:
            _git(repo, "checkout", original)


def _default_branch(repo: Path) -> str:
    origin_head = _git_run(repo, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    if origin_head.returncode == 0:
        ref = origin_head.stdout.strip()
        if ref.startswith("origin/"):
            return ref

    current = _git_run(repo, "symbolic-ref", "--short", "HEAD")
    if current.returncode == 0 and current.stdout.strip():
        return current.stdout.strip()

    raise RuntimeError("could not determine repository default branch")


def _current_branch(repo: Path) -> str:
    current = _git_run(repo, "symbolic-ref", "--short", "HEAD")
    if current.returncode == 0:
        return current.stdout.strip()
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
