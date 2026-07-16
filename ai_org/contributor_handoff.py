"""Canonical contributor-handoff compatibility boundary.

All durable members are validated CUE bodies. Historical JSON remains readable
at the boundary; JSON/schema/carrier renderings are per-invocation projections.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
import re
from types import MappingProxyType
from typing import Any, Iterable, Mapping

from ai_org.body_codec import (
    BodyCodecClient,
    ContractIdentity,
    PreparedBodySet,
    strict_json_dumps,
    strict_json_loads,
)


QUESTIONS_SERIES_CONTEXT = "questions-patch-series-v1"
QUESTIONS_CONTRIBUTION_CONTEXT = "questions-contribution-v1"
EXPERIENCE_CONTEXT = "implementation-experience-report-v1"
RESULT_CONTRACT_CONTEXT = "implementation-result-contract-v1"
RESULT_CONTEXT = "implementation-result-v1"
FUNCTIONAL_CARRIER_CONTEXT = "functional-check-carrier-recipe-v1"
FUNCTIONAL_ACCEPTANCE_CONTEXT = "functional-acceptance-v1"
PROJECTION_PROFILE = "consumer-json-v1"
SCHEMA_PROFILE = "codex-structured-output-v1"
ACCEPTANCE_REACHABLE_SUBJECT = "acceptance: reachable"
ACCEPTANCE_BLOCKED_SUBJECT = "acceptance: blocked"
ACCEPTANCE_SUBJECTS = frozenset(
    {ACCEPTANCE_REACHABLE_SUBJECT, ACCEPTANCE_BLOCKED_SUBJECT}
)

QUESTIONS_PATH = "questions-the-patch-must-answer.cue"
LEGACY_QUESTIONS_PATH = "questions-the-patch-must-answer.json"
EXPERIENCE_PATH = "implementation-experience-report.cue"
LEGACY_EXPERIENCE_PATH = "implementation-experience-report.json"
RESULT_CONTRACT_PATH = "contributor-contract/implementation-result.cue"
LEGACY_RESULT_SCHEMA_PATH = "contributor-contract/implementation-result.schema.json"
RESULT_PATH = "implementation-result.cue"
LEGACY_RESULT_PATH = "implementation-result.json"

QUESTIONS_SERIES_CONTRACT = ContractIdentity("ai-org-cue-body-v1", "QuestionsThePatchMustAnswer", "patch-series")
QUESTIONS_CONTRIBUTION_CONTRACT = ContractIdentity("ai-org-cue-body-v1", "QuestionsThePatchMustAnswer", "contribution")
EXPERIENCE_CONTRACT = ContractIdentity("ai-org-cue-body-v1", "ImplementationExperienceReport", "patch-series")
RESULT_CONTRACT = ContractIdentity("ai-org-cue-body-v1", "ImplementationResultContract", "contributor-contract")
RESULT_BODY_CONTRACT = ContractIdentity("ai-org-cue-body-v1", "ImplementationResult", "harness-authored")
FUNCTIONAL_CARRIER_CONTRACT = ContractIdentity("ai-org-cue-body-v1", "FunctionalCheckCarrierRecipe", "contributor-handoff")
FUNCTIONAL_ACCEPTANCE_CONTRACT = ContractIdentity(
    "ai-org-cue-body-v1", "FunctionalAcceptanceVerdict", "contribution"
)


@dataclass(frozen=True, slots=True)
class FunctionalCheckCarrierRecipe:
    """Independently typed invocation from a result to acceptance."""

    source_context: str
    source_contract: ContractIdentity
    target_context: str
    target_contract: ContractIdentity
    projection_profile: str
    schema_profile: str

    def __post_init__(self) -> None:
        if (
            self.source_context != RESULT_CONTEXT
            or self.source_contract != RESULT_BODY_CONTRACT
        ):
            raise ValueError("functional carrier source contract is not registered")
        if (
            self.target_context != FUNCTIONAL_ACCEPTANCE_CONTEXT
            or self.target_contract != FUNCTIONAL_ACCEPTANCE_CONTRACT
        ):
            raise ValueError("functional carrier target contract is not registered")
        if self.projection_profile != PROJECTION_PROFILE:
            raise ValueError("functional carrier projection profile does not match")
        if self.schema_profile != SCHEMA_PROFILE:
            raise ValueError("functional carrier schema profile does not match")


FUNCTIONAL_CHECK_RECIPE = FunctionalCheckCarrierRecipe(
    RESULT_CONTEXT,
    RESULT_BODY_CONTRACT,
    FUNCTIONAL_ACCEPTANCE_CONTEXT,
    FUNCTIONAL_ACCEPTANCE_CONTRACT,
    PROJECTION_PROFILE,
    SCHEMA_PROFILE,
)
FUNCTIONAL_CARRIER_CONTRACTS = MappingProxyType(
    {
        "source": (
            FUNCTIONAL_CHECK_RECIPE.source_context,
            FUNCTIONAL_CHECK_RECIPE.source_contract,
        ),
        "target": (
            FUNCTIONAL_CHECK_RECIPE.target_context,
            FUNCTIONAL_CHECK_RECIPE.target_contract,
        ),
    }
)


class AmbiguousRepresentation(ValueError):
    """A member has two competing durable bodies at one tree coordinate."""


@dataclass(frozen=True, slots=True)
class AcceptanceMarker:
    """Typed Git evidence emitted only by functional acceptance.

    Acceptance remains separate from the harness-authored Implementation
    Result. Keeping the subjects and noreply identity here makes every writer
    consume the same contributor-handoff compatibility boundary.
    """

    subject: str
    identity_name: str
    identity_email: str

    def __post_init__(self) -> None:
        if self.subject not in ACCEPTANCE_SUBJECTS:
            raise ValueError("acceptance marker subject is not registered")
        if not self.identity_name or any(c in self.identity_name for c in "\r\n\x00"):
            raise ValueError("acceptance marker identity name is invalid")
        if not re.fullmatch(
            r"[^@\s\x00-\x1f\x7f]+@users\.noreply\.github\.com",
            self.identity_email,
        ):
            raise ValueError("acceptance marker identity must use a GitHub noreply email")

    @property
    def identity(self) -> Mapping[str, str]:
        return MappingProxyType(
            {"name": self.identity_name, "email": self.identity_email}
        )


def acceptance_marker(
    reachable: bool, identity: Mapping[str, Any]
) -> AcceptanceMarker:
    """Create the fixed acceptance marker for one functional verdict."""

    if not isinstance(reachable, bool):
        raise TypeError("acceptance marker reachability must be boolean")
    return AcceptanceMarker(
        subject=(
            ACCEPTANCE_REACHABLE_SUBJECT
            if reachable
            else ACCEPTANCE_BLOCKED_SUBJECT
        ),
        identity_name=str(identity.get("name") or ""),
        identity_email=str(identity.get("email") or ""),
    )


def result_contract_body() -> dict[str, Any]:
    return {
        "schema_version": "1",
        "authority_path": RESULT_PATH,
        "computed_by": "patch-author-harness",
        "required_fields": ["patch_series_branch", "node_key", "acknowledged_patchwork_checks"],
        "require_complete_must_answer_outcomes": True,
        "generated_json_temporary": True,
        "generated_schema_temporary": True,
    }


def functional_carrier_recipe() -> dict[str, Any]:
    return {
        "source_context": FUNCTIONAL_CHECK_RECIPE.source_context,
        "source_api_version": FUNCTIONAL_CHECK_RECIPE.source_contract.api_version,
        "source_kind": FUNCTIONAL_CHECK_RECIPE.source_contract.kind,
        "source_variant": FUNCTIONAL_CHECK_RECIPE.source_contract.variant,
        "target_context": FUNCTIONAL_CHECK_RECIPE.target_context,
        "target_api_version": FUNCTIONAL_CHECK_RECIPE.target_contract.api_version,
        "target_kind": FUNCTIONAL_CHECK_RECIPE.target_contract.kind,
        "target_variant": FUNCTIONAL_CHECK_RECIPE.target_contract.variant,
        "target": "functional-acceptance",
        "projection_profile": FUNCTIONAL_CHECK_RECIPE.projection_profile,
        "schema_profile": FUNCTIONAL_CHECK_RECIPE.schema_profile,
        "validation_precedes_dispatch": True,
        "complete_outcomes": True,
        "preserve_failed_worktree": True,
        "carrier_output_temporary": True,
    }


def prepare_questions(value: Mapping[str, Any], *, contribution: bool = False, client: BodyCodecClient | None = None) -> PreparedBodySet:
    context = QUESTIONS_CONTRIBUTION_CONTEXT if contribution else QUESTIONS_SERIES_CONTEXT
    contract = QUESTIONS_CONTRIBUTION_CONTRACT if contribution else QUESTIONS_SERIES_CONTRACT
    return (client or BodyCodecClient()).prepare(context, value, expected=contract)


def prepare_experience(value: Mapping[str, Any], *, client: BodyCodecClient | None = None) -> PreparedBodySet:
    return (client or BodyCodecClient()).prepare(EXPERIENCE_CONTEXT, value, expected=EXPERIENCE_CONTRACT)


def prepare_result_contract(*, client: BodyCodecClient | None = None) -> PreparedBodySet:
    return (client or BodyCodecClient()).prepare(RESULT_CONTRACT_CONTEXT, result_contract_body(), expected=RESULT_CONTRACT)


def prepare_result(value: Mapping[str, Any], *, client: BodyCodecClient | None = None) -> PreparedBodySet:
    return (client or BodyCodecClient()).prepare(RESULT_CONTEXT, value, expected=RESULT_BODY_CONTRACT)


def prepare_functional_carrier(*, client: BodyCodecClient | None = None) -> PreparedBodySet:
    return (client or BodyCodecClient()).prepare(FUNCTIONAL_CARRIER_CONTEXT, functional_carrier_recipe(), expected=FUNCTIONAL_CARRIER_CONTRACT)


def prepare_series_promotion_files(extra_files: Mapping[str, Any]) -> dict[str, Any]:
    """Preflight the complete series-side handoff before Git publication.

    The returned mapping is safe to write only after this function succeeds.
    Keeping preparation outside the checkout/index transaction ensures that a
    malformed Questions or Experience member cannot create or reset the target
    series branch while the rest of the handoff remains unpublished.
    """
    supplied_contract = sorted(
        {RESULT_CONTRACT_PATH, LEGACY_RESULT_SCHEMA_PATH}.intersection(extra_files)
    )
    if supplied_contract:
        raise ValueError(f"{supplied_contract[0]} is written only by the promotion harness")
    supplied_result = sorted({RESULT_PATH, LEGACY_RESULT_PATH}.intersection(extra_files))
    if supplied_result:
        raise ValueError(f"{supplied_result[0]} is written only by the patch-author harness")
    prepared: dict[str, Any] = {
        RESULT_CONTRACT_PATH: prepare_result_contract().canonical.data.decode("utf-8"),
    }
    remaining = dict(extra_files)
    _prepare_promotion_member(
        remaining,
        prepared,
        canonical_path=QUESTIONS_PATH,
        legacy_path=LEGACY_QUESTIONS_PATH,
        prepare=prepare_questions,
        parse=parse_questions,
    )
    _prepare_promotion_member(
        remaining,
        prepared,
        canonical_path=EXPERIENCE_PATH,
        legacy_path=LEGACY_EXPERIENCE_PATH,
        prepare=prepare_experience,
        parse=parse_experience,
    )
    prepared.update(remaining)
    return prepared


def _prepare_promotion_member(
    source: dict[str, Any],
    target: dict[str, Any],
    *,
    canonical_path: str,
    legacy_path: str,
    prepare,
    parse,
) -> None:
    """Consume one logical member and emit its sole canonical authority.

    Historical JSON remains a read input, but promotion must not publish it as
    a second durable representation. Detecting coexistence here also keeps the
    target ref untouched because this function runs before checkout/reset.
    """
    present = [path for path in (canonical_path, legacy_path) if path in source]
    if len(present) > 1:
        raise AmbiguousRepresentation(
            "contributor-handoff promotion has both canonical and legacy representations: "
            f"{canonical_path}, {legacy_path}"
        )
    if not present:
        return
    payload = source.pop(present[0])
    if not isinstance(payload, Mapping):
        payload = parse(payload)
    target[canonical_path] = prepare(payload).canonical.data.decode("utf-8")


def parse_questions(raw: str | bytes, *, contribution: bool = False, client: BodyCodecClient | None = None) -> dict[str, Any]:
    context = QUESTIONS_CONTRIBUTION_CONTEXT if contribution else QUESTIONS_SERIES_CONTEXT
    contract = QUESTIONS_CONTRIBUTION_CONTRACT if contribution else QUESTIONS_SERIES_CONTRACT
    return _parse(raw, context, contract, client)


def parse_experience(raw: str | bytes, *, client: BodyCodecClient | None = None) -> dict[str, Any]:
    return _parse(raw, EXPERIENCE_CONTEXT, EXPERIENCE_CONTRACT, client)


def parse_result_contract(raw: str | bytes, *, client: BodyCodecClient | None = None) -> dict[str, Any]:
    return _parse(raw, RESULT_CONTRACT_CONTEXT, RESULT_CONTRACT, client)


def parse_historical_result_contract_schema(raw: str | bytes) -> dict[str, Any]:
    """Project a compatible historical JSON Schema to the CUE contract body.

    The schema is a read-only compatibility input, never authority.  Validate
    the parts that express the closed result-body contract before returning the
    same ordinary projection produced by the canonical CUE body.
    """

    try:
        data = raw if isinstance(raw, bytes) else raw.encode("utf-8")
        schema = strict_json_loads(data)
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError(f"historical implementation result contract is invalid: {exc}") from exc
    if not isinstance(schema, Mapping):
        raise ValueError("historical implementation result contract must be an object")

    expected_required = result_contract_body()["required_fields"]
    properties = schema.get("properties")
    expected_properties = {*expected_required, "must_answer_outcomes"}
    series_anchor = (
        properties.get("patch_series_branch")
        if isinstance(properties, Mapping)
        else None
    )
    node_anchor = properties.get("node_key") if isinstance(properties, Mapping) else None
    acknowledgements = (
        properties.get("acknowledged_patchwork_checks")
        if isinstance(properties, Mapping)
        else None
    )
    acknowledgement_values = (
        acknowledgements.get("additionalProperties")
        if isinstance(acknowledgements, Mapping)
        else None
    )
    acknowledgement_types = (
        acknowledgement_values.get("type")
        if isinstance(acknowledgement_values, Mapping)
        else None
    )
    outcomes = properties.get("must_answer_outcomes") if isinstance(properties, Mapping) else None
    outcome_values = outcomes.get("additionalProperties") if isinstance(outcomes, Mapping) else None
    outcome_properties = (
        outcome_values.get("properties") if isinstance(outcome_values, Mapping) else None
    )
    outcome = outcome_properties.get("outcome") if isinstance(outcome_properties, Mapping) else None
    minimum_length = outcome.get("minLength") if isinstance(outcome, Mapping) else None
    if hasattr(minimum_length, "as_int_exact"):
        minimum_length = minimum_length.as_int_exact()
    compatible = (
        schema.get("type") == "object"
        and schema.get("additionalProperties") is False
        and schema.get("required") == expected_required
        and isinstance(properties, Mapping)
        # A closed result contract cannot acquire a declared property merely
        # because its historical JSON Schema still says additionalProperties
        # is false. Likewise, anchor schemas must not be widened or coerced
        # before being projected as the stricter canonical CUE contract.
        and set(properties) == expected_properties
        and isinstance(series_anchor, Mapping)
        and series_anchor.get("type") == "string"
        and isinstance(node_anchor, Mapping)
        and node_anchor.get("type") == "string"
        and isinstance(acknowledgements, Mapping)
        and acknowledgements.get("type") == "object"
        and isinstance(acknowledgement_types, list)
        and set(acknowledgement_types) == {"boolean", "number", "string", "null"}
        and isinstance(outcomes, Mapping)
        and outcomes.get("type") == "object"
        and isinstance(outcome_values, Mapping)
        and outcome_values.get("type") == "object"
        and outcome_values.get("required") == ["outcome"]
        and isinstance(outcome, Mapping)
        and outcome.get("type") == "string"
        and minimum_length == 1
    )
    if not compatible:
        raise ValueError("historical implementation result contract is incompatible")
    return result_contract_body()


def read_result_contract(repo: str | Path, ref: str) -> dict[str, Any] | None:
    """Read the canonical contract or its historical schema without mutation."""

    return read_member(
        repo,
        ref,
        RESULT_CONTRACT_PATH,
        LEGACY_RESULT_SCHEMA_PATH,
        parse_result_contract_compat,
    )


def parse_result_contract_compat(raw: str | bytes) -> dict[str, Any]:
    """Read canonical CUE or a compatible historical schema projection."""

    try:
        return parse_result_contract(raw)
    except Exception as cue_error:
        # A short-lived historical writer reused the now-canonical coordinate
        # for its generated JSON Schema. Keep those bytes readable too.
        try:
            return parse_historical_result_contract_schema(raw)
        except ValueError:
            raise cue_error


def parse_result(raw: str | bytes, *, client: BodyCodecClient | None = None) -> dict[str, Any]:
    return _parse(raw, RESULT_CONTEXT, RESULT_BODY_CONTRACT, client)


def read_member(repo: str | Path, ref: str, canonical_path: str, legacy_path: str, parser) -> dict[str, Any] | None:
    """Read exactly one durable representation from one frozen Git tree.

    Historical JSON is a compatibility input, not a second authority.  A tree
    containing both forms is therefore rejected instead of silently preferring
    whichever path a consumer happened to check first.
    """
    from ai_org import git_wrapper

    frozen = git_wrapper.head_sha(repo, ref)
    if frozen is None:
        return None
    candidates = {
        canonical_path: git_wrapper.show_file(repo, frozen, canonical_path),
        legacy_path: git_wrapper.show_file(repo, frozen, legacy_path),
    }
    present = [(path, raw) for path, raw in candidates.items() if raw is not None]
    if len(present) > 1:
        raise AmbiguousRepresentation(
            f"contributor-handoff member has both canonical and legacy representations: "
            f"{canonical_path}, {legacy_path}"
        )
    return None if not present else parser(present[0][1])


def read_checkout_member(
    root: str | Path,
    canonical_path: str,
    legacy_path: str,
    parser,
) -> Any | None:
    """Read exactly one representation from a detached contributor checkout."""

    base = Path(root)
    candidates = [base / canonical_path, base / legacy_path]
    present = [path for path in candidates if path.is_file()]
    if len(present) > 1:
        raise AmbiguousRepresentation(
            f"contributor-handoff member has both canonical and legacy representations: "
            f"{canonical_path}, {legacy_path}"
        )
    return None if not present else parser(present[0].read_bytes())


@dataclass(frozen=True, slots=True)
class FunctionalCheckCarrier:
    result: Mapping[str, Any]
    recipe: FunctionalCheckCarrierRecipe = FUNCTIONAL_CHECK_RECIPE
    validated: bool = True
    temporary: bool = True


def validated_functional_carrier(raw: str | bytes, *, client: BodyCodecClient | None = None) -> FunctionalCheckCarrier:
    # Parsing is deliberately complete before a consumer receives the carrier.
    codec = client or BodyCodecClient()
    exact = parse_result(raw, client=codec)
    ordinary = json.loads(strict_json_dumps(exact))
    return FunctionalCheckCarrier(ordinary)


def outcome_key_mismatches(
    result: Mapping[str, Any], question_ids: list[str] | tuple[str, ...]
) -> tuple[list[str], list[str]]:
    """Return missing and unexpected must-answer outcome identifiers."""

    expected = set(question_ids)
    outcomes = result.get("must_answer_outcomes")
    actual = set(outcomes) if isinstance(outcomes, Mapping) else set()
    return sorted(expected - actual), sorted(actual - expected)


def validate_result_transition(
    result: Mapping[str, Any],
    *,
    expected_series_branch: str,
    expected_node_key: str,
    question_ids: Iterable[str] | None = None,
) -> None:
    """Bind one harness result to its exact source and complete outcomes.

    CUE owns the body shape.  This cross-body transition owns the values that
    cannot be known by the standalone result schema: the series/node address
    and the objection identifiers read from Questions the Patch Must Answer.
    """

    if (
        result.get("patch_series_branch") != expected_series_branch
        or result.get("node_key") != expected_node_key
    ):
        raise ValueError("implementation result does not preserve the exact series/node anchor")

    acknowledgements = result.get("acknowledged_patchwork_checks")
    if not isinstance(acknowledgements, Mapping):
        raise ValueError("implementation result acknowledgements must be a mapping")
    if any(
        not _is_typed_json_scalar(value)
        for value in acknowledgements.values()
    ):
        raise ValueError("implementation result acknowledgement values must be typed JSON scalars")

    if question_ids is None:
        return
    expected = tuple(question_ids)
    missing, unexpected = outcome_key_mismatches(result, expected)
    if missing or unexpected:
        raise ValueError(
            "implementation result outcomes do not match Questions the Patch Must Answer: "
            f"missing={missing}, unexpected={unexpected}"
        )
    outcomes = result.get("must_answer_outcomes", {})
    for objection_id in expected:
        outcome = outcomes.get(objection_id) if isinstance(outcomes, Mapping) else None
        if not isinstance(outcome, Mapping) or not str(outcome.get("outcome") or "").strip():
            raise ValueError(f"implementation result outcome is incomplete: {objection_id}")


def _is_typed_json_scalar(value: Any) -> bool:
    """Recognize only scalar values that have an exact JSON representation.

    Python's ``float`` also admits NaN and infinities even though the durable
    contract is strict JSON. Reject them at the cross-body transition so a
    caller cannot pass transition validation and fail later while rendering
    the temporary carrier or canonical CUE body.
    """

    if value is None or type(value) in (bool, int, str):
        return True
    return type(value) is float and math.isfinite(value)


def _parse(raw: str | bytes, context: str, contract: ContractIdentity, client: BodyCodecClient | None) -> dict[str, Any]:
    data = raw.encode("utf-8") if isinstance(raw, str) else raw
    return (client or BodyCodecClient()).parse(context, data, expected=contract)


__all__ = [name for name in globals() if name.isupper() or name.startswith("prepare_") or name.startswith("parse_") or name in {"AcceptanceMarker", "AmbiguousRepresentation", "FunctionalCheckCarrier", "FunctionalCheckCarrierRecipe", "acceptance_marker", "validated_functional_carrier", "validate_result_transition", "read_member", "read_checkout_member", "read_result_contract", "result_contract_body", "outcome_key_mismatches"}]
