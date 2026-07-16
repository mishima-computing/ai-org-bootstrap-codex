"""Code worker: drive the committed patch plan in the author's own clone.

[RATIFIED, brief 23] This module is the CODE-authoring phase work.py used to
delegate whole-shot: given an authoring announcement and the author's clone,
it drives the committed patch plan item by item. The committed artifacts ARE
the brief (cover letter, technical-approach-plan, patch plan node,
implementation-result schema, spine when present) — nothing off-git is read.

決定論ハーネス law: everything the model does not have to judge is mechanism.
Code owns branch/worktree mechanics, item sequencing, per-item commits,
declared-check invocation, result-artifact writing, and retry fingerprinting.
The model owns exactly one thing per invocation: writing the code for ONE
patch-plan item inside a bounded window.

BOUNDED WINDOWS law (条件文は契約ではない — proven three ways: decision
slot-drop @400KB, reviewer oscillation, prior_art non-engagement @725KB): the
model NEVER receives the whole approach tree or whole brief in one prompt.
A per-item window = the item + its referenced approach nodes (1-hop over
id-typed edges, the review scope-builder) + the files prior items touched +
the committed contract facts. Do not "helpfully" widen it.

PARENT/CHILD structure (brief 23 addendum): the PARENT is the patch_author
process holding the author identity (work.implement_announced). CHILDREN are
the worker invocations the parent spawns per patch-plan item partition —
author_items() calls below. The partition is the ONLY conflict-prevention
mechanism anywhere and it is intra-family only (家族内重複=無駄); there is NO
inter-family arbitration code here and none may be added (家族間重複=競争).
[PROVISIONAL-W1] process model: children run in-process and sequentially in
one shared worktree. The partition logic and the no-arbitration invariant are
the contract; the execution vehicle may later become real processes/clones.

Gadget doctrine: the initial per-item prompt is free-form. Structure skeletons
appear only in REACTIVE repair rounds keyed by the typed error. Retry
discipline is the codex_exec fingerprint law: a retry must change the odds or
stop (a repeated failure fingerprint is permanent — fail closed with the item
id).
"""
from __future__ import annotations

import importlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Any, Mapping

import ai_org.log as org_log
from ai_org import engineering_precedent_store as precedent_store
from ai_org import contributor_handoff, git_wrapper, network_bodies, patch_series_bodies
from ai_org.codex_reset import RESET_RE as _CODEX_RESET_RE
from ai_org.codex_reset import TRANSIENT_RETRIES_EXHAUSTED as _TRANSIENT_RETRIES_EXHAUSTED
from ai_org.codex_reset import codex_resume_command as _codex_resume_command
from ai_org.codex_reset import codex_not_before as _codex_not_before
from ai_org.codex_reset import run_with_reset_retries as _run_with_reset_retries
from ai_org.codex_reset import transient_retries_exhausted as _transient_retries_exhausted
from ai_org.codex_reset import wait_for_codex_reset as _shared_wait_for_codex_reset
from ai_org.patch_author import contract as patch_author_contract
from ai_org.patch_author import producer_lifecycle
from ai_org.patchwork_queue import codex_exec
from ai_org.patchwork_queue import patch_series_gate
from ai_org.patchwork_queue.field_registry import (
    STRING_ARRAY_FIELDS,
    STRING_FIELDS,
    WORK_ORDER_VIEW_FIELDS,
    validate_tech_stack,
    validate_user_experience_requirements,
)


# [PROVISIONAL-W2] bounded repair budget per item: one free-form attempt plus
# at most MAX_REPAIR_ROUNDS reactive repair rounds. The fingerprint law can
# stop earlier (a repeated typed-error fingerprint is permanent immediately).
MAX_REPAIR_ROUNDS = 2

# [PROVISIONAL-R2] precedent facets cap per term inside an item window: the
# window law (認知負債を借りない) outranks completeness; the store's own row
# order is the ranking, truncation is always recorded, never silent.
PRECEDENT_FACETS_PER_TERM = 3

RESULT_ARTIFACT_PATH = contributor_handoff.RESULT_PATH
RFC_FIELDS = WORK_ORDER_VIEW_FIELDS


# ---------------------------------------------------------------------------
# Brief loading — the committed artifacts ARE the brief (design D).
# ---------------------------------------------------------------------------

def _root_identity(snapshot: object) -> dict[str, Any]:
    identity = getattr(snapshot, "identity", None)
    return dict(identity()) if callable(identity) else {}


def _decision_dict(decision: object) -> dict[str, Any]:
    if isinstance(decision, Mapping):
        return dict(decision)
    as_dict = getattr(decision, "as_dict", None)
    return dict(as_dict()) if callable(as_dict) else {}


def load_brief(
    repo,
    series_branch: str,
    node_path: str,
    *,
    producer_gate_route: str = "producer_code_authoring",
) -> dict[str, Any]:
    """Load every committed brief artifact for one series address. Typed, total."""
    # Freeze the publication tree before loading any member of the contributor
    # handoff.  A branch name is a lifecycle address, not immutable evidence;
    # using it for each read could combine a contract from one commit with
    # questions or node anchors from another if the series advances mid-load.
    series_snapshot = git_wrapper.head_sha(repo, series_branch)
    source_ref = series_snapshot or series_branch
    root_snapshot = patch_series_bodies.classify_root_generation(repo, source_ref)
    producer_v2 = root_snapshot.generation == patch_series_bodies.ROOT_GENERATION_V2
    producer_authorability_decision = None
    if producer_v2:
        # Initial implementation and feedback are separate authoring
        # transitions, but both consume the same frozen producer-pair gate.
        # Vet ready cohorts too: classification proves representation
        # completeness, while this decision proves route authorability.
        producer_authorability_decision = patch_series_gate.vet_producer_pair_closure(
            repo,
            series_branch,
            producer_gate_route,
            frozen_root_oid=series_snapshot,
        )
        decision_dict = _decision_dict(producer_authorability_decision)
        decision_source = decision_dict.get("source")
        if (
            not isinstance(decision_source, Mapping)
            or decision_source.get("route") != producer_gate_route
            or decision_source.get("frozen_root_oid") != series_snapshot
        ):
            return {
                "ok": False,
                "status": "producer_authoring_decision_invalid",
                "series_branch": series_branch,
                "frozen_oid": root_snapshot.frozen_oid,
                "detail": "producer readiness decision does not match the authoring route and frozen snapshot",
                "authorability_decision": decision_dict,
                "root_generation": _root_identity(root_snapshot),
                **_root_identity(root_snapshot),
            }
        if producer_authorability_decision.blocked:
            return {
                "ok": False,
                "status": (
                    root_snapshot.disposition
                    if not root_snapshot.lifecycle_ready
                    else "producer_authoring_blocked"
                ),
                "series_branch": series_branch,
                "frozen_oid": root_snapshot.frozen_oid,
                "detail": (
                    producer_authorability_decision.diagnostic.detail
                    if producer_authorability_decision.diagnostic
                    else root_snapshot.detail
                ),
                "authorability_decision": decision_dict,
                "root_generation": _root_identity(root_snapshot),
                **_root_identity(root_snapshot),
            }
    elif not root_snapshot.lifecycle_ready:
        return {
            "ok": False,
            "status": root_snapshot.disposition,
            "series_branch": series_branch,
            "frozen_oid": root_snapshot.frozen_oid,
            "detail": root_snapshot.detail,
            "root_generation": _root_identity(root_snapshot),
            **_root_identity(root_snapshot),
        }
    cover_path = (
        patch_series_bodies.LEGACY_COVER_PATH
        if node_path == "."
        else f"{node_path}/{patch_series_bodies.LEGACY_COVER_PATH}"
    )
    if node_path == ".":
        try:
            cover_letter = root_snapshot.cover_letter()
        except Exception as exc:
            return {"ok": False, "status": "cover_letter_unparseable", "path": cover_path, "detail": str(exc)}
    else:
        # Memento: children use the same canonical/legacy resolution as the
        # root. A raw path read here is how canonical CUE children went missing.
        try:
            cover_letter = network_bodies.read_network_body(repo, source_ref, cover_path)
        except Exception as exc:
            return {"ok": False, "status": "cover_letter_unparseable", "path": cover_path, "detail": str(exc)}
        if cover_letter is None:
            return {"ok": False, "status": "cover_letter_missing", "path": cover_path, "series_branch": series_branch}
    if not _is_common_8(cover_letter):
        return {"ok": False, "status": "cover_letter_invalid", "path": cover_path}

    try:
        # The root technical approach is the executable plan for every address
        # in the series. Child approach bodies describe lineage and readiness;
        # validate that admitted child boundary, but do not replace the bounded
        # implementation plan with it.
        if node_path != ".":
            child_approach = network_bodies.read_network_body(
                repo,
                source_ref,
                f"{node_path}/{patch_series_bodies.LEGACY_ROOT_APPROACH_PATH}",
            )
            if not isinstance(child_approach, Mapping):
                return {"ok": False, "status": "approach_plan_invalid"}
        approach = root_snapshot.technical_approach()
    except Exception as exc:
        return {"ok": False, "status": "approach_plan_unparseable", "detail": str(exc)}
    if not isinstance(approach, Mapping):
        return {"ok": False, "status": "approach_plan_invalid"}

    patch_plan = _find_patch_plan(approach)
    if node_path == "." and producer_v2 and isinstance(approach.get("problem"), Mapping):
        producer_assignments = approach["problem"].get("patch_plan")
        # New roots execute the graph body and retain producer assignments on
        # their matching work items. Historical producer roots expose only the
        # assignment carrier and remain readable.
        if isinstance(patch_plan, Mapping) and isinstance(patch_plan.get("items"), list):
            patch_plan = _attach_producer_assignments(patch_plan, producer_assignments)
        elif isinstance(producer_assignments, list):
            patch_plan = producer_assignments
    if patch_plan is None:
        # Fail closed, never fall back to a whole-brief single shot: a promoted
        # series always carries a patch plan (receive builds one); its absence
        # means the brief is broken, not that bounded windows are optional.
        return {"ok": False, "status": "patch_plan_missing", "series_branch": series_branch}

    # Probe-A contract gap #1: the committed contract is the interface a
    # cross-clone author learns the result artifact from. Missing contract =
    # broken boundary, fail closed (boundary-needs-an-interface).
    try:
        committed_contract = contributor_handoff.read_result_contract(repo, source_ref)
    except Exception as exc:
        return {
            "ok": False,
            "status": "committed_contract_unparseable",
            "detail": str(exc),
        }
    if committed_contract is None:
        return {
            "ok": False,
            "status": "committed_contract_missing",
            "path": patch_author_contract.IMPLEMENTATION_RESULT_SCHEMA_PATH,
            "series_branch": series_branch,
        }
    contract_schema = patch_author_contract.implementation_result_schema()

    tree = set(git_wrapper.tree_files(repo, source_ref))
    spine_paths = sorted(path for path in tree if path.startswith("spine/"))

    # Brief 27 (amendment 1, front-loaded plumbing): the committed obligation
    # artifact is loaded HERE, not discovered at the acceptance gate — that
    # bounce is an expensive late failure. Obligation-form throughout: these are
    # questions the patch MUST answer, never "deferred" anything.
    must_answer_questions: list[dict[str, Any]] = []
    questions_text = git_wrapper.show_file(repo, source_ref, patch_series_gate.PATCH_MUST_ANSWER_QUESTIONS_PATH)
    legacy_questions_text = git_wrapper.show_file(
        repo, source_ref, patch_series_gate.LEGACY_PATCH_MUST_ANSWER_QUESTIONS_PATH
    )
    if questions_text is not None and legacy_questions_text is not None:
        return {"ok": False, "status": "must_answer_questions_ambiguous"}
    questions_text = questions_text if questions_text is not None else legacy_questions_text
    if questions_text is not None:
        try:
            questions_parsed = contributor_handoff.parse_questions(questions_text)
        except Exception as exc:
            return {"ok": False, "status": "must_answer_questions_unparseable", "detail": str(exc)}
        entries = (
            questions_parsed.get("questions_the_patch_must_answer")
            if isinstance(questions_parsed, Mapping)
            else None
        )
        if not isinstance(entries, list):
            return {"ok": False, "status": "must_answer_questions_invalid"}
        must_answer_questions = [dict(entry) for entry in entries if isinstance(entry, Mapping)]

    manifest_path = "patch-series-manifest.json" if node_path == "." else f"{node_path}/patch-series-manifest.json"
    manifest: dict[str, Any] = {}
    manifest_text = git_wrapper.show_file(repo, source_ref, manifest_path)
    if manifest_text is not None:
        try:
            parsed = json.loads(manifest_text)
            if isinstance(parsed, Mapping):
                manifest = dict(parsed)
        except json.JSONDecodeError:
            manifest = {}

    node_key = _node_key(manifest, node_path)
    facts = patch_series_gate.computed_patchwork_check_facts(repo, series_branch, node_key)
    if isinstance(facts, Mapping) and facts.get("ok") is False:
        return {
            "ok": False,
            "status": "patchwork_facts_unavailable",
            "series_branch": series_branch,
            "node_key": node_key,
            "facts_report": dict(facts),
        }
    current_snapshot = git_wrapper.head_sha(repo, series_branch)
    if series_snapshot is not None and current_snapshot != series_snapshot:
        return {
            "ok": False,
            "status": "series_snapshot_changed",
            "series_branch": series_branch,
            "expected_snapshot": series_snapshot,
            "actual_snapshot": current_snapshot,
        }

    brief = {
        "ok": True,
        "series_branch": series_branch,
        "node_path": node_path,
        "node_key": node_key,
        "cover_letter": {field: cover_letter[field] for field in RFC_FIELDS},
        "approach": approach,
        "patch_plan": patch_plan,
        "plan_id": (
            "root.problem.patch_plan"
            if producer_v2
            else str(patch_plan.get("id") or "patch_plan")
        ),
        "contract_schema": contract_schema,
        "spine_paths": spine_paths,
        "acceptance_criteria": list(manifest.get("acceptance_criteria", []))
        if isinstance(manifest.get("acceptance_criteria"), list)
        else [],
        "functional_check_description": str(manifest.get("functional_check") or ""),
        "facts": dict(facts) if isinstance(facts, Mapping) else {},
        "must_answer_questions": must_answer_questions,
    }
    if producer_v2:
        brief.update(
            {
                # This is the exact root OID already used for generation
                # dispatch, producer-pair/scope vetting, and every brief read.
                "series_snapshot_oid": series_snapshot,
                "canonical_producer_plan": True,
                "producer_gate_route": producer_gate_route,
                # Binding materialization consumes this exact common-gate
                # decision instead of independently re-vetting the route.
                "producer_authorability_decision": (
                    producer_authorability_decision.as_dict()
                    if producer_authorability_decision is not None
                    else None
                ),
                "root_generation": _root_identity(root_snapshot),
            }
        )
    return brief


def _find_patch_plan(value: Any) -> Mapping[str, Any] | None:
    """Locate the patch_plan node anywhere in the committed approach tree."""
    if isinstance(value, Mapping):
        found = value.get("patch_plan")
        if isinstance(found, Mapping):
            return found
        for child in value.values():
            nested = _find_patch_plan(child)
            if nested is not None:
                return nested
    elif isinstance(value, list):
        for child in value:
            nested = _find_patch_plan(child)
            if nested is not None:
                return nested
    return None


def _attach_producer_assignments(patch_plan: Mapping[str, Any], assignments: Any) -> dict[str, Any]:
    assignment_items = assignments if isinstance(assignments, list) else []
    by_id = {
        str(item.get("item_id")): list(item.get("production_obligation_ids", []))
        for item in assignment_items
        if isinstance(item, Mapping)
        and isinstance(item.get("item_id"), str)
        and isinstance(item.get("production_obligation_ids"), list)
    }
    result = dict(patch_plan)
    result["items"] = [
        {
            **dict(item),
            "production_obligation_ids": by_id.get(str(item.get("id") or ""), []),
        }
        for item in patch_plan.get("items", [])
        if isinstance(item, Mapping)
    ]
    return result


def _node_key(manifest: Mapping[str, Any], node_path: str) -> str:
    # Probe-A contract gap #2: node_key convention is documented in the
    # committed contract (contract.py) — 'root' for a root series, else the
    # child_key (the last path segment of sub/<child_key>). This function is
    # that convention, executable.
    child_key = manifest.get("child_key")
    if isinstance(child_key, str) and child_key:
        return child_key
    if node_path not in {"", "."}:
        return node_path.rsplit("/", 1)[-1]
    return "root"


# ---------------------------------------------------------------------------
# Plan items and the family partition.
# ---------------------------------------------------------------------------

def plan_items(patch_plan: Mapping[str, Any] | list[Any], plan_id: str) -> list[dict[str, Any]]:
    """Enumerate only the work items present in this node's plan slice.

    Current CUE slices carry stable ids directly. Positional ids below are only
    a read boundary for historical roots published before the graph cutover.
    """
    items: list[dict[str, Any]] = []
    if isinstance(patch_plan, list):
        for assignment in patch_plan:
            if not isinstance(assignment, Mapping):
                continue
            item_id = str(assignment.get("item_id") or "")
            if item_id:
                items.append(
                    {
                        "item_id": item_id,
                        "kind": "patch_plan_assignment",
                        "body": dict(assignment),
                    }
                )
        return items
    graph_items = patch_plan.get("items")
    if isinstance(graph_items, list):
        for item in graph_items:
            if not isinstance(item, Mapping):
                continue
            item_id = str(item.get("id") or "")
            if item_id:
                items.append(
                    {
                        "item_id": item_id,
                        "kind": "work_item",
                        "body": dict(item),
                    }
                )
        return items
    first = patch_plan.get("first_proof_moment")
    if isinstance(first, Mapping):
        items.append({"item_id": f"{plan_id}#first_proof_moment", "kind": "first_proof_moment", "body": dict(first)})
    follow_ups = patch_plan.get("follow_ups")
    if isinstance(follow_ups, list):
        for index, follow_up in enumerate(follow_ups):
            if isinstance(follow_up, Mapping):
                items.append(
                    {
                        "item_id": f"{plan_id}#follow-up-{index + 1:02d}",
                        "kind": "follow_up",
                        "body": dict(follow_up),
                    }
                )
    return items


def contingency_elaboration_items(questions: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """One dedicated Plan-B elaboration item per unelaborated must-answer question.

    Amendment 3 (requester: contingency wa betsudate — a lean proposal, a dedicated
    bounded call): each item is its OWN partition-schedulable unit, placed BEFORE
    the Plan-A coding items so completed contingencies land independent of and
    prior to Plan-A completion (koodingu to heiretsu — the partition structure is
    the parallelism; children own their items exclusively).
    """
    items: list[dict[str, Any]] = []
    for question in questions:
        if not isinstance(question, Mapping):
            continue
        objection_id = str(question.get("objection_id") or "")
        if not objection_id:
            continue
        if str(question.get("if_check_fails_implement") or "").strip():
            continue  # already elaborated (committed on a prior pass)
        items.append(
            {
                "item_id": f"must-answer#{objection_id}#contingency",
                "kind": "contingency_elaboration",
                "body": dict(question),
            }
        )
    return items


def partition_items(items: list[dict[str, Any]], child_count: int | None = None) -> list[list[dict[str, Any]]]:
    """Partition plan items among this family's children. Intra-family ONLY.

    Memento (the no-lock law's second half): 家族内重複=無駄 — two children of
    one parent taking the same item is waste, and this deterministic partition
    is the ONLY conflict-prevention mechanism in the org. It never sees other
    families (家族間重複=競争 is resolved at review, not here). Properties the
    tests pin: order-preserving, contiguous, disjoint, covering.

    [PROVISIONAL-W1] default = one child per item (maximum partition); callers
    may pass a smaller child_count for coarser contiguous chunks.
    """
    if not items:
        return []
    count = len(items) if child_count is None else max(1, min(child_count, len(items)))
    base, remainder = divmod(len(items), count)
    partitions: list[list[dict[str, Any]]] = []
    start = 0
    for child in range(count):
        size = base + (1 if child < remainder else 0)
        partitions.append(items[start : start + size])
        start += size
    return partitions


# ---------------------------------------------------------------------------
# Engineering precedent facets — Reference timing lanes ② (leaf READ) and
# ③ (top-up on miss) for the implementation side.
# ---------------------------------------------------------------------------

def derive_item_terms(item: Mapping[str, Any]) -> list[str]:
    """Mechanical lookup-term surface for one plan item. No model, no codex.

    [PROVISIONAL-R1] term surface = the item's own named phrases: every
    named_content name plus a follow_up's "adds" phrase. Prose condition
    fields (how_verified, win_or_progress_condition) are sentences, not term
    phrases, and stay out (search granularity is 語句単位). Dedup reuses the
    store's own term-key normalization; the store's paraphrase-tolerant read layer
    does the fuzzy matching, so no new extractor is invented here.
    """
    body = item.get("body") if isinstance(item.get("body"), Mapping) else {}
    raw: list[str] = []
    named = body.get("named_content")
    if isinstance(named, list):
        for entry in named:
            if isinstance(entry, Mapping):
                name = str(entry.get("name") or "").strip()
                if name:
                    raw.append(name)
    adds = body.get("adds")
    if isinstance(adds, str) and adds.strip():
        raw.append(adds.strip())
    return precedent_store._dedupe_terms_by_key(raw)


def precedent_facets_for_item(
    item: Mapping[str, Any],
    *,
    ctx: org_log.RunContext | None = None,
) -> dict[str, Any]:
    """Read stored engineering precedent for one item; top up on miss.

    Memento (the wire this closes, requester: 温めた知識を誰も飲んでいない):
    receive WARMS the well at intake time (timing ② build, background
    implementation lanes included), but before this function no patch-side
    consumer ever drank it — warmed implementation knowledge sat unread. Here
    the item window DRINKS it (lane READ: consumption fields only, the
    READ-separation canon) and a genuine miss TOPS UP via the store's own
    expand (timing ③); expand persists what it learns regardless of who asked
    (write-always canon lives in the store, never re-implemented here).

    Totality: facets are enrichment. Store unavailability, expand failures,
    and the exploration switch all degrade to a typed record in the returned
    mapping — they never abort the drive and never raise.
    """
    facets = _empty_precedent_facets()
    terms = derive_item_terms(item)
    if not terms:
        return facets
    # The switch's read-off/write-always semantics live in the store; this is
    # the store's own predicate, consulted (not re-parsed) so top-up research
    # is not fired for misses that are merely the switch reporting.
    if precedent_store._precedent_read_disabled():
        facets["read_disabled"] = True
        return facets
    # [PROVISIONAL-R3] reads are UNFILTERED by consuming stack: the store's
    # context matcher requires token overlap, so a prose cover-letter language
    # ("text", "repo-native files") would silently drop valid "general"
    # knowledge. Each candidate's lang_env_version is a consumption field the
    # model sees and judges applicability from inside the window.
    context: dict[str, Any] = {}
    for term in terms:
        try:
            candidates = _lookup_consumption_candidates(term, context)
        except precedent_store.StoreUnavailable as exc:
            # No store, no facets, and critically NO top-up: expand would
            # create a store at the default path as a side effect of a
            # missing fixture. Recorded, never raised.
            facets["store_unavailable"] = str(exc)
            return facets
        if not candidates:
            # Timing ③ top-up: the store's own expand researches AND persists
            # (write-always). If it fails, the miss is recorded in the drive
            # record and the item proceeds without this term's facets.
            try:
                precedent_store.expand(term, context, kinds=("implementation", "design"), ctx=ctx)
                candidates = _lookup_consumption_candidates(term, context)
            except Exception as exc:  # noqa: BLE001 — enrichment must never kill the drive; the miss is typed below.
                facets["misses"].append({"term": term, "expand_error": str(exc)})
                continue
        if not candidates:
            facets["misses"].append({"term": term})
            continue
        kept = candidates[:PRECEDENT_FACETS_PER_TERM]
        facets["terms"][term] = kept
        if len(candidates) > len(kept):
            # Never silent: the truncation is recorded in the facets mapping
            # (-> drive record), rendered as a cap note in the prompt, and
            # emitted as a ledger line when a run context exists.
            facets["capped"][term] = {"total": len(candidates), "kept": len(kept)}
            if ctx is not None:
                org_log.emit(
                    "patch_author.code_worker.precedent_facets_capped",
                    {"term": term, "total": len(candidates), "kept": len(kept)},
                    ctx=ctx,
                )
    return facets


def _lookup_consumption_candidates(term: str, context: Mapping[str, Any]) -> list[dict[str, Any]]:
    # lookup() already returns consumption fields ONLY (_consumption_candidate
    # — snippet/summary/pitfalls/lang_env_version/author_level/source_url and
    # the design equivalents; no found_via, no evidence_class, no management
    # tags). kind=None means both implementation and design facets.
    result = precedent_store.lookup(term, context)
    candidates = result.get("candidates") if isinstance(result, Mapping) else None
    return [dict(candidate) for candidate in candidates] if isinstance(candidates, list) else []


def _empty_precedent_facets() -> dict[str, Any]:
    return {"terms": {}, "capped": {}, "misses": [], "read_disabled": False, "store_unavailable": ""}


def _precedent_summary(facets: Mapping[str, Any]) -> dict[str, Any]:
    """Compact drive-record projection: which terms hit, missed, were capped."""
    return {
        "terms_hit": sorted(facets.get("terms", {})),
        "misses": list(facets.get("misses", [])),
        "capped": dict(facets.get("capped", {})),
        "read_disabled": bool(facets.get("read_disabled")),
        "store_unavailable": str(facets.get("store_unavailable") or ""),
    }


# ---------------------------------------------------------------------------
# Bounded per-item window.
# ---------------------------------------------------------------------------

def build_item_window(
    brief: Mapping[str, Any],
    item: Mapping[str, Any],
    touched_paths: list[str],
    precedent_facets: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Bounded window for ONE plan item; never the whole tree, never the whole plan.

    Contents (brief 23, window law): the item + its referenced approach nodes
    (1-hop over id-typed edges via the review scope-builder, seeded on the
    patch_plan node) + the files prior items touched + the committed contract
    facts. The patch_plan node's own body is EXCLUDED from the referenced
    nodes (it contains every other item — sending it would smuggle the whole
    plan into every window) and replaced by exactly this item.
    """
    plan_id = str(brief["plan_id"])
    if brief.get("canonical_producer_plan"):
        referenced_nodes: dict[str, Any] = {}
    else:
        review_module = importlib.import_module("ai_org.patchwork_queue.review")
        scope = review_module._scope_from_seeds(brief["approach"], {plan_id})
        referenced_nodes = {
            node_id: body for node_id, body in scope["changed_nodes"].items() if node_id != plan_id
        }
    facts = brief.get("facts") if isinstance(brief.get("facts"), Mapping) else {}
    return {
        "item_id": str(item["item_id"]),
        "item_kind": str(item["kind"]),
        "item": dict(item["body"]),
        "referenced_nodes": referenced_nodes,
        "referenced_node_ids": sorted(referenced_nodes),
        "files_touched_so_far": sorted(set(touched_paths)),
        "cover_letter": dict(brief["cover_letter"]),
        "acceptance_criteria": list(brief.get("acceptance_criteria", [])),
        "functional_check_description": str(brief.get("functional_check_description") or ""),
        "spine_paths": list(brief.get("spine_paths", [])),
        "facts": dict(facts),
        "precedent_facets": dict(precedent_facets) if precedent_facets is not None else _empty_precedent_facets(),
        # [PROVISIONAL-Q2] obligations attach to EVERY item window: matching a
        # check's subject to an item mechanically would require prose inference
        # (forbidden); the questions are few and compact, so the bounded-window
        # law holds with the attach-to-all fallback the amendment allows.
        "must_answer_questions": [
            {
                "objection_id": str(question.get("objection_id") or ""),
                "executable_check": str(question.get("executable_check") or ""),
                "claim": str(question.get("claim") or ""),
            }
            for question in brief.get("must_answer_questions", [])
            if isinstance(question, Mapping)
        ],
    }


def render_item_prompt(
    window: Mapping[str, Any],
    repair: Mapping[str, Any] | None = None,
) -> str:
    """Formal-frame-first item prompt."""
    item_id = str(window["item_id"])
    cue_dir = _cue_dir(item_id)
    lines = [
        "Implement ONE patch-plan item of the patch series in this repository.",
        "Edit the working tree only. Do not commit.",
        f"Implement ONLY this item ({item_id}); other items are delivered in separate invocations.",
        "Do NOT write implementation-result.json or implementation-result.cue — the org toolchain writes the canonical result deterministically after all items land.",
        "",
        "Required CUE formal frame workflow:",
        f"- Before writing implementation code, define the item frame in {cue_dir}/frame.cue.",
        "- The frame must define the state space, invariants, transitions, and pre/post contracts using #StateSpace, #Invariant, #Transition, #PreState, and #PostState.",
        f"- Provide a real positive state at {cue_dir}/real-states/positive.json and a real negative state at {cue_dir}/real-states/negative.json.",
        "- Run cue vet so it passes on the positive state and fails on the negative state.",
        "- Implement code satisfying the frame.",
        "- Write ordinary tests covering the transitions.",
        "",
        "Patch series cover letter:",
        _format_rfc(dict(window["cover_letter"])),
        f"Patch-plan item {item_id} ({window['item_kind']}):",
        json.dumps(window["item"], indent=2, sort_keys=True, ensure_ascii=True),
    ]
    if window["referenced_nodes"]:
        lines.extend(
            [
                "",
                "Referenced approach nodes (1-hop over id-typed edges; the rest of the tree is deliberately absent):",
                json.dumps(window["referenced_nodes"], indent=2, sort_keys=True, ensure_ascii=True),
            ]
        )
    if window["files_touched_so_far"]:
        lines.extend(
            [
                "",
                "Files earlier items already touched on this branch (readable in this worktree):",
                *[f"- {path}" for path in window["files_touched_so_far"]],
            ]
        )
    if window["spine_paths"]:
        lines.extend(["", "Committed spine contracts present in this worktree (binding):", *[f"- {path}" for path in window["spine_paths"]]])
    facets = window.get("precedent_facets") if isinstance(window.get("precedent_facets"), Mapping) else {}
    facet_terms = facets.get("terms") if isinstance(facets.get("terms"), Mapping) else {}
    if facet_terms:
        lines.extend(
            [
                "",
                "ENGINEERING PRECEDENT FACETS",
                "Stored engineering precedent matching this item's terms. Verify against THIS repository before relying on any of it.",
                json.dumps(facet_terms, indent=2, sort_keys=True, ensure_ascii=True),
            ]
        )
        capped = facets.get("capped") if isinstance(facets.get("capped"), Mapping) else {}
        for term in sorted(capped):
            cap = capped[term]
            lines.append(
                f"(term '{term}': showing {cap['kept']} of {cap['total']} stored candidates — capped by the window law, recorded in the drive record)"
            )
    must_answer = window.get("must_answer_questions") or []
    if must_answer:
        lines.extend(
            [
                "",
                "QUESTIONS THE PATCH MUST ANSWER",
                "Review-accepted questions this patch MUST answer. Build so each executable check passes; the harness runs every check after all items land.",
                json.dumps(list(must_answer), indent=2, sort_keys=True, ensure_ascii=True),
            ]
        )
    lines.extend(["", "Acceptance criteria:"])
    criteria = window.get("acceptance_criteria")
    if criteria:
        lines.extend(f"- {item}" for item in criteria)
    else:
        lines.append("- none declared")
    facts = window.get("facts")
    if isinstance(facts, Mapping) and facts:
        lines.extend(
            [
                "",
                "COMPUTED CHECK FACTS",
                "These values are computed facts from the deterministic engine. Do not recompute, reinterpret, or assume other values.",
                json.dumps(facts, indent=2, sort_keys=True, ensure_ascii=True),
            ]
        )
    if repair is not None:
        lines.extend(
            [
                "",
                "REPAIR ROUND — the previous CUE validation attempt failed with a typed error.",
                json.dumps(dict(repair), indent=2, sort_keys=True, ensure_ascii=True),
                "Fix exactly what the typed error names while preserving the CUE formal frame obligations.",
            ]
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# The drive: parent-level orchestration over children.
# ---------------------------------------------------------------------------

def drive(
    repo,
    record: Mapping[str, Any],
    *,
    attempt: int = 1,
    ctx: org_log.RunContext | None = None,
) -> dict[str, Any]:
    """Author the announced series: per-item commits + result artifact. Typed, total."""
    repo_path = Path(repo).resolve()
    series_branch = str(record["series_branch"])
    node_path = str(record["node_path"])
    branch = str(record["contrib_branch"])
    identity = dict(record["author"])
    series_id = series_branch.removeprefix("ai-org/patch-series/")
    stage = "patch_author.code_worker"
    log_ctx = ctx or org_log.RunContext(repo=repo_path, patch_series_id=series_id, stage=stage, attempt=attempt)
    if ctx is not None:
        log_ctx = ctx.child(patch_series_id=series_id, stage=stage, attempt=attempt)

    brief = load_brief(repo_path, series_branch, node_path)
    if not brief.get("ok"):
        org_log.emit("patch_author.code_worker.brief_failed", brief, ctx=log_ctx, severity="error")
        return brief
    items = plan_items(brief["patch_plan"], brief["plan_id"])
    if not items:
        return {"ok": False, "status": "patch_plan_empty", "series_branch": series_branch, "plan_id": brief["plan_id"]}
    # Amendment 3: Plan-B elaboration runs as first-class partition items ahead
    # of (and independent from) Plan-A coding.
    items = contingency_elaboration_items(brief.get("must_answer_questions", [])) + items

    branch_preexisting = _branch_exists(repo_path, branch)
    if branch_preexisting and not producer_lifecycle.is_promise_only_contribution(
        repo_path, branch
    ):
        return {"ok": False, "status": "contrib_branch_exists", "branch": branch}

    partitions = partition_items(items)
    base = _refreshed_default_base(repo_path)
    if not base.get("ok"):
        return {**base, "branch": branch}
    temp_dir = Path(tempfile.mkdtemp(prefix="ai-org-patch-author-"))
    worktree = temp_dir / "worktree"
    outcome: dict[str, Any] = {"ok": False, "status": "worktree_failed", "branch": branch}
    try:
        if branch_preexisting:
            created = _git_run(repo_path, "worktree", "add", str(worktree), branch)
        else:
            created = _git_run(repo_path, "worktree", "add", "-b", branch, str(worktree), str(base["commit"]))
        if created.returncode != 0:
            outcome = {"ok": False, "status": "worktree_failed", "branch": branch, "detail": created.stderr.strip()}
            return outcome
        outcome = _drive_in_worktree(
            repo_path,
            worktree,
            brief,
            partitions,
            identity=identity,
            branch=branch,
            ctx=log_ctx,
        )
        return outcome
    finally:
        # Cleanup is SUCCESS-ONLY: a failed item is exactly when the operator
        # needs the in-progress tree for forensics, so failure (typed result or
        # exception) preserves the temp dir on disk and records where it is.
        if outcome.get("ok"):
            if worktree.exists():
                _git_run(repo_path, "worktree", "remove", "--force", str(worktree))
            shutil.rmtree(temp_dir, ignore_errors=True)
        else:
            outcome["preserved_worktree"] = _preserve_failed_worktree(
                temp_dir, worktree, branch=branch, ctx=log_ctx
            )
            # A failed drive leaves NO local branch: the branch was created by
            # this drive and never published, so deleting it is hygiene, not
            # history rewriting. The typed failure already carries the item id.
            # (The preserved worktree was detached above, so the delete works.)
            if not branch_preexisting:
                _git_run(repo_path, "branch", "-D", branch)


def _drive_in_worktree(
    repo_path: Path,
    worktree: Path,
    brief: Mapping[str, Any],
    partitions: list[list[dict[str, Any]]],
    *,
    identity: Mapping[str, str],
    branch: str,
    ctx: org_log.RunContext,
) -> dict[str, Any]:
    item_commits: list[dict[str, str]] = []
    touched_paths: list[str] = []
    binding = _materialize_producer_task_binding(
        repo_path,
        worktree,
        brief,
        branch=branch,
        identity=identity,
    )
    if not binding.get("ok"):
        return binding
    for partition in partitions:
        # Children run sequentially in-process ([PROVISIONAL-W1]); each child
        # owns its partition exclusively — the intra-family no-duplication
        # invariant lives in partition_items, not here.
        child = author_items(
            repo_path,
            worktree,
            brief,
            partition,
            identity=identity,
            touched_paths=touched_paths,
            producer_binding=binding if binding.get("active") else None,
            ctx=ctx,
        )
        if not child.get("ok"):
            return child
        item_commits.extend(child["item_commits"])
        touched_paths = child["touched_paths"]

    # Brief 27 submission-side early gate: every must-answer question needs an
    # outcome (and an engaged contingency when its check failed) BEFORE the
    # result artifact and submission — the acceptance judge is the backstop,
    # never the discovery point.
    must_answer = _answer_must_answer_questions(
        repo_path, worktree, brief, identity=identity, touched_paths=touched_paths, ctx=ctx
    )
    if not must_answer.get("ok"):
        org_log.emit("patch_author.code_worker.must_answer_failed", must_answer, ctx=ctx, severity="error")
        return must_answer

    assertion: dict[str, Any] = {"ok": True, "active": False}
    if binding.get("active"):
        assertion = _write_producer_completion_assertion(
            repo_path, worktree, identity=identity
        )
        if not assertion.get("ok"):
            return assertion

    result = _write_result_artifact(
        repo_path,
        worktree,
        brief,
        identity=identity,
        must_answer_outcomes=must_answer["outcomes"],
    )
    if not result.get("ok"):
        return result

    head = _git_run(worktree, "rev-parse", "HEAD").stdout.strip()
    org_log.emit(
        "patch_author.code_worker.completed",
        {"branch": branch, "commit": head, "items": [entry["item_id"] for entry in item_commits]},
        ctx=ctx,
    )
    completed: dict[str, Any] = {
        "ok": True,
        "branch": branch,
        "commit": head,
        "item_commits": item_commits,
        "result_commit": result["commit"],
        "patch_series_branch": brief["series_branch"],
        "node_key": brief["node_key"],
        "must_answer_outcomes": must_answer["outcomes"],
        "contingency_engaged": must_answer["contingency_engaged"],
    }
    if binding.get("active"):
        completed.update(
            {
                "task_binding_commit": binding["commit"],
                "task_binding_body_sha256": binding["body_sha256"],
                "completion_assertion_commit": assertion["commit"],
                "completion_assertion_body_sha256": assertion["body_sha256"],
            }
        )
    return completed


def author_items(
    repo_path: Path,
    worktree: Path,
    brief: Mapping[str, Any],
    partition: list[dict[str, Any]],
    *,
    identity: Mapping[str, str],
    touched_paths: list[str],
    producer_binding: Mapping[str, Any] | None = None,
    ctx: org_log.RunContext,
) -> dict[str, Any]:
    """One child: author every item in its partition, one commit per item."""
    item_commits: list[dict[str, Any]] = []
    paths = list(touched_paths)
    for item in partition:
        item_stage = "patch_author.code_worker.item"
        with org_log.span(item_stage, ctx.child(stage=item_stage, step=str(item["item_id"]))) as item_ctx:
            outcome = _author_one_item(
                repo_path,
                worktree,
                brief,
                item,
                identity=identity,
                touched_paths=paths,
                producer_binding=producer_binding,
                ctx=item_ctx,
            )
        if not outcome.get("ok"):
            return outcome
        item_commits.append(
            {
                "item_id": str(item["item_id"]),
                "commit": outcome["commit"],
                # Drive record: precedent consumption per item — hits, misses
                # (including deferred/failed top-ups), and cap truncations.
                "precedent": outcome.get("precedent", _precedent_summary(_empty_precedent_facets())),
            }
        )
        paths.extend(outcome["changed_paths"])
    return {"ok": True, "item_commits": item_commits, "touched_paths": paths}


def _author_one_item(
    repo_path: Path,
    worktree: Path,
    brief: Mapping[str, Any],
    item: Mapping[str, Any],
    *,
    identity: Mapping[str, str],
    touched_paths: list[str],
    producer_binding: Mapping[str, Any] | None = None,
    ctx: org_log.RunContext,
) -> dict[str, Any]:
    item_id = str(item["item_id"])
    if str(item.get("kind")) == "contingency_elaboration":
        return _elaborate_contingency(repo_path, worktree, brief, item["body"], identity=identity, ctx=ctx)
    # Timing ② leaf READ + ③ top-up: warmed knowledge enters the bounded
    # window here (once per item; repair rounds reuse the same facets).
    worker_stage = "patch_author.code_worker"
    facets = precedent_facets_for_item(item, ctx=ctx.child(stage=f"{worker_stage}.precedent"))
    if facets.get("misses") or facets.get("store_unavailable"):
        org_log.emit(
            "patch_author.code_worker.precedent_misses",
            {"item_id": item_id, **_precedent_summary(facets)},
            ctx=ctx,
        )
    window = build_item_window(brief, item, touched_paths, precedent_facets=facets)
    retry_state = codex_exec.RejectionRetryState()
    repair: Mapping[str, Any] | None = None

    for round_index in range(1 + MAX_REPAIR_ROUNDS):
        prompt = render_item_prompt(window, repair=repair)
        run = _run_codex(worktree, prompt, ctx=ctx.child(stage=f"{worker_stage}.codex", attempt=round_index + 1))
        failure = run.get("failure")
        if failure is None:
            failure = _reserved_result_write_failure(worktree)
        if failure is None:
            with org_log.span(
                "patch_author.code_worker.cue_vet",
                ctx.child(stage="patch_author.code_worker.cue_vet", attempt=round_index + 1),
            ):
                validation = _run_cue_frame_validation(worktree, item_id)
            if not validation.get("ok"):
                failure = validation["failure"]
            if failure is None:
                with org_log.span(
                    "patch_author.code_worker.commit",
                    ctx.child(stage="patch_author.code_worker.commit", attempt=round_index + 1),
                ):
                    commit = _commit_item(
                        repo_path,
                        worktree,
                        brief,
                        item,
                        window,
                        identity=identity,
                        repair_rounds=round_index,
                        producer_binding=producer_binding,
                    )
                if not commit.get("ok"):
                    return {**commit, "item_id": item_id}
                return {**commit, "precedent": _precedent_summary(facets)}

        # Fingerprint law: a retry must change the odds or stop. A repeated
        # typed-error fingerprint across rounds is permanent — fail closed
        # with the item id; do not keep pulling the same lever. Failed edits
        # stay in the worktree for the repair round (repair fixes in place —
        # slot-refill, low effort); on terminal failure the whole worktree is
        # discarded by drive(), so nothing broken ever reaches the branch.
        rejection = codex_exec.validation_rejection(
            worker_stage,
            f"{failure['type']}: {failure.get('detail', '')}".strip(),
            error_class=failure["type"],
            path=item_id,
        )
        decision = retry_state.classify(rejection, attempt=round_index + 1, ctx=ctx)
        if decision.permanent or round_index == MAX_REPAIR_ROUNDS:
            return {
                "ok": False,
                "status": "item_failed",
                "item_id": item_id,
                "failure": failure,
                "repair_rounds": round_index,
                "permanent": decision.permanent,
            }
        repair = {"typed_error": failure, "failed_round": round_index + 1}
    # Unreachable: every path above returns; kept for totality.
    return {"ok": False, "status": "item_failed", "item_id": item_id, "failure": {"type": "exhausted"}}


def _reserved_result_write_failure(worktree: Path) -> dict[str, Any] | None:
    """Reject contributor edits to either durable result coordinate.

    The result commit is a separate harness transition after every item has
    landed. Detecting both tracked changes and untracked files before item
    validation prevents contributor-authored result bodies from entering Git
    history, while leaving the failed worktree available for repair/forensics.
    """

    reserved = (contributor_handoff.RESULT_PATH, contributor_handoff.LEGACY_RESULT_PATH)
    tracked = _git_run(worktree, "diff", "--name-only", "HEAD", "--", *reserved)
    untracked = _git_run(
        worktree, "ls-files", "--others", "--exclude-standard", "--", *reserved
    )
    if tracked.returncode != 0 or untracked.returncode != 0:
        return {
            "type": "implementation_result_reservation_check_failed",
            "detail": (tracked.stderr or untracked.stderr).strip(),
        }
    changed = sorted(
        {
            path
            for output in (tracked.stdout, untracked.stdout)
            for path in output.splitlines()
            if path
        }
    )
    if not changed:
        return None
    return {
        "type": "implementation_result_written_by_contributor",
        "detail": "reserved for the patch-author harness: " + ", ".join(changed),
        "paths": changed,
    }


def build_contingency_plan_schema() -> dict[str, Any]:
    # Amendment 4 (requester: makimodori shindo wa Node no ichi de kimaru — rewind
    # depth is decided by the network position of what the failure contaminates).
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": ["contingency_plan", "rewind_class", "direction_change_node_ids"],
        "properties": {
            "contingency_plan": {
                "type": "string",
                "description": (
                    "What the patch implements if the executable check FAILS — a concrete alternative "
                    "approach for exactly this question's part of the work."
                ),
            },
            "rewind_class": {
                "type": "string",
                "enum": ["in_patch", "requires_direction_change"],
                "description": (
                    "in_patch: the contingency touches only files/patch items. "
                    "requires_direction_change: a check failure would invalidate committed direction nodes."
                ),
            },
            "direction_change_node_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "The committed direction node ids a check failure would invalidate; empty unless "
                    "rewind_class is requires_direction_change. Use exact ids from the anchored nodes' tree."
                ),
            },
        },
    }


_CODEX_OUTPUT_SCHEMA_BUILDERS = {"CONTINGENCY_PLAN_SCHEMA": build_contingency_plan_schema}


def _anchored_node_bodies(brief: Mapping[str, Any], anchor_node_ids: list[str]) -> dict[str, Any]:
    receive_module = importlib.import_module("ai_org.patchwork_queue.receive")
    subtrees: dict[str, Any] = {}
    for node in receive_module._tree_node_subtrees(brief.get("approach", {})):
        node_id = str(node.get("id") or "")
        if node_id in anchor_node_ids and node_id not in subtrees:
            subtrees[node_id] = node
    return subtrees


def _elaborate_contingency(
    repo_path: Path,
    worktree: Path,
    brief: Mapping[str, Any],
    question: Mapping[str, Any],
    *,
    identity: Mapping[str, str],
    ctx: org_log.RunContext,
) -> dict[str, Any]:
    """Dedicated bounded Plan-B call: ONE question in, one contingency out.

    Amendment 3: the window is ONLY the flagged question (obligation form), the
    anchored nodes' bodies, and the check text — never other questions, never
    the tree. NORMAL effort: fresh judgment, not a slot refill. The result is
    committed into the contribution branch's copy of the obligation artifact
    (obligation framing: if_check_fails_implement), so the plan survives the
    drive and the acceptance judge can read it.
    """
    objection_id = str(question.get("objection_id") or "")
    anchors = [anchor for anchor in question.get("anchor_node_ids", []) if isinstance(anchor, str)]
    payload = {
        "question_the_patch_must_answer": {
            "objection_id": objection_id,
            "executable_check": str(question.get("executable_check") or ""),
            "why_resolved_in_patch": str(question.get("why_resolved_in_patch") or ""),
            "claim": str(question.get("claim") or ""),
        },
        "anchored_nodes": _anchored_node_bodies(brief, anchors),
    }
    prompt = (
        "One review-accepted question the patch must answer is listed below with its anchored approach "
        "nodes. Write the contingency plan: what this patch implements if the executable check FAILS — a "
        "concrete alternative approach for exactly this part of the work, written now while the trade-off "
        "is fresh. Do not modify files.\n\n"
        + json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True)
        + "\n\nReturn only JSON matching the provided schema."
    )
    known_node_ids = _approach_node_ids(brief)
    feedback = ""
    detail = ""
    for attempt in range(2):
        run = codex_exec.run_json(
            worktree,
            schema=build_contingency_plan_schema(),
            prompt=prompt + feedback,
            schema_filename=f"contingency-{_safe_id(objection_id)}.schema.json",
            output_filename=f"contingency-{_safe_id(objection_id)}.json",
            failure_label="Codex contingency elaboration",
            ctx=ctx.child(stage="patch_author.code_worker.contingency", attempt=attempt + 1),
        )
        if not run["ok"]:
            # Memento: a run-level failure here is a transient subprocess/output
            # error (empty completion / "no output file"), classified transient
            # in codex_exec.run_json. Do NOT sink the whole IMPLEMENT on one
            # stochastic empty reply: treat it like a validation rejection and
            # retry within the same bounded attempt budget. Only the final
            # attempt's failure returns (the range(2) loop bounds the retry).
            detail = run["error"]
            continue
        try:
            parsed = json.loads(run["raw"])
        except json.JSONDecodeError as exc:
            return {"ok": False, "status": "contingency_elaboration_failed", "objection_id": objection_id, "detail": str(exc)}
        plan = parsed.get("contingency_plan") if isinstance(parsed, Mapping) else None
        rewind_class = parsed.get("rewind_class") if isinstance(parsed, Mapping) else None
        node_ids = parsed.get("direction_change_node_ids") if isinstance(parsed, Mapping) else None
        if not isinstance(plan, str) or not plan.strip() or rewind_class not in ("in_patch", "requires_direction_change"):
            detail = "empty contingency_plan or invalid rewind_class"
        else:
            node_ids = [node_id for node_id in (node_ids or []) if isinstance(node_id, str) and node_id]
            if rewind_class == "requires_direction_change":
                # Amendment 4 item 3: node-position validation is mechanical — the
                # declared ids must exist in the committed approach tree.
                unknown = sorted(set(node_ids) - known_node_ids)
                if not node_ids or unknown:
                    detail = f"requires_direction_change with empty or unknown node ids: {unknown or 'none declared'}"
                else:
                    detail = ""
            else:
                node_ids = []
                detail = ""
        if not detail:
            commit = _commit_contingency_plan(
                worktree, brief, objection_id, plan, identity=identity,
                rewind_class=rewind_class, direction_change_node_ids=node_ids,
            )
            if not commit.get("ok"):
                return {**commit, "objection_id": objection_id}
            return {
                "ok": True,
                "commit": commit["commit"],
                "changed_paths": [patch_series_gate.PATCH_MUST_ANSWER_QUESTIONS_PATH],
                "precedent": _precedent_summary(_empty_precedent_facets()),
                "contingency_plan": plan,
                "rewind_class": rewind_class,
                "direction_change_node_ids": node_ids,
            }
        feedback = (
            "\n\nPrevious output failed deterministic validation. Fix exactly this and return the full JSON again:\n- "
            + detail
        )
    return {"ok": False, "status": "contingency_elaboration_failed", "objection_id": objection_id, "detail": detail}


def _approach_node_ids(brief: Mapping[str, Any]) -> set[str]:
    receive_module = importlib.import_module("ai_org.patchwork_queue.receive")
    return {
        str(node.get("id") or "")
        for node in receive_module._tree_node_subtrees(brief.get("approach", {}))
        if node.get("id")
    }


def _safe_id(value: str) -> str:
    return "".join(character if character.isalnum() else "-" for character in value) or "question"


def _commit_contingency_plan(
    worktree: Path,
    brief: Mapping[str, Any],
    objection_id: str,
    plan: str,
    *,
    identity: Mapping[str, str],
    rewind_class: str = "in_patch",
    direction_change_node_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Write the elaborated Plan B into the contribution branch's obligation artifact."""
    artifact_path = worktree / patch_series_gate.PATCH_MUST_ANSWER_QUESTIONS_PATH
    entries: list[dict[str, Any]] = []
    if artifact_path.exists():
        try:
            existing = contributor_handoff.parse_questions(
                artifact_path.read_text(encoding="utf-8"), contribution=True
            )
        except Exception:
            existing = None
        if isinstance(existing, Mapping) and isinstance(existing.get("questions_the_patch_must_answer"), list):
            entries = [dict(entry) for entry in existing["questions_the_patch_must_answer"] if isinstance(entry, Mapping)]
    if not entries:
        entries = [dict(question) for question in brief.get("must_answer_questions", []) if isinstance(question, Mapping)]
    for entry in entries:
        if str(entry.get("objection_id") or "") == objection_id:
            entry["if_check_fails_implement"] = plan
            entry["rewind_class"] = rewind_class
            entry["direction_change_node_ids"] = list(direction_change_node_ids or [])
    prepared = contributor_handoff.prepare_questions(
        {"questions_the_patch_must_answer": entries}, contribution=True
    )
    artifact_path.write_bytes(prepared.canonical.data)
    add = _git_run(worktree, "add", patch_series_gate.PATCH_MUST_ANSWER_QUESTIONS_PATH)
    if add.returncode != 0:
        return {"ok": False, "status": "git_add_failed", "detail": add.stderr.strip()}
    commit = _git_run(
        worktree,
        *git_wrapper.identity_config_args(identity),
        "commit",
        "-m",
        f"patch: contingency plan for {objection_id}",
        "-m",
        "Dedicated bounded elaboration (one question in, one plan out); obligation artifact updated on this branch.",
    )
    if commit.returncode != 0:
        return {"ok": False, "status": "git_commit_failed", "detail": commit.stderr.strip()}
    return {"ok": True, "commit": _git_run(worktree, "rev-parse", "HEAD").stdout.strip()}


def _contrib_contingency_plans(worktree: Path) -> dict[str, dict[str, Any]]:
    artifact_path = worktree / patch_series_gate.PATCH_MUST_ANSWER_QUESTIONS_PATH
    if not artifact_path.exists():
        return {}
    try:
        parsed = contributor_handoff.parse_questions(
            artifact_path.read_text(encoding="utf-8"), contribution=True
        )
    except Exception:
        return {}
    entries = parsed.get("questions_the_patch_must_answer") if isinstance(parsed, Mapping) else None
    plans: dict[str, dict[str, Any]] = {}
    for entry in entries or []:
        if isinstance(entry, Mapping):
            plan = str(entry.get("if_check_fails_implement") or "")
            if plan.strip():
                plans[str(entry.get("objection_id") or "")] = {
                    "contingency_plan": plan,
                    "rewind_class": str(entry.get("rewind_class") or "in_patch"),
                    "direction_change_node_ids": [
                        node_id for node_id in entry.get("direction_change_node_ids", []) if isinstance(node_id, str)
                    ],
                }
    return plans


def _run_must_answer_check(worktree: Path, question: Mapping[str, Any]) -> dict[str, Any] | None:
    """Run one must-answer executable check. [PROVISIONAL-Q1] shell-string convention:
    the check text runs as /bin/sh -c in the worktree; exit 0 answers the question.
    A non-runnable (prose-shaped) check fails here and surfaces typed — fail-closed
    honesty over fabricated outcomes (joukenbun wa keiyaku dewa nai).
    """
    check = str(question.get("executable_check") or "")
    if not check.strip():
        return {"type": "must_answer_check_invalid", "detail": "empty executable_check"}
    try:
        completed = subprocess.run(
            ["/bin/sh", "-c", check],
            cwd=worktree,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            stdin=subprocess.DEVNULL,
        )
    except OSError as exc:
        return {"type": "must_answer_check_failed", "detail": f"sh: {exc}"}
    if completed.returncode == 0:
        return None
    tail = (completed.stdout or "")[-2000:]
    return {"type": "must_answer_check_failed", "detail": f"exit={completed.returncode} check={check} output_tail={tail}"}


def _answer_must_answer_questions(
    repo_path: Path,
    worktree: Path,
    brief: Mapping[str, Any],
    *,
    identity: Mapping[str, str],
    touched_paths: list[str],
    ctx: org_log.RunContext,
) -> dict[str, Any]:
    """The submission-side early gate: every question gets an outcome or the drive
    fails typed BEFORE submission (the acceptance judge is the backstop, never the
    discovery point). Check fails -> the elaborated contingency becomes the work
    content of ONE bounded pivot (lazy-elaborated here if its call never ran);
    a failing contingency is a genuine typed fail-closed — no plan C/D ladder.
    Memento circle (brief 27): paper decides -> question routed to code -> the
    check answers it -> on "no", Plan B engages (and, when the failure reaches
    committed direction nodes, returns as reviewable evidence) -> back to code.
    """
    questions = [question for question in brief.get("must_answer_questions", []) if isinstance(question, Mapping)]
    if not questions:
        return {"ok": True, "outcomes": {}, "contingency_engaged": []}
    plans = _contrib_contingency_plans(worktree)
    outcomes: dict[str, Any] = {}
    engaged: list[str] = []
    for question in questions:
        objection_id = str(question.get("objection_id") or "")
        check_failure = _run_must_answer_check(worktree, question)
        if check_failure is None:
            outcomes[objection_id] = {
                "outcome": "check_passed",
                "executable_check": str(question.get("executable_check") or ""),
            }
            continue
        contingency = plans.get(objection_id)
        if contingency is None or not str(contingency.get("contingency_plan") or "").strip():
            # Amendment 3 item 4, lazy fallback: elaborate at pivot time, once.
            elaborated = _elaborate_contingency(repo_path, worktree, brief, question, identity=identity, ctx=ctx)
            if not elaborated.get("ok"):
                return {
                    "ok": False,
                    "status": "must_answer_question_failed",
                    "objection_id": objection_id,
                    "failure": check_failure,
                    "detail": elaborated.get("detail", "contingency elaboration failed"),
                    "contingency_engaged": engaged,
                }
            contingency = {
                "contingency_plan": elaborated["contingency_plan"],
                "rewind_class": elaborated.get("rewind_class", "in_patch"),
                "direction_change_node_ids": elaborated.get("direction_change_node_ids", []),
            }
        plan = contingency["contingency_plan"]
        if contingency.get("rewind_class") == "requires_direction_change":
            # Amendment 4: the failure contaminates committed direction nodes.
            # The worker must NOT improvise design (sekinin bunkaiten — the
            # jurisdiction boundary): commit an implementation-experience report
            # to the series branch (evidence, with Plan B riding verbatim; the
            # committed tree is NOT rewritten) and stop typed. The report
            # re-enters the EXISTING reform loop as objection-grade evidence
            # (amendment 5: pure routing — brief25 targeted revision seeded on
            # the named nodes, brief19-D delta review of exactly Plan B's
            # consequences; canon: TC39 stage regression, Rust stabilization ->
            # new RFC, K8s alpha redesign).
            report = _commit_implementation_experience_report(
                repo_path, brief, question, contingency, check_failure, identity=identity
            )
            if not report.get("ok"):
                return {**report, "objection_id": objection_id}
            return {
                "ok": False,
                "status": "direction_change_required",
                "objection_id": objection_id,
                "invalidated_node_ids": list(contingency.get("direction_change_node_ids", [])),
                "failure": check_failure,
                "report_path": report["path"],
                "contingency_engaged": engaged,
            }
        pivot = _author_contingency_pivot(
            worktree, question, plan, check_failure, touched_paths=touched_paths, identity=identity, ctx=ctx
        )
        if not pivot.get("ok"):
            return {
                "ok": False,
                "status": "must_answer_question_failed",
                "objection_id": objection_id,
                "failure": pivot.get("failure", check_failure),
                "contingency_engaged": engaged + [objection_id],
            }
        recheck = _run_must_answer_check(worktree, question)
        if recheck is not None:
            # ONE pivot per question: a failing contingency surfaces for review.
            return {
                "ok": False,
                "status": "must_answer_question_failed",
                "objection_id": objection_id,
                "failure": recheck,
                "contingency_engaged": engaged + [objection_id],
            }
        engaged.append(objection_id)
        outcomes[objection_id] = {
            "outcome": "check_passed_via_contingency",
            "executable_check": str(question.get("executable_check") or ""),
            "contingency_engaged": True,
            "initial_failure": str(check_failure.get("detail") or "")[:500],
        }
    return {"ok": True, "outcomes": outcomes, "contingency_engaged": engaged}


def _commit_implementation_experience_report(
    repo_path: Path,
    brief: Mapping[str, Any],
    question: Mapping[str, Any],
    contingency: Mapping[str, Any],
    check_failure: Mapping[str, Any],
    *,
    identity: Mapping[str, str],
) -> dict[str, Any]:
    """Commit the implementation-experience report to the SERIES branch (evidence).

    Jurisdiction invariant: this writes ONE evidence artifact on the series
    branch and nothing else — no direction node, no cover letter, no
    technical-approach-plan edit. Plan B rides verbatim so review reviews only
    Plan B's consequences (amendment 5).
    """
    series_branch = str(brief["series_branch"])
    path = patch_series_gate.IMPLEMENTATION_EXPERIENCE_REPORT_PATH
    raw = git_wrapper.show_file(repo_path, series_branch, path)
    legacy_raw = git_wrapper.show_file(
        repo_path, series_branch, patch_series_gate.LEGACY_IMPLEMENTATION_EXPERIENCE_REPORT_PATH
    )
    if raw is not None and legacy_raw is not None:
        return {"ok": False, "status": "experience_report_ambiguous"}
    raw = raw if raw is not None else legacy_raw
    reports: list[dict[str, Any]] = []
    if raw is not None:
        try:
            parsed = contributor_handoff.parse_experience(raw)
        except Exception:
            parsed = None
        if isinstance(parsed, Mapping) and isinstance(parsed.get("implementation_experience_reports"), list):
            reports = [dict(entry) for entry in parsed["implementation_experience_reports"] if isinstance(entry, Mapping)]
    objection_id = str(question.get("objection_id") or "")
    if not any(str(entry.get("objection_id") or "") == objection_id and not entry.get("processed_in_author_version") for entry in reports):
        reports.append(
            {
                "objection_id": objection_id,
                "claim": str(question.get("claim") or ""),
                "executable_check": str(question.get("executable_check") or ""),
                "check_failure_evidence": str(check_failure.get("detail") or "")[:2000],
                "invalidated_node_ids": [
                    node_id for node_id in contingency.get("direction_change_node_ids", []) if isinstance(node_id, str)
                ],
                "contingency_plan": str(contingency.get("contingency_plan") or ""),
                "rewind_class": "requires_direction_change",
                "reported_from_node_key": str(brief.get("node_key") or ""),
            }
        )
    try:
        prepared = contributor_handoff.prepare_experience(
            {"implementation_experience_reports": reports}
        )
        written = git_wrapper.commit_files(
            repo_path,
            series_branch,
            {path: prepared.canonical.data.decode("utf-8")},
            subject=f"patch_series: implementation experience for {objection_id}",
            body=(
                "A must-answer check failed and its contingency declares requires_direction_change: "
                "the worker does not improvise design (jurisdiction boundary). This report is the "
                "evidence the reform loop consumes; Plan B rides verbatim."
            ),
        )
    except Exception as exc:  # noqa: BLE001 - typed, total
        return {"ok": False, "status": "experience_report_commit_failed", "detail": str(exc)}
    return {"ok": True, "path": path, "commit": written.get("commit", "")}


def _author_contingency_pivot(
    worktree: Path,
    question: Mapping[str, Any],
    plan: str,
    check_failure: Mapping[str, Any],
    *,
    touched_paths: list[str],
    identity: Mapping[str, str],
    ctx: org_log.RunContext,
) -> dict[str, Any]:
    """One bounded pivot: the contingency plan becomes the work content."""
    objection_id = str(question.get("objection_id") or "")
    lines = [
        "A question this patch must answer FAILED its executable check. Implement the contingency plan",
        "below — it is the agreed fallback for exactly this part of the work. Edit the working tree only;",
        "do not commit; touch only what the contingency requires.",
        "",
        f"Question ({objection_id}): {question.get('claim') or ''}",
        f"Executable check: {question.get('executable_check') or ''}",
        f"Check failure: {check_failure.get('detail') or ''}",
        "",
        "Contingency plan to implement:",
        plan,
    ]
    if touched_paths:
        lines.extend(["", "Files this patch already touched:", *[f"- {path}" for path in sorted(set(touched_paths))]])
    run = _run_codex(worktree, "\n".join(lines), ctx=ctx.child(stage="patch_author.code_worker.contingency_pivot"))
    if run.get("failure") is not None:
        return {"ok": False, "failure": run["failure"]}
    add = _git_run(worktree, "add", "-A", "--", ".", ":(exclude)__pycache__", ":(exclude)**/__pycache__", ":(exclude)*.pyc", ":(exclude)**/*.pyc")
    if add.returncode != 0:
        return {"ok": False, "failure": {"type": "git_add_failed", "detail": add.stderr.strip()}}
    commit = _git_run(
        worktree,
        *git_wrapper.identity_config_args(identity),
        "commit",
        "-m",
        f"patch: contingency engaged for {objection_id}",
        "-m",
        "The must-answer check failed; the reviewed contingency plan was implemented (one pivot per question).",
    )
    if commit.returncode != 0:
        return {"ok": False, "failure": {"type": "git_commit_failed", "detail": commit.stderr.strip()}}
    return {"ok": True, "commit": _git_run(worktree, "rev-parse", "HEAD").stdout.strip()}


def _run_codex(worktree: Path, prompt: str, *, ctx: org_log.RunContext) -> dict[str, Any]:
    out_file = worktree.parent / "codex-output.txt"
    if out_file.exists():
        out_file.unlink()
    cmd = [
        "codex",
        "exec",
        "--sandbox",
        "workspace-write",
        "-C",
        str(worktree),
        "-o",
        str(out_file),
        "--json",
        prompt,
    ]

    def invoke(command: list[str]) -> subprocess.CompletedProcess[str]:
        return org_log.logged_subprocess(
            command,
            ctx=ctx,
            capture_policy="head_tail",
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
        )

    completed = _run_with_reset_retries(
        lambda: invoke(cmd),
        ctx=ctx,
        event_name="patch_author.code_worker.codex_reset_wait",
        resume_once=lambda session_id: invoke(_codex_resume_command(cmd, session_id)),
    )
    # codex can exit non-zero / write no -o file -> check BOTH before trusting
    # the worktree (fail closed).
    if completed.returncode != 0 or not out_file.exists():
        if _transient_retries_exhausted(completed):
            return {
                "failure": {
                    "type": _TRANSIENT_RETRIES_EXHAUSTED,
                    "marker": _TRANSIENT_RETRIES_EXHAUSTED,
                    "detail": f"returncode={completed.returncode} output_exists={out_file.exists()}",
                }
            }
        return {
            "failure": {
                "type": "codex_failed",
                "detail": f"returncode={completed.returncode} output_exists={out_file.exists()}",
            }
        }
    status = _git_run(worktree, "status", "--porcelain", "-uall")
    if status.returncode != 0:
        return {"failure": {"type": "git_status_failed", "detail": status.stderr.strip()}}
    if not status.stdout.strip():
        return {"failure": {"type": "item_no_edits", "detail": "codex produced no edits for this item"}}
    return {"failure": None}


def _wait_for_codex_reset(not_before: Any, *, ctx: org_log.RunContext) -> None:
    _shared_wait_for_codex_reset(
        not_before,
        ctx=ctx,
        event_name="patch_author.code_worker.codex_reset_wait",
    )


def _materialize_producer_task_binding(
    repo_path: Path,
    worktree: Path,
    brief: Mapping[str, Any],
    *,
    branch: str,
    identity: Mapping[str, str],
) -> dict[str, Any]:
    """Commit the exact plural binding before any model-authored code."""

    series_head = brief.get("series_snapshot_oid")
    if brief.get("canonical_producer_plan") is not True and series_head is None:
        return {"ok": True, "active": False}
    if not isinstance(series_head, str) or re.fullmatch(r"[0-9a-f]{40,64}", series_head) is None:
        return {
            "ok": False,
            "status": "producer_task_binding_invalid",
            "detail": "gated series snapshot is missing",
        }
    prepared = producer_lifecycle.prepare_task_binding(
        repo_path,
        series_head,
        branch,
        {
            "series_branch": str(brief["series_branch"]),
            "node_path": str(brief["node_path"]),
            "contrib_branch": branch,
            "author": dict(identity),
        },
        readiness_decision=brief.get("producer_authorability_decision"),
        expected_readiness_route=str(
            brief.get("producer_gate_route") or producer_lifecycle.CODE_AUTHORING_ROUTE
        ),
    )
    if prepared.status == "inactive":
        if brief.get("canonical_producer_plan") is True:
            return {
                "ok": False,
                "status": "producer_task_binding_missing",
                "detail": (
                    "migrated producer authoring cannot continue without "
                    "materializing its task binding"
                ),
                "frozen_oid": series_head,
            }
        return {"ok": True, "active": False}
    if not prepared.active:
        failure = {
            "ok": False,
            "status": f"producer_task_binding_{prepared.status}",
            "detail": prepared.detail,
            "frozen_oid": series_head,
        }
        decision_dict = getattr(prepared.decision, "as_dict", None)
        if isinstance(prepared.decision, Mapping):
            failure["authorability_decision"] = dict(prepared.decision)
        elif callable(decision_dict):
            failure["authorability_decision"] = decision_dict()
        return failure
    for path, content in prepared.files.items():
        target = worktree / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    add = _git_run(worktree, "add", "--", *sorted(prepared.files))
    if add.returncode != 0:
        return {"ok": False, "status": "git_add_failed", "detail": add.stderr.strip()}
    commit = _git_run(
        worktree,
        *git_wrapper.identity_config_args(identity),
        "commit",
        "-m",
        f"{producer_lifecycle.TASK_BINDING_SUBJECT_PREFIX} {brief['node_key']}",
        "-m",
        "Complete-body digest is carried by later item trailers; the binding commit OID is derived from Git.",
    )
    if commit.returncode != 0:
        return {"ok": False, "status": "git_commit_failed", "detail": commit.stderr.strip()}
    return {
        "ok": True,
        "active": True,
        "commit": _git_run(worktree, "rev-parse", "HEAD").stdout.strip(),
        "body_sha256": prepared.body_sha256,
        "obligation_ids_by_item": dict(prepared.obligation_ids_by_item or {}),
    }


def _write_producer_completion_assertion(
    repo_path: Path,
    worktree: Path,
    *,
    identity: Mapping[str, str],
) -> dict[str, Any]:
    """Commit the producer-authored assertion for the current implementation tip."""

    implementation_oid = _git_run(worktree, "rev-parse", "HEAD").stdout.strip()
    try:
        prepared = producer_lifecycle.prepare_completion_assertion(
            repo_path, implementation_oid
        )
    except Exception as exc:
        return {
            "ok": False,
            "status": "producer_completion_assertion_invalid",
            "detail": str(exc),
        }
    for path, content in prepared.files.items():
        target = worktree / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    add = _git_run(worktree, "add", "--", *sorted(prepared.files))
    if add.returncode != 0:
        return {"ok": False, "status": "git_add_failed", "detail": add.stderr.strip()}
    commit = _git_run(
        worktree,
        *git_wrapper.identity_config_args(identity),
        "commit",
        "-m",
        f"{producer_lifecycle.COMPLETION_ASSERTION_SUBJECT_PREFIX} {implementation_oid}",
    )
    if commit.returncode != 0:
        return {"ok": False, "status": "git_commit_failed", "detail": commit.stderr.strip()}
    return {
        "ok": True,
        "active": True,
        "commit": _git_run(worktree, "rev-parse", "HEAD").stdout.strip(),
        "body_sha256": prepared.body_sha256,
        "implementation_oid": implementation_oid,
    }


def _commit_item(
    repo_path: Path,
    worktree: Path,
    brief: Mapping[str, Any],
    item: Mapping[str, Any],
    window: Mapping[str, Any],
    *,
    identity: Mapping[str, str],
    repair_rounds: int,
    producer_binding: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    add = _git_run(
        worktree,
        "add",
        "-A",
        "--",
        ".",
        ":(exclude)__pycache__",
        ":(exclude)**/__pycache__",
        ":(exclude)*.pyc",
        ":(exclude)**/*.pyc",
    )
    if add.returncode != 0:
        return {"ok": False, "status": "git_add_failed", "detail": add.stderr.strip()}
    working_title = str(brief["cover_letter"].get("working_title") or brief["series_branch"])
    subject = f"patch: {working_title} [{item['item_id']}]"
    body_lines = [
            f"Patch-plan item: {item['item_id']} ({item['kind']})",
            f"Referenced approach nodes: {', '.join(window['referenced_node_ids']) or 'none'}",
            f"Files previously touched on this branch: {', '.join(window['files_touched_so_far']) or 'none'}",
            f"Repair rounds used: {repair_rounds}",
        ]
    if producer_binding is not None:
        digest = str(producer_binding["body_sha256"])
        obligations_by_item = producer_binding.get("obligation_ids_by_item", {})
        if not isinstance(obligations_by_item, Mapping):
            return {
                "ok": False,
                "status": "producer_task_binding_invalid",
                "detail": "producer task binding item obligations are not a mapping",
            }
        item_id = str(item["item_id"])
        if item_id in obligations_by_item:
            try:
                trailers = producer_lifecycle.item_binding_trailers(
                    digest,
                    obligations_by_item[item_id],
                )
            except ValueError as exc:
                return {
                    "ok": False,
                    "status": "producer_task_binding_invalid",
                    "detail": str(exc),
                }
            body_lines.extend(["", *trailers])
    body = "\n".join(body_lines)
    commit = _git_run(worktree, *git_wrapper.identity_config_args(identity), "commit", "-m", subject, "-m", body)
    if commit.returncode != 0:
        return {"ok": False, "status": "git_commit_failed", "detail": commit.stderr.strip()}
    sha = _git_run(worktree, "rev-parse", "HEAD").stdout.strip()
    changed = [
        path
        for entry in git_wrapper.changed_paths(repo_path, f"{sha}^", sha)
        for path in entry.get("paths", [])
    ]
    return {"ok": True, "commit": sha, "changed_paths": changed}


def _cue_dir(item_id: str) -> str:
    safe_item_id = re.sub(r"-+", "-", re.sub(r"[^A-Za-z0-9._-]", "-", item_id))
    return f"ai-org/implementation-cue/{safe_item_id}"


def _run_cue_frame_validation(worktree: Path, item_id: str) -> dict[str, Any]:
    cue_dir = _cue_dir(item_id)
    frame_path = f"{cue_dir}/frame.cue"
    positive_path = f"{cue_dir}/real-states/positive.json"
    negative_path = f"{cue_dir}/real-states/negative.json"
    missing = [
        name
        for name, path in (("frame", frame_path), ("positive_state", positive_path), ("negative_state", negative_path))
        if not (worktree / path).is_file()
    ]
    if missing:
        return {"ok": False, "failure": {"type": "cue_artifact_missing", "detail": ", ".join(missing)}}
    frame = (worktree / frame_path).read_text(encoding="utf-8")
    markers = ["#StateSpace", "#Invariant", "#Transition", "#PreState", "#PostState"]
    missing_markers = [marker for marker in markers if marker not in frame]
    if missing_markers:
        return {"ok": False, "failure": {"type": "cue_frame_invalid", "detail": "missing CUE definitions: " + ", ".join(missing_markers)}}
    static_checks = _static_stateful_vacuity_checks(frame, markers)
    if any(check["result"] == "fail" for check in static_checks):
        return {"ok": False, "failure": {"type": "cue_vacuity_validation_failed", "detail": _failed_check_detail(static_checks)}}
    state_failures = _validate_stateful_json_states(worktree, positive_path, negative_path)
    if state_failures:
        return {"ok": False, "failure": {"type": "cue_state_invalid", "detail": "; ".join(state_failures)}}
    vet = _run_cue_vet(worktree, frame_path, positive_path, negative_path)
    if vet.get("failure") is not None:
        return {"ok": False, "failure": vet["failure"]}
    checks = [*static_checks, *_dynamic_stateful_vacuity_checks(vet["checks"])]
    if any(check["result"] == "fail" for check in checks):
        return {"ok": False, "failure": {"type": "cue_vacuity_validation_failed", "detail": _failed_check_detail(checks)}}
    return {"ok": True}


def _run_cue_vet(worktree: Path, frame_path: str, positive_path: str, negative_path: str) -> dict[str, Any]:
    cue = shutil.which("cue")
    if cue is None:
        return {"failure": {"type": "cue_missing", "detail": "CUE validation requires the cue executable"}}
    checks: list[dict[str, Any]] = []
    for name, state_path, expected_success in (("positive", positive_path, True), ("negative", negative_path, False)):
        try:
            completed = subprocess.run(
                [cue, "vet", frame_path, state_path], cwd=worktree, check=False,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, stdin=subprocess.DEVNULL,
            )
        except OSError as exc:
            return {"failure": {"type": "cue_failed", "detail": f"{cue}: {exc}"}}
        passed = (completed.returncode == 0) == expected_success
        checks.append({"name": name, "expected": "pass" if expected_success else "fail", "result": "pass" if passed else "fail", "exit_status": completed.returncode, "output_tail": (completed.stdout or "")[-2000:]})
    failed = next((check for check in checks if check["result"] == "fail"), None)
    if failed is not None:
        return {"failure": {"type": "cue_vet_failed", "detail": f"{failed['name']} state expected {failed['expected']} but exit={failed['exit_status']} output_tail={failed['output_tail']}"}}
    return {"failure": None, "checks": checks}


def _validate_stateful_json_states(worktree: Path, positive_path: str, negative_path: str) -> list[str]:
    failures: list[str] = []
    for name, path in (("positive", positive_path), ("negative", negative_path)):
        try:
            payload = json.loads((worktree / path).read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            failures.append(f"{name} state is not JSON: {exc}")
            continue
        if not isinstance(payload, Mapping) or not payload:
            failures.append(f"{name} state must be a non-empty JSON object")
    return failures


def _static_stateful_vacuity_checks(frame: str, markers: list[str]) -> list[dict[str, Any]]:
    vacuous = [marker for marker in markers if _cue_required_definition_is_vacuous(frame, marker)]
    return [
        {"name": "required_cue_definitions_constrain_data", "result": "fail" if vacuous else "pass", "vacuous_definitions": vacuous},
        {"name": "invariant_not_trivially_true", "result": "fail" if "#Invariant" in vacuous else "pass", "vacuous_definitions": ["#Invariant"] if "#Invariant" in vacuous else []},
    ]


def _cue_required_definition_is_vacuous(frame: str, marker: str) -> bool:
    value = _cue_definition_value(frame, marker)
    normalized = re.sub(r"\s+", "", value.split("//", 1)[0])
    return normalized in {"", "_", "true", "{}", "{...}", "...", "_:_", "{_:_}"}


def _cue_definition_value(frame: str, marker: str) -> str:
    lines = frame.splitlines()
    marker_re = re.compile(rf"^\s*{re.escape(marker)}\s*:\s*(.*)$")
    for index, line in enumerate(lines):
        match = marker_re.match(line)
        if match is None:
            continue
        inline = match.group(1).strip()
        if inline and inline != "{":
            return inline
        block = [inline] if inline else []
        for child in lines[index + 1:]:
            if re.match(r"^\s*#\w+\s*:", child):
                break
            if child.strip():
                block.append(child.strip())
        return "\n".join(block)
    return ""


def _dynamic_stateful_vacuity_checks(checks: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    negative = next((check for check in checks if check.get("name") == "negative"), {})
    return [
        {"name": "negative_invalid_case_required", "result": "pass" if negative else "fail"},
        {"name": "negative_invalid_case_rejected", "result": "pass" if negative and negative.get("result") == "pass" and negative.get("expected") == "fail" else "fail"},
    ]


def _failed_check_detail(checks: list[Mapping[str, Any]]) -> str:
    failed = [str(check.get("name") or "unnamed_check") for check in checks if check.get("result") == "fail"]
    return "failed checks: " + ", ".join(failed)


def _write_result_artifact(
    repo_path: Path,
    worktree: Path,
    brief: Mapping[str, Any],
    *,
    identity: Mapping[str, str],
    must_answer_outcomes: Mapping[str, Any] | None = None,
    allow_unchanged_regeneration: bool = False,
) -> dict[str, Any]:
    """Harness-computed implementation-result.json (Probe-A contract gap #3).

    acknowledged_patchwork_checks is computed by the worker harness — it IS
    the org toolchain — from the same engine facts the acceptance judge will
    recompute. The model never fabricates (or even sees) this artifact write.
    """
    facts = brief.get("facts") if isinstance(brief.get("facts"), Mapping) else {}
    checks = facts.get("checks", {}) if isinstance(facts, Mapping) else {}
    result = {
        "patch_series_branch": brief["series_branch"],
        "node_key": brief["node_key"],
        "acknowledged_patchwork_checks": {
            name: item.get("current_value") for name, item in checks.items() if isinstance(item, Mapping)
        },
    }
    questions = brief.get("must_answer_questions", [])
    if questions:
        # Defense-in-depth on the early gate: an outcome per question or fail typed.
        outcomes = dict(must_answer_outcomes or {})
        question_ids = [
            str(question.get("objection_id") or "")
            for question in questions
            if isinstance(question, Mapping)
        ]
        missing, unexpected = contributor_handoff.outcome_key_mismatches(
            {"must_answer_outcomes": outcomes}, question_ids
        )
        if missing:
            return {"ok": False, "status": "must_answer_outcome_missing", "objection_ids": sorted(missing)}
        if unexpected:
            return {
                "ok": False,
                "status": "must_answer_outcome_unexpected",
                "objection_ids": unexpected,
            }
        result["must_answer_outcomes"] = outcomes
    try:
        contributor_handoff.validate_result_transition(
            result,
            expected_series_branch=str(brief["series_branch"]),
            expected_node_key=str(brief["node_key"]),
            question_ids=question_ids if questions else (),
        )
    except ValueError as exc:
        return {"ok": False, "status": "result_contract_violation", "detail": str(exc)}
    violations = validate_result_against_schema(result, brief["contract_schema"])
    if violations:
        return {"ok": False, "status": "result_schema_violation", "violations": violations}
    prepared = contributor_handoff.prepare_result(result)
    (worktree / RESULT_ARTIFACT_PATH).write_bytes(prepared.canonical.data)
    add = _git_run(worktree, "add", RESULT_ARTIFACT_PATH)
    if add.returncode != 0:
        return {"ok": False, "status": "git_add_failed", "detail": add.stderr.strip()}
    # The result is a harness observation, not a producer claim. Keep its Git
    # authorship on the engine identity just as the registered body variant is
    # harness-authored; contribution identity lookup steps over this generated
    # tip to the producer-authored assertion or implementation.
    commit_args = [*git_wrapper.identity_config_args(), "commit"]
    if allow_unchanged_regeneration:
        commit_args.append("--allow-empty")
    commit = _git_run(
        worktree,
        *commit_args,
        "-m",
        f"patch: implementation result for {brief['node_key']}",
        "-m",
        "Harness-computed per the committed contract "
        f"({patch_author_contract.IMPLEMENTATION_RESULT_SCHEMA_PATH} on the series branch): "
        "acknowledged_patchwork_checks echoes the engine-computed facts; the model never writes this file.",
    )
    if commit.returncode != 0:
        return {"ok": False, "status": "git_commit_failed", "detail": commit.stderr.strip()}
    return {"ok": True, "commit": _git_run(worktree, "rev-parse", "HEAD").stdout.strip()}


def validate_result_against_schema(result: Mapping[str, Any], schema: Mapping[str, Any]) -> list[str]:
    """Validate the result artifact against the COMMITTED schema. Typed, total.

    Deliberately a small draft-07 subset (type/required/additionalProperties/
    per-property type): the committed contract is the authority, and this
    validator enforces exactly what it declares without a schema library
    dependency.
    """
    violations: list[str] = []
    if schema.get("type") == "object" and not isinstance(result, Mapping):
        return ["result is not an object"]
    properties = schema.get("properties") if isinstance(schema.get("properties"), Mapping) else {}
    for field in schema.get("required", []):
        if field not in result:
            violations.append(f"required field missing: {field}")
    if schema.get("additionalProperties") is False:
        for field in result:
            if field not in properties:
                violations.append(f"unknown field: {field}")
    for field, spec in properties.items():
        if field not in result or not isinstance(spec, Mapping):
            continue
        expected = spec.get("type")
        value = result[field]
        if expected == "string" and not isinstance(value, str):
            violations.append(f"field {field} must be a string")
        if expected == "object" and not isinstance(value, Mapping):
            violations.append(f"field {field} must be an object")
    return violations


# ---------------------------------------------------------------------------
# Cross-clone re-implement loop (acceptance blocked -> fast-forward append).
# ---------------------------------------------------------------------------

def address_feedback(
    repo,
    record: Mapping[str, Any],
    *,
    feedback: Any,
    attempt: int = 1,
    ctx: org_log.RunContext | None = None,
) -> dict[str, Any]:
    """Append fix commits to the already-published branch (never rewrite it).

    This is the existing cross-clone loop's fix half: a canonical-side
    `acceptance: blocked` verdict travels back as blockers, and the author
    repairs in a bounded reactive window (blockers + files this branch already
    changed), committing fast-forward. One reactive round per call; the
    caller's attempt counter is the outer bound.
    """
    repo_path = Path(repo).resolve()
    series_branch = str(record["series_branch"])
    branch = str(record["contrib_branch"])
    identity = dict(record["author"])
    series_id = series_branch.removeprefix("ai-org/patch-series/")
    feedback_stage = "patch_author.code_worker.feedback"
    log_ctx = ctx or org_log.RunContext(repo=repo_path, patch_series_id=series_id, stage=feedback_stage, attempt=attempt)
    if ctx is not None:
        log_ctx = ctx.child(patch_series_id=series_id, stage=feedback_stage, attempt=attempt)
    if not _branch_exists(repo_path, branch):
        return {"ok": False, "status": "contrib_branch_missing", "branch": branch}

    brief = load_brief(
        repo_path,
        series_branch,
        str(record["node_path"]),
        producer_gate_route="producer_feedback_authoring",
    )
    if not brief.get("ok"):
        return brief
    migrated_producer_plan = brief.get("canonical_producer_plan") is True
    binding_text = git_wrapper.show_file(
        repo_path, branch, producer_lifecycle.TASK_BINDING_PATH
    )
    if migrated_producer_plan and binding_text is None:
        return {
            "ok": False,
            "status": "producer_task_binding_missing",
            "detail": (
                "migrated producer feedback cannot continue without its "
                "materialized task binding"
            ),
        }
    producer_lifecycle_active = migrated_producer_plan or binding_text is not None
    producer_binding: dict[str, Any] | None = None
    if producer_lifecycle_active:
        binding_raw = git_wrapper.show_file_bytes(
            repo_path, branch, producer_lifecycle.TASK_BINDING_PATH
        )
        try:
            if binding_raw is None:
                raise ValueError("producer task binding is missing")
            binding_body = patch_series_bodies.read_producer_task_binding(binding_raw)
            if binding_body.get("series_snapshot_oid") != brief.get(
                "series_snapshot_oid"
            ):
                raise ValueError(
                    "producer task binding does not match the gated series snapshot"
                )
            producer_lifecycle.validate_promise_bindings(binding_body)
            if migrated_producer_plan:
                producer_lifecycle.validate_task_binding_source(
                    binding_body,
                    record,
                    series_snapshot_oid=str(brief["series_snapshot_oid"]),
                    readiness_decision=brief.get(
                        "producer_authorability_decision"
                    ),
                    expected_readiness_route=(
                        producer_lifecycle.FEEDBACK_AUTHORING_ROUTE
                    ),
                )
            producer_binding = {
                "body_sha256": hashlib.sha256(binding_raw).hexdigest(),
                "obligation_ids": producer_lifecycle.required_claim_set(binding_body),
            }
        except Exception as exc:
            return {
                "ok": False,
                "status": "producer_task_binding_invalid",
                "detail": str(exc),
            }

    temp_dir = Path(tempfile.mkdtemp(prefix="ai-org-patch-author-"))
    worktree = temp_dir / "worktree"
    outcome: dict[str, Any] = {"ok": False, "status": "worktree_failed", "branch": branch}
    try:
        created = _git_run(repo_path, "worktree", "add", str(worktree), branch)
        if created.returncode != 0:
            outcome = {"ok": False, "status": "worktree_failed", "branch": branch, "detail": created.stderr.strip()}
            return outcome
        changed = [
            path
            for entry in git_wrapper.changed_paths(
                repo_path, _local_default_branch_for_comparison(repo_path), branch
            )
            for path in entry.get("paths", [])
        ]
        window = {
            "item_id": f"{brief['plan_id']}#acceptance-feedback",
            "item_kind": "acceptance_feedback",
            "item": {"blockers": feedback},
            "referenced_nodes": {},
            "referenced_node_ids": [],
            "files_touched_so_far": sorted(set(changed)),
            "cover_letter": dict(brief["cover_letter"]),
            "acceptance_criteria": list(brief.get("acceptance_criteria", [])),
            "functional_check_description": str(brief.get("functional_check_description") or ""),
            "spine_paths": list(brief.get("spine_paths", [])),
            "facts": dict(brief.get("facts", {})),
            "precedent_facets": _empty_precedent_facets(),
        }
        prompt = render_item_prompt(window, repair={"typed_error": {"type": "acceptance_blocked", "detail": feedback}, "failed_round": attempt})
        codex_stage = f"{feedback_stage}.codex"
        run = _run_codex(worktree, prompt, ctx=log_ctx.child(stage=codex_stage))
        if run.get("failure") is not None:
            outcome = {"ok": False, "status": "feedback_round_failed", "branch": branch, "failure": run["failure"]}
            return outcome
        with org_log.span(
            "patch_author.code_worker.feedback.commit",
            log_ctx.child(stage="patch_author.code_worker.feedback.commit"),
        ):
            commit = _commit_feedback(
                worktree,
                identity=identity,
                attempt=attempt,
                item_id=f"{brief['plan_id']}#acceptance-feedback-{attempt}",
                producer_binding=producer_binding,
            )
        if not commit.get("ok"):
            outcome = commit
            return outcome
        implementation_commit = str(commit["commit"])
        assertion: dict[str, Any] = {"ok": True, "active": False}
        regenerated_result: dict[str, Any] = {"ok": True}
        if producer_lifecycle_active:
            prior_result_raw = git_wrapper.show_file(
                repo_path, implementation_commit, RESULT_ARTIFACT_PATH
            )
            prior_outcomes: Mapping[str, Any] = {}
            if prior_result_raw is not None:
                try:
                    prior_result = contributor_handoff.parse_result(prior_result_raw)
                    if isinstance(prior_result.get("must_answer_outcomes"), Mapping):
                        prior_outcomes = prior_result["must_answer_outcomes"]
                except Exception as exc:
                    outcome = {
                        "ok": False,
                        "status": "result_contract_violation",
                        "detail": str(exc),
                    }
                    return outcome
            assertion = _write_producer_completion_assertion(
                repo_path, worktree, identity=identity
            )
            if not assertion.get("ok"):
                outcome = assertion
                return outcome
            regenerated_result = _write_result_artifact(
                repo_path,
                worktree,
                brief,
                identity=identity,
                must_answer_outcomes=prior_outcomes,
                allow_unchanged_regeneration=True,
            )
            if not regenerated_result.get("ok"):
                outcome = regenerated_result
                return outcome
        outcome = {
            "ok": True,
            "branch": branch,
            "commit": regenerated_result.get("commit", implementation_commit),
            "implementation_commit": implementation_commit,
            "patch_series_branch": series_branch,
            "node_key": brief["node_key"],
        }
        if producer_lifecycle_active:
            outcome.update(
                {
                    "completion_assertion_commit": assertion["commit"],
                    "completion_assertion_body_sha256": assertion["body_sha256"],
                    "result_commit": regenerated_result["commit"],
                }
            )
        return outcome
    finally:
        # Cleanup is SUCCESS-ONLY (same invariant as drive): failure preserves
        # the temp tree for forensics; detaching frees the published branch so
        # a later feedback round can `worktree add` it again.
        if outcome.get("ok"):
            if worktree.exists():
                _git_run(repo_path, "worktree", "remove", "--force", str(worktree))
            shutil.rmtree(temp_dir, ignore_errors=True)
        else:
            outcome["preserved_worktree"] = _preserve_failed_worktree(
                temp_dir, worktree, branch=branch, ctx=log_ctx
            )


def _commit_feedback(
    worktree: Path,
    *,
    identity: Mapping[str, str],
    attempt: int,
    item_id: str = "acceptance-feedback",
    producer_binding: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    add = _git_run(worktree, "add", "-A", "--", ".", ":(exclude)__pycache__", ":(exclude)**/__pycache__", ":(exclude)*.pyc", ":(exclude)**/*.pyc")
    if add.returncode != 0:
        return {"ok": False, "status": "git_add_failed", "detail": add.stderr.strip()}
    subject = f"patch: address acceptance blockers [attempt {attempt}]"
    body_lines: list[str] = []
    if producer_binding is not None:
        digest = str(producer_binding.get("body_sha256") or "")
        obligation_ids = producer_binding.get("obligation_ids")
        try:
            trailers = producer_lifecycle.item_binding_trailers(
                digest, obligation_ids
            )
        except ValueError as exc:
            return {
                "ok": False,
                "status": "producer_task_binding_invalid",
                "detail": str(exc),
            }
        subject += f" [{item_id}]"
        body_lines = list(trailers)
    commit = _git_run(
        worktree,
        *git_wrapper.identity_config_args(identity),
        "commit",
        "-m",
        subject,
        *(["-m", "\n".join(body_lines)] if body_lines else []),
    )
    if commit.returncode != 0:
        return {"ok": False, "status": "git_commit_failed", "detail": commit.stderr.strip()}
    return {"ok": True, "commit": _git_run(worktree, "rev-parse", "HEAD").stdout.strip()}


# ---------------------------------------------------------------------------
# Local git helpers.
# ---------------------------------------------------------------------------

def _preserve_failed_worktree(temp_dir: Path, worktree: Path, *, branch: str, ctx: org_log.RunContext) -> str:
    """Failure-path counterpart of the success-only cleanup: keep the tree.

    Invariant: a failed item's temp working tree is NEVER deleted — it is the
    only inspectable evidence of what the worker did (transient failures like
    provider limit exhaustion left hours of work here). The checkout is
    detached (files untouched) so the preserved tree pins no branch, and the
    preserved path is logged and returned for the caller's failure result.
    """
    if worktree.exists():
        _git_run(worktree, "checkout", "--detach")
        preserved = str(worktree)
    else:
        preserved = str(temp_dir)
    org_log.emit(
        "patch_author.code_worker.worktree_preserved",
        {"preserved_worktree": preserved, "branch": branch},
        ctx=ctx,
        severity="warning",
    )
    return preserved


def _branch_exists(repo_path: Path, branch: str) -> bool:
    return _git_run(repo_path, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}").returncode == 0


def _local_default_branch_for_comparison(repo_path: Path) -> str:
    """Return the local baseline for a read-only, already-created-tree diff."""
    origin_head = _git_run(repo_path, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    if origin_head.returncode == 0:
        ref = origin_head.stdout.strip()
        if ref.startswith("origin/"):
            return ref
    current = _git_run(repo_path, "symbolic-ref", "--short", "HEAD")
    if current.returncode == 0 and current.stdout.strip():
        return current.stdout.strip()
    raise RuntimeError("could not determine comparison baseline")


def _refreshed_default_base(repo_path: Path) -> dict[str, Any]:
    """Resolve origin's current default tip to an immutable commit."""
    try:
        origin_head = _git_run(
            repo_path, "symbolic-ref", "--quiet", "refs/remotes/origin/HEAD"
        )
        advertised = _git_run(repo_path, "ls-remote", "--symref", "origin", "HEAD")
        if advertised.returncode != 0:
            return {
                "ok": False,
                "status": "base_unverifiable",
                "detail": _git_failure_detail(
                    advertised, "origin did not advertise its default branch"
                ),
            }
        target = next(
            (
                fields[1]
                for line in advertised.stdout.splitlines()
                if len(fields := line.split()) >= 3
                and fields[0] == "ref:"
                and fields[1].startswith("refs/heads/")
                and fields[2] == "HEAD"
            ),
            "",
        )
        default_name = target.removeprefix("refs/heads/") if target else ""
        if not default_name:
            return {
                "ok": False,
                "status": "base_unverifiable",
                "detail": "origin/HEAD does not name an origin branch",
            }

        remote_ref = f"refs/remotes/origin/{default_name}"
        # Memento: the implementation base is origin's, never the clone's. A
        # sync step that can be forgotten is not a mechanism; refresh here.
        fetched = _git_run(
            repo_path,
            "fetch",
            "origin",
            f"+refs/heads/{default_name}:{remote_ref}",
        )
        if fetched.returncode != 0:
            return {
                "ok": False,
                "status": "base_unverifiable",
                "default_branch": default_name,
                "detail": _git_failure_detail(fetched, f"could not fetch origin/{default_name}"),
            }
        if origin_head.returncode != 0 or origin_head.stdout.strip() != remote_ref:
            set_head = _git_run(
                repo_path, "symbolic-ref", "refs/remotes/origin/HEAD", remote_ref
            )
            if set_head.returncode != 0:
                return {
                    "ok": False,
                    "status": "base_unverifiable",
                    "default_branch": default_name,
                    "detail": _git_failure_detail(set_head, "could not refresh origin/HEAD"),
                }
        resolved = _git_run(repo_path, "rev-parse", "--verify", f"{remote_ref}^{{commit}}")
        commit = resolved.stdout.strip()
        if resolved.returncode != 0 or not commit:
            return {
                "ok": False,
                "status": "base_unverifiable",
                "default_branch": default_name,
                "detail": _git_failure_detail(resolved, f"could not resolve {remote_ref}"),
            }
    except OSError as exc:
        return {
            "ok": False,
            "status": "base_unverifiable",
            "detail": str(exc) or "git could not verify origin's default base",
        }
    return {
        "ok": True,
        "default_branch": default_name,
        "ref": remote_ref,
        "commit": commit,
    }


def _git_failure_detail(result: subprocess.CompletedProcess[str], fallback: str) -> str:
    return result.stderr.strip() or result.stdout.strip() or fallback


def _is_common_8(value: object) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == set(RFC_FIELDS)
        and all(isinstance(value[field], str) for field in STRING_FIELDS)
        and all(isinstance(value[field], list) and all(isinstance(item, str) for item in value[field]) for field in STRING_ARRAY_FIELDS)
        and validate_tech_stack(value.get("tech_stack"))
        and validate_user_experience_requirements(value.get("user_experience_requirements"))
    )


def _format_rfc(cover_letter: Mapping[str, Any]) -> str:
    lines = []
    for field in RFC_FIELDS:
        value = cover_letter[field]
        lines.append(f"{field}:\n" + ("\n".join(f"- {item}" for item in value) if isinstance(value, list) else str(value)))
    return "\n\n".join(lines) + "\n"


def _git_run(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
