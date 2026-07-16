"""Branch-local producer promises behind the canonical readiness decision.

The lifecycle is non-exclusive: its coordinates include the producing family,
and no read in this module inspects a competing family for admission or scope
ownership.  A promise-only contribution ref is evidence of intent, not an
implementation submission.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import importlib
import json
from pathlib import PurePosixPath
import re
from typing import Any, Mapping

from ai_org import git_wrapper, patch_series_bodies
from ai_org.body_codec import BodyCodecClient


EVIDENCE_DIRECTORY = "producer-evidence"
PROMISE_BASENAME = "producer-promise.cue"
EVIDENCE_COORDINATE_VERSION = "producer-evidence-coordinate-v1"
TASK_BINDING_PATH = "producer-task-binding.cue"
COMPLETION_ASSERTION_PATH = "producer-completion-assertion.cue"
PROMISE_READINESS_ROUTE = "producer_promise_initialization"
TASK_BINDING_ROUTE = "producer_task_binding"
CODE_AUTHORING_ROUTE = "producer_code_authoring"
FEEDBACK_AUTHORING_ROUTE = "producer_feedback_authoring"
AUTHORING_ROUTES = frozenset({CODE_AUTHORING_ROUTE, FEEDBACK_AUTHORING_ROUTE})
INITIAL_SUBJECT_PREFIX = "producer-promise: initialize"
TRANSITION_SUBJECT_PREFIX = "producer-promise: transition"
TASK_BINDING_SUBJECT_PREFIX = "producer-task-binding: materialize"
COMPLETION_ASSERTION_SUBJECT_PREFIX = "producer-completion: assert"
TASK_BINDING_DIGEST_TRAILER = "Producer-Task-Binding-SHA256"
TASK_BINDING_COMMIT_OID_TRAILER = "Producer-Task-Binding-Commit-OID"
OBLIGATION_ID_TRAILER = "Production-Obligation-ID"
ITEM_TRACE_TRAILER_KEYS = frozenset(
    {TASK_BINDING_DIGEST_TRAILER, OBLIGATION_ID_TRAILER}
)
_TRAILER_LINE = re.compile(r"^(?P<key>[A-Za-z0-9-]+):[ \t]*(?P<value>.*)$")


@dataclass(frozen=True, slots=True)
class PromiseInitialization:
    status: str
    files: Mapping[str, str]
    obligation_coordinates: Mapping[str, str]
    decision: Any = None
    detail: str = ""

    @property
    def active(self) -> bool:
        return self.status == "ready"


def validate_initialization_cohort(initialization: PromiseInitialization) -> None:
    """Validate the exact single-family promise set before atomic publication.

    ``prepare_initialization`` constructs this projection, but the announcement
    writer is the publication boundary and must independently reject a partial,
    aliased, or cross-family projection.  This keeps every obligation on one
    distinct creation-sealed coordinate before either initial ref is visible.
    """

    if not initialization.active:
        raise ValueError("producer promise initialization is not ready")
    files = initialization.files
    coordinates = initialization.obligation_coordinates
    if not files or not coordinates:
        raise ValueError("producer promise initialization cohort is empty")
    if any(not isinstance(item, str) or not item for item in coordinates):
        raise ValueError("producer promise obligation id is invalid")
    paths = tuple(coordinates.values())
    if any(
        not isinstance(path, str) or not is_evidence_path(path)
        for path in paths
    ):
        raise ValueError("producer promise evidence coordinate is invalid")
    if len(set(paths)) != len(paths):
        raise ValueError("producer promise obligation coordinates are not distinct")
    if set(paths) != set(files):
        raise ValueError("producer promise files do not match obligation coordinates")

    codec = BodyCodecClient()
    family: tuple[str, ...] | None = None
    for obligation_id, path in coordinates.items():
        body = patch_series_bodies.read_producer_promise(files[path], client=codec)
        _validate_initial_promise_body(body, path, obligation_id)
        producer = body["producer"]
        current_family = (
            str(body["series_snapshot_oid"]),
            str(body["series_branch"]),
            str(body["node_path"]),
            str(body["contribution_branch"]),
            str(body["canonical_root_body_sha256"]),
            str(producer["name"]),
            str(producer["email"]).strip().lower(),
        )
        if family is None:
            family = current_family
        elif family != current_family:
            raise ValueError("producer promise initialization crosses families")
        if body["obligation_id"] != obligation_id:
            raise ValueError(
                f"producer promise obligation coordinate mismatch for {obligation_id}"
            )


def _validate_initial_promise_body(
    body: Mapping[str, Any], path: str, obligation_id: str
) -> None:
    """Reject lifecycle state that is not an initial eligible promise."""

    if body.get("eligibility_decision") != "eligible":
        raise ValueError(f"initial producer promise is ineligible at {path}")
    if body.get("commitment_slot"):
        raise ValueError(f"initial producer promise commitment slot is already bound at {path}")
    if body.get("completion_claim_slot"):
        raise ValueError(
            f"initial producer promise completion claim slot is already bound at {path}"
        )
    if body.get("previous_promise_commit_oid") is not None:
        raise ValueError(f"initial producer promise has transition history at {path}")
    producer = body.get("producer")
    if not isinstance(producer, Mapping):
        raise ValueError(f"initial producer promise identity is invalid at {path}")
    expected = evidence_coordinate(
        str(body.get("series_branch")),
        str(body.get("node_path")),
        str(body.get("contribution_branch")),
        producer,
        str(body.get("canonical_root_body_sha256")),
        obligation_id,
    )
    if path != expected:
        raise ValueError(f"producer promise evidence coordinate mismatch at {path}")


@dataclass(frozen=True, slots=True)
class PreparedTaskBinding:
    """Exact plural assignment and the promise updates committed with it."""

    status: str
    files: Mapping[str, str]
    body: Mapping[str, Any]
    body_sha256: str = ""
    obligation_ids_by_item: Mapping[str, tuple[str, ...]] | None = None
    decision: Any = None
    detail: str = ""

    @property
    def active(self) -> bool:
        return self.status == "ready"


@dataclass(frozen=True, slots=True)
class PreparedCompletionAssertion:
    files: Mapping[str, str]
    body: Mapping[str, Any]
    body_sha256: str
    implementation_oid: str


def completion_claim(
    obligation_id: str,
    referee_goal_id: str,
    item_id: str,
    evidence: Mapping[str, str],
) -> dict[str, Any]:
    """Return the sole admitted claim projection for one bound item edge."""

    return {
        "obligation_id": obligation_id,
        "referee_goal_id": referee_goal_id,
        "claim": f"completed production obligation {obligation_id}",
        "means": f"Git-derived item commit trace for {item_id}",
        "evidence": [dict(evidence)],
    }


def required_claim_set(binding: Mapping[str, Any]) -> tuple[str, ...]:
    """Derive the exact, duplicate-free obligation set from plural tasks."""

    tasks = binding.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("producer task binding tasks are missing")
    obligations: list[str] = []
    for task in tasks:
        values = task.get("production_obligation_ids") if isinstance(task, Mapping) else None
        if not isinstance(values, list) or len(values) != 1 or not isinstance(values[0], str) or not values[0]:
            raise ValueError("producer task binding must contain one obligation per task edge")
        obligations.append(values[0])
    if len(obligations) != len(set(obligations)):
        raise ValueError("producer task binding obligation edges are not unique")
    return tuple(obligations)


def validate_promise_bindings(
    binding: Mapping[str, Any],
) -> tuple[Mapping[str, Any], ...]:
    """Validate one exact Promise Binding row per required obligation.

    CUE owns the row shape, while this cross-row check owns cohort exactness.
    Keeping the original row order preserves the frozen binding body when it
    is copied into the later completion assertion.
    """

    required = required_claim_set(binding)
    rows = binding.get("promise_bindings")
    if not isinstance(rows, list) or not rows:
        raise ValueError("producer task binding promise bindings are missing")
    obligation_ids: list[str] = []
    normalized: list[Mapping[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError("producer task binding promise binding is not an object")
        obligation_id = row.get("obligation_id")
        if not isinstance(obligation_id, str) or not obligation_id:
            raise ValueError("producer task binding promise obligation id is invalid")
        obligation_ids.append(obligation_id)
        normalized.append(row)
    if len(obligation_ids) != len(set(obligation_ids)):
        raise ValueError("producer task binding promise obligations are not unique")
    if set(obligation_ids) != set(required):
        raise ValueError(
            "producer task binding promise bindings do not cover the required claim set"
        )
    return tuple(normalized)


def validate_task_binding_source(
    binding: Mapping[str, Any],
    record: Mapping[str, Any],
    *,
    series_snapshot_oid: str,
    readiness_decision: Any,
    expected_readiness_route: str,
) -> None:
    """Bind a materialized task cohort to the exact authoring gate input.

    Initial authoring creates this body from the frozen source vector, while
    feedback reads it back from Git. Both paths use this same comparison so a
    schema-valid binding copied from another producer family, or one retaining
    stale root/scope digests, cannot reach code dispatch.
    """

    if expected_readiness_route not in AUTHORING_ROUTES | {TASK_BINDING_ROUTE}:
        raise ValueError("producer readiness decision route is not a binding route")
    source = (
        readiness_decision.get("source")
        if isinstance(readiness_decision, Mapping)
        else getattr(readiness_decision, "source", None)
    )
    if source is None:
        raise ValueError("producer readiness decision source is missing")

    def source_value(field: str) -> Any:
        return (
            source.get(field)
            if isinstance(source, Mapping)
            else getattr(source, field, None)
        )

    expected_binding = {
        "series_branch": str(record["series_branch"]),
        "node_path": str(record["node_path"]),
        "contribution_branch": str(record["contrib_branch"]),
        "series_snapshot_oid": series_snapshot_oid,
        "producer": dict(record["author"]),
        "canonical_root_body_sha256": source_value("root_sha256"),
        "scope_decomposition_commit_oid": series_snapshot_oid,
        "scope_decomposition_body_sha256": source_value(
            "scope_decomposition_sha256"
        ),
    }
    expected_decision_source = {
        "route": expected_readiness_route,
        "branch": str(record["series_branch"]),
        "frozen_root_oid": series_snapshot_oid,
    }
    for field, expected in expected_decision_source.items():
        if not expected or source_value(field) != expected:
            raise ValueError(
                f"producer readiness decision does not match the gated {field}"
            )
    for field, expected in expected_binding.items():
        if expected in (None, "") or binding.get(field) != expected:
            raise ValueError(
                f"producer task binding does not match the gated {field}"
            )


def item_binding_trailers(
    binding_body_sha256: str,
    obligation_ids: Any,
) -> tuple[str, ...]:
    """Return the sole trace trailers allowed on a bound item commit.

    The binding commit OID is intentionally absent: Git derives the item OID
    after materialization, and completion assertions carry the separately
    derived binding commit OID.  Keeping both authoring paths on this helper
    prevents their trailer policies from drifting.
    """

    if not isinstance(binding_body_sha256, str) or re.fullmatch(
        r"[0-9a-f]{64}", binding_body_sha256
    ) is None:
        raise ValueError("item binding digest must be a lowercase SHA-256")
    if not isinstance(obligation_ids, (list, tuple)):
        raise ValueError("item binding obligation ids must be a list or tuple")
    normalized = tuple(obligation_ids)
    if not normalized:
        raise ValueError("item binding obligation ids are missing")
    if any(not isinstance(item, str) or not item for item in normalized):
        raise ValueError("item binding obligation ids must be non-empty strings")
    if len(normalized) != len(set(normalized)):
        raise ValueError("item binding obligation ids must be unique")
    return (
        f"{TASK_BINDING_DIGEST_TRAILER}: {binding_body_sha256}",
        *(f"{OBLIGATION_ID_TRAILER}: {item}" for item in normalized),
    )


def _message_trailers(message: str) -> tuple[tuple[str, str], ...]:
    """Parse the final, contiguous Git trailer block from a commit message.

    Trace markers appearing in the subject or explanatory prose are not
    trailers and therefore cannot make an otherwise unrelated commit part of
    the item trace. The authoring helpers emit single-line values, so folded
    continuation lines are intentionally outside this compact protocol.
    """

    lines = message.rstrip("\n").splitlines()
    parsed: list[tuple[str, str]] = []
    cursor = len(lines) - 1
    while cursor >= 0:
        match = _TRAILER_LINE.fullmatch(lines[cursor])
        if match is None:
            break
        parsed.append((match.group("key"), match.group("value").strip()))
        cursor -= 1
    if not parsed or (cursor >= 0 and lines[cursor].strip()):
        return ()
    parsed.reverse()
    return tuple(parsed)


def item_commit_trace(
    repo,
    binding_commit_oid: str,
    implementation_ref: str,
    binding_body_sha256: str,
) -> list[dict[str, Any]]:
    """Derive item commit OIDs from Git and validate their minimal trailers."""

    traces: list[dict[str, Any]] = []
    for commit in git_wrapper.first_parent_commits_between(
        repo, binding_commit_oid, implementation_ref
    ):
        message = git_wrapper.commit_message(repo, commit)
        lines = message.splitlines()
        trailers = _message_trailers(message)
        digests = [
            value for key, value in trailers if key == TASK_BINDING_DIGEST_TRAILER
        ]
        if not lines or not digests:
            continue
        if any(
            key == TASK_BINDING_COMMIT_OID_TRAILER for key, _value in trailers
        ):
            raise ValueError(
                f"bound item commit stores the binding commit OID at {commit}"
            )
        unsupported_keys = sorted(
            {key for key, _value in trailers if key not in ITEM_TRACE_TRAILER_KEYS}
        )
        if unsupported_keys:
            raise ValueError(
                f"bound item commit stores unsupported trailers at {commit}: "
                + ", ".join(unsupported_keys)
            )
        subject = lines[0]
        if "[" not in subject or not subject.endswith("]"):
            raise ValueError(f"bound item commit subject is missing its item id at {commit}")
        item_id = subject.rsplit("[", 1)[1][:-1]
        if digests != [binding_body_sha256]:
            raise ValueError(f"bound item commit digest mismatch at {commit}")
        obligation_ids = [
            value for key, value in trailers if key == OBLIGATION_ID_TRAILER
        ]
        try:
            item_binding_trailers(
                binding_body_sha256,
                obligation_ids,
            )
        except ValueError as exc:
            raise ValueError(f"bound item commit has invalid trailers at {commit}: {exc}") from exc
        traces.append(
            {
                "item_id": item_id,
                "commit_oid": git_wrapper.head_sha(repo, commit) or commit,
                "task_binding_body_sha256": binding_body_sha256,
                "production_obligation_ids": obligation_ids,
            }
        )
    return traces


def prepare_completion_assertion(
    repo,
    implementation_ref: str,
    *,
    asserted_at: str | None = None,
) -> PreparedCompletionAssertion:
    """Prepare the producer claim and promise updates for an implementation tip."""

    implementation_oid = git_wrapper.head_sha(repo, implementation_ref)
    if implementation_oid is None:
        raise ValueError("implementation ref is missing")
    binding_raw = git_wrapper.show_file_bytes(repo, implementation_oid, TASK_BINDING_PATH)
    if binding_raw is None:
        raise ValueError("producer task binding is missing")
    codec = BodyCodecClient()
    binding = patch_series_bodies.read_producer_task_binding(binding_raw, client=codec)
    binding_digest = hashlib.sha256(binding_raw).hexdigest()
    binding_commit_oid = git_wrapper.path_last_commit(
        repo, implementation_oid, TASK_BINDING_PATH
    )
    if binding_commit_oid is None:
        raise ValueError("producer task binding commit is missing")
    trace = item_commit_trace(
        repo, binding_commit_oid, implementation_oid, binding_digest
    )
    trace_by_item: dict[str, dict[str, Any]] = {}
    for row in trace:
        item_id = str(row["item_id"])
        if item_id in trace_by_item:
            raise ValueError(f"multiple item commits found for {item_id}")
        trace_by_item[item_id] = row

    promise_bindings = list(validate_promise_bindings(binding))
    promise_by_obligation = {
        str(row.get("obligation_id")): row
        for row in promise_bindings
        if isinstance(row, Mapping)
    }
    claims: list[dict[str, Any]] = []
    seen_claims: set[str] = set()
    required_by_item: dict[str, list[str]] = {}
    for task in binding["tasks"]:
        required_by_item.setdefault(str(task["item_id"]), []).append(
            str(task["production_obligation_ids"][0])
        )
    for item_id, obligation_ids in required_by_item.items():
        item_trace = trace_by_item.get(item_id)
        if item_trace is None or item_trace["production_obligation_ids"] != obligation_ids:
            raise ValueError(f"item commit trace does not exactly bind {item_id}")
    for task in binding["tasks"]:
        item_id = str(task["item_id"])
        obligation_id = str(task["production_obligation_ids"][0])
        item_trace = trace_by_item[item_id]
        promise_binding = promise_by_obligation.get(obligation_id)
        if not isinstance(promise_binding, Mapping) or obligation_id in seen_claims:
            raise ValueError(f"promise binding does not exactly bind {obligation_id}")
        promise_raw = git_wrapper.show_file_bytes(
            repo, str(promise_binding["promise_commit_oid"]), str(promise_binding["evidence_coordinate"])
        )
        if promise_raw is None or hashlib.sha256(promise_raw).hexdigest() != promise_binding["promise_body_sha256"]:
            raise ValueError(f"promise binding digest mismatch for {obligation_id}")
        promise = patch_series_bodies.read_producer_promise(promise_raw, client=codec)
        evidence = item_evidence(repo, item_trace["commit_oid"])
        claims.append(
            completion_claim(
                obligation_id,
                str(promise["referee_goal_id"]),
                item_id,
                evidence,
            )
        )
        seen_claims.add(obligation_id)
    if tuple(claim["obligation_id"] for claim in claims) != required_claim_set(binding):
        raise ValueError("completion assertion does not cover the required claim set")

    assertion = {
        field: binding[field]
        for field in (
            "series_branch",
            "node_path",
            "contribution_branch",
            "producer",
            "series_snapshot_oid",
            "canonical_root_body_sha256",
            "scope_decomposition_commit_oid",
            "scope_decomposition_body_sha256",
        )
    }
    assertion.update(
        {
            "task_binding_commit_oid": binding_commit_oid,
            "task_binding_body_sha256": binding_digest,
            "implementation_oid": implementation_oid,
            "promise_bindings": promise_bindings,
            "assertions": claims,
            "asserted_at": asserted_at or _utc_now(),
        }
    )
    prepared = codec.prepare(
        patch_series_bodies.PRODUCER_COMPLETION_ASSERTION_CONTEXT,
        assertion,
        expected=patch_series_bodies.PRODUCER_COMPLETION_ASSERTION_CONTRACT,
    )
    files = {COMPLETION_ASSERTION_PATH: prepared.canonical.data.decode("utf-8")}
    for promise_binding in promise_bindings:
        coordinate = str(promise_binding["evidence_coordinate"])
        current_raw = git_wrapper.show_file_bytes(repo, implementation_oid, coordinate)
        if current_raw is None:
            raise ValueError(f"current producer promise is missing at {coordinate}")
        promise = patch_series_bodies.read_producer_promise(current_raw, client=codec)
        promise["completion_claim_slot"] = {
            "assertion_path": COMPLETION_ASSERTION_PATH,
            "assertion_body_sha256": prepared.canonical.sha256,
        }
        promise["previous_promise_commit_oid"] = implementation_oid
        promise["authored_at"] = asserted_at or _utc_now()
        updated = codec.prepare(
            patch_series_bodies.PRODUCER_PROMISE_CONTEXT,
            promise,
            expected=patch_series_bodies.PRODUCER_PROMISE_CONTRACT,
        )
        files[coordinate] = updated.canonical.data.decode("utf-8")
    return PreparedCompletionAssertion(
        files, assertion, prepared.canonical.sha256, implementation_oid
    )


def item_evidence(repo, commit_oid: str) -> dict[str, str]:
    """Derive the canonical retained-artifact evidence for one item commit."""

    paths = [
        path
        for entry in git_wrapper.changed_paths(repo, f"{commit_oid}^", commit_oid)
        for path in entry.get("paths", [])
    ]
    for path in paths:
        blob_oid = git_wrapper.tree_blob_oid(repo, commit_oid, path)
        raw = git_wrapper.show_file_bytes(repo, commit_oid, path)
        if blob_oid is not None and raw is not None:
            return {
                "description": f"artifact changed by Git item commit {commit_oid}",
                "artifact_anchor": path,
                "artifact_blob_oid": blob_oid,
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
    raise ValueError(f"item commit {commit_oid} has no retained artifact evidence")


def prepare_task_binding(
    repo,
    series_head: str,
    contribution_ref: str,
    record: Mapping[str, Any],
    *,
    readiness_decision: Any = None,
    expected_readiness_route: str | None = None,
) -> PreparedTaskBinding:
    """Prepare one complete binding before any implementation commit.

    Historical generations remain on their established path. Producer-aware
    roots use the same common readiness decision as promise initialization and
    fail closed until that decision is authorable. The returned files update
    every promise commitment slot in the same commit as the binding, avoiding
    a digest/commit circularity: promises retain the pre-binding snapshot OID,
    while later assertions can name the now-known binding commit OID.
    """

    snapshot = patch_series_bodies.classify_root_generation(repo, series_head)
    if snapshot.generation != patch_series_bodies.ROOT_GENERATION_V2:
        return PreparedTaskBinding("inactive", {}, {})

    decision = readiness_decision
    if decision is None:
        # Compatibility for direct callers. The authoring orchestration passes
        # the exact decision returned by load_brief so binding preparation does
        # not create a second gate evaluation over the same source vector.
        patch_series_gate = importlib.import_module(
            "ai_org.patchwork_queue.patch_series_gate"
        )

        decision = patch_series_gate.vet_producer_pair_closure(
            repo,
            str(record["series_branch"]),
            TASK_BINDING_ROUTE,
            frozen_root_oid=series_head,
        )
    decision_source = (
        decision.get("source")
        if isinstance(decision, Mapping)
        else getattr(decision, "source", None)
    )
    decision_frozen_oid = (
        decision_source.get("frozen_root_oid", series_head)
        if isinstance(decision_source, Mapping)
        else getattr(decision_source, "frozen_root_oid", series_head)
    )
    decision_route = (
        decision_source.get("route")
        if isinstance(decision_source, Mapping)
        else getattr(decision_source, "route", None)
    )
    if decision_frozen_oid != series_head:
        return PreparedTaskBinding(
            "invalid",
            {},
            {},
            decision=decision,
            detail="producer readiness decision does not match the gated series snapshot",
        )
    if expected_readiness_route is not None and (
        expected_readiness_route not in AUTHORING_ROUTES
        or decision_route != expected_readiness_route
    ):
        return PreparedTaskBinding(
            "invalid",
            {},
            {},
            decision=decision,
            detail="producer readiness decision does not match the authoring route",
        )
    if isinstance(decision, Mapping):
        blocked = not bool(decision.get("vet_passed")) or (
            not bool(decision.get("authorable"))
            and not bool(decision.get("transition_allowed"))
        )
        diagnostic = decision.get("diagnostic")
        detail = (
            str(diagnostic.get("detail") or "")
            if isinstance(diagnostic, Mapping)
            else ""
        )
    else:
        blocked = decision.blocked
        diagnostic = decision.diagnostic
        detail = diagnostic.detail if diagnostic else ""
    if blocked:
        detail = detail or "producer lifecycle readiness is closed"
        return PreparedTaskBinding("blocked", {}, {}, decision=decision, detail=detail)

    try:
        decomposition = patch_series_bodies.project_series_scope_decomposition(repo, series_head)
        root_raw = snapshot.raw(patch_series_bodies.ROOT_APPROACH_PATH)
        if root_raw is None:
            raise ValueError("canonical producer root is missing")
        root = BodyCodecClient().parse(
            patch_series_bodies.ROOT_APPROACH_CONTEXT,
            root_raw,
            expected=patch_series_bodies.ROOT_APPROACH_CONTRACT,
        )
        prepared = _prepare_task_binding_files(
            repo,
            contribution_ref,
            record,
            series_head,
            decomposition,
            root,
            decision=decision,
        )
        validate_task_binding_source(
            prepared.body,
            record,
            series_snapshot_oid=series_head,
            readiness_decision=decision,
            expected_readiness_route=(
                expected_readiness_route or TASK_BINDING_ROUTE
            ),
        )
        return prepared
    except Exception as exc:
        return PreparedTaskBinding("invalid", {}, {}, decision=decision, detail=str(exc))


def _prepare_task_binding_files(
    repo,
    contribution_ref: str,
    record: Mapping[str, Any],
    series_head: str,
    decomposition: Any,
    root: Mapping[str, Any],
    *,
    decision: Any,
) -> PreparedTaskBinding:
    contribution_head = git_wrapper.head_sha(repo, contribution_ref)
    if contribution_head is None:
        raise ValueError("producer contribution ref is missing")
    node_path = str(record["node_path"])
    branch = str(record["contrib_branch"])
    producer = dict(record["author"])
    ownership = {
        str(row.get("scope_item_id")): str(row.get("owner_node_path"))
        for row in decomposition.body.get("ownership", [])
        if isinstance(row, Mapping)
    }

    problem = root.get("problem")
    if not isinstance(problem, Mapping):
        raise ValueError("producer task binding root problem is missing")
    assignments = problem.get("patch_plan")
    if not isinstance(assignments, list):
        raise ValueError("producer task binding patch-plan assignments are missing")

    tasks: list[dict[str, Any]] = []
    obligations_by_item: dict[str, list[str]] = {}
    for assignment in assignments:
        if not isinstance(assignment, Mapping):
            raise ValueError("producer task binding assignment is not an object")
        item_id = str(assignment.get("item_id") or "")
        obligation_ids = assignment.get("production_obligation_ids")
        if not item_id or not isinstance(obligation_ids, list):
            raise ValueError("producer task binding assignment is incomplete")
        for obligation_id in obligation_ids:
            obligation_id = str(obligation_id)
            if ownership.get(obligation_id) != node_path:
                continue
            tasks.append(
                {
                    "item_id": item_id,
                    "production_obligation_ids": [obligation_id],
                }
            )
            obligations_by_item.setdefault(item_id, []).append(obligation_id)
    if not tasks:
        raise ValueError(f"no production obligations are assigned to {node_path}")

    codec = BodyCodecClient()
    promises: dict[str, tuple[str, dict[str, Any], bytes]] = {}
    for path in sorted(
        path for path in git_wrapper.tree_files(repo, contribution_head) if is_evidence_path(path)
    ):
        raw = git_wrapper.show_file_bytes(repo, contribution_head, path)
        if raw is None:
            raise ValueError(f"producer promise body is missing at {path}")
        body = patch_series_bodies.read_producer_promise(raw, client=codec)
        obligation_id = str(body.get("obligation_id") or "")
        if obligation_id in promises:
            raise ValueError(f"duplicate producer promise for {obligation_id}")
        expected_source = {
            "series_snapshot_oid": series_head,
            "series_branch": str(record["series_branch"]),
            "node_path": node_path,
            "contribution_branch": branch,
            "canonical_root_body_sha256": str(decomposition.canonical_root_sha256),
        }
        if any(body.get(field) != value for field, value in expected_source.items()):
            raise ValueError(f"producer promise source mismatch at {path}")
        if dict(body.get("producer") or {}) != producer:
            raise ValueError(f"producer promise identity mismatch at {path}")
        if body.get("eligibility_decision") != "eligible":
            raise ValueError(f"producer promise is ineligible at {path}")
        expected_coordinate = evidence_coordinate(
            str(record["series_branch"]),
            node_path,
            branch,
            producer,
            str(decomposition.canonical_root_sha256),
            obligation_id,
        )
        if path != expected_coordinate:
            raise ValueError(f"producer evidence coordinate mismatch at {path}")
        promises[obligation_id] = (path, body, raw)

    required = [
        obligation
        for task in tasks
        for obligation in task["production_obligation_ids"]
    ]
    if len(required) != len(set(required)) or set(required) != set(promises):
        raise ValueError("producer task binding must cover the exact promise obligation cohort")

    promise_bindings = [
        {
            "obligation_id": obligation_id,
            "evidence_coordinate": path,
            "promise_commit_oid": contribution_head,
            "promise_body_sha256": hashlib.sha256(raw).hexdigest(),
        }
        for obligation_id, path, raw in (
            (obligation_id, promises[obligation_id][0], promises[obligation_id][2])
            for obligation_id in sorted(promises)
        )
    ]
    binding_seed = "\0".join(
        (
            str(record["series_branch"]),
            node_path,
            branch,
            producer["email"].strip().lower(),
            series_head,
            *required,
        )
    ).encode("utf-8")
    body = {
        "binding_id": f"producer-task-binding:{hashlib.sha256(binding_seed).hexdigest()}",
        "series_branch": str(record["series_branch"]),
        "node_path": node_path,
        "contribution_branch": branch,
        "series_snapshot_oid": series_head,
        "canonical_root_body_sha256": str(decomposition.canonical_root_sha256),
        "scope_decomposition_commit_oid": str(decomposition.frozen_oid),
        "scope_decomposition_body_sha256": str(decomposition.body_sha256),
        "producer": producer,
        "tasks": tasks,
        "promise_bindings": promise_bindings,
    }
    prepared = codec.prepare(
        patch_series_bodies.PRODUCER_TASK_BINDING_CONTEXT,
        body,
        expected=patch_series_bodies.PRODUCER_TASK_BINDING_CONTRACT,
    )
    files = {TASK_BINDING_PATH: prepared.canonical.data.decode("utf-8")}
    for binding in promise_bindings:
        obligation_id = str(binding["obligation_id"])
        path = str(binding["evidence_coordinate"])
        promise = dict(promises[obligation_id][1])
        promise["commitment_slot"] = {
            "task_binding_path": TASK_BINDING_PATH,
            "task_binding_body_sha256": prepared.canonical.sha256,
        }
        promise["previous_promise_commit_oid"] = contribution_head
        promise["authored_at"] = _utc_now()
        updated = codec.prepare(
            patch_series_bodies.PRODUCER_PROMISE_CONTEXT,
            promise,
            expected=patch_series_bodies.PRODUCER_PROMISE_CONTRACT,
        )
        files[path] = updated.canonical.data.decode("utf-8")
    return PreparedTaskBinding(
        "ready",
        files,
        body,
        prepared.canonical.sha256,
        {item_id: tuple(values) for item_id, values in obligations_by_item.items()},
        decision,
    )


def evidence_coordinate(
    series_branch: str,
    node_path: str,
    contribution_branch: str,
    producer: Mapping[str, str],
    canonical_root_body_sha256: str,
    obligation_id: str,
) -> str:
    """Return the versioned, family-local coordinate for one obligation.

    The path digest binds the complete evidence address.  In particular, the
    producer's normalized name and email and the canonical root body digest
    are inputs, so neither an identity change nor a different root generation
    can silently reuse an existing promise coordinate.
    """
    if (
        not isinstance(series_branch, str)
        or not series_branch.startswith("ai-org/patch-series/")
        or series_branch == "ai-org/patch-series/"
    ):
        raise ValueError("producer evidence coordinate series branch is invalid")
    if not isinstance(node_path, str) or (
        node_path != "." and re.fullmatch(r"sub/[a-z0-9_]+", node_path) is None
    ):
        raise ValueError("producer evidence coordinate node path is invalid")
    if (
        not isinstance(contribution_branch, str)
        or not contribution_branch.startswith("ai-org/contrib/")
        or contribution_branch == "ai-org/contrib/"
    ):
        raise ValueError("producer evidence coordinate contribution branch is invalid")
    if not isinstance(producer, Mapping):
        raise ValueError("producer evidence coordinate identity is incomplete")
    producer_name = producer.get("name")
    producer_email = producer.get("email")
    if not isinstance(producer_name, str) or not isinstance(producer_email, str):
        raise ValueError("producer evidence coordinate identity is incomplete")
    normalized_producer = {
        "name": producer_name.strip(),
        "email": producer_email.strip().lower(),
    }
    if (
        not normalized_producer["name"]
        or re.fullmatch(
            r"[^@\s]+@users\.noreply\.github\.com",
            normalized_producer["email"],
        )
        is None
    ):
        raise ValueError("producer evidence coordinate identity is incomplete")
    if not isinstance(canonical_root_body_sha256, str) or re.fullmatch(
        r"[0-9a-f]{64}", canonical_root_body_sha256
    ) is None:
        raise ValueError("producer evidence coordinate root digest is invalid")
    if not isinstance(obligation_id, str) or not obligation_id:
        raise ValueError("producer evidence coordinate obligation id is invalid")
    payload = json.dumps(
        [
            EVIDENCE_COORDINATE_VERSION,
            series_branch,
            node_path,
            contribution_branch,
            normalized_producer,
            canonical_root_body_sha256,
            obligation_id,
        ],
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    return f"{EVIDENCE_DIRECTORY}/{digest}/{PROMISE_BASENAME}"


def is_evidence_path(path: str) -> bool:
    item = PurePosixPath(path)
    return (
        len(item.parts) == 3
        and item.parts[0] == EVIDENCE_DIRECTORY
        and len(item.parts[1]) == 64
        and all(character in "0123456789abcdef" for character in item.parts[1])
        and item.name == PROMISE_BASENAME
    )


def promise_readiness_decision(
    repo,
    series_head: str,
    series_branch: str,
) -> Any | None:
    """Freeze the common readiness decision before lifecycle-related reads.

    Historical roots have no producer lifecycle and therefore return ``None``.
    Any root with migrated generation members, including an incomplete or
    malformed cohort, returns the exact typed authorability decision that must
    be reused by initialization. Callers must not perform naming reads that can
    refresh local refs before checking whether this decision is blocked.
    """

    snapshot = patch_series_bodies.classify_root_generation(repo, series_head)
    if snapshot.generation in {
        patch_series_bodies.ROOT_GENERATION_HISTORICAL,
        patch_series_bodies.ROOT_GENERATION_CURRENT,
    }:
        return None
    if snapshot.generation == patch_series_bodies.ROOT_GENERATION_INVALID:
        try:
            migrated_members_present = any(
                snapshot.raw(path) is not None
                for path in (
                    *patch_series_bodies.ROOT_COHORT_V2_PATHS,
                    patch_series_bodies.SCOPE_DECOMPOSITION_PATH,
                )
            )
        except AttributeError:
            # Lightweight test doubles may expose only the already-computed
            # generation-members predicate.
            migrated_members_present = bool(snapshot.has_generation_members)
        if not migrated_members_present:
            return None

    # Dynamic import avoids patch_series_gate -> announcements -> this module.
    patch_series_gate = importlib.import_module(
        "ai_org.patchwork_queue.patch_series_gate"
    )

    return patch_series_gate.vet_producer_pair_closure(
        repo,
        series_branch,
        PROMISE_READINESS_ROUTE,
        frozen_root_oid=series_head,
    )


def prepare_initialization(
    repo,
    series_head: str,
    record: Mapping[str, Any],
    *,
    authored_at: str | None = None,
    readiness_decision: Any = None,
) -> PromiseInitialization:
    """Prepare initial promises only after the common closed readiness gate.

    Historical roots stay on their established announcement-only path.  A
    canonical producer-aware root is never allowed to bypass the common
    decision: until canonical scope is durably present this returns a typed
    blocked result and publishes nothing.
    """
    decision = readiness_decision
    if decision is None:
        decision = promise_readiness_decision(
            repo, series_head, str(record["series_branch"])
        )
    if decision is None:
        return PromiseInitialization("inactive", {}, {})

    if isinstance(decision, Mapping):
        blocked = not bool(decision.get("vet_passed")) or (
            not bool(decision.get("authorable"))
            and not bool(decision.get("transition_allowed"))
        )
        diagnostic = decision.get("diagnostic")
        detail = (
            str(diagnostic.get("detail") or "")
            if isinstance(diagnostic, Mapping)
            else ""
        )
    else:
        blocked = decision.blocked
        diagnostic = decision.diagnostic
        detail = diagnostic.detail if diagnostic else ""
    decision_source = (
        decision.get("source")
        if isinstance(decision, Mapping)
        else getattr(decision, "source", None)
    )
    decision_route = (
        decision_source.get("route")
        if isinstance(decision_source, Mapping)
        else getattr(decision_source, "route", None)
    )
    decision_frozen_oid = (
        decision_source.get("frozen_root_oid")
        if isinstance(decision_source, Mapping)
        else getattr(decision_source, "frozen_root_oid", None)
    )
    if (
        decision_route != PROMISE_READINESS_ROUTE
        or decision_frozen_oid != series_head
    ):
        return PromiseInitialization(
            "invalid",
            {},
            {},
            decision,
            "producer readiness decision does not match the gated series snapshot",
        )
    if blocked:
        detail = detail or "producer lifecycle readiness is closed"
        return PromiseInitialization("blocked", {}, {}, decision, detail)

    snapshot = patch_series_bodies.classify_root_generation(repo, series_head)
    if snapshot.generation != patch_series_bodies.ROOT_GENERATION_V2:
        return PromiseInitialization(
            "invalid",
            {},
            {},
            decision,
            "frozen root generation changed after readiness decision",
        )

    try:
        decomposition = patch_series_bodies.project_series_scope_decomposition(repo, series_head)
        root_raw = snapshot.raw(patch_series_bodies.ROOT_APPROACH_PATH)
        assert root_raw is not None
        root = BodyCodecClient().parse(
            patch_series_bodies.ROOT_APPROACH_CONTEXT,
            root_raw,
            expected=patch_series_bodies.ROOT_APPROACH_CONTRACT,
        )
        files, coordinates = _initial_promise_files(
            record,
            series_head,
            decomposition,
            root,
            authored_at=authored_at or _utc_now(),
        )
    except Exception as exc:
        return PromiseInitialization("invalid", {}, {}, decision, str(exc))
    return PromiseInitialization("ready", files, coordinates, decision)


def _initial_promise_files(
    record: Mapping[str, Any],
    series_head: str,
    decomposition: Any,
    root: Mapping[str, Any],
    *,
    authored_at: str,
) -> tuple[dict[str, str], dict[str, str]]:
    node_path = str(record["node_path"])
    ownership = {
        str(row.get("scope_item_id")): str(row.get("owner_node_path"))
        for row in decomposition.body.get("ownership", [])
        if isinstance(row, Mapping)
    }
    problem = root.get("problem")
    if not isinstance(problem, Mapping):
        raise ValueError("producer promise root problem is missing")
    raw_obligations = problem.get("production_obligations")
    if not isinstance(raw_obligations, list):
        raise ValueError("producer promise obligations are missing")

    codec = BodyCodecClient()
    files: dict[str, str] = {}
    coordinates: dict[str, str] = {}
    for obligation in raw_obligations:
        if not isinstance(obligation, Mapping):
            continue
        obligation_id = str(obligation.get("id") or "")
        if not obligation_id or ownership.get(obligation_id) != node_path:
            continue
        path = evidence_coordinate(
            str(record["series_branch"]),
            node_path,
            str(record["contrib_branch"]),
            record["author"],
            str(decomposition.canonical_root_sha256),
            obligation_id,
        )
        body = {
            "series_snapshot_oid": series_head,
            "series_branch": str(record["series_branch"]),
            "node_path": node_path,
            "contribution_branch": str(record["contrib_branch"]),
            "canonical_root_body_sha256": str(decomposition.canonical_root_sha256),
            "referee_goal_id": str(obligation.get("referee_goal_id") or ""),
            "obligation_id": obligation_id,
            "deliverable": str(obligation.get("deliverable") or ""),
            "eligibility_predicate": str(obligation.get("eligibility_predicate") or ""),
            "producer": dict(record["author"]),
            "eligibility_decision": "eligible",
            "eligibility_basis": f"exact scope ownership at {node_path}",
            "commitment_slot": {},
            "completion_claim_slot": {},
            "previous_promise_commit_oid": None,
            "authored_at": authored_at,
        }
        prepared = codec.prepare(
            patch_series_bodies.PRODUCER_PROMISE_CONTEXT,
            body,
            expected=patch_series_bodies.PRODUCER_PROMISE_CONTRACT,
        )
        files[path] = prepared.canonical.data.decode("utf-8", errors="strict")
        coordinates[obligation_id] = path
    if not files:
        raise ValueError(f"no production obligations are owned by {node_path}")
    if len(set(coordinates.values())) != len(coordinates):
        raise ValueError("producer evidence coordinate collision")
    return files, coordinates


def is_promise_only_contribution(repo, ref: str) -> bool:
    """Return true only when no implementation commit follows initialization.

    Lifecycle-looking subjects are not authority by themselves.  Every
    initialization or slot-transition commit must have a non-empty diff whose
    paths are the exact creation-sealed family promise cohort; otherwise the
    commit is an implementation submission even if its subject uses a
    lifecycle prefix.
    """
    current = git_wrapper.head_sha(repo, ref)
    if current is None:
        return False
    implementation_seen = False
    transition_path_sets: list[tuple[str, ...]] = []
    for _depth in range(4096):
        subjects = git_wrapper.log_subjects(repo, current)
        subject = subjects[0] if subjects else ""
        parents = git_wrapper.parent_commits(repo, current)
        initialization = subject.startswith(INITIAL_SUBJECT_PREFIX)
        transition = subject.startswith(TRANSITION_SUBJECT_PREFIX)
        if initialization:
            initialization_paths = (
                _promise_evidence_change_paths(
                    git_wrapper.changed_paths(repo, f"{current}^", current),
                    allowed_status="A",
                )
                if len(parents) == 1
                else None
            )
            initialized_branch = (
                _single_promise_family_branch(repo, current, initialization_paths)
                if initialization_paths is not None
                else None
            )
            return (
                initialization_paths is not None
                and initialized_branch is not None
                and _initialization_commit_is_valid(
                    repo, current, initialization_paths
                )
                and not _tree_has_promise_for_branch(
                    repo, parents[0], initialized_branch
                )
                and all(
                    paths == initialization_paths
                    for paths in transition_path_sets
                )
                and not implementation_seen
            )
        transition_paths = (
            _promise_evidence_change_paths(
                git_wrapper.changed_paths(repo, f"{current}^", current),
                allowed_status="M",
            )
            if transition and len(parents) == 1
            else None
        )
        if transition_paths is not None and not _promise_transition_is_valid(
            repo, current, parents[0], transition_paths, subject
        ):
            transition_paths = None
        if transition_paths is None:
            implementation_seen = True
        else:
            transition_path_sets.append(transition_paths)
        if not parents:
            return False
        current = parents[0]
    return False


def _promise_evidence_change_paths(
    changes: list[Mapping[str, Any]],
    *,
    allowed_status: str,
) -> tuple[str, ...] | None:
    """Return the exact changed promise cohort, or ``None`` if it is invalid."""

    if not changes or any(
        change.get("status") != allowed_status for change in changes
    ):
        return None
    paths = [
        path
        for change in changes
        for path in change.get("paths", [])
    ]
    if (
        not paths
        or len(paths) != len(set(paths))
        or not all(is_evidence_path(path) for path in paths)
    ):
        return None
    return tuple(sorted(paths))


def _initialization_commit_is_valid(
    repo, treeish: str, paths: tuple[str, ...]
) -> bool:
    """Validate initial slot state, not merely lifecycle-looking path names."""

    try:
        bodies: dict[str, Mapping[str, Any]] = {}
        if _single_promise_family_branch(
            repo, treeish, paths, bodies=bodies
        ) is None:
            return False
        seen: set[str] = set()
        for path in paths:
            body = bodies[path]
            obligation_id = str(body["obligation_id"])
            if obligation_id in seen:
                return False
            _validate_initial_promise_body(body, path, obligation_id)
            seen.add(obligation_id)
    except Exception:
        return False
    return bool(seen)


_IMMUTABLE_PROMISE_FIELDS = frozenset(
    {
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
    }
)


def _promise_transition_is_valid(
    repo,
    current: str,
    parent: str,
    paths: tuple[str, ...],
    subject: str,
) -> bool:
    """Recognize only the two family-local monotonic slot transitions.

    A lifecycle-looking subject plus an exact path set is insufficient: without
    checking body semantics, arbitrary edits to every promise could disappear
    from implementation discovery. The remote writer still supplies CAS; this
    reader verifies the resulting commit is a legitimate monotonic transition.
    """

    transition = {
        f"{TRANSITION_SUBJECT_PREFIX} commitment": "commitment",
        f"{TRANSITION_SUBJECT_PREFIX} completion_claim": "completion_claim",
    }.get(subject)
    if transition is None:
        return False
    try:
        before_bodies: dict[str, Mapping[str, Any]] = {}
        after_bodies: dict[str, Mapping[str, Any]] = {}
        before_branch = _single_promise_family_branch(
            repo, parent, paths, bodies=before_bodies
        )
        after_branch = _single_promise_family_branch(
            repo, current, paths, bodies=after_bodies
        )
        if before_branch is None or before_branch != after_branch:
            return False
        for path in paths:
            before = before_bodies[path]
            after = after_bodies[path]
            if any(
                before.get(field) != after.get(field)
                for field in _IMMUTABLE_PROMISE_FIELDS
            ):
                return False
            if after.get("previous_promise_commit_oid") != parent:
                return False
            if transition == "commitment":
                if (
                    before.get("commitment_slot")
                    or not after.get("commitment_slot")
                    or before.get("completion_claim_slot")
                    or after.get("completion_claim_slot")
                ):
                    return False
            elif (
                not before.get("commitment_slot")
                or before.get("commitment_slot") != after.get("commitment_slot")
                or before.get("completion_claim_slot")
                or not after.get("completion_claim_slot")
            ):
                return False
    except Exception:
        return False
    return True


def _single_promise_family_branch(
    repo,
    treeish: str,
    paths: tuple[str, ...],
    *,
    bodies: dict[str, Mapping[str, Any]] | None = None,
) -> str | None:
    """Return one family branch when every initialized body agrees."""

    codec = BodyCodecClient()
    try:
        branches: set[str] = set()
        for path in paths:
            body = patch_series_bodies.read_producer_promise(
                git_wrapper.show_file(repo, treeish, path),
                client=codec,
            )
            branches.add(str(body["contribution_branch"]))
            if bodies is not None:
                bodies[path] = body
    except Exception:
        return None
    return next(iter(branches)) if len(branches) == 1 else None


def _tree_has_promise_for_branch(repo, treeish: str, branch: str) -> bool:
    """Whether an inherited tree already contains this family's coordinates."""

    codec = BodyCodecClient()
    for path in git_wrapper.tree_files(repo, treeish):
        if not is_evidence_path(path):
            continue
        try:
            body = patch_series_bodies.read_producer_promise(
                git_wrapper.show_file(repo, treeish, path),
                client=codec,
            )
        except Exception:
            continue
        if body.get("contribution_branch") == branch:
            return True
    return False


def _initialized_family_paths(
    repo,
    ref: str,
    contribution_branch: str,
) -> tuple[str, ...]:
    """Recover the creation-sealed coordinates for one contribution family.

    A contribution branch inherits the base tree, which may already contain
    accepted promise evidence from unrelated families.  The family's own
    initialization diff is therefore the authority for later transitions;
    scanning every promise-looking path in the inherited tree would turn
    competitors into accidental admission inputs.
    """

    current = git_wrapper.head_sha(repo, ref)
    for _depth in range(4096):
        if current is None:
            break
        subjects = git_wrapper.log_subjects(repo, current)
        parents = git_wrapper.parent_commits(repo, current)
        if subjects and subjects[0].startswith(INITIAL_SUBJECT_PREFIX) and len(parents) == 1:
            paths = _promise_evidence_change_paths(
                git_wrapper.changed_paths(repo, f"{current}^", current),
                allowed_status="A",
            )
            if paths is not None and _single_promise_family_branch(
                repo, current, paths
            ) == contribution_branch:
                return paths
        if not parents:
            break
        current = parents[0]
    raise ValueError("producer promise initialization cohort is missing")


def has_implementation_submission(repo, ref: str) -> bool:
    """Branch presence alone is not submission when the branch is promise-only."""
    return git_wrapper.head_sha(repo, ref) is not None and not is_promise_only_contribution(repo, ref)


def transition_slots(
    repo,
    remote: str,
    contribution_branch: str,
    transition: str,
    slots_by_obligation: Mapping[str, Mapping[str, str]],
    *,
    authored_at: str | None = None,
) -> dict[str, Any]:
    """Advance every promise for one family with compare-and-swap."""
    slot_field = {
        "commitment": "commitment_slot",
        "completion_claim": "completion_claim_slot",
    }.get(transition)
    if slot_field is None:
        return {"ok": False, "status": "promise_transition_invalid", "transition": transition}
    remote_ref = f"refs/heads/{contribution_branch}"
    listed = git_wrapper.ls_remote(repo, remote, remote_ref)
    if not listed.get("ok"):
        return {"ok": False, "status": "remote_unavailable", "detail": listed.get("detail", "")}
    expected = listed["refs"].get(remote_ref, "")
    if not expected:
        return {"ok": False, "status": "promise_contribution_missing", "branch": contribution_branch}
    fetched_ref = f"refs/remotes/{remote}/{contribution_branch}"
    fetched = git_wrapper.fetch_refspecs(repo, remote, [f"+{remote_ref}:{fetched_ref}"])
    if not fetched.get("ok") or git_wrapper.head_sha(repo, fetched_ref) != expected:
        return {"ok": False, "status": "remote_unavailable", "detail": fetched.get("detail", "")}

    try:
        paths = _initialized_family_paths(repo, expected, contribution_branch)
    except ValueError as exc:
        return {
            "ok": False,
            "status": "promise_transition_invalid",
            "detail": str(exc),
        }
    sealed_paths = set(paths)
    for extra_path in git_wrapper.tree_files(repo, expected):
        if extra_path in sealed_paths or not is_evidence_path(extra_path):
            continue
        try:
            extra = patch_series_bodies.read_producer_promise(
                git_wrapper.show_file(repo, expected, extra_path)
            )
        except Exception:
            continue
        if extra.get("contribution_branch") == contribution_branch:
            return {
                "ok": False,
                "status": "promise_transition_invalid",
                "detail": (
                    "promise transition crosses producer families or adds an "
                    f"unsealed coordinate at {extra_path}"
                ),
            }
    codec = BodyCodecClient()
    files: dict[str, str] = {}
    producer: Mapping[str, str] | None = None
    family: tuple[str, ...] | None = None
    seen: set[str] = set()
    try:
        for path in paths:
            raw = git_wrapper.show_file(repo, expected, path)
            if raw is None:
                raise ValueError(f"promise body missing at {path}")
            body = patch_series_bodies.read_producer_promise(raw, client=codec)
            obligation_id = str(body["obligation_id"])
            body_producer = body["producer"]
            body_family = (
                str(body["series_snapshot_oid"]),
                str(body["series_branch"]),
                str(body["node_path"]),
                str(body["contribution_branch"]),
                str(body["canonical_root_body_sha256"]),
                str(body_producer["name"]),
                str(body_producer["email"]).strip().lower(),
            )
            if str(body["contribution_branch"]) != contribution_branch:
                raise ValueError(f"promise family branch mismatch at {path}")
            if body["eligibility_decision"] != "eligible":
                raise ValueError(f"producer promise is ineligible at {path}")
            if family is None:
                family = body_family
            elif family != body_family:
                raise ValueError("promise transition crosses producer families")
            coordinate = evidence_coordinate(
                str(body["series_branch"]),
                str(body["node_path"]),
                str(body["contribution_branch"]),
                body_producer,
                str(body["canonical_root_body_sha256"]),
                obligation_id,
            )
            if path != coordinate:
                raise ValueError(f"promise evidence coordinate mismatch at {path}")
            if obligation_id in seen:
                raise ValueError(f"duplicate producer promise for {obligation_id}")
            slot = slots_by_obligation.get(obligation_id)
            if not isinstance(slot, Mapping):
                raise ValueError(f"promise slot missing for {obligation_id}")
            if body[slot_field]:
                raise ValueError(f"promise {slot_field} is already bound for {obligation_id}")
            if transition == "commitment" and body["completion_claim_slot"]:
                raise ValueError(f"promise completion precedes commitment for {obligation_id}")
            if transition == "completion_claim" and not body["commitment_slot"]:
                raise ValueError(f"promise commitment is missing for {obligation_id}")
            body[slot_field] = dict(slot)
            body["previous_promise_commit_oid"] = expected
            body["authored_at"] = authored_at or _utc_now()
            prepared = codec.prepare(
                patch_series_bodies.PRODUCER_PROMISE_CONTEXT,
                body,
                expected=patch_series_bodies.PRODUCER_PROMISE_CONTRACT,
            )
            files[path] = prepared.canonical.data.decode("utf-8", errors="strict")
            producer = body_producer
            seen.add(obligation_id)
        if not files or seen != set(slots_by_obligation):
            raise ValueError("promise transition must cover the exact obligation cohort")
        written = git_wrapper.create_ref_with_files(
            repo,
            remote_ref,
            files,
            subject=f"{TRANSITION_SUBJECT_PREFIX} {transition}",
            parent=expected,
            identity=producer,
            update_ref=False,
            inherit_parent_tree=True,
        )
    except Exception as exc:
        return {"ok": False, "status": "promise_transition_invalid", "detail": str(exc)}

    pushed = git_wrapper.push_cas(repo, remote, remote_ref, written["commit"], expected)
    if not pushed.get("ok"):
        status = "promise_transition_conflict" if pushed.get("status") == "rejected" else "push_failed"
        return {
            "ok": False,
            "status": status,
            "expected": expected,
            "expected_ref_oids": {remote_ref: expected},
            "resulting_ref_oids": {},
            "detail": pushed.get("detail", ""),
        }
    refreshed = git_wrapper.update_ref(
        repo,
        f"refs/heads/{contribution_branch}",
        written["commit"],
        expected=expected,
    )
    if not refreshed.get("ok"):
        return {"ok": False, "status": "local_refresh_failed", "detail": refreshed.get("detail", "")}
    return {
        "ok": True,
        "status": f"producer_promise_{transition}_bound",
        "branch": contribution_branch,
        "commit": written["commit"],
        "previous_commit": expected,
        "expected_ref_oids": {remote_ref: expected},
        "resulting_ref_oids": {remote_ref: written["commit"]},
    }


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
