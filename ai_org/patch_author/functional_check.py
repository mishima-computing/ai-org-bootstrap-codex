"""Patch acceptance: git-read -> codex judgment -> git-write verdict."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import inspect
import json
from pathlib import Path
import os
import shutil
import stat
import subprocess
import tempfile
from typing import Any, Mapping

from ai_org.body_codec import strict_json_loads
from ai_org.schema_lifecycle import generated_schema_attr
from ai_org import deterministic_structure_gadgets as structure_gadgets
from ai_org import contributor_handoff, git_wrapper, patch_series_bodies
from ai_org.body_codec import BodyCodecClient
from ai_org.patch_author import announcements, producer_lifecycle
from ai_org.patchwork_queue import patch_series_gate

# PROVEN against codex v0.142.0 --output-schema:
# - no allOf / anyOf / oneOf / if-then
# - every object must set additionalProperties=false
# - required must list every key in properties; use empty strings/null unions for absent values
def build_verdict_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["reachable", "blockers", "notes"],
        "properties": {
            "reachable": {"type": "boolean"},
            "blockers": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["where", "why"],
                    "properties": {
                        "where": {"type": "string"},
                        "why": {"type": "string"},
                    },
                },
            },
            "notes": {"type": "string"},
        },
    }


_SCHEMA_BUILDERS = {
    "VERDICT_SCHEMA": build_verdict_schema,
}
_CODEX_OUTPUT_SCHEMA_BUILDERS = dict(_SCHEMA_BUILDERS)
NO_PATCHWORK_ANCHOR = object()
# Compatibility exports. The contributor-handoff boundary owns the subjects;
# functional acceptance only chooses and writes the typed marker.
ACCEPTANCE_REACHABLE_SUBJECT = contributor_handoff.ACCEPTANCE_REACHABLE_SUBJECT
ACCEPTANCE_BLOCKED_SUBJECT = contributor_handoff.ACCEPTANCE_BLOCKED_SUBJECT
ACCEPTANCE_AUTHORITY_NOTES_REF = "refs/notes/ai-org/acceptance-authority"
CLAIM_ADMISSION_PATH = "claim-admission.cue"
FUNCTIONAL_ACCEPTANCE_VERDICT_PATH = "functional-acceptance-verdict.cue"
ZERO_OID = "0" * 40


@dataclass(frozen=True, slots=True)
class FrozenAcceptanceSourceVector:
    """The mutable Git coordinates frozen before exact claim admission."""

    contribution_ref: str
    contribution_oid: str
    series_ref: str
    series_oid: str
    authority_notes_ref: str
    authority_notes_oid: str

    def stale_coordinates(self, repo) -> tuple[str, ...]:
        """Return every coordinate that moved after this vector was frozen."""

        live_notes = git_wrapper.head_sha(repo, self.authority_notes_ref) or (
            "0" * len(self.authority_notes_oid)
        )
        observed = (
            (
                self.contribution_ref,
                git_wrapper.head_sha(repo, self.contribution_ref),
                self.contribution_oid,
            ),
            (
                self.series_ref,
                git_wrapper.head_sha(repo, self.series_ref),
                self.series_oid,
            ),
            (self.authority_notes_ref, live_notes, self.authority_notes_oid),
        )
        return tuple(ref for ref, actual, expected in observed if actual != expected)

    def content_stale_coordinates(self, repo) -> tuple[str, ...]:
        """Return moved coordinates whose referenced tree also changed.

        Content changes are rejected before verdict objects are materialized.
        Commit-only ref movement remains for the final atomic transaction to
        reject, preserving the complete source-vector CAS boundary.
        """

        expected_by_ref = {
            self.contribution_ref: self.contribution_oid,
            self.series_ref: self.series_oid,
            self.authority_notes_ref: self.authority_notes_oid,
        }
        changed: list[str] = []
        for ref in self.stale_coordinates(repo):
            actual = git_wrapper.head_sha(repo, ref)
            expected = expected_by_ref[ref]
            if actual is None or not expected or set(expected) == {"0"}:
                changed.append(ref)
                continue
            actual_tree = _git_run(
                Path(repo), "rev-parse", "--verify", f"{actual}^{{tree}}"
            )
            expected_tree = _git_run(
                Path(repo), "rev-parse", "--verify", f"{expected}^{{tree}}"
            )
            if (
                actual_tree.returncode != 0
                or expected_tree.returncode != 0
                or actual_tree.stdout.strip() != expected_tree.stdout.strip()
            ):
                changed.append(ref)
        return tuple(changed)


def freeze_acceptance_source_vector(
    repo,
    contribution_ref: str,
    series_ref: str,
    *,
    contribution_oid: str | None = None,
    series_oid: str | None = None,
) -> FrozenAcceptanceSourceVector:
    """Capture contribution, series, and verifier-notes identities once."""

    frozen_contribution = contribution_oid or git_wrapper.head_sha(
        repo, contribution_ref
    )
    frozen_series = series_oid or git_wrapper.head_sha(repo, series_ref)
    if frozen_contribution is None:
        raise ValueError("contribution ref is missing")
    if frozen_series is None:
        raise ValueError("series ref is missing")
    frozen_notes = git_wrapper.head_sha(repo, ACCEPTANCE_AUTHORITY_NOTES_REF) or (
        "0" * len(frozen_contribution)
    )
    return FrozenAcceptanceSourceVector(
        contribution_ref=contribution_ref,
        contribution_oid=frozen_contribution,
        series_ref=series_ref,
        series_oid=frozen_series,
        authority_notes_ref=ACCEPTANCE_AUTHORITY_NOTES_REF,
        authority_notes_oid=frozen_notes,
    )


@dataclass(frozen=True, slots=True)
class ClaimAdmissionPreparation:
    """An exact frozen claim cohort, or typed reasons the judge must not run."""

    status: str
    contribution_oid: str
    authority_notes_ref_oid: str
    body: Mapping[str, Any] | None = None
    canonical: bytes = b""
    body_sha256: str = ""
    referee_goal_ids: tuple[str, ...] = ()
    rejections: tuple[Mapping[str, str], ...] = ()
    source_vector: FrozenAcceptanceSourceVector | None = None

    @property
    def admitted(self) -> bool:
        return self.status == "admitted"


def __getattr__(name: str) -> Any:
    return generated_schema_attr(name, _SCHEMA_BUILDERS)


def prepare_claim_admission(
    repo,
    contribution_ref: str,
    *,
    evaluated_at: str | None = None,
    frozen_contribution_oid: str | None = None,
    frozen_series_oid: str | None = None,
    frozen_authority_notes_oid: str | None = None,
    expected_contribution_branch: str | None = None,
    required_contribution_branch: str | None = None,
    frozen_source_vector: FrozenAcceptanceSourceVector | None = None,
) -> ClaimAdmissionPreparation:
    """Freeze and exactly admit all producer claims before referee invocation.

    Every evidence read is addressed through the one frozen contribution OID;
    callers may also pin the series and authority-notes tips to complete the
    source vector. Missing, surplus, stale, tampered, or cross-source evidence
    returns typed rejections; no registered admission body is emitted for an
    inexact cohort.
    """

    if frozen_source_vector is not None:
        mismatched_pin = (
            frozen_contribution_oid not in {None, frozen_source_vector.contribution_oid}
            or frozen_series_oid not in {None, frozen_source_vector.series_oid}
            or frozen_authority_notes_oid
            not in {None, frozen_source_vector.authority_notes_oid}
            or frozen_source_vector.authority_notes_ref
            != ACCEPTANCE_AUTHORITY_NOTES_REF
        )
        if mismatched_pin:
            return _rejected_admission(
                frozen_source_vector.contribution_oid,
                frozen_source_vector.authority_notes_oid,
                "source-mismatch",
                "frozen-acceptance-source-vector",
                "individual source pin differs from the frozen source vector",
                source_vector=frozen_source_vector,
            )
        contribution_oid = frozen_source_vector.contribution_oid
        frozen_series_oid = frozen_source_vector.series_oid
        notes_oid = frozen_source_vector.authority_notes_oid
        expected_contribution_branch = (
            expected_contribution_branch or frozen_source_vector.contribution_ref
        )
    else:
        contribution_oid = frozen_contribution_oid or git_wrapper.head_sha(repo, contribution_ref) or ""
        notes_oid = frozen_authority_notes_oid or (
            git_wrapper.head_sha(repo, ACCEPTANCE_AUTHORITY_NOTES_REF)
            or ("0" * len(contribution_oid) if contribution_oid else ZERO_OID)
        )
    if not contribution_oid:
        return _rejected_admission(
            contribution_oid,
            notes_oid,
            "source-mismatch",
            contribution_ref,
            "contribution ref is missing",
        )

    binding_raw = git_wrapper.show_file_bytes(
        repo, contribution_oid, producer_lifecycle.TASK_BINDING_PATH
    )
    assertion_raw = git_wrapper.show_file_bytes(
        repo, contribution_oid, producer_lifecycle.COMPLETION_ASSERTION_PATH
    )
    result_members = [
        (path, git_wrapper.show_file_bytes(repo, contribution_oid, path))
        for path in (contributor_handoff.RESULT_PATH, contributor_handoff.LEGACY_RESULT_PATH)
    ]
    present_results = [(path, raw) for path, raw in result_members if raw is not None]
    early: list[Mapping[str, str]] = []
    if binding_raw is None:
        early.append(_claim_rejection("missing-binding", producer_lifecycle.TASK_BINDING_PATH, "task binding is missing"))
    if assertion_raw is None:
        early.append(_claim_rejection("missing-assertion", producer_lifecycle.COMPLETION_ASSERTION_PATH, "completion assertion is missing"))
    if len(present_results) != 1:
        detail = "implementation result is missing" if not present_results else "canonical and legacy implementation results coexist"
        early.append(_claim_rejection("result-mismatch", "implementation-result", detail))
    if early:
        return ClaimAdmissionPreparation(
            "rejected",
            contribution_oid,
            notes_oid,
            rejections=tuple(early),
            source_vector=frozen_source_vector,
        )

    assert binding_raw is not None and assertion_raw is not None
    result_path, result_raw = present_results[0]
    assert result_raw is not None
    codec = BodyCodecClient()
    try:
        binding = patch_series_bodies.read_producer_task_binding(binding_raw, client=codec)
        assertion = patch_series_bodies.read_producer_completion_assertion(assertion_raw, client=codec)
        result_body = contributor_handoff.parse_result(result_raw, client=codec)
    except Exception as exc:
        return _rejected_admission(
            contribution_oid, notes_oid, "digest-mismatch", "producer-evidence", str(exc)
        )

    binding_sha = hashlib.sha256(binding_raw).hexdigest()
    assertion_sha = hashlib.sha256(assertion_raw).hexdigest()
    result_sha = hashlib.sha256(result_raw).hexdigest()
    binding_commit = git_wrapper.path_last_commit(
        repo, contribution_oid, producer_lifecycle.TASK_BINDING_PATH
    )
    assertion_commit = git_wrapper.path_last_commit(
        repo, contribution_oid, producer_lifecycle.COMPLETION_ASSERTION_PATH
    )
    rejections: list[Mapping[str, str]] = []
    try:
        required = producer_lifecycle.required_claim_set(binding)
    except ValueError as exc:
        required = ()
        rejections.append(_claim_rejection("missing-binding", producer_lifecycle.TASK_BINDING_PATH, str(exc)))

    source_fields = (
        "series_snapshot_oid",
        "canonical_root_body_sha256",
        "scope_decomposition_commit_oid",
        "scope_decomposition_body_sha256",
    )
    source_snapshot = {field: binding.get(field) for field in source_fields}
    bound_identity_fields = (
        *source_fields,
        "series_branch",
        "node_path",
        "contribution_branch",
        "producer",
    )
    if any(assertion.get(field) != binding.get(field) for field in bound_identity_fields):
        rejections.append(_claim_rejection("source-mismatch", producer_lifecycle.COMPLETION_ASSERTION_PATH, "assertion source vector differs from task binding"))
    series_branch = str(binding.get("series_branch") or "")
    # The root classifier owns the series-ref read when an anchored v2
    # acceptance route is used. Reuse that immutable OID so admission cannot
    # silently move to a later series tip between the gate and evidence reads.
    series_tip = (
        frozen_series_oid
        if frozen_series_oid is not None
        else git_wrapper.head_sha(repo, series_branch) if series_branch else None
    )
    if series_tip != binding.get("series_snapshot_oid"):
        rejections.append(_claim_rejection("source-mismatch", series_branch or "series_snapshot_oid", "series tip no longer matches the frozen producer source"))
    if (
        frozen_source_vector is not None
        and frozen_source_vector.series_ref.removeprefix("refs/heads/")
        != series_branch.removeprefix("refs/heads/")
    ):
        rejections.append(
            _claim_rejection(
                "source-mismatch",
                frozen_source_vector.series_ref,
                "frozen series coordinate differs from the task binding",
            )
        )
    normalized_contribution_ref = (
        required_contribution_branch
        or expected_contribution_branch
        or contribution_ref
    ).removeprefix("refs/heads/")
    exact_contribution_coordinate = (
        frozen_source_vector is not None
        or required_contribution_branch is not None
    )
    if (
        (exact_contribution_coordinate or normalized_contribution_ref.startswith("ai-org/contrib/"))
        and binding.get("contribution_branch") != normalized_contribution_ref
    ):
        rejections.append(_claim_rejection("source-mismatch", "contribution_branch", "task binding names a different contribution branch"))
    if assertion.get("task_binding_body_sha256") != binding_sha or assertion.get("task_binding_commit_oid") != binding_commit:
        rejections.append(_claim_rejection("digest-mismatch", producer_lifecycle.TASK_BINDING_PATH, "assertion does not bind the frozen task-binding bytes and commit"))
    if not assertion_commit:
        rejections.append(_claim_rejection("missing-assertion", producer_lifecycle.COMPLETION_ASSERTION_PATH, "assertion commit is missing"))
    implementation_oid = assertion.get("implementation_oid")
    if not isinstance(implementation_oid, str) or not git_wrapper.is_ancestor(repo, implementation_oid, contribution_oid):
        rejections.append(_claim_rejection("source-mismatch", "implementation_oid", "asserted implementation is not an ancestor of the frozen contribution"))

    result_commit = git_wrapper.path_last_commit(repo, contribution_oid, result_path)
    if result_commit != contribution_oid:
        rejections.append(_claim_rejection("result-mismatch", result_path, "implementation result is not the frozen contribution tip"))
    if (
        assertion_commit
        and result_commit
        and git_wrapper.parent_commits(repo, result_commit) != [assertion_commit]
    ):
        rejections.append(_claim_rejection("result-mismatch", result_path, "implementation result does not immediately follow the completion assertion"))
    if (
        assertion_commit
        and isinstance(implementation_oid, str)
        and git_wrapper.parent_commits(repo, assertion_commit) != [implementation_oid]
    ):
        rejections.append(_claim_rejection("source-mismatch", producer_lifecycle.COMPLETION_ASSERTION_PATH, "completion assertion does not immediately follow its asserted implementation"))

    expected_node_key = "root" if binding.get("node_path") == "." else str(binding.get("node_path") or "").rsplit("/", 1)[-1]
    if (
        result_body.get("patch_series_branch") != binding.get("series_branch")
        or result_body.get("node_key") != expected_node_key
    ):
        rejections.append(_claim_rejection("result-mismatch", result_path, "implementation result does not preserve the task-binding series/node source"))

    binding_rows = binding.get("promise_bindings")
    assertion_rows = assertion.get("promise_bindings")
    binding_by_obligation = _unique_rows(binding_rows, "obligation_id")
    if binding_by_obligation is None or set(binding_by_obligation) != set(required):
        rejections.append(_claim_rejection("missing-promise", "promise_bindings", "promise bindings are incomplete, surplus, or duplicated"))
        binding_by_obligation = {}
    if assertion_rows != binding_rows:
        rejections.append(_claim_rejection("source-mismatch", "assertion.promise_bindings", "assertion changed the frozen promise bindings"))
    bound_coordinates = {
        str(row.get("evidence_coordinate") or "")
        for row in binding_rows
        if isinstance(row, Mapping)
    } if isinstance(binding_rows, list) else set()
    current_coordinates = {
        path
        for path in git_wrapper.tree_files(repo, contribution_oid)
        if producer_lifecycle.is_evidence_path(path)
    }
    if current_coordinates != bound_coordinates:
        rejections.append(_claim_rejection("missing-promise", producer_lifecycle.EVIDENCE_DIRECTORY, "current promise cohort is incomplete or surplus"))

    assertion_rows_raw = assertion.get("assertions")
    assertion_by_obligation = _unique_rows(assertion_rows_raw, "obligation_id")
    if assertion_by_obligation is None or set(assertion_by_obligation) != set(required):
        rejections.append(_claim_rejection("missing-assertion", "assertions", "assertions are incomplete, surplus, or duplicated"))
        assertion_by_obligation = {}

    item_trace_by_obligation: dict[str, tuple[str, Mapping[str, Any]]] = {}
    if binding_commit and isinstance(implementation_oid, str):
        try:
            trace_by_item: dict[str, Mapping[str, Any]] = {}
            for row in producer_lifecycle.item_commit_trace(
                repo, binding_commit, implementation_oid, binding_sha
            ):
                item_id = str(row["item_id"])
                if item_id in trace_by_item:
                    raise ValueError(f"multiple item commits found for {item_id}")
                trace_by_item[item_id] = row
            required_by_item: dict[str, list[str]] = {}
            for task in binding.get("tasks", []):
                item_id = str(task["item_id"])
                obligation_id = str(task["production_obligation_ids"][0])
                required_by_item.setdefault(item_id, []).append(obligation_id)
            for item_id, obligation_ids in required_by_item.items():
                trace = trace_by_item.get(item_id)
                if trace is None or trace.get("production_obligation_ids") != obligation_ids:
                    raise ValueError(f"item commit trace does not exactly bind {item_id}")
                for obligation_id in obligation_ids:
                    item_trace_by_obligation[obligation_id] = (item_id, trace)
        except (KeyError, TypeError, ValueError) as exc:
            rejections.append(_claim_rejection("digest-mismatch", "item-commit-trace", str(exc)))

    for obligation_id in required:
        row = binding_by_obligation.get(obligation_id)
        if not isinstance(row, Mapping):
            continue
        coordinate = str(row.get("evidence_coordinate") or "")
        promise_commit = str(row.get("promise_commit_oid") or "")
        promised_raw = git_wrapper.show_file_bytes(repo, promise_commit, coordinate)
        current_raw = git_wrapper.show_file_bytes(repo, contribution_oid, coordinate)
        if promised_raw is None or hashlib.sha256(promised_raw).hexdigest() != row.get("promise_body_sha256"):
            rejections.append(_claim_rejection("digest-mismatch", coordinate or obligation_id, "promise binding bytes do not match their digest"))
            continue
        if current_raw is None:
            rejections.append(_claim_rejection("missing-promise", coordinate or obligation_id, "current promise is missing"))
            continue
        try:
            promised = patch_series_bodies.read_producer_promise(promised_raw, client=codec)
            current = patch_series_bodies.read_producer_promise(current_raw, client=codec)
        except Exception as exc:
            rejections.append(_claim_rejection("digest-mismatch", coordinate or obligation_id, str(exc)))
            continue
        expected_promise_source = {
            "series_snapshot_oid": binding.get("series_snapshot_oid"),
            "series_branch": binding.get("series_branch"),
            "node_path": binding.get("node_path"),
            "contribution_branch": binding.get("contribution_branch"),
            "canonical_root_body_sha256": binding.get("canonical_root_body_sha256"),
        }
        if obligation_id != promised.get("obligation_id") or any(promised.get(field) != value for field, value in expected_promise_source.items()):
            rejections.append(_claim_rejection("source-mismatch", coordinate, "promise source or obligation differs from the task binding"))
        if (
            current.get("obligation_id") != obligation_id
            or any(current.get(field) != value for field, value in expected_promise_source.items())
            or current.get("producer") != binding.get("producer")
            or promised.get("producer") != binding.get("producer")
        ):
            rejections.append(_claim_rejection("source-mismatch", coordinate, "current promise source, producer, or obligation changed"))
        immutable_promise_fields = (
            "series_snapshot_oid",
            "series_branch",
            "node_path",
            "contribution_branch",
            "canonical_root_body_sha256",
            "referee_goal_id",
            "obligation_id",
            "deliverable",
            "eligibility_predicate",
            "producer",
            "eligibility_decision",
            "eligibility_basis",
        )
        if any(current.get(field) != promised.get(field) for field in immutable_promise_fields):
            rejections.append(_claim_rejection("source-mismatch", coordinate, "current promise changed immutable admitted semantics"))
        commitment = current.get("commitment_slot")
        completion = current.get("completion_claim_slot")
        if not isinstance(commitment, Mapping) or commitment.get("task_binding_body_sha256") != binding_sha:
            rejections.append(_claim_rejection("digest-mismatch", coordinate, "current promise does not bind the task-binding digest"))
        if not isinstance(completion, Mapping) or completion.get("assertion_body_sha256") != assertion_sha:
            rejections.append(_claim_rejection("digest-mismatch", coordinate, "current promise does not bind the completion-assertion digest"))
        claim_row = assertion_by_obligation.get(obligation_id)
        item_trace = item_trace_by_obligation.get(obligation_id)
        if isinstance(claim_row, Mapping) and item_trace is not None:
            item_id, trace = item_trace
            try:
                expected_claim = producer_lifecycle.completion_claim(
                    obligation_id,
                    str(promised["referee_goal_id"]),
                    item_id,
                    producer_lifecycle.item_evidence(repo, str(trace["commit_oid"])),
                )
            except (KeyError, TypeError, ValueError) as exc:
                rejections.append(_claim_rejection("digest-mismatch", coordinate, str(exc)))
            else:
                if dict(claim_row) != expected_claim:
                    rejections.append(_claim_rejection("source-mismatch", f"assertions/{obligation_id}", "completion claim is not the exact Git-derived bound projection"))

    # Recheck only the mutable coordinates captured by this admission call.
    # Evidence bytes remain addressed through contribution_oid throughout;
    # these equality checks prevent a ref move during that work from allowing
    # the independent judge to run on an already-stale source vector.  The
    # publication transaction repeats both comparisons as the final CAS.
    if frozen_source_vector is not None:
        for coordinate in frozen_source_vector.stale_coordinates(repo):
            rejections.append(
                _claim_rejection(
                    "source-mismatch",
                    coordinate,
                    "acceptance source moved while claim admission was frozen",
                )
            )
    elif expected_contribution_branch is not None:
        live_contribution_oid = git_wrapper.head_sha(
            repo, expected_contribution_branch
        )
        if live_contribution_oid != contribution_oid:
            rejections.append(
                _claim_rejection(
                    "source-mismatch",
                    expected_contribution_branch,
                    "contribution tip moved while claim admission was frozen",
                )
            )
    if frozen_source_vector is None and frozen_authority_notes_oid is None:
        live_notes_oid = git_wrapper.head_sha(
            repo, ACCEPTANCE_AUTHORITY_NOTES_REF
        ) or ("0" * len(contribution_oid))
        if live_notes_oid != notes_oid:
            rejections.append(
                _claim_rejection(
                    "source-mismatch",
                    ACCEPTANCE_AUTHORITY_NOTES_REF,
                    "acceptance authority notes moved while claim admission was frozen",
                )
            )

    if rejections:
        return ClaimAdmissionPreparation(
            "rejected",
            contribution_oid,
            notes_oid,
            rejections=tuple(rejections),
            source_vector=frozen_source_vector,
        )

    admission_assertions = [
        {
            "obligation_id": obligation_id,
            "assertion_commit_oid": assertion_commit,
            "assertion_body_sha256": assertion_sha,
        }
        for obligation_id in required
    ]
    body = {
        "evaluated_contribution_oid": contribution_oid,
        "evaluated_at": evaluated_at or _utc_now(),
        "source_snapshot": source_snapshot,
        "authority_notes_ref_oid": notes_oid,
        "required_obligation_ids": list(required),
        "promise_bindings": list(binding_rows),
        "task_binding_body_sha256": binding_sha,
        "assertions": admission_assertions,
        "implementation_result_body_sha256": result_sha,
        "admitted": True,
    }
    prepared = codec.prepare(
        patch_series_bodies.CLAIM_ADMISSION_CONTEXT,
        body,
        expected=patch_series_bodies.CLAIM_ADMISSION_CONTRACT,
    )
    goals = tuple(str(assertion_by_obligation[item]["referee_goal_id"]) for item in required)
    return ClaimAdmissionPreparation(
        "admitted",
        contribution_oid,
        notes_oid,
        body,
        bytes(prepared.canonical.data),
        prepared.canonical.sha256,
        goals,
        source_vector=frozen_source_vector,
    )


def _unique_rows(value: Any, key: str) -> dict[str, Mapping[str, Any]] | None:
    if not isinstance(value, list):
        return None
    result: dict[str, Mapping[str, Any]] = {}
    for row in value:
        identity = row.get(key) if isinstance(row, Mapping) else None
        if not isinstance(identity, str) or not identity or identity in result:
            return None
        result[identity] = row
    return result


def _claim_rejection(kind: str, coordinate: str, detail: str) -> Mapping[str, str]:
    return {"type": kind, "coordinate": coordinate or "claim-admission", "detail": detail or "claim admission failed"}


def _rejected_admission(
    contribution_oid: str,
    notes_oid: str,
    kind: str,
    coordinate: str,
    detail: str,
    *,
    source_vector: FrozenAcceptanceSourceVector | None = None,
) -> ClaimAdmissionPreparation:
    return ClaimAdmissionPreparation(
        "rejected",
        contribution_oid,
        notes_oid,
        rejections=(_claim_rejection(kind, coordinate, detail),),
        source_vector=source_vector,
    )


def check(
    repo,
    branch: str,
    *,
    patch_series_branch: str | object | None = None,
    node_key: str | object | None = None,
    root_generation_snapshot: (
        patch_series_bodies.RootGenerationSnapshot | None
    ) = None,
) -> dict:
    """Judge whether a real user can reach the patch series goal with the contribution branch."""
    repo_path = Path(repo).resolve()
    temp_dir = Path(tempfile.mkdtemp(prefix="ai-org-functional-check-"))
    worktree = temp_dir / "worktree"
    schema_file = temp_dir / "verdict.schema.json"
    out_file = temp_dir / "verdict.json"
    branch_ref = _branch_ref(branch)
    original_sha = ""
    admission: ClaimAdmissionPreparation | None = None
    producer_protocol = False
    producer_gate_vetted = False
    frozen_series_oid: str | None = None
    frozen_source_vector: FrozenAcceptanceSourceVector | None = None

    try:
        original_sha = git_wrapper.head_sha(repo_path, branch) or ""
        if not original_sha:
            raise RuntimeError("contribution branch is missing")
        if isinstance(patch_series_branch, str) and patch_series_branch:
            root_snapshot = (
                root_generation_snapshot
                or patch_series_bodies.classify_root_generation(
                    repo_path, patch_series_branch
                )
            )
            frozen_series_oid = root_snapshot.frozen_oid
            if root_snapshot.has_generation_members and not root_snapshot.lifecycle_ready:
                return _verdict(
                    reachable=False,
                    blockers=[{
                        "where": patch_series_branch,
                        "why": f"{root_snapshot.disposition}: {root_snapshot.detail or root_snapshot.disposition}",
                    }],
                    notes="independent referee was not invoked",
                )
            producer_protocol = (
                root_snapshot.generation == patch_series_bodies.ROOT_GENERATION_V2
            )
            if producer_protocol:
                gate = patch_series_gate.vet_producer_pair_closure(
                    repo_path,
                    patch_series_branch,
                    "producer_functional_acceptance",
                    frozen_root_oid=frozen_series_oid,
                )
                if gate.blocked:
                    return producer_gate_rejection(gate)
                producer_gate_vetted = True
        else:
            # Direct historical callers have no root anchor. Preserve their
            # compatibility behavior while treating any producer lifecycle
            # member as a fail-closed claim-admission attempt.
            producer_protocol = any(
                git_wrapper.show_file_bytes(repo_path, original_sha, path) is not None
                for path in (
                    producer_lifecycle.TASK_BINDING_PATH,
                    producer_lifecycle.COMPLETION_ASSERTION_PATH,
                )
            )
        if producer_protocol and not producer_gate_vetted:
            # A producer lifecycle member on an unanchored contribution is a
            # partial migration, not an opt-in acceptance route.  Reject it at
            # the gate boundary before freezing sources or parsing claims.
            # Anchored v2 callers set producer_gate_vetted only after the
            # common frozen-root closure check succeeds above.
            result = _verdict(
                reachable=False,
                blockers=[{
                    "where": "functional_check",
                    "why": "producer_functional_acceptance_gate_closed: "
                    "a frozen patch-series root anchor is required",
                }],
                notes="independent referee was not invoked",
            )
            result["status"] = "producer_functional_acceptance_gate_closed"
            return result
        if producer_protocol:
            if (
                isinstance(patch_series_branch, str)
                and patch_series_branch
                and frozen_series_oid is not None
            ):
                frozen_source_vector = freeze_acceptance_source_vector(
                    repo_path,
                    branch,
                    patch_series_branch,
                    contribution_oid=original_sha,
                    series_oid=frozen_series_oid,
                )
            admission = prepare_claim_admission(
                repo_path,
                original_sha,
                frozen_contribution_oid=original_sha,
                frozen_series_oid=frozen_series_oid,
                expected_contribution_branch=branch,
                frozen_source_vector=frozen_source_vector,
            )
            if not admission.admitted:
                return _verdict(
                    reachable=False,
                    blockers=[
                        {
                            "where": str(rejection["coordinate"]),
                            "why": f"claim_admission_rejected:{rejection['type']}: {rejection['detail']}",
                        }
                        for rejection in admission.rejections
                    ],
                    notes="independent referee was not invoked",
                )
        # Git read is done by python. Codex receives only this detached checkout.
        _git(repo_path, "worktree", "add", "--detach", str(worktree), original_sha)
        _make_read_only(worktree)

        acknowledgement_rejection = _patchwork_check_acknowledgement_rejection(
            repo_path,
            worktree,
            patch_series_branch=patch_series_branch,
            node_key=node_key,
            frozen_patch_series_oid=(
                frozen_series_oid if producer_protocol else None
            ),
        )
        must_answer_rejection = _must_answer_questions_rejection(
            repo_path,
            worktree,
            patch_series_branch=patch_series_branch,
            frozen_patch_series_oid=(
                frozen_series_oid if producer_protocol else None
            ),
        )
        schema_file.write_text(json.dumps(build_verdict_schema(), indent=2), encoding="utf-8")
        completed = _run_codex(worktree, _prompt(), out_file, schema_file)
        verdict = _read_verdict(completed, out_file)
        if acknowledgement_rejection is not None:
            verdict = _verdict(
                reachable=False,
                blockers=[*verdict.get("blockers", []), acknowledgement_rejection],
                notes=verdict.get("notes", ""),
            )
        if must_answer_rejection is not None:
            verdict = _verdict(
                reachable=False,
                blockers=[*verdict.get("blockers", []), must_answer_rejection],
                notes=verdict.get("notes", ""),
            )

        if producer_protocol:
            assert admission is not None and admission.admitted
            published = _publish_registered_verdict(
                repo_path, branch_ref, admission, verdict
            )
            if not published.get("ok"):
                return _verdict(
                    reachable=False,
                    blockers=[{
                        "where": "functional_check",
                        "why": f"acceptance publication failed: {published.get('status', 'unknown')}",
                    }],
                    notes="",
                )
        else:
            # Historical branches retain their established unstructured marker.
            _make_writable(worktree)
            _commit_verdict(worktree, verdict)
            verdict_sha = _git(worktree, "rev-parse", "HEAD").strip()
            _git(repo_path, "update-ref", branch_ref, verdict_sha, original_sha)

        return verdict
    except Exception as exc:
        verdict = _verdict(
            reachable=False,
            blockers=[{"where": "functional_check", "why": f"acceptance failed: {exc}"}],
            notes="",
        )
        if not producer_protocol and original_sha and worktree.exists():
            try:
                _make_writable(worktree)
                _commit_verdict(worktree, verdict)
                verdict_sha = _git(worktree, "rev-parse", "HEAD").strip()
                _git(repo_path, "update-ref", branch_ref, verdict_sha, original_sha)
            except Exception:
                pass
        return verdict
    finally:
        if worktree.exists():
            _make_writable(worktree)
            _git_run(repo_path, "worktree", "remove", "--force", str(worktree))
        shutil.rmtree(temp_dir, ignore_errors=True)


def acceptance_pull(repo):
    """Judge one announced contribution whose current tip lacks a verdict."""
    for branch in sorted(git_wrapper.branches(repo, "ai-org/contrib/*")):
        if not producer_lifecycle.has_implementation_submission(repo, branch):
            continue
        announcement = announcements.find_for_contrib(repo, branch)
        if announcement is None:
            continue
        record = announcement["record"]
        root_snapshot = patch_series_bodies.classify_root_generation(
            repo, record["series_branch"]
        )
        if not root_snapshot.lifecycle_ready:
            return {
                "ok": False,
                "status": root_snapshot.disposition,
                "series_branch": record["series_branch"],
                "frozen_oid": root_snapshot.frozen_oid,
                "detail": root_snapshot.detail,
                "root_generation": root_snapshot.identity(),
                **root_snapshot.identity(),
            }
        if root_snapshot.generation == patch_series_bodies.ROOT_GENERATION_V2:
            decision = patch_series_gate.vet_producer_pair_closure(
                repo,
                record["series_branch"],
                "producer_functional_acceptance",
                frozen_root_oid=root_snapshot.frozen_oid,
            )
            if decision.blocked:
                return producer_gate_rejection(decision)
            if current_tip_has_terminal_verdict(repo, branch):
                continue
        else:
            subjects = git_wrapper.log_subjects(repo, branch)
            if subjects and subjects[0] in contributor_handoff.ACCEPTANCE_SUBJECTS:
                continue
        node_path = record["node_path"]
        check_kwargs = {
            "patch_series_branch": record["series_branch"],
            "node_key": "root" if node_path == "." else node_path.rsplit("/", 1)[-1],
        }
        # Preserve the historical injectable judge seam while the canonical
        # judge consumes the already-frozen classifier snapshot. This avoids a
        # second root read without forcing older external judges to add a
        # keyword they never needed.
        if "root_generation_snapshot" in inspect.signature(check).parameters:
            check_kwargs["root_generation_snapshot"] = root_snapshot
        return check(repo, branch, **check_kwargs)
    return None


def producer_gate_rejection(
    decision: patch_series_gate.AuthorabilityDecision,
) -> dict[str, Any]:
    """Preserve the common vet's typed coordinates at the acceptance boundary."""

    diagnostic = (
        decision.diagnostic.as_dict()
        if decision.diagnostic is not None
        else {
            "route": "producer_functional_acceptance",
            "frozen_root_oid": decision.source.frozen_root_oid,
            "cue_location": "root",
            "rule": "producer-functional-acceptance-gate-closed",
            "goal_id": "root",
            "obligation_id": "",
            "field": "",
            "detail": "producer functional acceptance gate is closed",
            "context": decision.source.context,
        }
    )
    result = _verdict(
        reachable=False,
        blockers=[{
            "where": diagnostic["cue_location"],
            "why": "producer_functional_acceptance_gate_closed: "
            + json.dumps(diagnostic, sort_keys=True),
        }],
        notes="independent referee was not invoked",
    )
    result.update(
        status="producer_functional_acceptance_gate_closed",
        authorability=decision.as_dict(),
    )
    return result


def _publish_registered_verdict(
    repo: Path,
    branch_ref: str,
    admission: ClaimAdmissionPreparation,
    verdict: Mapping[str, Any],
) -> dict[str, Any]:
    """Materialize an independent verdict and atomically seal its authority."""

    assert admission.body is not None and admission.admitted
    source_vector = admission.source_vector
    if source_vector is not None:
        stale_coordinates = source_vector.content_stale_coordinates(repo)
        if stale_coordinates:
            return {
                "ok": False,
                "status": "frozen_source_moved",
                "stale_coordinates": stale_coordinates,
                "prepared_object_oids": {},
                "resulting_ref_oids": {
                    branch_ref: git_wrapper.head_sha(repo, branch_ref),
                    ACCEPTANCE_AUTHORITY_NOTES_REF: git_wrapper.head_sha(
                        repo, ACCEPTANCE_AUTHORITY_NOTES_REF
                    ),
                },
            }
    issued_at = _utc_now()
    finding = "functional goal is reachable" if verdict["reachable"] else "functional goal is blocked"
    evidence = verdict.get("notes") or "independent source-grounded referee evaluation"
    verdict_body = {
        "evaluated_contribution_oid": admission.contribution_oid,
        "claim_admission_body_sha256": admission.body_sha256,
        "required_obligation_ids": list(admission.body["required_obligation_ids"]),
        "source_snapshot": dict(admission.body["source_snapshot"]),
        "authority_notes_ref_oid": admission.authority_notes_ref_oid,
        "referee_evaluations": [
            {
                "referee_goal_id": goal_id,
                "accepted": bool(verdict["reachable"]),
                "finding": finding,
                "evidence": str(evidence),
            }
            for goal_id in admission.referee_goal_ids
        ],
        "reachable": bool(verdict["reachable"]),
        "notes": str(verdict.get("notes") or "independent referee completed"),
        "issued_at": issued_at,
    }
    if verdict.get("blockers"):
        verdict_body["blockers"] = [
            f"{item.get('where', 'functional_check')}: {item.get('why', 'blocked')}"
            for item in verdict["blockers"]
        ]
    codec = BodyCodecClient()
    prepared_verdict = codec.prepare(
        patch_series_bodies.FUNCTIONAL_ACCEPTANCE_CONTEXT,
        verdict_body,
        expected=patch_series_bodies.FUNCTIONAL_ACCEPTANCE_CONTRACT,
    )
    subject = ACCEPTANCE_REACHABLE_SUBJECT if verdict["reachable"] else ACCEPTANCE_BLOCKED_SUBJECT
    created = git_wrapper.create_ref_with_files(
        repo,
        branch_ref,
        {
            CLAIM_ADMISSION_PATH: admission.canonical.decode("utf-8", errors="strict"),
            FUNCTIONAL_ACCEPTANCE_VERDICT_PATH: prepared_verdict.canonical.data.decode("utf-8", errors="strict"),
        },
        subject=subject,
        body=_blockers_body(list(verdict.get("blockers") or [])) if not verdict["reachable"] else "",
        parent=admission.contribution_oid,
        update_ref=False,
        inherit_parent_tree=True,
    )
    verdict_commit = created["commit"]
    seal_body = {
        "authority_contract_version": "acceptance-authority-v1",
        "verifier_role": "independent-functional-acceptance",
        "contribution_ref": branch_ref,
        "target_verdict_commit_oid": verdict_commit,
        "verdict_body_sha256": prepared_verdict.canonical.sha256,
        "claim_admission_body_sha256": admission.body_sha256,
        "source_snapshot": dict(admission.body["source_snapshot"]),
        "authority_notes_ref_oid": admission.authority_notes_ref_oid,
        "issued_at": issued_at,
    }
    prepared_seal = codec.prepare(
        patch_series_bodies.ACCEPTANCE_AUTHORITY_SEAL_CONTEXT,
        seal_body,
        expected=patch_series_bodies.ACCEPTANCE_AUTHORITY_SEAL_CONTRACT,
    )
    old_notes = (
        ""
        if admission.authority_notes_ref_oid
        and set(admission.authority_notes_ref_oid) == {"0"}
        else admission.authority_notes_ref_oid
    )
    notes_commit = git_wrapper.create_ref_with_files(
        repo,
        ACCEPTANCE_AUTHORITY_NOTES_REF,
        {verdict_commit: prepared_seal.canonical.data.decode("utf-8", errors="strict")},
        subject=f"acceptance authority: seal {verdict_commit}",
        parent=old_notes or None,
        update_ref=False,
        inherit_parent_tree=bool(old_notes),
    )["commit"]
    publication_updates = {
        branch_ref: (verdict_commit, admission.contribution_oid),
        ACCEPTANCE_AUTHORITY_NOTES_REF: (notes_commit, old_notes),
    }
    if source_vector is not None:
        if (
            _branch_ref(source_vector.contribution_ref) != branch_ref
            or source_vector.authority_notes_ref != ACCEPTANCE_AUTHORITY_NOTES_REF
        ):
            return {
                "ok": False,
                "status": "frozen_source_vector_mismatch",
                "verdict_commit": verdict_commit,
                "notes_commit": notes_commit,
            }
        series_ref = _branch_ref(source_vector.series_ref)
        if series_ref in publication_updates:
            raise ValueError("frozen series ref overlaps an acceptance publication ref")
        # The series ref is an input, not an output. A same-OID update verifies
        # its frozen OID in the same transaction, so a concurrent series move
        # rejects both verdict and authority-notes updates.
        publication_updates[series_ref] = (
            source_vector.series_oid,
            source_vector.series_oid,
        )
    publication = git_wrapper.update_refs_atomic(repo, publication_updates)
    resulting_ref_oids = {
        branch_ref: git_wrapper.head_sha(repo, branch_ref),
        ACCEPTANCE_AUTHORITY_NOTES_REF: git_wrapper.head_sha(
            repo, ACCEPTANCE_AUTHORITY_NOTES_REF
        ),
    }
    return {
        "ok": publication.ok,
        "status": "sealed" if publication.ok else "atomic_ref_cas_rejected",
        "verdict_commit": verdict_commit,
        "notes_commit": notes_commit,
        "prepared_object_oids": {
            "verdict_commit_oid": verdict_commit,
            "acceptance_authority_notes_commit_oid": notes_commit,
        },
        "resulting_ref_oids": resulting_ref_oids,
    }


def current_tip_accepted(repo, branch: str) -> bool:
    """Current-Tip Acceptance Predicate for one frozen branch/notes pair."""

    tip = git_wrapper.head_sha(repo, branch)
    notes_tip = git_wrapper.head_sha(repo, ACCEPTANCE_AUTHORITY_NOTES_REF)
    if tip is None or notes_tip is None:
        return False
    accepted = sealed_verdict_accepted(
        repo,
        tip,
        frozen_authority_notes_oid=notes_tip,
        expected_contribution_branch=branch,
    )
    return bool(
        accepted
        and git_wrapper.head_sha(repo, branch) == tip
        and git_wrapper.head_sha(repo, ACCEPTANCE_AUTHORITY_NOTES_REF) == notes_tip
    )


def sealed_verdict_accepted(
    repo,
    verdict_oid: str,
    *,
    frozen_authority_notes_oid: str | None = None,
    expected_contribution_branch: str | None = None,
    require_reachable: bool = True,
) -> bool:
    """Verify one durable verdict through its retained authority-seal link.

    Unlike :func:`current_tip_accepted`, this accepts an immutable verdict OID.
    Current-tip consumers also provide the contribution coordinate so copying
    the exact sealed commit to another contribution ref does not transfer its
    authority. Integration commits use the immutable verdict link after the
    contribution is no longer an ancestor of their code-only tree. Queue
    bookkeeping may set ``require_reachable=False`` to authenticate an
    independent blocked verdict; acceptance consumers retain the default and
    can only accept reachability.
    """

    notes_oid = frozen_authority_notes_oid or git_wrapper.head_sha(
        repo, ACCEPTANCE_AUTHORITY_NOTES_REF
    )
    if git_wrapper.head_sha(repo, verdict_oid) is None or notes_oid is None:
        return False
    seal_raw = git_wrapper.show_file_bytes(repo, notes_oid, verdict_oid)
    verdict_raw = git_wrapper.show_file_bytes(repo, verdict_oid, FUNCTIONAL_ACCEPTANCE_VERDICT_PATH)
    admission_raw = git_wrapper.show_file_bytes(repo, verdict_oid, CLAIM_ADMISSION_PATH)
    if seal_raw is None or verdict_raw is None or admission_raw is None:
        return False
    parents = git_wrapper.parent_commits(repo, verdict_oid)
    if len(parents) != 1:
        return False
    try:
        codec = BodyCodecClient()
        seal = patch_series_bodies.read_acceptance_authority_seal(seal_raw, client=codec)
        verdict = patch_series_bodies.read_functional_acceptance_verdict(verdict_raw, client=codec)
        admission = patch_series_bodies.read_claim_admission(admission_raw, client=codec)
        binding_raw = git_wrapper.show_file_bytes(
            repo,
            parents[0],
            producer_lifecycle.TASK_BINDING_PATH,
        )
        if binding_raw is None:
            return False
        binding = patch_series_bodies.read_producer_task_binding(
            binding_raw, client=codec
        )
    except Exception:
        return False
    bound_contribution_ref = _branch_ref(
        str(binding.get("contribution_branch") or "")
    )
    expected_contribution_ref = (
        _branch_ref(expected_contribution_branch)
        if expected_contribution_branch
        else bound_contribution_ref
    )
    sealed_contribution_ref = seal.get("contribution_ref")
    contribution_ref_is_authorized = (
        sealed_contribution_ref
        == bound_contribution_ref
        == expected_contribution_ref
        if sealed_contribution_ref is not None
        else expected_contribution_branch is None
    )
    source_snapshot = admission.get("source_snapshot")
    if not isinstance(source_snapshot, Mapping):
        return False
    try:
        rederived_admission = prepare_claim_admission(
            repo,
            parents[0],
            evaluated_at=str(admission.get("evaluated_at") or ""),
            frozen_contribution_oid=parents[0],
            frozen_series_oid=str(
                source_snapshot.get("series_snapshot_oid") or ""
            ),
            frozen_authority_notes_oid=str(
                admission.get("authority_notes_ref_oid") or ""
            ),
            required_contribution_branch=expected_contribution_branch,
        )
    except Exception:
        return False
    if (
        not rederived_admission.admitted
        or rederived_admission.canonical != admission_raw
    ):
        return False
    required_obligations = list(admission.get("required_obligation_ids") or [])
    referee_evaluations = list(verdict.get("referee_evaluations") or [])
    evaluated_goals = tuple(
        str(item.get("referee_goal_id") or "")
        for item in referee_evaluations
        if isinstance(item, Mapping)
    )
    if (
        list(verdict.get("required_obligation_ids") or [])
        != required_obligations
        or len(referee_evaluations) != len(required_obligations)
        or evaluated_goals != rederived_admission.referee_goal_ids
        or any(
            not isinstance(item, Mapping)
            or item.get("accepted") is not verdict.get("reachable")
            for item in referee_evaluations
        )
    ):
        return False
    seal_commit = git_wrapper.path_last_commit(repo, notes_oid, verdict_oid)
    if seal_commit is None:
        return False
    notes_parents = git_wrapper.parent_commits(repo, seal_commit)
    previous_notes = notes_parents[0] if notes_parents else "0" * len(verdict_oid)
    return bool(
        seal.get("target_verdict_commit_oid") == verdict_oid
        and contribution_ref_is_authorized
        and seal.get("verdict_body_sha256") == hashlib.sha256(verdict_raw).hexdigest()
        and seal.get("claim_admission_body_sha256") == hashlib.sha256(admission_raw).hexdigest()
        and seal.get("claim_admission_body_sha256") == verdict.get("claim_admission_body_sha256")
        and seal.get("source_snapshot") == verdict.get("source_snapshot") == admission.get("source_snapshot")
        and seal.get("authority_notes_ref_oid") == previous_notes
        and verdict.get("authority_notes_ref_oid") == previous_notes
        and admission.get("authority_notes_ref_oid") == previous_notes
        and admission.get("evaluated_contribution_oid") == parents[0]
        and verdict.get("evaluated_contribution_oid") == parents[0]
        and admission.get("admitted") is True
        and (
            verdict.get("reachable") is True
            if require_reachable
            else isinstance(verdict.get("reachable"), bool)
        )
    )


def current_tip_has_registered_verdict(repo, branch: str) -> bool:
    """Return whether the current tip is an independently materialized verdict.

    This is queue bookkeeping, not acceptance authority.  A reachable verdict
    still requires :func:`current_tip_accepted` and its protected-ref seal.
    """

    tip = git_wrapper.head_sha(repo, branch)
    return bool(
        tip is not None
        and _registered_verdict_at(repo, tip) is not None
        and git_wrapper.head_sha(repo, branch) == tip
    )


def current_tip_has_terminal_verdict(repo, branch: str) -> bool:
    """Queue predicate: every terminal independent verdict requires a seal.

    Contribution commits can contain codec-valid verdict bodies, so structural
    registration alone cannot close a reachable acceptance route.  Freeze the
    branch and authority-notes tips together, require the protected seal, then
    confirm neither coordinate moved during verification.
    """

    tip = git_wrapper.head_sha(repo, branch)
    notes_tip = git_wrapper.head_sha(repo, ACCEPTANCE_AUTHORITY_NOTES_REF)
    if tip is None:
        return False
    registered = _registered_verdict_at(repo, tip)
    if registered is None:
        return False
    if notes_tip is None:
        return False
    authenticated = sealed_verdict_accepted(
        repo,
        tip,
        frozen_authority_notes_oid=notes_tip,
        expected_contribution_branch=branch,
        require_reachable=False,
    )
    return bool(
        authenticated
        and git_wrapper.head_sha(repo, branch) == tip
        and git_wrapper.head_sha(repo, ACCEPTANCE_AUTHORITY_NOTES_REF) == notes_tip
    )


def _registered_verdict_at(
    repo, tip: str
) -> tuple[Mapping[str, Any], Mapping[str, Any]] | None:
    """Read the admission/verdict pair only through one immutable tip."""

    verdict_raw = git_wrapper.show_file_bytes(repo, tip, FUNCTIONAL_ACCEPTANCE_VERDICT_PATH)
    admission_raw = git_wrapper.show_file_bytes(repo, tip, CLAIM_ADMISSION_PATH)
    if verdict_raw is None or admission_raw is None:
        return None
    try:
        codec = BodyCodecClient()
        verdict = patch_series_bodies.read_functional_acceptance_verdict(verdict_raw, client=codec)
        admission = patch_series_bodies.read_claim_admission(admission_raw, client=codec)
    except Exception:
        return None
    parents = git_wrapper.parent_commits(repo, tip)
    if not (
        len(parents) == 1
        and admission.get("admitted") is True
        and admission.get("evaluated_contribution_oid") == parents[0]
        and verdict.get("evaluated_contribution_oid") == parents[0]
        and verdict.get("claim_admission_body_sha256") == hashlib.sha256(admission_raw).hexdigest()
        and verdict.get("source_snapshot") == admission.get("source_snapshot")
    ):
        return None
    return verdict, admission


def _run_codex(
    worktree: Path,
    prompt: str,
    out_file: Path,
    schema_file: Path,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "codex",
            "exec",
            "--sandbox",
            "read-only",
            "-C",
            str(worktree),
            "-o",
            str(out_file),
            "--output-schema",
            str(schema_file),
            prompt,
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
    )


def _prompt() -> str:
    return (
        "You are the patch acceptance judge for this contribution branch.\n"
        "Decide whether a real USER can reach the patch series goal with the code in this checkout.\n\n"
        "Hard rules:\n"
        "- Read files only; do not edit, commit, launch the app, run tests, install packages, or run build tools.\n"
        "- Infer the patch series goal from committed project/patch series/task files in this checkout.\n"
        "- Use the Mona two-agent walkthrough idea: USER keeps trying to reach the goal; APP answers only "
        "from source-grounded facts.\n"
        "- Passing tests alone is not enough. Reject false success where the code claims success but a real "
        "user cannot complete the goal.\n"
        "- If blocked, list concrete blockers with where as file:line when possible and why as the user-visible "
        "reason.\n\n"
        "Return only JSON matching the provided schema."
    )


def _read_verdict(completed: subprocess.CompletedProcess[str], out_file: Path) -> dict:
    # PROVEN: codex can exit non-zero or write no -o file. Check both before read_text; fail closed.
    if completed.returncode != 0:
        return _verdict(
            reachable=False,
            blockers=[{"where": "functional_check", "why": "codex acceptance judge exited non-zero"}],
            notes=(completed.stderr or completed.stdout or "").strip(),
        )
    if not out_file.exists():
        return _verdict(
            reachable=False,
            blockers=[{"where": "functional_check", "why": "codex acceptance judge wrote no output file"}],
            notes=(completed.stderr or completed.stdout or "").strip(),
        )

    raw = out_file.read_text(encoding="utf-8")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return _verdict(
            reachable=False,
            blockers=[{"where": "functional_check", "why": "codex acceptance judge returned invalid JSON"}],
            notes=raw,
        )
    return _normalize_verdict(parsed, raw)


def _normalize_verdict(parsed: Any, raw: str) -> dict:
    if not isinstance(parsed, dict):
        return _invalid_shape(raw)
    if set(parsed) != {"reachable", "blockers", "notes"}:
        return _invalid_shape(raw)
    if not isinstance(parsed["reachable"], bool):
        return _invalid_shape(raw)
    if not isinstance(parsed["blockers"], list) or not isinstance(parsed["notes"], str):
        return _invalid_shape(raw)

    blockers = []
    for item in parsed["blockers"]:
        if not isinstance(item, dict) or set(item) != {"where", "why"}:
            return _invalid_shape(raw)
        if not isinstance(item["where"], str) or not isinstance(item["why"], str):
            return _invalid_shape(raw)
        blockers.append({"where": item["where"], "why": item["why"]})

    return _verdict(reachable=parsed["reachable"], blockers=blockers, notes=parsed["notes"])


def _invalid_shape(raw: str) -> dict:
    return _verdict(
        reachable=False,
        blockers=[{"where": "functional_check", "why": "codex verdict did not match the schema"}],
        notes=raw,
    )


def _verdict(*, reachable: bool, blockers: list[dict], notes: str) -> dict:
    return {"ok": reachable, "reachable": reachable, "blockers": blockers, "notes": notes}


def _patchwork_check_acknowledgement_rejection(
    repo: Path,
    worktree: Path,
    *,
    patch_series_branch: str | object | None,
    node_key: str | object | None,
    frozen_patch_series_oid: str | None = None,
) -> dict[str, str] | None:
    if patch_series_branch is NO_PATCHWORK_ANCHOR and node_key is NO_PATCHWORK_ANCHOR:
        return None
    if not patch_series_branch or not node_key:
        return {
            "where": "functional_check",
            "why": 'patchwork_check_acknowledgement_mismatch: {"type": "anchor_missing"}',
        }
    if not isinstance(patch_series_branch, str) or not isinstance(node_key, str):
        return {
            "where": "functional_check",
            "why": 'patchwork_check_acknowledgement_mismatch: {"type": "anchor_missing"}',
        }
    try:
        parsed_result = contributor_handoff.read_checkout_member(
            worktree,
            contributor_handoff.RESULT_PATH,
            contributor_handoff.LEGACY_RESULT_PATH,
            contributor_handoff.validated_functional_carrier,
        )
    except Exception as exc:
        detail = str(exc)
        if "rule=utf8" in detail:
            detail = "invalid UTF-8"
        return _patchwork_check_acknowledgement_blocker(detail)
    if parsed_result is None:
        return _patchwork_check_acknowledgement_blocker("implementation-result.json is missing")
    parsed = parsed_result.result
    if not isinstance(parsed, Mapping):
        return _patchwork_check_acknowledgement_blocker("implementation-result.json must contain an object")
    declared_patch_series_branch = parsed.get("patch_series_branch")
    declared_node_key = parsed.get("node_key")
    acknowledged = parsed.get("acknowledged_patchwork_checks")
    if not isinstance(declared_patch_series_branch, str) or not declared_patch_series_branch:
        return _patchwork_check_acknowledgement_blocker("patch_series_branch is missing")
    if not isinstance(declared_node_key, str) or not declared_node_key:
        return _patchwork_check_acknowledgement_blocker("node_key is missing")
    if not isinstance(acknowledged, Mapping):
        return _patchwork_check_acknowledgement_blocker("acknowledged_patchwork_checks is missing")
    if declared_patch_series_branch != patch_series_branch or declared_node_key != node_key:
        details = {
            "type": "anchor_mismatch",
            "expected": {"patch_series_branch": patch_series_branch, "node_key": node_key},
            "actual": {"patch_series_branch": declared_patch_series_branch, "node_key": declared_node_key},
        }
        return _patchwork_check_acknowledgement_blocker(json.dumps(details, sort_keys=True))

    facts = patch_series_gate.computed_patchwork_check_facts(
        repo, frozen_patch_series_oid or patch_series_branch, node_key
    )
    if isinstance(facts, Mapping) and facts.get("ok") is False:
        return _patchwork_check_acknowledgement_blocker(json.dumps(facts, sort_keys=True))
    expected = {
        name: item.get("current_value")
        for name, item in facts.get("checks", {}).items()
        if isinstance(item, Mapping)
    }
    actual = dict(acknowledged)
    if set(actual) != set(expected) or any(not _strictly_equal_patchwork_value(actual[name], expected[name]) for name in expected if name in actual):
        missing = sorted(name for name in expected if name not in actual)
        mismatched = sorted(name for name in expected if name in actual and not _strictly_equal_patchwork_value(actual[name], expected[name]))
        extra = sorted(name for name in actual if name not in expected)
        details = {
            "missing": missing,
            "mismatched": mismatched,
            "extra": extra,
            "expected": expected,
            "actual": actual,
        }
        return _patchwork_check_acknowledgement_blocker(json.dumps(details, sort_keys=True))
    return None


def _must_answer_questions_rejection(
    repo: Path,
    worktree: Path,
    *,
    patch_series_branch: str | None,
    frozen_patch_series_oid: str | None = None,
) -> dict[str, Any] | None:
    """Brief 27 teeth (the anti-shiozuke core): deferred questions come back HERE.

    When the series branch carries questions-the-patch-must-answer.json, the
    implementation-result MUST carry an outcome per question — missing or
    unanswered fails closed. This is the structural backstop; the code worker's
    submission-side gate is the early discovery point. Vocabulary is
    obligation-form throughout (must_answer_*): the artifact's primary reader is
    the patch author, and "deferred" prompts deprioritization (requester ruling).
    """
    if not isinstance(patch_series_branch, str) or not patch_series_branch:
        return None
    from ai_org import git_wrapper

    source_ref = frozen_patch_series_oid or patch_series_branch
    raw = git_wrapper.show_file(
        repo, source_ref, patch_series_gate.PATCH_MUST_ANSWER_QUESTIONS_PATH
    )
    legacy_raw = git_wrapper.show_file(
        repo, source_ref, patch_series_gate.LEGACY_PATCH_MUST_ANSWER_QUESTIONS_PATH
    )
    if raw is not None and legacy_raw is not None:
        return _must_answer_blocker(
            "questions-the-patch-must-answer has both canonical and legacy representations"
        )
    raw = raw if raw is not None else legacy_raw
    if raw is None:
        return None
    try:
        artifact = contributor_handoff.parse_questions(raw)
    except Exception:
        return _must_answer_blocker("questions-the-patch-must-answer.json on the series branch is unparseable")
    questions = artifact.get("questions_the_patch_must_answer") if isinstance(artifact, Mapping) else None
    if not isinstance(questions, list) or not questions:
        return None
    try:
        parsed_result = contributor_handoff.read_checkout_member(
            worktree,
            contributor_handoff.RESULT_PATH,
            contributor_handoff.LEGACY_RESULT_PATH,
            contributor_handoff.validated_functional_carrier,
        )
    except Exception as exc:
        return _must_answer_blocker(f"implementation-result.json unreadable: {exc}")
    if parsed_result is None:
        return _must_answer_blocker("implementation-result.json is missing")
    parsed = parsed_result.result
    outcomes = parsed.get("must_answer_outcomes") if isinstance(parsed, Mapping) else None
    unanswered: list[dict[str, str]] = []
    for question in questions:
        if not isinstance(question, Mapping):
            continue
        objection_id = str(question.get("objection_id") or "")
        outcome = outcomes.get(objection_id) if isinstance(outcomes, Mapping) else None
        if not isinstance(outcome, Mapping) or not str(outcome.get("outcome") or "").strip():
            unanswered.append(
                {"objection_id": objection_id, "executable_check": str(question.get("executable_check") or "")}
            )
    if unanswered:
        return _must_answer_blocker(
            "must_answer_question_unanswered: " + json.dumps(unanswered, sort_keys=True, ensure_ascii=True)
        )
    question_ids = [
        str(question.get("objection_id") or "")
        for question in questions
        if isinstance(question, Mapping)
    ]
    _, unexpected = contributor_handoff.outcome_key_mismatches(parsed, question_ids)
    if unexpected:
        return _must_answer_blocker(
            "must_answer_outcome_unexpected: " + ", ".join(unexpected)
        )
    return None


def _must_answer_blocker(message: str) -> dict[str, str]:
    return {"where": "implementation-result.json", "why": message}


def _strictly_equal_patchwork_value(actual: Any, expected: Any) -> bool:
    return type(actual) is type(expected) and actual == expected


def _patchwork_check_acknowledgement_blocker(message: str) -> dict[str, Any]:
    blocker: dict[str, Any] = {
        "where": "implementation-result.json",
        "why": f"patchwork_check_acknowledgement_mismatch: {message}",
    }
    # Memento: fix-it hint on a typed gate error without auto-repair wiring. The error
    # type token is known statically right here, so matching needs zero string parsing
    # and zero prose interpretation. The hint is additive and non-binding: the blocker
    # verdict is identical with or without it.
    matched = structure_gadgets.match_gadget_hints(
        {"gate_error_type": "patchwork_check_acknowledgement_mismatch"}
    )
    if matched.get("ok") and matched["hints"]:
        blocker["gadget_hint"] = matched["hints"][0]
    return blocker


def _commit_verdict(worktree: Path, verdict: dict) -> None:
    # Verdict markers are engine-authored (the independent judge), so they carry
    # the explicit engine identity — never ambient config, never the contributor's.
    marker = contributor_handoff.acceptance_marker(
        verdict["reachable"], git_wrapper.engine_identity()
    )
    identity = git_wrapper.identity_config_args(marker.identity)
    if verdict["reachable"]:
        _git(worktree, *identity, "commit", "--allow-empty", "-m", marker.subject)
        return

    body = _blockers_body(verdict["blockers"])
    _git(worktree, *identity, "commit", "--allow-empty", "-m", marker.subject, "-m", body)


def _blockers_body(blockers: list[dict]) -> str:
    if not blockers:
        return "functional_check: acceptance blocked without a specific blocker"
    return "\n".join(f"{item['where']}: {item['why']}" for item in blockers)


def _branch_ref(branch: str) -> str:
    if branch.startswith("refs/"):
        return branch
    return f"refs/heads/{branch}"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


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


def _make_read_only(path: Path) -> None:
    for root, dirs, files in os.walk(path):
        for name in files:
            _chmod_read_only(Path(root) / name)
        for name in dirs:
            _chmod_read_only(Path(root) / name)
    _chmod_read_only(path)


def _make_writable(path: Path) -> None:
    for root, dirs, files in os.walk(path):
        for name in dirs:
            _chmod_writable(Path(root) / name)
        for name in files:
            _chmod_writable(Path(root) / name)
    _chmod_writable(path)


def _chmod_read_only(path: Path) -> None:
    if path.is_symlink():
        return
    mode = path.stat().st_mode
    if path.is_dir():
        path.chmod((mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH) & ~0o222)
    else:
        path.chmod(mode & ~0o222)


def _chmod_writable(path: Path) -> None:
    if path.is_symlink():
        return
    path.chmod(path.stat().st_mode | stat.S_IWUSR)
