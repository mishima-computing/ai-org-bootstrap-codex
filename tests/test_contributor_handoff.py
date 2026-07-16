from __future__ import annotations

import json
import subprocess

import pytest

from ai_org import contributor_handoff
from ai_org.body_codec import CodecFailure
from ai_org.patch_author.contract import implementation_result_schema


def _question() -> dict:
    return {
        "questions_the_patch_must_answer": [
            {"objection_id": "approach:codec", "executable_check": "true"}
        ]
    }


@pytest.mark.parametrize(
    ("reachable", "subject"),
    [
        (True, contributor_handoff.ACCEPTANCE_REACHABLE_SUBJECT),
        (False, contributor_handoff.ACCEPTANCE_BLOCKED_SUBJECT),
    ],
)
def test_functional_verdict_transition_creates_typed_acceptance_marker(
    reachable, subject
):
    marker = contributor_handoff.acceptance_marker(
        reachable,
        {
            "name": "AI Org Engine",
            "email": "ai-org-engine@users.noreply.github.com",
        },
    )

    assert marker.subject == subject
    assert marker.identity == {
        "name": "AI Org Engine",
        "email": "ai-org-engine@users.noreply.github.com",
    }


@pytest.mark.parametrize(
    "identity",
    [
        {"name": "AI Org Engine", "email": "engine@example.com"},
        {"name": "AI Org\nImpersonator", "email": "engine@users.noreply.github.com"},
    ],
)
def test_acceptance_marker_transition_rejects_noncanonical_identity(identity):
    with pytest.raises(ValueError, match="acceptance marker identity"):
        contributor_handoff.acceptance_marker(True, identity)


def test_acceptance_marker_rejects_unregistered_subject():
    with pytest.raises(ValueError, match="subject is not registered"):
        contributor_handoff.AcceptanceMarker(
            "acceptance: passed",
            "AI Org Engine",
            "ai-org-engine@users.noreply.github.com",
        )


def test_series_and_contribution_question_transitions_preserve_variants():
    series = contributor_handoff.prepare_questions(_question())
    contribution = contributor_handoff.prepare_questions(_question(), contribution=True)

    assert series.contract == contributor_handoff.QUESTIONS_SERIES_CONTRACT
    assert contribution.contract == contributor_handoff.QUESTIONS_CONTRIBUTION_CONTRACT
    assert contributor_handoff.parse_questions(series.canonical.data) == _question()
    assert contributor_handoff.parse_questions(
        contribution.canonical.data, contribution=True
    ) == _question()


def test_contract_and_result_are_cue_authorities_with_temporary_projections():
    contract = contributor_handoff.prepare_result_contract()
    projected = contributor_handoff.parse_result_contract(contract.canonical.data)

    assert projected["authority_path"] == contributor_handoff.RESULT_PATH
    assert projected["computed_by"] == "patch-author-harness"
    assert projected["generated_json_temporary"] is True
    assert projected["generated_schema_temporary"] is True

    result = {
        "patch_series_branch": "ai-org/patch-series/example",
        "node_key": "follow-up-08",
        "acknowledged_patchwork_checks": {"counter": 3},
        "must_answer_outcomes": {"approach:codec": {"outcome": "check_passed"}},
    }
    prepared = contributor_handoff.prepare_result(result)
    carrier = contributor_handoff.validated_functional_carrier(prepared.canonical.data)
    assert carrier.validated is True
    assert carrier.temporary is True
    assert carrier.result["patch_series_branch"] == result["patch_series_branch"]


def test_historical_and_canonical_result_contracts_have_equal_projections():
    canonical = contributor_handoff.prepare_result_contract().canonical.data
    historical = json.dumps(implementation_result_schema()).encode("utf-8")

    assert contributor_handoff.parse_result_contract(canonical) == (
        contributor_handoff.parse_historical_result_contract_schema(historical)
    )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda schema: schema["properties"]["node_key"].update(type="integer"),
        lambda schema: schema["properties"].update(
            invented={"type": "string"}
        ),
    ],
    ids=["type-coerced-anchor", "extra-declared-field"],
)
def test_historical_result_contract_rejects_nonexact_result_shape(mutate):
    historical = implementation_result_schema()
    mutate(historical)

    with pytest.raises(ValueError, match="historical implementation result contract is incompatible"):
        contributor_handoff.parse_historical_result_contract_schema(
            json.dumps(historical)
        )


def test_cold_clone_reads_historical_contract_without_persistence_mutation(tmp_path):
    repo = tmp_path / "clone"
    repo.mkdir()

    def git(*args):
        return subprocess.run(
            ["git", "-C", str(repo), *args],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        ).stdout.strip()

    git("init", "-q")
    git("config", "user.name", "Test")
    git("config", "user.email", "test@example.com")
    historical = repo / contributor_handoff.LEGACY_RESULT_SCHEMA_PATH
    historical.parent.mkdir(parents=True)
    historical.write_text(json.dumps(implementation_result_schema()), encoding="utf-8")
    git("add", contributor_handoff.LEGACY_RESULT_SCHEMA_PATH)
    git("commit", "-q", "-m", "historical contract")
    before_head = git("rev-parse", "HEAD")
    before_status = git("status", "--porcelain")

    projected = contributor_handoff.read_result_contract(repo, "HEAD")

    assert projected == contributor_handoff.result_contract_body()
    assert git("rev-parse", "HEAD") == before_head
    assert git("status", "--porcelain") == before_status == ""


def test_result_contract_read_rejects_canonical_and_historical_coordinates(tmp_path):
    repo = tmp_path / "clone"
    repo.mkdir()

    def git(*args):
        return subprocess.run(
            ["git", "-C", str(repo), *args],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        ).stdout.strip()

    git("init", "-q")
    git("config", "user.name", "Test")
    git("config", "user.email", "test@example.com")
    canonical = repo / contributor_handoff.RESULT_CONTRACT_PATH
    historical = repo / contributor_handoff.LEGACY_RESULT_SCHEMA_PATH
    canonical.parent.mkdir(parents=True)
    canonical.write_bytes(contributor_handoff.prepare_result_contract().canonical.data)
    historical.write_text(json.dumps(implementation_result_schema()), encoding="utf-8")
    git("add", contributor_handoff.RESULT_CONTRACT_PATH, contributor_handoff.LEGACY_RESULT_SCHEMA_PATH)
    git("commit", "-q", "-m", "ambiguous contracts")

    with pytest.raises(contributor_handoff.AmbiguousRepresentation, match="both canonical and legacy"):
        contributor_handoff.read_result_contract(repo, "HEAD")


def test_functional_carrier_registers_source_and_target_contracts_independently():
    recipe = contributor_handoff.FUNCTIONAL_CHECK_RECIPE

    assert contributor_handoff.FUNCTIONAL_CARRIER_CONTRACTS["source"] == (
        contributor_handoff.RESULT_CONTEXT,
        contributor_handoff.RESULT_BODY_CONTRACT,
    )
    assert contributor_handoff.FUNCTIONAL_CARRIER_CONTRACTS["target"] == (
        contributor_handoff.FUNCTIONAL_ACCEPTANCE_CONTEXT,
        contributor_handoff.FUNCTIONAL_ACCEPTANCE_CONTRACT,
    )
    assert recipe.source_context != recipe.target_context
    assert recipe.source_contract != recipe.target_contract
    prepared = contributor_handoff.prepare_functional_carrier()
    assert prepared.contract == contributor_handoff.FUNCTIONAL_CARRIER_CONTRACT
    assert contributor_handoff.functional_carrier_recipe()["schema_profile"] == (
        contributor_handoff.SCHEMA_PROFILE
    )


def test_functional_carrier_recipe_rejects_a_target_using_the_source_contract():
    with pytest.raises(ValueError, match="target contract is not registered"):
        contributor_handoff.FunctionalCheckCarrierRecipe(
            contributor_handoff.RESULT_CONTEXT,
            contributor_handoff.RESULT_BODY_CONTRACT,
            contributor_handoff.FUNCTIONAL_ACCEPTANCE_CONTEXT,
            contributor_handoff.RESULT_BODY_CONTRACT,
            contributor_handoff.PROJECTION_PROFILE,
            contributor_handoff.SCHEMA_PROFILE,
        )


def test_validation_precedes_functional_dispatch():
    with pytest.raises(CodecFailure):
        contributor_handoff.validated_functional_carrier(
            b'{"patch_series_branch":"wrong","node_key":"n"}\n'
        )


def test_result_transition_binds_exact_anchor_and_complete_outcomes():
    result = {
        "patch_series_branch": "ai-org/patch-series/example",
        "node_key": "follow-up-08",
        "acknowledged_patchwork_checks": {"counter": 3, "ready": True},
        "must_answer_outcomes": {"approach:codec": {"outcome": "check_passed"}},
    }

    contributor_handoff.validate_result_transition(
        result,
        expected_series_branch="ai-org/patch-series/example",
        expected_node_key="follow-up-08",
        question_ids=["approach:codec"],
    )

    with pytest.raises(ValueError, match="exact series/node anchor"):
        contributor_handoff.validate_result_transition(
            result,
            expected_series_branch="ai-org/patch-series/other",
            expected_node_key="follow-up-08",
        )
    with pytest.raises(ValueError, match="outcomes do not match"):
        contributor_handoff.validate_result_transition(
            result,
            expected_series_branch="ai-org/patch-series/example",
            expected_node_key="follow-up-08",
            question_ids=["approach:codec", "approach:missing"],
        )


def test_result_body_rejects_untyped_patchwork_acknowledgements():
    with pytest.raises(CodecFailure):
        contributor_handoff.prepare_result(
            {
                "patch_series_branch": "ai-org/patch-series/example",
                "node_key": "follow-up-08",
                "acknowledged_patchwork_checks": {"counter": {"value": 3}},
            }
        )


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_result_transition_rejects_non_json_numeric_acknowledgements(value):
    result = {
        "patch_series_branch": "ai-org/patch-series/example",
        "node_key": "follow-up-08",
        "acknowledged_patchwork_checks": {"counter": value},
    }

    with pytest.raises(ValueError, match="typed JSON scalars"):
        contributor_handoff.validate_result_transition(
            result,
            expected_series_branch="ai-org/patch-series/example",
            expected_node_key="follow-up-08",
        )


def test_checkout_transition_accepts_one_legacy_representation(tmp_path):
    legacy = tmp_path / contributor_handoff.LEGACY_RESULT_PATH
    value = {
        "patch_series_branch": "ai-org/patch-series/example",
        "node_key": "follow-up-08",
        "acknowledged_patchwork_checks": {"counter": 8},
    }
    legacy.write_text(json.dumps(value), encoding="utf-8")

    carrier = contributor_handoff.read_checkout_member(
        tmp_path,
        contributor_handoff.RESULT_PATH,
        contributor_handoff.LEGACY_RESULT_PATH,
        contributor_handoff.validated_functional_carrier,
    )

    assert carrier is not None
    assert carrier.result == value


def test_checkout_transition_rejects_split_brain_representations(tmp_path):
    value = {
        "patch_series_branch": "ai-org/patch-series/example",
        "node_key": "follow-up-08",
        "acknowledged_patchwork_checks": {"counter": 8},
    }
    prepared = contributor_handoff.prepare_result(value)
    (tmp_path / contributor_handoff.RESULT_PATH).write_bytes(prepared.canonical.data)
    (tmp_path / contributor_handoff.LEGACY_RESULT_PATH).write_text(
        json.dumps(value), encoding="utf-8"
    )

    with pytest.raises(ValueError, match="both canonical and legacy"):
        contributor_handoff.read_checkout_member(
            tmp_path,
            contributor_handoff.RESULT_PATH,
            contributor_handoff.LEGACY_RESULT_PATH,
            contributor_handoff.validated_functional_carrier,
        )


def test_promotion_transition_canonicalizes_historical_handoff_inputs():
    experience = {"implementation_experience_reports": []}

    files = contributor_handoff.prepare_series_promotion_files(
        {
            contributor_handoff.LEGACY_QUESTIONS_PATH: json.dumps(_question()),
            contributor_handoff.LEGACY_EXPERIENCE_PATH: json.dumps(experience),
            "unrelated.txt": "retained",
        }
    )

    assert set(files) == {
        contributor_handoff.RESULT_CONTRACT_PATH,
        contributor_handoff.QUESTIONS_PATH,
        contributor_handoff.EXPERIENCE_PATH,
        "unrelated.txt",
    }
    assert contributor_handoff.parse_questions(files[contributor_handoff.QUESTIONS_PATH]) == _question()
    assert contributor_handoff.parse_experience(files[contributor_handoff.EXPERIENCE_PATH]) == experience
    assert files["unrelated.txt"] == "retained"


def test_promotion_transition_rejects_split_brain_before_emitting_files():
    with pytest.raises(contributor_handoff.AmbiguousRepresentation, match="both canonical and legacy"):
        contributor_handoff.prepare_series_promotion_files(
            {
                contributor_handoff.QUESTIONS_PATH: _question(),
                contributor_handoff.LEGACY_QUESTIONS_PATH: _question(),
            }
        )


@pytest.mark.parametrize(
    "path",
    [
        contributor_handoff.RESULT_CONTRACT_PATH,
        contributor_handoff.LEGACY_RESULT_SCHEMA_PATH,
        contributor_handoff.RESULT_PATH,
        contributor_handoff.LEGACY_RESULT_PATH,
    ],
)
def test_promotion_transition_reserves_contract_and_result_for_harnesses(path):
    with pytest.raises(ValueError, match="written only by"):
        contributor_handoff.prepare_series_promotion_files({path: {}})
