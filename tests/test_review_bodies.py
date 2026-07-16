from __future__ import annotations

import json
import subprocess

import pytest

from ai_org import review_bodies
from ai_org import git_wrapper
from ai_org.patchwork_queue import review


def _round_record() -> dict[str, object]:
    return {
        "patch_series_id": "series-1",
        "branch": "ai-org/patch-series/series-1",
        "round": 1,
        "inputs": {"node_ids": ["problem"]},
        "axis_reviews": [
            {"axis": "scope", "verdict": "objections_pending", "future_evidence": {"accepted": True}},
            {"axis": "approach", "verdict": "approved"},
        ],
        "objections": [{"objection_id": "scope:1", "anchor_node_ids": ["problem"], "status": "open"}],
        "per_axis_verdicts": {"scope": "objections_pending", "approach": "approved"},
        "consolidation": {"verdict": "needs_revision"},
        "verdict": "needs_revision",
        "git_result_marker": "patch_series: needs-revision round 1",
        "serial": "0001",
        "deferred": [],
        "future_top_level_property": "tolerated",
    }


def _response() -> dict[str, object]:
    return {
        "patch_series_id": "series-1",
        "review_round": 1,
        "author_version": 2,
        "affected_steps": ["problem"],
        "changed_node_ids": ["problem"],
        "requester_assumption_notes": [],
        "objections": [{"objection_id": "scope:1", "status": "answered"}],
        "memento": ["append-only"],
    }


def _delta() -> dict[str, object]:
    return {
        "patch_series_id": "series-1",
        "review_round": 1,
        "author_version": 2,
        "added_node_ids": [],
        "changed_node_ids": ["problem"],
        "pruned_node_ids": [],
        "node_replacements": [{"from": "problem", "to": "problem"}],
        "objection_responses": [
            {"objection_id": "scope:1", "classification": "addressed_by_change", "anchor_node_ids": ["problem"]}
        ],
        "memento": ["review-facing delta"],
    }


def _synthetic_round_record() -> dict[str, object]:
    record = _round_record()
    record["lineage_escalation"] = True
    record["escalated_child_address"] = "series-1:sub/child"
    return record


def test_typed_recipes_preflight_one_canonical_immutable_tree():
    record = _round_record()
    files = {
        "patch-series-review-rounds/round-0001-direction-review-record.json": record,
        "patch-series-review-rounds/round-0001-author-v0002-response-record.json": _response(),
        "patch-series-review-rounds/round-0001-revision-delta.json": _delta(),
    }

    aggregate = review_bodies.prepare_reform_tree(files)

    assert aggregate.validated and aggregate.temporary
    assert len(aggregate.prepared) == 3
    assert all(path.endswith(".cue") for path in aggregate.files)
    assert not any(path.endswith(".json") for path in aggregate.files)
    projected = review_bodies.parse_record(
        aggregate.files["patch-series-review-rounds/round-0001-direction-review-record.cue"],
        review_bodies.CONSOLIDATION_RECIPE,
    )
    assert projected["objections"][0]["anchor_node_ids"] == ["problem"]
    assert projected["round"] == review_bodies.parse_record(
        json.dumps(record), review_bodies.CONSOLIDATION_RECIPE
    )["round"]
    assert projected["future_top_level_property"] == "tolerated"


def test_recipe_roles_are_independent_and_preserve_established_aggregate_shape():
    scope = review_bodies.axis_recipe("scope")
    approach = review_bodies.axis_recipe("approach")

    assert scope.recipe_id != approach.recipe_id
    assert scope.role == approach.role == "axis"
    assert review_bodies.CONSOLIDATION_RECIPE.role == "consolidation"
    assert review_bodies.AUTHOR_RESPONSE_RECIPE.role == "reform"
    assert review_bodies.REVISION_DELTA_RECIPE.role == "reform"
    assert review_bodies.SYNTHETIC_RECIPE.role == "synthetic"
    assert scope.source_contract == scope.target_contract == review_bodies.REVIEW_CONTRACT
    assert approach.source_contract == approach.target_contract == review_bodies.REVIEW_CONTRACT
    assert review_bodies.CONSOLIDATION_RECIPE.source_contract == review_bodies.REVIEW_CONTRACT
    assert review_bodies.AUTHOR_RESPONSE_RECIPE.source_contract == review_bodies.AUTHOR_RESPONSE_CONTRACT
    assert review_bodies.REVISION_DELTA_RECIPE.source_contract == review_bodies.REVISION_DELTA_CONTRACT
    assert review_bodies.SYNTHETIC_RECIPE.source_context == review_bodies.SYNTHETIC_CONTEXT
    assert review_bodies.SYNTHETIC_RECIPE.source_contract == review_bodies.REVIEW_CONTRACT
    assert all(
        recipe.required_all_properties
        for recipe in (
            scope,
            approach,
            review_bodies.CONSOLIDATION_RECIPE,
            review_bodies.AUTHOR_RESPONSE_RECIPE,
            review_bodies.REVISION_DELTA_RECIPE,
            review_bodies.SYNTHETIC_RECIPE,
        )
    )
    assert isinstance(review_bodies.parse_record(json.dumps(_round_record()), scope)["axis_reviews"], list)


def test_recipe_cannot_narrow_required_all_properties_policy():
    with pytest.raises(ValueError, match="required-all-properties-policy"):
        review_bodies.ReviewCarrierRecipe(
            "axis:scope:narrowed",
            "axis",
            review_bodies.REVIEW_CONTEXT,
            review_bodies.REVIEW_CONTEXT,
            review_bodies.REVIEW_CONTRACT,
            review_bodies.REVIEW_CONTRACT,
            ("patch_series_id",),
            required_all_properties=False,
        )


def test_recipe_uses_independently_typed_source_and_target_contexts():
    value = _round_record()
    prepared = review_bodies.prepare_direction_review(value)
    recipe = review_bodies.ReviewCarrierRecipe(
        "axis:scope:synthetic-target:v1",
        "axis",
        review_bodies.REVIEW_CONTEXT,
        review_bodies.SYNTHETIC_CONTEXT,
        review_bodies.REVIEW_CONTRACT,
        review_bodies.REVIEW_CONTRACT,
        ("patch_series_id", "round", "axis_reviews", "verdict"),
        source_axis="scope",
    )

    class ContextRecordingCodec:
        def __init__(self):
            self.calls = []

        def prepare(self, context_id, source, *, expected):
            self.calls.append(("prepare", context_id, expected))
            return prepared

        def parse(self, context_id, data, *, expected):
            self.calls.append(("parse", context_id, expected))
            return value

    codec = ContextRecordingCodec()
    recipe.prepare(value, codec)

    assert codec.calls == [
        ("prepare", review_bodies.REVIEW_CONTEXT, review_bodies.REVIEW_CONTRACT),
        ("parse", review_bodies.SYNTHETIC_CONTEXT, review_bodies.REVIEW_CONTRACT),
    ]


def test_recipe_rejects_source_projection_with_the_wrong_target_contract():
    class WrongContractCodec:
        def prepare(self, context_id, value, *, expected):
            assert context_id == review_bodies.AUTHOR_RESPONSE_CONTEXT
            assert expected == review_bodies.AUTHOR_RESPONSE_CONTRACT
            return review_bodies.prepare_revision_delta(_delta())

    with pytest.raises(ValueError, match="reform:author-response:v1:target-contract-mismatch"):
        review_bodies.AUTHOR_RESPONSE_RECIPE.prepare(_response(), WrongContractCodec())


@pytest.mark.parametrize(
    ("recipe", "value"),
    (
        (review_bodies.axis_recipe("scope"), _round_record()),
        (review_bodies.CONSOLIDATION_RECIPE, _round_record()),
        (review_bodies.AUTHOR_RESPONSE_RECIPE, _response()),
        (review_bodies.REVISION_DELTA_RECIPE, _delta()),
        (review_bodies.SYNTHETIC_RECIPE, _synthetic_round_record()),
    ),
)
def test_typed_recipe_rejects_source_to_target_identity_or_status_drift(recipe, value):
    prepared = recipe.prepare(value, review_bodies.BodyCodecClient())

    class DriftingCodec:
        def prepare(self, context_id, source, *, expected):
            assert context_id == recipe.source_context
            assert source == value
            assert expected == recipe.source_contract
            return prepared

        def parse(self, context_id, data, *, expected):
            assert context_id == recipe.target_context
            assert data == prepared.canonical.data
            assert expected == recipe.target_contract
            projected = json.loads(json.dumps(value))
            if "round" in projected:
                projected["round"] += 1
            else:
                projected["review_round"] += 1
            return projected

    with pytest.raises(ValueError, match=f"{recipe.recipe_id}:source-target-drift"):
        recipe.prepare(value, DriftingCodec())


def test_axis_recipe_rejects_an_aggregate_that_does_not_contain_its_source_axis():
    record = _round_record()
    record["axis_reviews"] = [{"axis": "approach", "verdict": "approved"}]

    with pytest.raises(ValueError, match="axis:scope:v1:source-axis-not-present"):
        review_bodies.axis_recipe("scope").recognize(record)


def test_prepare_transition_requires_at_least_one_independently_typed_axis():
    record = _round_record()
    record["axis_reviews"] = []

    with pytest.raises(ValueError, match="direction-review:axis_reviews-empty"):
        review_bodies.prepare_immutable_tree(
            {"patch-series-review-rounds/round-0001-direction-review-record.json": record}
        )


def test_direction_transition_executes_each_axis_recipe_before_consolidation():
    value = _round_record()
    delegate = review_bodies.BodyCodecClient()

    class RecordingCodec:
        def __init__(self):
            self.calls = []

        def prepare(self, context_id, source, *, expected):
            self.calls.append(("prepare", context_id, expected))
            return delegate.prepare(context_id, source, expected=expected)

        def parse(self, context_id, data, *, expected):
            self.calls.append(("parse", context_id, expected))
            return delegate.parse(context_id, data, expected=expected)

    codec = RecordingCodec()
    prepared = review_bodies.prepare_direction_review(value, client=codec)

    assert prepared.context_id == review_bodies.REVIEW_CONTEXT
    assert codec.calls == [
        (operation, review_bodies.REVIEW_CONTEXT, review_bodies.REVIEW_CONTRACT)
        for _recipe in ("axis:scope:v1", "axis:approach:v1", "consolidation:v1")
        for operation in ("prepare", "parse")
    ]


def test_marker_and_reform_transitions_preserve_the_carrier_aggregate_shape():
    files = {
        "patch-series-review-rounds/round-0001-direction-review-record.json": _round_record()
    }

    marker = review_bodies.prepare_marker_tree(files)
    reform = review_bodies.prepare_reform_tree(files)

    assert marker.files == reform.files
    assert marker.prepared[0].contract == reform.prepared[0].contract
    assert marker.validated is reform.validated is True
    assert marker.temporary is reform.temporary is True


def test_already_prepared_update_is_revalidated_during_tree_preflight():
    prepared = review_bodies.prepare_author_response(_response())
    path = "patch-series-review-rounds/round-0001-author-v0002-response-record.cue"

    class RecordingCodec:
        def __init__(self):
            self.calls = []

        def parse(self, context_id, data, *, expected):
            self.calls.append((context_id, data, expected))
            return {"validated": True}

    codec = RecordingCodec()
    aggregate = review_bodies.prepare_immutable_tree(
        {path: prepared}, client=codec
    )

    assert codec.calls == [(prepared.context_id, prepared.canonical.data, prepared.contract)]
    assert aggregate.files[path] == prepared.canonical.data.decode("utf-8")


def test_unregistered_prepared_path_is_rejected_before_publication():
    prepared = review_bodies.prepare_author_response(_response())

    with pytest.raises(ValueError, match="unregistered-prepared-path:technical-approach-plan.cue"):
        review_bodies.prepare_immutable_tree({"technical-approach-plan.cue": prepared})


def test_invalid_prepared_update_yields_no_carrier_aggregate():
    prepared = review_bodies.prepare_author_response(_response())

    class RejectingCodec:
        def parse(self, *_args, **_kwargs):
            raise ValueError("invalid canonical update")

    with pytest.raises(ValueError, match="invalid canonical update"):
        review_bodies.prepare_immutable_tree(
            {
                "patch-series-review-rounds/round-0001-author-v0002-response-record.cue": prepared
            },
            client=RejectingCodec(),
        )


@pytest.mark.parametrize(
    ("path", "expected_context", "expected_contract"),
    (
        (
            "patch-series-cover-letter.cue",
            "patch-series-cover-letter-v1",
            review_bodies.ContractIdentity(
                "ai-org-cue-body-v1", "PatchSeriesCoverLetter", "patch-series-root"
            ),
        ),
        (
            "sub/child/patch-series-cover-letter.cue",
            "child-patch-series-cover-letter-v1",
            review_bodies.ContractIdentity(
                "ai-org-cue-body-v1", "PatchSeriesCoverLetter", "patch-series-child"
            ),
        ),
        (
            "sub/child/technical-approach-plan.cue",
            "child-technical-approach-tree-v1",
            review_bodies.ContractIdentity(
                "ai-org-cue-body-v1", "TechnicalApproachTree", "patch-series-child"
            ),
        ),
    ),
)
def test_already_migrated_cover_and_approach_bytes_are_typed_and_revalidated(
    path, expected_context, expected_contract
):
    class RecordingCodec:
        def __init__(self):
            self.calls = []

        def parse(self, context_id, data, *, expected):
            self.calls.append((context_id, data, expected))
            return {"validated": True}

    codec = RecordingCodec()
    canonical = 'apiVersion: "ai-org-cue-body-v1"\n'

    aggregate = review_bodies.prepare_immutable_tree({path: canonical}, client=codec)

    assert codec.calls == [(expected_context, canonical.encode(), expected_contract)]
    assert aggregate.files[path] == canonical


def test_invalid_already_migrated_update_fails_before_carrier_aggregate():
    class RejectingCodec:
        def parse(self, context_id, data, *, expected):
            assert context_id == "child-technical-approach-tree-v1"
            raise ValueError("invalid canonical child approach")

    with pytest.raises(ValueError, match="invalid canonical child approach"):
        review_bodies.prepare_immutable_tree(
            {"sub/child/technical-approach-plan.cue": "not canonical"},
            client=RejectingCodec(),
        )


def test_prepared_child_update_must_match_its_path_contract():
    prepared_response = review_bodies.prepare_author_response(_response())

    with pytest.raises(ValueError, match="prepared-contract-mismatch"):
        review_bodies.prepare_immutable_tree(
            {"sub/child/technical-approach-plan.cue": prepared_response}
        )


def test_prepared_review_member_must_match_its_typed_record_path():
    prepared_delta = review_bodies.prepare_revision_delta(_delta())

    with pytest.raises(ValueError, match="prepared-contract-mismatch"):
        review_bodies.prepare_immutable_tree(
            {"patch-series-review-rounds/round-0001-direction-review-record.cue": prepared_delta}
        )


@pytest.mark.parametrize(
    ("path", "prepare", "value", "expected_context"),
    (
        (
            "patch-series-review-rounds/round-0001-direction-review-record.cue",
            review_bodies.prepare_direction_review,
            _round_record,
            review_bodies.REVIEW_CONTEXT,
        ),
        (
            "patch-series-review-rounds/round-0001-author-v0002-response-record.cue",
            review_bodies.prepare_author_response,
            _response,
            review_bodies.AUTHOR_RESPONSE_CONTEXT,
        ),
        (
            "patch-series-review-rounds/round-0001-revision-delta.cue",
            review_bodies.prepare_revision_delta,
            _delta,
            review_bodies.REVISION_DELTA_CONTEXT,
        ),
    ),
)
def test_already_canonical_review_member_is_preflighted_before_publication(
    path, prepare, value, expected_context
):
    prepared = prepare(value())

    class RecordingCodec:
        def __init__(self):
            self.calls = []

        def parse(self, context_id, data, *, expected):
            self.calls.append((context_id, data, expected))
            return {}

    codec = RecordingCodec()

    aggregate = review_bodies.prepare_immutable_tree(
        {path: prepared.canonical.data}, client=codec
    )

    assert aggregate.files[path] == prepared.canonical.data.decode("utf-8")
    assert aggregate.prepared == ()
    assert codec.calls == [
        (expected_context, prepared.canonical.data, prepared.contract)
    ]


def test_already_canonical_synthetic_review_uses_its_registered_context():
    prepared = review_bodies.prepare_direction_review(_synthetic_round_record(), synthetic=True)

    class RecordingCodec:
        def __init__(self):
            self.calls = []

        def parse(self, context_id, data, *, expected):
            self.calls.append((context_id, data, expected))
            return {"lineage_escalation": True}

    codec = RecordingCodec()
    path = "patch-series-review-rounds/round-0001-direction-review-record.cue"
    review_bodies.prepare_immutable_tree({path: prepared.canonical.data}, client=codec)

    assert [call[0] for call in codec.calls] == [
        review_bodies.REVIEW_CONTEXT,
        review_bodies.SYNTHETIC_CONTEXT,
    ]


def test_failed_member_preflight_returns_no_partial_carrier_aggregate():
    invalid_delta = _delta()
    del invalid_delta["node_replacements"]

    with pytest.raises(ValueError, match="missing-required:node_replacements"):
        review_bodies.prepare_immutable_tree(
            {
                "patch-series-review-rounds/round-0001-direction-review-record.json": _round_record(),
                "patch-series-review-rounds/round-0001-revision-delta.json": invalid_delta,
            }
        )


def test_synthetic_network_recipe_requires_visible_lineage_status():
    synthetic = _round_record()
    synthetic["lineage_escalation"] = True
    prepared = review_bodies.prepare_direction_review(synthetic, synthetic=True)

    assert prepared.context_id == review_bodies.SYNTHETIC_CONTEXT
    del synthetic["lineage_escalation"]
    with pytest.raises(ValueError, match="missing-required:lineage_escalation"):
        review_bodies.prepare_direction_review(synthetic, synthetic=True)


def test_marker_publication_transitions_once_and_rejection_leaves_git_unchanged(tmp_path):
    subprocess.run(["git", "-C", str(tmp_path), "init", "-b", "main"], check=True, capture_output=True)
    (tmp_path / "README").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "README"], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), *git_wrapper.identity_config_args(), "commit", "-m", "base"],
        check=True,
        capture_output=True,
    )
    branch = "ai-org/patch-series/series-1"
    subprocess.run(["git", "-C", str(tmp_path), "branch", branch], check=True)

    path = review.round_record_path(1)
    published = review._write_marker(
        tmp_path, branch, "patch_series: needs-revision round 1", files={path: _round_record()}
    )
    paths = set(git_wrapper.tree_files(tmp_path, branch, "patch-series-review-rounds"))
    assert paths == {path}
    assert published["commit"] == git_wrapper.head_sha(tmp_path, branch)

    before = git_wrapper.head_sha(tmp_path, branch)
    invalid = _round_record()
    del invalid["serial"]
    with pytest.raises(Exception):
        review._write_marker(
            tmp_path, branch, "patch_series: needs-revision round 2", files={review.round_record_path(2): invalid}
        )
    assert git_wrapper.head_sha(tmp_path, branch) == before
