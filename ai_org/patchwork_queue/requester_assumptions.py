"""Requester-authority assumption handling shared by patch series receive/review.

Memento — REQUESTER INTERVENTION PORTS (ratified in live use, requester 2026-07-07).
The human requester (and, per the court backlog, a future lawyer agent through
the SAME ports) can intervene on a committed review round record between the
review round and the author reform. Two ports exist; both are exercised by
editing patch-series-review-rounds/round-NNNN-direction-review-record.json on
the series branch and committing with a clearly-labeled requester commit:

PORT 1 — ANNOTATION (guidance reaches the author verbatim):
  Append an attributed note to an objection's ``claim`` text, e.g.
  ``... [REQUESTER NOTE (requester, human requester authority): <guidance>]``.
  Never alter the reviewer-authored words themselves — append only, with
  explicit attribution; the separate commit keeps provenance honest.
  This works because reform passes ``claim`` verbatim into the revision
  prompt (receive._affected_step_revision_plans -> objections[].claim), so
  the note lands in front of the authoring model.
  First live use: CUE body/lens run — git-reduction notes ("version token =
  git commit SHA, do not invent bespoke token composition"; "write-back
  atomicity = temp worktree + commit, no bespoke rollback").

PORT 2 — DEMOTION (removes an objection from the author's plate):
  Set ``resolution_authority`` to ``"requester"`` on the objection. Reform
  then routes it out of author_blocking (see requester_authority_objections
  below) and records it as a requester assumption instead of forcing the
  author to revise for it. This is the correct lever for "real but should
  not block direction" rulings (e.g. toolchain upgrade policy).

Do NOT fabricate reviewer content, flip verdicts, or edit axis_reviews —
review round records are otherwise immutable; author answers land in
separate response records, requester rulings land through these two ports.
"""
from __future__ import annotations

import json
from typing import Any, Mapping

from ai_org.patchwork_queue.field_registry import WORK_ORDER_VIEW_FIELDS


def patch_series_to_view(patch_series_view: Mapping[str, Any]) -> dict[str, Any]:
    return {field: patch_series_view[field] for field in WORK_ORDER_VIEW_FIELDS}


def open_blocking_objections(record: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        dict(objection)
        for objection in record.get("objections", [])
        if isinstance(objection, Mapping)
        and objection.get("type") == "blocking"
        and objection.get("status") in {"open", "carried-forward"}
    ]


def requester_authority_objections(objections: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [dict(objection) for objection in objections if objection.get("resolution_authority") == "requester"]


def record_requester_authority_assumptions(
    patch_series_view: Mapping[str, Any],
    approach: Mapping[str, Any],
    objections: list[Mapping[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, str]], bool]:
    revised = patch_series_to_view(patch_series_view)
    existing_entries = requester_assumption_entries_by_objection_id(revised)
    existing_ids = set(existing_entries)
    entries: list[dict[str, str]] = []
    changed = False
    for objection in objections:
        objection_id = str(objection.get("objection_id") or "").strip()
        if not objection_id:
            continue
        if objection_id in existing_ids:
            entries.append(existing_entries[objection_id])
            continue
        question = requester_assumption_question(objection)
        entry = {
            "objection_id": objection_id,
            "question": question,
            "adopted_default": requester_assumption_default(objection, revised, approach),
            "default_provenance": "deterministic",
            "rationale": requester_assumption_rationale(objection),
        }
        revised["constraints_assumptions"] = append_unique_strings(
            revised.get("constraints_assumptions"),
            [json.dumps(entry, sort_keys=True, ensure_ascii=True)],
        )
        revised["open_questions"] = append_unique_strings(
            revised.get("open_questions"),
            [f"{objection_id}: {question}"],
        )
        entries.append(entry)
        existing_ids.add(objection_id)
        existing_entries[objection_id] = entry
        changed = True
    return revised, entries, changed


def recorded_assumption_objection_ids(patch_series_view: Mapping[str, Any]) -> set[str]:
    return set(requester_assumption_entries_by_objection_id(patch_series_view))


def requester_assumption_entries_by_objection_id(patch_series_view: Mapping[str, Any]) -> dict[str, dict[str, str]]:
    entries: dict[str, dict[str, str]] = {}
    assumptions = patch_series_view.get("constraints_assumptions")
    if not isinstance(assumptions, list):
        return entries
    for item in assumptions:
        if not isinstance(item, str):
            continue
        try:
            parsed = json.loads(item)
        except json.JSONDecodeError:
            continue
        if not isinstance(parsed, Mapping):
            continue
        objection_id = parsed.get("objection_id")
        if not isinstance(objection_id, str) or not objection_id:
            continue
        entry: dict[str, str] = {"objection_id": objection_id}
        for key in ("question", "adopted_default", "default_provenance", "rationale"):
            value = parsed.get(key)
            entry[key] = value if isinstance(value, str) else ""
        entries[objection_id] = entry
    return entries


def requester_assumption_notes(
    objections: list[Mapping[str, Any]],
    patch_series_view: Mapping[str, Any],
) -> list[dict[str, str]]:
    entries_by_id = requester_assumption_entries_by_objection_id(patch_series_view)
    notes: list[dict[str, str]] = []
    for objection in objections:
        objection_id = str(objection.get("objection_id") or "")
        if objection_id in entries_by_id:
            notes.append(entries_by_id[objection_id])
    return notes


def requester_assumption_question(objection: Mapping[str, Any]) -> str:
    claim = str(objection.get("claim") or "").strip()
    impact = str(objection.get("impact") or "").strip()
    if claim and impact:
        return f"{claim} Impact if wrong: {impact}"
    return claim or impact or "Requester decision is needed."


def requester_assumption_default(
    objection: Mapping[str, Any],
    patch_series_view: Mapping[str, Any],
    approach: Mapping[str, Any],
) -> str:
    claim = str(objection.get("claim") or "").strip() or "the requester-authority concern"
    impact = str(objection.get("impact") or "").strip()
    action = str(objection.get("requested_author_action") or "").strip()
    action_default = {
        "re_explain": "continue with the current explanation",
        "provide_evidence": "continue without requiring additional author evidence",
        "revise_subtree": "continue without author-side subtree revision",
        "split_scope": "keep the current scope unsplit",
        "withdraw": "continue without withdrawal",
    }.get(action, "continue with the current authored direction")
    decision = _approach_decision_label(approach)
    decision_clause = f" for selected approach {decision}" if decision else ""
    claim_end = "" if claim.endswith((".", "?", "!")) else "."
    impact_clause = f" Monitor requester impact: {impact}" if impact else ""
    return f"{action_default}{decision_clause}: {claim}{claim_end}{impact_clause} Requester may override this default."


def requester_assumption_rationale(objection: Mapping[str, Any]) -> str:
    action = str(objection.get("requested_author_action") or "").strip()
    action_note = f" Requested author action was {action}." if action else ""
    return (
        "The objection is classified as requester-authority, so the author cannot settle it by technical revision."
        f"{action_note} Recording an explicit default preserves progress while keeping the requester question visible."
    )


def append_unique_strings(existing: Any, additions: list[str]) -> list[str]:
    values = list(existing) if isinstance(existing, list) else []
    seen = {value for value in values if isinstance(value, str)}
    for addition in additions:
        if addition and addition not in seen:
            values.append(addition)
            seen.add(addition)
    return values


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
