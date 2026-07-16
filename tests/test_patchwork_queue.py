from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import random
import subprocess
import time
from pathlib import Path

import pytest

from ai_org import git_wrapper, mailing_list, network_bodies, patchwork_queue as patch_series, review_bodies
from ai_org.patch_author import functional_check
from ai_org.patchwork_queue import patch_series_gate as network
from ai_org.patchwork_queue.field_registry import empty_user_experience_requirements


def test_stateless_producer_pair_vet_route_policy_is_exact():
    assert network.STATELESS_PRODUCER_PAIR_VET_ROUTES == {
        "already_refined",
        "right_sized",
        "no_network_discovery",
        "ordinary_refinement",
        "split_preparation",
        "stale_decision",
        "rebaseline_decision",
        "stamping",
        "elaboration_preparation",
        "scope_absent_manifest_fallback",
    }


def test_migrated_lifecycle_activation_is_closed_over_root_generation(monkeypatch):
    def snapshot(generation, disposition, present):
        return network.patch_series_bodies.RootGenerationSnapshot(
            "a" * 40,
            generation,
            disposition,
            tuple(
                (path, b"present\n" if path in present else None)
                for path in network.patch_series_bodies._ROOT_GENERATION_PATHS
            ),
        )

    cohort = set(network.patch_series_bodies.ROOT_COHORT_V2_PATHS)
    pending = network.migrated_lifecycle_activation(
        snapshot(
            network.patch_series_bodies.ROOT_GENERATION_V2,
            network.patch_series_bodies.ROOT_DISPOSITION_V2_NOT_READY,
            cohort,
        )
    )
    active = network.migrated_lifecycle_activation(
        snapshot(
            network.patch_series_bodies.ROOT_GENERATION_V2,
            network.patch_series_bodies.ROOT_DISPOSITION_V2_READY,
            cohort | {network.patch_series_bodies.SCOPE_DECOMPOSITION_PATH},
        )
    )
    legacy = network.migrated_lifecycle_activation(
        snapshot(
            network.patch_series_bodies.ROOT_GENERATION_HISTORICAL,
            network.patch_series_bodies.ROOT_DISPOSITION_LEGACY_READY,
            {
                network.patch_series_bodies.LEGACY_COVER_PATH,
                network.patch_series_bodies.LEGACY_ROOT_APPROACH_PATH,
            },
        )
    )

    assert pending == network.MigratedLifecycleActivation(
        "blocked", False, False
    )
    assert pending.activation_count == 0
    assert active == network.MigratedLifecycleActivation(
        "producer-aware-v2",
        True,
        True,
        (
            "discovery",
            "production",
            "handoff",
            "acceptance",
            "resolution",
            "integration",
        ),
    )
    assert active.activation_count == 1
    assert legacy == network.MigratedLifecycleActivation("legacy", False, True)
    assert legacy.activation_count == 0

    monkeypatch.setattr(
        network,
        "MIGRATED_LIFECYCLE_READINESS_MATRIX",
        {**network.MIGRATED_LIFECYCLE_READINESS_MATRIX, "atomicity": False},
    )
    rejected = network.migrated_lifecycle_activation(
        snapshot(
            network.patch_series_bodies.ROOT_GENERATION_V2,
            network.patch_series_bodies.ROOT_DISPOSITION_V2_READY,
            cohort | {network.patch_series_bodies.SCOPE_DECOMPOSITION_PATH},
        )
    )
    assert rejected == network.MigratedLifecycleActivation(
        "blocked", False, False
    )
    assert rejected.activation_count == 0


def test_stateless_authorability_boundary_delegates_only_exact_routes(
    monkeypatch,
):
    calls = []
    frozen = "a" * 40
    approach = {"problem": {"goals": []}}

    def fake_common_vet(
        repo,
        branch,
        route,
        *,
        technical_approach=None,
        frozen_root_oid=None,
    ):
        decision = network.AuthorabilityDecision(
            True,
            True,
            network.FrozenNetworkSourceVector(
                route=route,
                branch=branch,
                frozen_root_oid=frozen_root_oid or frozen,
                root_generation=network.patch_series_bodies.ROOT_GENERATION_HISTORICAL,
            ),
        )
        calls.append(
            (
                repo,
                branch,
                route,
                technical_approach,
                frozen_root_oid,
                decision,
            )
        )
        return decision

    monkeypatch.setattr(network, "vet_producer_pair_closure", fake_common_vet)

    for route in sorted(network.STATELESS_PRODUCER_PAIR_VET_ROUTES):
        decision = network.vet_stateless_authorability(
            "/repo",
            "ai-org/patch-series/root",
            route,
            technical_approach=approach,
            frozen_root_oid=frozen,
        )
        assert decision is calls[-1][5]

    assert [call[2] for call in calls] == sorted(
        network.STATELESS_PRODUCER_PAIR_VET_ROUTES
    )
    assert all(call[3:5] == (approach, frozen) for call in calls)

    with pytest.raises(ValueError, match="unknown stateless authorability route"):
        network.vet_stateless_authorability(
            "/repo",
            "ai-org/patch-series/root",
            "producer_code_authoring",
        )
    assert len(calls) == len(network.STATELESS_PRODUCER_PAIR_VET_ROUTES)


def test_stateless_authorability_boundary_closes_mismatched_vet_source(
    monkeypatch,
):
    branch = "ai-org/patch-series/root"
    frozen = "a" * 40
    returned = network.FrozenNetworkSourceVector(
        route="stamping",
        branch="ai-org/patch-series/other",
        frozen_root_oid="b" * 40,
        root_generation=network.patch_series_bodies.ROOT_GENERATION_V2,
        root_sha256="c" * 64,
        scope_decomposition_sha256="d" * 64,
        context=network.patch_series_bodies.SCOPE_DECOMPOSITION_CONTEXT,
    )
    monkeypatch.setattr(
        network,
        "vet_producer_pair_closure",
        lambda *_args, **_kwargs: network.AuthorabilityDecision(
            True, True, returned
        ),
    )

    decision = network.vet_stateless_authorability(
        "/repo",
        branch,
        "ordinary_refinement",
        frozen_root_oid=frozen,
    )

    assert decision.blocked is True
    assert decision.vet_passed is False
    assert decision.authorable is False
    assert decision.source.route == "ordinary_refinement"
    assert decision.source.branch == branch
    assert decision.source.frozen_root_oid == frozen
    assert decision.source.root_sha256 == ""
    assert decision.source.scope_decomposition_sha256 == ""
    assert decision.diagnostic is not None
    assert decision.diagnostic.as_dict() == {
        "route": "ordinary_refinement",
        "frozen_root_oid": frozen,
        "cue_location": "authorability_decision.source",
        "rule": "stateless-source-vector-binding",
        "goal_id": "root.problem.goals",
        "obligation_id": "",
        "field": "route,branch,frozen_root_oid",
        "detail": (
            "the common producer-pair vet returned a decision that is not "
            "bound to the requested stateless route and frozen root"
        ),
        "context": network.patch_series_bodies.SCOPE_DECOMPOSITION_CONTEXT,
    }


def test_stateless_pending_decisions_read_the_vetted_frozen_root(
    tmp_path, monkeypatch
):
    branch = "ai-org/patch-series/root"
    frozen = "a" * 40
    source = network.FrozenNetworkSourceVector(
        route="no_network_discovery",
        branch=branch,
        frozen_root_oid=frozen,
        root_generation=network.patch_series_bodies.ROOT_GENERATION_HISTORICAL,
    )
    reads = []

    def fake_vet(_repo, candidate_branch, route, *, frozen_root_oid=None, **_kwargs):
        assert candidate_branch == branch
        assert frozen_root_oid in {None, frozen}
        return network.AuthorabilityDecision(
            True,
            True,
            replace(source, route=route),
        )

    class Snapshot:
        lifecycle_ready = True
        lifecycle_blocked = False

        @staticmethod
        def technical_approach():
            return {"patch_plan": [{"id": "patch_plan:frozen"}]}

        @staticmethod
        def cover_letter():
            return {}

    monkeypatch.setattr(network, "vet_producer_pair_closure", fake_vet)
    monkeypatch.setattr(network.git_wrapper, "branch_exists", lambda *_args: True)
    monkeypatch.setattr(
        network.patch_series_bodies,
        "classify_root_generation",
        lambda _repo, ref: reads.append(("classify", ref)) or Snapshot(),
    )
    monkeypatch.setattr(
        network.git_wrapper,
        "has_subject",
        lambda _repo, ref, subject: (
            reads.append(("subject", ref, subject))
            or subject == "patch_series: direction-ok"
        ),
    )
    monkeypatch.setattr(
        network,
        "_read_metadata",
        lambda _repo, ref: reads.append(("metadata", ref)) or {},
    )
    monkeypatch.setattr(
        network,
        "_read_json",
        lambda _repo, ref, path, **_kwargs: (
            reads.append(("json", ref, path)) or None
        ),
    )
    monkeypatch.setattr(network, "right_sized", lambda _approach: False)

    assert network.split_pending(tmp_path, branch) is True
    assert reads
    assert {read[1] for read in reads} == {frozen}

    reads.clear()
    monkeypatch.setattr(
        network,
        "_current_patch_series_state",
        lambda _repo, ref: reads.append(("state", ref)) or "direction-ok",
    )
    monkeypatch.setattr(network.git_wrapper, "default_branch", lambda _repo: "main")
    monkeypatch.setattr(
        network.git_wrapper,
        "is_ancestor",
        lambda _repo, ref, default: (
            reads.append(("ancestor", ref, default)) or False
        ),
    )

    assert (
        network._parent_escalation_refusal(
            tmp_path,
            branch,
            frozen_ref=frozen,
        )
        == ""
    )
    assert {read[1] for read in reads} == {frozen}


def test_already_refined_shortcut_vets_route_before_reading_snapshot(
    tmp_path, monkeypatch
):
    branch = "ai-org/patch-series/root"
    frozen = "c" * 40
    events = []
    source = network.FrozenNetworkSourceVector(
        route="already_refined",
        branch=branch,
        frozen_root_oid=frozen,
        root_generation=network.patch_series_bodies.ROOT_GENERATION_HISTORICAL,
    )

    monkeypatch.setattr(
        network.git_wrapper,
        "head_sha",
        lambda _repo, candidate_branch: (
            events.append(("freeze", candidate_branch)) or frozen
        ),
    )
    monkeypatch.setattr(
        network,
        "_network_file_exists",
        lambda _repo, ref, path: ref == frozen and path == network.STATUS_PATH,
    )

    def fake_vet(_repo, candidate_branch, route, *, frozen_root_oid=None, **_kwargs):
        events.append(("vet", route, frozen_root_oid))
        assert candidate_branch == branch
        return network.AuthorabilityDecision(True, True, source)

    def fake_read(_repo, ref, path, **_kwargs):
        events.append(("read", ref, path))
        assert events[-2] == ("vet", "already_refined", frozen)
        return {"generated_from": network.NODE_MANIFEST_PATH}

    monkeypatch.setattr(network, "vet_producer_pair_closure", fake_vet)
    monkeypatch.setattr(network, "_read_json", fake_read)

    result = network._refine(
        tmp_path,
        branch,
        horizon=1,
        ctx=network.org_log.RunContext(repo=tmp_path, stage="test"),
    )

    assert result["status"] == "already-refined"
    assert result["lineage_status"] == {
        "generated_from": network.NODE_MANIFEST_PATH
    }
    assert events == [
        ("freeze", branch),
        ("vet", "already_refined", frozen),
        ("read", frozen, network.STATUS_PATH),
    ]


def test_already_refined_shortcuts_reuse_one_vet_for_status_and_ledger(
    tmp_path, monkeypatch
):
    branch = "ai-org/patch-series/root"
    frozen = "f" * 40
    events = []
    source = network.FrozenNetworkSourceVector(
        route="already_refined",
        branch=branch,
        frozen_root_oid=frozen,
        root_generation=network.patch_series_bodies.ROOT_GENERATION_HISTORICAL,
    )

    monkeypatch.setattr(network.git_wrapper, "head_sha", lambda *_args: frozen)
    monkeypatch.setattr(
        network,
        "_network_file_exists",
        lambda _repo, ref, path: (
            events.append(("exists", ref, path))
            or path in {network.STATUS_PATH, network.LEDGER_PATH}
        ),
    )

    def fake_vet(_repo, candidate_branch, route, *, frozen_root_oid=None):
        events.append(("vet", route, frozen_root_oid))
        assert candidate_branch == branch
        return network.AuthorabilityDecision(True, True, source)

    def fake_read(_repo, ref, path, **_kwargs):
        events.append(("read", ref, path))
        if path == network.STATUS_PATH:
            return {"generated_from": "legacy-status"}
        assert path == network.LEDGER_PATH
        return {"relation": "split-into", "children": []}

    monkeypatch.setattr(network, "vet_producer_pair_closure", fake_vet)
    monkeypatch.setattr(network, "_read_json", fake_read)

    result = network._refine(
        tmp_path,
        branch,
        horizon=1,
        ctx=network.org_log.RunContext(repo=tmp_path, stage="test"),
    )

    assert result["status"] == "already-refined"
    assert [event for event in events if event[0] == "vet"] == [
        ("vet", "already_refined", frozen)
    ]
    assert [event[1] for event in events if event[0] == "read"] == [
        frozen,
        frozen,
    ]


def test_right_sized_shortcut_vets_before_predicate_and_reuses_decision(
    tmp_path, monkeypatch
):
    branch = "ai-org/patch-series/root"
    frozen = "d" * 40
    approach = {"problem": {"goals": []}}
    events = []

    class Snapshot:
        frozen_oid = frozen

        @staticmethod
        def cover_letter():
            return {"raw_request": "bounded request"}

        @staticmethod
        def technical_approach():
            return approach

    def fake_vet(
        _repo,
        candidate_branch,
        route,
        *,
        technical_approach=None,
        frozen_root_oid=None,
    ):
        events.append(("vet", route, frozen_root_oid))
        assert candidate_branch == branch
        if route == "right_sized":
            assert technical_approach is approach
        return network.AuthorabilityDecision(
            True,
            True,
            network.FrozenNetworkSourceVector(
                route=route,
                branch=branch,
                frozen_root_oid=frozen_root_oid or frozen,
                root_generation=network.patch_series_bodies.ROOT_GENERATION_HISTORICAL,
            ),
        )

    monkeypatch.setattr(network.git_wrapper, "head_sha", lambda *_args: frozen)
    monkeypatch.setattr(network, "_network_file_exists", lambda *_args: False)
    monkeypatch.setattr(network, "vet_producer_pair_closure", fake_vet)
    monkeypatch.setattr(network, "_vetted_root_snapshot", lambda *_args: Snapshot())
    monkeypatch.setattr(network, "_declared_patchwork_checks_source", lambda *_args: {})
    monkeypatch.setattr(network, "_scope_items", lambda *_args: [])
    monkeypatch.setattr(
        network,
        "right_sized",
        lambda candidate: events.append(("predicate", candidate)) or True,
    )
    monkeypatch.setattr(network, "_right_sized_node_files", lambda *_args: {})
    monkeypatch.setattr(
        network,
        "_commit_network_files",
        lambda *_args, **kwargs: (
            events.append(("commit", kwargs["source"].route))
            or {"commit": "e" * 40}
        ),
    )

    result = network._refine(
        tmp_path,
        branch,
        horizon=1,
        ctx=network.org_log.RunContext(repo=tmp_path, stage="test"),
    )

    assert result["status"] == "right-sized"
    assert [event[:2] for event in events] == [
        ("vet", "ordinary_refinement"),
        ("vet", "right_sized"),
        (
            "predicate",
            {
                "patch_series": {"raw_request": "bounded request"},
                "technical_approach": approach,
            },
        ),
        ("commit", "right_sized"),
    ]
    assert [event[1] for event in events if event[0] == "vet"].count(
        "right_sized"
    ) == 1


def test_stale_and_elaboration_decisions_keep_post_vet_reads_frozen(
    tmp_path, monkeypatch
):
    branch = "ai-org/patch-series/root"
    address = f"{branch}:sub/preview"
    frozen = "b" * 40
    reads = []

    def fake_vet(_repo, candidate_branch, route, *, frozen_root_oid=None, **_kwargs):
        assert candidate_branch == branch
        assert frozen_root_oid in {None, frozen}
        source = network.FrozenNetworkSourceVector(
            route=route,
            branch=branch,
            frozen_root_oid=frozen,
            root_generation=network.patch_series_bodies.ROOT_GENERATION_HISTORICAL,
        )
        return network.AuthorabilityDecision(True, True, source)

    class Snapshot:
        lifecycle_blocked = False

    monkeypatch.setattr(network, "vet_producer_pair_closure", fake_vet)
    monkeypatch.setattr(network.git_wrapper, "branch_exists", lambda *_args: True)
    monkeypatch.setattr(
        network.patch_series_bodies,
        "classify_root_generation",
        lambda _repo, ref: reads.append(("classify", ref)) or Snapshot(),
    )
    monkeypatch.setattr(
        network,
        "_read_node_manifest",
        lambda _repo, ref, node, **_kwargs: (
            reads.append(("manifest", ref, node))
            or {
                "child_key": "preview",
                "node_path": "sub/preview",
                "lifecycle_status": "stale",
                "parent_branch": branch,
            }
        ),
    )
    monkeypatch.setattr(
        network,
        "_network_file_exists",
        lambda _repo, ref, path: reads.append(("exists", ref, path)) or True,
    )

    assert network.stale_revalidation_pending(tmp_path, address) is True
    assert {read[1] for read in reads} == {frozen}

    reads.clear()
    monkeypatch.setattr(
        network,
        "_read_node_manifest",
        lambda _repo, ref, node, **_kwargs: (
            reads.append(("manifest", ref, node))
            or {
                "child_key": "preview",
                "node_path": "sub/preview",
                "lifecycle_status": "posted_to_mailing_list",
            }
        ),
    )
    monkeypatch.setattr(
        network,
        "_load_manifest_network_from_git",
        lambda _repo, branch, *, frozen_root_oid=None: (
            reads.append(("network", frozen_root_oid, branch))
            or {"errors": ["stop"]}
        ),
    )

    assert network.coarse_ready(tmp_path, address) is False
    assert reads
    assert {read[1] for read in reads} == {frozen}


def test_authorability_failure_coordinates_survive_route_and_pull_events(
    tmp_path, monkeypatch
):
    frozen = "a" * 40
    source = network.FrozenNetworkSourceVector(
        route="no_network_discovery",
        branch="ai-org/patch-series/root",
        frozen_root_oid=frozen,
        root_generation=network.patch_series_bodies.ROOT_GENERATION_V2,
        root_sha256="b" * 64,
        scope_decomposition_sha256="c" * 64,
        source_path=network.patch_series_bodies.ROOT_APPROACH_PATH,
        context=network.patch_series_bodies.ROOT_APPROACH_CONTEXT,
        canonical_digest="d" * 64,
    )
    diagnostic = network.PublicationDiagnostic(
        "no_network_discovery",
        frozen,
        "problem.production_obligations.0.deliverable_requirement_id",
        "producer-pair-cross-link",
        goal_id="goal:preview",
        obligation_id="obligation:preview",
        field="deliverable_requirement_id",
        detail="the obligation does not link to its requirement",
        context=network.patch_series_bodies.ROOT_APPROACH_CONTEXT,
    )
    decision = network.AuthorabilityDecision(False, False, source, diagnostic)
    result = network._authorability_failure(decision, source.branch)

    lineage_payload = network._lineage_result_payload(result)

    assert lineage_payload["route"] == "no_network_discovery"
    assert lineage_payload["frozen_oid"] == frozen
    assert lineage_payload["diagnostic"] == diagnostic.as_dict()
    assert lineage_payload["authorability_decision"] == decision.as_dict()

    events = []
    monkeypatch.setattr(
        patch_series.org_log,
        "emit",
        lambda event, payload, **_kwargs: events.append((event, payload)),
    )

    returned = patch_series._emit_pull_outcome(
        result,
        patch_series.org_log.RunContext(repo=tmp_path, stage="patch_series.pull"),
    )

    assert returned is result
    event, payload = events[-1]
    assert event == "patch_series.pull.outcome"
    assert payload["route"] == "no_network_discovery"
    assert payload["frozen_oid"] == frozen
    assert payload["diagnostic"] == diagnostic.as_dict()
    assert payload["authorability_decision"] == decision.as_dict()


def test_scope_absent_manifest_fallback_vets_stamping_source_before_defaulting(
    tmp_path, monkeypatch
):
    frozen = "1" * 40
    source = network.FrozenNetworkSourceVector(
        route="stamping",
        branch="ai-org/patch-series/root",
        frozen_root_oid=frozen,
        root_generation=network.patch_series_bodies.ROOT_GENERATION_HISTORICAL,
    )
    events = []

    def fake_vet(_repo, branch, route, *, frozen_root_oid=None, **_kwargs):
        events.append(("vet", route, frozen_root_oid))
        fallback_source = network.FrozenNetworkSourceVector(
            route=route,
            branch=branch,
            frozen_root_oid=frozen_root_oid or "",
            root_generation=source.root_generation,
        )
        return network.AuthorabilityDecision(True, True, fallback_source)

    monkeypatch.setattr(network, "vet_producer_pair_closure", fake_vet)
    monkeypatch.setattr(
        network,
        "_read_node_manifest",
        lambda _repo, ref, _node, **_kwargs: events.append(("manifest", ref)) or {},
    )
    monkeypatch.setattr(
        network,
        "_read_json",
        lambda _repo, ref, _path, **_kwargs: events.append(("ledger", ref)) or None,
    )
    monkeypatch.setattr(
        network,
        "_projected_root_children_from_git",
        lambda _repo, ref: events.append(("children", ref)) or {},
    )

    files = network._stamped_root_projection_files(
        tmp_path,
        source.branch,
        [],
        source=source,
    )

    assert events[:3] == [
        ("manifest", frozen),
        ("ledger", frozen),
        ("vet", "scope_absent_manifest_fallback", frozen),
    ]
    assert ("children", frozen) in events
    assert files[network.LEDGER_PATH]["relation"] == "stamped-children"


def test_scope_absent_manifest_fallback_returns_typed_failure_without_publication(
    tmp_path, monkeypatch
):
    repo = _repo(tmp_path)
    _write_parent(repo, "root")
    branch = "ai-org/patch-series/root"
    frozen = git_wrapper.head_sha(repo, branch)
    assert frozen is not None
    events = []

    def fake_vet(_repo, candidate_branch, route, *, frozen_root_oid=None, **_kwargs):
        source = network.FrozenNetworkSourceVector(
            route=route,
            branch=candidate_branch,
            frozen_root_oid=frozen_root_oid or frozen,
            root_generation=network.patch_series_bodies.ROOT_GENERATION_CURRENT,
            root_sha256="a" * 64,
            source_path=network.patch_series_bodies.LEGACY_ROOT_APPROACH_PATH,
            context=network.patch_series_bodies.ROOT_APPROACH_CONTEXT,
            canonical_digest="b" * 64,
        )
        if route == "stamping":
            return network.AuthorabilityDecision(True, True, source)
        assert route == "scope_absent_manifest_fallback"
        diagnostic = network.PublicationDiagnostic(
            route,
            source.frozen_root_oid,
            "series-scope-decomposition.scope_items[0]",
            "exact-scope-owner-required",
            goal_id="goal:preview",
            obligation_id="obligation:preview",
            field="scope_item_id",
            detail="the frozen scope row has no exact owner",
            context=network.patch_series_bodies.SCOPE_DECOMPOSITION_CONTEXT,
        )
        return network.AuthorabilityDecision(False, False, source, diagnostic)

    monkeypatch.setattr(network, "vet_producer_pair_closure", fake_vet)
    monkeypatch.setattr(
        network.org_log,
        "emit",
        lambda event, payload, **_kwargs: events.append((event, payload)),
    )
    before = (
        _git(repo, "show-ref"),
        _git(repo, "ls-tree", "-r", frozen),
        _git(repo, "status", "--porcelain"),
    )

    result = network.stamp_children(repo, branch, {}, [])

    assert result["ok"] is False
    assert result["status"] == "producer-pair-vet-failed"
    assert result["route"] == "scope_absent_manifest_fallback"
    assert result["frozen_oid"] == frozen
    assert result["diagnostic"] == {
        "route": "scope_absent_manifest_fallback",
        "frozen_root_oid": frozen,
        "cue_location": "series-scope-decomposition.scope_items[0]",
        "rule": "exact-scope-owner-required",
        "goal_id": "goal:preview",
        "obligation_id": "obligation:preview",
        "field": "scope_item_id",
        "detail": "the frozen scope row has no exact owner",
        "context": network.patch_series_bodies.SCOPE_DECOMPOSITION_CONTEXT,
    }
    assert result["authorability_decision"]["source"]["route"] == (
        "scope_absent_manifest_fallback"
    )
    assert events[-1][0] == "patch_series.network.stamp_children.result"
    assert events[-1][1]["diagnostic"] == result["diagnostic"]
    assert (
        _git(repo, "show-ref"),
        _git(repo, "ls-tree", "-r", frozen),
        _git(repo, "status", "--porcelain"),
    ) == before


def test_no_network_discovery_consumes_the_vetted_frozen_root_when_ref_moves(
    tmp_path, monkeypatch
):
    repo = _repo(tmp_path)
    _write_parent(repo, "root")
    branch = "ai-org/patch-series/root"
    frozen = git_wrapper.head_sha(repo, branch)
    assert frozen is not None
    real_vet = network.vet_producer_pair_closure

    def move_after_vet(candidate_repo, candidate_branch, route, **kwargs):
        decision = real_vet(
            candidate_repo, candidate_branch, route, **kwargs
        )
        assert route == "no_network_discovery"
        git_wrapper.commit_empty(
            repo, branch, "network: right-sized after discovery vet"
        )
        return decision

    monkeypatch.setattr(network, "vet_producer_pair_closure", move_after_vet)

    assert network.split_pending(repo, branch) is True
    assert git_wrapper.head_sha(repo, branch) != frozen


def test_elaboration_readiness_consumes_only_the_vetted_source_vector(
    tmp_path, monkeypatch
):
    branch = "ai-org/patch-series/root"
    address = f"{branch}:sub/child"
    frozen = "1" * 40
    source = network.FrozenNetworkSourceVector(
        route="elaboration_preparation",
        branch=branch,
        frozen_root_oid=frozen,
        root_generation=network.patch_series_bodies.ROOT_GENERATION_CURRENT,
    )
    manifest = {
        "child_key": "child",
        "node_path": "sub/child",
        "lifecycle_status": "posted_to_mailing_list",
        "edges": [],
    }
    inspected_refs = []

    monkeypatch.setattr(
        network,
        "vet_producer_pair_closure",
        lambda _repo, candidate_branch, route: (
            network.AuthorabilityDecision(True, True, source)
            if candidate_branch == branch
            and route == "elaboration_preparation"
            else pytest.fail("unexpected authorability route")
        ),
    )

    class ReadyRoot:
        lifecycle_blocked = False

    def classify(_repo, ref):
        inspected_refs.append(("root", ref))
        return ReadyRoot()

    def read_manifest(_repo, ref, node_path):
        inspected_refs.append(("manifest", ref))
        assert node_path == "sub/child"
        return dict(manifest)

    def load_network(_repo, candidate_branch, *, frozen_root_oid=None):
        inspected_refs.append(("network", frozen_root_oid))
        assert candidate_branch == branch
        return {
            "nodes": {"child": dict(manifest)},
            "errors": [],
            "declared_patchwork_checks": {},
            "declared_patchwork_checks_present": False,
        }

    def project_events(_repo, ref, **_kwargs):
        inspected_refs.append(("events", ref))
        return {"state": {}, "errors": []}

    monkeypatch.setattr(
        network.patch_series_bodies, "classify_root_generation", classify
    )
    monkeypatch.setattr(network, "_read_node_manifest", read_manifest)
    monkeypatch.setattr(network, "_load_manifest_network_from_git", load_network)
    monkeypatch.setattr(
        network, "_project_tree_patchwork_check_events_from_git", project_events
    )

    assert network.coarse_ready(tmp_path, address) is True
    assert inspected_refs == [
        ("root", frozen),
        ("manifest", frozen),
        ("network", frozen),
        ("events", frozen),
    ]


def test_refine_consumes_patch_plan_and_creates_rolling_wave_lineage(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _write_parent(repo, "root")
    parent_contract = _patch_series()
    parent_ux = empty_user_experience_requirements()
    parent_ux["experience_identity"]["named_reference"] = "Battle HUD"
    parent_ux["core_status_surfaces"]["player_status"] = "HP and MP are always visible."
    parent_contract["user_experience_requirements"] = parent_ux
    git_wrapper.commit_files(
        repo,
        "ai-org/patch-series/root",
        {"patch-series-cover-letter.json": parent_contract},
        subject="test: bind parent product contract",
    )
    _install_codex_fake(monkeypatch, [_split_all_scope()])

    result = network.refine(repo, "root", horizon=1)

    assert result["ok"] is True
    assert result["status"] == "refined"
    assert [child["node_path"] for child in result["children"]] == ["sub/prep", "sub/battle", "sub/campaign"]
    assert [child["id"] for child in result["children"]] == ["0001:sub/prep", "0001:sub/battle", "0001:sub/campaign"]
    assert [child["lifecycle_status"] for child in result["children"]] == ["ready_for_patch_authoring", "posted_to_mailing_list", "posted_to_mailing_list"]

    parent_head = git_wrapper.head_sha(repo, "ai-org/patch-series/root")
    ledger = network_bodies.read_network_body(
        repo, "ai-org/patch-series/root", "series-coverage-ledger.json"
    )
    assert ledger["relation"] == "split-into"
    assert ledger["split_operator"] == "AND"
    assert set(_scope_ids(ledger)) == set(_expected_scope_ids())
    assert all(item["owner"] != "" for item in ledger["coverage"])
    assert result["ledger_commit"] == parent_head

    assert git_wrapper.branches(repo, "ai-org/patch-series/0001-*") == []

    battle_meta = _node_json(repo, "sub/battle", "patch-series-manifest.json")
    campaign_meta = _node_json(repo, "sub/campaign", "patch-series-manifest.json")
    assert _depends_targets(battle_meta) == ["prep"]
    assert _depends_targets(campaign_meta) == ["battle"]

    request = _node_json(repo, "sub/prep", "maintainer-series-request.json")
    assert set(request) >= {"id", "submitted_at", "request"}
    assert request["provenance"]["requester"] == "parent"
    assert request["request"]["tech_stack"] == parent_contract["tech_stack"]
    assert request["request"]["user_experience_requirements"] == parent_ux
    prep_cover = _node_json(repo, "sub/prep", "patch-series-cover-letter.json")
    battle_request = _node_json(repo, "sub/battle", "maintainer-series-request.json")
    assert prep_cover["tech_stack"] == parent_contract["tech_stack"]
    assert prep_cover["user_experience_requirements"] == parent_ux
    assert battle_request["request"]["tech_stack"] == parent_contract["tech_stack"]
    assert battle_request["request"]["user_experience_requirements"] == parent_ux
    status = network_bodies.read_network_body(
        repo, "ai-org/patch-series/root", "patch-queue-status-rollup.json"
    )
    assert status["generated"] is True
    assert status["generated_from"] == "patch-series-manifest.json"
    tree_paths = set(git_wrapper.tree_files(repo, "ai-org/patch-series/root"))
    assert "series-coverage-ledger.cue" in tree_paths
    assert "series-coverage-ledger.json" not in tree_paths
    for child in ("prep", "battle", "campaign"):
        assert {
            f"sub/{child}/patch-series-manifest.cue",
            f"sub/{child}/maintainer-series-request.cue",
            f"sub/{child}/patch-series-metadata.cue",
            f"sub/{child}/patch-series-cover-letter.cue",
            f"sub/{child}/technical-approach-plan.cue",
        } <= tree_paths

    prompt = _install_codex_fake.last_prompt
    assert "weakly connected components" in prompt
    assert "depends_on" in prompt
    assert "contention" not in prompt


def test_domain_specification_scope_items_and_split_summary_are_aspect_level():
    approach = _approach()

    scope_items = network._scope_items(_patch_series(), approach)
    split_view = network._approach_split_view(approach)

    assert [item for item in scope_items if item["id"] == "domain_specification:battle-numbers"]
    domain_summary = split_view["domain_specification"][0]
    assert domain_summary["id"] == "domain_specification:battle-numbers"
    assert domain_summary["tables"] == [{"table_name": "stats", "columns": ["name", "hp"], "row_count": 1}]
    assert domain_summary["externalized_tables"][0]["row_count"] == 20
    assert "Slime" not in json.dumps(domain_summary["externalized_tables"])


def test_producer_pair_vet_covers_every_stateless_route_from_one_frozen_root(tmp_path):
    repo = _repo(tmp_path)
    _write_parent(repo, "root")
    branch = "ai-org/patch-series/root"
    git_wrapper.commit_files(
        repo,
        branch,
        {"technical-approach-plan.json": _producer_aware_network_root()},
        subject="test: frozen producer-aware root",
    )
    frozen = git_wrapper.head_sha(repo, branch)
    before = (_git(repo, "show-ref"), _git(repo, "status", "--porcelain"))

    for route in sorted(network.PRODUCER_PAIR_VET_ROUTES):
        decision = network.vet_producer_pair_closure(
            repo,
            branch,
            route,
            frozen_root_oid=frozen,
        )

        assert decision.vet_passed is True
        assert decision.authorable is False
        assert decision.diagnostic is None
        assert decision.source.route == route
        assert decision.source.frozen_root_oid == frozen
        assert decision.source.root_sha256 == hashlib.sha256(
            git_wrapper.read_tree_file(
                repo, frozen, "technical-approach-plan.json"
            ).encode("utf-8")
        ).hexdigest()

    mismatched = network.vet_producer_pair_closure(
        repo,
        branch,
        "split_preparation",
        technical_approach=_approach(),
        frozen_root_oid=frozen,
    )
    assert mismatched.vet_passed is False
    assert mismatched.diagnostic.rule == "frozen-root-candidate-mismatch"

    assert (_git(repo, "show-ref"), _git(repo, "status", "--porcelain")) == before


def test_scope_formation_routes_reject_candidate_retargeted_from_frozen_v2_root(
    tmp_path, monkeypatch
):
    repo = _repo(tmp_path)
    branch = "ai-org/patch-series/root"
    frozen = git_wrapper.head_sha(repo, "main")
    assert frozen is not None
    canonical_root = b"canonical producer root\n"
    members = tuple(
        (
            path,
            canonical_root
            if path == network.patch_series_bodies.ROOT_APPROACH_PATH
            else None,
        )
        for path in network.patch_series_bodies._ROOT_GENERATION_PATHS
    )
    snapshot = network.patch_series_bodies.RootGenerationSnapshot(
        frozen,
        network.patch_series_bodies.ROOT_GENERATION_V2,
        network.patch_series_bodies.ROOT_DISPOSITION_V2_NOT_READY,
        members,
    )
    frozen_candidate = _producer_aware_network_root()

    class RootCodec:
        def parse(self, context, raw, *, expected):
            assert context == network.patch_series_bodies.ROOT_APPROACH_CONTEXT
            assert raw == canonical_root
            assert expected == network.patch_series_bodies.ROOT_APPROACH_CONTRACT
            return frozen_candidate

    monkeypatch.setattr(
        network.patch_series_bodies,
        "classify_root_generation",
        lambda _repo, ref: snapshot if ref == frozen else pytest.fail(ref),
    )
    monkeypatch.setattr(network, "BodyCodecClient", RootCodec)
    retargeted = _producer_aware_network_root()
    retargeted["problem"]["production_obligations"][0]["deliverable"] = (
        "invented-by-route-caller"
    )
    before = (_git(repo, "show-ref"), _git(repo, "status", "--porcelain"))

    for route in sorted(network.SCOPE_FORMATION_ROUTES):
        admitted = network.vet_producer_pair_closure(
            repo,
            branch,
            route,
            technical_approach=frozen_candidate,
            frozen_root_oid=frozen,
        )
        decision = network.vet_producer_pair_closure(
            repo,
            branch,
            route,
            technical_approach=retargeted,
            frozen_root_oid=frozen,
        )

        assert admitted.vet_passed is True
        assert admitted.authorable is False
        assert admitted.transition_allowed is True
        assert admitted.source.frozen_root_oid == frozen
        assert decision.blocked is True
        assert decision.transition_allowed is False
        assert decision.source.frozen_root_oid == frozen
        assert decision.diagnostic is not None
        assert decision.diagnostic.route == route
        assert decision.diagnostic.rule == "frozen-root-candidate-mismatch"
        assert decision.diagnostic.cue_location == "technical-approach-plan"
        assert decision.diagnostic.goal_id == "root.problem.goals"

    assert (_git(repo, "show-ref"), _git(repo, "status", "--porcelain")) == before


def test_migrated_transition_cas_is_bound_to_the_vetted_frozen_root(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _write_parent(repo, "root")
    _install_codex_fake(monkeypatch, [_split_all_scope()])
    assert network.refine(repo, "root")["ok"] is True
    branch = "ai-org/patch-series/root"
    decision = network.vet_producer_pair_closure(repo, branch, "stamping")
    metadata = network_bodies.read_network_body(
        repo, branch, "patch-series-metadata.json"
    )
    assert decision.authorable is True
    assert isinstance(metadata, dict)

    mismatched = network.prepare_network_transition(
        repo,
        branch,
        {},
        route="stamping",
        source=replace(decision.source, canonical_digest="0" * 64),
    )

    assert mismatched.prepared is False
    assert mismatched.diagnostic is not None
    assert mismatched.diagnostic.rule == "source-canonical-identity-mismatch"
    assert git_wrapper.head_sha(repo, branch) == decision.source.frozen_root_oid

    advanced = git_wrapper.commit_files(
        repo,
        branch,
        {"concurrent-change.txt": "the ref moved after preflight\n"},
        subject="test: concurrent root movement",
    )["commit"]

    with pytest.raises(RuntimeError, match="network publication failed"):
        network._commit_network_files(
            repo,
            branch,
            {network.METADATA_PATH: metadata},
            subject="network: stale revalidation fixture",
            source=decision.source,
        )

    assert git_wrapper.head_sha(repo, branch) == advanced


def test_supplied_source_cannot_bypass_route_vet_before_transition_preparation(
    tmp_path,
):
    repo = _repo(tmp_path)
    _write_parent(repo, "root")
    branch = "ai-org/patch-series/root"
    candidate = _producer_aware_network_root()
    candidate["problem"]["patch_plan"][0]["production_obligation_ids"] = []
    git_wrapper.commit_files(
        repo,
        branch,
        {"technical-approach-plan.json": candidate},
        subject="test: invalid producer pair cannot become a source capability",
    )
    initial = network.vet_producer_pair_closure(
        repo, branch, "ordinary_refinement"
    )
    assert initial.blocked is True

    for route in sorted(network.ORDINARY_NETWORK_TRANSITION_ROUTES):
        transition = network.prepare_network_transition(
            repo,
            branch,
            {},
            route=route,
            source=initial.source,
        )

        assert transition.prepared is False
        assert transition.releasable is False
        assert (
            transition.source.frozen_root_oid
            == initial.source.frozen_root_oid
        )
        assert transition.diagnostic is not None
        assert (
            transition.diagnostic.route
            == network._TRANSITION_VET_ROUTE[route]
        )
        assert (
            transition.diagnostic.frozen_root_oid
            == initial.source.frozen_root_oid
        )
        assert transition.diagnostic.cue_location.startswith("root.problem")
        assert (
            transition.diagnostic.rule
            == "production-obligation-exact-partition"
        )
        assert transition.diagnostic.goal_id or transition.diagnostic.obligation_id
        assert transition.diagnostic.obligation_id == "obligation:preview"


def test_complete_v2_requires_digest_bound_scope_decomposition_before_authorability(
    tmp_path, monkeypatch
):
    repo = _repo(tmp_path)
    _write_parent(repo, "root")
    branch = "ai-org/patch-series/root"
    frozen = git_wrapper.head_sha(repo, branch)
    canonical_root = b"canonical producer root\n"
    members = tuple(
        (
            path,
            {
                network.patch_series_bodies.COVER_PATH: b"cover\n",
                network.patch_series_bodies.PROVENANCE_PATH: b"provenance\n",
                network.patch_series_bodies.ROOT_APPROACH_PATH: canonical_root,
                network.patch_series_bodies.SCOPE_DECOMPOSITION_PATH: b"scope\n",
            }.get(path),
        )
        for path in network.patch_series_bodies._ROOT_GENERATION_PATHS
    )
    snapshot = network.patch_series_bodies.RootGenerationSnapshot(
        frozen,
        network.patch_series_bodies.ROOT_GENERATION_V2,
        network.patch_series_bodies.ROOT_DISPOSITION_V2_READY,
        members,
    )

    class RootCodec:
        def parse(self, context, raw, *, expected):
            assert context == network.patch_series_bodies.ROOT_APPROACH_CONTEXT
            assert raw == canonical_root
            return _producer_aware_network_root()

    class Decomposition:
        frozen_oid = frozen
        canonical_root_sha256 = hashlib.sha256(canonical_root).hexdigest()
        body_sha256 = "d" * 64

    monkeypatch.setattr(
        network.patch_series_bodies,
        "classify_root_generation",
        lambda _repo, _ref: snapshot,
    )
    monkeypatch.setattr(network, "BodyCodecClient", RootCodec)
    monkeypatch.setattr(
        network.patch_series_bodies,
        "project_series_scope_decomposition",
        lambda _repo, ref: Decomposition() if ref == frozen else None,
    )
    before = (_git(repo, "show-ref"), _git(repo, "status", "--porcelain"))

    decision = network.vet_producer_pair_closure(repo, branch, "stamping")

    assert decision.vet_passed is True
    assert decision.authorable is True
    assert decision.source.root_generation == network.patch_series_bodies.ROOT_GENERATION_V2
    assert decision.source.root_sha256 == hashlib.sha256(canonical_root).hexdigest()
    assert decision.source.scope_decomposition_sha256 == "d" * 64
    assert (_git(repo, "show-ref"), _git(repo, "status", "--porcelain")) == before


def test_scope_projection_failure_preserves_publication_diagnostic_on_every_stateless_route(
    tmp_path, monkeypatch
):
    repo = _repo(tmp_path)
    _write_parent(repo, "root")
    branch = "ai-org/patch-series/root"
    frozen = git_wrapper.head_sha(repo, branch)
    canonical_root = b"canonical producer root\n"
    members = tuple(
        (
            path,
            {
                network.patch_series_bodies.COVER_PATH: b"cover\n",
                network.patch_series_bodies.PROVENANCE_PATH: b"provenance\n",
                network.patch_series_bodies.ROOT_APPROACH_PATH: canonical_root,
                network.patch_series_bodies.SCOPE_DECOMPOSITION_PATH: b"scope\n",
            }.get(path),
        )
        for path in network.patch_series_bodies._ROOT_GENERATION_PATHS
    )
    snapshot = network.patch_series_bodies.RootGenerationSnapshot(
        frozen,
        network.patch_series_bodies.ROOT_GENERATION_V2,
        network.patch_series_bodies.ROOT_DISPOSITION_V2_READY,
        members,
    )

    class RootCodec:
        def parse(self, context, raw, *, expected):
            assert context == network.patch_series_bodies.ROOT_APPROACH_CONTEXT
            assert raw == canonical_root
            return _producer_aware_network_root()

    def reject_scope(_repo, ref):
        assert ref == frozen
        raise network.patch_series_bodies.SeriesScopeDecompositionError(
            "production-obligation-exact-partition",
            "root/problem/production_obligations/0",
            "obligation ownership is not exact",
        )

    monkeypatch.setattr(
        network.patch_series_bodies,
        "classify_root_generation",
        lambda _repo, _ref: snapshot,
    )
    monkeypatch.setattr(network, "BodyCodecClient", RootCodec)
    monkeypatch.setattr(
        network.patch_series_bodies,
        "project_series_scope_decomposition",
        reject_scope,
    )
    before = (_git(repo, "show-ref"), _git(repo, "status", "--porcelain"))

    for route in sorted(network.STATELESS_PRODUCER_PAIR_VET_ROUTES):
        decision = network.vet_producer_pair_closure(repo, branch, route)

        assert decision.blocked is True
        assert decision.authorable is False
        assert decision.source.route == route
        assert decision.source.frozen_root_oid == frozen
        assert decision.diagnostic is not None
        assert decision.diagnostic.route == route
        assert decision.diagnostic.frozen_root_oid == frozen
        assert (
            decision.diagnostic.cue_location
            == "root/problem/production_obligations/0"
        )
        assert (
            decision.diagnostic.rule
            == "production-obligation-exact-partition"
        )
        assert decision.diagnostic.goal_id == "goal:preview"
        assert decision.diagnostic.obligation_id == "obligation:preview"
        assert (
            decision.diagnostic.context
            == network.patch_series_bodies.SCOPE_DECOMPOSITION_CONTEXT
        )

    assert (_git(repo, "show-ref"), _git(repo, "status", "--porcelain")) == before


def test_pending_v2_scope_formation_activates_every_lifecycle_route(
    tmp_path, monkeypatch
):
    assert network.DEFAULT_PRODUCER_AWARE_CUTOVER is True
    assert network.MIGRATED_LIFECYCLE_READINESS_MATRIX == {
        "formation": True,
        "closure": True,
        "lifecycle": True,
        "acceptance": True,
        "integration": True,
        "registry": True,
        "legacy": True,
        "atomicity": True,
    }
    assert network.MIGRATED_LIFECYCLE_GATES == {
        "discovery": True,
        "production": True,
        "handoff": True,
        "acceptance": True,
        "resolution": True,
        "integration": True,
    }
    with pytest.raises(TypeError):
        network.MIGRATED_LIFECYCLE_GATES["production"] = True
    assert git_wrapper.PRODUCTION_V2_NETWORK_PUBLICATION_ENABLED is True
    with pytest.raises(TypeError):
        network.MIGRATED_LIFECYCLE_READINESS_MATRIX["atomicity"] = False
    assert network._migrated_lifecycle_readiness_gate(
        network.MIGRATED_LIFECYCLE_READINESS_MATRIX
    ) is True
    assert network._migrated_lifecycle_readiness_gate(
        {**network.MIGRATED_LIFECYCLE_READINESS_MATRIX, "atomicity": False}
    ) is False
    repo = _repo(tmp_path)
    branch = "ai-org/patch-series/root"
    root = _approach()
    root["problem"].update(
        {
            "id": "problem",
            "problem": "The root needs producer-aware scope.",
            "affected": "Maintainers and patch authors.",
            "current_inadequacy": "Scope is not yet canonical.",
            "non_goals": [],
            "constraints": {},
            "prior_art": [],
            "open_questions": [],
        }
    )
    root["problem"].update(_producer_aware_network_root()["problem"])
    producer_problem = root["problem"]
    historical_goal_id = "goal:technical_approach:1"
    producer_problem["goals"][0]["id"] = historical_goal_id
    producer_problem["deliverable_requirements"][0][
        "referee_goal_id"
    ] = historical_goal_id
    producer_problem["production_obligations"][0][
        "referee_goal_id"
    ] = historical_goal_id
    root["cross_links"] = []
    provenance = {
        "request_id": "request-v2",
        "payload_sha256": "a" * 64,
        "raw_request": "form a producer-aware root",
        "request_payload": {"raw_request": "form a producer-aware root"},
        "memento": "off-Git intake remains ingress only",
    }
    prepared = network.patch_series_bodies.prepare_root_technical_approach_preview(
        _patch_series(), provenance, root
    )
    from ai_org.patchwork_queue import receive

    receive._write_patch_series_branch(
        repo,
        branch,
        "main",
        _patch_series(),
        extra_files={
            network.patch_series_bodies.PROVENANCE_PATH: provenance,
            network.patch_series_bodies.ROOT_APPROACH_PATH: (
                prepared.canonical_cue.decode("utf-8")
            ),
        },
        commit_message="patch_series: form producer-aware root",
    )
    pending_oid = git_wrapper.head_sha(repo, branch)
    pending = network.patch_series_bodies.classify_root_generation(repo, branch)
    assert pending.disposition == network.patch_series_bodies.ROOT_DISPOSITION_V2_NOT_READY
    for route in network.STATELESS_PRODUCER_PAIR_VET_ROUTES:
        decision = network.vet_producer_pair_closure(
            repo,
            branch,
            route,
            frozen_root_oid=pending_oid,
        )
        assert decision.vet_passed is True
        assert decision.authorable is False
        assert decision.transition_allowed is (route in network.SCOPE_FORMATION_ROUTES)
        assert decision.source.frozen_root_oid == pending_oid
        assert decision.source.route == route
    assert network.vet_producer_pair_closure(
        repo, branch, "producer_code_authoring"
    ).blocked is True

    split = _single_child_split()
    split["children"][0]["scope_item_ids"].append("goal:technical_approach:1")
    _install_codex_fake(monkeypatch, [split, split])
    result = network.refine(repo, branch)

    assert result["ok"] is True, result
    assert git_wrapper.head_sha(repo, branch) != pending_oid
    ready = network.patch_series_bodies.classify_root_generation(repo, branch)
    assert ready.disposition == network.patch_series_bodies.ROOT_DISPOSITION_V2_READY
    assert ready.lifecycle_ready is True
    for route in network.PRODUCER_PAIR_VET_ROUTES:
        assert network.vet_producer_pair_closure(
            repo, branch, route
        ).authorable is True
    paths = set(git_wrapper.tree_files(repo, branch))
    assert network.patch_series_bodies.SCOPE_DECOMPOSITION_PATH in paths
    assert network.patch_series_bodies.LEGACY_ROOT_APPROACH_PATH not in paths

    # Scope formation persists the exact decomposition and is the single
    # activation boundary for every lifecycle route. Compatibility
    # observations cannot narrow it again.
    monkeypatch.setattr(network, "DEFAULT_PRODUCER_AWARE_CUTOVER", False)
    monkeypatch.setattr(
        git_wrapper, "PRODUCTION_V2_NETWORK_PUBLICATION_ENABLED", False
    )
    production_transitions = {
        route: network.prepare_network_transition(repo, branch, {}, route=route)
        for route in sorted(network.ORDINARY_NETWORK_TRANSITION_ROUTES)
    }

    assert all(item.prepared for item in production_transitions.values())
    assert all(item.releasable for item in production_transitions.values())
    assert all(
        item.publication.expected_ref_oid == git_wrapper.head_sha(repo, branch)
        for item in production_transitions.values()
    )
    assert all(
        item.source.scope_decomposition_sha256
        for item in production_transitions.values()
    )
    assert all(
        network_bodies.classify_path(
            network.patch_series_bodies.SCOPE_DECOMPOSITION_PATH
        )[2]
        in item.publication.publication.aliases
        for item in production_transitions.values()
    )
    assert all(
        item.prepared_generation == network.patch_series_bodies.ROOT_GENERATION_V2
        for item in production_transitions.values()
    )
    for item in production_transitions.values():
        assert item.publication.release_enabled is True
        assert item.publication.release_rule == ""
        assert item.diagnostic is None

    frozen_ready_oid = ready.frozen_oid
    assert frozen_ready_oid is not None
    injected = network.publish_network_transition(
        repo, production_transitions["stamping"], inject_failure=True
    )
    assert injected.ok is False
    assert injected.commit_oid == ""
    assert injected.diagnostic is not None
    assert injected.diagnostic.rule == "injected-publication-failure"
    assert git_wrapper.head_sha(repo, branch) == frozen_ready_oid

    escalation = network.escalate(
        repo,
        result["children"][0]["address"],
        {"evidence": "producer-aware scope drift"},
    )
    escalation_oid = escalation["child_commit"]
    publication_ref = f"refs/heads/{branch}"
    assert escalation["ok"] is True
    assert git_wrapper.parent_commits(repo, escalation_oid) == [frozen_ready_oid]
    assert escalation["parent_commit"]["expected_ref_oids"] == {
        publication_ref: frozen_ready_oid
    }
    assert escalation["parent_commit"]["resulting_ref_oids"] == {
        publication_ref: escalation_oid
    }
    successor_paths = set(git_wrapper.tree_files(repo, escalation_oid))
    review_path = (
        "patch-series-review-rounds/round-0001-direction-review-record.cue"
    )
    assert review_path in successor_paths
    assert network.patch_series_bodies.SCOPE_DECOMPOSITION_PATH in successor_paths
    assert network_bodies.classify_path(network.STATUS_PATH)[1] in successor_paths
    assert git_wrapper.path_last_commit(repo, branch, review_path) == escalation_oid

    git_wrapper.commit_empty(repo, branch, "patch_series v2: refreshed root")
    git_wrapper.commit_empty(repo, branch, "patch_series: direction-ok")
    assert network.rebaseline_pending(repo, branch) is True
    before_rebaseline = git_wrapper.head_sha(repo, branch)

    rebaseline = network.rebaseline(repo, branch)

    assert rebaseline["ok"] is True
    assert git_wrapper.parent_commits(repo, rebaseline["ledger_commit"]) == [
        before_rebaseline
    ]
    ledger = network_bodies.read_network_body(repo, branch, network.LEDGER_PATH)
    consumed = ledger["rebaselined_from_escalation"]
    assert consumed["review_round"].as_int_exact() == 1
    assert consumed["child_branch"] == result["children"][0]["address"]
    assert consumed["git_result_commit"] == escalation_oid
    assert set(consumed) == {
        "review_round",
        "child_branch",
        "git_result_commit",
    }
    after_rebaseline = git_wrapper.head_sha(repo, branch)

    assert network.rebaseline(repo, branch) == {
        "ok": False,
        "status": "not-pending",
        "branch": branch,
    }
    assert git_wrapper.head_sha(repo, branch) == after_rebaseline


def test_default_cutover_releases_complete_v2_lifecycle_after_scope_formation(
    tmp_path, monkeypatch
):
    test_pending_v2_scope_formation_activates_every_lifecycle_route(
        tmp_path, monkeypatch
    )


def test_invalid_producer_scope_blocks_stamping_with_publication_diagnostic(
    tmp_path, monkeypatch
):
    repo = _repo(tmp_path)
    _write_parent(repo, "root")
    branch = "ai-org/patch-series/root"
    candidate = _producer_aware_network_root()
    candidate["problem"]["patch_plan"][0]["production_obligation_ids"] = []
    git_wrapper.commit_files(
        repo,
        branch,
        {"technical-approach-plan.json": candidate},
        subject="test: invalid migrated producer scope",
    )
    before = git_wrapper.head_sha(repo, branch)
    events = []
    monkeypatch.setattr(
        network.org_log,
        "emit",
        lambda event, payload, **_kwargs: events.append((event, payload)),
    )

    result = network.stamp_children(
        repo,
        branch,
        {"id": "fixture"},
        [{"child_key": "must_not_publish"}],
    )

    assert result["ok"] is False
    assert result["status"] == "producer-pair-vet-failed"
    assert result["route"] == "stamping"
    assert result["frozen_oid"] == before
    assert result["diagnostic"]["cue_location"].startswith("root.problem")
    assert result["diagnostic"]["rule"] == "production-obligation-exact-partition"
    assert result["diagnostic"]["obligation_id"] == "obligation:preview"
    assert (
        result["diagnostic"]["context"]
        == network.patch_series_bodies.ROOT_APPROACH_CONTEXT
    )
    assert events[-1][0] == "patch_series.network.stamp_children.result"
    assert events[-1][1]["diagnostic"] == result["diagnostic"]
    assert git_wrapper.head_sha(repo, branch) == before
    assert network.split_pending(repo, branch) is False


def test_dependency_chain_collapses_to_one_root_leaf():
    approach = _graph_approach(
        _graph_item("foundation"),
        _graph_item("behavior", ["foundation"]),
        _graph_item("verification", ["behavior"]),
    )

    split = network._partition_plan_graph(approach)

    assert split["split_mode"] == "right_sized"
    assert split["children"] == []  # the parent itself is the single leaf


def test_independent_components_become_parallel_leaf_slices():
    approach = _graph_approach(_graph_item("api"), _graph_item("docs_ui"))

    split = network._partition_plan_graph(approach)

    assert split["split_mode"] == "split_into_children"
    assert len(split["children"]) == 2
    assert [
        [item["id"] for item in child["patch_plan"]["items"]]
        for child in split["children"]
    ] == [["api"], ["docs_ui"]]
    assert all(child["edges"] == [] for child in split["children"])


def test_refine_publishes_only_each_parallel_leaf_component(tmp_path):
    repo = _repo(tmp_path)
    _write_parent(repo, "root")
    git_wrapper.commit_files(
        repo,
        "ai-org/patch-series/root",
        {"technical-approach-plan.json": _graph_approach(_graph_item("api"), _graph_item("docs_ui"))},
        subject="test: independent plan graph",
    )

    result = network.refine(repo, "root")

    assert result["ok"] is True
    assert result["status"] == "refined"
    assert len(result["children"]) == 2
    slices = [
        network_bodies.read_network_body(
            repo,
            "ai-org/patch-series/root",
            f"{child['node_path']}/technical-approach-plan.json",
        )["lineage_child"]["patch_plan"]
        for child in result["children"]
    ]
    assert [[item["id"] for item in plan["items"]] for plan in slices] == [["api"], ["docs_ui"]]


def test_cross_component_dependency_projects_a_serial_raw_edge(tmp_path):
    repo = _repo(tmp_path)
    children = network._partition_plan_graph(
        _graph_approach(_graph_item("foundation"), _graph_item("consumer"))
    )["children"]
    children[1]["patch_plan"]["items"][0]["depends_on"] = ["foundation"]

    normalized = network._normalize_split(
        repo,
        "main",
        {"split_mode": "split_into_children", "rationale": "fixture", "children": children},
        [
            {"id": "foundation", "kind": "patch_plan", "text": "foundation"},
            {"id": "consumer", "kind": "patch_plan", "text": "consumer"},
        ],
        1,
    )

    depends_edges = [
        edge
        for edge in normalized["children"][1]["edges"]
        if edge["type"] == "depends"
    ]
    assert [(edge["to"], edge["state"]) for edge in depends_edges] == [
        (normalized["children"][0]["child_key"], "merged_into_subsystem_tree")
    ]


def test_scope_ledger_is_projected_without_a_split_retry_gate(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _write_parent(repo, "root")
    _install_codex_fake(monkeypatch, [_split_missing_scope(), _split_missing_scope()])

    result = network.refine(repo, "root")

    assert result["ok"] is True
    assert result["status"] == "refined"
    assert _install_codex_fake.calls == 1
    assert git_wrapper.file_exists(repo, "ai-org/patch-series/root", "series-coverage-ledger.cue") is True
    assert git_wrapper.branches(repo, "ai-org/patch-series/0001-*") == []
    assert git_wrapper.list_serials(repo) == []


def test_right_sized_partition_has_no_split_judgment_retry(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _write_parent(repo, "root")
    surplus = _right_sized_split()
    surplus["children"] = [_proposal_child("surplus", ["goal:patch_series.desired_outcomes_success"])]
    _install_codex_fake(monkeypatch, [surplus, surplus])

    result = network.refine(repo, "root")

    assert result["ok"] is True
    assert result["status"] == "right-sized"
    assert result["surplus_children_ignored"] == 0
    assert _install_codex_fake.calls == 1
    assert "Previous deterministic validation failed" not in _install_codex_fake.last_prompt
    assert git_wrapper.file_exists(repo, "ai-org/patch-series/root", "series-coverage-ledger.cue") is True
    assert git_wrapper.file_exists(repo, "ai-org/patch-series/root", "series-coverage-ledger.json") is False
    assert git_wrapper.file_exists(repo, "ai-org/patch-series/root", "patch-series-manifest.cue") is True
    assert git_wrapper.file_exists(repo, "ai-org/patch-series/root", "patch-queue-status-rollup.cue") is True
    assert git_wrapper.branches(repo, "ai-org/patch-series/0001-*") == []


def test_right_sized_root_is_deliverable_with_patchwork_ack_anchor(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _write_parent(repo, "root")
    _install_codex_fake(monkeypatch, [_right_sized_split()])

    refined = network.refine(repo, "root")

    assert refined["ok"] is True
    assert refined["status"] == "right-sized"
    facts = network.computed_patchwork_check_facts(repo, "ai-org/patch-series/root", "root")
    assert facts["checks"] == {}
    assert facts["lifecycle_status"] == "ready_for_patch_authoring"

def test_right_sized_with_surplus_children_retries_and_accepts_corrected_split(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _write_parent(repo, "root")
    surplus = _right_sized_split()
    surplus["children"] = [_proposal_child("surplus", ["goal:patch_series.desired_outcomes_success"])]
    _install_codex_fake(monkeypatch, [surplus, _split_all_scope()])

    result = network.refine(repo, "root")

    assert result["ok"] is True
    assert result["status"] == "right-sized"
    assert _install_codex_fake.calls == 1
    assert git_wrapper.file_exists(repo, "ai-org/patch-series/root", "series-coverage-ledger.cue") is True


def test_committed_dependency_edges_are_not_rejudged_by_a_split_gate(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _write_parent(repo, "root")
    first_proof_moment = _proposal_child(
        "playable",
        [
            "goal:patch_series.desired_outcomes_success",
            "patch_plan:first_proof_moment",
            "ux:technical_approach:1:screenshot_checks:1",
            "domain_specification:battle-numbers",
        ],
        depends_on=["later"],
    )
    later = _proposal_child(
        "later",
        [
            "patch_plan:follow_up:1",
            "patch_plan:deferred:1",
            "ux:technical_approach:1:interaction_checks:1",
            "ux:technical_approach:1:playtest_checks:1",
            "risk:state-drift",
        ],
    )
    later["edges"] = [_edge("depends", to="gate", reason="fixture")]
    gate = _proposal_child("gate", [])
    invalid = {
        "split_mode": "split_into_children",
        "rationale": "Inverted dependency.",
        "parent_retained_scope_ids": [],
        "children": [first_proof_moment, later, gate],
    }
    _install_codex_fake(monkeypatch, [invalid, _split_all_scope()])

    result = network.refine(repo, "root", horizon=1)

    assert result["ok"] is True
    assert _install_codex_fake.calls == 1
    assert "Previous deterministic validation failed" not in _install_codex_fake.last_prompt


def test_resolved_rolls_up_children_and_parent_integration_gate(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _write_parent(repo, "root")
    _install_codex_fake(monkeypatch, [_single_child_split()])
    result = network.refine(repo, "root")
    child = result["children"][0]["address"]
    contrib = _write_contrib_branch(repo, child, "ai-org/patch-series/root")

    assert network.resolved(repo, child) is False
    assert network.resolved(repo, "ai-org/patch-series/root") is False

    git_wrapper.commit_empty(repo, contrib, "acceptance: passed")
    assert network.resolved(repo, child) is False

    _merge(repo, "ai-org/patch-series/root", contrib)
    assert network.resolved(repo, child) is False

    _merge_into_subsystem(repo, contrib)
    assert network.resolved(repo, child) is True
    assert network.resolved(repo, "ai-org/patch-series/root") is False

    git_wrapper.commit_empty(repo, "ai-org/patch-series/root", "network: integration-gate")
    assert network.resolved(repo, "ai-org/patch-series/root") is True


def test_right_sized_root_resolves_only_after_unique_contrib_acceptance(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _write_parent(repo, "root")
    _install_codex_fake(monkeypatch, [_right_sized_split()])
    result = network.refine(repo, "root")

    assert result["status"] == "right-sized"
    git_wrapper.commit_empty(repo, "ai-org/patch-series/root", "network: integration-gate")
    assert network.resolved(repo, "ai-org/patch-series/root") is False

    contrib = _write_contrib_branch(repo, "ai-org/patch-series/root", "ai-org/patch-series/root")
    git_wrapper.commit_empty(repo, contrib, "acceptance: passed")
    _merge_into_subsystem(repo, contrib)

    assert network.resolved(repo, "ai-org/patch-series/root") is True


def test_fresh_contrib_branch_does_not_inherit_old_acceptance_subject(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    git_wrapper.commit_empty(repo, "main", "acceptance: passed")
    _write_parent(repo, "root")
    _install_codex_fake(monkeypatch, [_single_child_split()])
    result = network.refine(repo, "root")
    child = result["children"][0]["address"]
    contrib = _write_contrib_branch(repo, child, "ai-org/patch-series/root")
    _merge_into_subsystem(repo, contrib)

    assert network.resolved(repo, child) is False


def test_resolved_never_requires_child_doc_branch_ancestry_for_sibling_leaves(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _write_parent(repo, "root")
    split = {
        "split_mode": "split_into_children",
        "rationale": "Two independent implementation leaves.",
        "parent_retained_scope_ids": [],
        "children": [
                _proposal_child(
                    "prep",
                    [
                        "goal:patch_series.desired_outcomes_success",
                        "patch_plan:first_proof_moment",
                        "ux:technical_approach:1:screenshot_checks:1",
                        "domain_specification:battle-numbers",
                    ],
                ),
            _proposal_child(
                "battle",
                [
                    "patch_plan:follow_up:1",
                    "patch_plan:deferred:1",
                    "ux:technical_approach:1:interaction_checks:1",
                    "ux:technical_approach:1:playtest_checks:1",
                    "risk:state-drift",
                ],
            ),
        ],
    }
    _install_codex_fake(monkeypatch, [split])
    result = network.refine(repo, "root")
    children = [child["address"] for child in result["children"]]
    contribs = [_write_contrib_branch(repo, child, "ai-org/patch-series/root") for child in children]

    for child, contrib in zip(children, contribs):
        git_wrapper.commit_empty(repo, contrib, "acceptance: reachable")
        _merge_into_subsystem(repo, contrib)

    assert git_wrapper.branches(repo, "ai-org/patch-series/0001-*") == []
    assert all(network.resolved(repo, child) is True for child in children)
    git_wrapper.commit_empty(repo, "ai-org/patch-series/root", "network: integration-gate")
    assert network.resolved(repo, "ai-org/patch-series/root") is True


def test_resolved_rejects_legacy_branch_child_ledger(tmp_path):
    repo = _repo(tmp_path)
    _write_child_branch(repo, "0001-1", "main")
    git_wrapper.commit_files(
        repo,
        "ai-org/patch-series/0001-1",
        {"series-coverage-ledger.json": {"parent_branch": "ai-org/patch-series/root", "children": [{"branch": "ai-org/patch-series/0001-1"}]}},
        subject="network: inherited foreign ledger fixture",
    )
    git_wrapper.commit_empty(repo, "ai-org/patch-series/0001-1", "acceptance: passed")
    _merge(repo, "main", "ai-org/patch-series/0001-1")

    with pytest.raises(RuntimeError, match="legacy branch-child network records are not supported"):
        network.resolved(repo, "ai-org/patch-series/0001-1")


def test_resolved_rejects_legacy_branch_child_cycle_records(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _write_child_branch(repo, "0001-1", "main")
    _write_child_branch(repo, "0001-2", "main")
    git_wrapper.commit_files(
        repo,
        "ai-org/patch-series/0001-1",
        {"series-coverage-ledger.json": {"parent_branch": "ai-org/patch-series/0001-1", "children": [{"branch": "ai-org/patch-series/0001-2"}]}},
        subject="network: cycle fixture",
    )
    git_wrapper.commit_files(
        repo,
        "ai-org/patch-series/0001-2",
        {"series-coverage-ledger.json": {"parent_branch": "ai-org/patch-series/0001-2", "children": [{"branch": "ai-org/patch-series/0001-1"}]}},
        subject="network: cycle fixture",
    )
    monkeypatch.setattr(network, "MAX_RESOLUTION_DEPTH", 3)

    with pytest.raises(RuntimeError, match="legacy branch-child network records are not supported"):
        network.resolved(repo, "ai-org/patch-series/0001-1")


def test_escalate_blocks_child_rebaselines_parent_and_marks_dependents_stale(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _write_parent(repo, "root")
    _install_codex_fake(monkeypatch, [_split_all_scope()])
    result = network.refine(repo, "root")
    prep = result["children"][0]["address"]
    battle = result["children"][1]["address"]

    escalation = network.escalate(repo, prep, {"evidence": "parent acceptance no longer matches"})

    assert escalation["ok"] is True
    prep_meta = _address_json(repo, prep, "patch-series-manifest.json")
    battle_meta = _address_json(repo, battle, "patch-series-manifest.json")
    assert prep_meta["lifecycle_status"] == "blocked:parent-invalidated"
    assert prep_meta["escalation_evidence"]["evidence"] == "parent acceptance no longer matches"
    assert battle_meta["lifecycle_status"] == "stale"
    assert battle_meta["stale_ledger_commit"] == result["ledger_commit"]
    assert len({item["commit"] for item in escalation["stale"]}) == 1
    assert git_wrapper.has_subject(repo, "ai-org/patch-series/root", "patch_series: needs-revision round 1")
    record = review_bodies.read_record(
        repo,
        "ai-org/patch-series/root",
        "patch-series-review-rounds/round-0001-direction-review-record.cue",
        review_bodies.SYNTHETIC_RECIPE,
    )
    assert record["lineage_escalation"] is True
    assert record["verdict"] == "needs_revision"
    assert record["objections"][0]["axis"] == "scope"
    assert record["objections"][0]["type"] == "blocking"
    assert record["objections"][0]["evidence"][0]["citation"]


def test_escalation_publishes_one_complete_parent_successor_with_one_cas(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _write_parent(repo, "root")
    _install_codex_fake(monkeypatch, [_split_all_scope()])
    refined = network.refine(repo, "root")
    branch = "ai-org/patch-series/root"
    before = git_wrapper.head_sha(repo, branch)
    ledger_source_refs: list[str] = []
    cohort_source_refs: list[str | None] = []
    review_source_refs: list[str | None] = []
    refusal_source_refs: list[str | None] = []
    prepared_cohorts: list[set[str]] = []
    path_last_commit = network._network_path_last_commit
    nodes_with_ledger_commit = network._nodes_with_ledger_commit
    review_round_records = network.review_module.review_round_records
    parent_escalation_refusal = network._parent_escalation_refusal
    validate_prepared = network._validate_prepared_escalation_successor
    write_synthetic_review = network._write_synthetic_escalation_round
    review_root_snapshots = []

    def observed_path_last_commit(candidate_repo, ref, path):
        if path == network.LEDGER_PATH:
            ledger_source_refs.append(ref)
        return path_last_commit(candidate_repo, ref, path)

    def observed_nodes_with_ledger_commit(*args, **kwargs):
        cohort_source_refs.append(kwargs.get("frozen_ref"))
        return nodes_with_ledger_commit(*args, **kwargs)

    def observed_review_round_records(*args, **kwargs):
        review_source_refs.append(kwargs.get("ref"))
        return review_round_records(*args, **kwargs)

    def observed_parent_refusal(*args, **kwargs):
        refusal_source_refs.append(kwargs.get("frozen_ref"))
        return parent_escalation_refusal(*args, **kwargs)

    def observed_prepared_cohort(transition, **kwargs):
        assert git_wrapper.head_sha(repo, branch) == before
        validate_prepared(transition, **kwargs)
        prepared_cohorts.append(
            {
                member.canonical_path
                for member in transition.publication.publication.members
            }
        )

    def observed_synthetic_review(*args, **kwargs):
        review_root_snapshots.append(kwargs.get("root_snapshot"))
        return write_synthetic_review(*args, **kwargs)

    monkeypatch.setattr(network, "_network_path_last_commit", observed_path_last_commit)
    monkeypatch.setattr(network, "_nodes_with_ledger_commit", observed_nodes_with_ledger_commit)
    monkeypatch.setattr(
        network.review_module, "review_round_records", observed_review_round_records
    )
    monkeypatch.setattr(network, "_parent_escalation_refusal", observed_parent_refusal)
    monkeypatch.setattr(
        network, "_write_synthetic_escalation_round", observed_synthetic_review
    )
    monkeypatch.setattr(
        network, "_validate_prepared_escalation_successor", observed_prepared_cohort
    )

    escalation = network.escalate(
        repo, refined["children"][0]["address"], {"evidence": "scope drift"}
    )

    after = git_wrapper.head_sha(repo, branch)
    assert before and after and after != before
    assert _git(repo, "rev-list", "--count", f"{before}..{after}") == "1"
    assert git_wrapper.parent_commits(repo, after) == [before]
    assert escalation["child_commit"] == after
    assert escalation["parent_commit"]["commit"] == after
    assert {item["commit"] for item in escalation["stale"]} == {after}
    publication_ref = f"refs/heads/{branch}"
    assert escalation["expected_ref_oids"] == {publication_ref: before}
    assert escalation["resulting_ref_oids"] == {publication_ref: after}
    assert ledger_source_refs[0] == before
    assert cohort_source_refs == [before]
    assert review_source_refs == [before]
    assert refusal_source_refs == [before]
    assert len(review_root_snapshots) == 1
    assert review_root_snapshots[0].frozen_oid == before
    assert len(prepared_cohorts) == 1
    record_path = "patch-series-review-rounds/round-0001-direction-review-record.cue"
    cohort_paths = {
        "sub/prep/patch-series-manifest.cue",
        "sub/battle/patch-series-manifest.cue",
        "series-coverage-ledger.cue",
        "patch-queue-status-rollup.cue",
    }
    assert cohort_paths <= prepared_cohorts[0]
    for path in (*sorted(cohort_paths), record_path):
        assert git_wrapper.path_last_commit(repo, branch, path) == after
    record = review_bodies.read_record(
        repo, branch, record_path, review_bodies.SYNTHETIC_RECIPE
    )
    assert record["reviewed_commit"] == before
    assert "git_result_commit" not in record


def test_escalation_cas_loss_releases_none_of_the_prepared_cohort(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _write_parent(repo, "root")
    _install_codex_fake(monkeypatch, [_split_all_scope()])
    refined = network.refine(repo, "root")
    branch = "ai-org/patch-series/root"
    before = git_wrapper.head_sha(repo, branch)
    publish = git_wrapper.publish_network_publication
    monkeypatch.setattr(
        git_wrapper,
        "publish_network_publication",
        lambda candidate_repo, prepared, **_kwargs: publish(
            candidate_repo, prepared, inject_failure=True
        ),
    )

    with pytest.raises(RuntimeError, match="network publication failed"):
        network.escalate(
            repo, refined["children"][0]["address"], {"evidence": "scope drift"}
        )

    assert git_wrapper.head_sha(repo, branch) == before
    assert not git_wrapper.file_exists(
        repo,
        branch,
        "patch-series-review-rounds/round-0001-direction-review-record.cue",
    )


def test_escalation_incomplete_prepared_cohort_moves_no_ref(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _write_parent(repo, "root")
    _install_codex_fake(monkeypatch, [_split_all_scope()])
    refined = network.refine(repo, "root")
    branch = "ai-org/patch-series/root"
    before = git_wrapper.head_sha(repo, branch)
    prepare_transition = network.prepare_network_transition

    def incomplete_transition(*args, **kwargs):
        transition = prepare_transition(*args, **kwargs)
        assert transition.publication is not None
        publication = transition.publication
        body_set = publication.publication
        incomplete_members = tuple(
            member
            for member in body_set.members
            if member.canonical_path != "patch-queue-status-rollup.cue"
        )
        return replace(
            transition,
            publication=replace(
                publication,
                publication=replace(body_set, members=incomplete_members),
            ),
        )

    monkeypatch.setattr(network, "prepare_network_transition", incomplete_transition)

    with pytest.raises(RuntimeError, match="prepared escalation successor is incomplete"):
        network.escalate(
            repo,
            refined["children"][0]["address"],
            {"evidence": "scope drift"},
        )

    assert git_wrapper.head_sha(repo, branch) == before
    assert not git_wrapper.file_exists(
        repo,
        branch,
        "patch-series-review-rounds/round-0001-direction-review-record.cue",
    )


def test_escalation_rejects_incomplete_prepared_cohort_before_cas(
    tmp_path, monkeypatch
):
    repo = _repo(tmp_path)
    _write_parent(repo, "root")
    _install_codex_fake(monkeypatch, [_split_all_scope()])
    refined = network.refine(repo, "root")
    branch = "ai-org/patch-series/root"
    before = git_wrapper.head_sha(repo, branch)
    required_paths = network._escalation_successor_paths
    publish_calls: list[str] = []

    def require_missing_member(**kwargs):
        return (*required_paths(**kwargs), "missing-escalation-cohort-member.cue")

    monkeypatch.setattr(
        network, "_escalation_successor_paths", require_missing_member
    )
    monkeypatch.setattr(
        network,
        "publish_network_transition",
        lambda _repo, transition: publish_calls.append(transition.route),
    )

    with pytest.raises(
        RuntimeError,
        match="prepared successor is missing required cohort members",
    ):
        network.escalate(
            repo,
            refined["children"][0]["address"],
            {"evidence": "scope drift"},
        )

    assert publish_calls == []
    assert git_wrapper.head_sha(repo, branch) == before
    assert not git_wrapper.file_exists(
        repo,
        branch,
        "patch-series-review-rounds/round-0001-direction-review-record.cue",
    )


def test_escalation_rejects_review_retargeting_before_preparing_successor(
    tmp_path, monkeypatch
):
    repo = _repo(tmp_path)
    _write_parent(repo, "root")
    _install_codex_fake(monkeypatch, [_split_all_scope()])
    refined = network.refine(repo, "root")
    branch = "ai-org/patch-series/root"
    before = git_wrapper.head_sha(repo, branch)
    write_review = network._write_synthetic_escalation_round

    def retargeted_review(*args, **kwargs):
        path, record = write_review(*args, **kwargs)
        record = dict(record)
        record["reviewed_commit"] = "0" * 40
        return path, record

    monkeypatch.setattr(
        network, "_write_synthetic_escalation_round", retargeted_review
    )

    with pytest.raises(RuntimeError, match="not bound to the frozen cohort"):
        network.escalate(
            repo,
            refined["children"][0]["address"],
            {"evidence": "scope drift"},
        )

    assert git_wrapper.head_sha(repo, branch) == before
    assert not git_wrapper.file_exists(
        repo,
        branch,
        "patch-series-review-rounds/round-0001-direction-review-record.cue",
    )


def test_escalation_rejects_absent_child_before_preparing_successor(
    tmp_path, monkeypatch
):
    repo = _repo(tmp_path)
    _write_parent(repo, "root")
    _install_codex_fake(monkeypatch, [_split_all_scope()])
    network.refine(repo, "root")
    branch = "ai-org/patch-series/root"
    before = git_wrapper.head_sha(repo, branch)

    monkeypatch.setattr(
        network,
        "prepare_network_transition",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("invalid escalation must not prepare a successor")
        ),
    )

    with pytest.raises(RuntimeError, match="absent from the frozen tree"):
        network.escalate(
            repo,
            f"{branch}:sub/not-declared",
            {"evidence": "scope drift"},
        )

    assert git_wrapper.head_sha(repo, branch) == before
    assert not git_wrapper.file_exists(
        repo,
        branch,
        "patch-series-review-rounds/round-0001-direction-review-record.cue",
    )


def test_patch_series_pull_reforms_escalated_parent_despite_older_direction_ok(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _write_parent(repo, "root")
    _install_codex_fake(monkeypatch, [_split_all_scope()])
    result = network.refine(repo, "root")
    network.escalate(repo, result["children"][0]["address"], {"evidence": "parent scope drift"})
    calls: list[str] = []

    monkeypatch.setattr(patch_series.receive, "reform_patch_series", lambda _repo, patch_series_id: calls.append(patch_series_id) or {"status": "reformed"})
    monkeypatch.setattr(patch_series.review, "run_patch_series_review", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not review")))

    assert patch_series.pull(repo)["status"] == "reformed"
    assert calls == ["root"]


def test_patch_series_pull_reviews_parent_after_escalation_v2_commit(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _write_parent(repo, "root")
    _install_codex_fake(monkeypatch, [_split_all_scope()])
    result = network.refine(repo, "root")
    network.escalate(repo, result["children"][0]["address"], {"evidence": "parent scope drift"})
    git_wrapper.commit_empty(repo, "ai-org/patch-series/root", "patch_series v2: Battle Slice")
    calls: list[str] = []

    monkeypatch.setattr(patch_series.receive, "reform_patch_series", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not reform")))
    monkeypatch.setattr(patch_series.review, "run_patch_series_review", lambda _repo, patch_series_id: calls.append(patch_series_id) or {"status": "reviewed"})

    assert patch_series.pull(repo)["status"] == "reviewed"
    assert calls == ["root"]


def test_patch_series_pull_rebaselines_parent_after_escalation_v2_direction_ok(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _write_parent(repo, "root")
    _install_codex_fake(monkeypatch, [_split_all_scope()])
    result = network.refine(repo, "root")
    network.escalate(repo, result["children"][0]["address"], {"evidence": "parent scope drift"})
    git_wrapper.commit_empty(repo, "ai-org/patch-series/root", "patch_series v2: Battle Slice")
    git_wrapper.commit_empty(repo, "ai-org/patch-series/root", "patch_series: direction-ok")
    calls: list[str] = []

    monkeypatch.setattr(patch_series.network, "rebaseline", lambda _repo, branch: calls.append(branch) or {"status": "rebaselined"})

    assert patch_series.pull(repo)["status"] == "rebaselined"
    assert calls == ["ai-org/patch-series/root"]


def test_rebaseline_versions_ledger_and_revalidate_stale_unchanged_child(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _write_parent(repo, "root")
    _install_codex_fake(monkeypatch, [_split_all_scope(), _split_all_scope()])
    result = network.refine(repo, "root")
    prep = result["children"][0]["address"]
    battle = result["children"][1]["address"]
    network.escalate(repo, prep, {"evidence": "parent scope drift"})
    git_wrapper.commit_empty(repo, "ai-org/patch-series/root", "patch_series v2: Battle Slice")
    git_wrapper.commit_empty(repo, "ai-org/patch-series/root", "patch_series: direction-ok")

    rebaseline = network.rebaseline(repo, "ai-org/patch-series/root")

    assert rebaseline["ok"] is True
    assert rebaseline["status"] == "rebaselined"
    ledger = network_bodies.read_network_body(
        repo, "ai-org/patch-series/root", "series-coverage-ledger.json"
    )
    assert ledger["ledger_revision"].as_int_exact() == 2
    assert ledger["supersedes_ledger_commit"] == rebaseline["supersedes_ledger_commit"]
    assert ledger["supersedes_ledger_commit"]
    assert ledger["rebaselined_from_escalation"]["review_round"].as_int_exact() == 1
    assert network.stale_revalidation_pending(repo, battle) is True

    revalidated = network.revalidate_stale(repo, battle)

    assert revalidated["ok"] is True
    assert revalidated["status"] == "reactivated"
    battle_meta = _address_json(repo, battle, "patch-series-manifest.json")
    assert battle_meta["lifecycle_status"] == "posted_to_mailing_list"
    assert battle_meta["ledger_commit"] == rebaseline["ledger_commit"]


def test_rebaseline_consumes_git_derived_review_tuple_and_repeat_poll_is_noop(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _write_parent(repo, "root")
    _install_codex_fake(monkeypatch, [_split_all_scope(), _split_all_scope()])
    refined = network.refine(repo, "root")
    branch = "ai-org/patch-series/root"
    child = refined["children"][0]["address"]
    escalation = network.escalate(repo, child, {"evidence": "scope drift"})
    review_path = "patch-series-review-rounds/round-0001-direction-review-record.cue"
    containing_commit = git_wrapper.path_last_commit(repo, branch, review_path)
    assert containing_commit == escalation["child_commit"]
    git_wrapper.commit_empty(repo, branch, "patch_series v2: Battle Slice")
    git_wrapper.commit_empty(repo, branch, "patch_series: direction-ok")
    assert network.rebaseline_pending(repo, branch) is True
    vet_calls: list[tuple[str, str]] = []
    consumed_tuple_reads: list[dict[str, object]] = []
    real_vet = network.vet_producer_pair_closure
    real_escalation_record = network._escalation_rebaseline_record

    def counted_vet(candidate_repo, candidate_branch, route, **kwargs):
        vet_calls.append((candidate_branch, route))
        return real_vet(candidate_repo, candidate_branch, route, **kwargs)

    def counted_escalation_record(candidate_repo, candidate_branch, record, **kwargs):
        value = real_escalation_record(
            candidate_repo, candidate_branch, record, **kwargs
        )
        consumed_tuple_reads.append(value)
        return value

    monkeypatch.setattr(network, "vet_producer_pair_closure", counted_vet)
    monkeypatch.setattr(
        network, "_escalation_rebaseline_record", counted_escalation_record
    )

    rebaseline = network.rebaseline(repo, branch)
    ledger = network_bodies.read_network_body(
        repo, branch, "series-coverage-ledger.json"
    )
    consumed = ledger["rebaselined_from_escalation"]
    assert consumed["review_round"].as_int_exact() == 1
    assert consumed["child_branch"] == child
    assert consumed["git_result_commit"] == containing_commit
    assert set(consumed) == {"review_round", "child_branch", "git_result_commit"}
    assert rebaseline["ok"] is True
    assert consumed_tuple_reads == [
        {
            "review_round": 1,
            "child_branch": child,
            "git_result_commit": containing_commit,
        }
    ]
    # The transition decision and its publication preflight independently vet
    # the same frozen source; tuple discovery itself still happens exactly once.
    assert [call for call in vet_calls if call[1] == "rebaseline_decision"] == [
        (branch, "rebaseline_decision"),
        (branch, "rebaseline_decision"),
    ]
    publication_ref = f"refs/heads/{branch}"
    assert rebaseline["expected_ref_oids"] == {
        publication_ref: git_wrapper.parent_commits(
            repo, rebaseline["resulting_ref_oids"][publication_ref]
        )[0]
    }
    assert rebaseline["resulting_ref_oids"] == {
        publication_ref: rebaseline["ledger_commit"]
    }
    assert network.rebaseline_pending(repo, branch) is False
    original_read_json = network._read_json
    with monkeypatch.context() as scoped:
        def stale_consumption(candidate_repo, ref, path, *args, **kwargs):
            value = original_read_json(candidate_repo, ref, path, *args, **kwargs)
            if path == network.LEDGER_PATH and isinstance(value, dict):
                value = dict(value)
                consumed_value = dict(value["rebaselined_from_escalation"])
                consumed_value["git_result_commit"] = "0" * 40
                value["rebaselined_from_escalation"] = consumed_value
            return value

        scoped.setattr(network, "_read_json", stale_consumption)
        assert network.rebaseline_pending(repo, branch) is True
    after = git_wrapper.head_sha(repo, branch)
    monkeypatch.setattr(
        network,
        "_split_plan_graph",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("a consumed escalation must not prepare another successor")
        ),
    )

    repeated = network.rebaseline(repo, branch)

    assert repeated == {"ok": False, "status": "not-pending", "branch": branch}
    assert git_wrapper.head_sha(repo, branch) == after


def test_rebaseline_rejects_review_oid_outside_atomic_escalation_successor(
    tmp_path, monkeypatch
):
    repo = _repo(tmp_path)
    _write_parent(repo, "root")
    _install_codex_fake(monkeypatch, [_split_all_scope(), _split_all_scope()])
    refined = network.refine(repo, "root")
    branch = "ai-org/patch-series/root"
    escalation = network.escalate(
        repo, refined["children"][0]["address"], {"evidence": "scope drift"}
    )
    containing_commit = escalation["child_commit"]
    git_wrapper.commit_empty(repo, branch, "patch_series v2: Battle Slice")
    git_wrapper.commit_empty(repo, branch, "patch_series: direction-ok")
    before = git_wrapper.head_sha(repo, branch)
    parent_commits = git_wrapper.parent_commits

    def severed_review_parent(candidate_repo, ref):
        if ref == containing_commit:
            return ["0" * len(containing_commit)]
        return parent_commits(candidate_repo, ref)

    monkeypatch.setattr(git_wrapper, "parent_commits", severed_review_parent)
    monkeypatch.setattr(
        network,
        "_split_plan_graph",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("an unproven review OID must not prepare a successor")
        ),
    )

    with pytest.raises(
        RuntimeError,
        match="review is not contained by its direct successor",
    ):
        network.rebaseline(repo, branch)

    assert git_wrapper.head_sha(repo, branch) == before


def test_rebaseline_pending_reads_the_single_vetted_snapshot(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _write_parent(repo, "root")
    _install_codex_fake(monkeypatch, [_split_all_scope()])
    refined = network.refine(repo, "root")
    branch = "ai-org/patch-series/root"
    network.escalate(
        repo,
        refined["children"][0]["address"],
        {"evidence": "scope drift"},
    )
    git_wrapper.commit_empty(repo, branch, "patch_series v2: Battle Slice")
    git_wrapper.commit_empty(repo, branch, "patch_series: direction-ok")
    frozen = git_wrapper.head_sha(repo, branch)
    assert frozen is not None
    real_vet = network.vet_producer_pair_closure
    real_classifier = network.patch_series_bodies.classify_root_generation
    classified_refs: list[str] = []

    def classify_once(candidate_repo, ref):
        classified_refs.append(ref)
        return real_classifier(candidate_repo, ref)

    def move_after_vet(candidate_repo, candidate_branch, route, **kwargs):
        decision = real_vet(
            candidate_repo, candidate_branch, route, **kwargs
        )
        git_wrapper.commit_empty(
            repo, branch, "patch_series: needs-revision round 99"
        )
        return decision

    monkeypatch.setattr(network, "vet_producer_pair_closure", move_after_vet)
    monkeypatch.setattr(
        network.patch_series_bodies,
        "classify_root_generation",
        classify_once,
    )

    assert network.rebaseline_pending(repo, branch) is True
    assert classified_refs == [frozen]
    assert git_wrapper.head_sha(repo, branch) != frozen


@pytest.mark.parametrize(
    "consumed",
    [
        {"review_round": 1, "child_branch": "ai-org/contrib/child"},
        {
            "review_round": 1,
            "child_branch": "ai-org/contrib/child",
            "git_result_commit": "a" * 40,
            "review_record_path": "patch-series-review-rounds/round-0001-direction-review-record.cue",
        },
        {
            "review_round": 0,
            "child_branch": "ai-org/contrib/child",
            "git_result_commit": "a" * 40,
        },
        {
            "review_round": "1",
            "child_branch": "ai-org/contrib/child",
            "git_result_commit": "a" * 40,
        },
        {
            "review_round": True,
            "child_branch": "ai-org/contrib/child",
            "git_result_commit": "a" * 40,
        },
        {
            "review_round": 1.0,
            "child_branch": "ai-org/contrib/child",
            "git_result_commit": "a" * 40,
        },
        {
            "review_round": 1,
            "child_branch": 7,
            "git_result_commit": "a" * 40,
        },
    ],
)
def test_escalation_consumption_normalization_rejects_inexact_tuple(consumed):
    assert network._normalized_escalation_rebaseline_record(consumed) == {}


@pytest.mark.parametrize("oid", ["a" * 40, "b" * 64])
def test_escalation_consumption_normalization_preserves_exact_tuple(oid):
    consumed = {
        "review_round": 1,
        "child_branch": "ai-org/patch-series/root:sub/child",
        "git_result_commit": oid,
    }

    assert network._normalized_escalation_rebaseline_record(consumed) == consumed


def test_revalidate_stale_blocks_changed_parent_contract(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _write_parent(repo, "root")
    changed = _split_with_battle_scope_changed()
    _install_codex_fake(monkeypatch, [_split_all_scope(), changed])
    result = network.refine(repo, "root")
    prep = result["children"][0]["address"]
    battle = result["children"][1]["address"]
    network.escalate(repo, prep, {"evidence": "parent scope drift"})
    git_wrapper.commit_empty(repo, "ai-org/patch-series/root", "patch_series v2: Battle Slice")
    git_wrapper.commit_empty(repo, "ai-org/patch-series/root", "patch_series: direction-ok")
    network.rebaseline(repo, "ai-org/patch-series/root")

    revalidated = network.revalidate_stale(repo, battle)

    assert revalidated["ok"] is False
    assert revalidated["status"] == "blocked:parent-rebaseline-changed"
    battle_meta = _address_json(repo, battle, "patch-series-manifest.json")
    assert battle_meta["lifecycle_status"] == "blocked:parent-rebaseline-changed"


def test_stamp_children_preserves_refined_ledger_contract_for_stale_sibling_revalidation(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _write_parent(repo, "root")
    _install_codex_fake(monkeypatch, [_split_all_scope()])
    result = network.refine(repo, "root")
    battle = result["children"][1]["address"]
    before = network_bodies.read_network_body(
        repo, "ai-org/patch-series/root", "series-coverage-ledger.json"
    )
    before_battle = next(child for child in before["children"] if child["child_key"] == "battle")
    network.mark_stale(repo, [battle], "fixture stale", superseded_ledger_commit=result["ledger_commit"])

    stamped = network.stamp_children(
        repo,
        "ai-org/patch-series/root",
        {"id": "sidecar", "title": "Sidecar"},
        [{"child_key": "sidecar", "summary": "Stamped unrelated sibling."}],
    )

    assert stamped["ok"] is True
    after = network_bodies.read_network_body(
        repo, "ai-org/patch-series/root", "series-coverage-ledger.json"
    )
    after_battle = next(child for child in after["children"] if child["child_key"] == "battle")
    for field in ("id", "serial_id", "contrib_branch", "scope_item_ids"):
        assert after_battle[field] == before_battle[field]

    revalidated = network.revalidate_stale(repo, battle)

    assert revalidated["ok"] is True
    assert revalidated["status"] == "reactivated"


def test_elaborated_child_is_not_split_pending_and_is_right_sized(tmp_path):
    repo = _repo(tmp_path)
    _write_child_branch(repo, "0001-1", "main")
    child = _proposal_child("leaf", ["goal:patch_series.desired_outcomes_success"])
    child["id"] = "0001-1"
    child["lifecycle_status"] = "ready_for_patch_authoring"
    git_wrapper.commit_files(
        repo,
        "ai-org/patch-series/0001-1",
        {
            "patch-series-metadata.json": {
                "schema": "patch_series-network-node-v1",
                "id": "0001-1",
                "parent_branch": "ai-org/patch-series/root",
                "lifecycle_status": "ready_for_patch_authoring",
            },
            "technical-approach-plan.json": network._child_approach(child),
        },
        subject="network: leaf metadata",
    )

    assert network.right_sized(network._child_approach(child)) is True
    assert network.split_pending(repo, "ai-org/patch-series/0001-1") is False


def test_elaborate_waits_for_resolved_dependencies(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _write_parent(repo, "root")
    _install_codex_fake(monkeypatch, [_split_all_scope(), _right_sized_split()])
    result = network.refine(repo, "root", horizon=1)
    prep = result["children"][0]["address"]
    battle = result["children"][1]["address"]

    assert network.coarse_ready(repo, battle) is False
    assert network.elaborate(repo, battle)["status"] == "blocked-by-dependencies"

    contrib = _write_contrib_branch(repo, prep, "ai-org/patch-series/root")
    git_wrapper.commit_empty(repo, contrib, "acceptance: passed")
    assert network.coarse_ready(repo, battle) is False

    _merge(repo, "ai-org/patch-series/root", contrib)
    assert network.coarse_ready(repo, battle) is False

    _merge_into_subsystem(repo, contrib)
    assert network.coarse_ready(repo, battle) is True

    elaborated = network.elaborate(repo, battle)
    assert elaborated["ok"] is True
    assert elaborated["status"] == "ready_for_patch_authoring"


def test_stale_coarse_child_is_not_elaborated_until_revalidated(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _write_parent(repo, "root")
    _install_codex_fake(monkeypatch, [_split_all_scope()])
    result = network.refine(repo, "root", horizon=1)
    prep = result["children"][0]["address"]
    battle = result["children"][1]["address"]
    network.mark_stale(repo, [battle], "fixture stale", superseded_ledger_commit=result["ledger_commit"])

    contrib = _write_contrib_branch(repo, prep, "ai-org/patch-series/root")
    git_wrapper.commit_empty(repo, contrib, "acceptance: passed")
    _merge_into_subsystem(repo, contrib)

    assert network.coarse_ready(repo, battle) is False
    assert network.elaborate(repo, battle)["status"] == "blocked-by-dependencies"


def test_escalate_refuses_nak_parent_without_blocking_child(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _write_parent(repo, "root")
    _install_codex_fake(monkeypatch, [_split_all_scope()])
    result = network.refine(repo, "root")
    prep = result["children"][0]["address"]
    git_wrapper.commit_empty(repo, "ai-org/patch-series/root", "patch_series: nak")

    escalation = network.escalate(repo, prep, {"evidence": "too late"})

    assert escalation["ok"] is False
    assert escalation["status"] == "parent-terminal-nak"
    prep_meta = _address_json(repo, prep, "patch-series-manifest.json")
    assert prep_meta["lifecycle_status"] == "ready_for_patch_authoring"


def test_escalate_refuses_merged_parent_without_blocking_child(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _write_parent(repo, "root")
    _install_codex_fake(monkeypatch, [_split_all_scope()])
    result = network.refine(repo, "root")
    prep = result["children"][0]["address"]
    _merge(repo, "main", "ai-org/patch-series/root")

    escalation = network.escalate(repo, prep, {"evidence": "too late"})

    assert escalation["ok"] is False
    assert escalation["status"] == "parent-merged-to-default"
    prep_meta = _address_json(repo, prep, "patch-series-manifest.json")
    assert prep_meta["lifecycle_status"] == "ready_for_patch_authoring"


def test_lineage_schema_is_codex_safe_subset_and_uses_unambiguous_modes():
    schema = network.LINEAGE_SPLIT_SCHEMA
    forbidden = {"$schema", "allOf", "anyOf", "oneOf", "not", "if", "then", "else", "const", "pattern", "format"}
    assert _forbidden_schema_keys(schema, forbidden) == []
    _assert_required_is_all_properties(schema)
    _assert_required_is_all_properties(schema["properties"]["children"]["items"])
    assert schema["properties"]["split_mode"]["enum"] == ["right_sized", "split_into_children"]
    child_props = schema["properties"]["children"]["items"]["properties"]
    assert "kind" not in child_props
    assert "node_kind" not in child_props
    assert "branching_mode" not in child_props
    assert "serial_after_child_key" not in child_props
    assert "depends_on_child_keys" not in child_props
    assert "scope_item_ids" not in child_props
    assert "patch_plan" in child_props
    assert "edges" in child_props
    assert "right_sized" not in child_props


def test_lineage_ledger_and_status_do_not_persist_dependency_graph(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _write_parent(repo, "root")
    _install_codex_fake(monkeypatch, [_split_all_scope()])

    result = network.refine(repo, "root", horizon=1)

    ledger = network_bodies.read_network_body(
        repo, "ai-org/patch-series/root", "series-coverage-ledger.json"
    )
    status = network_bodies.read_network_body(
        repo, "ai-org/patch-series/root", "patch-queue-status-rollup.json"
    )
    assert result["dependency_graph"]
    assert set(result["dependency_graph"][0]) == {"prerequisite_node_path", "dependent_node_path"}
    assert "dependency_graph" not in ledger
    assert "dependency_graph" not in status
    serialized = json.dumps(ledger, default=str)
    assert '"from"' not in serialized
    assert '"depends_on_child_keys"' not in serialized
    assert '"depends_on_node_paths"' not in serialized


def test_patch_series_pull_runs_lineage_after_reviewable_and_before_coarse_elaboration(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    git_wrapper.create_branch_with_files(
        repo,
        "ai-org/patch-series/reviewable",
        "main",
        {"patch-series-cover-letter.json": _patch_series(), "technical-approach-plan.json": _approach()},
        commit_message="patch_series: draft",
    )
    _write_parent(repo, "root")
    calls: list[str] = []

    monkeypatch.setattr(patch_series.review, "run_patch_series_review", lambda _repo, patch_series_id: calls.append(f"review:{patch_series_id}") or {"status": "reviewed"})
    monkeypatch.setattr(patch_series.network, "refine", lambda _repo, branch: calls.append(f"refine:{branch}") or {"status": "refined"})
    monkeypatch.setattr(patch_series.network, "elaborate", lambda _repo, branch: calls.append(f"elaborate:{branch}") or {"status": "ready_for_patch_authoring"})

    assert patch_series.pull(repo)["status"] == "reviewed"
    _git(repo, "branch", "-D", "ai-org/patch-series/reviewable")
    assert patch_series.pull(repo)["status"] == "refined"
    assert calls == ["review:reviewable", "refine:ai-org/patch-series/root"]


def test_subtree_write_scope_gate_rejects_parent_generated_paths(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _write_parent(repo, "root")
    _install_codex_fake(monkeypatch, [_split_all_scope()])
    result = network.refine(repo, "root", horizon=1)
    base = git_wrapper.head_sha(repo, "ai-org/patch-series/root")
    assert base
    git_wrapper.create_branch_with_files(
        repo,
        "candidate/out-of-scope",
        "ai-org/patch-series/root",
        {
            "sub/prep/notes.txt": "inside\n",
            "patch-queue-status-rollup.json": {"generated": False},
        },
        commit_message="candidate: mixed scope",
    )

    gate = network.validate_child_write_scope(repo, result["children"][0]["address"], base, "candidate/out-of-scope")

    assert gate["ok"] is False
    assert gate["status"] == "blocked:write-scope"
    assert gate["offending_paths"] == ["patch-queue-status-rollup.json"]


def test_subtree_write_scope_gate_allows_child_subtree_only(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _write_parent(repo, "root")
    _install_codex_fake(monkeypatch, [_split_all_scope()])
    result = network.refine(repo, "root", horizon=1)
    base = git_wrapper.head_sha(repo, "ai-org/patch-series/root")
    assert base
    git_wrapper.create_branch_with_files(
        repo,
        "candidate/in-scope",
        "ai-org/patch-series/root",
        {"sub/prep/notes.txt": "inside\n"},
        commit_message="candidate: child scope",
    )

    gate = network.validate_child_write_scope(repo, result["children"][0]["address"], base, "candidate/in-scope")

    assert gate["ok"] is True
    assert gate["offending_paths"] == []


def test_spine_ancestry_gate_requires_parent_spine_commit(tmp_path):
    repo = _repo(tmp_path)
    _write_parent(repo, "root")
    git_wrapper.commit_files(repo, "ai-org/patch-series/root", {"spine/api.json": {"version": 1}}, subject="network: spine api")
    spine_commit = git_wrapper.head_sha(repo, "ai-org/patch-series/root")
    assert spine_commit
    git_wrapper.create_branch_with_files(repo, "child/missing-spine", "main", {"sub/prep/work.txt": "x\n"}, commit_message="work")
    git_wrapper.create_branch_with_files(repo, "child/with-spine", "ai-org/patch-series/root", {"sub/prep/work.txt": "x\n"}, commit_message="work")

    assert network.validate_spine_ancestry(repo, "child/missing-spine", [spine_commit])["ok"] is False
    assert network.validate_spine_ancestry(repo, "child/with-spine", [spine_commit])["ok"] is True


def test_stamp_children_writes_request_only_nodes_with_provenance(tmp_path):
    repo = _repo(tmp_path)
    _write_parent(repo, "root")

    result = network.stamp_children(
        repo,
        "ai-org/patch-series/root",
        {"id": "encounter-template", "title": "Encounter"},
        [{"child_key": "encounter_001", "summary": "Build encounter 001."}],
        stamping_author="tester-signature",
    )

    assert result["ok"] is True
    assert result["children"][0]["address"] == "ai-org/patch-series/root:sub/encounter_001"
    request = _node_json(repo, "sub/encounter_001", "maintainer-series-request.json")
    manifest = _node_json(repo, "sub/encounter_001", "patch-series-manifest.json")
    assert request["request"]["raw_request"] == "Build encounter 001."
    assert manifest["lifecycle_status"] == "posted_to_mailing_list"
    assert manifest["stamping_provenance"]["template_id"] == "encounter-template"
    assert manifest["stamping_provenance"]["stamping_author_signature"] == "tester-signature"
    assert not git_wrapper.file_exists(repo, "ai-org/patch-series/root", "sub/encounter_001/patch-series-cover-letter.json")


def test_stamp_children_rejects_invalid_child_key_without_sibling_fork(tmp_path):
    repo = _repo(tmp_path)
    _write_parent(repo, "root")
    git_wrapper.commit_files(
        repo,
        "ai-org/patch-series/root",
        {
            "sub/pay-api/patch-series-manifest.json": {
                "schema": "patch_series-network-node-v1",
                "child_key": "pay-api",
                "node_path": "sub/pay-api",
                "lifecycle_status": "posted_to_mailing_list",
                "edges": [],
            }
        },
        subject="test: legacy invalid child key fixture",
    )

    result = network.stamp_children(
        repo,
        "ai-org/patch-series/root",
        {"id": "pay", "title": "Pay"},
        [{"child_key": "pay-api", "summary": "Pay API."}],
    )

    assert result["ok"] is False
    assert result["status"] == "child-key-invalid"
    assert result["errors"] == [{"type": "child_key_invalid", "child_key": "pay-api", "message": "child_key must match [a-z0-9_]+"}]
    assert git_wrapper.file_exists(repo, "ai-org/patch-series/root", "sub/pay-api/patch-series-manifest.json")
    assert not git_wrapper.file_exists(repo, "ai-org/patch-series/root", "sub/pay_api/patch-series-manifest.json")


def test_split_pending_does_not_rejudge_stamped_scope_distribution(tmp_path):
    repo = _repo(tmp_path)
    _write_parent(repo, "root")

    assert network.split_pending(repo, "ai-org/patch-series/root") is True

    result = network.stamp_children(
        repo,
        "ai-org/patch-series/root",
        {"id": "partial", "title": "Partial"},
        [{"child_key": "partial", "summary": "Cover one item.", "scope_item_ids": ["goal:patch_series.desired_outcomes_success"]}],
    )

    assert result["ok"] is True
    assert network.split_pending(repo, "ai-org/patch-series/root") is False


def test_elaborate_prefers_child_request_contract_over_parent_cover_letter(tmp_path):
    repo = _repo(tmp_path)
    _write_parent(repo, "root")
    rust_stack = {
        "build_strategy": "framework_based",
        "engine": "",
        "framework": "cargo",
        "language": "Rust",
        "platform": "CLI",
        "rationale": "Exercise child override.",
        "provenance": "requester_specified",
    }
    stamped = network.stamp_children(
        repo,
        "ai-org/patch-series/root",
        {"id": "rust-template", "title": "Rust Child"},
        [
            {
                "child_key": "rust_child",
                "summary": "Build the Rust child.",
                "acceptance_criteria": ["Rust child is reviewable."],
                "functional_check": "Run the Rust child check.",
                "tech_stack": rust_stack,
            }
        ],
    )

    assert stamped["ok"] is True
    elaborated = network.elaborate(repo, "ai-org/patch-series/root:sub/rust_child")

    assert elaborated["ok"] is True
    cover = _node_json(repo, "sub/rust_child", "patch-series-cover-letter.json")
    assert cover["tech_stack"]["language"] == "Rust"


def test_elaborate_posts_one_offer_per_node_with_cover_commit(tmp_path):
    repo = _repo(tmp_path)
    _write_parent(repo, "root")
    stamped = network.stamp_children(
        repo,
        "ai-org/patch-series/root",
        {"id": "offer-template", "title": "Offer Child"},
        [{"child_key": "offered", "summary": "Build the offered child."}],
    )
    assert stamped["ok"] is True

    first = network.elaborate(repo, "ai-org/patch-series/root:sub/offered")

    assert first["ok"] is True
    offers = mailing_list.read(repo, kinds={"offer"})
    assert [(post["subject"], post["refs"]) for post in offers] == [
        (
            "ai-org/patch-series/root:sub/offered",
            {
                "cover": first["commit"],
                "node": "sub/offered",
                "series": "ai-org/patch-series/root",
            },
        )
    ]

    manifest = _node_json(repo, "sub/offered", "patch-series-manifest.json")
    metadata = _node_json(repo, "sub/offered", "patch-series-metadata.json")
    manifest["lifecycle_status"] = "posted_to_mailing_list"
    metadata["lifecycle_status"] = "posted_to_mailing_list"
    network._commit_network_files(
        repo,
        "ai-org/patch-series/root",
        {
            "sub/offered/patch-series-manifest.json": manifest,
            "sub/offered/patch-series-metadata.json": metadata,
        },
        subject="test: reopen offered node",
    )

    second = network.elaborate(repo, "ai-org/patch-series/root:sub/offered")

    assert second["ok"] is True
    assert second["commit"] != first["commit"]
    assert len(mailing_list.read(repo, kinds={"offer"})) == 1


def test_elaborate_continues_when_offer_post_fails(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _write_parent(repo, "root")
    stamped = network.stamp_children(
        repo,
        "ai-org/patch-series/root",
        {"id": "offer-template", "title": "Offer Child"},
        [{"child_key": "offered", "summary": "Build the offered child."}],
    )
    assert stamped["ok"] is True
    warnings: list[tuple[str, dict, str]] = []
    monkeypatch.setattr(
        network.mailing_list,
        "post",
        lambda *args, **kwargs: {"ok": False, "error": "injected offer failure"},
    )
    monkeypatch.setattr(
        network.org_log,
        "debug_emit",
        lambda event_type, payload, *, ctx, severity="debug": warnings.append(
            (event_type, payload, severity)
        ),
    )

    result = network.elaborate(repo, "ai-org/patch-series/root:sub/offered")

    assert result["ok"] is True
    assert result["status"] == "ready_for_patch_authoring"
    assert warnings == [
        (
            "mailing_list.offer.failed",
            {
                "series": "ai-org/patch-series/root",
                "node": "sub/offered",
                "error": "injected offer failure",
            },
            "warning",
        )
    ]


def test_split_emits_unlabeled_commission_request_from_spine_citations(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _write_parent(repo, "root")
    git_wrapper.commit_files(
        repo,
        "ai-org/patch-series/root",
        {
            "spine/art-bible.json": {"palette": "fixture"},
            "spine/asset-manifest.schema.json": {"type": "object"},
        },
        subject="spine: add commission contracts",
    )
    split = _single_child_split()
    child = split["children"][0]
    assert isinstance(child, dict)
    child["child_key"] = "hero"
    child["title"] = "Hero Portrait"
    child["summary"] = (
        "Commission the hero portrait row; cite spine/art-bible.json and "
        "spine/asset-manifest.schema.json. The request text declares the portrait deliverable."
    )
    _install_codex_fake(monkeypatch, [split])

    result = network.refine(repo, "root")

    assert result["ok"] is True
    assert "kind" not in result["children"][0]
    request = _node_json(repo, "sub/hero", "maintainer-series-request.json")
    manifest = _node_json(repo, "sub/hero", "patch-series-manifest.json")
    metadata = _node_json(repo, "sub/hero", "patch-series-metadata.json")
    approach = _node_json(repo, "sub/hero", "technical-approach-plan.json")
    ledger = network_bodies.read_network_body(
        repo, "ai-org/patch-series/root", "series-coverage-ledger.json"
    )
    status = network_bodies.read_network_body(
        repo, "ai-org/patch-series/root", "patch-queue-status-rollup.json"
    )
    commission = request["commission_request"]
    assert commission["spine_contract_refs"] == [
        {"role": "art_bible_path", "path": "spine/art-bible.json"},
        {
            "role": "asset_manifest_schema_path",
            "path": "spine/asset-manifest.schema.json",
        },
    ]
    assert commission["identity_manifest_fields"] == [
        {"child_key": "hero", "field": field}
        for field in ("child_key", "license_provenance", "acceptance_checks")
    ]
    assert {item["child_key"] for item in commission["contract_fields"]} == {"hero"}
    assert [row["field"] for row in commission["contract_fields"]] == [
        "subject",
        "acceptance_criteria",
        "usage_license",
        "revision_limit",
    ]
    assert "asset_commission_" not in json.dumps(commission)
    assert "kind" not in commission
    assert "kind" not in manifest
    assert "kind" not in metadata
    assert "kind" not in approach["lineage_child"]
    assert all("kind" not in item for item in ledger["children"])
    assert all("kind" not in item for item in status["children"])


def test_split_omits_absent_spine_contract_refs_and_keeps_generic_commission_rows(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _write_parent(repo, "root")
    split = _single_child_split()
    child = split["children"][0]
    assert isinstance(child, dict)
    child["child_key"] = "hero"
    child["title"] = "Hero Portrait"
    child["summary"] = (
        "Commission the hero portrait row; cite spine/art-bible.json and "
        "spine/asset-manifest.schema.json. The request text declares format, dimensions, and palette needs."
    )
    _install_codex_fake(monkeypatch, [split])

    result = network.refine(repo, "root")

    assert result["ok"] is True
    envelope = _node_json(repo, "sub/hero", "maintainer-series-request.json")
    request = envelope["request"]
    commission = envelope["commission_request"]
    rows = {
        row["field"]: row["value"]
        for row in commission["contract_fields"]
    }
    assert request["references"] == []
    assert commission["spine_contract_refs"] == []
    assert set(rows) == {"subject", "acceptance_criteria", "usage_license", "revision_limit"}
    assert "spine/art-bible.json" not in json.dumps(commission)
    assert "spine/asset-manifest.schema.json" not in json.dumps(commission)
    assert "asset_commission_" not in json.dumps(request)


def test_scenario_split_keeps_text_commission_and_flag_graph_code_separate(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _write_parent(repo, "root")
    git_wrapper.commit_files(
        repo,
        "ai-org/patch-series/root",
        {
            "spine/art-bible.json": {"canon": "fixture"},
            "spine/asset-manifest.schema.json": {"type": "object"},
        },
        subject="spine: add commission contracts",
    )
    split = _single_child_split()
    split["children"] = [
        _proposal_child("dialogue", ["goal:patch_series.desired_outcomes_success"]),
        _proposal_child(
            "flag_graph",
            [item for item in _expected_scope_ids() if item != "goal:patch_series.desired_outcomes_success"],
            depends_on=["dialogue"],
        ),
    ]
    dialogue_child = split["children"][0]
    assert isinstance(dialogue_child, dict)
    dialogue_child["summary"] = (
        "Write dialogue text commission rows; cite spine/art-bible.json and spine/asset-manifest.schema.json. "
        "Include string keys, speaker tags, length limits, and flag references in the request text."
    )
    _install_codex_fake(monkeypatch, [split])

    result = network.refine(repo, "root")

    assert result["ok"] is True
    dialogue_request = _node_json(repo, "sub/dialogue", "maintainer-series-request.json")
    flag_request = _node_json(repo, "sub/flag_graph", "maintainer-series-request.json")
    scenario_fields = dialogue_request["commission_request"]["contract_fields"]
    assert [row["field"] for row in scenario_fields] == [
        "subject",
        "acceptance_criteria",
        "usage_license",
        "revision_limit",
    ]
    assert dialogue_request["commission_request"]["identity_manifest_fields"] == [
        {"child_key": "dialogue", "field": field}
        for field in ("child_key", "license_provenance", "acceptance_checks")
    ]
    assert flag_request["commission_request"] == network._empty_commission_request()
    assert "Flag graph/state machinery is code" in _install_codex_fake.last_prompt


def test_stamped_text_children_carry_provenance_and_row_thin_commission_request(tmp_path):
    repo = _repo(tmp_path)
    _write_parent(repo, "root")
    git_wrapper.commit_files(
        repo,
        "ai-org/patch-series/root",
        {
            "spine/art-bible.json": {"voice": "fixture"},
            "spine/asset-manifest.schema.json": {"type": "object"},
        },
        subject="spine: add commission contracts",
    )

    result = network.stamp_children(
        repo,
        "ai-org/patch-series/root",
        {"id": "dialogue-template", "title": "Dialogue"},
        [
            {
                "child_key": "dialogue_001",
                "summary": (
                    "Write dialogue 001 as text commission rows; cite spine/art-bible.json and "
                    "spine/asset-manifest.schema.json. Include string keys, speaker tags, length limits, "
                    "and flag references in the request text."
                ),
                "acceptance_criteria": ["Dialogue row is reviewable against the shared spine contract."],
                "functional_check": "Review the dialogue row and manifest fields.",
                "ux_acceptance_tests": ["Dialogue text is legible in the target UI surface."],
                "speaker": "mayor",
            },
            {
                "child_key": "dialogue_002",
                "summary": "Write dialogue 002 as a small text commission row.",
            },
        ],
        stamping_author="writer-signature",
    )

    assert result["ok"] is True
    request = _node_json(repo, "sub/dialogue_001", "maintainer-series-request.json")
    manifest = _node_json(repo, "sub/dialogue_001", "patch-series-manifest.json")
    absent_manifest = _node_json(repo, "sub/dialogue_002", "patch-series-manifest.json")
    assert "kind" not in manifest
    assert manifest["stamping_provenance"]["scale_table_row"]["speaker"] == "mayor"
    assert manifest["acceptance_criteria"] == ["Dialogue row is reviewable against the shared spine contract."]
    assert manifest["functional_check"] == "Review the dialogue row and manifest fields."
    assert manifest["ux_acceptance_tests"] == ["Dialogue text is legible in the target UI surface."]
    assert absent_manifest["acceptance_criteria"] == ["ABSENT: acceptance criteria not supplied by stamped request content."]
    assert absent_manifest["functional_check"] == "ABSENT: functional check not supplied by stamped request content."
    assert absent_manifest["ux_acceptance_tests"] == ["ABSENT: UX acceptance tests not supplied by stamped request content."]
    contract_fields = request["commission_request"]["contract_fields"]
    assert contract_fields
    assert {row["child_key"] for row in contract_fields} == {"dialogue_001"}
    assert request["commission_request"]["identity_manifest_fields"] == [
        {"child_key": "dialogue_001", "field": field}
        for field in ("child_key", "license_provenance", "acceptance_checks")
    ]
    assert "asset_commission_" not in json.dumps(request["commission_request"])
    assert {
        item["role"]: item["path"]
        for item in request["commission_request"]["spine_contract_refs"]
    }["art_bible_path"] == "spine/art-bible.json"
    assert request["request"]["tech_stack"] == _patch_series()["tech_stack"]
    assert network.ready_nested_elaboration(repo) == [
        {
            "branch": "ai-org/patch-series/root",
            "node_path": "sub/dialogue_001",
            "address": "ai-org/patch-series/root:sub/dialogue_001",
            "child_key": "dialogue_001",
        },
        {
            "branch": "ai-org/patch-series/root",
            "node_path": "sub/dialogue_002",
            "address": "ai-org/patch-series/root:sub/dialogue_002",
            "child_key": "dialogue_002",
        }
    ]


def test_child_creation_does_not_run_completeness_checkpoint():
    patch_series_view = _patch_series()
    patch_series_view["request_type"] = "asset commission"
    patch_series_view["working_title"] = "Asset Commission"
    files = {
        "sub/assets/maintainer-series-request.json": {
            "id": "0001-assets",
            "submitted_at": "",
            "request": patch_series_view,
            "provenance": {"requester": "parent"},
        },
        "sub/assets/patch-series-manifest.json": {"node_path": "sub/assets"},
        "sub/assets/technical-approach-plan.json": _approach(),
    }

    result = network._validate_nested_file_map(files)

    assert result["ok"] is True
    assert result["errors"] == []


def test_patch_series_pull_selects_ready_nested_coarse_child_after_dependency_resolution(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _write_parent(repo, "root")
    _install_codex_fake(monkeypatch, [_split_all_scope()])
    result = network.refine(repo, "root", horizon=1)
    prep = result["children"][0]["address"]
    battle = result["children"][1]["address"]
    contrib = _write_contrib_branch(repo, prep, "ai-org/patch-series/root")
    git_wrapper.commit_empty(repo, contrib, "acceptance: passed")
    _merge_into_subsystem(repo, contrib)
    calls: list[str] = []

    monkeypatch.setattr(patch_series.network, "elaborate", lambda _repo, address: calls.append(address) or {"status": "ready_for_patch_authoring"})

    assert patch_series.pull(repo)["status"] == "ready_for_patch_authoring"
    assert calls == [battle]


def test_when_parser_evaluator_is_total_and_rejects_malformed_input():
    assert network.evaluate_when('counter == 0 or flag == "open"', {"counter": 4, "flag": "open"}) is True
    assert network.evaluate_when("not (counter < 1)", {"counter": 4}) is True
    warnings: list[dict[str, object]] = []
    assert network.evaluate_when('flag == "open"', {}, warnings=warnings, node="a", edge_index=0) is False
    assert warnings == [{"type": "when_type_warning", "node": "a", "predicate": 'flag == "open"', "edge_index": 0}]
    nested = "counter == 0"
    for _ in range(60):
        nested = f"({nested})"
    assert network.evaluate_when(nested, {"counter": 0}) is True
    adversarial = "counter == 0"
    for _ in range(200):
        adversarial = f"({adversarial})"
    with pytest.raises(network.WhenSyntaxError):
        network.evaluate_when(adversarial, {"counter": 0})
    with pytest.raises(network.WhenSyntaxError):
        network.evaluate_when("counter ==", {"counter": 0})
    bad_escape = "x == '" + "\\x" + "'"
    huge_predicate = "x == 0 " + ("and x == 0 " * 100_000)
    for predicate in ("not " * 5000 + "x", bad_escape, "x == " + "1" * 5000, huge_predicate):
        with pytest.raises(network.WhenSyntaxError):
            network.evaluate_when(predicate, {})


def test_when_parser_rejects_long_logical_chains_at_form_validation(tmp_path):
    predicate = '""' + 'or""' * 997
    assert len(predicate) <= network.MAX_WHEN_LENGTH

    repo = _repo(tmp_path)
    _write_parent(repo, "root")

    result = network.stamp_children(
        repo,
        "ai-org/patch-series/root",
        {"id": "long-chain", "title": "Long Chain"},
        [{"child_key": "long_chain", "summary": "Bad edge.", "edges": [_edge("depends", to="root", reason="bad", when=predicate)]}],
    )

    assert result["ok"] is False
    assert result["status"] == "edge-form-invalid"
    assert "expression exceeds 256 terms" in result["errors"][0]["message"]
    assert not git_wrapper.file_exists(repo, "ai-org/patch-series/root", "sub/long_chain/patch-series-manifest.json")


def test_stamp_children_rejects_undeclared_when_identifier_before_commit(tmp_path):
    repo = _repo(tmp_path)
    git_wrapper.create_branch_with_files(
        repo,
        "ai-org/patch-series/root",
        "main",
        {
            "patch-series-cover-letter.json": _patch_series(),
            "technical-approach-plan.json": _approach(),
            "patch-series-manifest.json": {
                "schema": "patch_series-network-node-v1",
                "child_key": "root",
                "node_path": ".",
                "lifecycle_status": "merged_into_subsystem_tree",
                "edges": [],
                "declared_patchwork_checks": [{"name": "counter", "type": "counter"}],
            },
        },
        commit_message="patch_series: direction-ok",
    )

    result = network.stamp_children(
        repo,
        "ai-org/patch-series/root",
        {"id": "strict", "title": "Strict"},
        [{"child_key": "bad", "summary": "Bad edge.", "edges": [_edge("depends", to="root", reason="bad", when="missing == 0")]}],
    )

    assert result["ok"] is False
    assert result["status"] == "edge-form-invalid"
    assert result["errors"][0]["type"] == "when_undeclared_variable"
    assert not git_wrapper.file_exists(repo, "ai-org/patch-series/root", "sub/bad/patch-series-manifest.json")


def test_refine_does_not_run_a_separate_when_split_gate(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    approach = _approach()
    approach["declared_patchwork_checks"] = [{"name": "counter", "type": "counter"}]
    git_wrapper.create_branch_with_files(
        repo,
        "ai-org/patch-series/root",
        "main",
        {"patch-series-cover-letter.json": _patch_series(), "technical-approach-plan.json": approach},
        commit_message="patch_series: direction-ok",
    )
    split = _split_all_scope()
    split["children"][1]["edges"] = [_edge("depends", to="prep", reason="bad", when="missing == 0")]
    _install_codex_fake(monkeypatch, [split, split])

    result = network.refine(repo, "root", horizon=1)

    assert result["ok"] is True
    assert result["status"] == "refined"
    assert _install_codex_fake.calls == 1
    assert git_wrapper.file_exists(repo, "ai-org/patch-series/root", "sub/prep/patch-series-manifest.cue")


def test_when_evaluator_handles_direct_long_logical_ast_without_recursion_error():
    ast = ("literal", "")
    for _ in range(997):
        ast = ("or", ast, ("literal", ""))

    assert network._eval_when_ast(ast, {}) is False


def test_when_parser_generated_long_chains_are_bounded():
    for operator in ("or", "and"):
        accepted = f" {operator} ".join(["1"] * network.MAX_WHEN_TERMS)
        rejected = f" {operator} ".join(["1"] * (network.MAX_WHEN_TERMS + 1))

        assert network.evaluate_when(accepted, {}) is True
        with pytest.raises(network.WhenSyntaxError, match="expression exceeds 256 terms"):
            network.evaluate_when(rejected, {})


def test_malformed_when_edges_are_gate_errors_not_exceptions(tmp_path):
    root = tmp_path / "network"
    predicates = {
        "not_chain": "not " * 5000 + "x",
        "bad_escape": "x == '" + "\\x" + "'",
        "huge_int": "x == " + "1" * 5000,
        "huge_predicate": "x == 0 " + ("and x == 0 " * 100_000),
    }
    _write_network(
        root,
        {
            key: {
                "child_key": key,
                "node_path": f"sub/{key}",
                "lifecycle_status": "posted_to_mailing_list",
                "edges": [_edge("depends", to="root", reason="bad when", when=predicate)],
            }
            for key, predicate in predicates.items()
        },
    )

    report = network.validate_lineage_gate(root)

    assert report["ok"] is False
    malformed = [error for error in report["errors"] if error["type"] == "edge_schema" and "edge when is malformed" in error["message"]]
    assert {error["node"] for error in malformed} == set(predicates)


def test_gate_requires_registry_when_any_when_predicate_is_present(tmp_path):
    root = tmp_path / "network"
    _write_network(
        root,
        {
            "a": {
                "child_key": "a",
                "node_path": "sub/a",
                "lifecycle_status": "posted_to_mailing_list",
                "edges": [_edge("depends", to="root", reason="needs gate", when="counter == 0")],
            }
        },
    )

    report = network.validate_lineage_gate(root)

    assert report["ok"] is False
    assert any(error["type"] == "declared_patchwork_checks_missing" and error["node"] == "a" for error in report["errors"])


def test_gate_reports_static_when_type_errors_without_crashing(tmp_path):
    root = tmp_path / "network"
    _write_network(
        root,
        {
            "a": {
                "child_key": "a",
                "node_path": "sub/a",
                "lifecycle_status": "posted_to_mailing_list",
                "edges": [_edge("depends", to="root", reason="type mismatch", when='flag == "open"')],
            }
        },
        registry=[{"name": "flag", "type": "counter"}],
    )

    report = network.validate_lineage_gate(root)

    assert report["ok"] is False
    assert any(error["type"] == "when_type_error" and error["node"] == "a" for error in report["errors"])


def test_declared_unset_patchwork_checks_use_initial_values():
    graph = {
        "declared_patchwork_checks": {"counter": "counter", "flag": "text"},
        "nodes": {
            "root": {"child_key": "root", "lifecycle_status": "merged_into_subsystem_tree", "edges": []},
            "counter_waiter": {
                "child_key": "counter_waiter",
                "lifecycle_status": "posted_to_mailing_list",
                "edges": [_edge("depends", to="root", when="counter == 0", reason="default counter")],
            },
            "text_waiter": {
                "child_key": "text_waiter",
                "lifecycle_status": "posted_to_mailing_list",
                "edges": [_edge("depends", to="root", when='flag == ""', reason="default text")],
            },
        },
    }

    assert network.eligible("counter_waiter", {}, graph) is True
    assert network.eligible("text_waiter", {}, graph) is True


def test_git_gate_reports_when_errors_and_readiness_fails_closed(tmp_path):
    repo = _repo(tmp_path)
    git_wrapper.create_branch_with_files(
        repo,
        "ai-org/patch-series/static-when",
        "main",
        {
            "patch-series-manifest.json": {
                "schema": "patch_series-network-node-v1",
                "child_key": "root",
                "node_path": ".",
                "lifecycle_status": "merged_into_subsystem_tree",
                "edges": [],
                "declared_patchwork_checks": [{"name": "counter", "type": "counter"}],
            },
            "patch-queue-status-rollup.json": {
                "schema": "patch-queue-status-rollup-v1",
                "generated": True,
                "generated_from": "patch-series-manifest.json",
                "children": [{"child_key": "a", "node_path": "sub/a", "lifecycle_status": "posted_to_mailing_list", "edges": []}],
            },
            "sub/a/patch-series-manifest.json": {
                "schema": "patch_series-network-node-v1",
                "child_key": "a",
                "node_path": "sub/a",
                "lifecycle_status": "posted_to_mailing_list",
                "edges": [_edge("depends", to="root", when="missing == 0", reason="bad committed data")],
            },
            "sub/a/maintainer-series-request.json": {"id": "a", "submitted_at": "", "request": {"raw_request": "Request for a.", "working_title": "a"}},
        },
        commit_message="network: bad committed predicate",
    )

    report = network.validate_lineage_gate_from_git(repo, "ai-org/patch-series/static-when")

    assert report["ok"] is False
    assert any(error["type"] == "when_undeclared_variable" and error["node"] == "a" for error in report["errors"])
    assert network.coarse_ready(repo, "ai-org/patch-series/static-when:sub/a") is False


def test_eligible_supports_or_groups_state_targets_and_rearming():
    graph = {
        "nodes": {
            "api": {"child_key": "api", "lifecycle_status": "posted_to_mailing_list", "declared_milestones": ["interface_frozen"]},
            "legacy": {"child_key": "legacy", "lifecycle_status": "merged_into_subsystem_tree"},
            "consumer": {
                "child_key": "consumer",
                "lifecycle_status": "posted_to_mailing_list",
                "edges": [
                    _edge("depends", to="api", state="interface_frozen", or_group="api_choice", reason="can use new api"),
                    _edge("depends", to="legacy", state="merged_into_subsystem_tree", or_group="api_choice", reason="can use old api"),
                ],
            },
            "rearming": {
                "child_key": "rearming",
                "lifecycle_status": "posted_to_mailing_list",
                "edges": [_edge("depends", to="api", state="merged_into_subsystem_tree", when="counter > 0", reason="only after work starts")],
            },
        }
    }

    assert network.eligible("consumer", {}, graph) is True
    assert network.eligible("rearming", {"counter": 0}, graph) is True
    assert network.eligible("rearming", {"counter": 1}, graph) is False
    graph["nodes"]["api"]["lifecycle_status"] = "merged_into_subsystem_tree"
    assert network.eligible("rearming", {"counter": 1}, graph) is True
    assert network.readiness_rollup(graph, {"counter": 1})["rearming"] is True
    assert network.topological_order(graph, {"counter": 1}).index("api") < network.topological_order(graph, {"counter": 1}).index("rearming")
    assert network.critical_path(graph, {"counter": 1})[-1] in {"consumer", "rearming"}


def test_patchwork_check_event_projection_and_gate_validation(tmp_path):
    root = tmp_path / "network"
    _write_network(
        root,
        {
            "a": {"child_key": "a", "node_path": "sub/a", "lifecycle_status": "claimed_by_patch_author", "edges": [_edge("excludes", with_="b", reason="alternative")]},
            "b": {"child_key": "b", "node_path": "sub/b", "lifecycle_status": "claimed_by_patch_author", "edges": []},
            "c": {"child_key": "c", "node_path": "sub/c", "lifecycle_status": "posted_to_mailing_list", "edges": [_edge("depends", to="missing", reason="bad target")]},
        },
        registry=[{"name": "counter", "type": "counter"}, {"name": "flag", "type": "text"}],
    )
    (root / "patchwork-check-events.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"var": "counter", "op": "inc", "value": 3, "actor": "test", "timestamp": "commit:1"}),
                json.dumps({"var": "counter", "op": "dec", "value": 1, "actor": "test", "timestamp": "commit:2"}),
                json.dumps({"var": "flag", "op": "set", "value": "open", "actor": "test", "timestamp": "commit:3"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    assert network.project_patchwork_check_events(
        [
            {"var": "counter", "op": "inc", "value": 3},
            {"var": "counter", "op": "dec", "value": 1},
            {"var": "flag", "op": "set", "value": "open"},
        ],
        {"counter": "counter", "flag": "text"},
    ) == {"counter": 2, "flag": "open"}
    report = network.validate_lineage_gate(root)
    error_types = {error["type"] for error in report["errors"]}
    assert {"target_missing", "exclusion_asymmetry", "exclusion_conflict"} <= error_types


def test_computed_patchwork_check_facts_match_filesystem_and_git_projections(tmp_path):
    root = tmp_path / "network"
    registry = [
        {"name": "counter", "type": "counter"},
        {"name": "api_interface_frozen", "type": "text"},
        {"name": "flag", "type": "text"},
    ]
    _write_network(
        root,
        {
            "api": {
                "child_key": "api",
                "node_path": "sub/api",
                "lifecycle_status": "posted_to_mailing_list",
                "declared_milestones": ["interface_frozen"],
                "edges": [],
            },
            "consumer": {
                "child_key": "consumer",
                "node_path": "sub/consumer",
                "lifecycle_status": "ready_for_patch_authoring",
                "edges": [
                    _edge("depends", to="api", state="interface_frozen", reason="needs API", when="counter >= 2"),
                    _edge("depends", to="api", state="merged_into_subsystem_tree", reason="later only", when='flag == "open"'),
                ],
            },
        },
        registry=registry,
    )
    (root / "patchwork-check-events.jsonl").write_text(
        json.dumps({"var": "counter", "op": "inc", "value": 2}) + "\n",
        encoding="utf-8",
    )
    (root / "sub" / "api" / "patchwork-check-events.jsonl").write_text(
        json.dumps({"var": "api_interface_frozen", "op": "set", "value": "done"}) + "\n",
        encoding="utf-8",
    )

    expected = {
        "checks": {
            "api_interface_frozen": {"declared_type": "text", "current_value": "done", "last_event": {"path": "sub/api/patchwork-check-events.jsonl", "line": 1}},
            "counter": {"declared_type": "counter", "current_value": 2, "last_event": {"path": "patchwork-check-events.jsonl", "line": 1}},
            "flag": {"declared_type": "text", "current_value": "", "last_event": None},
        },
        "lifecycle_status": "ready_for_patch_authoring",
        "blocking_edges": [],
        "satisfied_edges": [
            {"to": "api", "state": "interface_frozen", "reason": "needs API", "when": "counter >= 2", "when_currently": True},
            {"to": "api", "state": "merged_into_subsystem_tree", "reason": "later only", "when": 'flag == "open"', "when_currently": False},
        ],
    }
    assert network.computed_patchwork_check_facts_from_path(root, "consumer") == expected

    repo = _repo(tmp_path)
    git_wrapper.create_branch_with_files(
        repo,
        "ai-org/patch-series/facts",
        "main",
        {
            path.relative_to(root).as_posix(): path.read_text(encoding="utf-8")
            for path in root.rglob("*")
            if path.is_file()
        },
        commit_message="network: facts fixture",
    )
    assert network.computed_patchwork_check_facts(repo, "ai-org/patch-series/facts", "sub/consumer") == expected


def test_computed_patchwork_check_facts_preserve_filesystem_git_parity_for_missing_manifest_fields_and_merged_contrib(tmp_path):
    root = tmp_path / "network"
    (root / "sub" / "api").mkdir(parents=True)
    (root / "patch-series-manifest.json").write_text(
        json.dumps(
            {
                "schema": "patch_series-network-node-v1",
                "lifecycle_status": "merged_into_subsystem_tree",
                "edges": [],
                "declared_patchwork_checks": [{"name": "counter", "type": "counter"}],
            }
        ),
        encoding="utf-8",
    )
    (root / "sub" / "api" / "patch-series-manifest.json").write_text(
        json.dumps(
            {
                "schema": "patch_series-network-node-v1",
                "lifecycle_status": "submitted_for_maintainer_review",
                "contrib_branch": "ai-org/contrib/facts-api",
                "edges": [],
            }
        ),
        encoding="utf-8",
    )
    (root / "patchwork-check-events.jsonl").write_text(
        json.dumps({"var": "counter", "op": "inc", "value": 1}) + "\n",
        encoding="utf-8",
    )

    expected = {
        "checks": {
            "counter": {"declared_type": "counter", "current_value": 1, "last_event": {"path": "patchwork-check-events.jsonl", "line": 1}},
        },
        "lifecycle_status": "merged_into_subsystem_tree",
        "blocking_edges": [],
        "satisfied_edges": [],
    }
    assert network.computed_patchwork_check_facts_from_path(root, "sub/api") == expected

    repo = _repo(tmp_path)
    git_wrapper.create_branch_with_files(
        repo,
        "ai-org/patch-series/facts-parity",
        "main",
        {
            path.relative_to(root).as_posix(): path.read_text(encoding="utf-8")
            for path in root.rglob("*")
            if path.is_file()
        },
        commit_message="network: facts parity fixture",
    )
    git_wrapper.create_branch_with_files(
        repo,
        "ai-org/contrib/facts-api",
        "ai-org/patch-series/facts-parity",
        {"accepted.txt": "ok\n"},
        commit_message="acceptance: reachable",
    )
    _git(repo, "checkout", "ai-org/patch-series/facts-parity")
    _git(repo, "merge", "--no-ff", "--no-edit", "ai-org/contrib/facts-api")
    _git(repo, "checkout", "main")

    unmerged_expected = {**expected, "lifecycle_status": "submitted_for_maintainer_review"}
    assert network.computed_patchwork_check_facts(repo, "ai-org/patch-series/facts-parity", "sub/api") == unmerged_expected

    _merge_into_subsystem(repo, "ai-org/contrib/facts-api")

    assert network.computed_patchwork_check_facts(repo, "ai-org/patch-series/facts-parity", "sub/api") == expected


def test_computed_patchwork_check_facts_return_typed_gate_errors_for_ineligible_tree(tmp_path):
    root = tmp_path / "network"
    _write_network(
        root,
        {
            "consumer": {
                "child_key": "consumer",
                "node_path": "sub/consumer",
                "lifecycle_status": "ready_for_patch_authoring",
                "edges": [_edge("depends", to="missing", reason="bad target")],
            },
        },
        registry=[{"name": "counter", "type": "counter"}],
    )

    facts = network.computed_patchwork_check_facts_from_path(root, "consumer")

    assert facts["ok"] is False
    assert facts["type"] == "gate_errors_present"
    assert any(error["type"] == "target_missing" for error in facts["errors"])


def test_malformed_patchwork_check_events_are_gate_errors_on_filesystem_and_git_paths(tmp_path):
    root = tmp_path / "network"
    state_registry = [{"name": "flag", "type": "text"}]
    _write_network(
        root,
        {"a": {"child_key": "a", "node_path": "sub/a", "lifecycle_status": "posted_to_mailing_list", "edges": []}},
        registry=state_registry,
    )
    (root / "sub" / "a" / "patchwork-check-events.jsonl").write_text(
        "\n".join(
            [
                "{not-json",
                json.dumps({"var": "flag", "op": "set", "value": "open"}),
                json.dumps({"var": "flag", "op": "inc", "value": 1}),
                json.dumps({"var": "other", "op": "bad"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    report = network.validate_lineage_gate(root)

    ingestion_invalid = [error for error in report["errors"] if error["type"] == "ingestion_invalid"]
    assert [(error["node"], error["path"], error["line"]) for error in ingestion_invalid] == [("a", "sub/a/patchwork-check-events.jsonl", 1)]
    invalid = [error for error in report["errors"] if error["type"] == "patchwork_check_event_invalid"]
    assert [(error["node"], error["line"]) for error in invalid] == [("a", 3), ("a", 4)]

    repo = _repo(tmp_path)
    git_wrapper.create_branch_with_files(
        repo,
        "ai-org/patch-series/patchwork-check-events",
        "main",
        {
            "patch-series-manifest.json": {
                "schema": "patch_series-network-node-v1",
                "child_key": "root",
                "node_path": ".",
                "lifecycle_status": "merged_into_subsystem_tree",
                "edges": [],
                "declared_patchwork_checks": state_registry,
            },
            "sub/a/patch-series-manifest.json": {"schema": "patch_series-network-node-v1", "child_key": "a", "node_path": "sub/a", "lifecycle_status": "posted_to_mailing_list", "edges": []},
            "sub/a/patchwork-check-events.jsonl": (root / "sub" / "a" / "patchwork-check-events.jsonl").read_text(encoding="utf-8"),
        },
        commit_message="network: state event fixture",
    )

    git_report = network.validate_lineage_gate_from_git(repo, "ai-org/patch-series/patchwork-check-events")
    git_ingestion_invalid = [error for error in git_report["errors"] if error["type"] == "ingestion_invalid"]
    assert [(error["node"], error["path"], error["line"]) for error in git_ingestion_invalid] == [("a", "sub/a/patchwork-check-events.jsonl", 1)]
    git_invalid = [error for error in git_report["errors"] if error["type"] == "patchwork_check_event_invalid"]
    assert [(error["node"], error["line"]) for error in git_invalid] == [("a", 3), ("a", 4)]


def test_bool_patchwork_check_event_values_are_rejected(tmp_path):
    root = tmp_path / "network"
    _write_network(
        root,
        {"a": {"child_key": "a", "node_path": "sub/a", "lifecycle_status": "posted_to_mailing_list", "edges": []}},
        registry=[{"name": "flag", "type": "text"}, {"name": "counter", "type": "counter"}],
    )
    (root / "sub" / "a" / "patchwork-check-events.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"var": "flag", "op": "set", "value": True}),
                json.dumps({"var": "counter", "op": "inc", "value": False}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    report = network.validate_lineage_gate(root)

    invalid = [error for error in report["errors"] if error["type"] == "patchwork_check_event_invalid"]
    assert [(error["node"], error["line"]) for error in invalid] == [("a", 1), ("a", 2)]


def test_patchwork_check_events_are_validated_against_registry(tmp_path):
    root = tmp_path / "network"
    _write_network(
        root,
        {"a": {"child_key": "a", "node_path": "sub/a", "lifecycle_status": "posted_to_mailing_list", "edges": []}},
        registry=[{"name": "counter", "type": "counter"}, {"name": "flag", "type": "text"}],
    )
    (root / "sub" / "a" / "patchwork-check-events.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"var": "missing", "op": "set", "value": 1}),
                json.dumps({"var": "flag", "op": "inc", "value": 1}),
                json.dumps({"var": "counter", "op": "set", "value": "wrong"}),
                json.dumps({"var": "flag", "op": "set", "value": 1}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    report = network.validate_lineage_gate(root)

    invalid = [error for error in report["errors"] if error["type"] == "patchwork_check_event_invalid"]
    assert [(error["node"], error["line"]) for error in invalid] == [("a", 1), ("a", 2), ("a", 3), ("a", 4)]
    messages = " ".join(error["message"] for error in invalid)
    assert "undeclared variable missing" in messages
    assert "non-counter var flag" in messages
    assert "counter counter must be integer" in messages
    assert "text flag must be string" in messages


def test_git_path_patchwork_check_events_unblock_declared_milestone_dependency(tmp_path):
    repo = _repo(tmp_path)
    git_wrapper.create_branch_with_files(
        repo,
        "ai-org/patch-series/stateful",
        "main",
        {
            "patch-series-manifest.json": {
                "schema": "patch_series-network-node-v1",
                "child_key": "root",
                "node_path": ".",
                "lifecycle_status": "merged_into_subsystem_tree",
                "edges": [],
                "declared_patchwork_checks": [{"name": "api_interface_frozen", "type": "text"}],
            },
            "patch-queue-status-rollup.json": {
                "schema": "patch-queue-status-rollup-v1",
                "generated": True,
                "generated_from": "patch-series-manifest.json",
                "children": [
                    {"child_key": "api", "node_path": "sub/api", "lifecycle_status": "posted_to_mailing_list", "edges": []},
                    {"child_key": "consumer", "node_path": "sub/consumer", "lifecycle_status": "posted_to_mailing_list", "edges": []},
                ],
            },
            "sub/api/patch-series-manifest.json": {
                "schema": "patch_series-network-node-v1",
                "child_key": "api",
                "node_path": "sub/api",
                "lifecycle_status": "posted_to_mailing_list",
                "declared_milestones": ["interface_frozen"],
                "edges": [],
            },
            "sub/api/patchwork-check-events.jsonl": json.dumps({"var": "api_interface_frozen", "op": "set", "value": "done"}) + "\n",
            "sub/consumer/patch-series-manifest.json": {
                "schema": "patch_series-network-node-v1",
                "child_key": "consumer",
                "node_path": "sub/consumer",
                "lifecycle_status": "posted_to_mailing_list",
                "edges": [_edge("depends", to="api", state="interface_frozen", reason="needs frozen API")],
            },
        },
        commit_message="network: stateful fixture",
    )

    assert network.coarse_ready(repo, "ai-org/patch-series/stateful:sub/consumer") is True
def test_instant_cycle_detection_and_citation_drift_lint(tmp_path):
    root = tmp_path / "network"
    _write_network(
        root,
        {
            "alpha": {"child_key": "alpha", "node_path": "sub/alpha", "lifecycle_status": "posted_to_mailing_list", "edges": [_edge("depends", to="beta", reason="cycle")]},
            "beta": {"child_key": "beta", "node_path": "sub/beta", "lifecycle_status": "posted_to_mailing_list", "edges": [_edge("depends", to="alpha", reason="cycle")]},
            "gamma": {"child_key": "gamma", "node_path": "sub/gamma", "lifecycle_status": "posted_to_mailing_list", "edges": [_edge("depends", to="alpha", reason="over declared")]},
        },
        requests={
            "gamma": "Mentions beta and cites spine/art-bible.json.",
        },
    )

    report = network.validate_lineage_gate(root)
    assert report["well_founded"] is False
    assert any(error["type"] == "instant_cycle" for error in report["errors"])
    assert any(error["type"] == "citation_missing" and error["path"] == "spine/art-bible.json" for error in report["errors"])
    warning_types = {warning["type"] for warning in report["warnings"]}
    assert {"drift_missing_edge", "drift_over_declared_edge"} <= warning_types


def test_git_gate_matches_filesystem_gate_for_citations_and_drift(tmp_path):
    root = tmp_path / "network"
    manifests = {
        "alpha": {"child_key": "alpha", "node_path": "sub/alpha", "lifecycle_status": "posted_to_mailing_list", "edges": []},
        "beta": {"child_key": "beta", "node_path": "sub/beta", "lifecycle_status": "posted_to_mailing_list", "edges": []},
        "gamma": {
            "child_key": "gamma",
            "node_path": "sub/gamma",
            "lifecycle_status": "posted_to_mailing_list",
            "edges": [_edge("depends", to="alpha", reason="declared but absent from request")],
        },
    }
    requests = {"gamma": "Mentions beta and cites spine/art-bible.json."}
    _write_network(root, manifests, requests=requests)

    fs_report = network.validate_lineage_gate(root)

    files: dict[str, object] = {
        "patch-series-manifest.json": {"schema": "patch_series-network-node-v1", "child_key": "root", "node_path": ".", "lifecycle_status": "merged_into_subsystem_tree", "edges": []}
    }
    for key, manifest in manifests.items():
        files[f"sub/{key}/patch-series-manifest.json"] = manifest
        request_text = requests.get(key, f"Request for {key}.")
        files[f"sub/{key}/maintainer-series-request.json"] = {"id": key, "submitted_at": "", "request": {"raw_request": request_text, "working_title": key}}
    repo = _repo(tmp_path)
    git_wrapper.create_branch_with_files(repo, "ai-org/patch-series/git-parity", "main", files, commit_message="network: git parity fixture")

    git_report = network.validate_lineage_gate_from_git(repo, "ai-org/patch-series/git-parity")

    fs_citations = sorted((error["node"], error["path"]) for error in fs_report["errors"] if error["type"] == "citation_missing")
    git_citations = sorted((error["node"], error["path"]) for error in git_report["errors"] if error["type"] == "citation_missing")
    assert git_citations == fs_citations == [("gamma", "spine/art-bible.json")]
    fs_drift = sorted((warning["type"], warning["node"], tuple(warning.get("targets", []))) for warning in fs_report["warnings"] if warning["type"].startswith("drift_"))
    git_drift = sorted((warning["type"], warning["node"], tuple(warning.get("targets", []))) for warning in git_report["warnings"] if warning["type"].startswith("drift_"))
    assert git_drift == fs_drift == [
        ("drift_missing_edge", "gamma", ("beta",)),
        ("drift_over_declared_edge", "gamma", ("alpha",)),
    ]


def test_git_loader_preserves_stored_merged_lifecycle_and_warns_without_evidence(tmp_path):
    root = tmp_path / "network"
    manifest = {
        "child_key": "api",
        "node_path": "sub/api",
        "lifecycle_status": "merged_into_subsystem_tree",
        "contrib_branch": "ai-org/contrib/missing-api",
        "edges": [],
    }
    _write_network(root, {"api": manifest})

    repo = _repo(tmp_path)
    git_wrapper.create_branch_with_files(
        repo,
        "ai-org/patch-series/git-lifecycle",
        "main",
        {
            "patch-series-manifest.json": {"schema": "patch_series-network-node-v1", "child_key": "root", "node_path": ".", "lifecycle_status": "merged_into_subsystem_tree", "edges": []},
            "sub/api/patch-series-manifest.json": manifest,
            "sub/api/maintainer-series-request.json": {"id": "api", "submitted_at": "", "request": {"raw_request": "Request for api.", "working_title": "api"}},
        },
        commit_message="network: lifecycle parity fixture",
    )

    fs_graph = network._load_manifest_network_from_path(root)
    git_graph = network._load_manifest_network_from_git(repo, "ai-org/patch-series/git-lifecycle")
    git_report = network.validate_lineage_gate_from_git(repo, "ai-org/patch-series/git-lifecycle")

    assert fs_graph["nodes"]["api"]["lifecycle_status"] == "merged_into_subsystem_tree"
    assert git_graph["nodes"]["api"]["lifecycle_status"] == "merged_into_subsystem_tree"
    assert any(warning["type"] == "lifecycle_merge_evidence_missing" and warning["node"] == "api" for warning in git_report["warnings"])


def test_ingestion_chokepoint_rejects_seeded_manifest_byte_fuzz(tmp_path):
    valid = json.dumps(
        {
            "schema": "patch_series-network-node-v1",
            "child_key": "root",
            "node_path": ".",
            "lifecycle_status": "merged_into_subsystem_tree",
            "edges": [],
        },
        sort_keys=True,
    ).encode("utf-8")
    rng = random.Random(1337)
    for index in range(500):
        payload = bytearray(valid)
        for _ in range(rng.randint(1, 6)):
            if payload:
                payload[rng.randrange(len(payload))] = rng.randrange(256)
            if rng.random() < 0.4:
                payload.insert(rng.randrange(len(payload) + 1), rng.randrange(256))
        payload.insert(rng.randrange(len(payload) + 1), 0xFF)
        path = tmp_path / f"fuzz-{index}.json"
        path.write_bytes(bytes(payload))

        value, error = network._ingest_json_from_file(path, node="root", report_path="patch-series-manifest.json")

        assert value is None
        assert error and error["type"] == "ingestion_invalid"
        assert error["path"] == "patch-series-manifest.json"


def test_ingestion_invalid_inputs_become_gate_errors_on_filesystem(tmp_path):
    giant = "9" * 5000

    giant_root = tmp_path / "giant"
    giant_root.mkdir()
    (giant_root / "patch-series-manifest.json").write_text(
        '{"schema":"patch_series-network-node-v1","child_key":"root","node_path":".","lifecycle_status":"merged_into_subsystem_tree","serial_id":'
        + giant
        + ',"edges":[]}',
        encoding="utf-8",
    )
    giant_report = network.validate_lineage_gate(giant_root)
    assert any(error["type"] == "ingestion_invalid" and error["path"] == "patch-series-manifest.json" for error in giant_report["errors"])

    oversized = tmp_path / "oversized"
    oversized.mkdir()
    (oversized / "patch-series-manifest.json").write_bytes(b" " * (network.MAX_INGESTION_BYTES + 1))
    oversized_report = network.validate_lineage_gate(oversized)
    assert any(error["type"] == "ingestion_invalid" and "ingestion cap" in error["message"] for error in oversized_report["errors"])

    non_utf = tmp_path / "non-utf"
    _write_network(non_utf, {"a": {"child_key": "a", "node_path": "sub/a", "lifecycle_status": "posted_to_mailing_list", "edges": []}})
    (non_utf / "sub" / "a" / "maintainer-series-request.json").write_bytes(b"\xff\xfe")
    non_utf_report = network.validate_lineage_gate(non_utf)
    assert any(error["type"] == "ingestion_invalid" and error["path"] == "sub/a/maintainer-series-request.json" for error in non_utf_report["errors"])

    deep = tmp_path / "deep"
    _write_network(deep, {"a": {"child_key": "a", "node_path": "sub/a", "lifecycle_status": "posted_to_mailing_list", "edges": []}})
    (deep / "sub" / "a" / "maintainer-series-request.json").write_text("[" * 70 + "0" + "]" * 70, encoding="utf-8")
    deep_report = network.validate_lineage_gate(deep)
    assert any(error["type"] == "ingestion_invalid" and "depth" in error["message"] for error in deep_report["errors"])

    directory_event = tmp_path / "directory-event"
    _write_network(directory_event, {"a": {"child_key": "a", "node_path": "sub/a", "lifecycle_status": "posted_to_mailing_list", "edges": []}})
    (directory_event / "sub" / "a" / "patchwork-check-events.jsonl").mkdir()
    directory_report = network.validate_lineage_gate(directory_event)
    assert any(error["type"] == "ingestion_invalid" and error["path"] == "sub/a/patchwork-check-events.jsonl" for error in directory_report["errors"])

    giant_state = tmp_path / "giant-state"
    _write_network(giant_state, {"a": {"child_key": "a", "node_path": "sub/a", "lifecycle_status": "posted_to_mailing_list", "edges": []}})
    (giant_state / "sub" / "a" / "patchwork-check-events.jsonl").write_text(
        '{"var":"counter","op":"set","value":' + giant + "}\n",
        encoding="utf-8",
    )
    giant_state_report = network.validate_lineage_gate(giant_state)
    assert any(error["type"] == "ingestion_invalid" and error["path"] == "sub/a/patchwork-check-events.jsonl" for error in giant_state_report["errors"])


def test_git_ingestion_errors_fail_readiness_and_do_not_escape_patch_series_pull(tmp_path):
    repo = _repo(tmp_path)
    giant = "9" * 5000
    git_wrapper.create_branch_with_files(
        repo,
        "ai-org/patch-series/bad-ingestion",
        "main",
        {
            "patch-series-manifest.json": {"schema": "patch_series-network-node-v1", "node_path": ".", "lifecycle_status": "merged_into_subsystem_tree", "edges": []},
            "patch-queue-status-rollup.json": {
                "schema": "patch-queue-status-rollup-v1",
                "generated": True,
                "generated_from": "patch-series-manifest.json",
                "children": [{"child_key": "a", "node_path": "sub/a", "lifecycle_status": "posted_to_mailing_list", "edges": []}],
            },
            "sub/a/patch-series-manifest.json": {"schema": "patch_series-network-node-v1", "child_key": "a", "node_path": "sub/a", "lifecycle_status": "posted_to_mailing_list", "edges": []},
        },
        commit_message="patch_series: direction-ok",
    )
    _commit_raw_file(repo, "ai-org/patch-series/bad-ingestion", "sub/a/patch-series-manifest.json", b"\xff")

    report = network.validate_lineage_gate_from_git(repo, "ai-org/patch-series/bad-ingestion")

    assert any(error["type"] == "ingestion_invalid" and error["path"] == "sub/a/patch-series-manifest.json" for error in report["errors"])
    assert network.coarse_ready(repo, "ai-org/patch-series/bad-ingestion:sub/a") is False
    assert network.ready_nested_elaboration(repo) == []
    assert patch_series.pull(repo) is None

    git_wrapper.create_branch_with_files(
        repo,
        "ai-org/patch-series/bad-int",
        "main",
        {
            "patch-series-manifest.json": {"schema": "patch_series-network-node-v1", "child_key": "root", "node_path": ".", "lifecycle_status": "merged_into_subsystem_tree", "edges": []},
        },
        commit_message="patch_series: direction-ok",
    )
    _commit_raw_file(
        repo,
        "ai-org/patch-series/bad-int",
        "patch-series-manifest.json",
        (
            '{"schema":"patch_series-network-node-v1","child_key":"root","node_path":".",'
            '"lifecycle_status":"merged_into_subsystem_tree","serial_id":' + giant + ',"edges":[]}'
        ).encode("utf-8"),
    )

    int_report = network.validate_lineage_gate_from_git(repo, "ai-org/patch-series/bad-int")
    assert any(error["type"] == "ingestion_invalid" and error["path"] == "patch-series-manifest.json" for error in int_report["errors"])


def test_citation_resolution_requires_regular_files_and_rejects_parent_refs_on_both_paths(tmp_path):
    root = tmp_path / "network"
    _write_network(
        root,
        {"a": {"child_key": "a", "node_path": "sub/a", "lifecycle_status": "posted_to_mailing_list", "edges": []}},
        requests={"a": "Cites docs/adr and docs/../secret.txt."},
    )
    (root / "docs" / "adr").mkdir(parents=True)
    (root / "secret.txt").write_text("outside probe\n", encoding="utf-8")

    fs_report = network.validate_lineage_gate(root)

    repo = _repo(tmp_path)
    git_wrapper.create_branch_with_files(
        repo,
        "ai-org/patch-series/citation-parity",
        "main",
        {
            "patch-series-manifest.json": {"schema": "patch_series-network-node-v1", "child_key": "root", "node_path": ".", "lifecycle_status": "merged_into_subsystem_tree", "edges": []},
            "sub/a/patch-series-manifest.json": {"schema": "patch_series-network-node-v1", "child_key": "a", "node_path": "sub/a", "lifecycle_status": "posted_to_mailing_list", "edges": []},
            "sub/a/maintainer-series-request.json": {"id": "a", "submitted_at": "", "request": {"raw_request": "Cites docs/adr and docs/../secret.txt.", "working_title": "a"}},
            "docs/adr/index.md": "adr\n",
            "secret.txt": "outside probe\n",
        },
        commit_message="network: citation parity",
    )
    git_report = network.validate_lineage_gate_from_git(repo, "ai-org/patch-series/citation-parity")

    def citation_errors(report):
        return sorted((error["type"], error["node"], error["path"]) for error in report["errors"] if error["type"].startswith("citation_"))

    assert citation_errors(fs_report) == citation_errors(git_report) == [
        ("citation_invalid", "a", "docs/../secret.txt"),
        ("citation_missing", "a", "docs/adr"),
    ]


def test_root_manifest_without_child_key_uses_root_on_filesystem_and_git(tmp_path):
    root = tmp_path / "network"
    _write_network(root, {"a": {"child_key": "a", "node_path": "sub/a", "lifecycle_status": "posted_to_mailing_list", "edges": []}})
    root_manifest = json.loads((root / "patch-series-manifest.json").read_text(encoding="utf-8"))
    root_manifest.pop("child_key")
    (root / "patch-series-manifest.json").write_text(json.dumps(root_manifest), encoding="utf-8")

    fs_report = network.validate_lineage_gate(root)

    repo = _repo(tmp_path)
    git_wrapper.create_branch_with_files(
        repo,
        "ai-org/patch-series/root-key",
        "main",
        {
            "patch-series-manifest.json": root_manifest,
            "sub/a/patch-series-manifest.json": {"schema": "patch_series-network-node-v1", "child_key": "a", "node_path": "sub/a", "lifecycle_status": "posted_to_mailing_list", "edges": []},
            "sub/a/maintainer-series-request.json": {"id": "a", "submitted_at": "", "request": {"raw_request": "Request for a.", "working_title": "a"}},
        },
        commit_message="network: root key parity",
    )
    git_report = network.validate_lineage_gate_from_git(repo, "ai-org/patch-series/root-key")

    assert "" not in fs_report["nodes"]
    assert "" not in git_report["nodes"]
    assert "root" in fs_report["nodes"]
    assert "root" in git_report["nodes"]
    assert not any("" in warning.get("targets", []) for warning in fs_report["warnings"] + git_report["warnings"])


def test_child_key_charset_is_validated_before_mention_scanning(tmp_path):
    split = _single_child_split()
    assert isinstance(split["children"][0], dict)
    split["children"][0]["child_key"] = "a.b"

    assert network._valid_child_key("a.b") is False

    root = tmp_path / "network"
    _write_network(
        root,
        {
            "a": {"child_key": "a.b", "node_path": "sub/a", "lifecycle_status": "posted_to_mailing_list", "edges": []},
            "b": {"child_key": "b", "node_path": "sub/b", "lifecycle_status": "posted_to_mailing_list", "edges": []},
        },
        requests={"a": "Mentions a.b."},
    )

    report = network.validate_lineage_gate(root)

    assert any(error["type"] == "child_key_invalid" and error["child_key"] == "a.b" for error in report["errors"])


def test_split_has_no_model_output_parser():
    assert not hasattr(network, "_parse_split")


def test_lineage_walkers_handle_deep_structures_without_recursion_error():
    root: dict[str, object] = {}
    current = root
    for _ in range(100_000):
        child: dict[str, object] = {}
        current["next"] = [child]
        current = child
    current["patch_plan"] = {"marker": "first"}
    current["user_experience_requirements"] = {"id": "ux"}
    current["goal"] = "  Finish safely.  "
    current["id"] = "leaf"

    assert network._find_first_mapping(root, "patch_plan") == {"marker": "first"}
    assert network._find_mappings_named(root, "user_experience_requirements") == [{"id": "ux"}]
    assert network._find_named_strings(root, {"goal"}) == ["Finish safely."]
    assert network._collect_node_ids(root) == {"leaf", "ux"}


def test_validate_lineage_gate_drift_lint_scales_to_large_trees(tmp_path):
    root = tmp_path / "network"
    manifests = {
        f"n{index}": {
            "child_key": f"n{index}",
            "node_path": f"sub/n{index}",
            "lifecycle_status": "posted_to_mailing_list",
            "edges": [],
        }
        for index in range(1500)
    }
    _write_network(root, manifests)

    started = time.perf_counter()
    report = network.validate_lineage_gate(root)
    elapsed = time.perf_counter() - started

    assert report["summary"]["node_count"] == 1501
    assert elapsed < 10


def test_instant_cycle_uses_only_blocking_graph(tmp_path):
    satisfied = tmp_path / "satisfied"
    _write_network(
        satisfied,
        {
            "a": {"child_key": "a", "node_path": "sub/a", "lifecycle_status": "posted_to_mailing_list", "declared_milestones": ["posted_to_mailing_list"], "edges": [_edge("depends", to="b", state="posted_to_mailing_list", reason="already held")]},
            "b": {"child_key": "b", "node_path": "sub/b", "lifecycle_status": "posted_to_mailing_list", "declared_milestones": ["posted_to_mailing_list"], "edges": [_edge("depends", to="a", state="posted_to_mailing_list", reason="already held")]},
        },
    )
    satisfied_report = network.validate_lineage_gate(satisfied)
    assert satisfied_report["well_founded"] is True
    assert not any(error["type"] == "instant_cycle" for error in satisfied_report["errors"])

    blocking = tmp_path / "blocking"
    _write_network(
        blocking,
        {
            "a": {"child_key": "a", "node_path": "sub/a", "lifecycle_status": "posted_to_mailing_list", "edges": [_edge("depends", to="b", state="merged_into_subsystem_tree", reason="blocking")]},
            "b": {"child_key": "b", "node_path": "sub/b", "lifecycle_status": "posted_to_mailing_list", "edges": [_edge("depends", to="a", state="merged_into_subsystem_tree", reason="blocking")]},
        },
    )
    blocking_report = network.validate_lineage_gate(blocking)
    assert blocking_report["well_founded"] is False
    assert any(error["type"] == "instant_cycle" for error in blocking_report["errors"])


def test_cycle_detection_handles_long_linear_chain():
    edges = [
        {"prerequisite_child_key": f"n{index - 1}", "dependent_child_key": f"n{index}"}
        for index in range(1, 5000)
    ]

    assert network._cycle(edges) == []


def test_duplicate_child_keys_are_gate_errors(tmp_path):
    root = tmp_path / "network"
    _write_network(root, {"x": {"child_key": "same", "node_path": "sub/x", "lifecycle_status": "posted_to_mailing_list", "edges": []}})
    y = root / "sub" / "y"
    y.mkdir(parents=True)
    (y / "patch-series-manifest.json").write_text(
        json.dumps({"schema": "patch_series-network-node-v1", "child_key": "same", "node_path": "sub/y", "lifecycle_status": "posted_to_mailing_list", "edges": []}),
        encoding="utf-8",
    )

    report = network.validate_lineage_gate(root)

    collision = [error for error in report["errors"] if error["type"] == "child_key_collision"]
    assert collision == [{"type": "child_key_collision", "child_key": "same", "paths": ["sub/x/patch-series-manifest.json", "sub/y/patch-series-manifest.json"]}]


def test_or_group_shape_warnings_replace_empty_group_check(tmp_path):
    root = tmp_path / "network"
    _write_network(
        root,
        {
            "a": {"child_key": "a", "node_path": "sub/a", "lifecycle_status": "merged_into_subsystem_tree", "edges": []},
            "b": {"child_key": "b", "node_path": "sub/b", "lifecycle_status": "merged_into_subsystem_tree", "edges": []},
            "consumer": {
                "child_key": "consumer",
                "node_path": "sub/consumer",
                "lifecycle_status": "posted_to_mailing_list",
                "edges": [
                    _edge("depends", to="a", or_group="single", reason="singleton"),
                    _edge("depends", to="b", or_group="same", reason="same target"),
                    _edge("depends", to="b", or_group="same", reason="same target"),
                ],
            },
        },
    )

    report = network.validate_lineage_gate(root)
    warning_types = {warning["type"] for warning in report["warnings"]}
    assert {"or_group_singleton", "or_group_same_target"} <= warning_types
    assert "or_group_empty" not in {error["type"] for error in report["errors"]}


def test_migration_refuses_unsafe_destinations_and_dedupes_path_targets(tmp_path):
    source = tmp_path / "source"
    _write_network(
        source,
        {
            "a": {
                "child_key": "a",
                "node_path": "sub/a",
                "lifecycle_status": "posted_to_mailing_list",
                "serial_after_child_key": "b",
                "depends_on_node_paths": ["sub/b", "sub/b"],
                "edges": [],
            },
            "b": {"child_key": "b", "node_path": "sub/b", "lifecycle_status": "posted_to_mailing_list", "edges": []},
        },
    )
    with pytest.raises(network.PatchSeriesGateError, match="destination must differ"):
        network.migrate_legacy_tree_to_edges(source, source)
    non_empty = tmp_path / "non-empty"
    non_empty.mkdir()
    (non_empty / "keep.txt").write_text("do not remove\n", encoding="utf-8")
    with pytest.raises(network.PatchSeriesGateError, match="not empty"):
        network.migrate_legacy_tree_to_edges(source, non_empty)
    assert (non_empty / "keep.txt").read_text(encoding="utf-8") == "do not remove\n"

    result = network.migrate_legacy_tree_to_edges(source, tmp_path / "dest")
    manifest = json.loads((Path(result["destination"]) / "sub" / "a" / "patch-series-manifest.json").read_text(encoding="utf-8"))
    depends = [edge for edge in manifest["edges"] if edge["type"] == "depends" and edge["to"] == "b"]
    assert len(depends) == 1
    assert not [warning for warning in result["gate_report"]["warnings"] if warning["type"] == "migration_divergence" and warning["node"] == "a"]
    assert result["nodes_migrated"] == 3
    assert result["files_translated"]["manifest"] == 3


def test_migration_alias_ladder_only_rewrites_node_directories_in_place(tmp_path):
    source = tmp_path / "legacy"
    source.mkdir()
    (source / "lineage-node.json").write_text(
        json.dumps({"schema": "patch_series-network-node-v1", "child_key": "root", "node_path": ".", "lifecycle_status": "posted_to_mailing_list"}),
        encoding="utf-8",
    )
    fixture_dir = source / "assets" / "api-fixtures"
    fixture_dir.mkdir(parents=True)
    (fixture_dir / "request.json").write_text('{"fixture": true}\n', encoding="utf-8")

    result = network.migrate_legacy_tree_to_edges(source)

    assert result["nodes_migrated"] == 1
    assert (source / "patch-series-manifest.json").is_file()
    assert not (source / "lineage-node.json").exists()
    assert (fixture_dir / "request.json").read_text(encoding="utf-8") == '{"fixture": true}\n'
    assert not (fixture_dir / "maintainer-series-request.json").exists()


def test_migration_empty_source_returns_typed_error_without_writes(tmp_path):
    source = tmp_path / "empty-source"
    destination = tmp_path / "empty-destination"
    source.mkdir()

    result = network.migrate_legacy_tree_to_edges(source, destination)

    assert result["ok"] is False
    assert result["status"] == "source_not_a_legacy_tree"
    assert result["errors"] == [{"type": "source_not_a_legacy_tree", "source": str(source)}]
    assert result["nodes_migrated"] == 0
    assert result["files_translated"]["manifest"] == 0
    assert not destination.exists()


def test_validate_lineage_gate_rejects_empty_tree(tmp_path):
    root = tmp_path / "empty-network"
    root.mkdir()

    report = network.validate_lineage_gate(root)

    assert report["ok"] is False
    assert report["nodes"] == []
    assert report["summary"]["node_count"] == 0
    assert {error["type"] for error in report["errors"]} >= {"root_manifest_missing", "network_empty"}


def test_migration_treats_serial_after_as_declared_even_with_unrelated_declared_keys():
    base = {
        "schema": "patch_series-network-node-v1",
        "child_key": "a",
        "node_path": "sub/a",
        "lifecycle_status": "posted_to_mailing_list",
        "serial_after_child_key": "b",
        "depends_on_node_paths": ["sub/b"],
        "edges": [],
    }

    no_declared_keys = network.migrate_legacy_manifest_edges({**base, "depends_on_child_keys": []})
    unrelated_declared_key = network.migrate_legacy_manifest_edges({**base, "depends_on_child_keys": ["c"]})

    for converted in (no_declared_keys, unrelated_declared_key):
        edge = next(edge for edge in converted["edges"] if edge["type"] == "depends" and edge["to"] == "b")
        assert edge["reason"] == "migrated: serial-after"


def test_stamp_children_rejects_malformed_when_before_commit(tmp_path):
    repo = _repo(tmp_path)
    _write_parent(repo, "root")

    result = network.stamp_children(
        repo,
        "ai-org/patch-series/root",
        {"id": "bad-template", "title": "Bad"},
        [{"child_key": "bad", "summary": "Bad edge.", "edges": [_edge("depends", to="root", reason="bad", when="not " * 5000 + "x")]}],
    )

    assert result["ok"] is False
    assert result["status"] == "edge-form-invalid"
    assert not git_wrapper.file_exists(repo, "ai-org/patch-series/root", "sub/bad/patch-series-manifest.json")


@pytest.mark.parametrize("artifact_style", ["original", "v1"])
def test_migration_converter_and_dq_fixture_assertions(tmp_path, artifact_style):
    source = _materialize_dq_fixture(tmp_path / f"dq_source_{artifact_style}", artifact_style=artifact_style)
    result = network.migrate_legacy_tree_to_edges(source, tmp_path / "dq_edges_trial")
    report = result["gate_report"]
    fixture = json.loads((Path(__file__).parent / "fixtures" / "dq_edges_trial_manifest_fields.json").read_text(encoding="utf-8"))

    assert report["well_founded"] is True
    assert result["nodes_migrated"] == len(fixture["nodes"]) + 1
    assert result["files_translated"]["manifest"] == len(fixture["nodes"]) + 1
    assert result["files_translated"]["request"] == len(fixture["nodes"])
    citations = sorted((error["node"], error["path"]) for error in report["errors"] if error["type"] == "citation_missing")
    assert citations == [
        ("audio_commission", "spine/art-bible.json"),
        ("audio_commission", "spine/asset-manifest.schema.json"),
        ("decorative_animation_layers", "spine/art-bible.json"),
        ("decorative_animation_layers", "spine/asset-manifest.schema.json"),
        ("late_bilingual_copy", "spine/art-bible.json"),
        ("late_bilingual_copy", "spine/asset-manifest.schema.json"),
    ]
    assert len(citations) == 6
    divergence = {
        warning["node"]: warning["code_derived_only"]
        for warning in report["warnings"]
        if warning["type"] == "migration_divergence"
    }
    assert divergence == {}
    mermaid = (tmp_path / "dq_edges_trial" / "series-dependency-diagram.md").read_text(encoding="utf-8")
    for node in fixture["nodes"]:
        assert node["child_key"] in mermaid
    migrated_files = {path.name for path in (tmp_path / "dq_edges_trial").rglob("*") if path.is_file()}
    assert not migrated_files.intersection(_legacy_fixture_names(artifact_style))


def _write_network(
    root: Path,
    manifests: dict[str, dict[str, object]],
    requests: dict[str, str] | None = None,
    registry: list[dict[str, str]] | None = None,
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    root_manifest: dict[str, object] = {
        "schema": "patch_series-network-node-v1",
        "child_key": "root",
        "node_path": ".",
        "lifecycle_status": "merged_into_subsystem_tree",
        "edges": [],
    }
    if registry is not None:
        root_manifest["declared_patchwork_checks"] = registry
    (root / "patch-series-manifest.json").write_text(
        json.dumps(root_manifest),
        encoding="utf-8",
    )
    for key, manifest in manifests.items():
        node_dir = root / "sub" / key
        node_dir.mkdir(parents=True, exist_ok=True)
        payload = dict(manifest)
        payload.setdefault("schema", "patch_series-network-node-v1")
        payload.setdefault("child_key", key)
        payload.setdefault("node_path", f"sub/{key}")
        (node_dir / "patch-series-manifest.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        request_text = (requests or {}).get(key, f"Request for {key}.")
        (node_dir / "maintainer-series-request.json").write_text(
            json.dumps({"id": key, "submitted_at": "", "request": {"raw_request": request_text, "working_title": key}}),
            encoding="utf-8",
        )


def _materialize_dq_fixture(root: Path, *, artifact_style: str = "current") -> Path:
    fixture = json.loads((Path(__file__).parent / "fixtures" / "dq_edges_trial_manifest_fields.json").read_text(encoding="utf-8"))
    root.mkdir(parents=True, exist_ok=True)
    (root / _fixture_artifact_name(artifact_style, "patch-series-manifest.json")).write_text(
        json.dumps({"schema": "patch_series-network-node-v1", "child_key": "root", "node_path": ".", "lifecycle_status": "merged_into_subsystem_tree", "edges": []}),
        encoding="utf-8",
    )
    for node in fixture["nodes"]:
        key = node["child_key"]
        node_dir = root / "sub" / key
        node_dir.mkdir(parents=True, exist_ok=True)
        manifest = {
            "schema": "patch_series-network-node-v1",
            "child_key": key,
            "node_path": f"sub/{key}",
            "parent_node_path": ".",
            "lifecycle_status": "posted_to_mailing_list",
            "depends_on_child_keys": node["depends_on_child_keys"],
            "depends_on_node_paths": node["depends_on_node_paths"],
            "serial_after_child_key": node.get("serial_after_child_key", ""),
            "branching_mode": "parallel_from_parent",
            "node_kind": "coarse",
        }
        (node_dir / _fixture_artifact_name(artifact_style, "patch-series-manifest.json")).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        (node_dir / _fixture_artifact_name(artifact_style, "maintainer-series-request.json")).write_text(
            json.dumps({"id": key, "submitted_at": "", "request": {"raw_request": node["request"], "working_title": key}}),
            encoding="utf-8",
        )
        (node_dir / _fixture_artifact_name(artifact_style, "patch-series-cover-letter.json")).write_text(json.dumps({"raw_request": node["request"]}), encoding="utf-8")
        (node_dir / _fixture_artifact_name(artifact_style, "patch-series-metadata.json")).write_text(json.dumps({"node": key}), encoding="utf-8")
        (node_dir / _fixture_artifact_name(artifact_style, "patchwork-check-events.jsonl")).write_text("", encoding="utf-8")
        (node_dir / _fixture_artifact_name(artifact_style, "technical-approach-plan.json")).write_text(json.dumps({"node": key}), encoding="utf-8")
    return root


def _fixture_artifact_name(artifact_style: str, current_name: str) -> str:
    names = {
        "current": {
            "patch-series-manifest.json": "patch-series-manifest.json",
            "maintainer-series-request.json": "maintainer-series-request.json",
            "patch-series-cover-letter.json": "patch-series-cover-letter.json",
            "patch-series-metadata.json": "patch-series-metadata.json",
            "patchwork-check-events.jsonl": "patchwork-check-events.jsonl",
            "technical-approach-plan.json": "technical-approach-plan.json",
        },
        "original": {
            "patch-series-manifest.json": "lineage-node.json",
            "maintainer-series-request.json": "request.json",
            "patch-series-cover-letter.json": "rfc.json",
            "patch-series-metadata.json": "rfc-metadata.json",
            "patchwork-check-events.jsonl": "state-events.jsonl",
            "technical-approach-plan.json": "technical-approach.json",
        },
        "v1": {
            "patch-series-manifest.json": "network-node-manifest.json",
            "maintainer-series-request.json": "parent-work-request.json",
            "patch-series-cover-letter.json": "executable-work-order.json",
            "patch-series-metadata.json": "work-order-metadata.json",
            "patchwork-check-events.jsonl": "state-variable-events.jsonl",
            "technical-approach-plan.json": "technical-approach-plan.json",
        },
    }
    return names[artifact_style][current_name]


def _legacy_fixture_names(artifact_style: str) -> set[str]:
    if artifact_style == "current":
        return set()
    current_names = {
        "patch-series-manifest.json",
        "maintainer-series-request.json",
        "patch-series-cover-letter.json",
        "patch-series-metadata.json",
        "patchwork-check-events.jsonl",
        "technical-approach-plan.json",
    }
    return {
        _fixture_artifact_name(artifact_style, current_name)
        for current_name in current_names
        if _fixture_artifact_name(artifact_style, current_name) != current_name
    }


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "network-b-test@example.invalid")
    _git(repo, "config", "user.name", "Network B Test")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "base")
    _git(repo, "branch", "-M", "main")
    return repo


def _write_parent(repo: Path, patch_series_id: str) -> None:
    git_wrapper.create_branch_with_files(
        repo,
        f"ai-org/patch-series/{patch_series_id}",
        "main",
        {
            "patch-series-cover-letter.json": _patch_series(),
            "technical-approach-plan.json": _approach(),
        },
        commit_message="patch_series: direction-ok",
    )


def _write_child_branch(repo: Path, patch_series_id: str, base: str) -> None:
    git_wrapper.create_branch_with_files(
        repo,
        f"ai-org/patch-series/{patch_series_id}",
        base,
        {
            "patch-series-cover-letter.json": _patch_series(),
            "technical-approach-plan.json": _approach(),
        },
        commit_message="patch_series: direction-ok",
    )


def _write_contrib_branch(repo: Path, child_branch: str, base: str) -> str:
    if ":" in child_branch:
        parent, node_path = child_branch.split(":", 1)
        serial = git_wrapper.serial_for_ref(repo, parent) or parent.removeprefix("ai-org/patch-series/")
        child_id = f"{serial}-{Path(node_path).name}"
    else:
        child_id = child_branch.removeprefix("ai-org/patch-series/")
    branch = f"ai-org/contrib/{child_id}"
    git_wrapper.create_branch_with_files(
        repo,
        branch,
        base,
        {f"implementation/{child_id}.txt": f"implementation for {child_id}\n"},
        commit_message=f"implement: {child_id}",
    )
    return branch


def _commit_raw_file(repo: Path, branch: str, rel_path: str, content: bytes) -> None:
    original = _git(repo, "branch", "--show-current")
    try:
        _git(repo, "checkout", branch)
        path = repo / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        _git(repo, "add", rel_path)
        _git(repo, "commit", "-m", "test: raw bytes")
    finally:
        if original:
            _git(repo, "checkout", original)


def _address_json(repo: Path, address: str, rel_path: str) -> dict:
    branch, node_path = address.split(":", 1)
    return network_bodies.read_network_body(repo, branch, f"{node_path}/{rel_path}")


def _node_json(repo: Path, node_path: str, rel_path: str) -> dict:
    return network_bodies.read_network_body(
        repo, "ai-org/patch-series/root", f"{node_path}/{rel_path}"
    )


def _patch_series() -> dict[str, object]:
    return {
        "raw_request": "Build the battle slice.",
        "working_title": "Battle Slice",
        "request_type": "feature",
        "problem_or_motivation": "Players need a visible first battle.",
        "intended_users_or_jobs": "Players can complete a tiny battle loop.",
        "desired_outcomes_success": "The battle slice is playable and reviewable.",
        "affected_area_platform": "ai_org.patchwork_queue",
        "tech_stack": {
            "build_strategy": "framework_based",
            "engine": "",
            "framework": "repo-native Python modules",
            "language": "Python",
            "platform": "CLI",
            "rationale": "Use existing repository modules.",
            "provenance": "requester_specified",
        },
        "user_experience_requirements": empty_user_experience_requirements(),
        "background_facts": "Test fixture.",
        "constraints_assumptions": [],
        "references": [],
        "grounding_provenance": "Test fixture.",
        "open_questions": [],
        "non_goals_out_of_scope": [],
        "proposal_hint": "Implement the first battle slice.",
        "alternatives_considered": [],
    }


def _approach() -> dict[str, object]:
    return {
        "problem": {
            "question": {
                "decision": {
                    "implementation": {
                        "systems": [{"name": "battle", "key_modules": ["game.battle"]}],
                        "domain_specification": {
                            "id": "domain_specification",
                            "aspects": [
                                {
                                    "id": "domain_specification:battle-numbers",
                                    "aspect_name": "battle numbers",
                                    "applicability": "applies",
                                    "specification_body": "Spark damage and enemy HP are contractual.",
                                    "quantities": [{"name": "Spark damage", "value": "4", "unit": "hit points"}],
                                    "tables": [{"table_name": "stats", "columns": ["name", "hp"], "rows": [["Slime", "4"]]}],
                                    "sources": ["Reference battle loop"],
                                    "externalized_tables": [
                                        {
                                            "table_name": "encounters",
                                            "columns": ["name", "hp"],
                                            "row_count": 20,
                                            "file_ref": "domain-spec/battle-numbers.json",
                                        }
                                    ],
                                }
                            ],
                        },
                        "user_experience_requirements": {
                            **empty_user_experience_requirements(),
                            "acceptance_tests": {
                                "screenshot_checks": ["Screenshot shows HP, MP, enemy, and command surfaces."],
                                "interaction_checks": ["Casting Spark shows damage feedback."],
                                "playtest_checks": ["Defeating the enemy opens the gate."],
                            },
                        },
                        "patch_plan": {
                            "first_proof_moment": {
                                "summary": "First proof moment battle loop.",
                                "how_verified": "Run functional_check for the battle loop.",
                            },
                            "follow_ups": [{"adds": "Add the battle log."}],
                            "deferred": [{"item": "Campaign map.", "why_safe_to_defer": "Battle loop stands alone."}],
                        },
                        "risks": [
                            {
                                "id": "state-drift",
                                "risk": "Saved battle state can drift from runtime state.",
                                "mitigation": "Verify save and reload.",
                            }
                        ],
                    }
                }
            }
        }
    }


def _producer_aware_network_root() -> dict[str, object]:
    return {
        "problem": {
            "goals": [
                {
                    "id": "goal:preview",
                    "requires_deliverable": True,
                    "actor": "maintainer",
                    "capability": {
                        "action": "Inspect the producer-aware root.",
                        "preconditions": ["The candidate is frozen."],
                    },
                    "verifiable_outcome": {
                        "expected_state": "The producer pair is closed.",
                        "evidence": "The deterministic vet passes.",
                    },
                    "verification": {
                        "method": "automated_test",
                        "check": "Exercise every stateless route.",
                    },
                    "ux_trace": "none",
                }
            ],
            "deliverable_requirements": [
                {
                    "id": "requirement:preview",
                    "referee_goal_id": "goal:preview",
                    "production_obligation_id": "obligation:preview",
                    "deliverable": "technical-approach-plan.cue",
                }
            ],
            "production_obligations": [
                {
                    "id": "obligation:preview",
                    "referee_goal_id": "goal:preview",
                    "deliverable_requirement_id": "requirement:preview",
                    "deliverable": "technical-approach-plan.cue",
                    "eligibility_predicate": "Can produce and test the canonical body.",
                    "replacement_links": [],
                }
            ],
            "patch_plan": [
                {
                    "item_id": "patch_plan:root#first-proof-moment",
                    "production_obligation_ids": ["obligation:preview"],
                }
            ],
        }
    }


def _graph_item(item_id: str, depends_on: list[str] | None = None) -> dict[str, object]:
    return {
        "id": item_id,
        "objective": f"Implement {item_id}.",
        "named_content": [],
        "acceptance_criteria": [f"{item_id} passes."],
        "how_verified": f"test {item_id}",
        "depends_on": list(depends_on or []),
    }


def _graph_approach(*items: dict[str, object]) -> dict[str, object]:
    return {
        "problem": {
            "question": {
                "decision": {
                    "implementation": {
                        "patch_plan": {
                            "id": "patch_plan:graph",
                            "items": list(items),
                            "deferred": [],
                            "open_questions": [],
                        }
                    }
                }
            }
        }
    }


def _split_all_scope() -> dict[str, object]:
    return {
        "split_mode": "split_into_children",
        "rationale": "Prep, behavior, and later campaign work are separate concerns.",
        "parent_retained_scope_ids": [],
        "children": [
            _proposal_child(
                "prep",
                [
                    "goal:patch_series.desired_outcomes_success",
                    "patch_plan:first_proof_moment",
                    "ux:technical_approach:1:screenshot_checks:1",
                    "domain_specification:battle-numbers",
                ],
            ),
            _proposal_child(
                "battle",
                ["patch_plan:follow_up:1", "ux:technical_approach:1:interaction_checks:1", "risk:state-drift"],
                depends_on=["prep"],
            ),
            _proposal_child(
                "campaign",
                ["patch_plan:deferred:1", "ux:technical_approach:1:playtest_checks:1"],
                depends_on=["battle"],
            ),
        ],
    }


def _split_with_battle_scope_changed() -> dict[str, object]:
    split = _split_all_scope()
    children = split["children"]
    assert isinstance(children, list)
    battle = children[1]
    campaign = children[2]
    assert isinstance(battle, dict)
    assert isinstance(campaign, dict)
    battle["scope_item_ids"] = [*battle["scope_item_ids"], "patch_plan:deferred:1"]
    campaign["scope_item_ids"] = ["ux:technical_approach:1:playtest_checks:1"]
    return split


def _single_child_split() -> dict[str, object]:
    return {
        "split_mode": "split_into_children",
        "rationale": "One child covers all executable work.",
        "parent_retained_scope_ids": [],
        "children": [_proposal_child("only", _expected_scope_ids())],
    }


def _split_missing_scope() -> dict[str, object]:
    split = _single_child_split()
    split["children"][0]["scope_item_ids"] = ["goal:patch_series.desired_outcomes_success"]
    return split


def _right_sized_split() -> dict[str, object]:
    return {
        "split_mode": "right_sized",
        "rationale": "The coarse node is now a bounded leaf.",
        "parent_retained_scope_ids": [],
        "children": [],
    }


def _proposal_child(
    key: str,
    scope_ids: list[str],
    *,
    depends_on: list[str] | None = None,
) -> dict[str, object]:
    return {
        "child_key": key,
        "title": f"{key.title()} Child",
        "stage_name": key,
        "edges": [_edge("depends", to=target, reason="fixture dependency") for target in (depends_on or [])],
        "summary": f"Implement {key}.",
        "acceptance_criteria": [f"{key} acceptance passes."],
        "functional_check": f"functional_check verifies {key}.",
        "scope_item_ids": scope_ids,
        "systems": ["game.battle"],
        "ux_acceptance_tests": [],
        "risks": [],
    }


def _contract_scope() -> list[dict[str, str]]:
    return [
        {"id": "scope:one", "kind": "goal", "text": "First scope."},
        {"id": "scope:two", "kind": "goal", "text": "Second scope."},
    ]


def _contract_split(children: list[dict[str, object]], retained: list[str]) -> dict[str, object]:
    return {"split_mode": "split_into_children", "rationale": "test", "children": children, "parent_retained_scope_ids": retained}


def _child(key: str, scope_ids: list[str], *, depends_on: list[str] | None = None) -> dict[str, object]:
    return {
        "child_key": key,
        "scope_item_ids": scope_ids,
        "edges": [_edge("depends", to=target, reason="fixture dependency") for target in (depends_on or [])],
    }


def _edge(edge_type: str, *, to: str = "", with_: str = "", reason: str = "fixture", state: str = "merged_into_subsystem_tree", or_group: str = "", when: str = "") -> dict[str, object]:
    return {
        "type": edge_type,
        "to": to,
        "with": with_,
        "state": state if edge_type == "depends" else "",
        "or_group": or_group,
        "when": when,
        "reason": reason,
    }


def _depends_targets(manifest: dict[str, object]) -> list[str]:
    return [edge["to"] for edge in manifest.get("edges", []) if isinstance(edge, dict) and edge.get("type") == "depends"]


def _expected_scope_ids() -> list[str]:
    return [
        "goal:patch_series.desired_outcomes_success",
        "ux:technical_approach:1:interaction_checks:1",
        "ux:technical_approach:1:playtest_checks:1",
        "ux:technical_approach:1:screenshot_checks:1",
        "patch_plan:first_proof_moment",
        "patch_plan:follow_up:1",
        "patch_plan:deferred:1",
        "domain_specification:battle-numbers",
        "risk:state-drift",
    ]


def _scope_ids(ledger: dict[str, object]) -> list[str]:
    return [item["id"] for item in ledger["scope_items"]]


def _install_codex_fake(monkeypatch: pytest.MonkeyPatch, responses: list[dict[str, object]]) -> None:
    calls = {"count": 0}

    def fake_split(repo, branch, patch_series, approach, scope_items, horizon, feedback, **_kwargs):
        # Historical network-mechanics tests inject a formed carrier below the
        # splitter. Dedicated tests exercise the deterministic partition.
        _install_codex_fake.last_prompt = network._split_prompt(
            branch, patch_series, approach, scope_items, horizon, feedback
        )
        calls["count"] += 1
        index = min(calls["count"] - 1, len(responses) - 1)
        return {"ok": True, "split": responses[index]}

    _install_codex_fake.calls = 0
    _install_codex_fake.last_prompt = ""

    def counting_fake(*args, **kwargs):
        result = fake_split(*args, **kwargs)
        _install_codex_fake.calls = calls["count"]
        return result

    monkeypatch.setattr(network, "_split_plan_graph", counting_fake)


def _assert_required_is_all_properties(schema: dict) -> None:
    assert schema["additionalProperties"] is False
    assert sorted(schema["required"]) == sorted(schema["properties"])


def _forbidden_schema_keys(value, forbidden: set[str], path: str = "$") -> list[str]:
    if isinstance(value, dict):
        found = [f"{path}.{key}" for key in value if key in forbidden]
        for key, child in value.items():
            found.extend(_forbidden_schema_keys(child, forbidden, f"{path}.{key}"))
        return found
    if isinstance(value, list):
        found = []
        for index, child in enumerate(value):
            found.extend(_forbidden_schema_keys(child, forbidden, f"{path}[{index}]"))
        return found
    return []


def _merge(repo: Path, target: str, source: str) -> None:
    original = _git(repo, "branch", "--show-current")
    try:
        _git(repo, "checkout", target)
        _git(repo, "merge", "--no-ff", "--no-edit", source)
    finally:
        if original:
            _git(repo, "checkout", original)


def _merge_into_subsystem(repo: Path, source: str) -> None:
    if not git_wrapper.branch_exists(repo, "ai-org/subsystem"):
        _git(repo, "branch", "ai-org/subsystem", "main")
    _merge(repo, "ai-org/subsystem", source)


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return result.stdout.strip()
