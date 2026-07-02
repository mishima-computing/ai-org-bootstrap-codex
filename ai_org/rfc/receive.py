# receive.py — the INTAKE GATE: judges whether an incoming REQUEST may become an RFC.
# This is NOT a dumb loader/translator. A request is discussed and can be SENT BACK (send-back):
#     request --[receive gate]--> promote to RFC | send back for revision | reject.
# Only requests that PASS this gate become grounded RFCs. The RFC phase has two parts:
#     1) receive : intake — can this REQUEST become an RFC at all? (this file)
#     2) review  : debate the direction of an already-formed RFC. (review.py)
# The RFC phase starts by taking a raw request and forming a grounded RFC view. That promotion is
# real work, not a load. It mirrors the Linux early-stage process (kernel.org process/3.Early-stage):
#     1) Specify the problem   — what must be solved, who is affected, where the system falls short
#     2) Early discussion      — surface objections / alternatives BEFORE implementation
#     3) Who do you talk to    — route to the right reviewers/maintainers (the right subsystem)
#     4) When to post          — the problem + intended approach are stated well enough to act on
#     5) Get buy-in            — go / no-go approval to proceed
# Receive owns the grounding and send-back gate. Review owns only the later direction debate.
#
# DECISION: these 5 processes happen INSIDE the RFC formation — one codex-driven stage, like review's internal
# 5-reviewer + Aufheben loop — NOT as 5 separate git stages/branches/commits. Git stores ONLY the result:
# the promoted, contributor-takeable RFC (ai-org/rfc/<id>: rfc.json) or a send-back/reject marker. Doing
# the 5 processes inside the RFC (not in git) keeps the git state from exploding.
#
# Input/output field contract:
#   entrance REQUEST (rough) requires only raw_request.
#   grounded RFC view = the research-derived field registry after correction and repository-context enrichment.
#   context was replaced by background_facts, references, and grounding_provenance. Every registry field carries
#   role/belongs/must_not/owner/required_at descriptions; must_not is the anti-dumping boundary that keeps
#   research prose out of requirement-bearing fields.
#
# Shape (to match the other stages): validate the request -> codex grounds it -> git-write the
# promoted RFC (ai-org/rfc/<id>: rfc.json), or send the request back with a proposed interpretation.
#
# ==========================================================================================================
# CANONICAL RECEIVE FLOW - 1/2/3/4 (terum 2026-07-02, CONFIRMED). Read this before touching the Reference wiring.
# This block is a "Memento tattoo": the correct flow lives HERE, in the code, because ADR/notes get missed.
#   1. validate + ground       -> grounded RFC (what to build: problem + proposal).
#                                  Provenance discipline: grounding never deliberates the stack. It may only mark
#                                  tech_stack requester_specified when the original request names the stack, or
#                                  unspecified otherwise. Franchise/domain stack precedent is background_facts
#                                  evidence, never a stack decision rule.
#   2. reference.build_from_rfc -> POPULATE the single org-level Reference with THIS RFC's concepts.
#                                  DESIGN facets are synchronous: the RFC WAITS because 3 consumes them.
#                                  IMPLEMENTATION facets are background: the RFC does NOT wait; this warms patch.
#   3. form_technical_approach  -> CONSUME the Reference by lookup to propose HOW to build it
#                                  (Reference patterns + repo context + requester approach). ORG_BUILDER_PROFILE
#                                  is injected as standing hard constraints at step 2: constraints prune the feasible
#                                  set, then judgment chooses among feasible candidates. Fidelity precedent remains
#                                  evidence; requester-specified stacks override org constraints, with conflicts
#                                  recorded later as non-blocking risks.
#   4. promote RFC (with Technical Approach) -> review.
#
# produce_rfc now executes the flow after confident grounding: it synchronously builds DESIGN Reference facets,
# starts IMPLEMENTATION Reference research in the background, forms the Technical Approach with the exact design
# terms from the build, and commits that approach as technical-approach.json beside the registry-shaped rfc.json. Review
# therefore receives a promoted RFC that already carries its Technical Approach.
#
# WHY DESIGN ② PRECEDES ③ (do not reorder): ③'s prior-art map READS the Reference. If design ② has not
# populated it, ③ reads an empty well and every prior_art node degrades to facet_kind='none'. Design ② fills
# the well first, ③ drinks. Implementation ② runs in the background for the later Contributor/patch stage.
#
# THE 0/6 BUG THIS PREVENTS (root cause — do NOT reintroduce): ② and ③ must key off the SAME concept list.
# The old code ran neither ② here NOR shared keys: build_from_rfc extracted terms from _rfc_text(rfc_view),
# while ③ re-extracted terms from a DIFFERENT string (RFC + accumulated approach tree) -> DIFFERENT terms ->
# every lookup MISSED the store -> facet_kind='none' for all 6/6 prior_art nodes, and ③ papered over it with an
# inline reference.expand() band-aid. FIX (current): ② runs first and RETURNS its exact ordered term list;
# form_technical_approach threads THOSE terms into ③, which CONSUMES them by lookup (no re-extract). Reference
# BUILD keys == Reference READ keys, by construction. Inline expand survives ONLY as a fallback when ② is skipped.
#
# SEARCH TIMEOUT (correctness, not speed): a single codex Reference search once hung 36 minutes and stalled the
# whole pipeline. reference search subprocess calls are bounded by AI_ORG_REFERENCE_SEARCH_TIMEOUT (default 180s);
# on timeout that one concept degrades to empty/failed and the build CONTINUES — a hung search can never wedge ③.
#
# COST NOTE (implemented timing split): ② is heavy, so only DESIGN research is synchronous. IMPLEMENTATION
# research is fired through reference.start_background_build(..., kinds=("implementation",)) and is not awaited
# by receive; it warms the same single append-only WAL SQLite Reference for Contributor/patch. Patch/tests can
# drain it with reference.await_background_builds(), and patch still uses expand-on-miss for gaps. WAL plus short
# write transactions keep concurrent foreground reads and background writes safe. The hooks remain: pass
# reference_terms=<prebuilt> or skip_reference_build=True to skip the internal build entirely.
# ==========================================================================================================
#
# TECHNICAL APPROACH — where the design is FORMED (BUILT: form_technical_approach; grounded in Linux/RFC review):
# "propose the approach" and "review the approach" are DISTINCT. Review CRITIQUES a submitted design; it must
# NOT be the first place the approach is created (Rust RFCs / PEPs / IETF I-Ds all post a design that review
# reshapes). So the RFC must arrive at review already carrying a Technical Approach. That approach is formed
# HERE, at receive, and it is the BOUNDARY between the requester and the AI Org:
#     Technical Approach present in the request?
#       YES -> use it as the basis (ground/refine with the Reference + repo context; do NOT discard the
#              requester's approach). Partial -> fill only the gaps.
#       NO  -> the AI Org GENERATES it (Reference-driven: use the Reference's implementation knowledge to
#              propose HOW to build it, labelled as PROPOSAL, not fact).
# GOAL: usable by a layperson (an amateur will not supply a Technical Approach -> the AI Org generates it).
# But even for an amateur, receive does NOT silently decide everything: when it generates the approach it
# also ASKS QUESTIONS BACK — surface the pivotal decisions/assumptions and return them for confirm/correct
# (this is the existing needs_confirmation "propose a guess, ask 'is this right?'" path, EXTENDED to the
# technical-approach decisions). The amateur steers by ANSWERING (intent/preferences), not by AUTHORING.
# The formed Technical Approach carries: problem/impact, reference-derived prior-art, implementation strategy,
# alternatives-with-why-not, compatibility/migration, testing plan, scope/patch plan, open questions.
#
# TECHNICAL APPROACH — formation procedure (grounded in RFC/PEP + ADR + ATAM + senior-dev practice; BUILT as a
# derivation tree, see form_technical_approach). Codex STRUCTURES the reasoning and exposes evidence/trade-offs; it must NOT emit a one-shot
# design claim. Weighting stays judgment-heavy (final priorities / risk tolerance / architectural taste are human):
#   1. Normalize the problem: problem, affected users/systems, current inadequacy, success criteria, non-goals.
#   2. Extract constraints: hard constraints + soft preferences (repo architecture, compatibility, data/API
#      contracts, performance/security/reliability, test constraints, delivery scope).
#   3. Build a prior-art map from the REFERENCE (design + implementation facets) + repo context: 3-6 patterns,
#      each {pattern, where-seen, when-applies, tradeoffs, adopt|adapt|reject}. This is where e.g. an engine like
#      Godot lands as a CANDIDATE — put on the table and judged on merit/fit, NOT on how often it appeared.
#   4. Generate 2-3 candidate approaches: always a minimal/local one, a repo-native/reference-aligned one, and a
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
"""RFC receive — validate and ground an entrance request into an RFC."""
from __future__ import annotations

import concurrent.futures
from dataclasses import dataclass, field
import json
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
from typing import Any, Mapping

import ai_org.reference as reference
import ai_org.rfc.codex_exec as codex_exec
from ai_org.rfc.field_registry import (
    ENTRANCE_REQUIRED_FIELDS,
    FIELD_REGISTRY,
    LINT_TARGET_FIELDS,
    OPTIONAL_FIELDS,
    RFC_HANDOFF_REQUIRED_FIELDS,
    RFC_VIEW_FIELDS,
    STRING_ARRAY_FIELDS,
    STRING_FIELDS,
    TECH_STACK_FIELDS,
    entrance_defaults,
    rfc_view_schema,
    validate_tech_stack,
)


LOGGER = logging.getLogger(__name__)

COMMON_8_FIELDS = RFC_VIEW_FIELDS
REQUIRED_FIELDS = ENTRANCE_REQUIRED_FIELDS
OPTIONAL_STRING_FIELDS = STRING_FIELDS
OPTIONAL_LIST_FIELDS = STRING_ARRAY_FIELDS

ORG_BUILDER_PROFILE: tuple[dict[str, str], ...] = (
    {
        "statement": "Contributors author text artifacts in git worktrees.",
        "trace": "Codex CLI agents can create and edit code, data, configuration, and text files.",
        "must": "Prefer stacks whose primary artifact can be authored and reviewed as text in a git worktree.",
        "must_not": "Depend on GUI-editor workflows or opaque project state as the primary construction path.",
    },
    {
        "statement": "The org does not author binary assets or operate a 3D DCC pipeline.",
        "trace": "Asset production is the org's 2D vector/SVG graphicist; no Blender, Maya, or binary asset production pipeline is available.",
        "must": "Use text-authored assets or 2D vector/SVG assets that can be generated and reviewed in the worktree.",
        "must_not": "Require bespoke 3D models, DCC scenes, binary editor assets, or heavyweight asset cooking to prove the RFC.",
    },
    {
        "statement": "Verification must run headlessly inside the worktree.",
        "trace": "functional_check boots or probes the artifact CI-style without an interactive editor.",
        "must": "Choose implementation paths that can be installed, launched, and checked by headless commands.",
        "must_not": "Require interactive editor sessions, manual GUI inspection, or heavyweight native build pipelines for the first verification path.",
    },
    {
        "statement": "Deliverables should be directly reachable by intended users.",
        "trace": "Browser or lightweight desktop delivery is reachable; heavyweight engine installs are not assumed for users or CI.",
        "must": "Prefer browser or lightweight desktop targets when they can satisfy the RFC.",
        "must_not": "Make heavyweight installs or commercial engine toolchains the default user access path without requester instruction.",
    },
    {
        "statement": "Licensing and cost are standing construction constraints.",
        "trace": "The org weighs licensing, commercial engine terms, CI availability, and contributor cost before choosing a stack.",
        "must": "Prefer stacks whose licenses, runtime costs, and CI/tooling costs fit routine repository work.",
        "must_not": "Select a costly or license-sensitive stack solely because external precedent uses it.",
    },
)

REQUEST_SCHEMA: dict[str, Any] = {
    "recognized_fields": list(RFC_VIEW_FIELDS),
    "field_registry": {entry.name: entry.description for entry in FIELD_REGISTRY},
    "required": list(REQUIRED_FIELDS),
    "optional": list(OPTIONAL_FIELDS),
    "additional_properties": True,
}

GROUNDING_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": ["confident", "proposed_rfc", "assumptions", "questions", "grounding_notes"],
    "properties": {
        "confident": {"type": "boolean"},
        "proposed_rfc": rfc_view_schema(),
        "assumptions": {"type": "array", "items": {"type": "string"}},
        "questions": {"type": "array", "items": {"type": "string"}},
        "grounding_notes": {"type": "string"},
    },
}

GROUNDING_VERDICT_SCHEMA: dict[str, Any] = {
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
)
SUCCESS_CRITERION_FIELDS = (
    "actor",
    "capability",
    "verifiable_outcome",
    "verification",
)
SUCCESS_CRITERION_CAPABILITY_FIELDS = ("action", "preconditions")
SUCCESS_CRITERION_OUTCOME_FIELDS = ("expected_state", "evidence")
SUCCESS_CRITERION_VERIFICATION_FIELDS = ("method", "check")
SUCCESS_CRITERION_VERIFICATION_METHODS = ("automated_test", "manual_check", "metric")
MAX_NORMALIZE_PROBLEM_REGENERATIONS = 2
MAX_TECHNICAL_APPROACH_NODE_REGENERATIONS = 2

SUCCESS_CRITERION_SCHEMA: dict[str, Any] = {
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
    },
}

NORMALIZE_PROBLEM_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": list(NORMALIZED_PROBLEM_FIELDS),
    "properties": {
        "problem": {"type": "string"},
        "affected": {"type": "string"},
        "current_inadequacy": {"type": "string"},
        "success_criteria": {"type": "array", "items": SUCCESS_CRITERION_SCHEMA},
        "non_goals": {"type": "array", "items": {"type": "string"}},
    },
}

CONSTRAINT_DERIVATION_VALUES = ("problem", "success_criteria", "non_goals", "repo", "domain", "org_builder_profile")
CONSTRAINT_DERIVATION_FIELDS = ("from", "trace")
CONSTRAINT_IMPLICATION_FIELDS = ("must", "must_not")
CONSTRAINT_ITEM_FIELDS = ("statement", "derivation", "implication")
PREFERENCE_ITEM_FIELDS = ("statement", "derivation", "rationale")
EXTRACT_CONSTRAINTS_FIELDS = ("hard_constraints", "soft_preferences")
MAX_EXTRACT_CONSTRAINTS_REGENERATIONS = 2

EXTRACT_CONSTRAINTS_SCHEMA: dict[str, Any] = {
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

PRIOR_ART_MAP_SCHEMA: dict[str, Any] = {
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
    "first_playable_moment",
    "core_systems",
    "draws_on",
)
CANDIDATE_FIRST_PLAYABLE_FIELDS = (
    "player_actions",
    "named_content",
    "win_or_progress_condition",
)
GAME_NAMED_CONTENT_FIELDS = (
    "locations",
    "enemies",
    "items_or_spells",
)
CANDIDATE_APPROACH_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": list(CANDIDATE_APPROACH_FIELDS),
    "properties": {
        "id": {"type": "string"},
        "name": {"type": "string"},
        "kind": {"type": "string", "enum": list(CANDIDATE_APPROACH_KINDS)},
        "summary": {"type": "string"},
        "first_playable_moment": {
            "type": "object",
            "additionalProperties": False,
            "required": list(CANDIDATE_FIRST_PLAYABLE_FIELDS),
            "properties": {
                "player_actions": {"type": "array", "items": {"type": "string"}},
                "named_content": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": list(GAME_NAMED_CONTENT_FIELDS),
                    "properties": {
                        "locations": {"type": "array", "items": {"type": "string"}},
                        "enemies": {"type": "array", "items": {"type": "string"}},
                        "items_or_spells": {"type": "array", "items": {"type": "string"}},
                    },
                },
                "win_or_progress_condition": {"type": "string"},
            },
        },
        "core_systems": {"type": "array", "items": {"type": "string"}},
        "draws_on": {"type": "array", "items": {"type": "string"}},
    },
}

GENERATE_CANDIDATES_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": ["candidates"],
    "properties": {
        "candidates": {
            "type": "array",
            "items": CANDIDATE_APPROACH_SCHEMA,
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
EVALUATE_CANDIDATE_SCHEMA: dict[str, Any] = {
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
EVALUATE_CANDIDATES_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": ["evaluations"],
    "properties": {
        "evaluations": {"type": "array", "items": EVALUATE_CANDIDATE_SCHEMA},
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
    "builder_buildability",
    "asset_supply",
    "distribution_reachability",
    "licensing_cost",
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
)

SELECT_APPROACH_SCHEMA: dict[str, Any] = {
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
    },
}
IMPLEMENTATION_STRATEGY_FIELDS = (
    "systems",
    "persistence",
)
IMPLEMENTATION_SYSTEM_FIELDS = (
    "system_name",
    "behavior_in_game",
    "named_content",
    "key_modules",
)
IMPLEMENTATION_NAMED_CONTENT_FIELDS = (
    "entities",
    "content_items",
)
IMPLEMENTATION_PERSISTENCE_FIELDS = ("saved_fields",)

IMPLEMENTATION_STRATEGY_SCHEMA: dict[str, Any] = {
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
                    "behavior_in_game": {"type": "string"},
                    "named_content": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": list(IMPLEMENTATION_NAMED_CONTENT_FIELDS),
                        "properties": {
                            "entities": {"type": "array", "items": {"type": "string"}},
                            "content_items": {"type": "array", "items": {"type": "string"}},
                        },
                    },
                    "key_modules": {"type": "array", "items": {"type": "string"}},
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
PATCH_PLAN_DEFERRED_FIELDS = (
    "item",
    "why_safe_to_defer",
)
PATCH_PLAN_FOLLOW_UP_FIELDS = (
    "adds",
    "named_content",
)
PATCH_PLAN_FIRST_PLAYABLE_FIELDS = (
    "player_can",
    "named_content",
    "win_or_progress_condition",
    "how_verified",
)
RIGHT_SIZE_PATCH_PLAN_FIELDS = (
    "first_playable",
    "follow_ups",
    "deferred",
)

RIGHT_SIZE_PATCH_PLAN_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": list(RIGHT_SIZE_PATCH_PLAN_FIELDS),
    "properties": {
        "first_playable": {
            "type": "object",
            "additionalProperties": False,
            "required": list(PATCH_PLAN_FIRST_PLAYABLE_FIELDS),
            "properties": {
                "player_can": {"type": "array", "items": {"type": "string"}},
                "named_content": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": list(GAME_NAMED_CONTENT_FIELDS),
                    "properties": {
                        "locations": {"type": "array", "items": {"type": "string"}},
                        "enemies": {"type": "array", "items": {"type": "string"}},
                        "items_or_spells": {"type": "array", "items": {"type": "string"}},
                    },
                },
                "win_or_progress_condition": {"type": "string"},
                "how_verified": {"type": "string"},
            },
        },
        "follow_ups": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": list(PATCH_PLAN_FOLLOW_UP_FIELDS),
                "properties": {
                    "adds": {"type": "string"},
                    "named_content": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": list(GAME_NAMED_CONTENT_FIELDS),
                        "properties": {
                            "locations": {"type": "array", "items": {"type": "string"}},
                            "enemies": {"type": "array", "items": {"type": "string"}},
                            "items_or_spells": {"type": "array", "items": {"type": "string"}},
                        },
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
    },
}
SURFACED_RISK_FIELDS = (
    "id",
    "risk",
    "mitigation",
    "attaches_to",
    "target_id",
)
RISK_ATTACHES_TO = ("candidate", "decision", "implementation")
SURFACE_RISKS_FIELDS = ("risks",)

SURFACE_RISKS_SCHEMA: dict[str, Any] = {
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
    "rfc",
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
_WORKING_TITLE_ABBREVIATIONS = {"ai", "api", "cli", "css", "html", "json", "rfc", "ui", "ux"}
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

GENERALIZER_MARKERS = (
    "-style",
    " style",
    "-like",
    "inspired by",
    "generic",
    "broad category",
    "genre entry",
    "safer-sounding substitute",
)

SCOPE_HEDGE_MARKERS = (
    "vertical slice",
    "mvp",
    "prototype",
    "short demo",
    "one town",
    "10-minute",
    "minimal",
    "first iteration",
    "small deliverable",
)

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

RETRO_MARKERS = (
    "1986",
    "famicom",
    "windows 95",
    "windows-95",
    "retro",
    "classic",
    "old-school",
    "old school",
    "vintage",
)

RETRO_REQUEST_MARKERS = RETRO_MARKERS + (
    "older version",
    "old version",
    "past version",
    "specific past version",
    "specific year",
)


@dataclass
class GroundingResult:
    rfc_view: dict[str, Any]
    grounding_notes: str = ""
    confident: bool = True
    assumptions: list[str] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)


def _require_confirmation() -> bool:
    return os.environ.get("AI_ORG_REQUIRE_CONFIRMATION", "").strip().lower() in {"1", "true", "yes"}


def _rfc_with_non_blocking_grounding_uncertainty(grounding: GroundingResult) -> dict[str, Any]:
    rfc = dict(grounding.rfc_view)
    if grounding.questions:
        rfc["open_questions"] = _append_unique_strings(rfc.get("open_questions"), grounding.questions)
    if grounding.assumptions:
        rfc["constraints_assumptions"] = _append_unique_strings(rfc.get("constraints_assumptions"), grounding.assumptions)

    uncertainty_notes: list[str] = []
    if not grounding.confident:
        uncertainty_notes.append("Grounding was not fully confident; best-guess RFC promoted without blocking.")
    if grounding.assumptions:
        uncertainty_notes.append("Non-blocking assumptions: " + "; ".join(grounding.assumptions))
    if grounding.questions:
        uncertainty_notes.append("Non-blocking open questions are preserved in open_questions.")

    if uncertainty_notes:
        existing = str(rfc.get("grounding_provenance") or "").strip()
        appended = " ".join(uncertainty_notes)
        rfc["grounding_provenance"] = f"{existing}\n\n{appended}" if existing else appended
    return rfc


def _append_unique_strings(existing: Any, additions: list[str]) -> list[str]:
    values = list(existing) if isinstance(existing, list) else []
    seen = {value for value in values if isinstance(value, str)}
    for addition in additions:
        if addition and addition not in seen:
            values.append(addition)
            seen.add(addition)
    return values


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
    if "tech_stack" in data and not validate_tech_stack(data["tech_stack"], require_choice=False):
        raise ValueError("Request field 'tech_stack' must contain the structured tech stack sub-tags.")

    return data


def intake(
    source: str | Path | Mapping[str, Any],
    repo: str | Path,
    rfc_path: str = "rfc.json",
    progress_path: str | Path | None = None,
) -> dict[str, Any]:
    """Validate and ground a raw request, then promote it only when grounding is confident."""
    try:
        request = receive(source)
        return produce_rfc(request, repo, rfc_path, progress_path=progress_path)
    except ValueError as exc:
        return {"status": "rejected", "error": str(exc)}


def produce_rfc(
    validated_request: Mapping[str, Any],
    repo: str | Path,
    rfc_path: str = "rfc.json",
    progress_path: str | Path | None = None,
) -> dict[str, Any]:
    """Ground and write a validated registry request to git as ai-org/rfc/<id>:rfc.json."""
    raw_rfc = _entrance_request(validated_request)
    repo_path = Path(repo).resolve()
    grounding = _ground_with_contract(repo_path, raw_rfc)
    if grounding.violations:
        return {
            "ok": False,
            "status": "needs_work",
            "error": "Grounding contract violations remain unresolved after verification.",
            "failed_step": "grounding",
            "proposed_rfc": grounding.rfc_view,
            "grounding_notes": grounding.grounding_notes,
            "violations": grounding.violations,
        }
    # Memento: needs_confirmation is default-off because autonomy is the AI Org's differentiator; the
    # confirm-back loop is deferred to the roadmap end. Preserve assumptions/open questions non-blocking.
    if not grounding.confident and _require_confirmation():
        result = {
            "status": "needs_confirmation",
            "proposed_rfc": grounding.rfc_view,
            "assumptions": grounding.assumptions,
            "questions": grounding.questions,
            "grounding_notes": grounding.grounding_notes,
        }
        if grounding.violations:
            result["violations"] = grounding.violations
        return result

    rfc = (
        grounding.rfc_view
        if grounding.confident
        else _rfc_with_non_blocking_grounding_uncertainty(grounding)
    )
    approach_context = _technical_approach_context(None, repo_path)
    design_build = reference.build_from_rfc(rfc, approach_context, kinds=("design",))
    design_terms = _reference_terms_from_build_result(design_build)
    reference.start_background_build(rfc, approach_context, kinds=("implementation",))
    # Keep the entrypoint from pinning a stack; hardcoded language/environment/version forecloses engine/platform alternatives.
    approach = form_technical_approach(
        rfc,
        repo_path,
        context=approach_context,
        reference_terms=design_terms,
        progress_path=progress_path,
    )
    if approach.get("ok") is False:
        return {
            "ok": False,
            "status": "needs_work",
            "error": approach.get("error", "Technical Approach formation failed"),
            "failed_step": approach.get("failed_step"),
            "proposed_rfc": rfc,
            "grounding_notes": grounding.grounding_notes,
        }

    rfc_id = _slug(rfc["working_title"])
    branch = f"ai-org/rfc/{rfc_id}"
    base = _default_branch(repo_path)
    written = _write_rfc_branch(
        repo_path,
        branch,
        base,
        rfc,
        rfc_path=rfc_path,
        extra_files={"technical-approach.json": approach["technical_approach"]},
        commit_message=f"rfc: receive {rfc['working_title']}",
    )
    return {
        "ok": True,
        "status": "promoted",
        "id": rfc_id,
        "branch": branch,
        "commit": written["commit"],
        "technical_approach_path": "technical-approach.json",
        "grounding_notes": grounding.grounding_notes,
    }


def _normalize_problem(rfc_view: dict[str, Any], context: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Form step 1 of the documented 10-step Technical Approach procedure."""
    if not _is_rfc_view(rfc_view):
        return _normalized_problem_error("normalize_problem requires a grounded registry RFC view")

    repo = _repo_from_context(context)
    feedback: list[str] = []
    last_lint_error = ""
    for attempt in range(MAX_NORMALIZE_PROBLEM_REGENERATIONS + 1):
        run = codex_exec.run_json(
            repo,
            schema=NORMALIZE_PROBLEM_SCHEMA,
            prompt=_normalize_problem_prompt(rfc_view, context, feedback if attempt else None),
            schema_filename="rfc-normalize-problem.schema.json",
            output_filename="rfc-normalized-problem.json",
            failure_label="Codex problem normalization",
        )
        if not run["ok"]:
            return _normalized_problem_error(run["error"])
        parsed = _parse_normalized_problem(run["raw"])
        if not parsed.get("ok", True):
            return parsed

        lint_errors = _lint_normalized_problem(parsed, rfc_view)
        if not lint_errors:
            return parsed
        last_lint_error = "; ".join(lint_errors)
        feedback = lint_errors

    return _normalized_problem_error(
        "Codex problem normalization remained unmeasurable after "
        f"{MAX_NORMALIZE_PROBLEM_REGENERATIONS + 1} attempts: {last_lint_error}"
    )


def _extract_constraints(
    rfc_view: dict[str, Any],
    repo: str | Path,
    context: Mapping[str, Any] | None = None,
    approach: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Form step 2 of the documented 10-step Technical Approach procedure."""
    # Step 2 of 10: attach constraints to the accumulated approach before approach generation.
    if not _is_rfc_view(rfc_view):
        return _constraints_error("extract_constraints requires a grounded registry RFC view")

    repo_path = Path(repo).resolve()
    accumulated_approach = _constraints_approach_context(approach)
    feedback: list[str] = []
    last_error = ""
    for attempt in range(MAX_EXTRACT_CONSTRAINTS_REGENERATIONS + 1):
        run = codex_exec.run_json(
            repo_path,
            schema=EXTRACT_CONSTRAINTS_SCHEMA,
            prompt=_extract_constraints_prompt(
                rfc_view,
                repo_path,
                context,
                accumulated_approach,
                feedback if attempt else None,
            ),
            schema_filename="rfc-extract-constraints.schema.json",
            output_filename="rfc-extracted-constraints.json",
            failure_label="Codex constraint extraction",
        )
        if not run["ok"]:
            return _constraints_error(run["error"])
        parsed = _parse_constraints(run["raw"])
        if not parsed.get("ok", True):
            last_error = parsed["error"]
            feedback = [last_error]
            continue
        parsed = _merge_org_builder_constraints(parsed)

        lint_errors = _lint_constraints(parsed, accumulated_approach)
        if not lint_errors:
            return parsed
        last_error = "; ".join(lint_errors)
        feedback = lint_errors

    return _constraints_error(
        "Codex constraint extraction remained invalid after "
        f"{MAX_EXTRACT_CONSTRAINTS_REGENERATIONS + 1} attempts: {last_error}"
    )


def _build_prior_art_map(
    rfc_view: dict[str, Any],
    repo: str | Path,
    context: Mapping[str, Any] | None = None,
    approach: Mapping[str, Any] | None = None,
    reference_terms: Any | None = None,
) -> dict[str, Any]:
    """Form step 3 of the documented 10-step Technical Approach procedure."""
    # Step 3 of 10: attach prior art to the accumulated approach before candidate generation.
    if not _is_rfc_view(rfc_view):
        return _prior_art_error("build_prior_art_map requires a grounded registry RFC view")

    repo_path = Path(repo).resolve()
    accumulated_approach = _prior_art_approach_context(approach)
    concepts = (
        _reference_terms_for_prior_art(reference_terms)
        if reference_terms is not None
        else _prior_art_key_concepts(rfc_view, context, accumulated_approach)
    )
    try:
        reference_facets = _read_prior_art_reference_facets(
            concepts,
            context,
            allow_expand=reference_terms is None,
        )
    except Exception as exc:
        return _prior_art_error(f"Reference prior-art read failed: {exc}")

    feedback: list[str] = []
    last_error = ""
    for attempt in range(MAX_PRIOR_ART_REGENERATIONS + 1):
        run = codex_exec.run_json(
            repo_path,
            schema=PRIOR_ART_MAP_SCHEMA,
            prompt=_prior_art_map_prompt(
                rfc_view,
                repo_path,
                concepts,
                reference_facets,
                context,
                accumulated_approach,
                feedback if attempt else None,
            ),
            schema_filename="rfc-prior-art-map.schema.json",
            output_filename="rfc-prior-art-map.json",
            failure_label="Codex prior-art mapping",
        )
        if not run["ok"]:
            return _prior_art_error(run["error"])
        parsed = _parse_prior_art_map(run["raw"])
        if not parsed.get("ok", True):
            last_error = parsed["error"]
            feedback = [last_error]
            continue

        lint_errors = _lint_prior_art_map(parsed, reference_facets, accumulated_approach)
        if not lint_errors:
            _attach_prior_art_reference_facets(parsed, reference_facets)
            return parsed
        last_error = "; ".join(lint_errors)
        feedback = lint_errors

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
) -> dict[str, Any]:
    """Form step 4 of the documented 10-step Technical Approach procedure."""
    # Step 4 of 10: generate candidate approaches from the already-normalized problem, constraints, and prior art.
    if not all(isinstance(value, Mapping) for value in (normalized_problem, constraints, prior_art_map)):
        return _candidate_generation_error("generate_candidates requires outputs from steps 1-3")

    repo = _repo_from_context(context)
    candidates: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_kinds: set[str] = set()
    for kind in ("minimal_local", "repo_native", "general_architectural"):
        feedback: list[str] = []
        last_error = ""
        parsed: dict[str, Any] | None = None
        for attempt in range(MAX_TECHNICAL_APPROACH_NODE_REGENERATIONS + 1):
            run = codex_exec.run_json(
                repo,
                schema=CANDIDATE_APPROACH_SCHEMA,
                prompt=_generate_candidate_prompt(
                    normalized_problem,
                    constraints,
                    prior_art_map,
                    kind,
                    candidates,
                    context,
                    accumulated_approach,
                    feedback if attempt else None,
                ),
                schema_filename=f"rfc-candidate-{kind}.schema.json",
                output_filename=f"rfc-candidate-{kind}.json",
                failure_label="Codex candidate generation",
            )
            if not run["ok"]:
                return _candidate_generation_error(run["error"])
            parsed = _parse_candidate_approach(run["raw"], expected_kind=kind)
            if not parsed.get("ok", True):
                last_error = parsed["error"]
                feedback = [last_error]
                continue
            lint_errors = _lint_empty_slots(parsed)
            lint_errors.extend(_lint_candidate_against_org_builder(parsed, constraints))
            if parsed["id"] in seen_ids:
                lint_errors.append(f"id duplicates an earlier candidate: {parsed['id']}")
            if parsed["kind"] in seen_kinds:
                lint_errors.append(f"kind duplicates an earlier candidate: {parsed['kind']}")
            if not lint_errors:
                break
            last_error = "; ".join(lint_errors)
            feedback = lint_errors
            parsed = None
        if parsed is None or not parsed.get("ok", True):
            return _candidate_generation_error(
                "Codex candidate generation remained invalid after "
                f"{MAX_TECHNICAL_APPROACH_NODE_REGENERATIONS + 1} attempts: {last_error}"
            )
        seen_ids.add(parsed["id"])
        seen_kinds.add(parsed["kind"])
        candidates.append(parsed)

    return {"candidates": candidates}


def _evaluate_candidates(
    candidates: Mapping[str, Any],
    normalized_problem: Mapping[str, Any],
    constraints: Mapping[str, Any],
    context: Mapping[str, Any] | None = None,
    accumulated_approach: Mapping[str, Any] | None = None,
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
        for attempt in range(MAX_TECHNICAL_APPROACH_NODE_REGENERATIONS + 1):
            run = codex_exec.run_json(
                repo,
                schema=EVALUATE_CANDIDATE_SCHEMA,
                prompt=_evaluate_candidate_prompt(
                    candidate,
                    candidates,
                    normalized_problem,
                    constraints,
                    context,
                    accumulated_approach,
                    feedback if attempt else None,
                ),
                schema_filename=f"rfc-evaluate-candidate-{candidate_id}.schema.json",
                output_filename=f"rfc-candidate-evaluation-{candidate_id}.json",
                failure_label="Codex candidate evaluation",
            )
            if not run["ok"]:
                return _candidate_evaluation_error(run["error"])
            parsed = _parse_candidate_evaluation(run["raw"], candidate_id)
            if not parsed.get("ok", True):
                last_error = parsed["error"]
                feedback = [last_error]
                continue
            lint_errors = _lint_empty_slots(parsed)
            if not lint_errors:
                break
            last_error = "; ".join(lint_errors)
            feedback = lint_errors
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
        return _approach_selection_error("select_approach requires one evaluation per candidate from step 5")

    repo = _repo_from_context(context)
    feedback: list[str] = []
    last_error = ""
    for attempt in range(MAX_TECHNICAL_APPROACH_NODE_REGENERATIONS + 1):
        run = codex_exec.run_json(
            repo,
            schema=SELECT_APPROACH_SCHEMA,
            prompt=_select_approach_prompt(
                candidates,
                evaluations,
                constraints,
                context,
                accumulated_approach,
                feedback if attempt else None,
            ),
            schema_filename="rfc-select-approach.schema.json",
            output_filename="rfc-selected-approach.json",
            failure_label="Codex approach selection",
        )
        if not run["ok"]:
            return _approach_selection_error(run["error"])
        parsed = _parse_approach_selection(run["raw"], candidate_ids)
        if not parsed.get("ok", True):
            last_error = parsed["error"]
            feedback = [last_error]
            continue
        lint_errors = _lint_empty_slots(parsed)
        if not lint_errors:
            return parsed
        last_error = "; ".join(lint_errors)
        feedback = lint_errors

    return _approach_selection_error(
        "Codex approach selection remained invalid after "
        f"{MAX_TECHNICAL_APPROACH_NODE_REGENERATIONS + 1} attempts: {last_error}"
    )


def _implementation_strategy(
    chosen: Mapping[str, Any],
    prior_art_map: Mapping[str, Any],
    constraints: Mapping[str, Any],
    rfc_view: dict[str, Any],
    repo: str | Path,
    context: Mapping[str, Any] | None = None,
    accumulated_approach: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Form step 7 of the documented 10-step Technical Approach procedure."""
    # Step 7 of 10: expand the selected approach into an implementation strategy without planning slices.
    if not all(isinstance(value, Mapping) for value in (chosen, prior_art_map, constraints)):
        return _implementation_strategy_error("implementation_strategy requires outputs from steps 2, 3, and 6")
    if not _is_rfc_view(rfc_view):
        return _implementation_strategy_error("implementation_strategy requires a grounded registry RFC view")

    repo_path = Path(repo).resolve()
    feedback: list[str] = []
    last_error = ""
    for attempt in range(MAX_TECHNICAL_APPROACH_NODE_REGENERATIONS + 1):
        run = codex_exec.run_json(
            repo_path,
            schema=IMPLEMENTATION_STRATEGY_SCHEMA,
            prompt=_implementation_strategy_prompt(
                chosen,
                prior_art_map,
                constraints,
                rfc_view,
                repo_path,
                context,
                accumulated_approach,
                feedback if attempt else None,
            ),
            schema_filename="rfc-implementation-strategy.schema.json",
            output_filename="rfc-implementation-strategy.json",
            failure_label="Codex implementation strategy",
        )
        if not run["ok"]:
            return _implementation_strategy_error(run["error"])
        parsed = _parse_implementation_strategy(run["raw"])
        if not parsed.get("ok", True):
            last_error = parsed["error"]
            feedback = [last_error]
            continue
        lint_errors = _lint_empty_slots(parsed)
        if not lint_errors:
            return parsed
        last_error = "; ".join(lint_errors)
        feedback = lint_errors

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
) -> dict[str, Any]:
    """Form step 8 of the documented 10-step Technical Approach procedure."""
    # Step 8 of 10: right-size the strategy into incremental slices without surfacing later-step risks.
    if not all(isinstance(value, Mapping) for value in (chosen, implementation_strategy, constraints)):
        return _right_size_patch_plan_error("right_size_patch_plan requires outputs from steps 2, 6, and 7")

    repo = _repo_from_context(context)
    feedback: list[str] = []
    last_error = ""
    for attempt in range(MAX_TECHNICAL_APPROACH_NODE_REGENERATIONS + 1):
        run = codex_exec.run_json(
            repo,
            schema=RIGHT_SIZE_PATCH_PLAN_SCHEMA,
            prompt=_right_size_patch_plan_prompt(
                chosen,
                implementation_strategy,
                constraints,
                context,
                accumulated_approach,
                feedback if attempt else None,
            ),
            schema_filename="rfc-right-size-patch-plan.schema.json",
            output_filename="rfc-right-sized-patch-plan.json",
            failure_label="Codex patch plan right-sizing",
        )
        if not run["ok"]:
            return _right_size_patch_plan_error(run["error"])
        parsed = _parse_right_size_patch_plan(run["raw"])
        if not parsed.get("ok", True):
            last_error = parsed["error"]
            feedback = [last_error]
            continue
        lint_errors = _lint_empty_slots(parsed)
        if not lint_errors:
            return parsed
        last_error = "; ".join(lint_errors)
        feedback = lint_errors

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
) -> dict[str, Any]:
    """Form step 9 of the documented 10-step Technical Approach procedure."""
    # Step 9 of 10: surface risk nodes and attach each one to its parent node.
    if not all(isinstance(value, Mapping) for value in (chosen, implementation_strategy, patch_plan, constraints)):
        return _surface_risks_error("surface_risks requires outputs from steps 2, 6, 7, and 8")

    repo = _repo_from_context(context)
    feedback: list[str] = []
    last_error = ""
    for attempt in range(MAX_TECHNICAL_APPROACH_NODE_REGENERATIONS + 1):
        run = codex_exec.run_json(
            repo,
            schema=SURFACE_RISKS_SCHEMA,
            prompt=_surface_risks_prompt(
                chosen,
                implementation_strategy,
                patch_plan,
                constraints,
                context,
                accumulated_approach,
                feedback if attempt else None,
            ),
            schema_filename="rfc-surface-risks.schema.json",
            output_filename="rfc-surfaced-risks.json",
            failure_label="Codex risk surfacing",
        )
        if not run["ok"]:
            return _surface_risks_error(run["error"])
        parsed = _parse_surface_risks(run["raw"])
        if not parsed.get("ok", True):
            last_error = parsed["error"]
            feedback = [last_error]
            continue
        lint_errors = _lint_empty_slots(parsed)
        if not lint_errors:
            return parsed
        last_error = "; ".join(lint_errors)
        feedback = lint_errors

    return _surface_risks_error(
        "Codex risk surfacing remained invalid after "
        f"{MAX_TECHNICAL_APPROACH_NODE_REGENERATIONS + 1} attempts: {last_error}"
    )


def form_technical_approach(
    rfc_view: dict[str, Any],
    repo: str | Path,
    context: Mapping[str, Any] | None = None,
    provided_approach: Any | None = None,
    progress_path: str | Path | None = None,
    reference_terms: Any | None = None,
    skip_reference_build: bool = False,
) -> dict[str, Any]:
    """Form step 10 of the documented 10-step Technical Approach procedure."""
    # Step 10 of 10: the orchestrator + requester/AI-Org boundary.
    if not _is_rfc_view(rfc_view):
        return {
            "ok": False,
            "error": "form_technical_approach requires a grounded registry RFC view",
            "failed_step": "input",
        }

    repo_path = Path(repo).resolve()
    approach_context = _technical_approach_context(context, repo_path)
    effective_provided_approach = provided_approach
    if effective_provided_approach is None:
        effective_provided_approach = _provided_approach_from_tech_stack(rfc_view)
    steps: dict[str, Any] = {}
    step_order = _technical_approach_step_order(effective_provided_approach)
    steps_completed: list[dict[str, Any]] = []

    def start_step() -> float:
        return time.monotonic() if progress_path is not None else 0.0

    def mark_step_completed(step: str, started_at: float, tree: Mapping[str, Any]) -> None:
        if progress_path is None:
            return
        steps_completed.append({"step": step, "seconds": time.monotonic() - started_at})
        _write_technical_approach_progress(
            progress_path,
            tree,
            steps_completed,
            _next_technical_approach_step(step_order, step),
        )

    started_at = start_step()
    normalized = _normalize_problem(rfc_view, approach_context)
    failure = _technical_approach_step_failure("normalize_problem", normalized)
    if failure:
        return failure
    steps["normalize_problem"] = normalized
    partial_tree: dict[str, Any] = {"problem": _problem_root_from_normalized(normalized)}
    mark_step_completed("normalize_problem", started_at, partial_tree)

    started_at = start_step()
    constraints = _extract_constraints(rfc_view, repo_path, approach_context, dict(partial_tree))
    failure = _technical_approach_step_failure("extract_constraints", constraints)
    if failure:
        return failure
    steps["extract_constraints"] = constraints
    partial_tree["problem"]["constraints"] = _constraint_tree_nodes(constraints)
    mark_step_completed("extract_constraints", started_at, partial_tree)

    prior_art_reference_terms = reference_terms
    if prior_art_reference_terms is None and not skip_reference_build:
        design_build = reference.build_from_rfc(rfc_view, approach_context, kinds=("design",))
        prior_art_reference_terms = _reference_terms_from_build_result(design_build)
        steps["build_reference_from_rfc"] = _json_safe(design_build)
        reference.start_background_build(rfc_view, approach_context, kinds=("implementation",))

    started_at = start_step()
    prior_art = _build_prior_art_map(
        rfc_view,
        repo_path,
        approach_context,
        dict(partial_tree),
        prior_art_reference_terms,
    )
    failure = _technical_approach_step_failure("build_prior_art_map", prior_art)
    if failure:
        return failure
    steps["build_prior_art_map"] = prior_art
    partial_tree["problem"]["prior_art"] = _prior_art_tree_nodes(prior_art)
    mark_step_completed("build_prior_art_map", started_at, partial_tree)

    if effective_provided_approach is None:
        started_at = start_step()
        candidates = _generate_candidates(
            normalized,
            constraints,
            prior_art,
            approach_context,
            _approach_snapshot(partial_tree),
        )
        failure = _technical_approach_step_failure("generate_candidates", candidates)
        if failure:
            return failure
        steps["generate_candidates"] = candidates
        partial_tree["problem"]["question"] = _partial_question_tree(candidates=candidates)
        mark_step_completed("generate_candidates", started_at, partial_tree)

        started_at = start_step()
        evaluations = _evaluate_candidates(
            candidates,
            normalized,
            constraints,
            approach_context,
            _approach_snapshot(partial_tree),
        )
        failure = _technical_approach_step_failure("evaluate_candidates", evaluations)
        if failure:
            return failure
        steps["evaluate_candidates"] = evaluations
        partial_tree["problem"]["question"] = _partial_question_tree(candidates=candidates, evaluations=evaluations)
        mark_step_completed("evaluate_candidates", started_at, partial_tree)

        started_at = start_step()
        selected = _select_approach(
            candidates,
            evaluations,
            constraints,
            approach_context,
            _approach_snapshot(partial_tree),
        )
        failure = _technical_approach_step_failure("select_approach", selected)
        if failure:
            return failure
        steps["select_approach"] = selected
        partial_tree["problem"]["question"] = _partial_question_tree(
            candidates=candidates,
            evaluations=evaluations,
            selected=selected,
        )
        mark_step_completed("select_approach", started_at, partial_tree)
        source = "generated"
        _fill_ai_deliberated_tech_stack(rfc_view, selected, candidates)
    else:
        started_at = start_step()
        steps["provided_approach"] = _json_safe(effective_provided_approach)
        candidates = None
        evaluations = None
        selected = _provided_approach_selection(effective_provided_approach)
        steps["select_approach"] = selected
        partial_tree["problem"]["question"] = _partial_question_tree(
            selected=selected,
            provided_approach=effective_provided_approach,
        )
        mark_step_completed("select_approach", started_at, partial_tree)
        source = "requester_provided_refined"

    started_at = start_step()
    implementation = _implementation_strategy(
        selected,
        prior_art,
        constraints,
        rfc_view,
        repo_path,
        approach_context,
        _approach_snapshot(partial_tree),
    )
    failure = _technical_approach_step_failure("implementation_strategy", implementation)
    if failure:
        return failure
    steps["implementation_strategy"] = implementation
    partial_tree["problem"]["question"] = _partial_question_tree(
        candidates=candidates,
        evaluations=evaluations,
        selected=selected,
        implementation=implementation,
        provided_approach=effective_provided_approach,
    )
    mark_step_completed("implementation_strategy", started_at, partial_tree)

    started_at = start_step()
    patch_plan = _right_size_patch_plan(
        selected,
        implementation,
        constraints,
        approach_context,
        _approach_snapshot(partial_tree),
    )
    failure = _technical_approach_step_failure("right_size_patch_plan", patch_plan)
    if failure:
        return failure
    steps["right_size_patch_plan"] = patch_plan
    partial_tree["problem"]["question"] = _partial_question_tree(
        candidates=candidates,
        evaluations=evaluations,
        selected=selected,
        implementation=implementation,
        patch_plan=patch_plan,
        provided_approach=effective_provided_approach,
    )
    mark_step_completed("right_size_patch_plan", started_at, partial_tree)

    started_at = start_step()
    risks = _surface_risks(
        selected,
        implementation,
        patch_plan,
        constraints,
        approach_context,
        _approach_snapshot(partial_tree),
    )
    failure = _technical_approach_step_failure("surface_risks", risks)
    if failure:
        return failure
    risks = _with_requester_stack_org_profile_risk(risks, rfc_view, selected)
    steps["surface_risks"] = risks

    risk_target_failure = _validate_risk_targets(risks, candidates, selected)
    if risk_target_failure:
        return {
            "ok": False,
            "error": risk_target_failure,
            "failed_step": "surface_risks",
        }

    partial_tree["problem"]["question"] = _question_tree(
        selected,
        candidates,
        evaluations,
        implementation,
        patch_plan,
        risks,
        [],
        provided_approach=effective_provided_approach,
    )
    mark_step_completed("surface_risks", started_at, partial_tree)

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
            patch_plan,
            risks,
            source,
            provided_approach=effective_provided_approach,
            rfc_view=rfc_view,
        ),
        "steps": steps,
    }


def _ground_with_contract(repo: str | Path, rfc_view: dict[str, Any]) -> GroundingResult:
    best_grounding: GroundingResult | None = None
    previous_violations: list[str] = []
    for attempt in range(MAX_REGROUNDS + 1):
        grounding = _ground_request(repo, rfc_view, previous_violations if attempt else None)
        best_grounding = grounding
        verification = _verify_grounding(rfc_view, grounding.rfc_view, grounding)
        violations = list(verification["violations"])
        if not violations:
            return grounding
        previous_violations = violations

    assert best_grounding is not None
    return GroundingResult(
        best_grounding.rfc_view,
        _with_unresolved_violations(best_grounding.grounding_notes, previous_violations),
        False,
        best_grounding.assumptions
        + ["Grounding contract violations remain unresolved after verification."],
        best_grounding.questions + ["Can you confirm or correct the proposed RFC interpretation?"],
        previous_violations,
    )


def _ground_request(
    repo: str | Path,
    rfc_view: dict[str, Any],
    previous_violations: list[str] | None = None,
) -> GroundingResult:
    """Research and correct a rough request before it becomes an RFC branch."""
    prompt = _grounding_prompt(rfc_view, previous_violations)
    temp_dir = Path(tempfile.mkdtemp(prefix="ai-org-rfc-grounding-"))
    schema_file = temp_dir / "rfc-grounding.schema.json"
    out_file = temp_dir / "grounded-rfc.json"
    try:
        schema_file.write_text(json.dumps(GROUNDING_SCHEMA, indent=2), encoding="utf-8")
        cmd = [
            "codex",
            "exec",
            "--sandbox",
            "read-only",
            "-C",
            str(repo),
            "-o",
            str(out_file),
            "--enable",
            "web_search",
            "--output-schema",
            str(schema_file),
            prompt,
        ]
        try:
            completed = subprocess.run(
                cmd,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
            )
        except OSError as exc:
            return _grounding_fail_closed(rfc_view, f"Grounding failed: {exc}")

        if completed.returncode != 0:
            detail = completed.stderr.strip() or (
                "no output file" if not out_file.exists() else "Codex grounding did not complete successfully."
            )
            return _grounding_fail_closed(rfc_view, f"Grounding failed: {detail}")
        if not out_file.exists():
            return _grounding_fail_closed(rfc_view, "Grounding failed: no output file")
        return _parse_grounding_result(out_file.read_text(encoding="utf-8"), rfc_view)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _grounding_prompt(rfc_view: dict[str, Any], previous_violations: list[str] | None = None) -> str:
    # Grounding faithfully renders the request: the specific named thing at full scope.
    # It never generalizes to the category, never shrinks scope, and is not the legal
    # department: no IP, trademark, or copyright analysis.
    reground_instruction = ""
    if previous_violations:
        reground_instruction = (
            "Your previous grounding violated the executable grounding contract. Fix these violations and "
            "re-render the RFC without repeating them:\n"
            + "\n".join(f"- {violation}" for violation in previous_violations)
            + "\n\n"
        )

    registry_text = _field_registry_prompt()
    return (
        "You are the RFC intake grounding step for AI Org.\n"
        "Your job is to turn a rough, vague, or even wrong request into the right well-grounded RFC view "
        "before an RFC branch is created.\n\n"
        + reground_instruction
        + "Use web search when the request names or implies a real product, game, genre, paper, standard, "
        "company, library, tool, or other external reference. Determine what the reference actually is, "
        "including its concrete defining signatures, core mechanics, interaction rhythms, structure, tone, "
        "progression cadence, characteristic look, conventions, and prior art. Correct misconceptions in the "
        "request. Also inspect the target repository read-only to identify existing code, patterns, "
        "constraints, and affected areas. Do not modify files.\n\n"
        "Faithfully render the request's specific identity. When the request names a specific thing, ground "
        "down to that named thing and preserve its full identity in proposed_rfc, including the title; never "
        "generalize up to a broad category, genre, archetype, or safer-sounding substitute. proposed_rfc must "
        "read as 'faithfully reproduce <the specific named thing>', and a reviewer should be able to tell it "
        "apart from a generic genre entry. Do not turn 'make an elephant' into 'make a mammal'. Do not rename "
        "a named thing as 'an original X-style' work.\n\n"
        "Preserve the request's full scope. Do not reduce the request to a vertical slice, short demo, one "
        "area, 10-minute experience, prototype, MVP, first iteration, or other smaller deliverable. Commit "
        "the proposed_rfc to the complete requested deliverable at the fidelity implied by the named thing. "
        "Downstream phases may decompose and iterate toward that full scope; intake must not hand down a "
        "smaller goal. Do not put start-small, minimal, slice, demo, or MVP hedging in proposed_rfc or "
        "assumptions.\n\n"
        "Grounding is not legal review. Do not perform IP, trademark, copyright, or licensing risk analysis; "
        "do not add legal disclaimers; do not spend proposed_rfc, assumptions, or grounding_notes on legal "
        "concerns. Do not avoid perceived IP risk by renaming, generalizing, or shrinking the named thing. "
        "Your job here is only to understand and faithfully render what to build.\n\n"
        "Do not deliberate or choose the technical stack in grounding. proposed_rfc.tech_stack.provenance may "
        "only be requester_specified when the original raw_request or proposal_hint explicitly names that stack "
        "(for example Godot, React, Unreal, Unity, Python, or a concrete platform), or unspecified otherwise. "
        "Never set provenance to ai_deliberated in grounding. When provenance is unspecified, leave "
        "build_strategy, engine, framework, language, platform, and rationale empty. If research shows the named "
        "domain, franchise, or product currently uses a stack, preserve that as evidence in background_facts, not as a "
        "tech_stack decision. For example, 'modern mainline Dragon Quest ships on Unreal Engine' belongs in "
        "background_facts unless the requester explicitly asked for Unreal.\n\n"
        "Default to the latest or current version, conventions, and best practices of the named thing unless "
        "the request explicitly asks for a retro, classic, old, vintage, specific past version, or specific "
        "past year target. This applies across domains: games should target the current experience, modern "
        "graphics, scope, and conventions; security should target current standards and patches; SaaS should "
        "use current stacks and practices; libraries should use current APIs. Do not gratuitously target an "
        "outdated incarnation when the latest sensible target is available.\n\n"
        "Always do the research and commit to the most-likely interpretation as proposed_rfc, even when the "
        "request is ambiguous or under-specified. Do not send back blank open questions instead of grounding. "
        "If you are confident, set confident=true and make proposed_rfc the grounded registry-shaped RFC view "
        "that should be promoted. If you are not fully confident, set confident=false and still return the "
        "best-guess grounded registry-shaped proposed_rfc for requester confirmation: this is what I think you "
        "mean, right? "
        "List the specific inferences you made in assumptions, each phrased so the requester can confirm or "
        "correct it, such as 'I assumed X because <research>'. Reserve questions only for gaps that research "
        "genuinely cannot resolve after you have inferred the most likely interpretation; questions is usually "
        "empty. grounding_notes must briefly state what you researched, what you corrected, and cite web "
        "references when used.\n\n"
        "Fill proposed_rfc from this registry. Each field's must_not is an anti-dumping gate: content matching "
        "must_not belongs elsewhere or nowhere. Research prose and audit trail go in grounding_provenance, not "
        "requirement fields; bounded domain facts go in background_facts; requester solution ideas go in "
        "proposal_hint; genuine blockers go in open_questions. Every field marked required_at=rfc_handoff "
        "must be filled with a non-empty value when it is a string. In particular, produce a concise "
        "working_title derived from the request, such as a short noun phrase naming the deliverable.\n\n"
        f"{registry_text}\n"
        + _format_rfc("Current request registry view", _rfc_to_view(rfc_view))
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
    rfc_view: dict[str, Any],
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
        "Use the grounded registry RFC view and repository context only to restate the problem clearly. "
        "Do not propose an implementation approach, alternatives, patch plan, reviewer decision, or later "
        "Technical Approach steps.\n\n"
        "Write English only. Distill problem and current_inadequacy; do not copy any RFC field verbatim. "
        "Derive success criteria from the RFC intent, but do not copy RFC feature-list bullets as criteria.\n\n"
        "Return these fields:\n"
        "- problem: the core problem, restated crisply.\n"
        "- affected: affected users, operators, contributors, systems, modules, or workflows.\n"
        "- current_inadequacy: what is missing or where the current state falls short.\n"
        "- success_criteria: measurable nested objects. Each criterion must include actor, capability "
        "{action, preconditions}, verifiable_outcome {expected_state, evidence}, and verification "
        "{method, check}. action must be observable; preconditions must be concrete; expected_state must "
        "name the end state that proves success; evidence must state what is observed or measured; method "
        "must be automated_test, manual_check, or metric; check must be the concrete verification performed.\n"
        "- non_goals: explicit boundaries that should remain out of scope for this RFC.\n\n"
        + _format_rfc("Grounded registry RFC view", _rfc_to_view(rfc_view))
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
    rfc_view: dict[str, Any],
    repo: Path,
    context: Mapping[str, Any] | None = None,
    approach: Mapping[str, Any] | None = None,
    feedback: list[str] | None = None,
) -> str:
    context_text = json.dumps(context or {}, indent=2, sort_keys=True, default=str)
    org_profile_text = json.dumps(ORG_BUILDER_PROFILE, indent=2, sort_keys=True, ensure_ascii=True)
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
        "Inspect the repository read-only as needed from the configured repo root. Use the grounded rfc field registry "
        "RFC view, accumulated approach document, repository architecture, and supplied context to identify "
        "constraints only. Do not propose candidate approaches, select an approach, create a patch plan, or "
        "perform later Technical Approach steps.\n\n"
        "This step must attach one nested-tag branch to the accumulated approach document. The accumulated "
        "document currently contains step 1 as approach.normalized_problem. Derive constraints from that branch, "
        "including nested success_criteria, and from repository facts. Treat a success criterion's required "
        "observable result as a hard constraint when later approaches could violate it. For example, a criterion "
        "that save reload restores identical state implies a persistence and versioning constraint; a criterion "
        "that battle resolution follows commands and stats implies a deterministic battle-state constraint. Use "
        "the raw RFC only as grounding and ambiguity context, not as an independent restart of problem discovery.\n\n"
        "Merge ORG_BUILDER_PROFILE as standing org facts, not as per-run preferences. These are hard "
        "constraints for unspecified stacks and must flow into candidate generation, evaluation, and selection. "
        "Use derivation.from=org_builder_profile when you echo them. If tech_stack.provenance is "
        "requester_specified, do not use these facts to reject the requested stack; conflicts are non-blocking "
        "risks surfaced later.\n\n"
        "Write English only. Extract two lists:\n"
        "- hard_constraints: must-satisfy constraints that later approaches cannot violate.\n"
        "- soft_preferences: nice-to-have preferences that should influence trade-offs but may be outweighed.\n\n"
        "Each hard constraint item must include statement, derivation {from, trace}, and implication {must, "
        "must_not}. Each soft preference item must include statement, derivation {from, trace}, and rationale. "
        "Set derivation.from to exactly one of: problem, success_criteria, non_goals, repo, domain, "
        "org_builder_profile. If a "
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
        + _format_rfc("Grounded registry RFC view", _rfc_to_view(rfc_view))
        + f"\nORG_BUILDER_PROFILE:\n{org_profile_text}\n"
        + f"\nRoot success_criteria from step 1:\n{goals_text}\n"
        + f"\nAccumulated approach so far:\n{approach_text}\n"
        + f"\nRepository root:\n{repo}\n"
        + f"\nContext:\n{context_text}\n"
        + feedback_text
        + "\nReturn only JSON matching the provided schema."
    )


def _prior_art_map_prompt(
    rfc_view: dict[str, Any],
    repo: Path,
    concepts: list[str],
    reference_facets: list[dict[str, Any]],
    context: Mapping[str, Any] | None = None,
    approach: Mapping[str, Any] | None = None,
    feedback: list[str] | None = None,
) -> str:
    context_text = json.dumps(context or {}, indent=2, sort_keys=True, ensure_ascii=True, default=str)
    approach_text = json.dumps(approach or {}, indent=2, sort_keys=True, ensure_ascii=True, default=str)
    goals_text = _root_success_criteria_text(approach)
    concepts_text = json.dumps(concepts, indent=2, ensure_ascii=True)
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
        "Inspect the repository read-only as needed from the configured repo root. Use the grounded rfc field registry "
        "RFC view, accumulated approach document, Reference design facets, Reference implementation facets, "
        "and repository context to synthesize 3 to 6 prior-art patterns. Do not generate candidate approaches, "
        "select an approach, create a patch plan, or perform later Technical Approach steps.\n\n"
        "This step must attach one nested-tag branch to the accumulated approach document. The accumulated "
        "document currently contains approach.normalized_problem and approach.constraints. Trace every pattern "
        "to the normalized problem or constraints it addresses. Use the raw RFC only as grounding and ambiguity "
        "context, not as an independent restart of problem discovery.\n\n"
        "Each pattern must identify a real design or implementation pattern visible in the Reference facets, "
        "the repository, or both. Treat frameworks, engines, and libraries as candidates judged on fit; do not "
        "favor them merely because they appeared often. A candidate such as Godot belongs here only when the "
        "evidence makes it relevant, and its disposition must be adopt, adapt, or reject on merit.\n\n"
        "Be honest about Reference coverage per concept. If Reference facets were returned for a concept, use "
        "their structure, rationale, when_to_use, tradeoffs, and implementation_hooks in at least one pattern "
        "when relevant, and set source.reference_concept to that concept with source.facet_kind design or "
        "implementation. Do not say Reference facets are absent for a concept that appears in the retrieved "
        "facets. Set source.facet_kind to none only for patterns derived solely from the RFC or repository, or "
        "for concepts that genuinely returned no Reference entry.\n\n"
        "For each pattern:\n"
        "- name: concise name of the prior-art pattern or candidate.\n"
        "- source: nested object with reference_concept, facet_kind, and where. facet_kind must be design, "
        "implementation, or none.\n"
        "- when_applies: conditions that make the pattern appropriate.\n"
        "- tradeoffs: nested object with pros and cons arrays; include concrete benefits, costs, and failure "
        "modes.\n"
        "- disposition: nested object with choice and why. choice must be adopt, adapt, or reject for this RFC.\n"
        "- traces_to: approach elements this pattern addresses, such as normalized_problem.success_criteria[0] "
        "or constraints.hard_constraints[0].\n\n"
        + _format_rfc("Grounded registry RFC view", _rfc_to_view(rfc_view))
        + f"\nRepository root:\n{repo}\n"
        + f"\nRoot success_criteria from step 1:\n{goals_text}\n"
        + f"\nAccumulated approach so far:\n{approach_text}\n"
        + f"\nContext:\n{context_text}\n"
        + f"\nReference key concepts queried:\n{concepts_text}\n"
        + f"\nReference facets read before this call:\n{reference_text}\n"
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
) -> str:
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
            "\nPrevious output failed deterministic empty-slot validation. Regenerate this candidate node and fix "
            "these issues:\n"
            + "\n".join(f"- {item}" for item in feedback)
            + "\n"
        )
    return (
        "You are forming step 4 of AI Org's Technical Approach derivation tree: generate one candidate node.\n"
        "Use the accumulated approach tree containing the root goals, constraints, and prior-art ancestors, "
        "plus read-only repository inspection from the configured repo root. Do not select a winner, evaluate candidates, write an implementation strategy, "
        "create a patch plan, or perform later Technical Approach steps. Do not modify files.\n\n"
        "If rfc.tech_stack.provenance is unspecified, engine/framework/platform selection is a first-class "
        "candidate axis: compare available engines, frameworks, platform targets, and a from-scratch option "
        "only when justified against those available options. Do not use from_scratch as a silent default.\n\n"
        "ORG_BUILDER_PROFILE constraints in the constraints tree are hard candidate-level filters for "
        "unspecified stacks. Do not generate candidates whose primary construction path requires GUI-editor "
        "workflow, binary asset authoring, interactive engine editors, heavyweight native build pipelines, or "
        "non-headless verification. External fidelity precedent from background_facts may explain what the "
        "franchise or domain uses, but it is evidence only; it cannot rescue a candidate the org cannot author "
        "or verify.\n\n"
        f"Return exactly one {candidate_kind} candidate. Make it distinct from existing candidates.\n\n"
        "For each candidate:\n"
        "- id: stable lowercase identifier using letters, numbers, underscores, or hyphens.\n"
        "- name: concise approach name.\n"
        "- kind: exactly one of minimal_local, repo_native, general_architectural, do_nothing_defer.\n"
        "- summary: what this approach would do and why it is materially different.\n"
        "- first_playable_moment: name the player actions, locations, enemies, items or spells, and the "
        "win or progress condition that would make the first slice playable or inspectable.\n"
        "- core_systems: concrete gameplay, workflow, repository, or runtime systems this candidate would change.\n"
        "- draws_on: prior-art pattern names, repository references, or Reference entries this candidate builds on.\n\n"
        f"Normalized problem:\n{normalized_problem_text}\n"
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
        "one candidate. Do not write an implementation strategy, create a patch plan, surface open questions, "
        "emit the final Technical Approach section, or perform later Technical Approach steps. Do not modify "
        "files.\n\n"
        "The decision must be a reasoned selection from the evaluation nodes, not a one-shot claim. Use Toulmin "
        "arguments: claim, grounds, warrant, backing, and rebuttal. Include support for the selected candidate "
        "and objections for rejected candidates.\n\n"
        "Return these fields:\n"
        "- selected_candidate_id: the exact id of the selected candidate.\n"
        "- arguments: support or objection Toulmin arguments about candidate ids.\n"
        "- rationale: because, under_constraints, and accepting_tradeoffs arrays.\n"
        "- stack_axes: five non-empty nested records for stack reasoning. fidelity_precedent is what "
        "background_facts or prior art says external exemplars use; builder_buildability is whether Codex CLI "
        "agents can author the artifact as text in a worktree and functional_check can verify it headlessly; "
        "asset_supply is whether the org's 2D vector/SVG asset path can supply needed assets; "
        "distribution_reachability is whether intended users can reach the deliverable without heavyweight "
        "installs; licensing_cost is license, commercial tooling, and CI cost exposure. Each axis must include "
        "evidence and judgment. Fidelity precedent is evidence only and must not override hard builder "
        "constraints. If no stack was specified by the requester, this is where the deliberation records why "
        "the selected feasible stack wins. If from_scratch is selected, explicitly justify it against available "
        "engine and framework options.\n"
        "- rejected: one item for each non-chosen candidate with candidate_id and objection.\n\n"
        f"Candidate approaches:\n{candidates_text}\n"
        f"\nEvaluation matrix:\n{evaluations_text}\n"
        f"\nConstraints:\n{constraints_text}\n"
        f"\nRoot success_criteria from step 1:\n{goals_text}\n"
        f"\nAccumulated approach so far:\n{approach_text}\n"
        f"\nContext:\n{context_text}\n"
        + feedback_text
        + "\nReturn only JSON matching the provided schema."
    )


def _implementation_strategy_prompt(
    chosen: Mapping[str, Any],
    prior_art_map: Mapping[str, Any],
    constraints: Mapping[str, Any],
    rfc_view: dict[str, Any],
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
        "evaluations, and selected decision, the grounded registry RFC view, and read-only repository inspection from the configured repo root. "
        "You may inspect module structure and tests to make the strategy concrete. Do not modify files.\n\n"
        "Expand only the chosen approach into an implementation strategy. Do not generate or re-evaluate "
        "alternatives, create a patch slice plan, surface risks and open questions, emit the final Technical "
        "Approach section, or perform later Technical Approach steps.\n\n"
        "Return these fields:\n"
        "- systems: each system has system_name, behavior_in_game, named_content with entities and "
        "content_items, and key_modules. Tie named content and system behavior to specific root goals where natural.\n"
        "- persistence: saved_fields that must survive reload or handoff; name the field or state explicitly and align it with any success criterion that requires durable state.\n\n"
        + _format_rfc("Grounded registry RFC view", _rfc_to_view(rfc_view))
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
        "You are forming step 8 of AI Org's Technical Approach derivation tree: right-size the patch plan.\n"
        "Use the accumulated approach tree containing the root goals, constraints, selected decision, and implementation strategy, "
        "plus read-only repository inspection from the configured repo root. Do not modify files.\n\n"
        "Turn the implementation strategy into a right-sized, incremental patch plan. Every slice must leave "
        "the system working and should enable behavior incrementally. The first_playable node must be the first safe "
        "playable or inspectable slice. Apply YAGNI: defer speculative work unless a deferred decision is hard to "
        "reverse or affects major quality attributes such as security, reliability, compatibility, performance, "
        "operability, or maintainability. Do not surface risks and open questions, emit the final Technical "
        "Approach section, or perform later Technical Approach steps.\n\n"
        "Return these fields:\n"
        "- first_playable: player_can, named_content locations/enemies/items_or_spells, win_or_progress_condition, "
        "and how_verified. Include per-item trace text in how_verified where natural so each verification maps to a specific success criterion.\n"
        "- follow_ups: later additions, each with adds and named_content.\n"
        "- deferred: intentionally deferred work, each with item and why_safe_to_defer.\n"
        f"Chosen approach:\n{chosen_text}\n"
        f"\nImplementation strategy:\n{strategy_text}\n"
        f"\nConstraints:\n{constraints_text}\n"
        f"\nRoot success_criteria from step 1:\n{goals_text}\n"
        f"\nAccumulated approach so far:\n{approach_text}\n"
        f"\nContext:\n{context_text}\n"
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
    feedback: list[str] | None = None,
) -> str:
    chosen_text = json.dumps(chosen, indent=2, sort_keys=True, ensure_ascii=True, default=str)
    strategy_text = json.dumps(implementation_strategy, indent=2, sort_keys=True, ensure_ascii=True, default=str)
    patch_plan_text = json.dumps(patch_plan, indent=2, sort_keys=True, ensure_ascii=True, default=str)
    constraints_text = json.dumps(constraints, indent=2, sort_keys=True, ensure_ascii=True, default=str)
    context_text = json.dumps(context or {}, indent=2, sort_keys=True, ensure_ascii=True, default=str)
    approach_text = json.dumps(accumulated_approach or {}, indent=2, sort_keys=True, ensure_ascii=True, default=str)
    goals_text = _root_success_criteria_text(accumulated_approach)
    feedback_text = ""
    if feedback:
        feedback_text = (
            "\nPrevious output failed deterministic empty-slot validation. Regenerate this risks node and fix "
            "these issues:\n"
            + "\n".join(f"- {item}" for item in feedback)
            + "\n"
        )
    return (
        "You are forming step 9 of AI Org's Technical Approach derivation tree: surface risks and open "
        "questions.\n"
        "Use the accumulated approach tree containing the root goals, constraints, selected decision, implementation strategy, and right-sized patch "
        "plan, plus read-only repository inspection from the configured "
        "repo root. Do not modify files.\n\n"
        "Surface only delivery or design risks that can be attached to a candidate, decision, or implementation "
        "node. Do not change the chosen approach, rewrite the implementation strategy, create new patch "
        "slices, emit the final Technical Approach section, or perform later Technical Approach steps.\n\n"
        "Requester sovereignty exception: when the accumulated decision came from a requester-specified "
        "tech_stack, ORG_BUILDER_PROFILE conflicts are non-blocking risks. Do not reject or replace the "
        "requested stack; attach any buildability, asset, reachability, or licensing conflict to the decision "
        "or implementation node.\n\n"
        "Return these fields:\n"
        "- risks: each risk node has id, risk, mitigation, attaches_to candidate|decision|implementation, and "
        "target_id naming the node it attaches under. State which success criterion or decision the risk threatens where natural.\n\n"
        f"Chosen approach:\n{chosen_text}\n"
        f"\nImplementation strategy:\n{strategy_text}\n"
        f"\nPatch plan:\n{patch_plan_text}\n"
        f"\nConstraints:\n{constraints_text}\n"
        f"\nRoot success_criteria from step 1:\n{goals_text}\n"
        f"\nAccumulated approach so far:\n{approach_text}\n"
        f"\nContext:\n{context_text}\n"
        + feedback_text
        + "\nReturn only JSON matching the provided schema."
    )


def _prior_art_key_concepts(
    rfc_view: dict[str, Any],
    context: Mapping[str, Any] | None = None,
    approach: Mapping[str, Any] | None = None,
) -> list[str]:
    concepts: list[str] = []
    context_values = dict(context or {})
    reference_context = _reference_stack_context(context_values)
    for field in ("reference_terms", "key_concepts", "concepts", "terms"):
        _extend_concepts(concepts, context_values.get(field))

    extraction_text = (
        _format_rfc("Grounded registry RFC view", _rfc_to_view(rfc_view))
        + "\nAccumulated approach:\n"
        + json.dumps(approach or {}, indent=2, sort_keys=True, ensure_ascii=True, default=str)
    )
    extracted = reference._extract_reference_terms(extraction_text, reference_context)
    _extend_concepts(concepts, extracted)

    if not concepts:
        _add_concept(concepts, rfc_view.get("working_title", ""))
        _add_concept(concepts, rfc_view.get("affected_area_platform", ""))
        _extend_concepts(concepts, _explicit_terms(_rfc_text(rfc_view)))
        if approach:
            _extend_concepts(concepts, _explicit_terms(json.dumps(approach, sort_keys=True, default=str)))

    if len(concepts) < 6:
        _extend_concepts(concepts, _important_phrases(_rfc_text(rfc_view)))
    return concepts[:12]


def _reference_terms_from_build_result(reference_build: Mapping[str, Any]) -> list[str]:
    processed = reference_build.get("processed_terms")
    terms = _reference_terms_for_prior_art(processed)
    if terms:
        return terms
    return _reference_terms_for_prior_art(reference_build.get("terms"))


def _reference_terms_for_prior_art(values: Any) -> list[str]:
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


def _read_prior_art_reference_facets(
    concepts: list[str],
    context: Mapping[str, Any] | None = None,
    *,
    allow_expand: bool = True,
) -> list[dict[str, Any]]:
    reference_context = _reference_stack_context(context)
    facets_by_index: dict[int, dict[str, Any]] = {}
    already_present: list[str] = []
    missing_indexes: list[int] = []
    researched: list[str] = []
    empty_after_research: list[str] = []
    skipped_after_cap: list[str] = []
    failed: dict[str, str] = {}

    initial = _lookup_prior_art_reference_facets_for_concepts(concepts, reference_context)
    for index, term in enumerate(concepts):
        term_facets = initial.get(index)
        if term_facets is None:
            term_facets = _failed_prior_art_reference_facets(term, "lookup worker did not return")
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

    researched_facets = _research_prior_art_reference_facets_for_concepts(
        [(index, concepts[index]) for index in research_indexes],
        reference_context,
    )
    for index in research_indexes:
        term = concepts[index]
        term_facets = researched_facets.get(index)
        if term_facets is None:
            term_facets = _failed_prior_art_reference_facets(term, "research worker did not return")
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
        "RFC prior-art Reference facets: already_present=%s researched=%s empty_after_research=%s "
        "skipped_after_cap=%s failed=%s expansion_cap=%s",
        already_present,
        researched,
        empty_after_research,
        skipped_after_cap,
        failed,
        MAX_PRIOR_ART_REFERENCE_EXPANSIONS,
    )
    return facets


def _lookup_prior_art_reference_facets_for_concepts(
    concepts: list[str],
    reference_context: Mapping[str, Any],
) -> dict[int, dict[str, Any]]:
    parallelism = reference._reference_parallelism(len(concepts))
    if parallelism <= 1:
        return {
            index: _lookup_prior_art_reference_facets_safely(term, reference_context)
            for index, term in enumerate(concepts)
        }

    outcomes: dict[int, dict[str, Any]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=parallelism) as executor:
        futures = {
            executor.submit(_lookup_prior_art_reference_facets_safely, term, reference_context): index
            for index, term in enumerate(concepts)
        }
        for future in concurrent.futures.as_completed(futures):
            outcomes[futures[future]] = future.result()
    return outcomes


def _research_prior_art_reference_facets_for_concepts(
    indexed_concepts: list[tuple[int, str]],
    reference_context: Mapping[str, Any],
) -> dict[int, dict[str, Any]]:
    parallelism = reference._reference_parallelism(len(indexed_concepts))
    if parallelism <= 1:
        return {
            index: _research_prior_art_reference_facets_safely(term, reference_context)
            for index, term in indexed_concepts
        }

    outcomes: dict[int, dict[str, Any]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=parallelism) as executor:
        futures = {
            executor.submit(_research_prior_art_reference_facets_safely, term, reference_context): index
            for index, term in indexed_concepts
        }
        for future in concurrent.futures.as_completed(futures):
            outcomes[futures[future]] = future.result()
    return outcomes


def _lookup_prior_art_reference_facets_safely(
    term: str,
    reference_context: Mapping[str, Any],
) -> dict[str, Any]:
    try:
        return _lookup_prior_art_reference_facets(term, reference_context)
    except Exception as exc:
        return _failed_prior_art_reference_facets(term, _format_prior_art_reference_error(exc))


def _research_prior_art_reference_facets_safely(
    term: str,
    reference_context: Mapping[str, Any],
) -> dict[str, Any]:
    try:
        expanded = reference.expand(term, reference_context)
        term_facets = _lookup_prior_art_reference_facets(term, reference_context)
        if not term_facets["design"]:
            term_facets["design"] = _trim_expanded_reference_candidates(expanded, "design")
        if not term_facets["implementation"]:
            term_facets["implementation"] = _trim_expanded_reference_candidates(expanded, "implementation")
        return term_facets
    except Exception as exc:
        return _failed_prior_art_reference_facets(term, _format_prior_art_reference_error(exc))


def _failed_prior_art_reference_facets(term: str, error: str) -> dict[str, Any]:
    return {"term": term, "design": [], "implementation": [], "status": "failed", "error": error}


def _format_prior_art_reference_error(exc: BaseException) -> str:
    message = str(exc).strip()
    if message:
        return f"{type(exc).__name__}: {message}"
    return type(exc).__name__


def _reference_stack_context(context: Mapping[str, Any] | None) -> dict[str, str]:
    context_values = dict(context or {})
    stack = context_values.get("stack")
    if isinstance(stack, Mapping):
        context_values = {**context_values, **stack}
    return {
        key: str(value)
        for key in ("language", "environment", "version")
        if (value := context_values.get(key)) is not None and str(value).strip()
    }


def _lookup_prior_art_reference_facets(term: str, reference_context: Mapping[str, Any]) -> dict[str, Any]:
    term_facets: dict[str, Any] = {"term": term, "design": [], "implementation": []}
    for kind in ("design", "implementation"):
        lookup = reference.lookup(term, reference_context, kind=kind)
        candidates = lookup.get("candidates", []) if isinstance(lookup, dict) else []
        term_facets[kind] = _trim_reference_candidates(candidates)
    return term_facets


def _trim_expanded_reference_candidates(expanded: Any, kind: str) -> list[dict[str, str]]:
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
    return _trim_reference_candidates(matching)


def _trim_reference_candidates(candidates: Any) -> list[dict[str, str]]:
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


def _attach_prior_art_reference_facets(
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


def _rfc_text(rfc_view: dict[str, Any]) -> str:
    parts: list[str] = []
    for field in RFC_VIEW_FIELDS:
        value = rfc_view.get(field)
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
    return {
        "actor": actor,
        "capability": {"action": action, "preconditions": list(preconditions)},
        "verifiable_outcome": {"expected_state": expected_state, "evidence": evidence},
        "verification": {"method": method, "check": check},
    }


def _lint_normalized_problem(parsed: Mapping[str, Any], rfc_view: Mapping[str, Any]) -> list[str]:
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
    return errors


NON_EMPTY_ARRAY_SLOT_NAMES = frozenset(
    {
        "success_criteria",
        "preconditions",
        "player_actions",
        "locations",
        "enemies",
        "items_or_spells",
        "core_systems",
        "draws_on",
        "because",
        "under_constraints",
        "accepting_tradeoffs",
        "systems",
        "entities",
        "content_items",
        "key_modules",
        "saved_fields",
        "player_can",
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


def _normalized_rfc_phrases(rfc_view: Mapping[str, Any]) -> set[str]:
    phrases: set[str] = set()
    for field in RFC_VIEW_FIELDS:
        value = rfc_view.get(field)
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


def _merge_org_builder_constraints(parsed: Mapping[str, Any]) -> dict[str, Any]:
    hard = [dict(item) for item in parsed.get("hard_constraints", []) if isinstance(item, Mapping)]
    soft = [dict(item) for item in parsed.get("soft_preferences", []) if isinstance(item, Mapping)]
    existing_traces = {
        item.get("derivation", {}).get("trace")
        for item in hard
        if isinstance(item.get("derivation"), Mapping)
        and item.get("derivation", {}).get("from") == "org_builder_profile"
    }
    for item in ORG_BUILDER_PROFILE:
        trace = item["trace"]
        if trace in existing_traces:
            continue
        hard.append(
            {
                "statement": item["statement"],
                "derivation": {"from": "org_builder_profile", "trace": trace},
                "implication": {"must": item["must"], "must_not": item["must_not"]},
            }
        )
    return {"hard_constraints": hard, "soft_preferences": soft}


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


ORG_BUILDER_DISALLOWED_STACK_MARKERS = (
    "unreal",
    "unity",
    "maya",
    "blender",
    "3d dcc",
    "visual studio solution",
    "xcode project",
    "native ios",
    "native android",
    "heavyweight native build",
    "interactive editor",
)


def _lint_candidate_against_org_builder(candidate: Mapping[str, Any], constraints: Mapping[str, Any]) -> list[str]:
    if not _has_org_builder_constraints(constraints):
        return []
    text = json.dumps(candidate, sort_keys=True, ensure_ascii=True).lower()
    hits = [marker for marker in ORG_BUILDER_DISALLOWED_STACK_MARKERS if marker in text]
    if not hits:
        return []
    return [
        "candidate conflicts with ORG_BUILDER_PROFILE hard constraints and must be pruned or rewritten: "
        + ", ".join(hits)
    ]


def _has_org_builder_constraints(constraints: Mapping[str, Any]) -> bool:
    for item in constraints.get("hard_constraints", []) if isinstance(constraints, Mapping) else []:
        if not isinstance(item, Mapping):
            continue
        derivation = item.get("derivation")
        if isinstance(derivation, Mapping) and derivation.get("from") == "org_builder_profile":
            return True
    return False


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
        if not isinstance(pattern, dict):
            return _prior_art_error("Codex prior-art mapping returned invalid pattern item")
        if set(pattern) != set(PRIOR_ART_PATTERN_FIELDS):
            return _prior_art_error("Codex prior-art mapping returned invalid pattern fields")
        source = _parse_prior_art_source(pattern.get("source"))
        if source is None:
            return _prior_art_error("Codex prior-art mapping returned invalid source")
        tradeoffs = _parse_prior_art_tradeoffs(pattern.get("tradeoffs"))
        if tradeoffs is None:
            return _prior_art_error("Codex prior-art mapping returned invalid tradeoffs")
        disposition = _parse_prior_art_disposition(pattern.get("disposition"))
        if disposition is None:
            return _prior_art_error("Codex prior-art mapping returned invalid disposition")
        traces_to = pattern.get("traces_to")
        if not isinstance(traces_to, list) or not all(isinstance(item, str) for item in traces_to):
            return _prior_art_error("Codex prior-art mapping returned invalid traces_to")
        name = pattern.get("name")
        when_applies = pattern.get("when_applies")
        if not isinstance(name, str) or not isinstance(when_applies, str):
            return _prior_art_error("Codex prior-art mapping returned invalid pattern values")
        parsed_patterns.append(
            {
                "name": name,
                "source": source,
                "when_applies": when_applies,
                "tradeoffs": tradeoffs,
                "disposition": disposition,
                "traces_to": list(traces_to),
            }
        )

    return {"patterns": parsed_patterns}


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
    retrieved = _retrieved_reference_facet_index(reference_facets)
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
                f"{source.get('reference_concept')!r}, but Reference returned "
                f"{', '.join(sorted(available_kinds))} facets"
            )
        elif available_kinds and facet_kind not in available_kinds:
            errors.append(
                "prior_art source uses unavailable facet_kind "
                f"{facet_kind!r} for {source.get('reference_concept')!r}; "
                f"available: {', '.join(sorted(available_kinds))}"
            )
    return errors


def _retrieved_reference_facet_index(reference_facets: list[dict[str, Any]]) -> dict[str, set[str]]:
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
    if set(parsed) != set(CANDIDATE_APPROACH_FIELDS):
        return _candidate_generation_error("Codex candidate generation returned invalid candidate fields")
    if not all(isinstance(parsed[field], str) for field in ("id", "name", "kind", "summary")):
        return _candidate_generation_error("Codex candidate generation returned invalid candidate string fields")
    if parsed["kind"] not in CANDIDATE_APPROACH_KINDS:
        return _candidate_generation_error("Codex candidate generation returned invalid candidate kind")
    if expected_kind is not None and parsed["kind"] != expected_kind:
        return _candidate_generation_error("Codex candidate generation returned unexpected candidate kind")
    first_playable = _parse_candidate_first_playable(parsed.get("first_playable_moment"))
    if first_playable is None:
        return _candidate_generation_error("Codex candidate generation returned invalid first_playable_moment")
    for field in ("core_systems", "draws_on"):
        value = parsed.get(field)
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            return _candidate_generation_error(f"Codex candidate generation returned invalid {field}")
    return {
        "id": parsed["id"],
        "name": parsed["name"],
        "kind": parsed["kind"],
        "summary": parsed["summary"],
        "first_playable_moment": first_playable,
        "core_systems": list(parsed["core_systems"]),
        "draws_on": list(parsed["draws_on"]),
    }


def _parse_candidate_first_playable(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict) or set(value) != set(CANDIDATE_FIRST_PLAYABLE_FIELDS):
        return None
    player_actions = value.get("player_actions")
    win_or_progress_condition = value.get("win_or_progress_condition")
    named_content = _parse_game_named_content(value.get("named_content"))
    if not isinstance(player_actions, list) or not all(isinstance(item, str) for item in player_actions):
        return None
    if named_content is None or not isinstance(win_or_progress_condition, str):
        return None
    return {
        "player_actions": list(player_actions),
        "named_content": named_content,
        "win_or_progress_condition": win_or_progress_condition,
    }


def _parse_game_named_content(value: Any) -> dict[str, list[str]] | None:
    if not isinstance(value, dict) or set(value) != set(GAME_NAMED_CONTENT_FIELDS):
        return None
    parsed: dict[str, list[str]] = {}
    for field in GAME_NAMED_CONTENT_FIELDS:
        items = value.get(field)
        if not isinstance(items, list) or not all(isinstance(item, str) for item in items):
            return None
        parsed[field] = list(items)
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
        named_content = _parse_implementation_named_content(system.get("named_content"))
        key_modules = system.get("key_modules")
        if named_content is None:
            return _implementation_strategy_error("Codex implementation strategy returned invalid named_content")
        if not isinstance(key_modules, list) or not all(isinstance(item, str) for item in key_modules):
            return _implementation_strategy_error("Codex implementation strategy returned invalid key_modules")
        if not isinstance(system.get("system_name"), str) or not isinstance(system.get("behavior_in_game"), str):
            return _implementation_strategy_error("Codex implementation strategy returned invalid system strings")
        parsed_systems.append(
            {
                "system_name": system["system_name"],
                "behavior_in_game": system["behavior_in_game"],
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


def _parse_implementation_named_content(value: Any) -> dict[str, list[str]] | None:
    if not isinstance(value, dict) or set(value) != set(IMPLEMENTATION_NAMED_CONTENT_FIELDS):
        return None
    parsed: dict[str, list[str]] = {}
    for field in IMPLEMENTATION_NAMED_CONTENT_FIELDS:
        items = value.get(field)
        if not isinstance(items, list) or not all(isinstance(item, str) for item in items):
            return None
        parsed[field] = list(items)
    return parsed


def _implementation_strategy_error(reason: str) -> dict[str, Any]:
    return {"ok": False, "error": reason}


def _parse_right_size_patch_plan(raw: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        return _right_size_patch_plan_error(f"Codex patch plan right-sizing returned invalid JSON: {exc}")

    if not isinstance(parsed, dict):
        return _right_size_patch_plan_error("Codex patch plan right-sizing returned non-object JSON")
    if set(parsed) != set(RIGHT_SIZE_PATCH_PLAN_FIELDS):
        return _right_size_patch_plan_error("Codex patch plan right-sizing returned invalid fields")
    first_playable = _parse_patch_plan_first_playable(parsed.get("first_playable"))
    if first_playable is None:
        return _right_size_patch_plan_error("Codex patch plan right-sizing returned invalid first_playable")
    follow_ups = parsed.get("follow_ups")
    if not isinstance(follow_ups, list):
        return _right_size_patch_plan_error("Codex patch plan right-sizing returned invalid follow_ups")
    parsed_follow_ups: list[dict[str, Any]] = []
    for item in follow_ups:
        if not isinstance(item, dict) or set(item) != set(PATCH_PLAN_FOLLOW_UP_FIELDS):
            return _right_size_patch_plan_error("Codex patch plan right-sizing returned invalid follow_up")
        named_content = _parse_game_named_content(item.get("named_content"))
        if named_content is None or not isinstance(item.get("adds"), str):
            return _right_size_patch_plan_error("Codex patch plan right-sizing returned invalid follow_up values")
        parsed_follow_ups.append({"adds": item["adds"], "named_content": named_content})

    deferred = parsed.get("deferred")
    if not isinstance(deferred, list):
        return _right_size_patch_plan_error("Codex patch plan right-sizing returned invalid deferred")

    parsed_deferred: list[dict[str, str]] = []
    for item in deferred:
        if not isinstance(item, dict):
            return _right_size_patch_plan_error("Codex patch plan right-sizing returned invalid deferred item")
        if set(item) != set(PATCH_PLAN_DEFERRED_FIELDS):
            return _right_size_patch_plan_error("Codex patch plan right-sizing returned invalid deferred fields")
        if not all(isinstance(item[field], str) for field in PATCH_PLAN_DEFERRED_FIELDS):
            return _right_size_patch_plan_error("Codex patch plan right-sizing returned invalid deferred values")
        parsed_deferred.append({"item": item["item"], "why_safe_to_defer": item["why_safe_to_defer"]})

    return {"first_playable": first_playable, "follow_ups": parsed_follow_ups, "deferred": parsed_deferred}


def _parse_patch_plan_first_playable(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict) or set(value) != set(PATCH_PLAN_FIRST_PLAYABLE_FIELDS):
        return None
    player_can = value.get("player_can")
    named_content = _parse_game_named_content(value.get("named_content"))
    win_or_progress_condition = value.get("win_or_progress_condition")
    how_verified = value.get("how_verified")
    if not isinstance(player_can, list) or not all(isinstance(item, str) for item in player_can):
        return None
    if named_content is None or not isinstance(win_or_progress_condition, str) or not isinstance(how_verified, str):
        return None
    return {
        "player_can": list(player_can),
        "named_content": named_content,
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
        if set(item) != set(SURFACED_RISK_FIELDS):
            return _surface_risks_error("Codex risk surfacing returned invalid risk fields")
        if not all(isinstance(item[field], str) for field in SURFACED_RISK_FIELDS):
            return _surface_risks_error("Codex risk surfacing returned invalid risk values")
        if item["attaches_to"] not in RISK_ATTACHES_TO:
            return _surface_risks_error("Codex risk surfacing returned invalid risk attachment")
        parsed_risks.append(
            {
                "id": item["id"],
                "risk": item["risk"],
                "mitigation": item["mitigation"],
                "attaches_to": item["attaches_to"],
                "target_id": item["target_id"],
            }
        )

    return {"risks": parsed_risks}


def _surface_risks_error(reason: str) -> dict[str, Any]:
    return {"ok": False, "error": reason}


def _with_requester_stack_org_profile_risk(
    risks: Mapping[str, Any],
    rfc_view: Mapping[str, Any],
    selected: Mapping[str, Any],
) -> dict[str, Any]:
    conflict = _requester_stack_org_profile_conflict(rfc_view)
    existing = [dict(item) for item in risks.get("risks", [])] if isinstance(risks, Mapping) else []
    if not conflict:
        return {"risks": existing}
    selected_id = str(selected.get("selected_candidate_id") or "provided_approach")
    risk_id = "risk:requester_stack_org_profile_conflict"
    if any(item.get("id") == risk_id for item in existing):
        return {"risks": existing}
    existing.append(
        {
            "id": risk_id,
            "risk": conflict,
            "mitigation": "Honor the requester-specified stack, keep generated alternatives out of the decision, and make verification gaps explicit in the patch plan.",
            "attaches_to": "decision",
            "target_id": f"decision:{selected_id}",
        }
    )
    return {"risks": existing}


def _requester_stack_org_profile_conflict(rfc_view: Mapping[str, Any]) -> str:
    tech_stack = rfc_view.get("tech_stack")
    if not isinstance(tech_stack, Mapping) or tech_stack.get("provenance") != "requester_specified":
        return ""
    stack_text = _tech_stack_text(tech_stack)
    hits = [marker for marker in ORG_BUILDER_DISALLOWED_STACK_MARKERS if marker in stack_text]
    if not hits:
        return ""
    stack_name = _stack_display_name(tech_stack) or "The requester-specified stack"
    return (
        f"{stack_name} conflicts with ORG_BUILDER_PROFILE defaults for text-authored worktree artifacts, "
        "headless functional_check verification, lightweight delivery, or available 2D/SVG asset production. "
        "Requester sovereignty makes this non-blocking."
    )


def _technical_approach_context(context: Mapping[str, Any] | None, repo: Path) -> dict[str, Any]:
    approach_context = dict(context or {})
    approach_context.setdefault("repo", repo)
    approach_context.setdefault("repo_root", repo)
    return approach_context


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
        "right_size_patch_plan",
        "surface_risks",
    ]


def _provided_approach_from_tech_stack(rfc_view: Mapping[str, Any]) -> dict[str, Any] | None:
    tech_stack = rfc_view.get("tech_stack")
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
    rfc_view: dict[str, Any],
    selected: Mapping[str, Any],
    candidates: Mapping[str, Any] | None = None,
) -> None:
    tech_stack = rfc_view.get("tech_stack")
    if not isinstance(tech_stack, dict) or tech_stack.get("provenance") != "unspecified":
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
    if not isinstance(candidate, Mapping):
        return ("framework_based", "", "Selected Technical Approach", "text-authored repository code", "headless functional_check target")
    text = " ".join(
        str(candidate.get(field, ""))
        for field in ("id", "name", "kind", "summary")
        if isinstance(candidate.get(field, ""), str)
    ).lower()
    if "from scratch" in text or "from_scratch" in text:
        return ("from_scratch", "", "", "text-authored repository code", "headless functional_check target")
    name = str(candidate.get("name") or candidate.get("id") or "Selected Technical Approach").strip()
    if "engine" in text or str(candidate.get("kind", "")) == "general_architectural":
        return ("engine_based", name, "", "text-authored repository code", "headless functional_check target")
    return ("framework_based", "", name, "text-authored repository code", "headless functional_check target")


def _selected_approach_rationale(selected: Mapping[str, Any]) -> str:
    rationale = selected.get("rationale")
    if not isinstance(rationale, Mapping):
        return ""
    parts: list[str] = []
    for key in ("because", "under_constraints", "accepting_tradeoffs"):
        value = rationale.get(key)
        if isinstance(value, list):
            parts.extend(str(item) for item in value if str(item).strip())
    return " ".join(parts)[:1000]


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
    safe_steps = [
        {"step": str(step.get("step", "")), "seconds": float(step.get("seconds", 0.0))}
        for step in steps_completed
    ]
    snapshot = {
        "technical_approach": _approach_snapshot(partial_tree),
        "steps_completed": safe_steps,
        "current_step": current_step,
        "progress": {
            "steps_done": [step["step"] for step in safe_steps],
            "steps_completed": safe_steps,
            "current_step": current_step,
        },
    }
    path = Path(progress_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snapshot, indent=2, default=str, sort_keys=True), encoding="utf-8")


def _technical_approach_step_failure(step: str, result: Any) -> dict[str, Any] | None:
    if isinstance(result, Mapping) and result.get("ok") is False:
        return {
            "ok": False,
            "error": str(result.get("error", f"{step} failed")),
            "failed_step": step,
        }
    return None


def _provided_approach_selection(provided_approach: Any) -> dict[str, Any]:
    return {
        "selected_candidate_id": "provided_approach",
        "arguments": [
            {
                "role": "support",
                "about_candidate_id": "provided_approach",
                "claim": "Use the requester-provided Technical Approach as the primary design node.",
                "grounds": "The requester supplied an approach to preserve at the RFC boundary.",
                "warrant": "Receive refines and grounds requester intent instead of discarding it.",
                "backing": "The RFC receive procedure treats provided Technical Approach content as the basis.",
                "rebuttal": "Generated alternatives are skipped unless the provided basis is absent.",
            }
        ],
        "rationale": {
            "because": ["Requester intent is preserved as the primary design input."],
            "under_constraints": ["Refinement remains inside ai_org.rfc and may use Reference and repo evidence."],
            "accepting_tradeoffs": ["Generated candidate comparison is not performed for this provided basis."],
        },
        "stack_axes": _requester_specified_stack_axes(provided_approach),
        "rejected": [],
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
        "builder_buildability": {
            "evidence": "ORG_BUILDER_PROFILE may reveal conflicts, but requester-specified stacks are authoritative.",
            "judgment": "Record any buildability conflict as a non-blocking risk rather than rejecting the stack.",
        },
        "asset_supply": {
            "evidence": "The org asset path remains 2D vector/SVG unless the requester stack requires otherwise.",
            "judgment": "Plan around the requested stack and surface any asset-pipeline mismatch as risk.",
        },
        "distribution_reachability": {
            "evidence": "The requested stack may impose install or runtime expectations beyond the org default.",
            "judgment": "Keep alternatives out of the decision and capture reachability concerns in risks.",
        },
        "licensing_cost": {
            "evidence": "Licensing and cost remain visible planning facts even when they cannot veto the stack.",
            "judgment": "Track licensing or commercial-tooling exposure as non-blocking risk.",
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
    patch_plan: Mapping[str, Any],
    risks: Mapping[str, Any],
    source: str,
    *,
    provided_approach: Any | None = None,
    rfc_view: Mapping[str, Any] | None = None,
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
        patch_plan,
        risks,
        cross_links,
        provided_approach=provided_approach,
    )
    problem["open_questions"] = []

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


def _partial_question_tree(
    *,
    candidates: Mapping[str, Any] | None = None,
    evaluations: Mapping[str, Any] | None = None,
    selected: Mapping[str, Any] | None = None,
    implementation: Mapping[str, Any] | None = None,
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
                "first_playable_moment": {
                    "player_actions": ["Follow the requester-provided approach through implementation planning."],
                    "named_content": {
                        "locations": ["Requester-provided scope"],
                        "enemies": ["Requester-provided conflicts"],
                        "items_or_spells": ["Requester-provided mechanisms"],
                    },
                    "win_or_progress_condition": "The provided approach is grounded into implementation and patch-plan nodes.",
                },
                "core_systems": ["RFC receive Technical Approach refinement"],
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
        "open_questions": [],
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


def _question_tree(
    selected: Mapping[str, Any],
    candidates: Mapping[str, Any] | None,
    evaluations: Mapping[str, Any] | None,
    implementation: Mapping[str, Any],
    patch_plan: Mapping[str, Any],
    risks: Mapping[str, Any],
    cross_links: list[dict[str, str]],
    *,
    provided_approach: Any | None = None,
) -> dict[str, Any]:
    candidate_nodes = _candidate_tree_nodes(candidates, evaluations, selected, risks, cross_links, provided_approach)
    decision = _decision_tree_node(selected, implementation, patch_plan, risks, cross_links)
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
) -> list[dict[str, Any]]:
    candidate_items = candidates.get("candidates", []) if isinstance(candidates, Mapping) else []
    if not candidate_items and provided_approach is not None:
        candidate_items = [
            {
                "id": "provided_approach",
                "name": "Requester-provided Technical Approach",
                "kind": "repo_native",
                "summary": "Preserve and refine the requester-provided Technical Approach.",
                "first_playable_moment": {
                    "player_actions": ["Follow the requester-provided approach through implementation planning."],
                    "named_content": {
                        "locations": ["Requester-provided scope"],
                        "enemies": ["Requester-provided conflicts"],
                        "items_or_spells": ["Requester-provided mechanisms"],
                    },
                    "win_or_progress_condition": "The provided approach is grounded into implementation and patch-plan nodes.",
                },
                "core_systems": ["RFC receive Technical Approach refinement"],
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
                prior_id = prior_art_links.setdefault(draw, f"prior_art:{_slug(draw)}")
                _add_cross_link(cross_links, candidate_id, prior_id, "derived_from")
        for risk in node["risks"]:
            _add_cross_link(cross_links, risk["id"], candidate_id, "mitigates")
    return nodes


def _decision_tree_node(
    selected: Mapping[str, Any],
    implementation: Mapping[str, Any],
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
    implementation_node["patch_plan"] = _node_with_id(patch_plan, patch_plan_id)
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
    _add_cross_link(cross_links, patch_plan_id, implementation_id, "implements")
    for rejection in decision_node["rejected"]:
        _add_cross_link(cross_links, decision_id, rejection["candidate_id"], "objects_to")
    for risk in decision_node["risks"]:
        _add_cross_link(cross_links, risk["id"], decision_id, "mitigates")
    for risk in implementation_node["risks"]:
        _add_cross_link(cross_links, risk["id"], implementation_id, "mitigates")
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


def _add_cross_link(cross_links: list[dict[str, str]], from_id: str, to_id: str, link_type: str) -> None:
    if link_type not in CROSS_LINK_TYPES:
        raise ValueError(f"invalid cross_link type: {link_type}")
    link = {"from": from_id, "to": to_id, "type": link_type}
    if link not in cross_links:
        cross_links.append(link)


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


def _parse_grounding_result(raw: str, original_rfc_view: dict[str, Any]) -> GroundingResult:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return _grounding_fail_closed(original_rfc_view, f"Grounding returned invalid JSON: {raw}")

    if not isinstance(parsed, dict):
        return _grounding_fail_closed(original_rfc_view, f"Grounding returned non-object JSON: {raw}")

    confident = parsed.get("confident")
    proposed_rfc = _with_working_title_fallback(parsed.get("proposed_rfc"), original_rfc_view)
    assumptions = parsed.get("assumptions")
    grounding_notes = parsed.get("grounding_notes")
    questions = parsed.get("questions")
    if not isinstance(confident, bool):
        return _grounding_fail_closed(original_rfc_view, f"Grounding returned invalid confident: {raw}")
    if not _is_rfc_view(proposed_rfc):
        return _grounding_fail_closed(original_rfc_view, f"Grounding returned invalid proposed_rfc: {raw}")
    if not isinstance(assumptions, list) or not all(isinstance(assumption, str) for assumption in assumptions):
        return _grounding_fail_closed(original_rfc_view, f"Grounding returned invalid assumptions: {raw}")
    if not isinstance(grounding_notes, str) or len(grounding_notes) > 2000:
        return _grounding_fail_closed(original_rfc_view, f"Grounding returned invalid grounding_notes: {raw}")
    if not isinstance(questions, list) or not all(isinstance(question, str) for question in questions):
        return _grounding_fail_closed(original_rfc_view, f"Grounding returned invalid questions: {raw}")
    return GroundingResult(_rfc_to_view(proposed_rfc), grounding_notes, confident, assumptions, questions)


def _with_working_title_fallback(value: Any, original_rfc_view: dict[str, Any]) -> Any:
    if not isinstance(value, dict):
        return value
    title = value.get("working_title")
    if not isinstance(title, str) or title.strip():
        return value

    repaired = dict(value)
    repaired["working_title"] = _derive_working_title(repaired, original_rfc_view)
    return repaired


def _derive_working_title(rfc_view: Mapping[str, Any], original_rfc_view: Mapping[str, Any]) -> str:
    raw_request = str(rfc_view.get("raw_request") or original_rfc_view.get("raw_request") or "")
    candidates = [
        rfc_view.get("problem_or_motivation"),
        _named_subject_from_request(raw_request),
        raw_request,
    ]
    for candidate in candidates:
        title = _working_title_phrase(str(candidate or ""))
        if title:
            return title
    return "Grounded RFC"


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
    rfc_view: dict[str, Any],
    grounding_result: GroundingResult,
) -> dict[str, Any]:
    violations = _lint_grounding(request, rfc_view, grounding_result)
    verdict = _run_grounding_verifier(request, rfc_view, grounding_result)

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
    return {"ok": not violations, "violations": violations}


def _lint_grounding(
    request: dict[str, Any],
    rfc_view: dict[str, Any],
    grounding_result: GroundingResult,
) -> list[str]:
    text = _grounding_text(rfc_view, grounding_result)
    marker_text = _lint_target_text(rfc_view)
    violations: list[str] = []

    empty_required_fields = [
        field
        for field in RFC_HANDOFF_REQUIRED_FIELDS
        if field in STRING_FIELDS and isinstance(rfc_view.get(field), str) and not rfc_view[field].strip()
    ]
    if empty_required_fields:
        violations.append(
            "C0 required-field completeness lint: rfc_handoff string fields must be non-empty: "
            + ", ".join(empty_required_fields)
        )

    # Deterministic marker lints are field-scoped: exclusion/rejection/assumption fields must
    # be free to name what they exclude, and whole-RFC scans punish correct behavior. The field
    # registry is the source of truth for which fields commit to the target being built.
    generalizers = _matching_markers(marker_text, GENERALIZER_MARKERS)
    if generalizers:
        violations.append(
            "C1 faithfulness/specificity lint: grounded RFC uses generalizer markers "
            + ", ".join(generalizers)
        )

    scope_hedges = _matching_markers(marker_text, SCOPE_HEDGE_MARKERS)
    if scope_hedges:
        violations.append("C2 full scope lint: grounded RFC uses scope-shrinking markers " + ", ".join(scope_hedges))

    legal_hits = _keyword_hit_count(text, LEGAL_KEYWORDS)
    word_count = max(1, len(re.findall(r"\w+", text)))
    if legal_hits >= 3 and legal_hits / word_count >= 0.015:
        violations.append("C3 non-legal lint: legal/IP/trademark/copyright language dominates grounding output")

    if not _request_explicitly_retro(request):
        retro_markers = _matching_markers(marker_text, RETRO_MARKERS)
        if retro_markers:
            violations.append(
                "C4 latest-default lint: grounded RFC targets dated/retro markers without a retro request "
                + ", ".join(retro_markers)
            )

    violations.extend(_lint_grounding_tech_stack_provenance(request, rfc_view))

    return violations


def _lint_grounding_tech_stack_provenance(request: Mapping[str, Any], rfc_view: Mapping[str, Any]) -> list[str]:
    tech_stack = rfc_view.get("tech_stack")
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
    rfc_view: dict[str, Any],
    grounding_result: GroundingResult,
) -> dict[str, Any]:
    prompt = (
        "You are an adversarial read-only verifier for an RFC grounding contract.\n"
        "Given the original request and the grounded RFC view, judge these four checks:\n"
        "C1 faithful_specific: the grounded identity stays the specific named thing, not generalized to its category.\n"
        "C2 full_scope: the RFC does not shrink the request to a slice, demo, MVP, prototype, one area, or first iteration.\n"
        "C3 non_legal: IP, trademark, copyright, licensing, or legal risk content does not dominate the output.\n"
        "C4 latest_default: unless the request explicitly asked for retro, old, classic, vintage, or a specific past "
        "version/year, the RFC targets the latest/current version, conventions, and best practices of the named thing.\n"
        "Be strict. Return false for any check that is semantically violated, even if wording tries to hide it.\n\n"
        + _format_rfc("Original request registry view", _rfc_to_view(request))
        + "\n"
        + _format_rfc("Grounded registry RFC view", _rfc_to_view(rfc_view))
        + f"\ngrounding_notes: {grounding_result.grounding_notes}\n"
        + "\nReturn only JSON matching the provided schema."
    )
    temp_dir = Path(tempfile.mkdtemp(prefix="ai-org-rfc-grounding-verify-"))
    schema_file = temp_dir / "rfc-grounding-verdict.schema.json"
    out_file = temp_dir / "grounding-verdict.json"
    try:
        schema_file.write_text(json.dumps(GROUNDING_VERDICT_SCHEMA, indent=2), encoding="utf-8")
        cmd = [
            "codex",
            "exec",
            "--sandbox",
            "read-only",
            "-o",
            str(out_file),
            "--output-schema",
            str(schema_file),
            prompt,
        ]
        try:
            completed = subprocess.run(
                cmd,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
            )
        except OSError as exc:
            return {"ok": False, "violation": f"C0 verifier: grounding verifier failed: {exc}"}

        if completed.returncode != 0:
            detail = completed.stderr.strip() or (
                "no output file" if not out_file.exists() else "Codex verifier did not complete successfully."
            )
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


def _grounding_text(rfc_view: dict[str, Any], grounding_result: GroundingResult) -> str:
    parts: list[str] = []
    for field in RFC_VIEW_FIELDS:
        value = rfc_view[field]
        if isinstance(value, list):
            parts.extend(str(item) for item in value)
        elif isinstance(value, dict):
            parts.append(json.dumps(value, sort_keys=True, ensure_ascii=True))
        else:
            parts.append(str(value))
    parts.append(grounding_result.grounding_notes)
    return f" {' '.join(parts).lower()} "


def _lint_target_text(rfc_view: Mapping[str, Any]) -> str:
    parts: list[str] = []
    for field in LINT_TARGET_FIELDS:
        value = rfc_view.get(field)
        if isinstance(value, list):
            parts.extend(str(item) for item in value)
        elif isinstance(value, dict):
            parts.append(json.dumps(value, sort_keys=True, ensure_ascii=True))
        else:
            parts.append(str(value or ""))
    return f" {' '.join(parts).lower()} "


def _matching_markers(text: str, markers: tuple[str, ...]) -> list[str]:
    return [marker for marker in markers if marker.lower() in text]


def _keyword_hit_count(text: str, keywords: tuple[str, ...]) -> int:
    return sum(text.count(keyword.lower()) for keyword in keywords)


def _request_explicitly_retro(request: dict[str, Any]) -> bool:
    text = _grounding_text(_rfc_to_view(request), GroundingResult(_rfc_to_view(request)))
    if _matching_markers(text, RETRO_REQUEST_MARKERS):
        return True
    years = [int(year) for year in re.findall(r"\b(19\d{2}|20[0-2]\d)\b", text)]
    return any(year < 2026 for year in years)


def _with_unresolved_violations(notes: str, violations: list[str]) -> str:
    suffix = " Unresolved grounding contract violations: " + "; ".join(violations)
    return (notes + suffix)[:2000]


def _dedupe(values: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value not in seen:
            seen.add(value)
            deduped.append(value)
    return deduped


def _grounding_fail_closed(rfc_view: dict[str, Any], reason: str) -> GroundingResult:
    assumption = "I assumed the current registry-shaped request is the closest available interpretation because grounding failed before it could produce a researched proposal."
    question = "Can you confirm or correct the proposed RFC interpretation?"
    return GroundingResult(_rfc_to_view(rfc_view), reason[:2000], False, [assumption], [question])


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
    rfc = entrance_defaults(data)
    _required_string_field(rfc, "raw_request")
    return rfc


def _registry_rfc(data: Mapping[str, Any]) -> dict[str, Any]:
    if set(data) != set(RFC_VIEW_FIELDS):
        missing = sorted(set(RFC_VIEW_FIELDS) - set(data))
        extra = sorted(set(data) - set(RFC_VIEW_FIELDS))
        detail = []
        if missing:
            detail.append("missing " + ", ".join(missing))
        if extra:
            detail.append("extra " + ", ".join(extra))
        suffix = ": " + "; ".join(detail) if detail else ""
        raise ValueError(f"RFC handoff must contain exactly the registry fields{suffix}.")
    for field in STRING_FIELDS:
        if not isinstance(data[field], str):
            raise ValueError(f"RFC field {field!r} must be a string.")
    for field in STRING_ARRAY_FIELDS:
        value = data[field]
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise ValueError(f"RFC field {field!r} must be a list of strings.")
    if not validate_tech_stack(data["tech_stack"]):
        raise ValueError("RFC field 'tech_stack' must contain valid structured sub-tags.")
    for field in RFC_HANDOFF_REQUIRED_FIELDS:
        value = data[field]
        if isinstance(value, str) and not value.strip():
            raise ValueError(f"RFC handoff field {field!r} is required and must be non-empty.")
    return {field: data[field] for field in RFC_VIEW_FIELDS}


def _is_rfc_view(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    try:
        _registry_rfc(value)
    except ValueError:
        return False
    return True


def _rfc_to_view(rfc_view: dict[str, Any]) -> dict[str, Any]:
    return {field: rfc_view[field] for field in RFC_VIEW_FIELDS}


def _format_rfc(label: str, view: dict[str, Any]) -> str:
    lines = [f"{label}:"]
    for field in RFC_VIEW_FIELDS:
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
    return slug[:80] or "rfc"


def _write_rfc_branch(
    repo: Path,
    branch: str,
    base: str,
    rfc: Mapping[str, Any],
    *,
    rfc_path: str = "rfc.json",
    extra_files: Mapping[str, Any] | None = None,
    commit_message: str | None = None,
) -> dict[str, str]:
    rfc_view = _registry_rfc(rfc)
    original = _current_branch(repo)
    try:
        _git(repo, "checkout", "-B", branch, base)
        path = repo / rfc_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(rfc_view, indent=2) + "\n", encoding="utf-8")
        add_paths = [rfc_path]
        for rel_path, payload in (extra_files or {}).items():
            extra_path = repo / rel_path
            extra_path.parent.mkdir(parents=True, exist_ok=True)
            extra_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            add_paths.append(rel_path)
        _git(repo, "add", *add_paths)
        _git(repo, "commit", "--allow-empty", "-m", commit_message or f"rfc: write {rfc_view['working_title']}")
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
