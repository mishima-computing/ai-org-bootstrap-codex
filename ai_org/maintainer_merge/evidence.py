"""Verified producer evidence carried by code-only integration commits."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import importlib
import json
from pathlib import Path
import subprocess
from typing import Iterable, Mapping

from ai_org import contributor_handoff, git_wrapper, patch_series_bodies
from ai_org.body_codec import BodyCodecClient

announcements = importlib.import_module("ai_org.patch_author.announcements")
functional_check = importlib.import_module("ai_org.patch_author.functional_check")
producer_lifecycle = importlib.import_module("ai_org.patch_author.producer_lifecycle")


EVIDENCE_LINK_TRAILER = "Producer-Evidence-Link"
EXCLUSION_POLICY_TRAILER = "Evidence-Exclusion-Policy-Version"
COORDINATE_TRAILER = "Integrated-Contribution-Coordinate"
EXCLUSION_POLICY_VERSION = "producer-code-only-v1"
COORDINATE_VERSION = "integrated-contribution-v1"
SUBSYSTEM_INTEGRATION_SUBJECT = "subsystem-integration: code-only contribution"
MAINLINE_INTEGRATION_SUBJECT = "mainline-integration: promote subsystem"
IMPLEMENTATION_CUE_DIRECTORY = "ai-org/implementation-cue"
AUTHORITY_NOTES_REF = functional_check.ACCEPTANCE_AUTHORITY_NOTES_REF
_MIGRATED_INTEGRATION_ROUTES = frozenset(
    {"subsystem_integration", "mainline_integration"}
)

_EXCLUDED_PATHS = frozenset(
    {
        # Frozen-root planning records are lifecycle authority, not producer
        # code.  In particular, the migrated scope decomposition must never
        # ride an implementation delta into subsystem or mainline.
        patch_series_bodies.COVER_PATH,
        patch_series_bodies.PROVENANCE_PATH,
        patch_series_bodies.ROOT_APPROACH_PATH,
        patch_series_bodies.SCOPE_DECOMPOSITION_PATH,
        patch_series_bodies.LEGACY_COVER_PATH,
        patch_series_bodies.LEGACY_PROVENANCE_PATH,
        patch_series_bodies.LEGACY_ROOT_APPROACH_PATH,
        patch_series_bodies.LEGACY_COVERAGE_LEDGER_PATH,
        patch_series_bodies.CANONICAL_COVERAGE_LEDGER_PATH,
        announcements.ANNOUNCEMENT_RECORD_PATH,
        announcements.LEGACY_ANNOUNCEMENT_RECORD_PATH,
        producer_lifecycle.TASK_BINDING_PATH,
        producer_lifecycle.COMPLETION_ASSERTION_PATH,
        functional_check.CLAIM_ADMISSION_PATH,
        functional_check.FUNCTIONAL_ACCEPTANCE_VERDICT_PATH,
        contributor_handoff.QUESTIONS_PATH,
        contributor_handoff.LEGACY_QUESTIONS_PATH,
        contributor_handoff.EXPERIENCE_PATH,
        contributor_handoff.LEGACY_EXPERIENCE_PATH,
        contributor_handoff.RESULT_CONTRACT_PATH,
        contributor_handoff.LEGACY_RESULT_SCHEMA_PATH,
        contributor_handoff.RESULT_PATH,
        contributor_handoff.LEGACY_RESULT_PATH,
    }
)
_EXCLUDED_PREFIXES = (
    f"{producer_lifecycle.EVIDENCE_DIRECTORY}/",
    f"{IMPLEMENTATION_CUE_DIRECTORY}/",
)


class EvidenceError(ValueError):
    """A migrated integration record is missing or cannot be verified."""


@dataclass(frozen=True, order=True, slots=True)
class ProducerEvidenceLink:
    coordinate: str
    verdict_oid: str
    implementation_oid: str

    def trailer_value(self) -> str:
        return f"{self.coordinate} {self.verdict_oid} {self.implementation_oid}"


@dataclass(frozen=True, slots=True)
class SealedContribution:
    links: tuple[ProducerEvidenceLink, ...]
    contribution_branch: str
    binding_commit_oid: str
    code_paths: tuple[str, ...]
    authority_notes_oid: str

    def __post_init__(self) -> None:
        ordered = tuple(sorted(set(self.links)))
        if not ordered:
            raise EvidenceError("sealed contribution has no obligation evidence links")
        if len({(link.verdict_oid, link.implementation_oid) for link in ordered}) != 1:
            raise EvidenceError("sealed contribution links do not share one sealed source")
        if len({link.coordinate for link in ordered}) != len(ordered):
            raise EvidenceError("sealed contribution coordinates are not distinct")
        if (
            len(self.authority_notes_oid) not in {40, 64}
            or any(
                character not in "0123456789abcdef"
                for character in self.authority_notes_oid
            )
            or len(self.authority_notes_oid) != len(ordered[0].verdict_oid)
        ):
            raise EvidenceError("sealed contribution authority snapshot is invalid")
        object.__setattr__(self, "links", ordered)

    @property
    def verdict_oid(self) -> str:
        return self.links[0].verdict_oid

    @property
    def implementation_oid(self) -> str:
        return self.links[0].implementation_oid


def contribution_root_generation(
    repo, ref: str, *, gate_route: str = "subsystem_integration"
) -> patch_series_bodies.RootGenerationSnapshot | None:
    """Return the frozen root generation bound to a producer contribution.

    The task binding is only a coordinate lookup.  The root classifier remains
    the sole authority for deciding whether the contribution is migrated, and
    it reads the exact immutable series snapshot named by the binding.
    """

    tip = git_wrapper.head_sha(repo, ref)
    if tip is None:
        return None
    binding_raw = git_wrapper.show_file_bytes(
        repo, tip, producer_lifecycle.TASK_BINDING_PATH
    )
    if binding_raw is None:
        producer_markers = (
            producer_lifecycle.COMPLETION_ASSERTION_PATH,
            functional_check.CLAIM_ADMISSION_PATH,
            functional_check.FUNCTIONAL_ACCEPTANCE_VERDICT_PATH,
        )
        if any(
            git_wrapper.show_file_bytes(repo, tip, path) is not None
            for path in producer_markers
        ):
            raise EvidenceError(
                "producer lifecycle cohort is incomplete: task binding is missing"
            )
        return None
    try:
        binding = patch_series_bodies.read_producer_task_binding(
            binding_raw, client=BodyCodecClient()
        )
    except Exception as exc:
        raise EvidenceError(f"producer task binding is invalid: {exc}") from exc
    frozen_root_oid = str(binding.get("series_snapshot_oid") or "")
    series_branch = str(binding.get("series_branch") or "")
    if not frozen_root_oid or not series_branch:
        raise EvidenceError("producer task binding source identity is incomplete")

    # Import dynamically to keep the lineage/evidence dependency one-way.  The
    # binding selects the immutable root; the common vet remains the sole
    # authority for producer-pair and exact-scope authorability.
    patch_series_gate = importlib.import_module(
        "ai_org.patchwork_queue.patch_series_gate"
    )

    decision = patch_series_gate.vet_producer_pair_closure(
        repo,
        series_branch,
        gate_route,
        frozen_root_oid=frozen_root_oid,
    )
    if decision.blocked:
        diagnostic = decision.diagnostic
        detail = (
            diagnostic.detail or diagnostic.rule
            if diagnostic is not None
            else "producer integration authorability is closed"
        )
        raise EvidenceError(
            f"{gate_route.replace('_', ' ')} gate closed: {detail}"
        )

    snapshot = patch_series_gate.vetted_root_snapshot(repo, decision.source)
    _require_migrated_integration_gate(snapshot, gate_route)
    if (
        snapshot.generation != patch_series_bodies.ROOT_GENERATION_V2
        or not snapshot.lifecycle_ready
    ):
        raise EvidenceError(snapshot.diagnostic)
    expected_identity = {
        "representation": patch_series_bodies.ROOT_GENERATION_V2,
        "source_path": patch_series_bodies.ROOT_APPROACH_PATH,
        "source_oid": frozen_root_oid,
        "context": patch_series_bodies.ROOT_APPROACH_CONTEXT,
        "canonical_digest": binding.get("canonical_root_body_sha256"),
    }
    if snapshot.identity() != expected_identity:
        raise EvidenceError("producer task binding root identity does not match its frozen tree")
    if (
        decision.source.frozen_root_oid != frozen_root_oid
        or decision.source.root_generation != patch_series_bodies.ROOT_GENERATION_V2
        or decision.source.root_sha256
        != str(binding.get("canonical_root_body_sha256") or "")
        or decision.source.scope_decomposition_sha256
        != str(binding.get("scope_decomposition_body_sha256") or "")
        or str(binding.get("scope_decomposition_commit_oid") or "")
        != frozen_root_oid
    ):
        raise EvidenceError(
            "producer task binding does not match the gated frozen source vector"
        )
    return snapshot


def _require_migrated_integration_gate(
    snapshot: patch_series_bodies.RootGenerationSnapshot,
    gate_route: str,
) -> None:
    """Require the one complete activation at either integration boundary."""

    if gate_route not in _MIGRATED_INTEGRATION_ROUTES:
        raise EvidenceError(f"unknown migrated integration route: {gate_route}")

    # The common producer-pair vet proves the frozen source vector. Rechecking
    # its activation projection here makes the integration boundary explicit:
    # neither a partially opened gate set nor an inconsistent authorability
    # decision may select or promote a sealed implementation.
    patch_series_gate = importlib.import_module(
        "ai_org.patchwork_queue.patch_series_gate"
    )

    activation = patch_series_gate.migrated_lifecycle_activation(snapshot)
    if (
        activation.dispatcher != "producer-aware-v2"
        or not activation.activated
        or not activation.authorable
        or "integration" not in activation.open_gates
    ):
        raise EvidenceError("migrated lifecycle integration gate is closed")


def is_migrated_tree(
    repo, ref: str, *, gate_route: str = "subsystem_integration"
) -> bool:
    """Return whether the contribution's classified frozen root is v2."""

    snapshot = contribution_root_generation(repo, ref, gate_route=gate_route)
    return bool(
        snapshot
        and snapshot.generation == patch_series_bodies.ROOT_GENERATION_V2
    )


def sealed_contribution(
    repo, ref: str, *, gate_route: str = "subsystem_integration"
) -> SealedContribution:
    verdict_oid = git_wrapper.head_sha(repo, ref)
    authority_notes_oid = git_wrapper.head_sha(repo, AUTHORITY_NOTES_REF)
    if (
        verdict_oid is None
        or authority_notes_oid is None
        or not functional_check.sealed_verdict_accepted(
            repo,
            verdict_oid,
            frozen_authority_notes_oid=authority_notes_oid,
            expected_contribution_branch=ref,
        )
    ):
        raise EvidenceError("current contribution tip is not sealed by acceptance authority")
    sealed = sealed_contribution_from_verdict(
        repo,
        verdict_oid,
        gate_route=gate_route,
        frozen_authority_notes_oid=authority_notes_oid,
    )
    if sealed.contribution_branch != ref.removeprefix("refs/heads/"):
        raise EvidenceError("sealed verdict belongs to a different contribution ref")
    return sealed


def sealed_contribution_from_verdict(
    repo,
    verdict_oid: str,
    *,
    gate_route: str = "subsystem_integration",
    frozen_authority_notes_oid: str | None = None,
) -> SealedContribution:
    authority_notes_oid = (
        git_wrapper.head_sha(repo, AUTHORITY_NOTES_REF)
        if frozen_authority_notes_oid is None
        else frozen_authority_notes_oid
    )
    if not authority_notes_oid or not functional_check.sealed_verdict_accepted(
        repo,
        verdict_oid,
        frozen_authority_notes_oid=authority_notes_oid,
    ):
        raise EvidenceError(f"producer evidence verdict is not sealed: {verdict_oid}")
    assertion_raw = git_wrapper.show_file_bytes(
        repo, verdict_oid, producer_lifecycle.COMPLETION_ASSERTION_PATH
    )
    binding_raw = git_wrapper.show_file_bytes(
        repo, verdict_oid, producer_lifecycle.TASK_BINDING_PATH
    )
    admission_raw = git_wrapper.show_file_bytes(
        repo, verdict_oid, functional_check.CLAIM_ADMISSION_PATH
    )
    if assertion_raw is None or binding_raw is None or admission_raw is None:
        raise EvidenceError("sealed producer lifecycle records are missing")
    try:
        codec = BodyCodecClient()
        assertion = patch_series_bodies.read_producer_completion_assertion(
            assertion_raw, client=codec
        )
        binding = patch_series_bodies.read_producer_task_binding(binding_raw, client=codec)
        admission = patch_series_bodies.read_claim_admission(admission_raw, client=codec)
    except Exception as exc:
        raise EvidenceError(f"sealed producer lifecycle record is invalid: {exc}") from exc

    # Reclassify the binding's immutable series snapshot at the integration
    # boundary. This prevents a migrated contribution from falling back to a
    # lifecycle-file predicate after acceptance.
    contribution_root_generation(repo, verdict_oid, gate_route=gate_route)

    implementation_oid = str(assertion.get("implementation_oid") or "")
    binding_commit_oid = str(assertion.get("task_binding_commit_oid") or "")
    contribution_branch = str(binding.get("contribution_branch") or "")
    verdict_parents = git_wrapper.parent_commits(repo, verdict_oid)
    evaluated_oid = str(admission.get("evaluated_contribution_oid") or "")
    assertion_commit_oid = (
        git_wrapper.path_last_commit(
            repo, evaluated_oid, producer_lifecycle.COMPLETION_ASSERTION_PATH
        )
        if evaluated_oid
        else None
    )
    admitted_assertions = admission.get("assertions")
    assertion_sha256 = hashlib.sha256(assertion_raw).hexdigest()
    admission_assertion_ids = _unique_obligation_ids(admitted_assertions)
    assertion_links_exact = bool(
        assertion_commit_oid is not None
        and isinstance(admitted_assertions, list)
        and admitted_assertions
        and all(
            isinstance(row, Mapping)
            and row.get("assertion_commit_oid") == assertion_commit_oid
            and row.get("assertion_body_sha256") == assertion_sha256
            for row in admitted_assertions
        )
    )
    if (
        not implementation_oid
        or not binding_commit_oid
        or not contribution_branch
        or verdict_parents != [evaluated_oid]
        or not assertion_links_exact
        or assertion.get("contribution_branch") != contribution_branch
        or assertion.get("task_binding_body_sha256")
        != hashlib.sha256(binding_raw).hexdigest()
        or git_wrapper.path_last_commit(
            repo, implementation_oid, producer_lifecycle.TASK_BINDING_PATH
        ) != binding_commit_oid
        or not git_wrapper.is_ancestor(repo, binding_commit_oid, implementation_oid)
        or not git_wrapper.is_ancestor(repo, implementation_oid, evaluated_oid)
    ):
        raise EvidenceError("sealed implementation source vector is not resolvable")

    required_obligation_ids = admission.get("required_obligation_ids")
    binding_promises = binding.get("promise_bindings")
    assertion_promises = assertion.get("promise_bindings")
    assertion_claims = assertion.get("assertions")
    binding_obligation_ids = _unique_obligation_ids(binding_promises)
    assertion_obligation_ids = _unique_obligation_ids(assertion_claims)
    binding_sha256 = hashlib.sha256(binding_raw).hexdigest()
    if (
        not isinstance(required_obligation_ids, list)
        or not required_obligation_ids
        or any(
            not isinstance(value, str) or not value
            for value in required_obligation_ids
        )
        or len(set(required_obligation_ids)) != len(required_obligation_ids)
        or binding_obligation_ids is None
        or assertion_obligation_ids is None
        or admission_assertion_ids is None
        or admission.get("promise_bindings") != binding_promises
        or admission.get("task_binding_body_sha256") != binding_sha256
        or assertion_promises != binding_promises
        or set(binding_obligation_ids) != set(required_obligation_ids)
        or list(assertion_obligation_ids) != required_obligation_ids
        or list(admission_assertion_ids) != required_obligation_ids
    ):
        raise EvidenceError("sealed obligation evidence cohort is not exact")

    code_paths = changed_code_paths(repo, binding_commit_oid, implementation_oid)
    if not code_paths:
        raise EvidenceError("sealed implementation has no code-only changes")
    links = tuple(
        ProducerEvidenceLink(
            integrated_contribution_coordinate(binding, obligation_id),
            verdict_oid,
            implementation_oid,
        )
        for obligation_id in required_obligation_ids
    )
    return SealedContribution(
        links,
        contribution_branch,
        binding_commit_oid,
        code_paths,
        authority_notes_oid or "",
    )


def _unique_obligation_ids(value: object) -> tuple[str, ...] | None:
    """Return a complete unique row projection, never a filtered subset."""

    if not isinstance(value, list) or not value:
        return None
    identifiers: list[str] = []
    for row in value:
        if not isinstance(row, Mapping):
            return None
        obligation_id = row.get("obligation_id")
        if not isinstance(obligation_id, str) or not obligation_id:
            return None
        identifiers.append(obligation_id)
    if len(set(identifiers)) != len(identifiers):
        return None
    return tuple(identifiers)


def integrated_contribution_coordinate(
    binding: Mapping[str, object], obligation_id: str
) -> str:
    """Derive one stable integration identity for one producer obligation.

    Verdict and item IDs are deliberately absent.  A verdict resolves the
    coordinate, but it does not define the producer family or obligation.
    """

    producer = binding.get("producer")
    normalized_producer = (
        {
            "name": str(producer.get("name") or "").strip(),
            "email": str(producer.get("email") or "").strip().lower(),
        }
        if isinstance(producer, Mapping)
        else {"name": "", "email": ""}
    )
    series_branch = str(binding.get("series_branch") or "")
    node_path = str(binding.get("node_path") or "")
    contribution_branch = str(binding.get("contribution_branch") or "")
    canonical_root_digest = str(binding.get("canonical_root_body_sha256") or "")
    if (
        not series_branch
        or not node_path
        or not contribution_branch
        or not normalized_producer["name"]
        or not normalized_producer["email"]
        or len(canonical_root_digest) != 64
        or any(
            character not in "0123456789abcdef"
            for character in canonical_root_digest
        )
        or not obligation_id
    ):
        raise EvidenceError("integrated contribution coordinate source is incomplete")
    payload = json.dumps(
        [
            COORDINATE_VERSION,
            series_branch,
            node_path,
            contribution_branch,
            normalized_producer,
            canonical_root_digest,
            obligation_id,
        ],
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"{COORDINATE_VERSION}:{hashlib.sha256(payload).hexdigest()}"


def is_excluded_path(path: str) -> bool:
    return path in _EXCLUDED_PATHS or path.startswith(_EXCLUDED_PREFIXES)


def changed_code_paths(repo, base: str, implementation_oid: str) -> tuple[str, ...]:
    paths: set[str] = set()
    for change in git_wrapper.changed_paths(repo, base, implementation_oid):
        if change.get("status") == "!":
            raise EvidenceError("could not resolve sealed implementation changes")
        changed = tuple(change.get("paths", []))
        # A rename or copy touching a lifecycle/handoff coordinate is one
        # indivisible change.  Excluding only the protected endpoint would let
        # its contents re-enter the code-only tree under an ordinary filename.
        if any(is_excluded_path(path) for path in changed):
            continue
        paths.update(changed)
    return tuple(sorted(paths))


def integration_message(subject: str, links: Iterable[ProducerEvidenceLink]) -> str:
    ordered = sorted(set(links))
    if not ordered:
        raise EvidenceError("integration commit requires producer evidence links")
    if len({link.coordinate for link in ordered}) != len(ordered):
        raise EvidenceError("integration commit has a coordinate collision")
    lines = [
        f"{EXCLUSION_POLICY_TRAILER}: {EXCLUSION_POLICY_VERSION}",
        *(f"{COORDINATE_TRAILER}: {link.coordinate}" for link in ordered),
        *(f"{EVIDENCE_LINK_TRAILER}: {link.trailer_value()}" for link in ordered),
    ]
    return subject + "\n\n" + "\n".join(lines)


def links_from_message(message: str) -> tuple[ProducerEvidenceLink, ...]:
    values = _trailer_values(message, EVIDENCE_LINK_TRAILER)
    links: list[ProducerEvidenceLink] = []
    for value in values:
        parts = value.split()
        if len(parts) != 3:
            raise EvidenceError("producer evidence link has an invalid shape")
        coordinate, verdict_oid, implementation_oid = parts
        coordinate_digest = coordinate.removeprefix(f"{COORDINATE_VERSION}:")
        if (
            not coordinate.startswith(f"{COORDINATE_VERSION}:")
            or len(coordinate_digest) != 64
            or any(c not in "0123456789abcdef" for c in coordinate_digest)
        ):
            raise EvidenceError("producer evidence link has an unsupported coordinate")
        if any(
            len(oid) not in {40, 64}
            or any(c not in "0123456789abcdef" for c in oid)
            for oid in (verdict_oid, implementation_oid)
        ):
            raise EvidenceError("producer evidence link has an invalid object id")
        links.append(ProducerEvidenceLink(coordinate, verdict_oid, implementation_oid))
    return tuple(links)


def verified_links(
    repo,
    commits: Iterable[str],
    *,
    require_current: bool = False,
    frozen_authority_notes_oid: str | None = None,
) -> tuple[ProducerEvidenceLink, ...]:
    """Return the exact sealed links carried by pending subsystem commits.

    A repository stays on the legacy path until it emits a producer integration
    marker. Once that marker exists, every pending commit is part of the current
    handshake and must be a complete subsystem integration record. This keeps a
    malformed or unlinked commit from being promoted through the legacy fallback.
    """

    records = tuple(
        (commit, git_wrapper.commit_message(repo, commit)) for commit in commits
    )
    require_current = require_current or any(
        _has_current_integration_marker(message) for _, message in records
    )
    if not require_current:
        return ()
    if not records:
        raise EvidenceError("current mainline integration requires subsystem evidence")

    by_coordinate: dict[str, ProducerEvidenceLink] = {}
    for _, message in records:
        if message.splitlines()[:1] != [SUBSYSTEM_INTEGRATION_SUBJECT]:
            raise EvidenceError(
                "current subsystem history contains a commit without producer evidence"
            )
        links = _validated_integration_links(message, SUBSYSTEM_INTEGRATION_SUBJECT)
        links_by_verdict: dict[str, list[ProducerEvidenceLink]] = {}
        for link in links:
            links_by_verdict.setdefault(link.verdict_oid, []).append(link)
        for verdict_oid, carried in links_by_verdict.items():
            resolved = sealed_contribution_from_verdict(
                repo,
                verdict_oid,
                gate_route="mainline_integration",
                frozen_authority_notes_oid=frozen_authority_notes_oid,
            ).links
            if tuple(carried) != resolved:
                raise EvidenceError(
                    "subsystem producer evidence links changed their sealed source"
                )
        for link in links:
            previous = by_coordinate.setdefault(link.coordinate, link)
            if previous != link:
                raise EvidenceError("integrated contribution coordinate collision")
    return tuple(sorted(by_coordinate.values()))


def current_integration_required(repo, refs: Iterable[str]) -> bool:
    """Return whether any reachable commit has entered the current handshake."""

    for commit in _commits_for_refs(repo, refs):
        if _has_current_integration_marker(git_wrapper.commit_message(repo, commit)):
            return True
    return False


def has_verified_link(repo, verdict_oid: str, refs: Iterable[str]) -> bool:
    authority_notes_oid = git_wrapper.head_sha(repo, AUTHORITY_NOTES_REF)
    if authority_notes_oid is None:
        return False
    matched = False
    for commit in _commits_for_refs(repo, refs):
        message = git_wrapper.commit_message(repo, commit)
        subject = message.splitlines()[0] if message.splitlines() else ""
        if subject not in {SUBSYSTEM_INTEGRATION_SUBJECT, MAINLINE_INTEGRATION_SUBJECT}:
            continue
        try:
            links = _validated_integration_links(message, subject)
        except EvidenceError:
            continue
        carried = tuple(link for link in links if link.verdict_oid == verdict_oid)
        if not carried:
            continue
        try:
            resolved = sealed_contribution_from_verdict(
                repo,
                verdict_oid,
                gate_route="mainline_integration",
                frozen_authority_notes_oid=authority_notes_oid,
            ).links
        except EvidenceError:
            return False
        if resolved != carried:
            return False
        matched = True
    return bool(
        matched
        and git_wrapper.head_sha(repo, AUTHORITY_NOTES_REF) == authority_notes_oid
    )


def _commits_for_refs(repo, refs: Iterable[str]) -> tuple[str, ...]:
    commits: set[str] = set()
    for ref in refs:
        if git_wrapper.head_sha(repo, ref) is None:
            continue
        result = subprocess.run(
            ["git", "-C", str(Path(repo)), "rev-list", ref],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if result.returncode == 0:
            commits.update(result.stdout.splitlines())
    return tuple(sorted(commits))


def _has_current_integration_marker(message: str) -> bool:
    lines = message.splitlines()
    subject = lines[0] if lines else ""
    return (
        subject.startswith("subsystem-integration:")
        or subject.startswith("mainline-integration:")
        or any(
            _trailer_values(message, trailer)
            for trailer in (
                EVIDENCE_LINK_TRAILER,
                EXCLUSION_POLICY_TRAILER,
                COORDINATE_TRAILER,
            )
        )
    )


def _validated_integration_links(
    message: str, expected_subject: str
) -> tuple[ProducerEvidenceLink, ...]:
    """Parse one complete, engine-shaped integration evidence record."""

    if message.splitlines()[:1] != [expected_subject]:
        raise EvidenceError("integration commit subject is invalid")
    if _trailer_values(message, EXCLUSION_POLICY_TRAILER) != [
        EXCLUSION_POLICY_VERSION
    ]:
        raise EvidenceError("integration exclusion policy is missing or unsupported")
    links = links_from_message(message)
    coordinates = _trailer_values(message, COORDINATE_TRAILER)
    if not links or coordinates != [link.coordinate for link in links]:
        raise EvidenceError("integration coordinates do not exactly match its links")
    if tuple(sorted(set(links))) != links:
        raise EvidenceError("producer evidence links are not sorted and unique")
    return links


def _trailer_values(message: str, name: str) -> list[str]:
    prefix = f"{name}:"
    return [
        line[len(prefix) :].strip()
        for line in message.splitlines()
        if line.startswith(prefix)
    ]
