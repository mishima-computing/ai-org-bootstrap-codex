from __future__ import annotations

import hashlib
import itertools
from pathlib import Path
import re
import subprocess

import pytest

from ai_org import git_wrapper, patch_series_bodies
from ai_org.patch_author import code_worker, discovery, functional_check
from ai_org.patchwork_queue import patch_series_gate, receive, review


def _git(repo, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ).stdout.strip()


def _repo(tmp_path):
    _git(tmp_path, "init", "-b", "main")
    (tmp_path / "README").write_text("base\n", encoding="utf-8")
    _git(tmp_path, "add", "README")
    subprocess.run(
        ["git", "-C", str(tmp_path), *git_wrapper.identity_config_args(), "commit", "-m", "base"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return tmp_path


@pytest.mark.parametrize(
    ("files", "generation", "disposition", "ready", "cover_path"),
    [
        (
            {
                patch_series_bodies.LEGACY_COVER_PATH: {},
                patch_series_bodies.LEGACY_ROOT_APPROACH_PATH: {},
            },
            patch_series_bodies.ROOT_GENERATION_HISTORICAL,
            patch_series_bodies.ROOT_DISPOSITION_LEGACY_READY,
            True,
            patch_series_bodies.LEGACY_COVER_PATH,
        ),
        (
            {
                patch_series_bodies.COVER_PATH: "cover",
                patch_series_bodies.PROVENANCE_PATH: "provenance",
                patch_series_bodies.LEGACY_ROOT_APPROACH_PATH: {},
            },
            patch_series_bodies.ROOT_GENERATION_CURRENT,
            patch_series_bodies.ROOT_DISPOSITION_CURRENT_READY,
            True,
            patch_series_bodies.COVER_PATH,
        ),
        (
            {
                patch_series_bodies.COVER_PATH: "cover",
                patch_series_bodies.PROVENANCE_PATH: "provenance",
                patch_series_bodies.ROOT_APPROACH_PATH: "approach",
            },
            patch_series_bodies.ROOT_GENERATION_V2,
            patch_series_bodies.ROOT_DISPOSITION_V2_NOT_READY,
            False,
            patch_series_bodies.COVER_PATH,
        ),
        (
            {
                patch_series_bodies.COVER_PATH: "cover",
                patch_series_bodies.PROVENANCE_PATH: "provenance",
                patch_series_bodies.ROOT_APPROACH_PATH: "approach",
                patch_series_bodies.SCOPE_DECOMPOSITION_PATH: "scope",
            },
            patch_series_bodies.ROOT_GENERATION_V2,
            patch_series_bodies.ROOT_DISPOSITION_V2_READY,
            True,
            patch_series_bodies.COVER_PATH,
        ),
        (
            {
                patch_series_bodies.COVER_PATH: "cover",
                patch_series_bodies.PROVENANCE_PATH: "provenance",
                patch_series_bodies.ROOT_APPROACH_PATH: "approach",
                patch_series_bodies.LEGACY_ROOT_APPROACH_PATH: {},
            },
            patch_series_bodies.ROOT_GENERATION_INVALID,
            patch_series_bodies.ROOT_DISPOSITION_INVALID,
            False,
            None,
        ),
    ],
)
def test_root_generation_transitions(
    tmp_path, files, generation, disposition, ready, cover_path
):
    repo = _repo(tmp_path)
    branch = "ai-org/patch-series/root-generation"
    git_wrapper.create_branch_with_files(
        repo, branch, "main", files, commit_message="root generation fixture"
    )

    snapshot = patch_series_bodies.classify_root_generation(repo, branch)

    assert snapshot.frozen_oid == _git(repo, "rev-parse", branch)
    assert snapshot.generation == generation
    assert snapshot.disposition == disposition
    assert snapshot.lifecycle_ready is ready
    assert snapshot.cover_path == cover_path
    assert snapshot.legacy_predicates_allowed is (
        ready and generation != patch_series_bodies.ROOT_GENERATION_V2
    )


def test_v2_generation_stays_blocked_until_the_separate_readiness_gate(tmp_path):
    repo = _repo(tmp_path)
    branch = "ai-org/patch-series/root-v2-readiness-transition"
    git_wrapper.create_branch_with_files(
        repo,
        branch,
        "main",
        {
            patch_series_bodies.COVER_PATH: "cover",
            patch_series_bodies.PROVENANCE_PATH: "provenance",
            patch_series_bodies.ROOT_APPROACH_PATH: "approach",
        },
        commit_message="complete root v2",
    )

    pending = patch_series_bodies.classify_root_generation(repo, branch)

    assert pending.generation == patch_series_bodies.ROOT_GENERATION_V2
    assert pending.disposition == patch_series_bodies.ROOT_DISPOSITION_V2_NOT_READY
    assert pending.lifecycle_ready is False
    assert pending.legacy_predicates_allowed is False

    git_wrapper.commit_files(
        repo,
        branch,
        {patch_series_bodies.SCOPE_DECOMPOSITION_PATH: "scope"},
        subject="publish migrated lifecycle readiness evidence",
    )
    admitted = patch_series_bodies.classify_root_generation(repo, branch)

    assert admitted.frozen_oid != pending.frozen_oid
    assert admitted.generation == patch_series_bodies.ROOT_GENERATION_V2
    assert admitted.disposition == patch_series_bodies.ROOT_DISPOSITION_V2_READY
    assert admitted.lifecycle_ready is True
    assert admitted.legacy_predicates_allowed is False


@pytest.mark.parametrize(
    ("files", "source_path", "context", "representation"),
    [
        (
            {
                patch_series_bodies.LEGACY_COVER_PATH: {},
            },
            patch_series_bodies.LEGACY_COVER_PATH,
            patch_series_bodies.COVER_CONTEXT,
            patch_series_bodies.ROOT_GENERATION_HISTORICAL,
        ),
        (
            {
                patch_series_bodies.LEGACY_COVER_PATH: {},
                patch_series_bodies.LEGACY_ROOT_APPROACH_PATH: {
                    "root": "historical"
                },
            },
            patch_series_bodies.LEGACY_ROOT_APPROACH_PATH,
            patch_series_bodies.ROOT_APPROACH_CONTEXT,
            patch_series_bodies.ROOT_GENERATION_HISTORICAL,
        ),
        (
            {
                patch_series_bodies.COVER_PATH: "cover",
                patch_series_bodies.PROVENANCE_PATH: "provenance",
                patch_series_bodies.LEGACY_ROOT_APPROACH_PATH: {"root": "current"},
            },
            patch_series_bodies.LEGACY_ROOT_APPROACH_PATH,
            patch_series_bodies.ROOT_APPROACH_CONTEXT,
            patch_series_bodies.ROOT_GENERATION_CURRENT,
        ),
        (
            {
                patch_series_bodies.COVER_PATH: "cover",
                patch_series_bodies.PROVENANCE_PATH: "provenance",
                patch_series_bodies.ROOT_APPROACH_PATH: "canonical root",
            },
            patch_series_bodies.ROOT_APPROACH_PATH,
            patch_series_bodies.ROOT_APPROACH_CONTEXT,
            patch_series_bodies.ROOT_GENERATION_V2,
        ),
    ],
)
def test_classifier_carries_authoritative_source_metadata(
    tmp_path, files, source_path, context, representation
):
    repo = _repo(tmp_path)
    branch = "ai-org/patch-series/root-source-metadata"
    git_wrapper.create_branch_with_files(
        repo, branch, "main", files, commit_message="root source metadata"
    )

    snapshot = patch_series_bodies.classify_root_generation(repo, branch)

    assert snapshot.representation == representation
    assert snapshot.source_path == source_path
    assert snapshot.source_oid == _git(repo, "rev-parse", branch)
    assert snapshot.context == context
    source = git_wrapper.read_tree_file(repo, snapshot.source_oid, source_path)
    assert source is not None
    assert snapshot.canonical_digest == hashlib.sha256(source.encode()).hexdigest()
    assert snapshot.identity() == {
        "representation": representation,
        "source_path": source_path,
        "source_oid": _git(repo, "rev-parse", branch),
        "context": context,
        "canonical_digest": snapshot.canonical_digest,
    }


def test_orphan_historical_approach_is_invalid(tmp_path):
    repo = _repo(tmp_path)
    branch = "ai-org/patch-series/incomplete-historical-root"
    git_wrapper.create_branch_with_files(
        repo,
        branch,
        "main",
        {patch_series_bodies.LEGACY_ROOT_APPROACH_PATH: {}},
        commit_message="orphan historical approach",
    )

    snapshot = patch_series_bodies.classify_root_generation(repo, branch)

    assert snapshot.generation == patch_series_bodies.ROOT_GENERATION_INVALID
    assert snapshot.disposition == patch_series_bodies.ROOT_DISPOSITION_INVALID
    assert snapshot.source_oid == _git(repo, "rev-parse", branch)
    assert snapshot.source_path is None
    assert snapshot.canonical_digest is None


@pytest.mark.parametrize(
    "files",
    [
        {
            patch_series_bodies.COVER_PATH: "cover",
            patch_series_bodies.LEGACY_ROOT_APPROACH_PATH: {},
        },
        {
            patch_series_bodies.COVER_PATH: "cover",
            patch_series_bodies.PROVENANCE_PATH: "provenance",
        },
        {
            patch_series_bodies.COVER_PATH: "cover",
            patch_series_bodies.PROVENANCE_PATH: "provenance",
            patch_series_bodies.ROOT_APPROACH_PATH: "approach",
            patch_series_bodies.LEGACY_ROOT_APPROACH_PATH: {},
        },
    ],
)
def test_current_and_v2_mixed_or_incomplete_shapes_fail_closed(tmp_path, files):
    repo = _repo(tmp_path)
    branch = "ai-org/patch-series/invalid-root-shape"
    git_wrapper.create_branch_with_files(
        repo,
        branch,
        "main",
        files,
        commit_message="invalid root shape",
    )

    snapshot = patch_series_bodies.classify_root_generation(repo, branch)

    assert snapshot.generation == patch_series_bodies.ROOT_GENERATION_INVALID
    assert snapshot.disposition == patch_series_bodies.ROOT_DISPOSITION_INVALID
    assert snapshot.lifecycle_ready is False
    assert snapshot.legacy_predicates_allowed is False


def test_closed_generation_recipe_manifest_classifies_every_root_shape_once():
    paths = patch_series_bodies._ROOT_REPRESENTATION_PATHS
    historical_members = {
        patch_series_bodies.LEGACY_COVER_PATH,
        patch_series_bodies.LEGACY_PROVENANCE_PATH,
        patch_series_bodies.LEGACY_ROOT_APPROACH_PATH,
    }
    current_members = {
        patch_series_bodies.COVER_PATH,
        patch_series_bodies.PROVENANCE_PATH,
        patch_series_bodies.LEGACY_ROOT_APPROACH_PATH,
    }
    v2_members = set(patch_series_bodies.ROOT_COHORT_V2_PATHS)

    observed: dict[str, list[frozenset[str]]] = {
        generation: []
        for generation in (
            patch_series_bodies.ROOT_GENERATION_HISTORICAL,
            patch_series_bodies.ROOT_GENERATION_CURRENT,
            patch_series_bodies.ROOT_GENERATION_V2,
            patch_series_bodies.ROOT_GENERATION_INVALID,
        )
    }
    for count in range(len(paths) + 1):
        for selection in itertools.combinations(paths, count):
            members = frozenset(selection)
            generation = patch_series_bodies._classify_root_generation_shape(
                set(members)
            )
            observed[generation].append(members)

            if (
                patch_series_bodies.LEGACY_COVER_PATH in members
                and members <= historical_members
            ):
                expected = patch_series_bodies.ROOT_GENERATION_HISTORICAL
            elif members == current_members:
                expected = patch_series_bodies.ROOT_GENERATION_CURRENT
            elif members == v2_members:
                expected = patch_series_bodies.ROOT_GENERATION_V2
            else:
                expected = patch_series_bodies.ROOT_GENERATION_INVALID
            assert generation == expected

    assert len(observed[patch_series_bodies.ROOT_GENERATION_HISTORICAL]) == 4
    assert observed[patch_series_bodies.ROOT_GENERATION_CURRENT] == [
        frozenset(current_members)
    ]
    assert observed[patch_series_bodies.ROOT_GENERATION_V2] == [
        frozenset(v2_members)
    ]
    assert len(observed[patch_series_bodies.ROOT_GENERATION_INVALID]) == 58


def test_network_decision_consumes_the_classifier_source_identity(tmp_path):
    repo = _repo(tmp_path)
    branch = "ai-org/patch-series/root-network-source"
    git_wrapper.create_branch_with_files(
        repo,
        branch,
        "main",
        {
            patch_series_bodies.LEGACY_COVER_PATH: {},
            patch_series_bodies.LEGACY_ROOT_APPROACH_PATH: {},
        },
        commit_message="historical root source",
    )
    snapshot = patch_series_bodies.classify_root_generation(repo, branch)

    decision = patch_series_gate.vet_producer_pair_closure(
        repo, branch, "stamping"
    )

    source = decision.source.as_dict()
    assert decision.authorable is True
    for field, value in snapshot.identity().items():
        assert source[field] == (value or "")


def test_network_decision_reuses_the_vetted_snapshot_without_reclassification(
    tmp_path, monkeypatch
):
    repo = _repo(tmp_path)
    branch = "ai-org/patch-series/root-vetted-snapshot"
    git_wrapper.create_branch_with_files(
        repo,
        branch,
        "main",
        {
            patch_series_bodies.LEGACY_COVER_PATH: {},
            patch_series_bodies.LEGACY_ROOT_APPROACH_PATH: {},
        },
        commit_message="historical root source",
    )
    decision = patch_series_gate.vet_producer_pair_closure(
        repo, branch, "stamping"
    )
    snapshot = decision.source.root_snapshot
    assert snapshot is not None

    monkeypatch.setattr(
        patch_series_bodies,
        "classify_root_generation",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("a vetted source must not be classified again")
        ),
    )

    assert patch_series_gate._vetted_root_snapshot(repo, decision.source) is snapshot


def test_complete_v2_never_falls_back_to_legacy_reader(tmp_path):
    repo = _repo(tmp_path)
    branch = "ai-org/patch-series/root-v2"
    git_wrapper.create_branch_with_files(
        repo,
        branch,
        "main",
        {
            patch_series_bodies.COVER_PATH: "cover",
            patch_series_bodies.PROVENANCE_PATH: "provenance",
            patch_series_bodies.ROOT_APPROACH_PATH: "approach",
        },
        commit_message="complete root v2",
    )

    snapshot = patch_series_bodies.classify_root_generation(repo, branch)

    with pytest.raises(
        patch_series_bodies.RootGenerationError,
        match="producer_lifecycle_not_ready",
    ) as caught:
        snapshot.technical_approach()
    assert caught.value.status == "producer_lifecycle_not_ready"
    assert snapshot.raw(patch_series_bodies.LEGACY_ROOT_APPROACH_PATH) is None
    with pytest.raises(
        patch_series_bodies.RootGenerationError,
        match="producer_lifecycle_not_ready",
    ):
        patch_series_bodies.read_cover_letter(repo, branch)


def test_classifier_reads_every_member_from_one_frozen_oid(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    branch = "ai-org/patch-series/frozen-root"
    git_wrapper.create_branch_with_files(
        repo,
        branch,
        "main",
        {
            patch_series_bodies.LEGACY_COVER_PATH: {},
            patch_series_bodies.LEGACY_ROOT_APPROACH_PATH: {},
        },
        commit_message="historical root",
    )
    real_head_sha = git_wrapper.head_sha
    real_show_file = git_wrapper.show_file
    frozen = real_head_sha(repo, branch)
    observed_refs: list[str] = []

    monkeypatch.setattr(git_wrapper, "head_sha", lambda *_args: frozen)

    def recording_show_file(repo_path, ref, path):
        observed_refs.append(ref)
        return real_show_file(repo_path, ref, path)

    monkeypatch.setattr(git_wrapper, "show_file", recording_show_file)

    patch_series_bodies.classify_root_generation(repo, branch)

    assert len(observed_refs) == len(patch_series_bodies._ROOT_GENERATION_PATHS)
    assert set(observed_refs) == {frozen}


def test_classifier_does_not_mix_generations_when_the_root_ref_moves(
    tmp_path, monkeypatch
):
    repo = _repo(tmp_path)
    branch = "ai-org/patch-series/moving-root"
    git_wrapper.create_branch_with_files(
        repo,
        branch,
        "main",
        {
            patch_series_bodies.LEGACY_COVER_PATH: {},
            patch_series_bodies.LEGACY_ROOT_APPROACH_PATH: {},
        },
        commit_message="historical root",
    )
    historical_oid = _git(repo, "rev-parse", branch)
    real_show_file = git_wrapper.show_file
    moved = False

    def move_after_first_member(repo_path, ref, path):
        nonlocal moved
        raw = real_show_file(repo_path, ref, path)
        if not moved:
            moved = True
            git_wrapper.commit_files(
                repo,
                branch,
                {
                    patch_series_bodies.COVER_PATH: "cover",
                    patch_series_bodies.PROVENANCE_PATH: "provenance",
                    patch_series_bodies.ROOT_APPROACH_PATH: "approach",
                },
                subject="move root to v2",
                remove_paths=(
                    patch_series_bodies.LEGACY_COVER_PATH,
                    patch_series_bodies.LEGACY_ROOT_APPROACH_PATH,
                ),
            )
        return raw

    monkeypatch.setattr(git_wrapper, "show_file", move_after_first_member)

    snapshot = patch_series_bodies.classify_root_generation(repo, branch)

    assert snapshot.frozen_oid == historical_oid
    assert snapshot.generation == patch_series_bodies.ROOT_GENERATION_HISTORICAL
    assert snapshot.disposition == patch_series_bodies.ROOT_DISPOSITION_LEGACY_READY
    assert _git(repo, "rev-parse", branch) != historical_oid
    assert (
        patch_series_bodies.classify_root_generation(repo, branch).disposition
        == patch_series_bodies.ROOT_DISPOSITION_V2_NOT_READY
    )


def test_canonical_carrier_predicate_consumes_the_generation_snapshot(monkeypatch):
    classified_refs: list[str] = []

    class Snapshot:
        frozen_oid = "c" * 40

        def raw(self, path):
            return (
                b"member"
                if path
                in {
                    patch_series_bodies.COVER_PATH,
                    patch_series_bodies.PROVENANCE_PATH,
                }
                else None
            )

    def classify(_repo, ref):
        classified_refs.append(ref)
        return Snapshot()

    monkeypatch.setattr(patch_series_bodies, "classify_root_generation", classify)

    assert patch_series_bodies.is_canonical_cohort("repo", "frozen-root") is True
    assert classified_refs == ["frozen-root"]


def test_leaf_resolution_reuses_one_frozen_generation_snapshot(
    tmp_path, monkeypatch
):
    repo = _repo(tmp_path)
    branch = "ai-org/patch-series/frozen-leaf-resolution"
    git_wrapper.create_branch_with_files(
        repo,
        branch,
        "main",
        {
            patch_series_bodies.LEGACY_COVER_PATH: {},
            patch_series_bodies.LEGACY_ROOT_APPROACH_PATH: {},
        },
        commit_message="historical root",
    )
    real_classifier = patch_series_bodies.classify_root_generation
    classified_refs: list[str] = []

    def recording_classifier(repo_path, ref):
        classified_refs.append(ref)
        return real_classifier(repo_path, ref)

    monkeypatch.setattr(
        patch_series_bodies, "classify_root_generation", recording_classifier
    )

    assert patch_series_gate.resolved(repo, branch) is False
    assert classified_refs == [branch]


def test_v2_disposition_reaches_lifecycle_readers(tmp_path):
    repo = _repo(tmp_path)
    branch = "ai-org/patch-series/root-v2-readers"
    git_wrapper.create_branch_with_files(
        repo,
        branch,
        "main",
        {
            patch_series_bodies.COVER_PATH: "cover",
            patch_series_bodies.PROVENANCE_PATH: "provenance",
            patch_series_bodies.ROOT_APPROACH_PATH: "approach",
        },
        commit_message="complete root v2",
    )

    authored = code_worker.load_brief(repo, branch, ".")
    reviewed, review_error = review._read_json_from_git(
        repo, branch, patch_series_bodies.LEGACY_ROOT_APPROACH_PATH
    )

    assert authored["status"] == "producer_lifecycle_not_ready"
    assert reviewed is None
    assert "producer_lifecycle_not_ready" in review_error
    assert patch_series_gate.resolved(repo, branch) is False
    assert discovery._authorable_nodes(repo, branch, "root-v2-readers") == [
        (
            ".",
            ["producer_lifecycle_not_ready"],
            "ai-org/contrib/root-v2-readers",
        )
    ]


def test_acceptance_polling_reuses_the_classified_frozen_snapshot(monkeypatch):
    branch = "ai-org/contrib/frozen-acceptance"
    series_branch = "ai-org/patch-series/frozen-acceptance"
    frozen_oid = "d" * 40
    snapshot = type(
        "HistoricalSnapshot",
        (),
        {
            "generation": patch_series_bodies.ROOT_GENERATION_HISTORICAL,
            "lifecycle_ready": True,
            "frozen_oid": frozen_oid,
        },
    )()
    classifications = []
    observed = []

    monkeypatch.setattr(
        functional_check.git_wrapper, "branches", lambda *_args, **_kwargs: [branch]
    )
    monkeypatch.setattr(
        functional_check.producer_lifecycle,
        "has_implementation_submission",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        functional_check.announcements,
        "find_for_contrib",
        lambda *_args, **_kwargs: {
            "record": {"series_branch": series_branch, "node_path": "."}
        },
    )

    def classify(_repo, ref):
        classifications.append(ref)
        return snapshot

    monkeypatch.setattr(
        functional_check.patch_series_bodies, "classify_root_generation", classify
    )
    monkeypatch.setattr(
        functional_check.git_wrapper, "log_subjects", lambda *_args, **_kwargs: []
    )

    def judge(
        _repo,
        selected,
        *,
        patch_series_branch,
        node_key,
        root_generation_snapshot,
    ):
        observed.append(
            (
                selected,
                patch_series_branch,
                node_key,
                root_generation_snapshot,
            )
        )
        return {"ok": False, "reachable": False, "blockers": [], "notes": "checked"}

    monkeypatch.setattr(functional_check, "check", judge)

    result = functional_check.acceptance_pull("repo")

    assert result["notes"] == "checked"
    assert classifications == [series_branch]
    assert observed == [(branch, series_branch, "root", snapshot)]


@pytest.mark.parametrize(
    ("files", "status"),
    [
        (
            {
                patch_series_bodies.COVER_PATH: "cover",
                patch_series_bodies.PROVENANCE_PATH: "provenance",
                patch_series_bodies.ROOT_APPROACH_PATH: "approach",
            },
            patch_series_bodies.ROOT_DISPOSITION_V2_NOT_READY,
        ),
        (
            {
                patch_series_bodies.COVER_PATH: "cover",
                patch_series_bodies.PROVENANCE_PATH: "provenance",
                patch_series_bodies.ROOT_APPROACH_PATH: "approach",
                patch_series_bodies.LEGACY_ROOT_APPROACH_PATH: {},
            },
            patch_series_bodies.ROOT_DISPOSITION_INVALID,
        ),
    ],
)
def test_blocked_generation_transitions_do_not_move_review_or_reform_ref(
    tmp_path, files, status
):
    repo = _repo(tmp_path)
    branch = "ai-org/patch-series/blocked-root-reader"
    git_wrapper.create_branch_with_files(
        repo, branch, "main", files, commit_message="blocked root generation"
    )
    before = _git(repo, "rev-parse", branch)

    reviewed = review.run_patch_series_review(repo, branch)
    reformed = receive.reform_patch_series(repo, branch)

    assert reviewed.status == status
    assert reviewed.final_view["root_generation"]["source_oid"] == before
    assert reformed["status"] == status
    assert reformed["root_generation"]["source_oid"] == before
    assert _git(repo, "rev-parse", branch) == before


def test_review_classifies_the_captured_review_oid(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    branch = "ai-org/patch-series/frozen-review-reader"
    git_wrapper.create_branch_with_files(
        repo,
        branch,
        "main",
        {
            patch_series_bodies.COVER_PATH: "cover",
            patch_series_bodies.PROVENANCE_PATH: "provenance",
            patch_series_bodies.ROOT_APPROACH_PATH: "approach",
        },
        commit_message="blocked v2 review fixture",
    )
    reviewed_oid = _git(repo, "rev-parse", branch)
    real_classifier = patch_series_bodies.classify_root_generation
    classified_refs: list[str] = []

    def recording_classifier(repo_path, ref):
        classified_refs.append(ref)
        return real_classifier(repo_path, ref)

    monkeypatch.setattr(
        patch_series_bodies, "classify_root_generation", recording_classifier
    )

    result = review.run_patch_series_review(repo, branch)

    assert result.status == patch_series_bodies.ROOT_DISPOSITION_V2_NOT_READY
    assert classified_refs == [reviewed_oid]


def test_migrated_root_consumers_have_no_literal_direct_json_reads():
    root = Path(__file__).resolve().parents[1]
    consumers = [
        "ai_org/patchwork_queue/receive.py",
        "ai_org/patchwork_queue/review.py",
        "ai_org/patchwork_queue/patch_series_gate.py",
        "ai_org/patch_author/discovery.py",
        "ai_org/patch_author/code_worker.py",
        "ai_org/patch_author/producer_lifecycle.py",
        "ai_org/patch_author/functional_check.py",
        "ai_org/maintainer_merge/__init__.py",
        "ai_org/maintainer_merge/evidence.py",
    ]
    direct_root_reads = re.compile(
        r"(?:_read_json(?:_from_branch)?|git_wrapper\.(?:show_file|read_tree_file))"
        r"\([^\n]*(?:\"patch-series-cover-letter\.json\"|\"technical-approach-plan\.json\")"
    )

    offenders = [
        path
        for path in consumers
        if direct_root_reads.search((root / path).read_text(encoding="utf-8"))
        or "is_canonical_cohort(" in (root / path).read_text(encoding="utf-8")
    ]

    assert offenders == []
