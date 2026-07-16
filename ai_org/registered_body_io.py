"""Versioned catalog and scoped source scan for durable AI Org body I/O.

The original 131-site ``callsite_inventory`` cited by the patch series was not
included in this repository. The checked-in v1 catalog records its
reconstruction basis explicitly and adds the canonical-root preview and
dormant producer-lifecycle reader boundaries. After removal of six obsolete
impl3-era boundaries, the admitted surface is 138 sites. The closing gate
requires every site to resolve to an admitted lifecycle cohort.

The scanner uses only the Python standard library.  Selectors are based on a
repository-relative path, enclosing function, qualified call name, and an
occurrence within that function; source line numbers are evidence only and are
not identity.  Prompt rendering, temporary Codex schema files, logs, research
storage, product graphics data, and off-Git intake are outside this scan.
"""
from __future__ import annotations

import ast
from collections import Counter, defaultdict
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath
import sys
from typing import Any, Iterable, Mapping

from ai_org.body_codec import (
    BodyCodecClient,
    EXPECTED_AUTHORITY_SHA256,
    EXPECTED_CATALOG_SHA256 as EXPECTED_CODEC_CATALOG_SHA256,
    EXPECTED_MANIFEST_SHA256,
    EXPECTED_PUBLIC_CONTRACT_SHA256,
    PINNED_PRODUCER_LIFECYCLE_IDENTITIES,
    PRODUCER_LIFECYCLE_MANIFEST,
    strict_json_loads,
)


CATALOG_SCHEMA = "ai-org-durable-body-coverage-catalog-v1"
REPORT_SCHEMA = "ai-org-durable-body-coverage-report-v1"
EXPECTED_SITE_COUNT = 138
EXPECTED_CONTEXT_COUNT = 35
EXPECTED_BODY_KIND_COUNT = 28
# The compatibility verifier and the codec handshake must advance as one
# identity cohort. Deriving the catalog pin from the codec boundary prevents a
# later authority refresh from leaving real inspection unverifiable while
# mirror-only tests continue to pass against a stale duplicate literal.
EXPECTED_CATALOG_SHA256 = EXPECTED_CODEC_CATALOG_SHA256
FROZEN_CONTEXT_IDS = frozenset(
    {
        "acceptance-authority-seal-v1",
        "author-response-record-v1",
        "authoring-announcement-child-v1",
        "authoring-announcement-root-v1",
        "child-network-node-manifest-v1",
        "child-patch-series-cover-letter-v1",
        "child-technical-approach-tree-v1",
        "claim-admission-v1",
        "direction-review-round-v1",
        "functional-acceptance-v1",
        "functional-check-carrier-recipe-v1",
        "implementation-experience-report-v1",
        "implementation-result-contract-v1",
        "implementation-result-v1",
        "lineage-carrier-recipe-v1",
        "maintainer-series-request-v1",
        "patch-queue-status-rollup-v1",
        "patch-series-cover-letter-v1",
        "patch-series-metadata-v1",
        "patchwork-check-event-stream-v1",
        "producer-completion-assertion-v1",
        "producer-promise-v1",
        "producer-task-binding-v1",
        "questions-contribution-v1",
        "questions-patch-series-v1",
        "request-outcome-custom-ref-v1",
        "request-provenance-branch-v1",
        "request-provenance-custom-ref-v1",
        "revision-delta-record-v1",
        "root-network-node-manifest-v1",
        "root-technical-approach-tree-v1",
        "semantic-status-note-v1",
        "series-coverage-ledger-v1",
        "series-scope-decomposition-v1",
        "synthetic-network-review-record-v1",
    }
)
FROZEN_BODY_KINDS = frozenset(
    {
        "AcceptanceAuthoritySeal",
        "AuthorResponseRecord",
        "AuthoringAnnouncement",
        "ClaimAdmission",
        "DirectionReviewRound",
        "FunctionalAcceptanceVerdict",
        "FunctionalCheckCarrierRecipe",
        "ImplementationExperienceReport",
        "ImplementationResult",
        "ImplementationResultContract",
        "LineageCarrierRecipe",
        "MaintainerSeriesRequest",
        "NetworkNodeManifest",
        "PatchQueueStatusRollup",
        "PatchSeriesCoverLetter",
        "PatchSeriesMetadata",
        "PatchworkCheckEventStream",
        "ProducerCompletionAssertion",
        "ProducerPromise",
        "ProducerTaskBinding",
        "QuestionsThePatchMustAnswer",
        "RequestOutcome",
        "RequestProvenanceRecord",
        "RevisionDeltaRecord",
        "SemanticStatus",
        "SeriesCoverageLedger",
        "SeriesScopeDecomposition",
        "TechnicalApproachTree",
    }
)
ZERO_GAP_FIELDS = (
    "pending_sites",
    "missing_rows",
    "ambiguous_rows",
    "orphaned_sites",
    "admitted_scope_bypasses",
    "pending_executable_rows",
    "admitted_non_executable_rows",
    "newly_admitted_contexts",
    "newly_admitted_body_kinds",
)
ZERO_GAP_DETAIL_FIELDS = (
    "missing_site_ids",
    "ambiguous_site_ids",
    "orphaned_source_selectors",
    "admitted_scope_bypass_selectors",
    "pending_executable_site_ids",
    "admitted_non_executable_site_ids",
    "newly_admitted_context_ids",
    "newly_admitted_body_kind_names",
)
SEMANTIC_CONTEXT_ID = "semantic-status-note-v1"
PRODUCER_LIFECYCLE_COHORT = "producer_lifecycle"
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = Path(__file__).with_name("data") / "durable-body-catalog-v1.json"
MIRRORED_CATALOG_PATH = (
    REPOSITORY_ROOT
    / "cuecodec"
    / "cue"
    / "engine"
    / "registry"
    / "durable-body-catalog-v1.json"
)

# These operations mechanically identify the reconstructed call-site surface.
# They intentionally do not include arbitrary Path.read_text/write_text calls:
# those are shared by temporary carrier files, logs, and product data as well as
# durable bodies, and blanket inclusion would recreate the false-positive
# problem called out by the patch-series plan.
SCANNED_OPERATIONS = frozenset(
    {
        "json.dumps",
        "json.loads",
        "git_wrapper.commit_files",
        "git_wrapper.create_ref_with_files",
        "git_wrapper.file_exists",
        "git_wrapper.push_cas",
        "git_wrapper.push_create",
        "git_wrapper.push_delete",
        "git_wrapper.show_file",
        "git_wrapper.tree_files",
    }
)

# Explicit, reviewable exclusions.  Each path is an off-Git, temporary,
# observability, research, or product-data boundary rather than durable Git body
# authority.  Keeping the reasons next to the policy makes the scan auditable.
EXCLUDED_PATHS: Mapping[str, str] = {
    "ai_org/engineering_precedent_store.py": "research/cache storage",
    "ai_org/graphicist.py": "product graphics and web data",
    "ai_org/launch.py": "process handshakes and launch logs",
    "ai_org/log.py": "observability projections and JSONL logs",
    "ai_org/maintainer_merge/mainline.py": "temporary Codex verdict carrier",
    "ai_org/maintainer_merge/subsystem.py": "temporary Codex verdict carrier",
    "ai_org/body_codec.py": "subprocess framing and disposable carrier boundary",
    "ai_org/patchwork_check_stream.py": "typed exact-byte codec and transient replay boundary",
    "ai_org/patchwork_queue/codex_exec.py": "temporary subprocess schema/output files",
    "ai_org/patchwork_queue/submit.py": "off-Git intake envelope",
    "ai_org/registered_body_io.py": "the scanner implementation itself",
}

# Scope exclusions are transformations or temporary prompt/carrier boundaries,
# not independently addressable durable Git bodies.  Parser scopes that produce
# values later persisted by a lifecycle operation remain pending catalog sites.
EXCLUDED_SCOPES = frozenset(
    {
        "_canonical_json",
        "_closed_precedent_fallback_prompt",
        "_domain_precedent_source_text",
        "_domain_specification_prompt",
        "_drift_warnings_for_request",
        "_elaborate_contingency",
        "_evaluate_candidate_prompt",
        "_evaluation_repair_prompt",
        "_extract_constraints_prompt",
        "_format_domain_specification",
        "_format_patch_series",
        "_format_patchwork_fact_context",
        "_format_rfc",
        "_generate_candidate_prompt",
        "_ground_request",
        "_grounding_prompt",
        "_grounding_text",
        "_implementation_strategy_prompt",
        "_json_for_prompt",
        "_json_safe",
        "_normalize_problem_prompt",
        "_prior_art_key_concepts",
        "_prior_art_map_prompt",
        "_reference_reconciliation_prompt",
        "_revision_contract_text",
        "_right_size_patch_plan_prompt",
        "_root_success_criteria_text",
        "_run_grounding_verifier",
        "_select_approach_prompt",
        "_split_prompt",
        "_surface_risks_prompt",
        "_targeted_revision_prompt",
        "_validation_feedback",
        # Positive/negative CUE examples are disposable validation fixtures,
        # not durable engine bodies.
        "_validate_stateful_json_states",
        "_compact_text",
        "check",
        "render_item_prompt",
        "submit",
        "write_gate_reports",
        "_mark_processed",
        "_load_revision_delta",
        "parse_record",
        # Purpose-built Codex output is an off-Git preparation carrier; the
        # resulting full root body is admitted at the named preview workflow.
        "parse_canonical_root_preview",
        # Receive-owned producer preparation uses JSON only as a temporary,
        # off-Git model carrier and prompt projection. Canonical CUE rendering
        # remains the sole admitted body boundary.
        "_load_canonical_root_preview_carrier",
        "_producer_aware_preparation_prompt",
        "_read_inbox_envelope",
        "_review_round_records",
        # These lifecycle coordinators enumerate or transport already
        # registered producer/result bodies, then immediately hand bytes to
        # their pinned codec readers. They are not additional body authority.
        "address_feedback",
        "_prepare_task_binding_files",
        # Root cover-letter reads are registered through patch_series_bodies;
        # the legacy fallback in these consumers is retained only for nested,
        # still-pending nodes and is not a second root authority.
        "_read_patch_series_from_git",
        "_raw_member",
        "read_cover_letter",
        "is_canonical_cohort",
        "read_member",
        "validated_functional_carrier",
        # This registered-kind dispatcher selects a closed context/contract
        # pair before parsing. Its one frozen-tree read is transport for the
        # already cataloged network cohort, not a 133rd durable body site.
        "read_network_body",
    }
)

# Source selectors are durable catalog coordinates. When a cohort replaces a
# direct JSON boundary with a named codec or publication entrypoint, retain the
# old coordinate as an explicit alias instead of silently renumbering catalog
# rows. Several historical members may converge on the same publication-unit
# boundary because that transaction now admits them together.
REPLACED_SELECTOR_ALIASES: Mapping[tuple[str, str, str, str, int], tuple[str, str, str, str, int]] = {
    # Root and child announcement routing historically performed separate
    # manifest reads. Registered network projection now supplies the canonical
    # manifest first and retains one legacy fallback read for both sites.
    ("ai_org/patch_author/announcements.py", "_contrib_branch_for", "call", "git_wrapper.show_file", 2):
        ("ai_org/patch_author/announcements.py", "_contrib_branch_for", "call", "git_wrapper.show_file", 1),
    ("ai_org/patch_author/code_worker.py", "_commit_contingency_plan", "call", "json.dumps", 1):
        ("ai_org/contributor_handoff.py", "prepare_questions", "function", "definition", 1),
    ("ai_org/patch_author/code_worker.py", "_commit_contingency_plan", "call", "json.loads", 1):
        ("ai_org/contributor_handoff.py", "parse_questions", "function", "definition", 1),
    ("ai_org/patch_author/code_worker.py", "_commit_implementation_experience_report", "call", "json.loads", 1):
        ("ai_org/contributor_handoff.py", "parse_experience", "function", "definition", 1),
    ("ai_org/patch_author/code_worker.py", "_contrib_contingency_plans", "call", "json.loads", 1):
        ("ai_org/contributor_handoff.py", "prepare_functional_carrier", "function", "definition", 1),
    ("ai_org/patch_author/code_worker.py", "_write_result_artifact", "call", "json.dumps", 1):
        ("ai_org/contributor_handoff.py", "prepare_result", "function", "definition", 1),
    # Root generation classification and registered network readers replaced
    # the five positional raw reads in load_brief. Bind each historical row to
    # its semantically identical boundary; never let call-number compaction
    # attach it to a different body merely because that call now has the old
    # occurrence number.
    ("ai_org/patch_author/code_worker.py", "load_brief", "call", "git_wrapper.show_file", 1):
        ("ai_org/patch_series_bodies.py", "read_cover_letter", "function", "definition", 1),
    ("ai_org/patch_author/code_worker.py", "load_brief", "call", "git_wrapper.show_file", 2):
        ("ai_org/patch_author/code_worker.py", "load_brief", "call", "network_bodies.read_network_body", 2),
    ("ai_org/patch_author/code_worker.py", "load_brief", "call", "git_wrapper.show_file", 3):
        ("ai_org/contributor_handoff.py", "read_result_contract", "function", "definition", 1),
    ("ai_org/patch_author/code_worker.py", "load_brief", "call", "git_wrapper.show_file", 4):
        ("ai_org/patch_author/code_worker.py", "load_brief", "call", "git_wrapper.show_file", 1),
    ("ai_org/patch_author/code_worker.py", "load_brief", "call", "git_wrapper.show_file", 5):
        ("ai_org/patch_author/code_worker.py", "load_brief", "call", "git_wrapper.show_file", 3),
    ("ai_org/patch_author/code_worker.py", "load_brief", "call", "json.loads", 1):
        ("ai_org/patch_author/code_worker.py", "load_brief", "call", "network_bodies.read_network_body", 1),
    ("ai_org/patch_author/code_worker.py", "load_brief", "call", "json.loads", 2):
        ("ai_org/patch_author/code_worker.py", "load_brief", "call", "network_bodies.read_network_body", 2),
    ("ai_org/patch_author/code_worker.py", "load_brief", "call", "json.loads", 3):
        ("ai_org/contributor_handoff.py", "read_result_contract", "function", "definition", 1),
    ("ai_org/patch_author/code_worker.py", "load_brief", "call", "json.loads", 4):
        ("ai_org/contributor_handoff.py", "parse_questions", "function", "definition", 1),
    ("ai_org/patch_author/code_worker.py", "load_brief", "call", "json.loads", 5):
        ("ai_org/patch_author/code_worker.py", "load_brief", "call", "json.loads", 1),
    ("ai_org/patch_author/functional_check.py", "_must_answer_questions_rejection", "call", "json.loads", 1):
        ("ai_org/patch_author/functional_check.py", "_must_answer_questions_rejection", "call", "git_wrapper.show_file", 2),
    ("ai_org/patch_author/functional_check.py", "_must_answer_questions_rejection", "call", "json.loads", 2):
        ("ai_org/patch_author/code_worker.py", "_commit_implementation_experience_report", "call", "git_wrapper.show_file", 2),
    ("ai_org/patch_author/functional_check.py", "_patchwork_check_acknowledgement_rejection", "call", "json.dumps", 4):
        ("ai_org/patchwork_queue/receive.py", "_questions_artifact_with_routed_closures", "call", "git_wrapper.show_file", 2),
    ("ai_org/patchwork_queue/receive.py", "_questions_artifact_with_routed_closures", "call", "json.loads", 1):
        ("ai_org/patchwork_queue/receive.py", "_unprocessed_experience_reports", "call", "git_wrapper.show_file", 2),
    ("ai_org/patchwork_queue/receive.py", "_unprocessed_experience_reports", "call", "json.loads", 1):
        ("ai_org/patchwork_queue/review.py", "_questions_the_patch_must_answer", "call", "git_wrapper.show_file", 2),
    ("ai_org/patchwork_queue/review.py", "_questions_the_patch_must_answer", "call", "json.loads", 1):
        ("ai_org/contributor_handoff.py", "prepare_result_contract", "function", "definition", 1),
    ("ai_org/patchwork_queue/patch_series_gate.py", "_rebaseline", "call", "git_wrapper.commit_files", 1):
        ("ai_org/patchwork_queue/patch_series_gate.py", "_rebaseline", "function", "definition", 1),
    ("ai_org/patchwork_queue/patch_series_gate.py", "_refine", "call", "git_wrapper.commit_files", 1):
        ("ai_org/patchwork_queue/patch_series_gate.py", "_refine", "function", "definition", 1),
    ("ai_org/patchwork_queue/patch_series_gate.py", "_refine", "call", "git_wrapper.commit_files", 2):
        ("ai_org/patchwork_queue/patch_series_gate.py", "_refine", "function", "definition", 1),
    ("ai_org/patchwork_queue/patch_series_gate.py", "_refine", "call", "git_wrapper.commit_files", 3):
        ("ai_org/patchwork_queue/patch_series_gate.py", "_refine", "function", "definition", 1),
    ("ai_org/patchwork_queue/patch_series_gate.py", "_refine", "call", "git_wrapper.file_exists", 1):
        ("ai_org/patchwork_queue/patch_series_gate.py", "_refine", "function", "definition", 1),
    ("ai_org/patchwork_queue/patch_series_gate.py", "_refine", "call", "git_wrapper.file_exists", 2):
        ("ai_org/patchwork_queue/patch_series_gate.py", "_refine", "function", "definition", 1),
    ("ai_org/patchwork_queue/patch_series_gate.py", "elaborate", "call", "git_wrapper.commit_files", 1):
        ("ai_org/patchwork_queue/patch_series_gate.py", "elaborate", "function", "definition", 1),
    ("ai_org/patchwork_queue/patch_series_gate.py", "mark_stale", "call", "git_wrapper.commit_files", 1):
        ("ai_org/patchwork_queue/patch_series_gate.py", "mark_stale", "function", "definition", 1),
    ("ai_org/patchwork_queue/patch_series_gate.py", "rebaseline_pending", "call", "git_wrapper.file_exists", 1):
        ("ai_org/patchwork_queue/patch_series_gate.py", "rebaseline_pending", "function", "definition", 1),
    ("ai_org/patchwork_queue/patch_series_gate.py", "revalidate_stale", "call", "git_wrapper.commit_files", 1):
        ("ai_org/patchwork_queue/patch_series_gate.py", "revalidate_stale", "function", "definition", 1),
    ("ai_org/patchwork_queue/patch_series_gate.py", "revalidate_stale", "call", "git_wrapper.commit_files", 2):
        ("ai_org/patchwork_queue/patch_series_gate.py", "revalidate_stale", "function", "definition", 1),
    ("ai_org/patchwork_queue/patch_series_gate.py", "split_pending", "call", "git_wrapper.file_exists", 1):
        ("ai_org/patchwork_queue/patch_series_gate.py", "split_pending", "function", "definition", 1),
    ("ai_org/patchwork_queue/patch_series_gate.py", "stale_revalidation_pending", "call", "git_wrapper.file_exists", 1):
        ("ai_org/patchwork_queue/patch_series_gate.py", "stale_revalidation_pending", "function", "definition", 1),
    ("ai_org/patchwork_queue/patch_series_gate.py", "stamp_children", "call", "git_wrapper.commit_files", 1):
        ("ai_org/patchwork_queue/patch_series_gate.py", "stamp_children", "function", "definition", 1),
}

# The catalog registers the public API definitions as the durable sites.  Once
# admitted, those functions must not reintroduce direct JSON or raw Git-body
# operations: all body interpretation and publication goes through the codec
# and prepared-note boundaries.
ADMITTED_BOUNDARY_CALLS: frozenset[tuple[str, str, str, int]] = frozenset(
    {
        # The manifest resolver freezes one tree and checks the canonical-root
        # cutover marker before delegating body decoding to read_network_body.
        # This exact read is mutation-state comparison, not a second body
        # reader; other operations in the resolver remain visible to the scan.
        ("ai_org/network_bodies.py", "resolve_manifest_input", "git_wrapper.show_file", 1),
        # These helpers transport already-registered ProducerPromise bytes to
        # the pinned reader.  They do not interpret the body themselves; an
        # added read or enumeration remains visible as a new occurrence.
        (
            "ai_org/patch_author/producer_lifecycle.py",
            "_single_promise_family_branch",
            "git_wrapper.show_file",
            1,
        ),
        (
            "ai_org/patch_author/producer_lifecycle.py",
            "_tree_has_promise_for_branch",
            "git_wrapper.tree_files",
            1,
        ),
        (
            "ai_org/patch_author/producer_lifecycle.py",
            "_tree_has_promise_for_branch",
            "git_wrapper.show_file",
            1,
        ),
        (
            "ai_org/patch_author/producer_lifecycle.py",
            "transition_slots",
            "git_wrapper.show_file",
            2,
        ),
        # The compatibility reader selects a canonical or historical network
        # coordinate before delegating body parsing to the registered network
        # reader. These two existence checks are generation discrimination.
        (
            "ai_org/patchwork_queue/patch_series_gate.py",
            "_read_json",
            "git_wrapper.file_exists",
            1,
        ),
        (
            "ai_org/patchwork_queue/patch_series_gate.py",
            "_read_json",
            "git_wrapper.file_exists",
            2,
        ),
        # The closed producer gate renders its typed diagnostic into a blocker
        # string. This JSON is a non-authoritative projection, not another
        # verdict body codec; only this exact occurrence is exempted.
        (
            "ai_org/patch_author/functional_check.py",
            "producer_gate_rejection",
            "json.dumps",
            1,
        ),
        # Exact transport operations inside the consolidated publication
        # boundary. They are not additional body sites; any new operation or
        # occurrence remains visible to the orphan/bypass scan.
        ("ai_org/patch_author/announcements.py", "_registered_manifest_input", "git_wrapper.file_exists", 1),
        ("ai_org/patchwork_queue/patch_series_gate.py", "_commit_network_files", "git_wrapper.tree_files", 1),
        ("ai_org/patchwork_queue/patch_series_gate.py", "_network_file_exists", "git_wrapper.file_exists", 1),
        ("ai_org/patchwork_queue/patch_series_gate.py", "_network_file_exists", "git_wrapper.file_exists", 2),
        ("ai_org/patchwork_queue/patch_series_gate.py", "_network_file_exists", "git_wrapper.file_exists", 3),
        ("ai_org/patchwork_queue/patch_series_gate.py", "_network_path_last_commit", "git_wrapper.file_exists", 1),
        # The registered network reader first resolves the canonical path and
        # checks that exact path in the frozen tree. This is transport
        # selection for read_network_body, not an orphan body reader.
        ("ai_org/patchwork_queue/patch_series_gate.py", "_read_json", "git_wrapper.file_exists", 1),
        ("ai_org/patchwork_queue/patch_series_gate.py", "_validate_prepared_successor_members", "git_wrapper.file_exists", 1),
        # These exact operations hash or transport bytes inside already
        # registered network-transition boundaries; none establishes a second
        # durable-body authority.
        ("ai_org/patchwork_queue/patch_series_gate.py", "_network_source_vector_sha256", "json.dumps", 1),
        ("ai_org/patchwork_queue/patch_series_gate.py", "prepare_network_transition", "git_wrapper.tree_files", 1),
        ("ai_org/patchwork_queue/patch_series_gate.py", "_validate_escalation_containing_commit", "git_wrapper.file_exists", 1),
        ("ai_org/patchwork_queue/patch_series_gate.py", "_read_json", "git_wrapper.show_file", 1),
        ("ai_org/patchwork_queue/patch_series_gate.py", "_read_json", "json.loads", 1),
        ("ai_org/patch_author/announcements.py", "_contrib_branch_for", "git_wrapper.show_file", 1),
        ("ai_org/patch_author/announcements.py", "_contrib_branch_for", "git_wrapper.show_file", 2),
        ("ai_org/patch_author/announcements.py", "_contrib_branch_for", "json.loads", 1),
        ("ai_org/patch_author/announcements.py", "announce", "git_wrapper.create_ref_with_files", 1),
        ("ai_org/patch_author/announcements.py", "announce", "git_wrapper.push_cas", 1),
        ("ai_org/patch_author/announcements.py", "announce", "git_wrapper.push_create", 1),
        ("ai_org/patch_author/announcements.py", "announce", "json.dumps", 1),
        ("ai_org/patch_author/announcements.py", "evaluate", "git_wrapper.show_file", 1),
        ("ai_org/patch_author/announcements.py", "parse_announcement_record", "json.loads", 1),
        ("ai_org/patch_author/announcements.py", "withdraw", "git_wrapper.push_delete", 1),
        ("ai_org/patch_author/announcements.py", "withdraw", "git_wrapper.show_file", 1),
        # Acceptance freezes and checks the complete promise cohort before the
        # judge, then prepares detached verdict/notes commits for one atomic
        # ref transaction. These are transports inside that registered-body
        # boundary, not additional durable body definitions.
        ("ai_org/patch_author/functional_check.py", "prepare_claim_admission", "git_wrapper.tree_files", 1),
        ("ai_org/patch_author/functional_check.py", "_publish_registered_verdict", "git_wrapper.create_ref_with_files", 1),
        ("ai_org/patch_author/functional_check.py", "_publish_registered_verdict", "git_wrapper.create_ref_with_files", 2),
        # Exact transient operations added around the dormant producer matrix:
        # coordinate JSON is hash input and rejection JSON is human-readable
        # text.
        ("ai_org/maintainer_merge/evidence.py", "integrated_contribution_coordinate", "json.dumps", 1),
        ("ai_org/patch_author/producer_lifecycle.py", "evidence_coordinate", "json.dumps", 1),
        ("ai_org/patch_author/functional_check.py", "producer_gate_rejection", "json.dumps", 1),
        ("ai_org/patch_series_bodies.py", "read_cover_letter", "git_wrapper.show_file", 1),
        ("ai_org/patchwork_queue/patch_series_gate.py", "_write_synthetic_escalation_round", "git_wrapper.commit_files", 1),
        ("ai_org/patchwork_queue/patch_series_gate.py", "_write_synthetic_escalation_round", "json.dumps", 1),
        ("ai_org/patchwork_queue/receive.py", "reform_patch_series", "git_wrapper.commit_files", 1),
        ("ai_org/patchwork_queue/review.py", "_review_round_records", "git_wrapper.tree_files", 1),
        ("ai_org/patchwork_queue/review.py", "_review_round_records", "json.loads", 1),
        ("ai_org/patchwork_queue/review.py", "_write_marker", "git_wrapper.commit_files", 1),
        ("ai_org/review_bodies.py", "read_record", "git_wrapper.show_file", 1),
        ("ai_org/review_bodies.py", "read_record", "git_wrapper.show_file", 2),
        ("ai_org/review_bodies.py", "parse_record", "json.loads", 1),
        ("ai_org/review_bodies.py", "parse_record", "json.dumps", 1),
        ("ai_org/review_bodies.py", "parse_record", "json.loads", 2),
        ("ai_org/patch_author/code_worker.py", "load_brief", "git_wrapper.show_file", 1),
        # The second questions read is the historical-coordinate fallback for
        # the registered canonical questions boundary above.
        ("ai_org/patch_author/code_worker.py", "load_brief", "git_wrapper.show_file", 2),
        ("ai_org/patch_author/code_worker.py", "load_brief", "git_wrapper.show_file", 2),
        ("ai_org/patch_author/code_worker.py", "load_brief", "json.loads", 2),
        ("ai_org/patch_author/code_worker.py", "load_brief", "git_wrapper.show_file", 3),
        ("ai_org/patch_author/code_worker.py", "load_brief", "git_wrapper.tree_files", 1),
        ("ai_org/patch_author/code_worker.py", "load_brief", "git_wrapper.show_file", 4),
        ("ai_org/patch_author/code_worker.py", "load_brief", "git_wrapper.show_file", 5),
        ("ai_org/patch_author/code_worker.py", "load_brief", "git_wrapper.show_file", 6),
        ("ai_org/patch_author/code_worker.py", "load_brief", "json.loads", 3),
        ("ai_org/patch_author/code_worker.py", "load_brief", "json.loads", 4),
        ("ai_org/patch_author/code_worker.py", "_commit_implementation_experience_report", "git_wrapper.show_file", 1),
        ("ai_org/patch_author/code_worker.py", "_commit_implementation_experience_report", "git_wrapper.show_file", 2),
        ("ai_org/patch_author/code_worker.py", "_commit_implementation_experience_report", "git_wrapper.commit_files", 1),
        ("ai_org/patch_author/functional_check.py", "_patchwork_check_acknowledgement_rejection", "json.dumps", 1),
        ("ai_org/patch_author/functional_check.py", "_patchwork_check_acknowledgement_rejection", "json.dumps", 2),
        ("ai_org/patch_author/functional_check.py", "_patchwork_check_acknowledgement_rejection", "json.dumps", 3),
        ("ai_org/patch_author/functional_check.py", "_must_answer_questions_rejection", "git_wrapper.show_file", 1),
        ("ai_org/patch_author/functional_check.py", "_must_answer_questions_rejection", "git_wrapper.show_file", 2),
        ("ai_org/patch_author/functional_check.py", "_must_answer_questions_rejection", "json.dumps", 1),
        ("ai_org/patch_author/discovery.py", "_authorable_nodes", "git_wrapper.tree_files", 1),
        ("ai_org/patch_author/discovery.py", "_read_committed_json", "git_wrapper.show_file", 1),
        ("ai_org/patch_author/discovery.py", "_read_committed_json", "json.loads", 1),
        ("ai_org/patchwork_queue/patch_series_gate.py", "_load_manifest_network_from_git", "git_wrapper.tree_files", 1),
        ("ai_org/patchwork_queue/patch_series_gate.py", "_project_tree_patchwork_check_events_from_git", "git_wrapper.tree_files", 1),
        ("ai_org/patchwork_queue/patch_series_gate.py", "_projected_root_children_from_git", "git_wrapper.tree_files", 1),
        ("ai_org/patchwork_queue/patch_series_gate.py", "_rebaseline", "git_wrapper.commit_files", 1),
        ("ai_org/patchwork_queue/patch_series_gate.py", "_refine", "git_wrapper.commit_files", 1),
        ("ai_org/patchwork_queue/patch_series_gate.py", "_refine", "git_wrapper.commit_files", 2),
        ("ai_org/patchwork_queue/patch_series_gate.py", "_refine", "git_wrapper.commit_files", 3),
        ("ai_org/patchwork_queue/patch_series_gate.py", "_refine", "git_wrapper.file_exists", 1),
        ("ai_org/patchwork_queue/patch_series_gate.py", "_refine", "git_wrapper.file_exists", 2),
        ("ai_org/patchwork_queue/patch_series_gate.py", "_write_node_manifest", "git_wrapper.commit_files", 1),
        ("ai_org/patchwork_queue/patch_series_gate.py", "elaborate", "git_wrapper.commit_files", 1),
        ("ai_org/patchwork_queue/patch_series_gate.py", "mark_stale", "git_wrapper.commit_files", 1),
        ("ai_org/patchwork_queue/patch_series_gate.py", "migrate_legacy_tree_to_edges", "json.dumps", 1),
        ("ai_org/patchwork_queue/patch_series_gate.py", "rebaseline_pending", "git_wrapper.file_exists", 1),
        ("ai_org/patchwork_queue/patch_series_gate.py", "revalidate_stale", "git_wrapper.commit_files", 1),
        ("ai_org/patchwork_queue/patch_series_gate.py", "revalidate_stale", "git_wrapper.commit_files", 2),
        ("ai_org/patchwork_queue/patch_series_gate.py", "split_pending", "git_wrapper.file_exists", 1),
        ("ai_org/patchwork_queue/patch_series_gate.py", "stale_revalidation_pending", "git_wrapper.file_exists", 1),
        ("ai_org/patchwork_queue/patch_series_gate.py", "stamp_children", "git_wrapper.commit_files", 1),
        ("ai_org/patchwork_queue/patch_series_gate.py", "validate_lineage_gate_from_git", "git_wrapper.tree_files", 1),
    }
)


class CatalogError(ValueError):
    """A stable catalog-structure rejection."""


@dataclass(frozen=True, slots=True)
class ContractIdentity:
    api_version: str
    kind: str
    variant: str


@dataclass(frozen=True, slots=True)
class SourceSelector:
    path: str
    scope: str
    node_kind: str
    operation: str
    occurrence: int

    @property
    def key(self) -> tuple[str, str, str, str, int]:
        return (self.path, self.scope, self.node_kind, self.operation, self.occurrence)


@dataclass(frozen=True, slots=True)
class CatalogRow:
    site_id: str
    context_id: str
    cohort: str
    disposition: str
    executable: bool
    source_selector: SourceSelector
    contract: ContractIdentity | None


@dataclass(frozen=True, slots=True)
class Catalog:
    schema: str
    reconstruction: Mapping[str, Any]
    rows: tuple[CatalogRow, ...]
    sha256: str


@dataclass(frozen=True, slots=True)
class ProducerLifecycleCompatibilityVerification:
    """One successful unchecked-to-verified dormant-matrix transition."""

    status: str
    manifest: str
    catalog_sha256: str
    registrations: tuple[tuple[str, str, str, str], ...]
    mode: str = "dormant"
    writers_activated: bool = False
    authority_sha256: str = EXPECTED_AUTHORITY_SHA256
    manifest_sha256: str = EXPECTED_MANIFEST_SHA256
    public_contract_sha256: str = EXPECTED_PUBLIC_CONTRACT_SHA256


@dataclass(frozen=True, slots=True)
class DiscoveredSite:
    selector: SourceSelector
    line: int
    column: int

    def report_value(self) -> dict[str, Any]:
        return {
            "path": self.selector.path,
            "scope": self.selector.scope,
            "node_kind": self.selector.node_kind,
            "operation": self.selector.operation,
            "occurrence": self.selector.occurrence,
            "line": self.line,
            "column": self.column,
        }


def load_catalog(path: str | Path = CATALOG_PATH) -> Catalog:
    """Load the structurally valid, closed catalog used by consumers."""
    return _load_catalog(path, require_closed=True)


def _load_catalog(path: str | Path, *, require_closed: bool) -> Catalog:
    """Load and structurally validate a catalog for consumption or reporting.

    Ordinary consumers require the frozen, closed catalog.  The coverage
    reporter alone opts out of that final assertion so it can retain and
    identify pending rows in the fail-closed report supplied to the gate.
    """
    catalog_path = Path(path)
    raw = catalog_path.read_bytes()
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CatalogError(f"catalog_invalid_json:{exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema") != CATALOG_SCHEMA:
        raise CatalogError("catalog_schema")
    reconstruction = payload.get("reconstruction")
    if not isinstance(reconstruction, dict):
        raise CatalogError("catalog_reconstruction")
    raw_rows = payload.get("rows")
    if not isinstance(raw_rows, list):
        raise CatalogError("catalog_rows")
    rows = tuple(_parse_row(value, index) for index, value in enumerate(raw_rows))
    site_ids = [row.site_id for row in rows]
    if len(site_ids) != len(set(site_ids)):
        raise CatalogError("catalog_duplicate_site_id")
    disposition_counts = Counter(row.disposition for row in rows)
    expected_counts = (
        reconstruction.get("expected_sites"),
        reconstruction.get("admitted_sites"),
        reconstruction.get("pending_sites"),
    )
    if any(not isinstance(value, int) or isinstance(value, bool) for value in expected_counts):
        raise CatalogError("catalog_reconstruction_counts")
    if expected_counts != (
        len(rows),
        disposition_counts["admitted"],
        disposition_counts["pending"],
    ):
        raise CatalogError("catalog_reconstruction_mismatch")
    if require_closed and disposition_counts["pending"] != 0:
        raise CatalogError("catalog_pending_rows")
    by_context: dict[str, list[CatalogRow]] = defaultdict(list)
    for row in rows:
        by_context[row.context_id].append(row)
    for context_rows in by_context.values():
        cohorts = {row.cohort for row in context_rows}
        dispositions = {row.disposition for row in context_rows}
        contracts = {row.contract for row in context_rows}
        if len(cohorts) != 1 or len(dispositions) != 1:
            raise CatalogError("catalog_context_ambiguous")
        disposition = next(iter(dispositions))
        if disposition == "admitted" and (contracts == {None} or len(contracts) != 1):
            raise CatalogError("catalog_context_contract_ambiguous")
        if disposition == "pending" and contracts != {None}:
            raise CatalogError("catalog_pending_contract")
    lifecycle_matrix = {
        (
            context_id,
            contract.api_version,
            contract.kind,
            contract.variant,
        )
        for context_id, context_rows in by_context.items()
        if {row.cohort for row in context_rows} == {PRODUCER_LIFECYCLE_COHORT}
        for contract in {row.contract for row in context_rows}
        if contract is not None
        and {row.disposition for row in context_rows} == {"admitted"}
        and all(row.executable for row in context_rows)
    }
    if lifecycle_matrix != set(PINNED_PRODUCER_LIFECYCLE_IDENTITIES):
        raise CatalogError("catalog_producer_lifecycle_matrix")
    if len(rows) != EXPECTED_SITE_COUNT:
        raise CatalogError("catalog_site_count")
    return Catalog(
        schema=CATALOG_SCHEMA,
        reconstruction=dict(reconstruction),
        rows=rows,
        sha256=hashlib.sha256(raw).hexdigest(),
    )


def catalog_rows(path: str | Path = CATALOG_PATH) -> tuple[CatalogRow, ...]:
    """Return the immutable catalog rows in checked-in order."""
    return load_catalog(path).rows


def verify_producer_lifecycle_compatibility_matrix(
    *,
    client: BodyCodecClient | None = None,
    catalog_path: str | Path = CATALOG_PATH,
    mirrored_catalog_path: str | Path = MIRRORED_CATALOG_PATH,
) -> ProducerLifecycleCompatibilityVerification:
    """Verify the dormant producer matrix without activating any writer.

    The transition binds the target-side catalog, the engine-owned mirror, and
    the engine's inspected contract snapshots to the same pinned six-member
    identity cohort.  It deliberately performs no Git read or write.
    """

    catalog_bytes = Path(catalog_path).read_bytes()
    mirrored_bytes = Path(mirrored_catalog_path).read_bytes()
    if catalog_bytes != mirrored_bytes:
        raise CatalogError("producer_lifecycle_catalog_mirror")
    catalog = load_catalog(catalog_path)
    mirrored_catalog = load_catalog(mirrored_catalog_path)
    if (
        catalog.sha256 != mirrored_catalog.sha256
        or catalog.sha256 != EXPECTED_CATALOG_SHA256
    ):
        raise CatalogError("producer_lifecycle_catalog_digest")

    result = (client or BodyCodecClient()).request("inspect")
    artifact = result.artifact
    if artifact is None:
        raise CatalogError("producer_lifecycle_contract_snapshot_artifact")
    try:
        inspection = strict_json_loads(artifact.data)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise CatalogError("producer_lifecycle_contract_snapshot_json") from exc
    if not isinstance(inspection, Mapping):
        raise CatalogError("producer_lifecycle_contract_snapshot_object")
    expected_snapshot_pins = (
        (
            "authority_sha256",
            EXPECTED_AUTHORITY_SHA256,
            "producer_lifecycle_contract_snapshot_authority",
        ),
        (
            "catalog_sha256",
            EXPECTED_CATALOG_SHA256,
            "producer_lifecycle_contract_snapshot_catalog",
        ),
        (
            "manifest_sha256",
            EXPECTED_MANIFEST_SHA256,
            "producer_lifecycle_contract_snapshot_manifest",
        ),
        (
            "public_contract_sha256",
            EXPECTED_PUBLIC_CONTRACT_SHA256,
            "producer_lifecycle_contract_snapshot_public_contract",
        ),
    )
    for field, expected, rule in expected_snapshot_pins:
        if inspection.get(field) != expected:
            raise CatalogError(rule)

    raw_contracts = inspection.get("contracts")
    if not isinstance(raw_contracts, list):
        raise CatalogError("producer_lifecycle_contract_snapshots")
    snapshots: dict[str, Mapping[str, Any]] = {}
    for snapshot in raw_contracts:
        if not isinstance(snapshot, Mapping):
            raise CatalogError("producer_lifecycle_contract_snapshot_shape")
        if snapshot.get("manifest") != PRODUCER_LIFECYCLE_MANIFEST:
            continue
        context_id = snapshot.get("context_id")
        if not isinstance(context_id, str) or not context_id or context_id in snapshots:
            raise CatalogError("producer_lifecycle_contract_snapshot_context")
        snapshots[context_id] = snapshot

    pinned_contexts = {
        context_id for context_id, _api_version, _kind, _variant
        in PINNED_PRODUCER_LIFECYCLE_IDENTITIES
    }
    if set(snapshots) != pinned_contexts:
        raise CatalogError("producer_lifecycle_contract_snapshot_matrix")

    for context_id, api_version, kind, variant in PINNED_PRODUCER_LIFECYCLE_IDENTITIES:
        snapshot = snapshots[context_id]
        if snapshot.get("contract") != {
            "api_version": api_version,
            "kind": kind,
            "variant": variant,
        }:
            raise CatalogError("producer_lifecycle_contract_snapshot_identity")
        definition_sha256 = snapshot.get("definition_sha256")
        if (
            snapshot.get("definition_path") != f"schema/semantic_status.cue:#{kind}"
            or not isinstance(definition_sha256, str)
            or len(definition_sha256) != 64
            or any(character not in "0123456789abcdef" for character in definition_sha256)
            or snapshot.get("projection_profiles") != ["consumer-json-v1"]
            or snapshot.get("schema_profiles") != []
            or snapshot.get("historical_writers") != []
            or snapshot.get("historical_aliases") != []
        ):
            raise CatalogError("producer_lifecycle_contract_snapshot_policy")

    return ProducerLifecycleCompatibilityVerification(
        status="verified",
        manifest=PRODUCER_LIFECYCLE_MANIFEST,
        authority_sha256=EXPECTED_AUTHORITY_SHA256,
        catalog_sha256=catalog.sha256,
        manifest_sha256=EXPECTED_MANIFEST_SHA256,
        public_contract_sha256=EXPECTED_PUBLIC_CONTRACT_SHA256,
        registrations=PINNED_PRODUCER_LIFECYCLE_IDENTITIES,
        mode="dormant",
        writers_activated=False,
    )


def lookup_context(context_id: str, path: str | Path = CATALOG_PATH) -> dict[str, Any]:
    """Return an admitted result or a stable, payload-free not-admitted result."""
    rows = tuple(row for row in load_catalog(path).rows if row.context_id == context_id)
    if not rows:
        return {
            "ok": False,
            "status": "not_admitted",
            "code": "context_not_admitted",
            "context_id": context_id,
            "cohort": "",
            "disposition": "unknown",
            "executable": False,
        }
    cohorts = {row.cohort for row in rows}
    dispositions = {row.disposition for row in rows}
    contracts = {row.contract for row in rows}
    executable = all(row.executable for row in rows)
    admitted = (
        dispositions == {"admitted"}
        and executable
        and len(cohorts) == 1
        and len(contracts) == 1
        and None not in contracts
    )
    result: dict[str, Any] = {
        "ok": admitted,
        "status": "admitted" if admitted else "not_admitted",
        "context_id": context_id,
        "cohort": next(iter(cohorts)) if len(cohorts) == 1 else "",
        "disposition": next(iter(dispositions)) if len(dispositions) == 1 else "ambiguous",
        "executable": admitted,
    }
    if admitted:
        contract = next(iter(contracts))
        assert contract is not None
        result["contract"] = {
            "apiVersion": contract.api_version,
            "kind": contract.kind,
            "variant": contract.variant,
        }
    else:
        result["code"] = "context_not_admitted"
    return result


def registered_body_io_source_scan(
    repository_root: str | Path = REPOSITORY_ROOT,
    *,
    catalog_path: str | Path = CATALOG_PATH,
) -> dict[str, Any]:
    """Reconcile catalog selectors with one stdlib-AST scan of ``ai_org``."""
    root = Path(repository_root)
    catalog = _load_catalog(catalog_path, require_closed=False)
    indexes = _source_indexes(
        root,
        {row.source_selector.path for row in catalog.rows}
        | {key[0] for key in REPLACED_SELECTOR_ALIASES.values()},
    )
    matches: dict[tuple[str, str, str, str, int], list[DiscoveredSite]] = defaultdict(list)
    for index in indexes.values():
        for site in (*index.functions, *index.calls):
            matches[site.selector.key].append(site)

    selector_rows: dict[tuple[str, str, str, str, int], list[CatalogRow]] = defaultdict(list)
    for row in catalog.rows:
        selector_rows[row.source_selector.key].append(row)

    missing_ids: list[str] = []
    ambiguous_ids: list[str] = []
    mapped_ids: list[str] = []
    for row in catalog.rows:
        selected = matches.get(REPLACED_SELECTOR_ALIASES.get(row.source_selector.key, row.source_selector.key), [])
        duplicate_rows = selector_rows[row.source_selector.key]
        if not selected:
            missing_ids.append(row.site_id)
        elif len(selected) != 1 or len(duplicate_rows) != 1:
            ambiguous_ids.append(row.site_id)
        else:
            mapped_ids.append(row.site_id)

    policy_sites = _policy_sites(root)
    catalog_keys = set(selector_rows) | set(REPLACED_SELECTOR_ALIASES.values())
    orphaned = [
        site
        for site in policy_sites
        if site.selector.key not in catalog_keys
        and (
            site.selector.path,
            site.selector.scope,
            site.selector.operation,
            site.selector.occurrence,
        )
        not in ADMITTED_BOUNDARY_CALLS
    ]
    admitted_coordinates = {
        (row.source_selector.path, row.source_selector.scope)
        for row in catalog.rows
        if row.disposition == "admitted"
    }
    boundary_calls = _calls_in_coordinates(root, admitted_coordinates)
    admitted_catalog_calls = {
        (
            row.source_selector.path,
            row.source_selector.scope,
            row.source_selector.operation,
            row.source_selector.occurrence,
        )
        for row in catalog.rows
        if row.disposition == "admitted" and row.source_selector.node_kind == "call"
    }
    admitted_catalog_calls.update(
        (path, scope, operation, occurrence)
        for source, (path, scope, node_kind, operation, occurrence) in REPLACED_SELECTOR_ALIASES.items()
        if node_kind == "call"
        and any(row.disposition == "admitted" and row.source_selector.key == source for row in catalog.rows)
    )
    admitted_bypasses = [
        site
        for site in boundary_calls
        if site.selector.operation in SCANNED_OPERATIONS
        and (
            site.selector.path,
            site.selector.scope,
            site.selector.operation,
            site.selector.occurrence,
        )
        not in ADMITTED_BOUNDARY_CALLS | admitted_catalog_calls
    ]

    invalid_pending = [
        row.site_id
        for row in catalog.rows
        if row.disposition == "pending" and row.executable
    ]
    invalid_admitted = [
        row.site_id
        for row in catalog.rows
        if row.disposition == "admitted" and not row.executable
    ]
    counts = Counter(row.disposition for row in catalog.rows)
    admitted_contexts = {
        row.context_id for row in catalog.rows if row.disposition == "admitted"
    }
    admitted_body_kinds = {
        row.contract.kind
        for row in catalog.rows
        if row.disposition == "admitted" and row.contract is not None
    }
    newly_admitted_context_ids = sorted(admitted_contexts - FROZEN_CONTEXT_IDS)
    newly_admitted_body_kind_names = sorted(admitted_body_kinds - FROZEN_BODY_KINDS)
    passed = not (
        counts["pending"]
        or missing_ids
        or ambiguous_ids
        or orphaned
        or admitted_bypasses
        or invalid_pending
        or invalid_admitted
        or newly_admitted_context_ids
        or newly_admitted_body_kind_names
    )
    return {
        "schema": REPORT_SCHEMA,
        "catalog_schema": catalog.schema,
        "catalog_sha256": catalog.sha256,
        "reconstruction": dict(catalog.reconstruction),
        "registered_sites": len(catalog.rows),
        "mapped_sites": len(mapped_ids),
        "admitted_sites": counts["admitted"],
        "registered_contexts": len(admitted_contexts),
        "registered_body_kinds": len(admitted_body_kinds),
        "pending_sites": counts["pending"],
        "missing_rows": len(missing_ids),
        "missing_site_ids": missing_ids,
        "ambiguous_rows": len(ambiguous_ids),
        "ambiguous_site_ids": ambiguous_ids,
        "orphaned_sites": len(orphaned),
        "orphaned_source_selectors": [site.report_value() for site in orphaned],
        "admitted_scope_bypasses": len(admitted_bypasses),
        "admitted_scope_bypass_selectors": [site.report_value() for site in admitted_bypasses],
        "pending_executable_rows": len(invalid_pending),
        "pending_executable_site_ids": invalid_pending,
        "admitted_non_executable_rows": len(invalid_admitted),
        "admitted_non_executable_site_ids": invalid_admitted,
        "newly_admitted_contexts": len(newly_admitted_context_ids),
        "newly_admitted_context_ids": newly_admitted_context_ids,
        "newly_admitted_body_kinds": len(newly_admitted_body_kind_names),
        "newly_admitted_body_kind_names": newly_admitted_body_kind_names,
        "result": "pass" if passed else "fail",
    }


def durable_body_coverage_report(
    repository_root: str | Path = REPOSITORY_ROOT,
    *,
    catalog_path: str | Path = CATALOG_PATH,
) -> dict[str, Any]:
    """Named report entry point used by architecture and operator checks."""
    return registered_body_io_source_scan(repository_root, catalog_path=catalog_path)


def require_zero_gap_coverage(report: Mapping[str, Any]) -> None:
    """Fail closed unless *report* proves the catalog migration is complete."""
    expected_counts = {
        "registered_sites": EXPECTED_SITE_COUNT,
        "mapped_sites": EXPECTED_SITE_COUNT,
        "admitted_sites": EXPECTED_SITE_COUNT,
        "registered_contexts": EXPECTED_CONTEXT_COUNT,
        "registered_body_kinds": EXPECTED_BODY_KIND_COUNT,
        **{field: 0 for field in ZERO_GAP_FIELDS},
    }
    mismatches: dict[str, Any] = {}
    for field, expected in expected_counts.items():
        value = report.get(field)
        # JSON booleans and floating-point lookalikes compare equal to Python
        # integers.  Neither is valid counter evidence at the contract-phase
        # boundary: counts must retain their exact JSON integer type.
        if type(value) is not int or value != expected:
            mismatches[field] = value
    for field in ZERO_GAP_DETAIL_FIELDS:
        # A zero counter with retained identities is an internally
        # inconsistent report, not an empty-set proof.
        if report.get(field) != []:
            mismatches[field] = report.get(field)
    if report.get("schema") != REPORT_SCHEMA:
        mismatches["schema"] = report.get("schema")
    if report.get("catalog_schema") != CATALOG_SCHEMA:
        mismatches["catalog_schema"] = report.get("catalog_schema")
    if report.get("catalog_sha256") != EXPECTED_CATALOG_SHA256:
        mismatches["catalog_sha256"] = report.get("catalog_sha256")
    if report.get("result") != "pass":
        mismatches["result"] = report.get("result")
    if mismatches:
        details = ",".join(f"{field}={mismatches[field]!r}" for field in sorted(mismatches))
        raise CatalogError(f"catalog_zero_gap_required:{details}")


def zero_gap_coverage_gate(
    repository_root: str | Path = REPOSITORY_ROOT,
    *,
    catalog_path: str | Path = CATALOG_PATH,
) -> dict[str, Any]:
    """Return the report only after enforcing the migration-closing contract."""
    report = durable_body_coverage_report(repository_root, catalog_path=catalog_path)
    require_zero_gap_coverage(report)
    return report


def main() -> int:
    """Emit the canonical CI report; a non-zero exit means admission is blocked."""
    try:
        report = zero_gap_coverage_gate()
    except CatalogError as error:
        json.dump(
            {"schema": REPORT_SCHEMA, "result": "fail", "error": str(error)},
            sys.stdout,
            sort_keys=True,
            separators=(",", ":"),
        )
        sys.stdout.write("\n")
        return 1
    json.dump(report, sys.stdout, sort_keys=True, separators=(",", ":"))
    sys.stdout.write("\n")
    return 0


@dataclass(frozen=True, slots=True)
class _SourceIndex:
    functions: tuple[DiscoveredSite, ...]
    calls: tuple[DiscoveredSite, ...]


class _SiteVisitor(ast.NodeVisitor):
    def __init__(self, path: str) -> None:
        self.path = path
        self.scopes: list[str] = []
        self.functions: list[DiscoveredSite] = []
        self.calls: list[DiscoveredSite] = []
        self.function_occurrences: Counter[tuple[str, str]] = Counter()
        self.call_occurrences: Counter[tuple[str, str]] = Counter()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802 - ast visitor API
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        self._visit_function(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.scopes.append(node.name)
        scope = ".".join(self.scopes)
        key = (scope, "definition")
        self.function_occurrences[key] += 1
        self.functions.append(
            DiscoveredSite(
                SourceSelector(
                    path=self.path,
                    scope=scope,
                    node_kind="function",
                    operation="definition",
                    occurrence=self.function_occurrences[key],
                ),
                line=node.lineno,
                column=node.col_offset,
            )
        )
        self.generic_visit(node)
        self.scopes.pop()

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        operation = _qualified_name(node.func)
        scope = ".".join(self.scopes) if self.scopes else "<module>"
        key = (scope, operation)
        self.call_occurrences[key] += 1
        self.calls.append(
            DiscoveredSite(
                SourceSelector(
                    path=self.path,
                    scope=scope,
                    node_kind="call",
                    operation=operation,
                    occurrence=self.call_occurrences[key],
                ),
                line=node.lineno,
                column=node.col_offset,
            )
        )
        self.generic_visit(node)


def _parse_row(value: Any, index: int) -> CatalogRow:
    if not isinstance(value, dict):
        raise CatalogError(f"catalog_row:{index}")
    site_id = _required_string(value, "site_id", index)
    context_id = _required_string(value, "context_id", index)
    cohort = _required_string(value, "cohort", index)
    disposition = _required_string(value, "disposition", index)
    if disposition not in {"admitted", "pending"}:
        raise CatalogError(f"catalog_disposition:{index}")
    executable = value.get("executable")
    if not isinstance(executable, bool):
        raise CatalogError(f"catalog_executable:{index}")
    selector_value = value.get("source_selector")
    if not isinstance(selector_value, dict):
        raise CatalogError(f"catalog_source_selector:{index}")
    selector = SourceSelector(
        path=_required_string(selector_value, "path", index),
        scope=_required_string(selector_value, "scope", index),
        node_kind=_required_string(selector_value, "node_kind", index),
        operation=_required_string(selector_value, "operation", index),
        occurrence=selector_value.get("occurrence"),
    )
    _validate_selector(selector, index)
    contract_value = value.get("contract")
    contract: ContractIdentity | None = None
    if contract_value is not None:
        if not isinstance(contract_value, dict):
            raise CatalogError(f"catalog_contract:{index}")
        contract = ContractIdentity(
            api_version=_required_string(contract_value, "apiVersion", index),
            kind=_required_string(contract_value, "kind", index),
            variant=_required_string(contract_value, "variant", index),
        )
    if disposition == "pending" and executable:
        raise CatalogError(f"catalog_pending_executable:{index}")
    if disposition == "pending" and contract is not None:
        raise CatalogError(f"catalog_pending_contract:{index}")
    if disposition == "admitted" and (not executable or contract is None):
        raise CatalogError(f"catalog_admitted_incomplete:{index}")
    return CatalogRow(
        site_id=site_id,
        context_id=context_id,
        cohort=cohort,
        disposition=disposition,
        executable=executable,
        source_selector=selector,
        contract=contract,
    )


def _required_string(value: Mapping[str, Any], key: str, index: int) -> str:
    selected = value.get(key)
    if not isinstance(selected, str) or not selected:
        raise CatalogError(f"catalog_{key}:{index}")
    return selected


def _validate_selector(selector: SourceSelector, index: int) -> None:
    path = PurePosixPath(selector.path)
    if path.is_absolute() or ".." in path.parts or path.suffix != ".py" or not selector.path.startswith("ai_org/"):
        raise CatalogError(f"catalog_selector_path:{index}")
    if selector.node_kind not in {"call", "function"}:
        raise CatalogError(f"catalog_selector_kind:{index}")
    if not isinstance(selector.occurrence, int) or isinstance(selector.occurrence, bool) or selector.occurrence < 1:
        raise CatalogError(f"catalog_selector_occurrence:{index}")


def _source_indexes(root: Path, paths: Iterable[str]) -> dict[str, _SourceIndex]:
    return {path: _source_index(root, path) for path in sorted(set(paths))}


def _source_index(root: Path, path: str) -> _SourceIndex:
    source_path = root / PurePosixPath(path)
    try:
        source = source_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=path)
    except (OSError, UnicodeError, SyntaxError):
        return _SourceIndex((), ())
    visitor = _SiteVisitor(path)
    visitor.visit(tree)
    return _SourceIndex(tuple(visitor.functions), tuple(visitor.calls))


def _policy_sites(root: Path) -> tuple[DiscoveredSite, ...]:
    sites: list[DiscoveredSite] = []
    package_root = root / "ai_org"
    for source_path in sorted(package_root.rglob("*.py")):
        rel_path = source_path.relative_to(root).as_posix()
        if rel_path in EXCLUDED_PATHS:
            continue
        index = _source_index(root, rel_path)
        sites.extend(
            site
            for site in index.calls
            if site.selector.operation in SCANNED_OPERATIONS
            and site.selector.scope not in EXCLUDED_SCOPES
            and site.selector.scope not in {"read_semantic", "write_semantic"}
        )
    for scope in (
        "read_semantic",
        "write_semantic",
        "read_request_provenance",
        "read_request_outcome",
        "prepare_terminal_request",
        "publish_terminal_request",
    ):
        index = _source_index(root, "ai_org/git_wrapper.py")
        sites.extend(
            site
            for site in index.functions
            if site.selector.scope == scope
        )
    for scope in (
        "read_cover_letter",
        "read_patch_series_cohort",
        "prepare_patch_series_cohort",
        "prepare_root_technical_approach_preview",
    ):
        index = _source_index(root, "ai_org/patch_series_bodies.py")
        sites.extend(site for site in index.functions if site.selector.scope == scope)
    index = _source_index(root, "ai_org/contributor_handoff.py")
    for scope in (
        "prepare_questions", "parse_questions", "parse_experience", "prepare_result",
        "parse_result", "prepare_functional_carrier", "prepare_result_contract",
    ):
        sites.extend(site for site in index.functions if site.selector.scope == scope)
    return tuple(sorted(sites, key=lambda site: site.selector.key))


def _calls_in_coordinates(
    root: Path,
    coordinates: Iterable[tuple[str, str]],
) -> tuple[DiscoveredSite, ...]:
    by_path: dict[str, set[str]] = defaultdict(set)
    for path, scope in coordinates:
        by_path[path].add(scope)
    selected: list[DiscoveredSite] = []
    for path, scopes in by_path.items():
        selected.extend(
            site
            for site in _source_index(root, path).calls
            if site.selector.scope in scopes
        )
    return tuple(selected)


def _qualified_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _qualified_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


if __name__ == "__main__":
    raise SystemExit(main())
