from __future__ import annotations

import dataclasses
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from ai_org import git_wrapper
from ai_org.body_codec import BodyCodecClient, CodecFailure
import ai_org.patchwork_queue.receive as receive_module


def test_branches_lists_matching_local_branches(tmp_path):
    repo = _init_repo(tmp_path)
    _branch_with_commit(repo, "feature/alpha", "alpha")
    _branch_with_commit(repo, "feature/beta", "beta")
    _branch_with_commit(repo, "topic/gamma", "gamma")

    assert git_wrapper.branches(repo, "feature/*") == ["feature/alpha", "feature/beta"]
    assert git_wrapper.branches(repo, "missing/*") == []


def test_branch_exists_checks_local_branch(tmp_path):
    repo = _init_repo(tmp_path)
    _branch_with_commit(repo, "feature/alpha", "alpha")

    assert git_wrapper.branch_exists(repo, "feature/alpha") is True
    assert git_wrapper.branch_exists(repo, "feature/missing") is False


def test_log_subjects_returns_subjects_for_ref(tmp_path):
    repo = _init_repo(tmp_path)
    _branch_with_commit(repo, "feature/alpha", "alpha: first")
    _commit_on_branch(repo, "feature/alpha", "alpha: second")

    assert git_wrapper.log_subjects(repo, "feature/alpha") == [
        "alpha: second",
        "alpha: first",
        "base",
    ]
    assert git_wrapper.log_subjects(repo, "feature/missing") == []


def test_recent_commit_history_context_is_bounded_and_labeled(tmp_path):
    repo = _init_repo(tmp_path)
    long_body = "First sentence kept. " + ("Second sentence has many details. " * 20)
    _git(repo, "commit", "--allow-empty", "-m", "design: long record", "-m", long_body)
    _git(repo, "commit", "--allow-empty", "-m", "design: latest", "-m", "Latest rationale body.")

    history = git_wrapper.recent_commit_history(repo, max_commits=2, max_chars=500, max_body_chars=40)
    context = receive_module._repository_history_context(repo)

    assert context["label"] == "design record over time (repository history; commit messages are the repo's design record)"
    assert "must not fabricate requester intent" in context["provenance_discipline"]
    assert [commit["subject"] for commit in history["commits"]] == ["design: latest", "design: long record"]
    assert history["commits"][1]["body"].endswith("[truncated: commit body exceeded 40 characters]")
    assert "Second sentence" not in history["commits"][1]["body"]


def test_repository_constitution_context_is_bounded_and_labeled(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    module = repo / "module.py"
    module.write_text(
        '"""First complete paragraph.\n\n'
        + "Second complete paragraph has many details. " * 20
        + '\n\nThird complete paragraph."""\n',
        encoding="utf-8",
    )

    context = git_wrapper.repository_constitution_context(repo, max_chars=220, per_module_chars=180)

    assert context["label"] == git_wrapper.CONSTITUTION_LABEL
    assert context["bounds"]["max_chars"] == 220
    assert context["bounds"]["truncated"] is True
    assert len(context["projection"]) <= 220
    assert "[truncated:" in context["projection"]
    assert "the source of truth remains the repository files" in context["provenance_discipline"].lower()


def test_repository_constitution_projection_contains_lineage_memento_for_self_repo():
    repo = Path(__file__).resolve().parents[1]

    context = git_wrapper.repository_constitution_context(repo)

    assert "P7 STAMPED UNITS" in context["projection"]
    assert "checkpatch is not always right" in context["projection"]
    assert "Org-preheld lessons (projected from ORG_PREHELD_LESSONS)" in context["projection"]
    assert "docs/rfcs/RFC-0001-nested-rfc-lineage.md" in context["projection"]


def test_repository_constitution_projection_extracts_foreign_repo_docstring(tmp_path):
    repo = tmp_path / "foreign"
    repo.mkdir()
    package = repo / "product"
    package.mkdir()
    (package / "service.py").write_text(
        '"""Product constitution.\n\nTop-of-file docstrings define local operating rules."""\n\nVALUE = 1\n',
        encoding="utf-8",
    )

    context = git_wrapper.repository_constitution_context(repo)

    assert "## product/service.py" in context["projection"]
    assert "Product constitution." in context["projection"]
    assert "Top-of-file docstrings define local operating rules." in context["projection"]


def test_receive_injects_history_and_constitution_contexts_adjacent(tmp_path):
    repo = _init_repo(tmp_path)

    approach_context = receive_module._technical_approach_context(None, repo)
    keys = list(approach_context)
    assert keys.index("repository_constitution") == keys.index("repository_history") + 1
    assert approach_context["repository_history"]["label"].startswith("design record over time")
    assert approach_context["repository_constitution"]["label"] == git_wrapper.CONSTITUTION_LABEL

    prompt = receive_module._grounding_prompt(
        receive_module.entrance_defaults({"raw_request": "Build the thing."}),
        None,
        approach_context["repository_history"],
        approach_context["repository_constitution"],
    )
    history_index = prompt.index("Design record over time")
    constitution_index = prompt.index("Constitution, current")
    assert history_index < constitution_index
    assert git_wrapper.CONSTITUTION_LABEL in prompt


def test_has_subject_matches_subject_substring(tmp_path):
    repo = _init_repo(tmp_path)
    _branch_with_commit(repo, "feature/alpha", "alpha: first")
    _commit_on_branch(repo, "feature/alpha", "alpha: second")

    assert git_wrapper.has_subject(repo, "feature/alpha", "first") is True
    assert git_wrapper.has_subject(repo, "feature/alpha", "missing") is False
    assert git_wrapper.has_subject(repo, "feature/missing", "first") is False


def test_is_ancestor_checks_reachability(tmp_path):
    repo = _init_repo(tmp_path)
    _branch_with_commit(repo, "feature/alpha", "alpha")
    _branch_at(repo, "integration", "feature/alpha")
    _branch_with_commit(repo, "feature/beta", "beta")

    assert git_wrapper.is_ancestor(repo, "feature/alpha", "integration") is True
    assert git_wrapper.is_ancestor(repo, "feature/beta", "integration") is False
    assert git_wrapper.is_ancestor(repo, "feature/missing", "integration") is False
    assert git_wrapper.is_ancestor(repo, "feature/alpha", "missing") is False


def test_head_sha_returns_commit_sha_or_none(tmp_path):
    repo = _init_repo(tmp_path)
    _branch_with_commit(repo, "feature/alpha", "alpha")
    expected = _git(repo, "rev-parse", "feature/alpha").stdout.strip()

    assert git_wrapper.head_sha(repo, "feature/alpha") == expected
    assert git_wrapper.head_sha(repo, "feature/missing") is None


def test_current_default_and_merge_base(tmp_path):
    repo = _init_repo(tmp_path)
    base = git_wrapper.head_sha(repo, "main")
    _branch_with_commit(repo, "feature/alpha", "alpha")

    assert git_wrapper.current_branch(repo) == "main"
    assert git_wrapper.default_branch(repo) == "main"
    assert git_wrapper.merge_base(repo, "main", "feature/alpha") == base
    assert git_wrapper.merge_base(repo, "main", "feature/missing") is None


def test_create_branch_with_files_notes_and_dependency_graph(tmp_path):
    repo = _init_repo(tmp_path)
    git_wrapper.create_branch_with_files(
        repo,
        "patch_series/prep",
        "main",
        {"patch-series-cover-letter.json": {"title": "Prep"}},
        commit_message="patch_series: prep",
    )
    git_wrapper.create_branch_with_files(
        repo,
        "patch_series/behavior",
        "patch_series/prep",
        {"patch-series-cover-letter.json": {"title": "Behavior"}},
        commit_message="patch_series: behavior",
    )
    git_wrapper.create_branch_with_files(
        repo,
        "patch_series/docs",
        "main",
        {"patch-series-cover-letter.json": {"title": "Docs"}},
        commit_message="patch_series: docs",
    )

    git_wrapper.write_semantic(
        repo,
        "patch_series/behavior",
        {
            "change_kind": "behavior",
            "subsystem": "docs",
            "owner": "maintainer",
            "working_state": "green",
            "ignored": "not stored",
        },
    )

    semantic_target = git_wrapper.head_sha(repo, "patch_series/behavior")
    assert semantic_target is not None
    raw_note = _git(
        repo,
        "notes",
        f"--ref={git_wrapper.SEMANTIC_NOTE_REF}",
        "show",
        semantic_target,
    ).stdout
    envelope = json.loads(raw_note)
    assert list(envelope) == ["apiVersion", "body", "kind", "variant"]
    assert envelope["apiVersion"] == "ai-org-cue-body-v1"
    assert envelope["kind"] == "SemanticStatus"
    assert envelope["variant"] == "git-note"
    assert raw_note.endswith("\n") and not raw_note.endswith("\n\n")

    assert json.loads(git_wrapper.show_file(repo, "patch_series/behavior", "patch-series-cover-letter.json") or "{}") == {"title": "Behavior"}
    assert git_wrapper.read_semantic(repo, "patch_series/behavior") == {
        "change_kind": "behavior",
        "subsystem": "docs",
        "owner": "maintainer",
        "working_state": "green",
    }
    assert git_wrapper.dependency_graph(repo, ["patch_series/prep", "patch_series/behavior", "patch_series/docs"]) == [
        {"from": "patch_series/prep", "to": "patch_series/behavior"}
    ]
    assert git_wrapper.is_ancestor(repo, "patch_series/prep", "patch_series/docs") is False


def test_frozen_semantic_snapshot_keeps_exact_historical_note_bytes(tmp_path):
    repo = _init_repo(tmp_path)
    target = git_wrapper.head_sha(repo, "main")
    assert target is not None
    historical = '{"change_kind":"behavior","subsystem":"docs","owner":"maintainer","working_state":"green"}\n'
    _git(repo, "notes", f"--ref={git_wrapper.SEMANTIC_NOTE_REF}", "add", "-m", historical, target)

    snapshot = git_wrapper.freeze_semantic_snapshot(repo, "main")

    assert snapshot is not None and snapshot.blob is not None
    assert snapshot.target_oid == target
    assert snapshot.notes_ref_oid == _git(repo, "rev-parse", git_wrapper.SEMANTIC_NOTE_FULL_REF).stdout.strip()
    assert snapshot.blob.data == historical.encode()
    assert snapshot.blob.byte_length == len(historical.encode())

    replacement = '{"change_kind":"docs","subsystem":"docs","owner":"other","working_state":"red"}\n'
    _git(repo, "notes", f"--ref={git_wrapper.SEMANTIC_NOTE_REF}", "add", "-f", "-m", replacement, target)

    # The live ref moved, but the prior snapshot still names and reads the old
    # notes commit/blob. No read-time repair or rewrite occurred.
    assert _git(repo, "rev-parse", git_wrapper.SEMANTIC_NOTE_FULL_REF).stdout.strip() != snapshot.notes_ref_oid
    frozen_again = git_wrapper._exact_note_blob(repo, snapshot.notes_ref_oid, target)
    assert frozen_again is not None and frozen_again.data == historical.encode()


def test_prepared_semantic_note_publishes_by_cas_without_index_or_worktree_changes(tmp_path):
    repo = _init_repo(tmp_path)
    target = git_wrapper.head_sha(repo, "main")
    assert target is not None
    canonical = b'''apiVersion: "ai-org-cue-body-v1"
body: {
\tchange_kind: "behavior"
\towner: "maintainer"
\tsubsystem: "docs"
\tworking_state: "green"
}
kind: "SemanticStatus"
variant: "git-note"
'''
    before_status = _git(repo, "status", "--porcelain=v1").stdout
    before_tree = _git(repo, "write-tree").stdout.strip()

    prepared = git_wrapper._prepare_semantic_notes_commit(
        repo,
        target_oid=target,
        expected_notes_ref_oid="",
        canonical=canonical,
    )

    assert git_wrapper._git(repo, "show-ref", "--verify", "--quiet", git_wrapper.SEMANTIC_NOTE_FULL_REF).returncode != 0
    git_wrapper._publish_semantic_notes_commit(repo, prepared, "")
    first_tip = _git(repo, "rev-parse", git_wrapper.SEMANTIC_NOTE_FULL_REF).stdout.strip()
    assert first_tip == prepared
    assert _git(repo, "notes", f"--ref={git_wrapper.SEMANTIC_NOTE_REF}", "show", target).stdout == canonical.decode()

    stale = git_wrapper._prepare_semantic_notes_commit(
        repo,
        target_oid=target,
        expected_notes_ref_oid=first_tip,
        canonical=canonical.replace(b'"green"', b'"stale"'),
    )
    _git(repo, "notes", f"--ref={git_wrapper.SEMANTIC_NOTE_REF}", "add", "-f", "-m", "concurrent", target)
    concurrent_tip = _git(repo, "rev-parse", git_wrapper.SEMANTIC_NOTE_FULL_REF).stdout.strip()

    with pytest.raises(git_wrapper.GitBodyFailure) as raised:
        git_wrapper._publish_semantic_notes_commit(repo, stale, first_tip)

    assert raised.value.code == "GIT_CAS"
    assert raised.value.location == git_wrapper.SEMANTIC_NOTE_FULL_REF
    assert _git(repo, "rev-parse", git_wrapper.SEMANTIC_NOTE_FULL_REF).stdout.strip() == concurrent_tip
    assert _git(repo, "status", "--porcelain=v1").stdout == before_status
    assert _git(repo, "write-tree").stdout.strip() == before_tree


def test_semantic_consumer_dispatch_occurs_only_after_codec_validation(tmp_path):
    repo = _init_repo(tmp_path)
    target = git_wrapper.head_sha(repo, "main")
    assert target is not None
    historical = '{"change_kind":"behavior","subsystem":"docs","owner":"maintainer","working_state":"green"}\n'
    _git(repo, "notes", f"--ref={git_wrapper.SEMANTIC_NOTE_REF}", "add", "-m", historical, target)
    notes_before = _git(repo, "rev-parse", git_wrapper.SEMANTIC_NOTE_FULL_REF).stdout.strip()
    dispatched: list[dict[str, str]] = []

    class ValidatingCodec:
        def parse(self, context_id, data, *, expected):
            assert context_id == git_wrapper.SEMANTIC_CONTEXT_ID
            assert data == historical.encode()
            assert expected["kind"] == "SemanticStatus"
            return {
                "change_kind": "behavior",
                "subsystem": "docs",
                "owner": "maintainer",
                "working_state": "green",
            }

    accepted = git_wrapper.read_semantic_result(
        repo,
        "main",
        client=ValidatingCodec(),
        consumer=dispatched.append,
    )

    assert accepted.ok is True
    assert dispatched == [dict(accepted.body or {})]
    assert _git(repo, "rev-parse", git_wrapper.SEMANTIC_NOTE_FULL_REF).stdout.strip() == notes_before

    class RejectingCodec:
        def parse(self, *_args, **_kwargs):
            raise ValueError("schema rejected")

    rejected = git_wrapper.read_semantic_result(
        repo,
        "main",
        client=RejectingCodec(),
        consumer=dispatched.append,
    )

    assert rejected.status == "rejected"
    assert rejected.body is None
    assert dispatched == [dict(accepted.body or {})]
    assert _git(repo, "rev-parse", git_wrapper.SEMANTIC_NOTE_FULL_REF).stdout.strip() == notes_before


def test_supported_writer_floor_rejects_legacy_payload_before_note_mutation(tmp_path):
    repo = _init_repo(tmp_path)
    target = git_wrapper.head_sha(repo, "main")
    assert target is not None
    current = _semantic_canonical(working_state="green")
    prepared = git_wrapper._prepare_semantic_notes_commit(
        repo,
        target_oid=target,
        expected_notes_ref_oid="",
        canonical=current,
    )
    git_wrapper._publish_semantic_notes_commit(repo, prepared, "")
    notes_before = _git(repo, "rev-parse", git_wrapper.SEMANTIC_NOTE_FULL_REF).stdout.strip()

    class LegacyWriter:
        def prepare(self, *_args, **_kwargs):
            return SimpleNamespace(
                canonical=SimpleNamespace(
                    data=b'{"change_kind":"behavior","subsystem":"docs","owner":"maintainer","working_state":"legacy"}\n'
                )
            )

    result = git_wrapper.write_semantic_result(
        repo,
        "main",
        {
            "change_kind": "behavior",
            "subsystem": "docs",
            "owner": "maintainer",
            "working_state": "legacy",
        },
        client=LegacyWriter(),
    )

    assert result.status == "rejected"
    assert isinstance(result.failure, git_wrapper.GitBodyFailure)
    assert result.failure.code == "WRITER_FLOOR"
    assert _git(repo, "rev-parse", git_wrapper.SEMANTIC_NOTE_FULL_REF).stdout.strip() == notes_before
    assert _git(repo, "notes", f"--ref={git_wrapper.SEMANTIC_NOTE_REF}", "show", target).stdout == current.decode()

    class CanonicalLookingInvalidWriter:
        def prepare(self, *_args, **_kwargs):
            return SimpleNamespace(
                canonical=SimpleNamespace(
                    data=(
                        b'{"apiVersion": "ai-org-cue-body-v1", "body": {"x": "y"}, '
                        b'"kind": "SemanticStatus", "variant": "git-note"}\n'
                    )
                )
            )

    invalid = git_wrapper.write_semantic_result(
        repo,
        "main",
        {
            "change_kind": "behavior",
            "subsystem": "docs",
            "owner": "maintainer",
            "working_state": "legacy",
        },
        client=CanonicalLookingInvalidWriter(),
    )
    assert invalid.status == "rejected"
    assert isinstance(invalid.failure, git_wrapper.GitBodyFailure)
    assert invalid.failure.code == "WRITER_FLOOR"
    assert _git(repo, "rev-parse", git_wrapper.SEMANTIC_NOTE_FULL_REF).stdout.strip() == notes_before


def test_real_codec_reads_historical_json_without_rewriting_and_matches_canonical(tmp_path):
    client = BodyCodecClient()
    labels = {
        "change_kind": "behavior",
        "subsystem": "docs",
        "owner": "maintainer",
        "working_state": "green",
    }

    historical_root = tmp_path / "historical"
    historical_root.mkdir()
    historical_repo = _init_repo(historical_root)
    historical_target = git_wrapper.head_sha(historical_repo, "main")
    assert historical_target is not None
    historical_payload = json.dumps(labels, sort_keys=True, separators=(",", ":"))
    _git(
        historical_repo,
        "notes",
        f"--ref={git_wrapper.SEMANTIC_NOTE_REF}",
        "add",
        "-m",
        historical_payload,
        historical_target,
    )
    historical_notes_oid = _git(
        historical_repo, "rev-parse", git_wrapper.SEMANTIC_NOTE_FULL_REF
    ).stdout.strip()
    historical_status = _git(historical_repo, "status", "--porcelain=v1").stdout
    historical_tree = _git(historical_repo, "write-tree").stdout.strip()

    historical = git_wrapper.read_semantic_result(
        historical_repo,
        "main",
        client=client,
    )

    assert historical.ok is True and dict(historical.body or {}) == labels
    assert git_wrapper.head_sha(historical_repo, "main") == historical_target
    assert _git(historical_repo, "rev-parse", git_wrapper.SEMANTIC_NOTE_FULL_REF).stdout.strip() == historical_notes_oid
    assert _git(historical_repo, "status", "--porcelain=v1").stdout == historical_status
    assert _git(historical_repo, "write-tree").stdout.strip() == historical_tree

    canonical_root = tmp_path / "canonical"
    canonical_root.mkdir()
    canonical_repo = _init_repo(canonical_root)
    publication = git_wrapper.write_semantic_result(canonical_repo, "main", labels, client=client)
    canonical = git_wrapper.read_semantic_result(canonical_repo, "main", client=client)

    assert publication.ok is True
    assert canonical.ok is True
    assert canonical.body == historical.body


def test_real_codec_publishes_unicode_note_as_exact_bytes(tmp_path):
    repo = _init_repo(tmp_path)
    client = BodyCodecClient()
    labels = {
        "change_kind": "振る舞い",
        "subsystem": "codec/境界",
        "owner": "維持担当",
        "working_state": "緑✅",
    }

    prepared = git_wrapper.prepare_semantic_note(repo, "main", labels, client=client)
    publication = git_wrapper.publish_semantic_note(repo, prepared)
    snapshot = git_wrapper.freeze_semantic_snapshot(repo, "main")

    assert publication.ok is True
    assert snapshot is not None and snapshot.blob is not None
    assert snapshot.blob.data == prepared.canonical
    assert snapshot.blob.sha256 == publication.sha256
    assert "維持担当".encode("utf-8") in snapshot.blob.data
    assert b"\r" not in snapshot.blob.data
    assert snapshot.blob.data.count(b"\n") == 1 and snapshot.blob.data.endswith(b"\n")
    assert git_wrapper.read_semantic(repo, "main", client=client) == labels


def test_real_codec_wrong_kind_failure_is_located_and_has_no_body(tmp_path):
    repo = _init_repo(tmp_path)
    target = git_wrapper.head_sha(repo, "main")
    assert target is not None
    wrong = json.dumps(
        {
            "apiVersion": "ai-org-cue-body-v1",
            "body": {
                "change_kind": "behavior",
                "owner": "maintainer",
                "subsystem": "docs",
                "working_state": "green",
            },
            "kind": "WrongKind",
            "variant": "git-note",
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    _git(repo, "notes", f"--ref={git_wrapper.SEMANTIC_NOTE_REF}", "add", "-m", wrong, target)

    result = git_wrapper.read_semantic_result(repo, "main", client=BodyCodecClient())

    assert result.status == "rejected" and result.body is None
    assert isinstance(result.failure, CodecFailure)
    assert result.failure.code == "IDENTITY"
    assert result.failure.location == "/kind"
    assert not hasattr(result.failure, "payload")


def test_create_branch_with_files_can_delete_inherited_paths(tmp_path):
    repo = _init_repo(tmp_path)
    git_wrapper.create_branch_with_files(
        repo,
        "patch_series/parent",
        "main",
        {"series-coverage-ledger.json": {"children": []}, "patch-series-cover-letter.json": {"title": "Parent"}},
        commit_message="patch_series: parent",
    )
    git_wrapper.create_branch_with_files(
        repo,
        "patch_series/child",
        "patch_series/parent",
        {"patch-series-cover-letter.json": {"title": "Child"}},
        commit_message="patch_series: child",
        deletions=["series-coverage-ledger.json"],
    )

    assert git_wrapper.file_exists(repo, "patch_series/child", "patch-series-cover-letter.json") is True
    assert git_wrapper.file_exists(repo, "patch_series/child", "series-coverage-ledger.json") is False


def test_serial_registry_uses_tags_and_is_idempotent(tmp_path):
    repo = _init_repo(tmp_path)
    _branch_with_commit(repo, "ai-org/patch-series/one", "patch_series: direction-ok")
    first_commit = _git(repo, "rev-parse", "ai-org/patch-series/one").stdout.strip()

    assert git_wrapper.next_serial(repo) == "0001"
    assert git_wrapper.ensure_serial(repo, "ai-org/patch-series/one") == "0001"
    assert git_wrapper.ensure_serial(repo, "ai-org/patch-series/one") == "0001"
    assert git_wrapper.list_serials(repo) == [{"tag": "ai-org/serial/0001", "number": 1, "commit": first_commit}]

    _branch_with_commit(repo, "ai-org/patch-series/two", "patch_series: direction-ok")
    assert git_wrapper.next_serial(repo) == "0002"
    assert git_wrapper.ensure_serial(repo, "ai-org/patch-series/two") == "0002"
    assert [item["tag"] for item in git_wrapper.list_serials(repo)] == ["ai-org/serial/0001", "ai-org/serial/0002"]


def test_engine_commits_use_explicit_identity_without_ambient_config(tmp_path, monkeypatch):
    # The CI disease: runners have no global git config at all, and developer
    # machines have a real email. Neither may reach an engine commit.
    _strip_ambient_git_identity(tmp_path, monkeypatch)
    repo = _init_repo_without_identity(tmp_path)

    result = git_wrapper.commit_files(repo, "main", {"note.txt": "hello\n"}, subject="engine: note")

    author = git_wrapper.commit_author(repo, result["commit"])
    assert author == {
        "name": git_wrapper.DEFAULT_ENGINE_IDENTITY_NAME,
        "email": git_wrapper.DEFAULT_ENGINE_IDENTITY_EMAIL,
    }
    marker = git_wrapper.commit_empty(repo, "main", "engine: marker")
    assert git_wrapper.commit_author(repo, marker["commit"])["email"] == git_wrapper.DEFAULT_ENGINE_IDENTITY_EMAIL


def test_engine_identity_is_env_overridable(tmp_path, monkeypatch):
    _strip_ambient_git_identity(tmp_path, monkeypatch)
    monkeypatch.setenv(git_wrapper.ENGINE_IDENTITY_NAME_ENV, "Org Robot")
    monkeypatch.setenv(git_wrapper.ENGINE_IDENTITY_EMAIL_ENV, "42+org-robot@users.noreply.github.com")
    repo = _init_repo_without_identity(tmp_path)

    result = git_wrapper.commit_files(repo, "main", {"note.txt": "hello\n"}, subject="engine: note")

    assert git_wrapper.commit_author(repo, result["commit"]) == {
        "name": "Org Robot",
        "email": "42+org-robot@users.noreply.github.com",
    }


def test_create_ref_with_files_accepts_contributor_identity_and_extra_parents(tmp_path, monkeypatch):
    _strip_ambient_git_identity(tmp_path, monkeypatch)
    repo = _init_repo_without_identity(tmp_path)
    base = _git(repo, "rev-parse", "main").stdout.strip()
    first = git_wrapper.create_ref_with_files(
        repo,
        "refs/ai-org/claims/demo",
        {"claim.json": {"status": "claimed"}},
        subject="claim: demo",
        parent=base,
        identity={"name": "Claimant", "email": "7+claimant@users.noreply.github.com"},
    )
    superseding = git_wrapper.create_ref_with_files(
        repo,
        "refs/ai-org/claims/demo",
        {"claim.json": {"status": "claimed"}},
        subject="claim: supersede demo",
        parent=base,
        extra_parents=[first["commit"]],
        identity={"name": "Successor", "email": "8+successor@users.noreply.github.com"},
    )

    assert git_wrapper.commit_author(repo, first["commit"]) == {
        "name": "Claimant",
        "email": "7+claimant@users.noreply.github.com",
    }
    assert git_wrapper.parent_commits(repo, superseding["commit"]) == [base, first["commit"]]
    assert git_wrapper.commit_author(repo, superseding["commit"])["name"] == "Successor"


def test_terminal_request_pair_prepares_alias_free_tree_and_publishes_by_create_cas(tmp_path):
    repo = _init_repo(tmp_path)
    parent = git_wrapper.head_sha(repo, "main")
    assert parent is not None
    ref = "refs/ai-org/request-outcomes/request-7"
    provenance = {
        "request_id": "request-7",
        "payload_sha256": "0" * 64,
        "raw_request": "Build it.",
        "request_payload": {"raw_request": "Build it."},
        "memento": "provenance",
    }
    outcome = {
        "request_id": "request-7",
        "status": "needs_work",
        "result": {"ok": False},
        "memento": "outcome",
    }
    codec = _TerminalCodec()

    prepared = git_wrapper.prepare_terminal_request(
        repo, ref, provenance, outcome, parent=parent, client=codec
    )

    assert codec.prepared == [
        git_wrapper.REQUEST_PROVENANCE_CONTEXT_ID,
        git_wrapper.REQUEST_OUTCOME_CONTEXT_ID,
    ]
    assert _git(repo, "ls-tree", "--name-only", prepared.commit_oid).stdout.splitlines() == [
        git_wrapper.REQUEST_OUTCOME_PATH,
        git_wrapper.REQUEST_PROVENANCE_PATH,
    ]
    assert git_wrapper.publish_terminal_request(repo, prepared).status == "created"
    assert git_wrapper.read_request_provenance(repo, ref, client=codec)["request_id"] == "request-7"
    assert git_wrapper.read_request_outcome(repo, ref, client=codec)["status"] == "needs_work"

    replacement = {**outcome, "status": "rejected"}
    successor = git_wrapper.prepare_terminal_request(
        repo, ref, provenance, replacement, parent=prepared.commit_oid,
        expected_ref_oid=prepared.commit_oid, client=codec,
    )
    injected = git_wrapper.publish_terminal_request(repo, successor, inject_failure=True)
    assert injected.status == "rejected"
    assert git_wrapper.head_sha(repo, ref) == prepared.commit_oid
    assert git_wrapper.read_request_outcome(repo, ref, client=codec)["status"] == "needs_work"

    _git(repo, "update-ref", ref, parent, prepared.commit_oid)
    stale = git_wrapper.publish_terminal_request(repo, successor)
    assert stale.status == "rejected"
    assert stale.failure is not None and stale.failure.code == "GIT_CAS"
    assert git_wrapper.head_sha(repo, ref) == parent


def test_terminal_request_pair_rejects_identity_before_tree_or_ref_publication(tmp_path):
    repo = _init_repo(tmp_path)
    parent = git_wrapper.head_sha(repo, "main")
    assert parent is not None
    before = _git(repo, "count-objects", "-v").stdout

    with pytest.raises(git_wrapper.GitBodyFailure) as raised:
        git_wrapper.prepare_terminal_request(
            repo,
            "refs/ai-org/request-outcomes/a",
            {"request_id": "a"},
            {"request_id": "b"},
            parent=parent,
            client=_TerminalCodec(),
        )

    assert raised.value.code == "PAIR_MISMATCH"
    assert git_wrapper.head_sha(repo, "refs/ai-org/request-outcomes/a") is None
    assert _git(repo, "count-objects", "-v").stdout == before


@pytest.mark.parametrize(
    ("change", "expected_rule"),
    [
        ({"ref": "refs/heads/escaped"}, "request-outcome-ref"),
        ({"parent_oid": "1" * 40}, "prepared-parent"),
        ({"tree_oid": "2" * 40}, "prepared-tree"),
        ({"provenance": b"tampered\n"}, "prepared-body"),
        ({"outcome": b"tampered\n"}, "prepared-body"),
    ],
)
def test_terminal_request_publication_rejects_forged_prepared_units(
    tmp_path, change, expected_rule
):
    repo = _init_repo(tmp_path)
    parent = git_wrapper.head_sha(repo, "main")
    assert parent is not None
    ref = "refs/ai-org/request-outcomes/verified"
    prepared = git_wrapper.prepare_terminal_request(
        repo,
        ref,
        {"request_id": "verified"},
        {"request_id": "verified"},
        parent=parent,
        client=_TerminalCodec(),
    )
    forged = dataclasses.replace(prepared, **change)

    result = git_wrapper.publish_terminal_request(repo, forged)

    assert result.status == "rejected"
    assert result.failure is not None
    assert result.failure.code in {"GIT_IDENTITY", "GIT_PARENT"}
    assert result.failure.rule == expected_rule
    assert git_wrapper.head_sha(repo, ref) is None
    assert git_wrapper.head_sha(repo, "refs/heads/escaped") is None


def test_terminal_request_publication_revalidates_pair_from_prepared_bodies(tmp_path):
    repo = _init_repo(tmp_path)
    parent = git_wrapper.head_sha(repo, "main")
    assert parent is not None
    ref = "refs/ai-org/request-outcomes/forged-pair"
    codec = _TerminalCodec()
    prepared = git_wrapper.prepare_terminal_request(
        repo,
        ref,
        {"request_id": "provenance"},
        {"request_id": "provenance"},
        parent=parent,
        client=codec,
    )
    mismatched_outcome = codec.prepare(
        git_wrapper.REQUEST_OUTCOME_CONTEXT_ID,
        {"request_id": "outcome"},
        expected=git_wrapper.REQUEST_OUTCOME_CONTRACT,
    ).canonical.data
    outcome_path = tmp_path / "mismatched-outcome.cue"
    outcome_path.write_bytes(mismatched_outcome)
    outcome_oid = _git(repo, "hash-object", "-w", str(outcome_path)).stdout.strip()
    entries = _git(repo, "ls-tree", prepared.tree_oid).stdout.splitlines()
    replacement = f"100644 blob {outcome_oid}\t{git_wrapper.REQUEST_OUTCOME_PATH}"
    tree_input = "\n".join(
        replacement if line.endswith(f"\t{git_wrapper.REQUEST_OUTCOME_PATH}") else line
        for line in entries
    ) + "\n"
    tree_oid = subprocess.run(
        ["git", "-C", str(repo), "mktree"],
        check=True,
        input=tree_input,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ).stdout.strip()
    commit_oid = _git(
        repo, "commit-tree", tree_oid, "-p", parent, "-m", "forged mismatched pair"
    ).stdout.strip()
    forged = dataclasses.replace(
        prepared,
        commit_oid=commit_oid,
        tree_oid=tree_oid,
        outcome=bytes(mismatched_outcome),
    )

    result = git_wrapper.publish_terminal_request(repo, forged)

    assert result.status == "rejected"
    assert result.failure is not None
    assert result.failure.code == "PAIR_MISMATCH"
    assert result.failure.rule == "prepared-paired-request-id"
    assert git_wrapper.head_sha(repo, ref) is None


def test_terminal_request_publication_rejects_forged_commit_identity_after_pair_validation(
    tmp_path,
):
    repo = _init_repo(tmp_path)
    parent = git_wrapper.head_sha(repo, "main")
    assert parent is not None
    ref = "refs/ai-org/request-outcomes/forged-identity"
    prepared = git_wrapper.prepare_terminal_request(
        repo,
        ref,
        {"request_id": "forged-identity"},
        {"request_id": "forged-identity"},
        parent=parent,
        client=_TerminalCodec(),
    )
    forged_commit = _git(
        repo,
        "commit-tree",
        prepared.tree_oid,
        "-p",
        parent,
        "-m",
        "forged identity",
    ).stdout.strip()

    result = git_wrapper.publish_terminal_request(
        repo, dataclasses.replace(prepared, commit_oid=forged_commit)
    )

    assert result.status == "rejected"
    assert result.failure is not None
    assert (result.failure.code, result.failure.rule) == (
        "GIT_IDENTITY",
        "commit-identity",
    )
    assert git_wrapper.head_sha(repo, ref) is None


@pytest.mark.parametrize(
    "ref",
    [
        "refs/ai-org/request-outcomes/",
        "refs/ai-org/request-outcomes/../escaped",
        "refs/ai-org/request-outcomes/request.lock",
    ],
)
def test_terminal_request_rejects_invalid_ref_before_emitting_bodies(tmp_path, ref):
    repo = _init_repo(tmp_path)
    parent = git_wrapper.head_sha(repo, "main")
    assert parent is not None
    codec = _TerminalCodec()
    objects_before = _git(repo, "count-objects", "-v").stdout

    with pytest.raises(git_wrapper.GitBodyFailure) as raised:
        git_wrapper.prepare_terminal_request(
            repo,
            ref,
            {"request_id": "a"},
            {"request_id": "a"},
            parent=parent,
            client=codec,
        )

    assert raised.value.code == "GIT_IDENTITY"
    assert codec.prepared == []
    assert _git(repo, "count-objects", "-v").stdout == objects_before


@pytest.mark.parametrize(
    ("failure", "provenance", "outcome", "parent", "expected_code"),
    [
        (None, {"request_id": "a"}, {"request_id": "b"}, "HEAD", "PAIR_MISMATCH"),
        (None, {"request_id": "a"}, {"request_id": "a"}, "missing", "GIT_PARENT"),
        ("outcome", {"request_id": "a"}, {"request_id": "a"}, "HEAD", "BODY_VALIDATION"),
    ],
)
def test_terminal_request_preflight_failures_leave_ref_unpublished(
    tmp_path, failure, provenance, outcome, parent, expected_code
):
    repo = _init_repo(tmp_path)
    ref = "refs/ai-org/request-outcomes/a"
    resolved_parent = git_wrapper.head_sha(repo, "main") if parent == "HEAD" else parent
    codec = _FailingTerminalCodec(failure)
    objects_before = _git(repo, "count-objects", "-v").stdout

    with pytest.raises(git_wrapper.GitBodyFailure) as raised:
        git_wrapper.prepare_terminal_request(
            repo, ref, provenance, outcome, parent=resolved_parent, client=codec,
        )

    assert raised.value.code == expected_code
    assert git_wrapper.head_sha(repo, ref) is None
    assert _git(repo, "count-objects", "-v").stdout == objects_before


@pytest.mark.parametrize(
    "identity",
    [
        {"name": "Invalid\nIdentity", "email": "not-an-email"},
        {"name": "Real Mail", "email": "real@example.com"},
    ],
)
def test_terminal_request_rejects_invalid_identity_before_emitting_bodies(tmp_path, identity):
    repo = _init_repo(tmp_path)
    parent = git_wrapper.head_sha(repo, "main")
    assert parent is not None
    codec = _TerminalCodec()
    objects_before = _git(repo, "count-objects", "-v").stdout

    with pytest.raises(git_wrapper.GitBodyFailure) as raised:
        git_wrapper.prepare_terminal_request(
            repo,
            "refs/ai-org/request-outcomes/a",
            {"request_id": "a"},
            {"request_id": "a"},
            parent=parent,
            identity=identity,
            client=codec,
        )

    assert raised.value.code == "GIT_IDENTITY"
    assert codec.prepared == []
    assert _git(repo, "count-objects", "-v").stdout == objects_before


def test_terminal_request_publication_revalidates_pair_identity(tmp_path):
    repo = _init_repo(tmp_path)
    parent = git_wrapper.head_sha(repo, "main")
    assert parent is not None
    ref = "refs/ai-org/request-outcomes/pair-a"
    codec = _TerminalCodec()
    prepared = git_wrapper.prepare_terminal_request(
        repo,
        ref,
        {"request_id": "pair-a"},
        {"request_id": "pair-a", "status": "needs_work"},
        parent=parent,
        client=codec,
    )
    mismatched_outcome = codec.prepare(
        git_wrapper.REQUEST_OUTCOME_CONTEXT_ID,
        {"request_id": "pair-b", "status": "needs_work"},
        expected=git_wrapper.REQUEST_OUTCOME_CONTRACT,
    ).canonical.data
    forged_commit = git_wrapper.create_ref_with_files(
        repo,
        ref,
        {
            git_wrapper.REQUEST_PROVENANCE_PATH: prepared.provenance.decode(),
            git_wrapper.REQUEST_OUTCOME_PATH: mismatched_outcome.decode(),
        },
        subject="forged mismatched pair",
        parent=parent,
        update_ref=False,
    )["commit"]
    forged_tree = _git(repo, "rev-parse", f"{forged_commit}^{{tree}}").stdout.strip()
    forged = dataclasses.replace(
        prepared,
        commit_oid=forged_commit,
        tree_oid=forged_tree,
        outcome=mismatched_outcome,
    )

    result = git_wrapper.publish_terminal_request(repo, forged)

    assert result.status == "rejected"
    assert result.failure is not None
    assert result.failure.code == "PAIR_MISMATCH"
    assert git_wrapper.head_sha(repo, ref) is None


@pytest.mark.parametrize("expected_ref_oid", ["1" * 41, "1" * 64])
def test_terminal_request_rejects_wrong_repository_oid_width_before_emitting_bodies(
    tmp_path, expected_ref_oid
):
    repo = _init_repo(tmp_path)
    parent = git_wrapper.head_sha(repo, "main")
    assert parent is not None
    codec = _TerminalCodec()
    objects_before = _git(repo, "count-objects", "-v").stdout

    with pytest.raises(git_wrapper.GitBodyFailure) as raised:
        git_wrapper.prepare_terminal_request(
            repo,
            "refs/ai-org/request-outcomes/a",
            {"request_id": "a"},
            {"request_id": "a"},
            parent=parent,
            expected_ref_oid=expected_ref_oid,
            client=codec,
        )

    assert raised.value.code == "GIT_IDENTITY"
    assert codec.prepared == []
    assert _git(repo, "count-objects", "-v").stdout == objects_before


def test_terminal_request_create_uses_sha256_null_oid(tmp_path):
    repo = _init_repo(tmp_path, object_format="sha256")
    parent = git_wrapper.head_sha(repo, "main")
    assert parent is not None and len(parent) == 64
    ref = "refs/ai-org/request-outcomes/sha256"
    prepared = git_wrapper.prepare_terminal_request(
        repo,
        ref,
        {"request_id": "sha256"},
        {"request_id": "sha256", "status": "needs_work"},
        parent=parent,
        client=_TerminalCodec(),
    )

    result = git_wrapper.publish_terminal_request(repo, prepared)

    assert result.status == "created"
    assert result.commit_oid == prepared.commit_oid
    assert git_wrapper.head_sha(repo, ref) == prepared.commit_oid


def test_terminal_request_cas_requires_expected_tip_as_successor_parent(tmp_path):
    repo = _init_repo(tmp_path)
    base = git_wrapper.head_sha(repo, "main")
    assert base is not None
    ref = "refs/ai-org/request-outcomes/a"
    provenance = {"request_id": "a"}
    outcome = {"request_id": "a"}
    first = git_wrapper.prepare_terminal_request(
        repo, ref, provenance, outcome, parent=base, client=_TerminalCodec(),
    )
    assert git_wrapper.publish_terminal_request(repo, first).status == "created"
    unrelated = _git(repo, "commit-tree", f"{base}^{{tree}}", "-p", base, "-m", "unrelated").stdout.strip()
    objects_before = _git(repo, "count-objects", "-v").stdout

    with pytest.raises(git_wrapper.GitBodyFailure) as raised:
        git_wrapper.prepare_terminal_request(
            repo, ref, provenance, outcome, parent=unrelated,
            expected_ref_oid=first.commit_oid, client=_TerminalCodec(),
        )

    assert raised.value.code == "GIT_PARENT"
    assert git_wrapper.head_sha(repo, ref) == first.commit_oid
    assert _git(repo, "count-objects", "-v").stdout == objects_before


def test_terminal_request_readers_strictly_import_paired_historical_json(tmp_path):
    repo = _init_repo(tmp_path)
    parent = git_wrapper.head_sha(repo, "main")
    assert parent is not None
    ref = "refs/ai-org/request-outcomes/legacy"
    provenance = {"request_id": "legacy", "payload_sha256": "0" * 64}
    outcome = {"request_id": "legacy", "status": "needs_work"}
    historical = git_wrapper.create_ref_with_files(
        repo, ref,
        {
            git_wrapper.REQUEST_PROVENANCE_ALIASES[0]: provenance,
            git_wrapper.REQUEST_OUTCOME_ALIASES[0]: outcome,
            "historical-audit.txt": "retain me\n",
        },
        subject="historical outcome", parent=parent,
    )
    codec = _TerminalCodec()

    assert git_wrapper.read_request_provenance(repo, ref, client=codec) == provenance
    assert git_wrapper.read_request_outcome(repo, ref, client=codec) == outcome
    assert git_wrapper.read_terminal_request(repo, ref, client=codec) == (provenance, outcome)
    assert git_wrapper.head_sha(repo, ref) == historical["commit"]

    complete_provenance = {
        **provenance, "raw_request": "legacy", "request_payload": {}, "memento": "p",
    }
    complete_outcome = {**outcome, "result": {}, "memento": "o"}
    successor = git_wrapper.prepare_terminal_request(
        repo, ref, complete_provenance, complete_outcome,
        parent=historical["commit"], expected_ref_oid=historical["commit"], client=codec,
    )
    assert git_wrapper.publish_terminal_request(repo, successor).status == "updated"
    paths = _git(repo, "ls-tree", "--name-only", successor.commit_oid).stdout.splitlines()
    assert paths == [
        "historical-audit.txt",
        git_wrapper.REQUEST_OUTCOME_PATH,
        git_wrapper.REQUEST_PROVENANCE_PATH,
    ]


def test_terminal_request_injected_publication_failure_keeps_historical_tree_unpublished(
    tmp_path,
):
    repo = _init_repo(tmp_path)
    parent = git_wrapper.head_sha(repo, "main")
    assert parent is not None
    ref = "refs/ai-org/request-outcomes/historical-publication-failure"
    provenance = {"request_id": "historical-publication-failure"}
    outcome = {"request_id": "historical-publication-failure", "status": "needs_work"}
    historical = git_wrapper.create_ref_with_files(
        repo,
        ref,
        {
            git_wrapper.REQUEST_PROVENANCE_ALIASES[0]: provenance,
            git_wrapper.REQUEST_OUTCOME_ALIASES[0]: outcome,
            "historical-audit.txt": "retain me\n",
        },
        subject="historical outcome",
        parent=parent,
    )
    codec = _TerminalCodec()
    prepared = git_wrapper.prepare_terminal_request(
        repo,
        ref,
        provenance,
        {**outcome, "status": "rejected"},
        parent=historical["commit"],
        expected_ref_oid=historical["commit"],
        client=codec,
    )

    result = git_wrapper.publish_terminal_request(repo, prepared, inject_failure=True)

    assert result.status == "rejected"
    assert result.failure is not None and result.failure.code == "GIT_PUBLICATION"
    assert git_wrapper.head_sha(repo, ref) == historical["commit"]
    assert _git(repo, "ls-tree", "--name-only", ref).stdout.splitlines() == [
        "historical-audit.txt",
        git_wrapper.REQUEST_OUTCOME_ALIASES[0],
        git_wrapper.REQUEST_PROVENANCE_ALIASES[0],
    ]
    assert git_wrapper.show_file(repo, ref, git_wrapper.REQUEST_OUTCOME_PATH) is None
    assert git_wrapper.show_file(repo, ref, git_wrapper.REQUEST_PROVENANCE_PATH) is None


def test_terminal_request_paired_historical_import_rejects_mismatched_identity_without_rewrite(tmp_path):
    repo = _init_repo(tmp_path)
    parent = git_wrapper.head_sha(repo, "main")
    assert parent is not None
    ref = "refs/ai-org/request-outcomes/legacy"
    historical = git_wrapper.create_ref_with_files(
        repo, ref,
        {
            git_wrapper.REQUEST_PROVENANCE_ALIASES[0]: {"request_id": "provenance"},
            git_wrapper.REQUEST_OUTCOME_ALIASES[0]: {"request_id": "outcome"},
        },
        subject="historical mismatched outcome", parent=parent,
    )

    for reader in (
        git_wrapper.read_request_provenance,
        git_wrapper.read_request_outcome,
        git_wrapper.read_terminal_request,
    ):
        with pytest.raises(git_wrapper.GitBodyFailure) as raised:
            reader(repo, ref, client=_TerminalCodec())
        assert raised.value.code == "PAIR_MISMATCH"
    assert git_wrapper.head_sha(repo, ref) == historical["commit"]

    with pytest.raises(git_wrapper.GitBodyFailure) as prepare_raised:
        git_wrapper.prepare_terminal_request(
            repo, ref,
            {
                "request_id": "legacy", "payload_sha256": "0" * 64,
                "raw_request": "replacement", "request_payload": {}, "memento": "p",
            },
            {"request_id": "legacy", "status": "needs_work", "result": {}, "memento": "o"},
            parent=historical["commit"], expected_ref_oid=historical["commit"],
            client=_TerminalCodec(),
        )

    assert prepare_raised.value.code == "PAIR_MISMATCH"
    assert git_wrapper.head_sha(repo, ref) == historical["commit"]


def test_terminal_request_public_readers_reject_incomplete_historical_pair(tmp_path):
    repo = _init_repo(tmp_path)
    parent = git_wrapper.head_sha(repo, "main")
    assert parent is not None
    ref = "refs/ai-org/request-outcomes/incomplete"
    historical = git_wrapper.create_ref_with_files(
        repo,
        ref,
        {git_wrapper.REQUEST_PROVENANCE_ALIASES[0]: {"request_id": "incomplete"}},
        subject="incomplete historical outcome",
        parent=parent,
    )

    for reader in (git_wrapper.read_request_provenance, git_wrapper.read_request_outcome):
        with pytest.raises(git_wrapper.GitBodyFailure) as raised:
            reader(repo, ref, client=_TerminalCodec())
        assert raised.value.rule == "exactly-one-canonical-or-historical-body"
    assert git_wrapper.head_sha(repo, ref) == historical["commit"]


def test_terminal_request_public_readers_reject_mixed_pair_representations(tmp_path):
    repo = _init_repo(tmp_path)
    parent = git_wrapper.head_sha(repo, "main")
    assert parent is not None
    ref = "refs/ai-org/request-outcomes/mixed"
    codec = _TerminalCodec()
    canonical_provenance = codec.prepare(
        git_wrapper.REQUEST_PROVENANCE_CONTEXT_ID,
        {"request_id": "mixed"},
        expected=git_wrapper.REQUEST_PROVENANCE_CONTRACT,
    ).canonical.data.decode()
    historical = git_wrapper.create_ref_with_files(
        repo,
        ref,
        {
            git_wrapper.REQUEST_PROVENANCE_PATH: canonical_provenance,
            git_wrapper.REQUEST_OUTCOME_ALIASES[0]: {"request_id": "mixed"},
        },
        subject="mixed historical outcome",
        parent=parent,
    )

    for reader in (git_wrapper.read_request_provenance, git_wrapper.read_request_outcome):
        with pytest.raises(git_wrapper.GitBodyFailure) as raised:
            reader(repo, ref, client=codec)
        assert raised.value.code == "PAIR_MISMATCH"
        assert raised.value.rule == "paired-representation"
    assert git_wrapper.head_sha(repo, ref) == historical["commit"]


class _TerminalCodec:
    def __init__(self):
        self.prepared: list[str] = []

    def prepare(self, context_id, value, *, expected):
        self.prepared.append(context_id)
        envelope = {
            "apiVersion": expected["apiVersion"],
            "body": dict(value),
            "kind": expected["kind"],
            "variant": expected["variant"],
        }
        data = (json.dumps(envelope, sort_keys=True, separators=(",", ":")) + "\n").encode()
        return SimpleNamespace(canonical=SimpleNamespace(data=data))

    def parse(self, context_id, data, *, expected):
        decoded = json.loads(data)
        if decoded.get("kind") == expected["kind"]:
            return decoded["body"]
        return decoded


class _FailingTerminalCodec(_TerminalCodec):
    def __init__(self, failing_context):
        super().__init__()
        self.failing_context = failing_context

    def prepare(self, context_id, value, *, expected):
        if self.failing_context == "outcome" and context_id == git_wrapper.REQUEST_OUTCOME_CONTEXT_ID:
            raise ValueError("injected outcome validation failure")
        return super().prepare(context_id, value, expected=expected)


def test_remote_layer_returns_typed_errors_never_exceptions(tmp_path):
    repo = _init_repo(tmp_path)

    listed = git_wrapper.ls_remote(repo, str(tmp_path / "missing-remote"))
    fetched = git_wrapper.fetch_refspecs(repo, str(tmp_path / "missing-remote"), ["+refs/heads/*:refs/remotes/x/*"])
    pushed = git_wrapper.push_create(repo, str(tmp_path / "missing-remote"), "refs/heads/x", "HEAD")

    assert listed == {"ok": False, "error_type": "ls_remote_failed", "detail": listed["detail"]}
    assert fetched["ok"] is False and fetched["error_type"] == "fetch_failed"
    assert pushed["ok"] is False and pushed["error_type"] == "push_failed"


def test_racing_claim_create_and_supersede_cas_against_bare_remote(tmp_path, monkeypatch):
    # Reproduces the empirically proven claim protocol: create-only
    # --force-with-lease=<ref>: lets exactly one clone create the claim ref;
    # the racer gets a deterministic rejection; a supersede must present the
    # observed old sha, and a stale expectation loses.
    _strip_ambient_git_identity(tmp_path, monkeypatch)
    canonical, series_sha = _bare_canonical_with_series(tmp_path)
    clone_a = _clone(canonical, tmp_path / "clone-a")
    clone_b = _clone(canonical, tmp_path / "clone-b")
    claim_ref = "refs/ai-org/claims/demo"

    claim_a = git_wrapper.create_ref_with_files(
        clone_a,
        claim_ref,
        {"claim.json": {"status": "claimed", "claimant": "a"}},
        subject="claim: demo",
        parent=series_sha,
        identity={"name": "A", "email": "1+a@users.noreply.github.com"},
    )
    created = git_wrapper.push_create(clone_a, "origin", claim_ref, claim_a["commit"])
    assert created == {"ok": True, "status": "pushed", "ref": claim_ref, "commit": claim_a["commit"]}

    claim_b = git_wrapper.create_ref_with_files(
        clone_b,
        claim_ref,
        {"claim.json": {"status": "claimed", "claimant": "b"}},
        subject="claim: demo",
        parent=series_sha,
        identity={"name": "B", "email": "2+b@users.noreply.github.com"},
    )
    lost = git_wrapper.push_create(clone_b, "origin", claim_ref, claim_b["commit"])
    assert lost["ok"] is False and lost["status"] == "rejected" and lost["error_type"] == "ref_cas_rejected"

    # B observes the winning sha, then supersedes with a CAS on that exact value.
    observed = git_wrapper.ls_remote(clone_b, "origin", claim_ref)["refs"][claim_ref]
    assert observed == claim_a["commit"]
    assert git_wrapper.fetch_refspecs(clone_b, "origin", [f"+{claim_ref}:{claim_ref}"])["ok"] is True
    supersede_b = git_wrapper.create_ref_with_files(
        clone_b,
        claim_ref,
        {"claim.json": {"status": "claimed", "claimant": "b"}},
        subject="claim: supersede demo",
        parent=series_sha,
        extra_parents=[observed],
        identity={"name": "B", "email": "2+b@users.noreply.github.com"},
    )
    won = git_wrapper.push_cas(clone_b, "origin", claim_ref, supersede_b["commit"], observed)
    assert won["ok"] is True

    # A's expectation is now stale: its CAS loses deterministically.
    stale_renew = git_wrapper.create_ref_with_files(
        clone_a,
        claim_ref,
        {"claim.json": {"status": "claimed", "claimant": "a"}},
        subject="claim: renew demo",
        parent=claim_a["commit"],
        identity={"name": "A", "email": "1+a@users.noreply.github.com"},
    )
    lost_again = git_wrapper.push_cas(clone_a, "origin", claim_ref, stale_renew["commit"], claim_a["commit"])
    assert lost_again["ok"] is False and lost_again["status"] == "rejected"

    # The old claim survives as an ancestor of the superseding claim: git
    # history is the amendment record, no release ledger.
    assert git_wrapper.is_ancestor(clone_b, claim_a["commit"], claim_ref) is True


def _semantic_canonical(*, working_state: str) -> bytes:
    return f'''apiVersion: "ai-org-cue-body-v1"
body: {{
\tchange_kind: "behavior"
\towner: "maintainer"
\tsubsystem: "docs"
\tworking_state: "{working_state}"
}}
kind: "SemanticStatus"
variant: "git-note"
'''.encode()


def _strip_ambient_git_identity(tmp_path: Path, monkeypatch) -> None:
    empty_home = tmp_path / "empty-home"
    empty_home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(empty_home))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for variable in (
        "XDG_CONFIG_HOME",
        "GIT_AUTHOR_NAME",
        "GIT_AUTHOR_EMAIL",
        "GIT_COMMITTER_NAME",
        "GIT_COMMITTER_EMAIL",
        git_wrapper.ENGINE_IDENTITY_NAME_ENV,
        git_wrapper.ENGINE_IDENTITY_EMAIL_ENV,
    ):
        monkeypatch.delenv(variable, raising=False)


def _init_repo_without_identity(tmp_path: Path) -> Path:
    repo = tmp_path / "repo-no-identity"
    repo.mkdir()
    _git(repo, "init")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "-c", "user.name=Seed", "-c", "user.email=seed@example.invalid", "commit", "-m", "base")
    _git(repo, "branch", "-M", "main")
    return repo


def _bare_canonical_with_series(tmp_path: Path) -> tuple[Path, str]:
    canonical = tmp_path / "canonical.git"
    subprocess.run(["git", "init", "--bare", str(canonical)], check=True, capture_output=True, text=True)
    seed = tmp_path / "seed"
    seed.mkdir()
    _git(seed, "init")
    (seed / "README.md").write_text("base\n", encoding="utf-8")
    _git(seed, "add", "README.md")
    _git(seed, "-c", "user.name=Seed", "-c", "user.email=seed@example.invalid", "commit", "-m", "base")
    _git(seed, "branch", "-M", "main")
    _git(seed, "remote", "add", "origin", str(canonical))
    _git(seed, "push", "origin", "main:refs/heads/main", "main:refs/heads/ai-org/patch-series/demo")
    series_sha = _git(seed, "rev-parse", "main").stdout.strip()
    return canonical, series_sha


def _clone(canonical: Path, dest: Path) -> Path:
    result = git_wrapper.clone_repository(canonical, dest)
    assert result["ok"] is True
    return dest


def _init_repo(tmp_path: Path, *, object_format: str = "sha1") -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", f"--object-format={object_format}")
    _git(repo, "config", "user.name", "Tracking Test")
    _git(repo, "config", "user.email", "tracking-test@example.invalid")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "base")
    _git(repo, "branch", "-M", "main")
    return repo


def _branch_with_commit(repo: Path, branch: str, message: str) -> None:
    _git(repo, "checkout", "-B", branch, "main")
    _write_branch_file(repo, branch, message)
    _git(repo, "checkout", "main")


def _commit_on_branch(repo: Path, branch: str, message: str) -> None:
    _git(repo, "checkout", branch)
    _write_branch_file(repo, branch, message)
    _git(repo, "checkout", "main")


def _write_branch_file(repo: Path, branch: str, message: str) -> None:
    path = repo / f"{branch.replace('/', '-')}.txt"
    current = path.read_text(encoding="utf-8") if path.exists() else ""
    path.write_text(f"{current}{message}\n", encoding="utf-8")
    _git(repo, "add", str(path.relative_to(repo)))
    _git(repo, "commit", "-m", message)


def _branch_at(repo: Path, branch: str, start_point: str) -> None:
    _git(repo, "branch", "-f", branch, start_point)


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
