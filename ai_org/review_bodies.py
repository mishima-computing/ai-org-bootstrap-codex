"""Canonical review/reform cohort and its typed carrier recipes.

The codec validates each durable member before callers publish the resulting
files as one Git tree. Historical JSON remains readable, but new review,
response, revision-delta, and synthetic network records are canonical CUE.
"""
from __future__ import annotations

from dataclasses import dataclass
import importlib
import json
from pathlib import Path
from typing import Any, Mapping

from ai_org.body_codec import BodyCodecClient, ContractIdentity, PreparedBodySet, strict_json_dumps


REVIEW_CONTEXT = "direction-review-round-v1"
SYNTHETIC_CONTEXT = "synthetic-network-review-record-v1"
AUTHOR_RESPONSE_CONTEXT = "author-response-record-v1"
REVISION_DELTA_CONTEXT = "revision-delta-record-v1"
VARIANT = "review-reform-cohort"

REVIEW_CONTRACT = ContractIdentity("ai-org-cue-body-v1", "DirectionReviewRound", VARIANT)
AUTHOR_RESPONSE_CONTRACT = ContractIdentity("ai-org-cue-body-v1", "AuthorResponseRecord", VARIANT)
REVISION_DELTA_CONTRACT = ContractIdentity("ai-org-cue-body-v1", "RevisionDeltaRecord", VARIANT)


@dataclass(frozen=True, slots=True)
class ReviewCarrierRecipe:
    """One independently typed source-to-target review carrier."""

    recipe_id: str
    role: str
    source_context: str
    target_context: str
    source_contract: ContractIdentity
    target_contract: ContractIdentity
    required_fields: tuple[str, ...]
    source_axis: str | None = None
    required_all_properties: bool = True

    def __post_init__(self) -> None:
        # The codec definitions are deliberately open after their required
        # fields.  Make that carrier policy explicit on every independently
        # typed recipe so an axis/reform builder cannot silently narrow the
        # established aggregate at this boundary.
        if self.required_all_properties is not True:
            raise ValueError(f"{self.recipe_id}:required-all-properties-policy")

    def recognize(self, value: Mapping[str, Any]) -> None:
        missing = [field for field in self.required_fields if field not in value]
        if missing:
            raise ValueError(f"{self.recipe_id}:missing-required:{','.join(missing)}")
        if self.source_axis is not None:
            axes = value.get("axis_reviews")
            if not isinstance(axes, list) or not any(
                isinstance(item, Mapping) and item.get("axis") == self.source_axis
                for item in axes
            ):
                raise ValueError(f"{self.recipe_id}:source-axis-not-present")

    def prepare(self, value: Mapping[str, Any], codec: BodyCodecClient) -> PreparedBodySet:
        self.recognize(value)
        prepared = codec.prepare(self.source_context, value, expected=self.source_contract)
        if prepared.contract != self.target_contract:
            raise ValueError(f"{self.recipe_id}:target-contract-mismatch")
        projected = codec.parse(
            self.target_context,
            prepared.canonical.data,
            expected=self.target_contract,
        )
        if not isinstance(projected, Mapping):
            raise ValueError(f"{self.recipe_id}:target-projection-not-object")
        # Required-all-properties is a preservation policy, not merely a
        # permissive schema setting.  Prove the independently typed carrier
        # round trip here, before its value can enter a publication aggregate.
        # This covers nested anchors/statuses as well as the stable top-level
        # round, serial, marker subject, verdict, and replacement members.
        if strict_json_dumps(value) != strict_json_dumps(projected):
            raise ValueError(f"{self.recipe_id}:source-target-drift")
        return prepared


def axis_recipe(axis: str) -> ReviewCarrierRecipe:
    if not isinstance(axis, str) or not axis:
        raise ValueError("axis recipe requires a non-empty axis")
    return ReviewCarrierRecipe(
        f"axis:{axis}:v1", "axis", REVIEW_CONTEXT, REVIEW_CONTEXT,
        REVIEW_CONTRACT, REVIEW_CONTRACT,
        ("patch_series_id", "round", "axis_reviews", "verdict"),
        source_axis=axis,
    )


CONSOLIDATION_RECIPE = ReviewCarrierRecipe(
    "consolidation:v1", "consolidation", REVIEW_CONTEXT, REVIEW_CONTEXT,
    REVIEW_CONTRACT, REVIEW_CONTRACT,
    ("patch_series_id", "round", "consolidation", "verdict"),
)
AUTHOR_RESPONSE_RECIPE = ReviewCarrierRecipe(
    "reform:author-response:v1", "reform", AUTHOR_RESPONSE_CONTEXT,
    AUTHOR_RESPONSE_CONTEXT,
    AUTHOR_RESPONSE_CONTRACT, AUTHOR_RESPONSE_CONTRACT,
    ("patch_series_id", "review_round", "author_version", "objections"),
)
REVISION_DELTA_RECIPE = ReviewCarrierRecipe(
    "reform:revision-delta:v1", "reform", REVISION_DELTA_CONTEXT,
    REVISION_DELTA_CONTEXT,
    REVISION_DELTA_CONTRACT, REVISION_DELTA_CONTRACT,
    ("patch_series_id", "review_round", "author_version", "node_replacements"),
)
SYNTHETIC_RECIPE = ReviewCarrierRecipe(
    "synthetic-network:v1", "synthetic", SYNTHETIC_CONTEXT, SYNTHETIC_CONTEXT,
    REVIEW_CONTRACT, REVIEW_CONTRACT,
    ("patch_series_id", "round", "lineage_escalation", "verdict"),
)


@dataclass(frozen=True, slots=True)
class CarrierAggregate:
    """Validated temporary value backing one immutable Git-tree publication."""

    files: Mapping[str, Any]
    prepared: tuple[PreparedBodySet, ...]
    validated: bool = True
    temporary: bool = True


def canonical_path(path: str) -> str:
    return path[:-5] + ".cue" if path.endswith(".json") else path


def legacy_path(path: str) -> str:
    return path[:-4] + ".json" if path.endswith(".cue") else path


def prepare_direction_review(
    value: Mapping[str, Any], *, synthetic: bool = False, client: BodyCodecClient | None = None
) -> PreparedBodySet:
    codec = client or BodyCodecClient()
    if synthetic:
        return SYNTHETIC_RECIPE.prepare(value, codec)
    axes = value.get("axis_reviews")
    if not isinstance(axes, list):
        raise ValueError("direction-review:axis_reviews")
    # A terminal pre-review NAK is emitted when required inputs are absent, so
    # no axis builder has run. Every nonterminal review must bind at least one.
    if not axes and value.get("verdict") != "nak":
        raise ValueError("direction-review:axis_reviews-empty")
    # Every axis and the consolidation independently carries the same
    # established aggregate through its typed source-to-target recipe.  It is
    # not enough for an axis merely to recognize the input: doing so would let
    # an axis-specific source/target contract drift escape the cohort preflight.
    seen_axes: set[str] = set()
    for item in axes:
        if not isinstance(item, Mapping) or not isinstance(item.get("axis"), str):
            raise ValueError("direction-review:axis")
        axis = str(item["axis"])
        if axis in seen_axes:
            raise ValueError(f"direction-review:duplicate-axis:{axis}")
        seen_axes.add(axis)
        axis_recipe(axis).prepare(value, codec)
    return CONSOLIDATION_RECIPE.prepare(value, codec)


def prepare_author_response(
    value: Mapping[str, Any], *, client: BodyCodecClient | None = None
) -> PreparedBodySet:
    return AUTHOR_RESPONSE_RECIPE.prepare(value, client or BodyCodecClient())


def prepare_revision_delta(
    value: Mapping[str, Any], *, client: BodyCodecClient | None = None
) -> PreparedBodySet:
    return REVISION_DELTA_RECIPE.prepare(value, client or BodyCodecClient())


def prepare_immutable_tree(
    files: Mapping[str, Any], *, client: BodyCodecClient | None = None
) -> CarrierAggregate:
    """Preflight all cohort members before a caller performs Git mutation."""
    network_bodies = importlib.import_module("ai_org.network_bodies")
    patch_series_bodies = importlib.import_module("ai_org.patch_series_bodies")
    contributor_handoff = importlib.import_module("ai_org.contributor_handoff")
    codec = client or BodyCodecClient()
    output: dict[str, Any] = {}
    prepared: list[PreparedBodySet] = []
    for path, value in files.items():
        target = canonical_path(path) if _is_review_record_path(path) else path
        if target in output:
            raise ValueError(f"review-reform:duplicate-target:{target}")
        body: PreparedBodySet | None = None
        update_contract = _canonical_update_contract(
            path, network_bodies=network_bodies, patch_series_bodies=patch_series_bodies
        )
        review_contracts = _review_contracts(path)
        if isinstance(value, PreparedBodySet):
            # Formation cohorts may hand an already-migrated cover or approach
            # update across this boundary. Revalidate its canonical bytes here:
            # PreparedBodySet is a public value type and its construction alone
            # is not proof that this publication cohort performed preflight.
            permitted = (
                ()
                if path == patch_series_bodies.ROOT_APPROACH_PATH
                else _prepared_contracts_for_path(
                    path,
                    review_contracts=review_contracts,
                    update_contract=update_contract,
                )
            )
            if not permitted:
                raise ValueError(f"review-reform:unregistered-prepared-path:{path}")
            if (value.context_id, value.contract) not in permitted:
                raise ValueError(f"review-reform:prepared-contract-mismatch:{path}")
            codec.parse(value.context_id, value.canonical.data, expected=value.contract)
            body = value
        elif review_contracts and isinstance(value, (str, bytes)):
            raw = value.encode("utf-8") if isinstance(value, str) else value
            context, contract = review_contracts[0]
            projected = codec.parse(context, raw, expected=contract)
            # Direction and synthetic records share an envelope contract, but
            # retain distinct registered source contexts. Re-run the canonical
            # preflight through the synthetic recipe when its visible lineage
            # status identifies that source family.
            if (
                len(review_contracts) > 1
                and isinstance(projected, Mapping)
                and projected.get("lineage_escalation") is True
            ):
                context, contract = review_contracts[1]
                codec.parse(context, raw, expected=contract)
            output[target] = raw.decode("utf-8", errors="strict")
            continue
        elif update_contract is not None:
            if not isinstance(value, (str, bytes)):
                raise TypeError(f"{path}: canonical update must contain canonical bytes")
            raw = value.encode("utf-8") if isinstance(value, str) else value
            context, contract = update_contract
            codec.parse(context, raw, expected=contract)
            output[target] = raw.decode("utf-8", errors="strict")
            continue
        elif _is_direction_path(path):
            if not isinstance(value, Mapping):
                raise TypeError(f"{path}: review record must be a mapping")
            body = prepare_direction_review(value, synthetic=value.get("lineage_escalation") is True, client=codec)
        elif _is_author_response_path(path):
            if not isinstance(value, Mapping):
                raise TypeError(f"{path}: author response must be a mapping")
            body = prepare_author_response(value, client=codec)
        elif _is_revision_delta_path(path):
            if not isinstance(value, Mapping):
                raise TypeError(f"{path}: revision delta must be a mapping")
            body = prepare_revision_delta(value, client=codec)
        elif path == patch_series_bodies.COVER_PATH and isinstance(value, Mapping):
            body = patch_series_bodies.prepare_cover_letter(value, client=codec)
        elif path == contributor_handoff.QUESTIONS_PATH and isinstance(value, Mapping):
            body = contributor_handoff.prepare_questions(value, client=codec)
        elif path == contributor_handoff.EXPERIENCE_PATH and isinstance(value, Mapping):
            body = contributor_handoff.prepare_experience(value, client=codec)
        if body is None:
            output[target] = value
        else:
            prepared.append(body)
            output[target] = body.canonical.data.decode("utf-8", errors="strict")
    return CarrierAggregate(files=output, prepared=tuple(prepared))


def prepare_marker_tree(
    files: Mapping[str, Any], *, client: BodyCodecClient | None = None
) -> CarrierAggregate:
    """Preflight the immutable tree immediately before a marker commit."""

    return prepare_immutable_tree(files, client=client)


def prepare_reform_tree(
    files: Mapping[str, Any], *, client: BodyCodecClient | None = None
) -> CarrierAggregate:
    """Preflight the immutable tree immediately before a reform commit."""

    return prepare_immutable_tree(files, client=client)


def _canonical_update_contract(path: str, *, network_bodies, patch_series_bodies):
    if path == patch_series_bodies.COVER_PATH:
        return patch_series_bodies.COVER_CONTEXT, patch_series_bodies.COVER_CONTRACT
    if path == patch_series_bodies.PROVENANCE_PATH:
        return patch_series_bodies.PROVENANCE_CONTEXT, patch_series_bodies.PROVENANCE_CONTRACT
    if path == patch_series_bodies.ROOT_APPROACH_PATH:
        return patch_series_bodies.ROOT_APPROACH_CONTEXT, patch_series_bodies.ROOT_APPROACH_CONTRACT
    if path == patch_series_bodies.SCOPE_DECOMPOSITION_PATH:
        return (
            patch_series_bodies.SCOPE_DECOMPOSITION_CONTEXT,
            patch_series_bodies.SCOPE_DECOMPOSITION_CONTRACT,
        )
    return network_bodies.canonical_update_contract(path)


def _review_contracts(path: str) -> tuple[tuple[str, ContractIdentity], ...]:
    if _is_direction_path(path):
        return (
            (REVIEW_CONTEXT, REVIEW_CONTRACT),
            (SYNTHETIC_CONTEXT, REVIEW_CONTRACT),
        )
    if _is_author_response_path(path):
        return ((AUTHOR_RESPONSE_CONTEXT, AUTHOR_RESPONSE_CONTRACT),)
    if _is_revision_delta_path(path):
        return ((REVISION_DELTA_CONTEXT, REVISION_DELTA_CONTRACT),)
    return ()


def _prepared_contracts_for_path(
    path: str,
    *,
    review_contracts: tuple[tuple[str, ContractIdentity], ...],
    update_contract: tuple[str, ContractIdentity] | None,
) -> tuple[tuple[str, ContractIdentity], ...]:
    """Return the exact contracts permitted at a prepared publication path."""

    contributor_handoff = importlib.import_module("ai_org.contributor_handoff")
    if review_contracts:
        return review_contracts
    if update_contract is not None:
        return (update_contract,)
    if path == contributor_handoff.QUESTIONS_PATH:
        return (
            (
                contributor_handoff.QUESTIONS_SERIES_CONTEXT,
                contributor_handoff.QUESTIONS_SERIES_CONTRACT,
            ),
        )
    if path == contributor_handoff.EXPERIENCE_PATH:
        return (
            (
                contributor_handoff.EXPERIENCE_CONTEXT,
                contributor_handoff.EXPERIENCE_CONTRACT,
            ),
        )
    return ()


def parse_record(
    raw: str | bytes,
    recipe: ReviewCarrierRecipe,
    *, client: BodyCodecClient | None = None,
) -> dict[str, Any]:
    data = raw.encode("utf-8") if isinstance(raw, str) else raw
    if recipe.target_contract == REVIEW_CONTRACT:
        # Earliest committed review records predate the complete aggregate but
        # are still durable history. Supply only the later aggregate members'
        # neutral defaults at the compatibility boundary; canonical writers
        # must satisfy the complete schema themselves.
        try:
            historical = json.loads(data)
        except (UnicodeDecodeError, json.JSONDecodeError):
            historical = None
        if isinstance(historical, dict) and "apiVersion" not in historical:
            historical.setdefault("inputs", {})
            historical.setdefault("axis_reviews", [])
            historical.setdefault("per_axis_verdicts", {})
            historical.setdefault("consolidation", {})
            historical.setdefault("git_result_marker", "")
            historical.setdefault("serial", "")
            historical.setdefault("deferred", [])
            data = json.dumps(
                historical, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
    projection = (client or BodyCodecClient()).parse(
        recipe.target_context, data, expected=recipe.target_contract
    )
    # Review consumers predate the exact-number outer codec boundary and rely
    # on ordinary json.loads aggregate shapes. Keep that established adapter
    # contract while canonical bytes retain their original number lexemes.
    restored = json.loads(strict_json_dumps(projection))
    if not isinstance(restored, dict):  # pragma: no cover - codec schema is object-only
        raise TypeError("review-reform projection must be an object")
    return restored


def read_record(
    repo: str | Path,
    ref: str,
    path: str,
    recipe: ReviewCarrierRecipe,
    *, client: BodyCodecClient | None = None,
) -> dict[str, Any] | None:
    git_wrapper = importlib.import_module("ai_org.git_wrapper")
    frozen = git_wrapper.head_sha(repo, ref)
    if frozen is None:
        return None
    canonical = canonical_path(path)
    raw = git_wrapper.show_file(repo, frozen, canonical)
    if raw is None:
        raw = git_wrapper.show_file(repo, frozen, legacy_path(canonical))
    return None if raw is None else parse_record(raw, recipe, client=client)


def _is_review_record_path(path: str) -> bool:
    return _is_direction_path(path) or _is_author_response_path(path) or _is_revision_delta_path(path)


def _is_direction_path(path: str) -> bool:
    return path.endswith("-direction-review-record.json") or path.endswith("-direction-review-record.cue")


def _is_author_response_path(path: str) -> bool:
    return path.endswith("-response-record.json") or path.endswith("-response-record.cue")


def _is_revision_delta_path(path: str) -> bool:
    return path.endswith("-revision-delta.json") or path.endswith("-revision-delta.cue")


__all__ = [
    "AUTHOR_RESPONSE_CONTEXT", "AUTHOR_RESPONSE_RECIPE", "CarrierAggregate",
    "CONSOLIDATION_RECIPE", "REVIEW_CONTEXT", "REVISION_DELTA_CONTEXT",
    "REVISION_DELTA_RECIPE", "ReviewCarrierRecipe", "SYNTHETIC_CONTEXT",
    "SYNTHETIC_RECIPE", "axis_recipe", "canonical_path", "legacy_path",
    "parse_record", "prepare_author_response", "prepare_direction_review",
    "prepare_immutable_tree", "prepare_marker_tree", "prepare_reform_tree",
    "prepare_revision_delta", "read_record",
]
