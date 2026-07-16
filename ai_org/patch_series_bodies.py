"""Canonical root patch-series cohort and its per-invocation carrier recipe.

New root formation and explicit reform prepare and publish the producer-aware
three-member CUE cohort as one unit.  Canonical scope remains the separate
activation marker written by the network gate before authorability opens.
Readers retain support for untouched historical roots on the legacy dispatcher.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
from types import MappingProxyType
from typing import Any, Mapping

from ai_org.body_codec import (
    BodyCodecClient,
    CodecArtifact,
    ContractIdentity,
    ExactJSONNumber,
    PINNED_PRODUCER_LIFECYCLE_IDENTITIES,
    PRODUCER_LIFECYCLE_MANIFEST,
    PreparedBodySet,
    strict_json_loads,
)


COVER_CONTEXT = "patch-series-cover-letter-v1"
PROVENANCE_CONTEXT = "request-provenance-branch-v1"
ROOT_APPROACH_CONTEXT = "root-technical-approach-tree-v1"
SCOPE_DECOMPOSITION_CONTEXT = "series-scope-decomposition-v1"
PRODUCER_PROMISE_CONTEXT = "producer-promise-v1"
PRODUCER_TASK_BINDING_CONTEXT = "producer-task-binding-v1"
PRODUCER_COMPLETION_ASSERTION_CONTEXT = "producer-completion-assertion-v1"
CLAIM_ADMISSION_CONTEXT = "claim-admission-v1"
FUNCTIONAL_ACCEPTANCE_CONTEXT = "functional-acceptance-v1"
ACCEPTANCE_AUTHORITY_SEAL_CONTEXT = "acceptance-authority-seal-v1"
COVER_PATH = "patch-series-cover-letter.cue"
PROVENANCE_PATH = "patch-series-request-provenance.cue"
ROOT_APPROACH_PATH = "technical-approach-plan.cue"
SCOPE_DECOMPOSITION_PATH = "series-scope-decomposition.cue"
LEGACY_COVER_PATH = "patch-series-cover-letter.json"
LEGACY_PROVENANCE_PATH = "patch-series-request-provenance.json"
LEGACY_ROOT_APPROACH_PATH = "technical-approach-plan.json"
LEGACY_COVERAGE_LEDGER_PATH = "series-coverage-ledger.json"
CANONICAL_COVERAGE_LEDGER_PATH = "series-coverage-ledger.cue"
COVERAGE_LEDGER_INPUT_PATHS = (
    CANONICAL_COVERAGE_LEDGER_PATH,
    LEGACY_COVERAGE_LEDGER_PATH,
)
# This value is part of every carrier invocation and must be the exact profile
# accepted by the engine registry, not a descriptive alias.
PROJECTION_PROFILE = "consumer-json-v1"
SCHEMA_PROFILE = "codex-structured-output-v1"

# Historical ledgers are migration inputs, not material to normalize.  Keep
# the complete admitted category vocabulary in one immutable contract value
# so the projector copies those rows verbatim and only adds the new producer
# obligation category.
ESTABLISHED_SERIES_SCOPE_CATEGORIES = (
    "desired_outcome",
    "referee_goal",
    "goal",
    "ux_acceptance_test",
    "patch_plan",
    "must_address_risk",
    "domain_specification",
)
# The canonical root schema is closed, and this local projection boundary is
# also callable with already-decoded mappings.  Keep the exact obligation
# fields here so a caller cannot smuggle an owner or patch-plan coordinate into
# an obligation and thereby create a second assignment authority.
_ROOT_PRODUCTION_OBLIGATION_FIELDS = frozenset(
    {
        "id",
        "referee_goal_id",
        "deliverable_requirement_id",
        "deliverable",
        "eligibility_predicate",
        "replacement_links",
    }
)
_ROOT_PRODUCTION_OBLIGATION_REQUIRED_FIELDS = (
    _ROOT_PRODUCTION_OBLIGATION_FIELDS - {"replacement_links"}
)
_ROOT_DELIVERABLE_REQUIREMENT_FIELDS = frozenset(
    {
        "id",
        "referee_goal_id",
        "production_obligation_id",
        "deliverable",
    }
)
_ROOT_PATCH_PLAN_ASSIGNMENT_FIELDS = frozenset(
    {"item_id", "production_obligation_ids"}
)

# The final decomposition persists the exact tuple consumed by rebaseline.
# Historical ledgers briefly also carried the review-record coordinate; it is
# accepted only as read-time compatibility input and never becomes part of the
# closed successor contract.
ESCALATION_CONSUMPTION_FIELDS = (
    "review_round",
    "child_branch",
    "git_result_commit",
)
HISTORICAL_ESCALATION_CONSUMPTION_FIELDS = (
    *ESCALATION_CONSUMPTION_FIELDS,
    "review_record_path",
)

COVER_CONTRACT = ContractIdentity("ai-org-cue-body-v1", "PatchSeriesCoverLetter", "patch-series-root")
PROVENANCE_CONTRACT = ContractIdentity("ai-org-cue-body-v1", "RequestProvenanceRecord", "patch-series-branch")
ROOT_APPROACH_CONTRACT = ContractIdentity(
    "ai-org-cue-body-v1", "TechnicalApproachTree", "patch-series-root"
)
SCOPE_DECOMPOSITION_CONTRACT = ContractIdentity(
    "ai-org-cue-body-v1", "SeriesScopeDecomposition", "patch-series-root-preview"
)
_PINNED_PRODUCER_LIFECYCLE_CONTRACTS = {
    context: ContractIdentity(api_version, kind, variant)
    for context, api_version, kind, variant in PINNED_PRODUCER_LIFECYCLE_IDENTITIES
}
PRODUCER_PROMISE_CONTRACT = _PINNED_PRODUCER_LIFECYCLE_CONTRACTS[
    PRODUCER_PROMISE_CONTEXT
]
PRODUCER_TASK_BINDING_CONTRACT = _PINNED_PRODUCER_LIFECYCLE_CONTRACTS[
    PRODUCER_TASK_BINDING_CONTEXT
]
PRODUCER_COMPLETION_ASSERTION_CONTRACT = _PINNED_PRODUCER_LIFECYCLE_CONTRACTS[
    PRODUCER_COMPLETION_ASSERTION_CONTEXT
]
CLAIM_ADMISSION_CONTRACT = _PINNED_PRODUCER_LIFECYCLE_CONTRACTS[
    CLAIM_ADMISSION_CONTEXT
]
FUNCTIONAL_ACCEPTANCE_CONTRACT = _PINNED_PRODUCER_LIFECYCLE_CONTRACTS[
    FUNCTIONAL_ACCEPTANCE_CONTEXT
]
ACCEPTANCE_AUTHORITY_SEAL_CONTRACT = _PINNED_PRODUCER_LIFECYCLE_CONTRACTS[
    ACCEPTANCE_AUTHORITY_SEAL_CONTEXT
]
# One closed, read-only routing authority keeps the Python readers aligned
# with the engine registry's producer lifecycle compatibility matrix.  The
# tuple is intentionally immutable: registering these bodies does not activate
# any producer lifecycle writer.
PRODUCER_LIFECYCLE_CONTRACT_MATRIX_MANIFEST = PRODUCER_LIFECYCLE_MANIFEST
PRODUCER_LIFECYCLE_CONTRACT_MATRIX = (
    (PRODUCER_PROMISE_CONTEXT, PRODUCER_PROMISE_CONTRACT),
    (PRODUCER_TASK_BINDING_CONTEXT, PRODUCER_TASK_BINDING_CONTRACT),
    (
        PRODUCER_COMPLETION_ASSERTION_CONTEXT,
        PRODUCER_COMPLETION_ASSERTION_CONTRACT,
    ),
    (CLAIM_ADMISSION_CONTEXT, CLAIM_ADMISSION_CONTRACT),
    (FUNCTIONAL_ACCEPTANCE_CONTEXT, FUNCTIONAL_ACCEPTANCE_CONTRACT),
    (ACCEPTANCE_AUTHORITY_SEAL_CONTEXT, ACCEPTANCE_AUTHORITY_SEAL_CONTRACT),
)
_PRODUCER_LIFECYCLE_CONTRACTS = MappingProxyType(
    dict(PRODUCER_LIFECYCLE_CONTRACT_MATRIX)
)
if len(_PRODUCER_LIFECYCLE_CONTRACTS) != len(PRODUCER_LIFECYCLE_CONTRACT_MATRIX):
    raise RuntimeError("producer lifecycle compatibility matrix has duplicate contexts")
if tuple(
    (context, contract.api_version, contract.kind, contract.variant)
    for context, contract in PRODUCER_LIFECYCLE_CONTRACT_MATRIX
) != PINNED_PRODUCER_LIFECYCLE_IDENTITIES:
    raise RuntimeError("producer lifecycle compatibility matrix differs from codec pins")
COVERAGE_LEDGER_CONTRACT = ContractIdentity(
    "ai-org-cue-body-v1", "SeriesCoverageLedger", "network-root"
)
ROOT_COHORT_V2_MANIFEST = "patch-series-root-cohort-v2"
ROOT_COHORT_V2_PATHS = (
    COVER_PATH,
    PROVENANCE_PATH,
    ROOT_APPROACH_PATH,
)

ROOT_GENERATION_HISTORICAL = "historical_json"
ROOT_GENERATION_CURRENT = "current_two_cue_plus_json"
ROOT_GENERATION_V2 = "complete_v2"
ROOT_GENERATION_INVALID = "invalid"

ROOT_DISPOSITION_LEGACY_READY = "legacy_ready"
ROOT_DISPOSITION_CURRENT_READY = "current_ready"
ROOT_DISPOSITION_V2_NOT_READY = "producer_lifecycle_not_ready"
ROOT_DISPOSITION_V2_READY = "producer_lifecycle_ready"
ROOT_DISPOSITION_INVALID = "invalid_root_generation"

_ROOT_REPRESENTATION_PATHS = (
    COVER_PATH,
    PROVENANCE_PATH,
    ROOT_APPROACH_PATH,
    LEGACY_COVER_PATH,
    LEGACY_PROVENANCE_PATH,
    LEGACY_ROOT_APPROACH_PATH,
)
_ROOT_GENERATION_PATHS = (
    *_ROOT_REPRESENTATION_PATHS,
    SCOPE_DECOMPOSITION_PATH,
)


class RootGenerationError(ValueError):
    """A frozen root tree cannot be consumed by the current lifecycle."""

    def __init__(self, status: str, frozen_oid: str | None, detail: str = "") -> None:
        self.status = status
        self.frozen_oid = frozen_oid
        self.detail = detail
        message = status if not detail else f"{status}: {detail}"
        super().__init__(message)


class SeriesScopeDecompositionError(ValueError):
    """A frozen root and its historical allocation cannot form one partition."""

    def __init__(self, rule_id: str, coordinate: str, detail: str = "") -> None:
        self.rule_id = rule_id
        self.coordinate = coordinate
        self.detail = detail
        message = f"series-scope-decomposition:{rule_id}:{coordinate}"
        if detail:
            message += f":{detail}"
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class RootGenerationRecipe:
    """One closed set of required, optional, and forbidden root members."""

    required: frozenset[str]
    optional: frozenset[str] = frozenset()
    forbidden: frozenset[str] = frozenset()

    def matches(self, paths: set[str] | frozenset[str]) -> bool:
        """Return whether *paths* is exactly one admitted recipe expansion."""

        members = frozenset(paths)
        return (
            self.required <= members
            and members <= self.required | self.optional
            and not members.intersection(self.forbidden)
        )


# Root generation is data, not precedence.  Keeping the three recipes in one
# immutable manifest ensures a partial or mixed tree cannot become historical
# merely because one legacy member happens to be present.  Historical
# provenance and approach are optional because both were introduced after the
# original cover-only root; the current and v2 cohorts are exact triples.
ROOT_GENERATION_RECIPES: Mapping[str, RootGenerationRecipe] = MappingProxyType(
    {
        ROOT_GENERATION_HISTORICAL: RootGenerationRecipe(
            required=frozenset({LEGACY_COVER_PATH}),
            optional=frozenset(
                {LEGACY_PROVENANCE_PATH, LEGACY_ROOT_APPROACH_PATH}
            ),
            forbidden=frozenset(
                {COVER_PATH, PROVENANCE_PATH, ROOT_APPROACH_PATH}
            ),
        ),
        ROOT_GENERATION_CURRENT: RootGenerationRecipe(
            required=frozenset(
                {COVER_PATH, PROVENANCE_PATH, LEGACY_ROOT_APPROACH_PATH}
            ),
            forbidden=frozenset(
                {LEGACY_COVER_PATH, LEGACY_PROVENANCE_PATH, ROOT_APPROACH_PATH}
            ),
        ),
        ROOT_GENERATION_V2: RootGenerationRecipe(
            required=frozenset(ROOT_COHORT_V2_PATHS),
            forbidden=frozenset(
                {
                    LEGACY_COVER_PATH,
                    LEGACY_PROVENANCE_PATH,
                    LEGACY_ROOT_APPROACH_PATH,
                }
            ),
        ),
    }
)


@dataclass(frozen=True, slots=True)
class RootGenerationSnapshot:
    """One classification and all root bytes from one immutable Git tree."""

    frozen_oid: str | None
    generation: str
    disposition: str
    members: tuple[tuple[str, bytes | None], ...]
    detail: str = ""

    @property
    def representation(self) -> str:
        """The sole root representation selected from the frozen tree."""

        return self.generation

    @property
    def source_path(self) -> str | None:
        """The authoritative root coordinate selected by this snapshot."""

        if self.generation == ROOT_GENERATION_V2:
            return ROOT_APPROACH_PATH
        if self.generation == ROOT_GENERATION_CURRENT:
            return LEGACY_ROOT_APPROACH_PATH
        if self.generation == ROOT_GENERATION_HISTORICAL:
            if self.raw(LEGACY_ROOT_APPROACH_PATH) is not None:
                return LEGACY_ROOT_APPROACH_PATH
            return LEGACY_COVER_PATH
        return None

    @property
    def cover_path(self) -> str | None:
        """The cover coordinate selected without probing the root tree again."""

        if self.generation in {ROOT_GENERATION_CURRENT, ROOT_GENERATION_V2}:
            return COVER_PATH
        if self.generation == ROOT_GENERATION_HISTORICAL:
            return LEGACY_COVER_PATH
        return None

    @property
    def source_oid(self) -> str | None:
        """The immutable tree identity from which every member was read."""

        return self.frozen_oid

    @property
    def context(self) -> str | None:
        """The semantic body context of the selected root source."""

        if self.source_path == LEGACY_COVER_PATH:
            return COVER_CONTEXT
        return ROOT_APPROACH_CONTEXT if self.source_path is not None else None

    @property
    def canonical_digest(self) -> str | None:
        """Digest of the selected source bytes from the frozen snapshot."""

        path = self.source_path
        if path is None:
            return None
        raw = self.raw(path)
        return hashlib.sha256(raw).hexdigest() if raw is not None else None

    def identity(self) -> dict[str, str | None]:
        """Return the complete identity consumed at root-reader boundaries."""

        return {
            "representation": self.representation,
            "source_path": self.source_path,
            "source_oid": self.source_oid,
            "context": self.context,
            "canonical_digest": self.canonical_digest,
        }

    @property
    def lifecycle_ready(self) -> bool:
        return self.disposition in {
            ROOT_DISPOSITION_LEGACY_READY,
            ROOT_DISPOSITION_CURRENT_READY,
            ROOT_DISPOSITION_V2_READY,
        }

    @property
    def has_generation_members(self) -> bool:
        return any(raw is not None for _path, raw in self.members)

    @property
    def lifecycle_blocked(self) -> bool:
        return self.has_generation_members and not self.lifecycle_ready

    @property
    def diagnostic(self) -> str:
        return (
            self.disposition
            if not self.detail
            else f"{self.disposition}: {self.detail}"
        )

    @property
    def legacy_predicates_allowed(self) -> bool:
        return self.lifecycle_ready and self.generation != ROOT_GENERATION_V2

    def raw(self, path: str) -> bytes | None:
        for member_path, value in self.members:
            if member_path == path:
                return value
        raise KeyError(path)

    def require_lifecycle_ready(self) -> None:
        if not self.lifecycle_ready:
            raise RootGenerationError(self.disposition, self.frozen_oid, self.detail)

    def cover_letter(
        self, *, client: BodyCodecClient | None = None
    ) -> dict[str, Any]:
        self.require_lifecycle_ready()
        if self.generation in {ROOT_GENERATION_CURRENT, ROOT_GENERATION_V2}:
            raw = self.raw(COVER_PATH)
            assert raw is not None
            value = (client or BodyCodecClient()).parse(
                COVER_CONTEXT, raw, expected=COVER_CONTRACT
            )
        else:
            raw = self.raw(LEGACY_COVER_PATH)
            if raw is None:
                raise RootGenerationError(
                    ROOT_DISPOSITION_INVALID,
                    self.frozen_oid,
                    "root cover letter is missing",
                )
            value = _legacy_json_value(strict_json_loads(raw))
        if not isinstance(value, Mapping):
            raise RootGenerationError(
                ROOT_DISPOSITION_INVALID,
                self.frozen_oid,
                "root cover letter must be an object",
            )
        return dict(value)

    def technical_approach(
        self, *, client: BodyCodecClient | None = None
    ) -> dict[str, Any]:
        self.require_lifecycle_ready()
        if self.generation == ROOT_GENERATION_V2:
            raw = self.raw(ROOT_APPROACH_PATH)
            assert raw is not None
            value = (client or BodyCodecClient()).parse(
                ROOT_APPROACH_CONTEXT, raw, expected=ROOT_APPROACH_CONTRACT
            )
        else:
            raw = self.raw(LEGACY_ROOT_APPROACH_PATH)
            if raw is None:
                raise RootGenerationError(
                    ROOT_DISPOSITION_INVALID,
                    self.frozen_oid,
                    f"{LEGACY_ROOT_APPROACH_PATH} is missing",
                )
            value = strict_json_loads(raw)
        value = _legacy_json_value(value)
        if not isinstance(value, Mapping):
            raise RootGenerationError(
                ROOT_DISPOSITION_INVALID,
                self.frozen_oid,
                "root technical approach must be an object",
            )
        return dict(value)


@dataclass(frozen=True, slots=True)
class CarrierMember:
    context: str
    contract: ContractIdentity
    profile: str
    blob_sha256: str


@dataclass(frozen=True, slots=True)
class GroundingCarrierRecipe:
    sources: tuple[CarrierMember, CarrierMember]
    target_context: str
    target_contract: ContractIdentity
    schema_profile: str
    schema_sha256: str

    def __post_init__(self) -> None:
        """Reject recipes whose coordinates do not describe this closed cohort."""
        expected_sources = (
            (COVER_CONTEXT, COVER_CONTRACT),
            (PROVENANCE_CONTEXT, PROVENANCE_CONTRACT),
        )
        if len(self.sources) != len(expected_sources):
            raise ValueError("grounding carrier requires the complete ordered source cohort")
        if tuple(member.context for member in self.sources) != tuple(
            context for context, _ in expected_sources
        ):
            raise ValueError("grounding carrier requires the complete ordered source cohort")
        for member, (context, contract) in zip(self.sources, expected_sources, strict=True):
            if member.context != context or member.contract != contract:
                raise ValueError("grounding carrier source context and contract triple do not match")
            if member.profile != PROJECTION_PROFILE:
                raise ValueError("grounding carrier source projection profile does not match")
            _require_sha256(member.blob_sha256, "source blob")
        if self.target_context != COVER_CONTEXT or self.target_contract != COVER_CONTRACT:
            raise ValueError("grounding carrier target context and contract triple do not match")
        if self.schema_profile != SCHEMA_PROFILE:
            raise ValueError("grounding carrier target schema profile does not match")
        _require_sha256(self.schema_sha256, "target schema")


@dataclass(frozen=True, slots=True)
class ImmutableGitTree:
    """The exact publication unit from which a carrier invocation projected."""

    commit_oid: str
    tree_oid: str

    def __post_init__(self) -> None:
        _require_git_oid(self.commit_oid, "commit")
        _require_git_oid(self.tree_oid, "tree")


@dataclass(frozen=True, slots=True)
class RootPreviewCarrierRecipe:
    """Exact ordered membership for root cohort v2 prepublication.

    This recipe is deliberately separate from :class:`GroundingCarrierRecipe`.
    Treating the preview as an extension of that live recipe would make the
    current two-member cohort predicate depend on a contract that is not yet
    authorable.
    """

    members: tuple[CarrierMember, CarrierMember, CarrierMember]
    cohort_manifest: str = ROOT_COHORT_V2_MANIFEST

    def __post_init__(self) -> None:
        expected_members = (
            (COVER_CONTEXT, COVER_CONTRACT),
            (PROVENANCE_CONTEXT, PROVENANCE_CONTRACT),
            (ROOT_APPROACH_CONTEXT, ROOT_APPROACH_CONTRACT),
        )
        if self.cohort_manifest != ROOT_COHORT_V2_MANIFEST:
            raise ValueError("root preview carrier manifest does not match cohort v2")
        if len(self.members) != len(expected_members):
            raise ValueError("root preview carrier requires the complete ordered three-member cohort")
        for member, (context, contract) in zip(
            self.members, expected_members, strict=True
        ):
            if member.context != context or member.contract != contract:
                raise ValueError(
                    "root preview carrier member context and contract triple do not match"
                )
            if member.profile != PROJECTION_PROFILE:
                raise ValueError("root preview carrier projection profile does not match")
            _require_sha256(member.blob_sha256, "root preview member blob")


@dataclass(frozen=True, slots=True)
class PairedProjectionResult:
    cover_letter: Mapping[str, Any]
    request_provenance: Mapping[str, Any]
    recipe: GroundingCarrierRecipe
    exact_schema: CodecArtifact
    publication: ImmutableGitTree


@dataclass(frozen=True, slots=True)
class PreparedPatchSeriesCohort:
    cover_letter: PreparedBodySet
    request_provenance: PreparedBodySet
    recipe: GroundingCarrierRecipe
    cover_projection: CodecArtifact
    provenance_projection: CodecArtifact
    exact_schema: CodecArtifact

    def __post_init__(self) -> None:
        """Keep the preflight result and its invocation-local recipe inseparable."""
        expected = (
            (self.cover_letter, COVER_CONTEXT, COVER_CONTRACT, self.cover_projection),
            (
                self.request_provenance,
                PROVENANCE_CONTEXT,
                PROVENANCE_CONTRACT,
                self.provenance_projection,
            ),
        )
        for index, (prepared, context, contract, projection) in enumerate(expected):
            if prepared.context_id != context or prepared.contract != contract:
                raise ValueError("prepared patch-series cohort member identity does not match")
            if self.recipe.sources[index].blob_sha256 != prepared.canonical.sha256:
                raise ValueError("grounding carrier source blob digest does not match preflight")
            _require_profile_artifact(
                projection,
                "application/json",
                self.recipe.sources[index].profile,
                "source projection",
            )
        if self.exact_schema.sha256 != self.recipe.schema_sha256:
            raise ValueError("grounding carrier target schema digest does not match preflight")
        _require_profile_artifact(
            self.exact_schema,
            "application/schema+json",
            self.recipe.schema_profile,
            "target schema",
        )

    def files(self) -> dict[str, str]:
        return {
            COVER_PATH: self.cover_letter.canonical.data.decode("utf-8", errors="strict"),
            PROVENANCE_PATH: self.request_provenance.canonical.data.decode("utf-8", errors="strict"),
        }


@dataclass(frozen=True, slots=True)
class PreparedRootTechnicalApproachPreview:
    """Validated, mutation-free cohort-v2 material for receive preparation."""

    cover_letter: PreparedBodySet
    request_provenance: PreparedBodySet
    technical_approach: PreparedBodySet
    recipe: RootPreviewCarrierRecipe
    cover_projection: CodecArtifact
    provenance_projection: CodecArtifact
    approach_projection: CodecArtifact

    @property
    def canonical_cue(self) -> bytes:
        return self.technical_approach.canonical.data

    @property
    def body_sha256(self) -> str:
        return self.technical_approach.canonical.sha256

    @property
    def lifecycle_status(self) -> str:
        return "preview_only"

    @property
    def authorable(self) -> bool:
        return False

    def files(self) -> dict[str, str]:
        """Return an in-memory tree view; this method performs no I/O."""

        return {
            COVER_PATH: self.cover_letter.canonical.data.decode("utf-8", errors="strict"),
            PROVENANCE_PATH: self.request_provenance.canonical.data.decode(
                "utf-8", errors="strict"
            ),
            ROOT_APPROACH_PATH: self.technical_approach.canonical.data.decode(
                "utf-8", errors="strict"
            ),
        }

    @property
    def complete_cohort(self) -> bool:
        """Whether this preview is the exact atomic root cohort v2."""

        return tuple(self.files()) == ROOT_COHORT_V2_PATHS


@dataclass(frozen=True, slots=True)
class PreparedSeriesScopeDecomposition:
    """One mutation-free scope projection bound to an immutable root tree."""

    frozen_oid: str
    canonical_root_sha256: str
    body: Mapping[str, Any]
    prepared: PreparedBodySet

    @property
    def lifecycle_status(self) -> str:
        return "preview_only"

    @property
    def authorable(self) -> bool:
        return False

    @property
    def canonical_cue(self) -> bytes:
        return self.prepared.canonical.data

    @property
    def body_sha256(self) -> str:
        return self.prepared.canonical.sha256

    def files(self) -> dict[str, str]:
        """Render the preview coordinate in memory without publishing it."""

        return {
            SCOPE_DECOMPOSITION_PATH: self.canonical_cue.decode(
                "utf-8", errors="strict"
            )
        }


def prepare_cover_letter(value: Mapping[str, Any], *, client: BodyCodecClient | None = None) -> PreparedBodySet:
    codec = client or BodyCodecClient()
    return codec.prepare(COVER_CONTEXT, value, expected=COVER_CONTRACT)


def prepare_request_provenance(value: Mapping[str, Any], *, client: BodyCodecClient | None = None) -> PreparedBodySet:
    codec = client or BodyCodecClient()
    return codec.prepare(PROVENANCE_CONTEXT, value, expected=PROVENANCE_CONTRACT)


def prepare_root_technical_approach(
    value: Mapping[str, Any], *, client: BodyCodecClient | None = None
) -> PreparedBodySet:
    """Prepare one producer-aware root candidate without publishing it."""

    codec = client or BodyCodecClient()
    return codec.prepare(
        ROOT_APPROACH_CONTEXT, value, expected=ROOT_APPROACH_CONTRACT
    )


def _read_producer_lifecycle_body(
    raw: bytes | str,
    context: str,
    *,
    client: BodyCodecClient | None = None,
) -> dict[str, Any]:
    """Read one supplied lifecycle body without consulting or changing Git."""

    try:
        contract = _PRODUCER_LIFECYCLE_CONTRACTS[context]
    except KeyError as exc:
        raise ValueError(f"unregistered producer lifecycle context: {context}") from exc
    data = raw.encode("utf-8", errors="strict") if isinstance(raw, str) else raw
    value = (client or BodyCodecClient()).parse(context, data, expected=contract)
    if not isinstance(value, Mapping):
        raise ValueError(f"{context} projection must be an object")
    return dict(value)


def read_producer_promise(
    raw: bytes | str, *, client: BodyCodecClient | None = None
) -> dict[str, Any]:
    return _read_producer_lifecycle_body(
        raw, PRODUCER_PROMISE_CONTEXT, client=client
    )


def read_producer_task_binding(
    raw: bytes | str, *, client: BodyCodecClient | None = None
) -> dict[str, Any]:
    return _read_producer_lifecycle_body(
        raw,
        PRODUCER_TASK_BINDING_CONTEXT,
        client=client,
    )


def read_producer_completion_assertion(
    raw: bytes | str, *, client: BodyCodecClient | None = None
) -> dict[str, Any]:
    return _read_producer_lifecycle_body(
        raw,
        PRODUCER_COMPLETION_ASSERTION_CONTEXT,
        client=client,
    )


def read_claim_admission(
    raw: bytes | str, *, client: BodyCodecClient | None = None
) -> dict[str, Any]:
    return _read_producer_lifecycle_body(
        raw, CLAIM_ADMISSION_CONTEXT, client=client
    )


def read_functional_acceptance_verdict(
    raw: bytes | str, *, client: BodyCodecClient | None = None
) -> dict[str, Any]:
    return _read_producer_lifecycle_body(
        raw,
        FUNCTIONAL_ACCEPTANCE_CONTEXT,
        client=client,
    )


def read_acceptance_authority_seal(
    raw: bytes | str, *, client: BodyCodecClient | None = None
) -> dict[str, Any]:
    return _read_producer_lifecycle_body(
        raw,
        ACCEPTANCE_AUTHORITY_SEAL_CONTEXT,
        client=client,
    )


def prepare_root_technical_approach_preview(
    cover_letter: Mapping[str, Any],
    request_provenance: Mapping[str, Any],
    technical_approach: Mapping[str, Any],
    *,
    client: BodyCodecClient | None = None,
) -> PreparedRootTechnicalApproachPreview:
    """Preflight all three v2 members without any Git mutation.

    Codec failures intentionally cross this boundary unchanged.  Their stable
    context, field/CUE location, and rule coordinates are part of the preview
    diagnostic contract and must not be collapsed into a generic exception.
    """

    codec = client or BodyCodecClient()
    cover = prepare_cover_letter(cover_letter, client=codec)
    provenance = prepare_request_provenance(request_provenance, client=codec)
    approach = prepare_root_technical_approach(technical_approach, client=codec)
    cover_projection = codec.project(
        COVER_CONTEXT,
        cover.canonical.data,
        profile=PROJECTION_PROFILE,
        expected=COVER_CONTRACT,
    )
    provenance_projection = codec.project(
        PROVENANCE_CONTEXT,
        provenance.canonical.data,
        profile=PROJECTION_PROFILE,
        expected=PROVENANCE_CONTRACT,
    )
    approach_projection = codec.project(
        ROOT_APPROACH_CONTEXT,
        approach.canonical.data,
        profile=PROJECTION_PROFILE,
        expected=ROOT_APPROACH_CONTRACT,
    )
    return PreparedRootTechnicalApproachPreview(
        cover,
        provenance,
        approach,
        _root_preview_recipe(
            cover.canonical, provenance.canonical, approach.canonical
        ),
        cover_projection,
        provenance_projection,
        approach_projection,
    )


def project_series_scope_decomposition(
    repo: str | Path,
    ref: str,
    *,
    client: BodyCodecClient | None = None,
) -> PreparedSeriesScopeDecomposition:
    """Project final scope ownership from one frozen canonical-root tree.

    The canonical root and historical ``SeriesCoverageLedger`` are both read
    at the same immutable OID.  The ledger is compatibility input only: this
    function returns an in-memory ``series-scope-decomposition.cue`` preview
    and performs no filesystem, ref, index, or commit operation.
    """

    from ai_org import git_wrapper

    frozen = git_wrapper.head_sha(repo, ref)
    if frozen is None:
        raise SeriesScopeDecompositionError(
            "missing-frozen-root", "ref", "root ref is missing"
        )
    snapshot = _classify_root_generation_at_oid(repo, frozen)
    if snapshot.generation != ROOT_GENERATION_V2:
        raise SeriesScopeDecompositionError(
            "canonical-root-required", "technical-approach-plan.cue", snapshot.diagnostic
        )
    root_raw = snapshot.raw(ROOT_APPROACH_PATH)
    assert root_raw is not None

    ledger_entries = [
        (path, git_wrapper.read_tree_file(repo, frozen, path))
        for path in COVERAGE_LEDGER_INPUT_PATHS
    ]
    present_ledgers = [(path, raw) for path, raw in ledger_entries if raw is not None]
    if len(present_ledgers) != 1:
        rule = "historical-ledger-required" if not present_ledgers else "ambiguous-ledger"
        raise SeriesScopeDecompositionError(rule, LEGACY_COVERAGE_LEDGER_PATH)

    codec = client or BodyCodecClient()
    root_value = codec.parse(
        ROOT_APPROACH_CONTEXT, root_raw, expected=ROOT_APPROACH_CONTRACT
    )
    ledger_path, ledger_text = present_ledgers[0]
    assert ledger_text is not None
    ledger_value = codec.parse(
        "series-coverage-ledger-v1",
        ledger_text.encode("utf-8"),
        expected=COVERAGE_LEDGER_CONTRACT,
    )
    root = _legacy_json_value(root_value)
    ledger = _legacy_json_value(ledger_value)
    if not isinstance(root, Mapping) or not isinstance(ledger, Mapping):
        raise SeriesScopeDecompositionError("body-object", ledger_path)

    scope_raw = snapshot.raw(SCOPE_DECOMPOSITION_PATH)
    source_oid = frozen
    if scope_raw is not None:
        persisted = codec.parse(
            SCOPE_DECOMPOSITION_CONTEXT,
            scope_raw,
            expected=SCOPE_DECOMPOSITION_CONTRACT,
        )
        persisted = _legacy_json_value(persisted)
        if not isinstance(persisted, Mapping):
            raise SeriesScopeDecompositionError(
                "body-object", SCOPE_DECOMPOSITION_PATH
            )
        source_oid = str(persisted.get("frozen_root_oid") or "")
        _verify_persisted_scope_source(
            repo,
            snapshot_oid=frozen,
            source_oid=source_oid,
            canonical_root_raw=root_raw,
        )
    body, prepared = prepare_series_scope_decomposition(
        source_oid,
        root_raw,
        root,
        ledger,
        client=codec,
    )
    if scope_raw is not None and (
        dict(persisted) != body or prepared.canonical.data != scope_raw
    ):
        raise SeriesScopeDecompositionError(
            "durable-scope-mismatch", SCOPE_DECOMPOSITION_PATH
        )
    return PreparedSeriesScopeDecomposition(
        frozen,
        body["canonical_root_sha256"],
        body,
        prepared,
    )


def _verify_persisted_scope_source(
    repo: str | Path,
    *,
    snapshot_oid: str,
    source_oid: str,
    canonical_root_raw: bytes,
) -> None:
    """Bind a persisted preview to reachable, byte-identical root evidence."""

    from ai_org import git_wrapper

    coordinate = f"{SCOPE_DECOMPOSITION_PATH}/frozen_root_oid"
    if (
        re.fullmatch(r"[0-9a-f]{40}", source_oid) is None
        or not git_wrapper.is_ancestor(repo, source_oid, snapshot_oid)
    ):
        raise SeriesScopeDecompositionError(
            "frozen-root-lineage",
            coordinate,
            "the persisted canonical source must be an ancestor of the inspected snapshot",
        )
    source_root_raw = git_wrapper.show_file_bytes(repo, source_oid, ROOT_APPROACH_PATH)
    if source_root_raw != canonical_root_raw:
        raise SeriesScopeDecompositionError(
            "canonical-root-source-bytes",
            f"{SCOPE_DECOMPOSITION_PATH}/canonical_root_sha256",
            "the persisted source OID does not contain the projected canonical root bytes",
        )


def prepare_series_scope_decomposition(
    frozen_root_oid: str,
    root_raw: bytes,
    root: Mapping[str, Any],
    ledger: Mapping[str, Any],
    *,
    client: BodyCodecClient | None = None,
) -> tuple[dict[str, Any], PreparedBodySet]:
    """Preflight canonical scope for the network commit that will publish it."""

    body = _series_scope_decomposition_body(
        frozen_root_oid,
        hashlib.sha256(root_raw).hexdigest(),
        root,
        ledger,
    )
    prepared = (client or BodyCodecClient()).prepare(
        SCOPE_DECOMPOSITION_CONTEXT,
        body,
        expected=SCOPE_DECOMPOSITION_CONTRACT,
    )
    return body, prepared


def _series_scope_decomposition_body(
    frozen_oid: str,
    root_sha256: str,
    root: Mapping[str, Any],
    ledger: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the closed value while retaining every historical scope row."""

    if re.fullmatch(r"[0-9a-f]{40}", frozen_oid) is None:
        raise SeriesScopeDecompositionError(
            "frozen-root-identity", "technical-approach-plan.cue/frozen_root_oid"
        )
    if re.fullmatch(r"[0-9a-f]{64}", root_sha256) is None:
        raise SeriesScopeDecompositionError(
            "canonical-root-digest", "technical-approach-plan.cue/sha256"
        )
    if ledger.get("schema") != "series-coverage-ledger-v1":
        raise SeriesScopeDecompositionError(
            "historical-ledger-schema", "series-coverage-ledger.json/schema"
        )
    raw_scope = ledger.get("scope_items")
    raw_coverage = ledger.get("coverage")
    if not isinstance(raw_scope, list) or not isinstance(raw_coverage, list):
        raise SeriesScopeDecompositionError(
            "historical-ledger-shape", "series-coverage-ledger.json"
        )

    # ``goal`` is the strict historical spelling used by already-published
    # ledgers. New ledgers distinguish the request outcome from the normalized
    # referee goals, but the projector must preserve either representation
    # verbatim instead of silently rewriting history.
    established_kinds = frozenset(ESTABLISHED_SERIES_SCOPE_CATEGORIES)
    scope_items: list[dict[str, Any]] = []
    scope_ids: set[str] = set()
    scope_kind_by_id: dict[str, str] = {}
    for index, item in enumerate(raw_scope):
        coordinate = f"series-coverage-ledger.json/scope_items/{index}"
        if not isinstance(item, Mapping):
            raise SeriesScopeDecompositionError("scope-item-object", coordinate)
        # Historical rows are compatibility input, not a loose source from
        # which a new row may be synthesized.  Reject surplus fields instead
        # of silently dropping them while constructing the final closed body.
        if set(item) != {"id", "kind", "text"}:
            raise SeriesScopeDecompositionError(
                "established-scope-shape", coordinate
            )
        row = {field: item.get(field) for field in ("id", "kind", "text")}
        if (
            not all(isinstance(row[field], str) and row[field] for field in row)
            or row["kind"] not in established_kinds
        ):
            raise SeriesScopeDecompositionError(
                "established-scope-category", coordinate
            )
        if row["id"] in scope_ids:
            raise SeriesScopeDecompositionError("duplicate-scope-id", coordinate)
        scope_ids.add(row["id"])
        scope_kind_by_id[row["id"]] = row["kind"]
        scope_items.append(row)

    ownership_by_scope: dict[str, dict[str, str]] = {}
    for index, owner in enumerate(raw_coverage):
        coordinate = f"series-coverage-ledger.json/coverage/{index}"
        if not isinstance(owner, Mapping):
            raise SeriesScopeDecompositionError("ownership-object", coordinate)
        if set(owner) != {"scope_item_id", "owner", "owner_node_path"}:
            raise SeriesScopeDecompositionError("ownership-shape", coordinate)
        scope_id = owner.get("scope_item_id")
        row = {
            field: owner.get(field)
            for field in ("scope_item_id", "owner", "owner_node_path")
        }
        if not all(isinstance(row[field], str) and row[field] for field in row):
            raise SeriesScopeDecompositionError("ownership-shape", coordinate)
        assert isinstance(scope_id, str)
        if scope_id not in scope_ids:
            raise SeriesScopeDecompositionError("fabricated-ownership", coordinate)
        if scope_id in ownership_by_scope:
            raise SeriesScopeDecompositionError("duplicate-ownership", coordinate)
        ownership_by_scope[scope_id] = row  # type: ignore[assignment]
    missing_owners = sorted(scope_ids - ownership_by_scope.keys())
    if missing_owners:
        raise SeriesScopeDecompositionError(
            "missing-ownership", missing_owners[0]
        )

    problem = root.get("problem")
    if not isinstance(problem, Mapping):
        raise SeriesScopeDecompositionError("root-problem", "root/problem")
    goals = problem.get("goals")
    requirements = problem.get("deliverable_requirements")
    obligations = problem.get("production_obligations")
    assignments = problem.get("patch_plan")
    if not all(
        isinstance(value, list)
        for value in (goals, requirements, obligations, assignments)
    ):
        raise SeriesScopeDecompositionError("root-producer-scope", "root/problem")

    goal_ids: set[str] = set()
    referee_goals: set[str] = set()
    for index, goal in enumerate(goals):
        coordinate = f"root/problem/goals/{index}"
        if not isinstance(goal, Mapping):
            raise SeriesScopeDecompositionError("referee-goal-object", coordinate)
        goal_id = goal.get("id")
        requires_deliverable = goal.get("requires_deliverable")
        if (
            not isinstance(goal_id, str)
            or not goal_id
            or goal_id in goal_ids
            or not isinstance(requires_deliverable, bool)
        ):
            raise SeriesScopeDecompositionError("referee-goal-identity", coordinate)
        goal_ids.add(goal_id)
        if requires_deliverable:
            referee_goals.add(goal_id)
            if scope_kind_by_id.get(goal_id) not in {"referee_goal", "goal"}:
                raise SeriesScopeDecompositionError(
                    "obligation-referee-association",
                    coordinate,
                    "each referee goal must retain its historical scope row",
                )

    requirement_by_id: dict[str, Mapping[str, Any]] = {}
    for index, requirement in enumerate(requirements):
        coordinate = f"root/problem/deliverable_requirements/{index}"
        if not isinstance(requirement, Mapping):
            raise SeriesScopeDecompositionError("requirement-object", coordinate)
        if frozenset(requirement) != _ROOT_DELIVERABLE_REQUIREMENT_FIELDS:
            raise SeriesScopeDecompositionError("requirement-shape", coordinate)
        requirement_id = requirement.get("id")
        if (
            not isinstance(requirement_id, str)
            or not requirement_id
            or requirement_id in requirement_by_id
        ):
            raise SeriesScopeDecompositionError("requirement-identity", coordinate)
        requirement_by_id[requirement_id] = requirement

    assignment_by_obligation: dict[str, str] = {}
    for index, assignment in enumerate(assignments):
        coordinate = f"root/problem/patch_plan/{index}"
        if not isinstance(assignment, Mapping):
            raise SeriesScopeDecompositionError(
                "patch-plan-assignment", coordinate
            )
        if frozenset(assignment) != _ROOT_PATCH_PLAN_ASSIGNMENT_FIELDS:
            raise SeriesScopeDecompositionError(
                "patch-plan-assignment-shape", coordinate
            )
        item_id = assignment.get("item_id")
        obligation_ids = assignment.get("production_obligation_ids")
        if not isinstance(item_id, str) or not item_id or not isinstance(obligation_ids, list):
            raise SeriesScopeDecompositionError(
                "patch-plan-assignment", coordinate
            )
        for obligation_id in obligation_ids:
            if (
                not isinstance(obligation_id, str)
                or not obligation_id
                or obligation_id in assignment_by_obligation
            ):
                raise SeriesScopeDecompositionError(
                    "inexact-obligation-assignment", coordinate
                )
            assignment_by_obligation[obligation_id] = item_id

    historical_scope_count = len(scope_items)
    historical_ownership_count = len(ownership_by_scope)
    associated_referee_goals: set[str] = set()
    associated_requirement_ids: set[str] = set()
    for index, obligation in enumerate(obligations):
        coordinate = f"root/problem/production_obligations/{index}"
        if not isinstance(obligation, Mapping):
            raise SeriesScopeDecompositionError("obligation-object", coordinate)
        obligation_fields = frozenset(obligation)
        if (
            not _ROOT_PRODUCTION_OBLIGATION_REQUIRED_FIELDS <= obligation_fields
            or not obligation_fields <= _ROOT_PRODUCTION_OBLIGATION_FIELDS
        ):
            raise SeriesScopeDecompositionError("obligation-shape", coordinate)
        obligation_id = obligation.get("id")
        referee_goal_id = obligation.get("referee_goal_id")
        requirement_id = obligation.get("deliverable_requirement_id")
        deliverable = obligation.get("deliverable")
        predicate = obligation.get("eligibility_predicate")
        if not all(
            isinstance(value, str) and value
            for value in (
                obligation_id,
                referee_goal_id,
                requirement_id,
                deliverable,
                predicate,
            )
        ):
            raise SeriesScopeDecompositionError("obligation-shape", coordinate)
        assert isinstance(obligation_id, str) and isinstance(referee_goal_id, str)
        assert isinstance(requirement_id, str)
        requirement = requirement_by_id.get(requirement_id)
        if (
            referee_goal_id not in referee_goals
            or requirement is None
            or referee_goal_id in associated_referee_goals
            or requirement_id in associated_requirement_ids
            or requirement.get("referee_goal_id") != referee_goal_id
            or requirement.get("production_obligation_id") != obligation_id
            or requirement.get("deliverable") != deliverable
        ):
            raise SeriesScopeDecompositionError(
                "obligation-referee-association", coordinate
            )
        associated_referee_goals.add(referee_goal_id)
        associated_requirement_ids.add(requirement_id)
        patch_item_id = assignment_by_obligation.pop(obligation_id, None)
        if patch_item_id is None:
            raise SeriesScopeDecompositionError(
                "missing-obligation-assignment", coordinate
            )
        if obligation_id in scope_ids:
            raise SeriesScopeDecompositionError("duplicate-scope-id", coordinate)
        owner_scope_id = _historical_patch_plan_scope_id(
            patch_item_id, ownership_by_scope
        )
        if (
            owner_scope_id is None
            or scope_kind_by_id.get(owner_scope_id) != "patch_plan"
        ):
            raise SeriesScopeDecompositionError(
                "patch-plan-owner", patch_item_id,
                "assigned patch item has no historical ownership row",
            )
        owner = ownership_by_scope[owner_scope_id]
        scope_items.append(
            {
                "id": obligation_id,
                "kind": "production_obligation",
                "text": deliverable,
                "production_obligation_id": obligation_id,
                "referee_goal_id": referee_goal_id,
                "deliverable_requirement_id": requirement_id,
                "patch_plan_item_id": patch_item_id,
                "deliverable": deliverable,
                "eligibility_predicate": predicate,
            }
        )
        ownership_by_scope[obligation_id] = {
            "scope_item_id": obligation_id,
            "owner": owner["owner"],
            "owner_node_path": owner["owner_node_path"],
        }
        scope_ids.add(obligation_id)
    if assignment_by_obligation:
        raise SeriesScopeDecompositionError(
            "fabricated-obligation-assignment", sorted(assignment_by_obligation)[0]
        )
    if (
        associated_referee_goals != referee_goals
        or associated_requirement_ids != requirement_by_id.keys()
    ):
        raise SeriesScopeDecompositionError(
            "obligation-referee-association",
            "root/problem/production_obligations",
            "producer-aware goals and requirements must project exactly once",
        )
    obligation_count = len(obligations)
    if len(scope_items) != historical_scope_count + obligation_count:
        raise SeriesScopeDecompositionError(
            "obligation-row-delta",
            "series-scope-decomposition.cue/scope_items",
        )
    if len(ownership_by_scope) != historical_ownership_count + obligation_count:
        raise SeriesScopeDecompositionError(
            "ownership-row-delta",
            "series-scope-decomposition.cue/ownership",
        )

    revision = ledger.get("ledger_revision")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
        raise SeriesScopeDecompositionError(
            "ledger-revision", "series-coverage-ledger.json/ledger_revision"
        )
    supersedes = ledger.get("supersedes_ledger_commit")
    if (
        not isinstance(supersedes, str)
        or (supersedes and re.fullmatch(r"[0-9a-f]{40}", supersedes) is None)
    ):
        raise SeriesScopeDecompositionError(
            "ledger-lineage", "series-coverage-ledger.json/supersedes_ledger_commit"
        )
    if (revision == 1 and supersedes) or (revision > 1 and not supersedes):
        raise SeriesScopeDecompositionError(
            "ledger-lineage",
            "series-coverage-ledger.json/supersedes_ledger_commit",
            "revision one has no predecessor; every later revision retains one",
        )
    escalation = _closed_escalation_consumption(
        ledger.get("rebaselined_from_escalation")
    )
    retained = ledger.get("parent_retained_scope_ids")
    if not isinstance(retained, list):
        raise SeriesScopeDecompositionError(
            "parent-retained-scope", "series-coverage-ledger.json/parent_retained_scope_ids"
        )
    retained_ids: set[str] = set()
    for index, item in enumerate(retained):
        coordinate = f"series-coverage-ledger.json/parent_retained_scope_ids/{index}"
        if not isinstance(item, str) or item not in scope_ids:
            raise SeriesScopeDecompositionError("parent-retained-scope", coordinate)
        if item in retained_ids:
            raise SeriesScopeDecompositionError(
                "duplicate-parent-retained-scope", coordinate
            )
        retained_ids.add(item)

    return {
        "schema": "series-scope-decomposition-v1",
        "frozen_root_oid": frozen_oid,
        "canonical_root_sha256": root_sha256,
        "historical_ledger_schema": "series-coverage-ledger-v1",
        "ledger_revision": revision,
        "supersedes_ledger_commit": supersedes,
        "rebaselined_from_escalation": escalation,
        "scope_items": scope_items,
        "ownership": list(ownership_by_scope.values()),
        "parent_retained_scope_ids": list(retained),
    }


def _historical_patch_plan_scope_id(
    item_id: str, ownership: Mapping[str, Any]
) -> str | None:
    """Resolve canonical item identities onto established ledger coordinates."""

    candidates = [item_id]
    suffix = item_id.rsplit("#", 1)[-1]
    normalized = suffix.lower().replace("-", "_")
    if normalized in {"first_proof_moment", "first_proof"}:
        candidates.append("patch_plan:first_proof_moment")
    if normalized.startswith("follow_up_"):
        ordinal = normalized.removeprefix("follow_up_")
        if ordinal.isdigit():
            candidates.append(f"patch_plan:follow_up:{int(ordinal)}")
    if normalized.startswith("deferred_"):
        ordinal = normalized.removeprefix("deferred_")
        if ordinal.isdigit():
            candidates.append(f"patch_plan:deferred:{int(ordinal)}")
    return next((candidate for candidate in candidates if candidate in ownership), None)


def _closed_escalation_consumption(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise SeriesScopeDecompositionError(
            "escalation-consumption", "series-coverage-ledger.json/rebaselined_from_escalation"
        )
    if not value:
        return {}
    fields = frozenset(ESCALATION_CONSUMPTION_FIELDS)
    legacy_fields = frozenset(HISTORICAL_ESCALATION_CONSUMPTION_FIELDS)
    if frozenset(value) not in {fields, legacy_fields}:
        raise SeriesScopeDecompositionError(
            "escalation-consumption", "series-coverage-ledger.json/rebaselined_from_escalation",
            "the complete review_round/child_branch/git_result_commit tuple is required",
        )
    review_round = value.get("review_round")
    child_branch = value.get("child_branch")
    result_commit = value.get("git_result_commit")
    review_record_path = value.get("review_record_path")
    if (
        not isinstance(review_round, int)
        or isinstance(review_round, bool)
        or review_round < 1
        or not isinstance(child_branch, str)
        or not child_branch
        or not isinstance(result_commit, str)
        or len(result_commit) != 40
        or any(character not in "0123456789abcdef" for character in result_commit)
        or (
            review_record_path is not None
            and (
                not isinstance(review_record_path, str)
                or review_record_path
                != f"patch-series-review-rounds/round-{review_round:04d}-direction-review-record.cue"
            )
        )
    ):
        raise SeriesScopeDecompositionError(
            "escalation-consumption", "series-coverage-ledger.json/rebaselined_from_escalation"
        )
    return dict(
        zip(
            ESCALATION_CONSUMPTION_FIELDS,
            (review_round, child_branch, result_commit),
            strict=True,
        )
    )


def classify_root_generation(repo: str | Path, ref: str) -> RootGenerationSnapshot:
    """Classify all root representations from exactly one frozen Git tree.

    Historical roots remain on the legacy dispatcher.  A complete three-CUE
    cohort is recognized without legacy fallback; the already-admitted
    canonical-scope predicate may independently make it lifecycle-ready.
    Alias overlap or any other partial cohort fails closed.
    """

    from ai_org import git_wrapper

    frozen = git_wrapper.head_sha(repo, ref)
    if frozen is None:
        return RootGenerationSnapshot(
            None,
            ROOT_GENERATION_INVALID,
            ROOT_DISPOSITION_INVALID,
            tuple((path, None) for path in _ROOT_GENERATION_PATHS),
            "root ref is missing",
        )
    return _classify_root_generation_at_oid(repo, frozen)


def _classify_root_generation_at_oid(
    repo: str | Path, frozen: str
) -> RootGenerationSnapshot:
    """Classify an OID that the caller has already frozen."""

    from ai_org import git_wrapper

    members = tuple(
        (
            path,
            raw.encode("utf-8") if (raw := git_wrapper.read_tree_file(repo, frozen, path)) is not None else None,
        )
        for path in _ROOT_GENERATION_PATHS
    )
    present = {path for path, raw in members if raw is not None}
    representation_paths = present - {SCOPE_DECOMPOSITION_PATH}

    generation = _classify_root_generation_shape(representation_paths)
    if (
        generation == ROOT_GENERATION_HISTORICAL
        and SCOPE_DECOMPOSITION_PATH not in present
    ):
        return RootGenerationSnapshot(
            frozen,
            generation,
            ROOT_DISPOSITION_LEGACY_READY,
            members,
        )
    if (
        generation == ROOT_GENERATION_CURRENT
        and SCOPE_DECOMPOSITION_PATH not in present
    ):
        return RootGenerationSnapshot(
            frozen,
            generation,
            ROOT_DISPOSITION_CURRENT_READY,
            members,
        )
    if generation == ROOT_GENERATION_V2:
        if SCOPE_DECOMPOSITION_PATH in present:
            return RootGenerationSnapshot(
                frozen,
                generation,
                ROOT_DISPOSITION_V2_READY,
                members,
            )
        return RootGenerationSnapshot(
            frozen,
            generation,
            ROOT_DISPOSITION_V2_NOT_READY,
            members,
            "complete root cohort v2 awaits canonical scope publication",
        )
    paths = ", ".join(sorted(present)) or "<none>"
    return RootGenerationSnapshot(
        frozen,
        ROOT_GENERATION_INVALID,
        ROOT_DISPOSITION_INVALID,
        members,
        f"mixed or incomplete root representations: {paths}",
    )


def _classify_root_generation_shape(representation_paths: set[str]) -> str:
    """Return the closed representation shape before applying readiness gates."""

    if not representation_paths <= set(_ROOT_REPRESENTATION_PATHS):
        return ROOT_GENERATION_INVALID
    matches = tuple(
        generation
        for generation, recipe in ROOT_GENERATION_RECIPES.items()
        if recipe.matches(representation_paths)
    )
    return matches[0] if len(matches) == 1 else ROOT_GENERATION_INVALID


def read_root_inputs(
    repo: str | Path,
    ref: str,
    *,
    client: BodyCodecClient | None = None,
) -> tuple[RootGenerationSnapshot, dict[str, Any], dict[str, Any]]:
    """Read the cover and approach selected by one generation snapshot."""

    snapshot = classify_root_generation(repo, ref)
    snapshot.require_lifecycle_ready()
    return (
        snapshot,
        snapshot.cover_letter(client=client),
        snapshot.technical_approach(),
    )


def prepare_patch_series_cohort(
    cover_letter: Mapping[str, Any],
    request_provenance: Mapping[str, Any],
    *,
    client: BodyCodecClient | None = None,
) -> PreparedPatchSeriesCohort:
    """Preflight both changed members before callers perform any Git mutation."""
    codec = client or BodyCodecClient()
    cover = prepare_cover_letter(cover_letter, client=codec)
    provenance = prepare_request_provenance(request_provenance, client=codec)
    cover_projection = codec.project(
        COVER_CONTEXT, cover.canonical.data, profile=PROJECTION_PROFILE, expected=COVER_CONTRACT
    )
    provenance_projection = codec.project(
        PROVENANCE_CONTEXT,
        provenance.canonical.data,
        profile=PROJECTION_PROFILE,
        expected=PROVENANCE_CONTRACT,
    )
    schema = codec.schema(COVER_CONTEXT, profile=SCHEMA_PROFILE, expected=COVER_CONTRACT)
    return PreparedPatchSeriesCohort(
        cover,
        provenance,
        _recipe(cover.canonical, provenance.canonical, schema),
        cover_projection,
        provenance_projection,
        schema,
    )


def prepare_promotion_cohort(
    patch_series_path: str,
    cover_letter: Mapping[str, Any],
    extra_files: Mapping[str, Any],
    *,
    client: BodyCodecClient | None = None,
) -> tuple[
    PreparedPatchSeriesCohort | PreparedRootTechnicalApproachPreview | None,
    dict[str, Any],
]:
    """Select and preflight the root cohort before publication can mutate Git.

    Canonical serialized bodies are outputs of this boundary, never an
    alternate ingress API. A mapping supplied at the explicit canonical
    coordinate is still off-Git request input: this function consumes it and
    regenerates both canonical members through the ordered carrier and exact
    schema checks in :func:`prepare_patch_series_cohort`. Serialized bytes at
    that coordinate are rejected so they cannot bypass preflight.
    """

    pending = dict(extra_files)
    legacy_provenance = pending.get(LEGACY_PROVENANCE_PATH)
    canonical_provenance = pending.get(PROVENANCE_PATH)
    if LEGACY_PROVENANCE_PATH in pending and PROVENANCE_PATH in pending:
        raise ValueError("patch-series provenance must have exactly one representation")

    if not is_root_cover_path(patch_series_path):
        if PROVENANCE_PATH in pending:
            raise ValueError(
                "canonical patch-series provenance must be produced by paired cohort preflight"
            )
        return None, pending

    # The historical writer remains historical. Supplying the stable JSON
    # coordinates is not an implicit migration request; explicit canonical
    # coordinates (or the complete root-v2 cohort below) are the promotion
    # boundary. This also keeps duplicate-representation detection observable
    # to readers of old trees.
    if (
        patch_series_path == LEGACY_COVER_PATH
        and LEGACY_PROVENANCE_PATH in pending
        and PROVENANCE_PATH not in pending
        and ROOT_APPROACH_PATH not in pending
    ):
        return None, pending

    provenance_path = (
        PROVENANCE_PATH if PROVENANCE_PATH in pending else LEGACY_PROVENANCE_PATH
    )
    provenance = (
        canonical_provenance
        if provenance_path == PROVENANCE_PATH
        else legacy_provenance
    )
    if not isinstance(provenance, Mapping):
        if PROVENANCE_PATH in pending:
            raise ValueError(
                "canonical patch-series provenance must be produced by paired cohort preflight"
            )
        if patch_series_path == COVER_PATH:
            raise ValueError(
                "canonical patch-series cover requires request provenance; "
                "publication requires paired provenance"
            )
        return None, pending

    pending.pop(provenance_path)
    canonical_root = pending.get(ROOT_APPROACH_PATH)
    legacy_root = pending.get(LEGACY_ROOT_APPROACH_PATH)
    if canonical_root is not None and legacy_root is not None:
        raise ValueError("root technical approach must have exactly one representation")
    if canonical_root is not None:
        if provenance_path != PROVENANCE_PATH:
            raise ValueError(
                "canonical root technical approach requires the exact root cohort v2"
            )
        supplied_cover = pending.pop(COVER_PATH, None)
        codec = client or BodyCodecClient()
        prepared_pair = prepare_patch_series_cohort(
            cover_letter, provenance, client=codec
        )
        expected_cover = prepared_pair.files()[COVER_PATH]
        if supplied_cover is not None and supplied_cover != expected_cover:
            raise ValueError("canonical root cohort cover does not match promotion input")
        if isinstance(canonical_root, Mapping):
            approach = canonical_root
        else:
            raw_root = (
                canonical_root.encode("utf-8", errors="strict")
                if isinstance(canonical_root, str)
                else canonical_root
            )
            if not isinstance(raw_root, bytes):
                raise TypeError(
                    "canonical root technical approach must contain a mapping or canonical bytes"
                )
            approach = codec.parse(
                ROOT_APPROACH_CONTEXT,
                raw_root,
                expected=ROOT_APPROACH_CONTRACT,
            )
        pending.pop(ROOT_APPROACH_PATH)
        return (
            prepare_root_technical_approach_preview(
                cover_letter,
                provenance,
                approach,
                client=codec,
            ),
            pending,
        )
    return prepare_patch_series_cohort(cover_letter, provenance, client=client), pending


def prepare_patch_series_update(
    repo: str | Path,
    ref: str,
    cover_letter: Mapping[str, Any],
    *,
    client: BodyCodecClient | None = None,
) -> PreparedPatchSeriesCohort:
    """Preflight a cover amendment together with its immutable provenance."""
    codec = client or BodyCodecClient()
    provenance = read_request_provenance(repo, ref, client=codec)
    if provenance is None:
        raise ValueError("patch-series provenance is required for a canonical cover update")
    return prepare_patch_series_cohort(cover_letter, provenance, client=codec)


def is_root_cover_path(path: str) -> bool:
    """Return whether *path* names either representation of the root cover.

    Callers continue to use the historical JSON name as their stable logical
    address.  The canonical CUE name is also accepted at consumer boundaries
    so an explicitly selected durable path cannot fall through to JSON parsing.
    Nested cover paths are intentionally excluded: their cohorts have not been
    admitted yet and must retain their single legacy representation.
    """
    return path in {COVER_PATH, LEGACY_COVER_PATH}


def read_cover_letter(
    repo: str | Path,
    ref: str,
    *,
    client: BodyCodecClient | None = None,
    root_snapshot: RootGenerationSnapshot | None = None,
) -> dict[str, Any] | None:
    """Read a root cover from one classifier snapshot.

    Readers that already froze and classified their root pass ``root_snapshot``
    so member-only compatibility projection cannot trigger a second classifier
    invocation.  The snapshot's OID, rather than the possibly moving ``ref``,
    remains authoritative.
    """
    from ai_org import git_wrapper

    frozen = (
        root_snapshot.frozen_oid
        if root_snapshot is not None
        else git_wrapper.head_sha(repo, ref)
    )
    if frozen is None:
        return None
    generation = root_snapshot or _classify_root_generation_at_oid(repo, frozen)
    if _generation_controls_lifecycle(generation):
        generation.require_lifecycle_ready()
        return generation.cover_letter(client=client)
    paired = _read_patch_series_cohort_at_oid(repo, frozen, client=client)
    if paired is not None:
        return dict(paired.cover_letter)
    return _read_member_at_oid(
        git_wrapper,
        repo,
        frozen,
        COVER_PATH,
        LEGACY_COVER_PATH,
        COVER_CONTEXT,
        COVER_CONTRACT,
        client,
    )


def read_request_provenance(
    repo: str | Path,
    ref: str,
    *,
    client: BodyCodecClient | None = None,
    root_snapshot: RootGenerationSnapshot | None = None,
) -> dict[str, Any] | None:
    """Read provenance using a caller's frozen classifier snapshot when given."""
    from ai_org import git_wrapper

    frozen = (
        root_snapshot.frozen_oid
        if root_snapshot is not None
        else git_wrapper.head_sha(repo, ref)
    )
    if frozen is None:
        return None
    generation = root_snapshot or _classify_root_generation_at_oid(repo, frozen)
    if _generation_controls_lifecycle(generation):
        generation.require_lifecycle_ready()
    paired = _read_patch_series_cohort_at_oid(repo, frozen, client=client)
    if paired is not None:
        return dict(paired.request_provenance)
    return _read_member_at_oid(
        git_wrapper,
        repo,
        frozen,
        PROVENANCE_PATH,
        LEGACY_PROVENANCE_PATH,
        PROVENANCE_CONTEXT,
        PROVENANCE_CONTRACT,
        client,
    )


def is_canonical_cohort(
    repo: str | Path,
    ref: str,
    *,
    require_complete: bool = True,
    root_snapshot: RootGenerationSnapshot | None = None,
) -> bool:
    """Inspect canonical carrier presence through one root snapshot.

    This compatibility predicate is intentionally presence-only, but it is
    still a root reader: derive its answer from a supplied immutable snapshot
    or classify the ref once. It cannot turn a partial or mixed tree into an
    admitted lifecycle generation.
    """

    snapshot = root_snapshot or classify_root_generation(repo, ref)
    if snapshot.frozen_oid is None:
        return False
    cover = snapshot.raw(COVER_PATH) is not None
    provenance = snapshot.raw(PROVENANCE_PATH) is not None
    return cover and provenance if require_complete else cover or provenance


def read_patch_series_cohort(
    repo: str | Path,
    ref: str,
    *,
    client: BodyCodecClient | None = None,
    root_snapshot: RootGenerationSnapshot | None = None,
) -> PairedProjectionResult | None:
    """Read the paired root cohort from one supplied or newly frozen snapshot."""
    from ai_org import git_wrapper

    frozen = (
        root_snapshot.frozen_oid
        if root_snapshot is not None
        else git_wrapper.head_sha(repo, ref)
    )
    if frozen is None:
        return None
    generation = root_snapshot or _classify_root_generation_at_oid(repo, frozen)
    if _generation_controls_lifecycle(generation):
        generation.require_lifecycle_ready()
    return _read_patch_series_cohort_at_oid(repo, frozen, client=client)


def _generation_controls_lifecycle(snapshot: RootGenerationSnapshot) -> bool:
    """Keep member-only historical imports while gating a claimed root tree."""

    return (
        snapshot.raw(ROOT_APPROACH_PATH) is not None
        or snapshot.raw(LEGACY_ROOT_APPROACH_PATH) is not None
    )


def _read_patch_series_cohort_at_oid(
    repo: str | Path, frozen: str, *, client: BodyCodecClient | None = None
) -> PairedProjectionResult | None:
    """Project both members from one already-frozen immutable Git tree."""

    from ai_org import git_wrapper

    cover_entry = _raw_member(git_wrapper, repo, frozen, COVER_PATH, LEGACY_COVER_PATH)
    provenance_entry = _raw_member(
        git_wrapper, repo, frozen, PROVENANCE_PATH, LEGACY_PROVENANCE_PATH
    )
    canonical_cover = cover_entry is not None and cover_entry[0] == COVER_PATH
    canonical_provenance = (
        provenance_entry is not None and provenance_entry[0] == PROVENANCE_PATH
    )
    if (canonical_cover or canonical_provenance) and not (
        canonical_cover and canonical_provenance
    ):
        raise ValueError("canonical patch-series cohort is incomplete")
    if cover_entry is None or provenance_entry is None:
        return None
    cover_raw = cover_entry[1]
    provenance_raw = provenance_entry[1]
    codec = client or BodyCodecClient()
    cover_projection = codec.project(
        COVER_CONTEXT, cover_raw, profile=PROJECTION_PROFILE, expected=COVER_CONTRACT
    )
    provenance_projection = codec.project(
        PROVENANCE_CONTEXT,
        provenance_raw,
        profile=PROJECTION_PROFILE,
        expected=PROVENANCE_CONTRACT,
    )
    schema = codec.schema(COVER_CONTEXT, profile=SCHEMA_PROFILE, expected=COVER_CONTRACT)
    tree_oid = git_wrapper.tree_sha(repo, frozen)
    if tree_oid is None:
        raise ValueError("patch-series carrier publication tree is unavailable")
    return PairedProjectionResult(
        cover_letter=_projection_object(cover_projection, COVER_CONTEXT),
        request_provenance=_projection_object(provenance_projection, PROVENANCE_CONTEXT),
        recipe=_recipe(_artifact(cover_raw), _artifact(provenance_raw), schema),
        exact_schema=schema,
        publication=ImmutableGitTree(commit_oid=frozen, tree_oid=tree_oid),
    )


def _read_member_at_oid(
    git_wrapper, repo, frozen, canonical_path, legacy_path, context, contract, client
):
    entry = _raw_member(git_wrapper, repo, frozen, canonical_path, legacy_path)
    if entry is None:
        return None
    _path, raw = entry
    return (client or BodyCodecClient()).parse(context, raw, expected=contract)


def _raw_member(
    git_wrapper, repo, ref, canonical_path, legacy_path
) -> tuple[str, bytes] | None:
    canonical = git_wrapper.show_file(repo, ref, canonical_path)
    historical = git_wrapper.show_file(repo, ref, legacy_path)
    if canonical is not None and historical is not None:
        raise ValueError("patch-series cohort must retain exactly one representation")
    if canonical is not None:
        return canonical_path, canonical.encode("utf-8")
    return (
        (legacy_path, historical.encode("utf-8"))
        if historical is not None
        else None
    )


def _projection_object(artifact: CodecArtifact, context: str) -> dict[str, Any]:
    value = strict_json_loads(artifact.data)
    if not isinstance(value, dict):
        raise ValueError(f"{context} projection must be an object")
    return value


def _legacy_json_value(value: Any) -> Any:
    """Restore the ordinary-number view used by historical JSON readers."""

    if isinstance(value, ExactJSONNumber):
        lexeme = value.lexeme
        return float(lexeme) if any(marker in lexeme for marker in ".eE") else int(lexeme)
    if isinstance(value, Mapping):
        return {str(key): _legacy_json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_legacy_json_value(item) for item in value]
    return value


def _artifact(data: bytes) -> CodecArtifact:
    return CodecArtifact("application/octet-stream", data, len(data), hashlib.sha256(data).hexdigest())


def _require_sha256(value: str, coordinate: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"grounding carrier {coordinate} digest must be exact sha256")


def _require_git_oid(value: str, coordinate: str) -> None:
    if len(value) not in {40, 64} or any(
        character not in "0123456789abcdef" for character in value
    ):
        raise ValueError(
            f"grounding carrier publication {coordinate} must be an exact Git object id"
        )


def _require_profile_artifact(
    artifact: CodecArtifact, media_type: str, profile: str, coordinate: str
) -> None:
    if artifact.media_type != f"{media_type}; profile={profile}":
        raise ValueError(
            f"grounding carrier {coordinate} artifact does not match bound profile"
        )


def _recipe(cover: CodecArtifact, provenance: CodecArtifact, schema: CodecArtifact) -> GroundingCarrierRecipe:
    return GroundingCarrierRecipe(
        sources=(
            CarrierMember(COVER_CONTEXT, COVER_CONTRACT, PROJECTION_PROFILE, cover.sha256),
            CarrierMember(PROVENANCE_CONTEXT, PROVENANCE_CONTRACT, PROJECTION_PROFILE, provenance.sha256),
        ),
        target_context=COVER_CONTEXT,
        target_contract=COVER_CONTRACT,
        schema_profile=SCHEMA_PROFILE,
        schema_sha256=schema.sha256,
    )


def _root_preview_recipe(
    cover: CodecArtifact,
    provenance: CodecArtifact,
    technical_approach: CodecArtifact,
) -> RootPreviewCarrierRecipe:
    return RootPreviewCarrierRecipe(
        members=(
            CarrierMember(
                COVER_CONTEXT, COVER_CONTRACT, PROJECTION_PROFILE, cover.sha256
            ),
            CarrierMember(
                PROVENANCE_CONTEXT,
                PROVENANCE_CONTRACT,
                PROJECTION_PROFILE,
                provenance.sha256,
            ),
            CarrierMember(
                ROOT_APPROACH_CONTEXT,
                ROOT_APPROACH_CONTRACT,
                PROJECTION_PROFILE,
                technical_approach.sha256,
            ),
        )
    )


__all__ = [
    "COVER_CONTEXT", "COVER_PATH", "LEGACY_COVER_PATH", "PROVENANCE_CONTEXT", "PROVENANCE_PATH",
    "ROOT_APPROACH_CONTEXT", "ROOT_APPROACH_PATH", "LEGACY_ROOT_APPROACH_PATH",
    "SCOPE_DECOMPOSITION_CONTEXT", "SCOPE_DECOMPOSITION_PATH",
    "COVERAGE_LEDGER_INPUT_PATHS", "ESTABLISHED_SERIES_SCOPE_CATEGORIES",
    "ROOT_APPROACH_CONTRACT", "ROOT_COHORT_V2_MANIFEST",
    "ROOT_GENERATION_HISTORICAL", "ROOT_GENERATION_CURRENT", "ROOT_GENERATION_V2",
    "ROOT_GENERATION_INVALID", "ROOT_GENERATION_RECIPES",
    "RootGenerationRecipe", "ROOT_DISPOSITION_LEGACY_READY",
    "ROOT_DISPOSITION_CURRENT_READY", "ROOT_DISPOSITION_V2_NOT_READY",
    "ROOT_DISPOSITION_V2_READY",
    "ROOT_DISPOSITION_INVALID", "RootGenerationError", "RootGenerationSnapshot",
    "PRODUCER_PROMISE_CONTEXT", "PRODUCER_PROMISE_CONTRACT",
    "PRODUCER_TASK_BINDING_CONTEXT", "PRODUCER_TASK_BINDING_CONTRACT",
    "PRODUCER_COMPLETION_ASSERTION_CONTEXT", "PRODUCER_COMPLETION_ASSERTION_CONTRACT",
    "CLAIM_ADMISSION_CONTEXT", "CLAIM_ADMISSION_CONTRACT",
    "FUNCTIONAL_ACCEPTANCE_CONTEXT", "FUNCTIONAL_ACCEPTANCE_CONTRACT",
    "ACCEPTANCE_AUTHORITY_SEAL_CONTEXT", "ACCEPTANCE_AUTHORITY_SEAL_CONTRACT",
    "PRODUCER_LIFECYCLE_CONTRACT_MATRIX",
    "GroundingCarrierRecipe", "ImmutableGitTree", "RootPreviewCarrierRecipe", "PairedProjectionResult",
    "PreparedPatchSeriesCohort", "PreparedRootTechnicalApproachPreview",
    "PreparedSeriesScopeDecomposition",
    "prepare_cover_letter", "prepare_request_provenance", "prepare_root_technical_approach",
    "prepare_root_technical_approach_preview", "prepare_patch_series_cohort", "prepare_promotion_cohort", "prepare_patch_series_update",
    "prepare_series_scope_decomposition", "project_series_scope_decomposition",
    "classify_root_generation", "read_root_inputs",
    "read_cover_letter", "read_request_provenance", "read_patch_series_cohort",
    "read_producer_promise", "read_producer_task_binding",
    "read_producer_completion_assertion", "read_claim_admission",
    "read_functional_acceptance_verdict", "read_acceptance_authority_seal",
    "is_canonical_cohort", "is_root_cover_path",
]
