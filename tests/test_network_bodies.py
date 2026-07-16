from __future__ import annotations

from dataclasses import replace
import subprocess

import pytest

from ai_org import git_wrapper, network_bodies
from ai_org.body_codec import BodyCodecClient
from ai_org.patchwork_queue import patch_series_gate as network


def _cover() -> dict:
    return {
        "raw_request":"child", "working_title":"Child", "request_type":"feature",
        "problem_or_motivation":"p", "intended_users_or_jobs":"u", "desired_outcomes_success":"d",
        "affected_area_platform":"a",
        "tech_stack":{"build_strategy":"engine_based","engine":"AI Org","framework":"","language":"Python","platform":"Git","rationale":"r","provenance":"requester_specified"},
        "user_experience_requirements":{
            "applicability":{"applicability":"not_user_facing","not_user_facing_reason":"internal"},
            "experience_identity":{"named_reference":"","genre_conventions":"","must_resemble":"","must_not_resemble":""},
            "presentation_model":{"camera_and_view":"","world_readability":"","ui_taxonomy_notes":""},
            "core_status_surfaces":{"player_status":"","opposition_status":"","inventory_resources":"","objective_progress":"","location_identity":""},
            "entity_affordances":{"interactive_entities":"","exits_and_transitions":"","gates_and_locks":"","hazards_and_bosses":"","collectibles":"","decorative_elements":""},
            "action_feedback_matrix":[],
            "progression_legibility":{"current_goal_visibility":"","locked_state_feedback":"","unlocked_state_feedback":"","flag_observability":"","ending_state_consistency":""},
            "hud_and_ui_flow":{"primary_hud":"","secondary_screens":"","menu_flow":"","dialog_flow":"","failure_and_recovery":""},
            "visual_language_constraints":{"contrast":"","palette_role":"","silhouette_readability":"","labels_and_markers":"","animation_minimums":""},
            "accessibility_baseline":{"controls":"","text_readability":"","color_independence":"","audio_independence":"","pacing":""},
            "acceptance_tests":{"screenshot_checks":[],"interaction_checks":[],"playtest_checks":[]},
        },
        "background_facts":"", "constraints_assumptions":[], "references":[], "grounding_provenance":"lineage",
        "open_questions":[], "non_goals_out_of_scope":[], "proposal_hint":"", "alternatives_considered":[],
    }


def _empty_commission_request() -> dict:
    return {
        "schema": "ai-org-commission-request-v1",
        "spine_contract_refs": [],
        "identity_manifest_fields": [],
        "acceptance_checks": [],
        "contract_fields": [],
    }


def _files() -> dict:
    edge = {"type":"depends","to":"schema","with":"","state":"accepted","or_group":"","when":"on_rebaseline","reason":"required"}
    child_projection = {"child_key":"codec","node_path":"sub/codec","lifecycle_status":"ready_for_patch_authoring","edges":[edge]}
    root = {"schema":"patch_series-network-node-v1","identity_stage":"serialized-parent","node_path":".","branch":"ai-org/patch-series/demo","relation_from_parent":"root","lifecycle_status":"active","ownership":{"request_owner":"requester","interior_owner":"parent"},"write_scope":{"allowed_subtree":"."},"scope_item_ids":["goal"],"children":[child_projection]}
    child = {"schema":"patch_series-network-node-v1","identity_stage":"forming","id":"demo:sub/codec","child_key":"codec","node_path":"sub/codec","parent_branch":"ai-org/patch-series/demo","relation_from_parent":"split-into","split_operator":"AND","edges":[edge],"scope_item_ids":["goal"],"lifecycle_status":"ready_for_patch_authoring","ownership":{"request_owner":"parent","interior_owner":"child"},"write_scope":{"allowed_subtree":"sub/codec/"},"contrib_branch":"ai-org/contrib/demo-codec"}
    ledger_child = {"id":"demo:sub/codec","serial_id":"","child_key":"codec","node_path":"sub/codec","address":"ai-org/patch-series/demo:sub/codec","allowed_subtree":"sub/codec/","contrib_branch":"ai-org/contrib/demo-codec","lifecycle_status":"ready_for_patch_authoring","edges":[edge],"scope_item_ids":["goal"]}
    coverage = {"scope_item_id":"goal","owner":"codec","owner_node_path":"sub/codec"}
    ledger = {"schema":"series-coverage-ledger-v1","ledger_revision":7,"supersedes_ledger_commit":"","rebaselined_from_escalation":{},"parent_branch":"ai-org/patch-series/demo","relation":"split-into","split_operator":"AND","scope_items":[{"id":"goal","kind":"desired_outcome","text":"works"}],"coverage":[coverage],"parent_retained_scope_ids":[],"children":[ledger_child]}
    rollup = {"schema":"patch-queue-status-rollup-v1","generated":True,"generated_from":"patch-series-manifest.json","ledger_schema":"series-coverage-ledger-v1","node_path":".","children":[child_projection],"coverage":[coverage]}
    cover = _cover()
    return {
        "patch-series-manifest.json":root,
        "series-coverage-ledger.json":ledger,
        "patch-queue-status-rollup.json":rollup,
        "patch-series-metadata.json":{"schema":"patch_series-network-node-v1","lifecycle_status":"active","ledger_commit":""},
        "sub/codec/patch-series-manifest.json":child,
        "sub/codec/maintainer-series-request.json":{"id":"demo:sub/codec","submitted_at":"","request":cover,"commission_request":_empty_commission_request(),"provenance":{"requester":"parent","parent_branch":"ai-org/patch-series/demo","parent_node_path":".","child_key":"codec"}},
        "sub/codec/patch-series-metadata.json":{"schema":"patch_series-network-node-v1","lifecycle_status":"ready_for_patch_authoring","id":"demo:sub/codec","edges":[edge]},
        "sub/codec/patch-series-cover-letter.json":cover,
        "sub/codec/technical-approach-plan.json":{"lineage_child":{"id":"demo:sub/codec","lifecycle_status":"ready_for_patch_authoring","summary":"codec","systems":["codec"],"acceptance_criteria":["passes"],"functional_check":"pytest","ux_acceptance_tests":[],"risks":[]}},
    }


def test_network_publication_preflights_one_alias_free_tree_and_preserves_edges():
    prepared = network_bodies.prepare_network_publication(_files())

    assert len(prepared.members) == 9
    assert len(prepared.carrier_recipes) == 9
    assert all(path.endswith(".cue") for path in prepared.files)
    assert set(prepared.aliases) == set(_files())
    child = next(member for member in prepared.members if member.context == "child-network-node-manifest-v1")
    projection = network_bodies.read_network_body  # public reader is part of the same boundary
    assert child.historical_path == "sub/codec/patch-series-manifest.json"
    assert b'on_rebaseline' in child.prepared.canonical.data
    assert projection is not None


def test_ordinary_network_transitions_prepare_complete_historical_successors_before_cas(
    tmp_path,
):
    repo = _repo(tmp_path)
    branch = "ai-org/patch-series/demo"
    git_wrapper.create_branch_with_files(
        repo,
        branch,
        "main",
        {
            "patch-series-cover-letter.json": _cover(),
            "technical-approach-plan.json": {},
            **_files(),
        },
        commit_message="historical network",
    )
    before = git_wrapper.head_sha(repo, branch)

    transitions = [
        network.prepare_network_transition(repo, branch, {}, route=route)
        for route in sorted(network.ORDINARY_NETWORK_TRANSITION_ROUTES)
    ]

    assert before is not None
    assert all(item.prepared and item.releasable for item in transitions)
    assert {item.route for item in transitions} == set(
        network.ORDINARY_NETWORK_TRANSITION_ROUTES
    )
    assert all(item.source.frozen_root_oid == before for item in transitions)
    assert all(item.prepared_generation == "historical_json" for item in transitions)
    assert all(item.publication.expected_ref_oid == before for item in transitions)
    assert all(
        len(item.prepared_source_vector_sha256) == 64
        and int(item.prepared_source_vector_sha256, 16) >= 0
        for item in transitions
    )
    assert transitions[0].publication is not None
    with pytest.raises(TypeError):
        transitions[0].publication.publication.files[
            "series-coverage-ledger.cue"
        ] = "tampered successor\n"
    assert git_wrapper.head_sha(repo, branch) == before

    released = git_wrapper.publish_network_publication(
        repo, transitions[0].publication
    )

    assert released.status == "updated"
    assert git_wrapper.show_file(repo, branch, "series-coverage-ledger.json") is None
    assert git_wrapper.show_file(repo, branch, "series-coverage-ledger.cue") is not None


def test_ordinary_network_transition_rejects_old_new_coexistence_without_ref_mutation(
    tmp_path,
):
    repo = _repo(tmp_path)
    branch = "ai-org/patch-series/demo"
    canonical_ledger = network_bodies.prepare_network_publication(_files()).files[
        "series-coverage-ledger.cue"
    ]
    git_wrapper.create_branch_with_files(
        repo,
        branch,
        "main",
        {
            "patch-series-cover-letter.json": _cover(),
            "technical-approach-plan.json": {},
            **_files(),
            "series-coverage-ledger.cue": canonical_ledger,
        },
        commit_message="mixed network",
    )
    before = git_wrapper.head_sha(repo, branch)

    transitions = [
        network.prepare_network_transition(repo, branch, {}, route=route)
        for route in sorted(network.ORDINARY_NETWORK_TRANSITION_ROUTES)
    ]

    assert all(not transition.prepared for transition in transitions)
    assert all(not transition.releasable for transition in transitions)
    assert all(
        transition.diagnostic is not None
        and transition.diagnostic.rule == "coexisting-representations"
        and transition.diagnostic.cue_location == "series-coverage-ledger.json"
        for transition in transitions
    )
    assert git_wrapper.head_sha(repo, branch) == before


@pytest.mark.parametrize(
    "route", sorted(network.ORDINARY_NETWORK_TRANSITION_ROUTES)
)
def test_ordinary_network_transition_rejects_cross_coordinate_generation_mix(
    tmp_path, route
):
    repo = _repo(tmp_path)
    branch = "ai-org/patch-series/demo"
    historical_files = _files()
    canonical_ledger = network_bodies.prepare_network_publication(
        historical_files
    ).files["series-coverage-ledger.cue"]
    del historical_files["series-coverage-ledger.json"]
    git_wrapper.create_branch_with_files(
        repo,
        branch,
        "main",
        {
            "patch-series-cover-letter.json": _cover(),
            "technical-approach-plan.json": {},
            **historical_files,
            "series-coverage-ledger.cue": canonical_ledger,
        },
        commit_message="cross-coordinate mixed network",
    )
    before = git_wrapper.head_sha(repo, branch)

    transition = network.prepare_network_transition(
        repo, branch, {}, route=route
    )

    assert transition.prepared is False
    assert transition.releasable is False
    assert transition.diagnostic is not None
    assert transition.diagnostic.route == route
    assert transition.diagnostic.frozen_root_oid == before
    assert transition.diagnostic.rule == "coexisting-representations"
    assert transition.diagnostic.cue_location == "series-coverage-ledger.json"
    assert "mixes generations across coordinates" in transition.diagnostic.detail
    assert git_wrapper.head_sha(repo, branch) == before


@pytest.mark.parametrize(
    "route", sorted(network.ORDINARY_NETWORK_TRANSITION_ROUTES)
)
def test_ordinary_network_transition_returns_structured_cas_diagnostic(
    tmp_path, route
):
    repo = _repo(tmp_path)
    branch = "ai-org/patch-series/demo"
    git_wrapper.create_branch_with_files(
        repo,
        branch,
        "main",
        {
            "patch-series-cover-letter.json": _cover(),
            "technical-approach-plan.json": {},
            **_files(),
        },
        commit_message="historical network",
    )
    prepared = network.prepare_network_transition(
        repo, branch, {}, route=route
    )
    frozen = prepared.source.frozen_root_oid
    advanced = git_wrapper.commit_files(
        repo,
        branch,
        {"concurrent-change.txt": "ref moved after preflight\n"},
        subject="test: concurrent network movement",
    )["commit"]

    result = network.publish_network_transition(repo, prepared)

    assert result.ok is False
    assert result.commit_oid == ""
    assert result.diagnostic is not None
    assert result.diagnostic.route == route
    assert result.diagnostic.frozen_root_oid == frozen
    assert result.diagnostic.rule == "network-publication-compare-and-swap"
    assert result.diagnostic.cue_location == f"refs/heads/{branch}"
    assert result.expected_ref_oids == ((f"refs/heads/{branch}", frozen),)
    assert result.resulting_ref_oids == ()
    assert git_wrapper.head_sha(repo, branch) == advanced


@pytest.mark.parametrize(
    "route", sorted(network.ORDINARY_NETWORK_TRANSITION_ROUTES)
)
def test_ordinary_network_transition_reports_complete_ref_oid_result(
    tmp_path, route
):
    repo = _repo(tmp_path)
    branch = "ai-org/patch-series/demo"
    git_wrapper.create_branch_with_files(
        repo,
        branch,
        "main",
        {
            "patch-series-cover-letter.json": _cover(),
            "technical-approach-plan.json": {},
            **_files(),
        },
        commit_message="historical network",
    )
    prepared = network.prepare_network_transition(
        repo, branch, {}, route=route
    )
    frozen = prepared.source.frozen_root_oid

    result = network.publish_network_transition(repo, prepared)

    ref = f"refs/heads/{branch}"
    assert result.ok is True
    assert result.expected_ref_oids == ((ref, frozen),)
    assert result.resulting_ref_oids == ((ref, result.commit_oid),)
    assert git_wrapper.head_sha(repo, ref) == result.commit_oid


@pytest.mark.parametrize(
    "route", sorted(network.ORDINARY_NETWORK_TRANSITION_ROUTES)
)
def test_ordinary_network_transition_injected_failure_preserves_ref(
    tmp_path, route
):
    repo = _repo(tmp_path)
    branch = "ai-org/patch-series/demo"
    git_wrapper.create_branch_with_files(
        repo,
        branch,
        "main",
        {
            "patch-series-cover-letter.json": _cover(),
            "technical-approach-plan.json": {},
            **_files(),
        },
        commit_message="historical network",
    )
    prepared = network.prepare_network_transition(
        repo, branch, {}, route=route
    )
    before = prepared.source.frozen_root_oid

    result = network.publish_network_transition(
        repo, prepared, inject_failure=True
    )

    assert result.ok is False
    assert result.diagnostic is not None
    assert result.diagnostic.rule == "injected-publication-failure"
    assert result.expected_ref_oids == (
        (f"refs/heads/{branch}", before),
    )
    assert result.resulting_ref_oids == ()
    assert git_wrapper.head_sha(repo, branch) == before


@pytest.mark.parametrize(
    "route", sorted(network.ORDINARY_NETWORK_TRANSITION_ROUTES)
)
def test_ordinary_network_transition_malformed_successor_preserves_ref(
    tmp_path, route
):
    repo = _repo(tmp_path)
    branch = "ai-org/patch-series/demo"
    git_wrapper.create_branch_with_files(
        repo,
        branch,
        "main",
        {
            "patch-series-cover-letter.json": _cover(),
            "technical-approach-plan.json": {},
            **_files(),
        },
        commit_message="historical network",
    )
    before = git_wrapper.head_sha(repo, branch)

    prepared = network.prepare_network_transition(
        repo,
        branch,
        {"series-coverage-ledger.json": "not a body"},
        route=route,
    )

    assert prepared.prepared is False
    assert prepared.diagnostic is not None
    assert prepared.diagnostic.rule == "network-publication-preflight"
    assert git_wrapper.head_sha(repo, branch) == before


def test_prepared_network_transition_rejects_source_vector_tampering(tmp_path):
    repo = _repo(tmp_path)
    branch = "ai-org/patch-series/demo"
    git_wrapper.create_branch_with_files(
        repo,
        branch,
        "main",
        {
            "patch-series-cover-letter.json": _cover(),
            "technical-approach-plan.json": {},
            **_files(),
        },
        commit_message="historical network",
    )
    prepared = network.prepare_network_transition(
        repo, branch, {}, route="rebaseline"
    )
    before = git_wrapper.head_sha(repo, branch)
    tampered = replace(
        prepared,
        source=replace(prepared.source, root_sha256="f" * 64),
    )

    result = network.publish_network_transition(repo, tampered)

    assert result.ok is False
    assert result.diagnostic is not None
    assert result.diagnostic.rule == "prepared-source-vector-mismatch"
    assert result.diagnostic.frozen_root_oid == before
    assert result.expected_ref_oids == (
        (f"refs/heads/{branch}", before),
    )
    assert result.resulting_ref_oids == ()
    assert git_wrapper.head_sha(repo, branch) == before


def test_prepared_network_transition_rejects_cas_coordinate_tampering(tmp_path):
    repo = _repo(tmp_path)
    branch = "ai-org/patch-series/demo"
    git_wrapper.create_branch_with_files(
        repo,
        branch,
        "main",
        {
            "patch-series-cover-letter.json": _cover(),
            "technical-approach-plan.json": {},
            **_files(),
        },
        commit_message="historical network",
    )
    prepared = network.prepare_network_transition(
        repo, branch, {}, route="stale_marking"
    )
    before = git_wrapper.head_sha(repo, branch)
    assert prepared.publication is not None
    tampered = replace(
        prepared,
        publication=replace(prepared.publication, expected_ref_oid="0" * 40),
    )

    result = network.publish_network_transition(repo, tampered)

    assert result.ok is False
    assert result.diagnostic is not None
    assert result.diagnostic.rule == "prepared-publication-coordinate-mismatch"
    assert result.diagnostic.frozen_root_oid == before
    assert result.expected_ref_oids == (
        (f"refs/heads/{branch}", before),
    )
    assert result.resulting_ref_oids == ()
    assert git_wrapper.head_sha(repo, branch) == before


@pytest.mark.parametrize(
    "path",
    [
        "patch-series-manifest.json",
        "patch-series-manifest.cue",
        "sub/codec/technical-approach-plan.json",
        "sub/codec/technical-approach-plan.cue",
    ],
)
def test_network_coordinate_classification_accepts_canonical_and_historical_aliases(path):
    role, canonical, historical = network_bodies.classify_path(path)

    assert canonical.endswith(".cue")
    assert historical.endswith(".json")
    assert network_bodies.classify_path(canonical) == (role, canonical, historical)
    assert network_bodies.classify_path(historical) == (role, canonical, historical)


def test_network_publication_carrier_source_order_is_mapping_order_independent():
    files = _files()
    reversed_files = dict(reversed(list(files.items())))

    forward = network_bodies.prepare_network_publication(files)
    reverse = network_bodies.prepare_network_publication(reversed_files)

    assert [member.context for member in forward.members] == [
        member.context for member in reverse.members
    ]
    assert [member.canonical_path for member in forward.members] == [
        member.canonical_path for member in reverse.members
    ]
    assert [recipe.canonical.data for recipe in forward.carrier_recipes] == [
        recipe.canonical.data for recipe in reverse.carrier_recipes
    ]


def test_network_publication_rejects_invalid_rebaseline_before_releasing_tree():
    files = _files()
    files["series-coverage-ledger.json"] = {**files["series-coverage-ledger.json"], "ledger_revision":"v8"}

    with pytest.raises(Exception):
        network_bodies.prepare_network_publication(files)


def test_network_publication_rejects_pairwise_child_drift():
    files = _files()
    child = files["sub/codec/patch-series-manifest.json"]
    files["sub/codec/patch-series-manifest.json"] = {**child, "lifecycle_status":"posted_to_mailing_list"}

    with pytest.raises(ValueError, match="pair-mismatch"):
        network_bodies.prepare_network_publication(files)


@pytest.mark.parametrize(
    "missing",
    [
        "series-coverage-ledger.json",
        "patch-queue-status-rollup.json",
        "patch-series-metadata.json",
        "sub/codec/maintainer-series-request.json",
        "sub/codec/patch-series-metadata.json",
        "sub/codec/patch-series-cover-letter.json",
        "sub/codec/technical-approach-plan.json",
    ],
)
def test_network_publication_rejects_incomplete_operational_cohort(missing):
    files = _files()
    del files[missing]

    with pytest.raises(ValueError, match="incomplete-(root|child)-cohort"):
        network_bodies.prepare_network_publication(files)


def test_network_publication_keeps_lineage_recipes_temporary_and_independently_typed():
    prepared = network_bodies.prepare_network_publication(_files())

    recipes = [
        recipe.canonical.data.decode("utf-8") for recipe in prepared.carrier_recipes
    ]
    assert len(recipes) == len(prepared.members)
    assert any('"source_context": "child-patch-series-cover-letter-v1"' in item and
               '"target_context": "maintainer-series-request-v1"' in item for item in recipes)
    assert any('"source_context": "series-coverage-ledger-v1"' in item and
               '"target_context": "patch-queue-status-rollup-v1"' in item for item in recipes)
    assert any('"source_context": "patch-series-metadata-v1"' in item and
               '"target_context": "child-network-node-manifest-v1"' in item for item in recipes)
    assert "lineage-carrier-recipe.cue" not in prepared.files

    files = _files()
    files["lineage-carrier-recipe.json"] = {}
    with pytest.raises(ValueError, match="temporary-recipe-cannot-be-published"):
        network_bodies.prepare_network_publication(files)


def test_root_and_child_metadata_get_coordinate_specific_carrier_targets():
    files = _files()

    prepared = network_bodies.prepare_network_publication(files)
    metadata_recipes = [
        recipe.canonical.data.decode("utf-8")
        for member, recipe in zip(prepared.members, prepared.carrier_recipes, strict=True)
        if member.context == "patch-series-metadata-v1"
    ]

    assert len(metadata_recipes) == 2
    assert any('"target_context": "root-network-node-manifest-v1"' in item
               for item in metadata_recipes)
    assert any('"target_context": "child-network-node-manifest-v1"' in item
               for item in metadata_recipes)


def test_network_publication_preserves_and_or_group_and_when_dependency_semantics():
    files = _files()
    edges = [
        {"type":"depends","to":"schema","with":"","state":"accepted","or_group":"","when":"on_rebaseline","reason":"required"},
        {"type":"depends","to":"linux","with":"","state":"accepted","or_group":"platform","when":"flag == 'open'","reason":"alternative"},
        {"type":"depends","to":"darwin","with":"","state":"accepted","or_group":"platform","when":"flag == 'open'","reason":"alternative"},
    ]
    root = files["patch-series-manifest.json"]
    root_child = {**root["children"][0], "edges": edges}
    files["patch-series-manifest.json"] = {**root, "children": [root_child]}
    ledger = files["series-coverage-ledger.json"]
    ledger_child = {**ledger["children"][0], "edges": edges}
    files["series-coverage-ledger.json"] = {**ledger, "children": [ledger_child]}
    rollup = files["patch-queue-status-rollup.json"]
    files["patch-queue-status-rollup.json"] = {**rollup, "children": [{**rollup["children"][0], "edges": edges}]}
    child = files["sub/codec/patch-series-manifest.json"]
    files["sub/codec/patch-series-manifest.json"] = {**child, "edges": edges}

    prepared = network_bodies.prepare_network_publication(files)
    child_member = next(member for member in prepared.members if member.context == "child-network-node-manifest-v1")

    assert b'on_rebaseline' in child_member.prepared.canonical.data
    assert child_member.prepared.canonical.data.count(b'"or_group": "platform"') == 2


def test_historical_network_read_is_strict_and_does_not_rewrite_history(tmp_path):
    repo = _repo(tmp_path)
    old = git_wrapper.create_ref_with_files(
        repo, "refs/heads/ai-org/patch-series/demo",
        {"series-coverage-ledger.json":_files()["series-coverage-ledger.json"]},
        subject="historical network", parent=_git(repo, "rev-parse", "main"),
    )["commit"]

    body = network_bodies.read_network_body(repo, "refs/heads/ai-org/patch-series/demo", "series-coverage-ledger.json")

    assert body["ledger_revision"].as_int_exact() == 7
    assert network_bodies.resolve_manifest_input(repo, old, ".").state == "legacy"
    assert _git(repo, "rev-parse", "refs/heads/ai-org/patch-series/demo") == old
    assert git_wrapper.show_file(repo, old, "series-coverage-ledger.cue") is None


def test_prepared_network_tree_publishes_by_create_and_cas_without_aliases(tmp_path):
    repo = _repo(tmp_path)
    ref = "refs/heads/ai-org/patch-series/demo"
    base = _git(repo, "rev-parse", "main")
    created = git_wrapper.prepare_network_publication(repo, ref, _files(), parent=base)

    assert git_wrapper.publish_network_publication(repo, created).status == "created"
    assert git_wrapper.show_file(repo, ref, "series-coverage-ledger.json") is None
    assert git_wrapper.show_file(repo, ref, "series-coverage-ledger.cue") is not None
    root_input = network_bodies.resolve_manifest_input(repo, ref, ".")
    child_input = network_bodies.resolve_manifest_input(repo, ref, "sub/codec")
    assert root_input.state == "registered"
    assert root_input.manifest["node_path"] == "."
    assert child_input.state == "registered"
    assert child_input.manifest["contrib_branch"] == "ai-org/contrib/demo-codec"
    assert network_bodies.resolve_manifest_input(repo, ref, "sub/codec/nested").state == "invalid"
    updated_files = _files()
    updated_files["series-coverage-ledger.json"] = {
        **updated_files["series-coverage-ledger.json"], "ledger_revision":8,
    }
    updated = git_wrapper.prepare_network_publication(repo, ref, updated_files, parent=created.commit_oid)
    assert git_wrapper.publish_network_publication(repo, updated).status == "updated"
    assert network_bodies.read_network_body(repo, ref, "series-coverage-ledger.json")["ledger_revision"].as_int_exact() == 8


@pytest.mark.parametrize(
    ("missing", "node_path", "detail"),
    [
        ("series-coverage-ledger.cue", "sub/codec", "incomplete-grounded-root-cohort"),
        (
            "sub/codec/maintainer-series-request.cue",
            ".",
            "incomplete-grounded-child-cohort:sub/codec",
        ),
    ],
)
def test_registered_manifest_discovery_rejects_incomplete_grounded_cohort(
    tmp_path, missing, node_path, detail
):
    repo = _repo(tmp_path)
    ref = "refs/heads/ai-org/patch-series/demo"
    prepared = git_wrapper.prepare_network_publication(
        repo, ref, _files(), parent=_git(repo, "rev-parse", "main")
    )
    assert git_wrapper.publish_network_publication(repo, prepared).ok
    _git(repo, "checkout", "ai-org/patch-series/demo")
    _git(repo, "rm", missing)
    _git(repo, "commit", "-m", "forge incomplete registered network")

    resolved = network_bodies.resolve_manifest_input(repo, ref, node_path)

    assert resolved.state == "invalid"
    assert detail in resolved.detail


def test_registered_manifest_discovery_rejects_child_orphaned_from_root_policy(tmp_path):
    repo = _repo(tmp_path)
    ref = "refs/heads/ai-org/patch-series/demo"
    prepared = git_wrapper.prepare_network_publication(
        repo, ref, _files(), parent=_git(repo, "rev-parse", "main")
    )
    assert git_wrapper.publish_network_publication(repo, prepared).ok
    root_member = next(
        member for member in prepared.publication.members
        if member.context == "root-network-node-manifest-v1"
    )
    root = {**_files()["patch-series-manifest.json"], "children": []}
    forged_root = BodyCodecClient().prepare(
        root_member.context, root, expected=root_member.contract
    ).canonical.data.decode("utf-8")
    git_wrapper.commit_files(
        repo,
        "ai-org/patch-series/demo",
        {"patch-series-manifest.cue": forged_root},
        subject="forge orphan registered child",
    )

    resolved = network_bodies.resolve_manifest_input(repo, ref, "sub/codec")

    assert resolved.state == "invalid"
    assert "grounding-mismatch:root-ledger-children" in resolved.detail


def test_network_rebaseline_rejects_ledger_rollback_without_moving_ref(tmp_path):
    repo = _repo(tmp_path)
    ref = "refs/heads/ai-org/patch-series/demo"
    created = git_wrapper.prepare_network_publication(
        repo, ref, _files(), parent=_git(repo, "rev-parse", "main")
    )
    assert git_wrapper.publish_network_publication(repo, created).ok
    rollback = _files()
    rollback["series-coverage-ledger.json"] = {
        **rollback["series-coverage-ledger.json"], "ledger_revision": 6,
    }

    with pytest.raises(git_wrapper.GitBodyFailure) as failure:
        git_wrapper.prepare_network_publication(repo, ref, rollback, parent=created.commit_oid)

    assert failure.value.code == "BODY_VALIDATION"
    assert _git(repo, "rev-parse", ref) == created.commit_oid


@pytest.mark.parametrize(
    ("field", "value", "rule"),
    [
        ("id", "demo:other", "child-ledger-authority:codec"),
        ("contrib_branch", "ai-org/contrib/other", "child-ledger-authority:codec"),
        ("scope_item_ids", ["other"], "child-ledger-authority:codec"),
        ("allowed_subtree", "other/", "child-ledger-write-scope:codec"),
    ],
)
def test_network_prepare_rejects_child_ledger_authority_drift_without_publishing(
    tmp_path, field, value, rule
):
    repo = _repo(tmp_path)
    ref = "refs/heads/ai-org/patch-series/demo"
    files = _files()
    child = dict(files["sub/codec/patch-series-manifest.json"])
    if field == "allowed_subtree":
        ledger = dict(files["series-coverage-ledger.json"])
        ledger["children"] = [{**ledger["children"][0], field: value}]
        files["series-coverage-ledger.json"] = ledger
    else:
        child[field] = value
    files["sub/codec/patch-series-manifest.json"] = child

    with pytest.raises(git_wrapper.GitBodyFailure) as failure:
        git_wrapper.prepare_network_publication(
            repo, ref, files, parent=_git(repo, "rev-parse", "main")
        )

    assert rule in str(failure.value.__cause__)
    assert failure.value.code == "BODY_VALIDATION"
    assert git_wrapper.head_sha(repo, ref) is None


def test_network_prepare_rejects_child_cohort_outside_declared_coordinate(tmp_path):
    repo = _repo(tmp_path)
    ref = "refs/heads/ai-org/patch-series/demo"
    files = {
        path.replace("sub/codec/", "sub/other/"): value
        for path, value in _files().items()
    }

    with pytest.raises(git_wrapper.GitBodyFailure) as failure:
        git_wrapper.prepare_network_publication(
            repo, ref, files, parent=_git(repo, "rev-parse", "main")
        )

    assert "child-coordinate:sub/other:sub/codec" in str(failure.value.__cause__)
    assert failure.value.code == "BODY_VALIDATION"
    assert git_wrapper.head_sha(repo, ref) is None


def test_incomplete_root_child_metadata_pair_is_rejected_before_publication(tmp_path):
    repo = _repo(tmp_path)
    ref = "refs/heads/ai-org/patch-series/demo"
    files = _files()
    del files["patch-series-metadata.json"]

    with pytest.raises(git_wrapper.GitBodyFailure) as failure:
        git_wrapper.prepare_network_publication(
            repo, ref, files, parent=_git(repo, "rev-parse", "main")
        )

    assert failure.value.code == "BODY_VALIDATION"
    assert git_wrapper.head_sha(repo, ref) is None


def test_injected_network_publication_failure_changes_no_ref(tmp_path):
    repo = _repo(tmp_path)
    ref = "refs/heads/ai-org/patch-series/demo"
    prepared = git_wrapper.prepare_network_publication(
        repo, ref, _files(), parent=_git(repo, "rev-parse", "main")
    )

    rejected = git_wrapper.publish_network_publication(repo, prepared, inject_failure=True)

    assert rejected.status == "rejected"
    assert git_wrapper.head_sha(repo, ref) is None


def test_network_publication_rejects_prepared_member_set_tampering(tmp_path):
    repo = _repo(tmp_path)
    ref = "refs/heads/ai-org/patch-series/demo"
    prepared = git_wrapper.prepare_network_publication(
        repo, ref, _files(), parent=_git(repo, "rev-parse", "main")
    )
    files = dict(prepared.publication.files)
    files.pop("sub/codec/technical-approach-plan.cue")
    forged = replace(
        prepared, publication=replace(prepared.publication, files=files)
    )

    rejected = git_wrapper.publish_network_publication(repo, forged)

    assert rejected.status == "rejected"
    assert rejected.failure is not None
    assert rejected.failure.rule == "prepared-network-member-set"
    assert git_wrapper.head_sha(repo, ref) is None


@pytest.mark.parametrize("mutation", ["reordered", "substituted", "missing"])
def test_network_publication_rejects_tampered_temporary_carriers_without_moving_ref(
    tmp_path, mutation
):
    repo = _repo(tmp_path)
    ref = "refs/heads/ai-org/patch-series/demo"
    prepared = git_wrapper.prepare_network_publication(
        repo, ref, _files(), parent=_git(repo, "rev-parse", "main")
    )
    recipes = list(prepared.publication.carrier_recipes)
    if mutation == "reordered":
        recipes[0], recipes[1] = recipes[1], recipes[0]
    elif mutation == "substituted":
        recipes[0] = recipes[1]
    else:
        recipes.pop()
    forged = replace(
        prepared,
        publication=replace(prepared.publication, carrier_recipes=tuple(recipes)),
    )

    rejected = git_wrapper.publish_network_publication(repo, forged)

    assert rejected.status == "rejected"
    assert rejected.failure is not None
    assert rejected.failure.rule == "prepared-network-carriers"
    assert git_wrapper.head_sha(repo, ref) is None


def test_network_publication_rejects_prepared_commit_tampering(tmp_path):
    repo = _repo(tmp_path)
    ref = "refs/heads/ai-org/patch-series/demo"
    prepared = git_wrapper.prepare_network_publication(
        repo, ref, _files(), parent=_git(repo, "rev-parse", "main")
    )
    forged = replace(prepared, tree_oid=prepared.parent_oid)

    rejected = git_wrapper.publish_network_publication(repo, forged)

    assert rejected.status == "rejected"
    assert rejected.failure is not None
    assert rejected.failure.rule == "prepared-tree"
    assert git_wrapper.head_sha(repo, ref) is None


def test_network_reader_has_nested_detached_and_fresh_clone_parity(tmp_path):
    repo = _repo(tmp_path)
    ref = "refs/heads/ai-org/patch-series/demo"
    prepared = git_wrapper.prepare_network_publication(
        repo, ref, _files(), parent=_git(repo, "rev-parse", "main")
    )
    assert git_wrapper.publish_network_publication(repo, prepared).ok
    expected = network_bodies.read_network_body(repo, ref, "sub/codec/patch-series-manifest.json")

    _git(repo, "checkout", "--detach", prepared.commit_oid)
    detached = network_bodies.read_network_body(repo, prepared.commit_oid, "sub/codec/patch-series-manifest.json")
    clone = tmp_path / "fresh"
    subprocess.run(["git", "clone", str(repo), str(clone)], check=True, text=True, capture_output=True)
    fresh = network_bodies.read_network_body(clone, prepared.commit_oid, "sub/codec/patch-series-manifest.json")

    assert detached == expected == fresh
    assert network_bodies.resolve_manifest_input(
        repo / "sub" / "codec", prepared.commit_oid, "sub/codec"
    ).state == "registered"
    assert network_bodies.resolve_manifest_input(
        clone, prepared.commit_oid, "sub/codec"
    ).state == "registered"


@pytest.mark.parametrize(
    ("ref", "parent"),
    [("refs/heads/not-a-network/demo", "main"), ("refs/heads/ai-org/patch-series/demo", "missing")],
)
def test_network_parent_or_identity_failure_releases_no_ref(tmp_path, ref, parent):
    repo = _repo(tmp_path)

    with pytest.raises(git_wrapper.GitBodyFailure):
        git_wrapper.prepare_network_publication(repo, ref, _files(), parent=parent)

    assert git_wrapper.head_sha(repo, ref) is None


def _repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@example.com")
    (repo / "README").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "README")
    _git(repo, "commit", "-m", "base")
    return repo


def _git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, check=True, text=True, capture_output=True).stdout.strip()
