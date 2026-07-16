from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from ai_org import contributor_handoff, git_wrapper, patch_series_bodies
from ai_org.body_codec import BodyCodecClient
from ai_org.patch_author import code_worker
from ai_org.patch_author import functional_check
from ai_org.patch_author import producer_lifecycle
from ai_org.patchwork_queue.field_registry import empty_user_experience_requirements


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return result.stdout.strip()


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    (repo / "patch-series-cover-letter.json").write_text(
        json.dumps(
            {
                "raw_request": "Expose the playable feature through app.py.",
                "working_title": "Playable feature",
                "request_type": "feature",
                "problem_or_motivation": "A real user needs to reach the feature.",
                "intended_users_or_jobs": "Application users need reachable behavior.",
                "desired_outcomes_success": "Users can reach the feature.",
                "affected_area_platform": "app.py",
                "tech_stack": {
                    "build_strategy": "framework_based",
                    "engine": "",
                    "framework": "repo-native Python files",
                    "language": "Python",
                    "platform": "CLI",
                    "rationale": "Use the repository's existing Python file layout.",
                    "provenance": "requester_specified",
                },
                "user_experience_requirements": empty_user_experience_requirements(),
                "background_facts": "Acceptance checks reachability.",
                "constraints_assumptions": [],
                "references": [],
                "grounding_provenance": "Test fixture grounding.",
                "open_questions": [],
                "non_goals_out_of_scope": [],
                "proposal_hint": "Expose the feature through app.py.",
                "alternatives_considered": ["Leave app.py without a reachable feature."],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (repo / "app.py").write_text("BASE = True\n", encoding="utf-8")
    _git(repo, "add", "patch-series-cover-letter.json", "app.py")
    _git(repo, "commit", "-m", "base")
    _git(repo, "branch", "-M", "main")
    _git(repo, "checkout", "-b", "feature/playable")
    (repo / "app.py").write_text("GOAL = 'reachable'\n", encoding="utf-8")
    _git(repo, "add", "app.py")
    _git(repo, "commit", "-m", "feature")
    _git(repo, "checkout", "main")
    return repo


def _producer_claim_repo(tmp_path: Path, *, retarget_goal: bool = False):
    repo = _repo(tmp_path)
    series_branch = "ai-org/patch-series/exact-claims"
    contribution_branch = "ai-org/contrib/exact-claims"
    producer = {
        "name": "Exact Claim Producer",
        "email": "101+exact-claim-producer@users.noreply.github.com",
    }
    _git(repo, "branch", series_branch, "main")
    series_oid = _git(repo, "rev-parse", series_branch)
    _git(repo, "checkout", "-b", contribution_branch, "main")
    source = {
        "series_snapshot_oid": series_oid,
        "series_branch": series_branch,
        "node_path": ".",
        "contribution_branch": contribution_branch,
        "canonical_root_body_sha256": "a" * 64,
    }
    promise_path = producer_lifecycle.evidence_coordinate(
        series_branch,
        ".",
        contribution_branch,
        producer,
        source["canonical_root_body_sha256"],
        "obligation:one",
    )
    promise = {
        **source,
        "referee_goal_id": "goal:one",
        "obligation_id": "obligation:one",
        "deliverable": "retained artifact",
        "eligibility_predicate": "producer may author the retained artifact",
        "producer": producer,
        "eligibility_decision": "eligible",
        "eligibility_basis": "the goal requires a retained artifact",
        "commitment_slot": {},
        "completion_claim_slot": {},
        "previous_promise_commit_oid": None,
        "authored_at": "2026-07-15T00:00:00Z",
    }
    codec = BodyCodecClient()
    prepared_promise = codec.prepare(
        patch_series_bodies.PRODUCER_PROMISE_CONTEXT,
        promise,
        expected=patch_series_bodies.PRODUCER_PROMISE_CONTRACT,
    )
    promise_target = repo / promise_path
    promise_target.parent.mkdir(parents=True, exist_ok=True)
    promise_target.write_bytes(prepared_promise.canonical.data)
    _git(repo, "add", promise_path)
    _git(repo, "commit", "-m", "producer-promise: initialize exact claims")
    promise_commit = _git(repo, "rev-parse", "HEAD")

    binding = {
        "binding_id": "producer-task-binding:exact-claims",
        **source,
        "scope_decomposition_commit_oid": series_oid,
        "scope_decomposition_body_sha256": "b" * 64,
        "producer": producer,
        "tasks": [{
            "item_id": "item:one",
            "production_obligation_ids": ["obligation:one"],
        }],
        "promise_bindings": [{
            "obligation_id": "obligation:one",
            "evidence_coordinate": promise_path,
            "promise_commit_oid": promise_commit,
            "promise_body_sha256": prepared_promise.canonical.sha256,
        }],
    }
    prepared_binding = codec.prepare(
        patch_series_bodies.PRODUCER_TASK_BINDING_CONTEXT,
        binding,
        expected=patch_series_bodies.PRODUCER_TASK_BINDING_CONTRACT,
    )
    (repo / producer_lifecycle.TASK_BINDING_PATH).write_bytes(
        prepared_binding.canonical.data
    )
    committed_promise = dict(promise)
    committed_promise.update(
        commitment_slot={
            "task_binding_path": producer_lifecycle.TASK_BINDING_PATH,
            "task_binding_body_sha256": prepared_binding.canonical.sha256,
        },
        previous_promise_commit_oid=promise_commit,
        authored_at="2026-07-15T00:01:00Z",
    )
    prepared_committed_promise = codec.prepare(
        patch_series_bodies.PRODUCER_PROMISE_CONTEXT,
        committed_promise,
        expected=patch_series_bodies.PRODUCER_PROMISE_CONTRACT,
    )
    promise_target.write_bytes(prepared_committed_promise.canonical.data)
    _git(repo, "add", producer_lifecycle.TASK_BINDING_PATH, promise_path)
    _git(repo, "commit", "-m", "producer-task-binding: materialize exact claims")
    binding_commit = _git(repo, "rev-parse", "HEAD")

    (repo / "artifact.txt").write_text("retained evidence\n", encoding="utf-8")
    _git(repo, "add", "artifact.txt")
    _git(
        repo,
        "commit",
        "-m",
        "patch: exact claim [item:one]",
        "-m",
        "\n".join(
            producer_lifecycle.item_binding_trailers(
                prepared_binding.canonical.sha256,
                ["obligation:one"],
            )
        ),
    )
    implementation_oid = _git(repo, "rev-parse", "HEAD")
    completion = producer_lifecycle.prepare_completion_assertion(
        repo, implementation_oid, asserted_at="2026-07-15T00:02:00Z"
    )
    completion_files = dict(completion.files)
    if retarget_goal:
        tampered = json.loads(json.dumps(completion.body))
        tampered["assertions"][0]["referee_goal_id"] = "goal:retargeted"
        prepared_tampered = codec.prepare(
            patch_series_bodies.PRODUCER_COMPLETION_ASSERTION_CONTEXT,
            tampered,
            expected=patch_series_bodies.PRODUCER_COMPLETION_ASSERTION_CONTRACT,
        )
        completion_files[producer_lifecycle.COMPLETION_ASSERTION_PATH] = (
            prepared_tampered.canonical.data.decode("utf-8")
        )
        current_promise = patch_series_bodies.read_producer_promise(
            git_wrapper.show_file_bytes(repo, implementation_oid, promise_path),
            client=codec,
        )
        current_promise["completion_claim_slot"] = {
            "assertion_path": producer_lifecycle.COMPLETION_ASSERTION_PATH,
            "assertion_body_sha256": prepared_tampered.canonical.sha256,
        }
        current_promise["previous_promise_commit_oid"] = implementation_oid
        current_promise["authored_at"] = "2026-07-15T00:02:00Z"
        completion_files[promise_path] = codec.prepare(
            patch_series_bodies.PRODUCER_PROMISE_CONTEXT,
            current_promise,
            expected=patch_series_bodies.PRODUCER_PROMISE_CONTRACT,
        ).canonical.data.decode("utf-8")
    for path, content in completion_files.items():
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    _git(repo, "add", *sorted(completion_files))
    _git(repo, "commit", "-m", "producer-completion: assert exact claims")

    result = contributor_handoff.prepare_result({
        "patch_series_branch": series_branch,
        "node_key": "root",
        "acknowledged_patchwork_checks": {},
    })
    (repo / contributor_handoff.RESULT_PATH).write_bytes(result.canonical.data)
    _git(repo, "add", contributor_handoff.RESULT_PATH)
    _git(repo, "commit", "-m", "patch: implementation result for root")
    _git(repo, "checkout", "main")
    return repo, series_branch, contribution_branch, series_oid, binding_commit


def test_check_returns_reachable_and_commits_acceptance_to_branch(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    before = _git(repo, "rev-parse", "refs/heads/feature/playable")
    real_run = subprocess.run
    codex_calls = []

    def fake_run(cmd, **kwargs):
        if cmd[0] == "git":
            return real_run(cmd, **kwargs)
        assert cmd[:4] == ["codex", "exec", "--sandbox", "read-only"]
        worktree = Path(cmd[cmd.index("-C") + 1])
        out_file = Path(cmd[cmd.index("-o") + 1])
        schema_file = Path(cmd[cmd.index("--output-schema") + 1])
        codex_calls.append({"cmd": cmd, "worktree": worktree, "schema_file": schema_file})
        assert _git(worktree, "rev-parse", "HEAD") == before
        assert (worktree / "app.py").read_text(encoding="utf-8") == "GOAL = 'reachable'\n"
        assert json.loads(schema_file.read_text(encoding="utf-8")) == functional_check.VERDICT_SCHEMA
        out_file.write_text(
            json.dumps(
                {
                    "reachable": True,
                    "blockers": [],
                    "notes": "USER reaches the goal through app.py:1.",
                }
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(functional_check.subprocess, "run", fake_run)

    verdict = functional_check.check(
        repo,
        "refs/heads/feature/playable",
        patch_series_branch=functional_check.NO_PATCHWORK_ANCHOR,
        node_key=functional_check.NO_PATCHWORK_ANCHOR,
    )

    after = _git(repo, "rev-parse", "refs/heads/feature/playable")
    assert verdict == {
        "ok": True,
        "reachable": True,
        "blockers": [],
        "notes": "USER reaches the goal through app.py:1.",
    }
    assert after != before
    assert _git(repo, "log", "-1", "--format=%s", "refs/heads/feature/playable") == "acceptance: reachable"
    assert _git(repo, "log", "-1", "--format=%ae", "refs/heads/feature/playable") == (
        git_wrapper.DEFAULT_ENGINE_IDENTITY_EMAIL
    )
    assert _git(repo, "rev-parse", "HEAD") != after
    assert len(codex_calls) == 1
    assert not codex_calls[0]["worktree"].exists()
    assert "Return only JSON matching the provided schema." in codex_calls[0]["cmd"][-1]


def test_check_returns_blocked_and_commits_blockers_to_branch(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    before = _git(repo, "rev-parse", "refs/heads/feature/playable")
    real_run = subprocess.run

    def fake_run(cmd, **kwargs):
        if cmd[0] == "git":
            return real_run(cmd, **kwargs)
        out_file = Path(cmd[cmd.index("-o") + 1])
        out_file.write_text(
            json.dumps(
                {
                    "reachable": False,
                    "blockers": [
                        {
                            "where": "app.py:1",
                            "why": "The branch sets a constant but exposes no user path.",
                        }
                    ],
                    "notes": "USER is blocked because APP finds no handler.",
                }
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(functional_check.subprocess, "run", fake_run)

    verdict = functional_check.check(
        repo,
        "feature/playable",
        patch_series_branch=functional_check.NO_PATCHWORK_ANCHOR,
        node_key=functional_check.NO_PATCHWORK_ANCHOR,
    )

    after = _git(repo, "rev-parse", "refs/heads/feature/playable")
    assert verdict == {
        "ok": False,
        "reachable": False,
        "blockers": [
            {
                "where": "app.py:1",
                "why": "The branch sets a constant but exposes no user path.",
            }
        ],
        "notes": "USER is blocked because APP finds no handler.",
    }
    assert after != before
    assert _git(repo, "log", "-1", "--format=%s", "refs/heads/feature/playable") == "acceptance: blocked"
    assert "app.py:1: The branch sets a constant" in _git(
        repo,
        "log",
        "-1",
        "--format=%b",
        "refs/heads/feature/playable",
    )


def test_unanchored_incomplete_producer_claim_is_rejected_before_admission(
    tmp_path, monkeypatch
):
    repo = _repo(tmp_path)
    _git(repo, "checkout", "feature/playable")
    (repo / "producer-task-binding.cue").write_text("{}\n", encoding="utf-8")
    _git(repo, "add", "producer-task-binding.cue")
    _git(repo, "commit", "-m", "producer: incomplete evidence")
    _git(repo, "checkout", "main")
    before = _git(repo, "rev-parse", "refs/heads/feature/playable")

    monkeypatch.setattr(
        functional_check,
        "freeze_acceptance_source_vector",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("acceptance sources froze before the common gate")
        ),
    )
    monkeypatch.setattr(
        functional_check,
        "prepare_claim_admission",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("claim admission ran before the common gate")
        ),
    )
    monkeypatch.setattr(
        functional_check,
        "_run_codex",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("independent judge ran before the common gate")
        ),
    )
    verdict = functional_check.check(
        repo,
        "feature/playable",
        patch_series_branch=functional_check.NO_PATCHWORK_ANCHOR,
        node_key=functional_check.NO_PATCHWORK_ANCHOR,
    )

    assert verdict["ok"] is False
    assert verdict["status"] == "producer_functional_acceptance_gate_closed"
    assert verdict["notes"] == "independent referee was not invoked"
    assert verdict["blockers"] == [{
        "where": "functional_check",
        "why": "producer_functional_acceptance_gate_closed: "
        "a frozen patch-series root anchor is required",
    }]
    assert _git(repo, "rev-parse", "refs/heads/feature/playable") == before
    assert subprocess.run(
        ["git", "-C", str(repo), "show-ref", "--verify", "--quiet", functional_check.ACCEPTANCE_AUTHORITY_NOTES_REF]
    ).returncode != 0


def test_claim_admission_accepts_only_the_exact_git_derived_claim_cohort(tmp_path):
    repo, _series_branch, contribution_branch, series_oid, _binding_commit = (
        _producer_claim_repo(tmp_path)
    )

    admission = functional_check.prepare_claim_admission(
        repo,
        contribution_branch,
        evaluated_at="2026-07-15T00:04:00Z",
        frozen_series_oid=series_oid,
        expected_contribution_branch=contribution_branch,
    )

    assert admission.admitted is True
    assert admission.rejections == ()
    assert admission.body is not None
    assert admission.body["required_obligation_ids"] == ["obligation:one"]
    assert admission.referee_goal_ids == ("goal:one",)


def test_complete_claim_without_frozen_root_anchor_is_rejected_before_judge(
    tmp_path, monkeypatch
):
    repo, _series_branch, contribution_branch, _series_oid, _binding_commit = (
        _producer_claim_repo(tmp_path)
    )
    branch_before = _git(repo, "rev-parse", contribution_branch)

    monkeypatch.setattr(
        functional_check,
        "prepare_claim_admission",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("claim admission ran without the common frozen-root gate")
        ),
    )
    monkeypatch.setattr(
        functional_check,
        "_run_codex",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("judge ran without the common frozen-root gate")
        ),
    )

    verdict = functional_check.check(repo, contribution_branch)

    assert verdict["ok"] is False
    assert verdict["status"] == "producer_functional_acceptance_gate_closed"
    assert verdict["notes"] == "independent referee was not invoked"
    assert _git(repo, "rev-parse", contribution_branch) == branch_before
    assert subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "show-ref",
            "--verify",
            "--quiet",
            functional_check.ACCEPTANCE_AUTHORITY_NOTES_REF,
        ]
    ).returncode != 0


def test_claim_admission_rejects_internally_rehashed_referee_retargeting(tmp_path):
    repo, _series_branch, contribution_branch, series_oid, _binding_commit = (
        _producer_claim_repo(tmp_path, retarget_goal=True)
    )

    admission = functional_check.prepare_claim_admission(
        repo,
        contribution_branch,
        frozen_series_oid=series_oid,
        expected_contribution_branch=contribution_branch,
    )

    assert admission.admitted is False
    assert any(
        rejection["coordinate"] == "assertions/obligation:one"
        and rejection["type"] == "source-mismatch"
        for rejection in admission.rejections
    )


def test_claim_admission_rejects_a_later_unclaimed_contribution_tip(tmp_path):
    repo, _series_branch, contribution_branch, series_oid, _binding_commit = (
        _producer_claim_repo(tmp_path)
    )
    _git(repo, "checkout", contribution_branch)
    _git(repo, "commit", "--allow-empty", "-m", "producer: later unclaimed tip")
    later_tip = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "main")

    admission = functional_check.prepare_claim_admission(
        repo,
        later_tip,
        frozen_contribution_oid=later_tip,
        frozen_series_oid=series_oid,
        expected_contribution_branch=contribution_branch,
    )

    assert admission.admitted is False
    assert any(
        rejection["type"] == "result-mismatch"
        and "frozen contribution tip" in rejection["detail"]
        for rejection in admission.rejections
    )


def test_claim_admission_rejects_a_branch_moved_past_the_frozen_tip(tmp_path):
    repo, _series_branch, contribution_branch, series_oid, _binding_commit = (
        _producer_claim_repo(tmp_path)
    )
    frozen_tip = _git(repo, "rev-parse", contribution_branch)
    _git(repo, "checkout", contribution_branch)
    _git(repo, "commit", "--allow-empty", "-m", "producer: concurrent tip")
    _git(repo, "checkout", "main")

    admission = functional_check.prepare_claim_admission(
        repo,
        frozen_tip,
        frozen_contribution_oid=frozen_tip,
        frozen_series_oid=series_oid,
        expected_contribution_branch=contribution_branch,
    )

    assert admission.admitted is False
    assert any(
        rejection["coordinate"] == contribution_branch
        and rejection["type"] == "source-mismatch"
        and "tip moved" in rejection["detail"]
        for rejection in admission.rejections
    )


def test_claim_admission_rejects_authority_notes_moved_during_freeze(
    tmp_path, monkeypatch
):
    repo, _series_branch, contribution_branch, series_oid, _binding_commit = (
        _producer_claim_repo(tmp_path)
    )
    real_tree_files = git_wrapper.tree_files
    moved = False

    def move_notes_then_list(repo_path, ref):
        nonlocal moved
        if not moved:
            moved = True
            git_wrapper.create_ref_with_files(
                repo_path,
                functional_check.ACCEPTANCE_AUTHORITY_NOTES_REF,
                {"concurrent-note": "verifier: concurrent\n"},
                subject="acceptance authority: concurrent note",
            )
        return real_tree_files(repo_path, ref)

    monkeypatch.setattr(git_wrapper, "tree_files", move_notes_then_list)
    admission = functional_check.prepare_claim_admission(
        repo,
        contribution_branch,
        frozen_series_oid=series_oid,
        expected_contribution_branch=contribution_branch,
    )

    assert admission.admitted is False
    assert any(
        rejection["coordinate"]
        == functional_check.ACCEPTANCE_AUTHORITY_NOTES_REF
        and rejection["type"] == "source-mismatch"
        and "notes moved" in rejection["detail"]
        for rejection in admission.rejections
    )


def test_claim_admission_rejects_series_tip_moved_from_frozen_source_vector(
    tmp_path, monkeypatch
):
    repo, series_branch, contribution_branch, series_oid, _binding_commit = (
        _producer_claim_repo(tmp_path)
    )
    contribution_oid = _git(repo, "rev-parse", contribution_branch)
    source_vector = functional_check.freeze_acceptance_source_vector(
        repo,
        contribution_branch,
        series_branch,
        contribution_oid=contribution_oid,
        series_oid=series_oid,
    )
    real_tree_files = git_wrapper.tree_files
    moved = False

    def move_series_then_list(repo_path, ref):
        nonlocal moved
        if not moved:
            moved = True
            git_wrapper.create_ref_with_files(
                repo_path,
                f"refs/heads/{series_branch}",
                {"series-concurrent-change": "later series source\n"},
                subject="patch series: concurrent source change",
                parent=series_oid,
                inherit_parent_tree=True,
            )
        return real_tree_files(repo_path, ref)

    monkeypatch.setattr(git_wrapper, "tree_files", move_series_then_list)
    admission = functional_check.prepare_claim_admission(
        repo,
        contribution_branch,
        expected_contribution_branch=contribution_branch,
        frozen_source_vector=source_vector,
    )

    assert admission.admitted is False
    assert admission.source_vector == source_vector
    assert any(
        rejection["coordinate"] == series_branch
        and rejection["type"] == "source-mismatch"
        and "acceptance source moved" in rejection["detail"]
        for rejection in admission.rejections
    )


def test_v2_acceptance_uses_frozen_root_gate_before_claim_admission(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    contribution_oid = _git(repo, "rev-parse", "refs/heads/feature/playable")
    frozen_series_oid = "f" * 40
    gate_calls = []
    admission_calls = []
    original_prepare = functional_check.prepare_claim_admission

    monkeypatch.setattr(
        patch_series_bodies,
        "classify_root_generation",
        lambda *_args, **_kwargs: SimpleNamespace(
            frozen_oid=frozen_series_oid,
            generation=patch_series_bodies.ROOT_GENERATION_V2,
            disposition=patch_series_bodies.ROOT_DISPOSITION_V2_READY,
            detail="",
            lifecycle_ready=True,
            has_generation_members=True,
        ),
    )

    def open_gate(_repo, branch, route, *, frozen_root_oid):
        gate_calls.append((branch, route, frozen_root_oid))
        return SimpleNamespace(blocked=False, diagnostic=None)

    def capture_admission(*args, **kwargs):
        admission_calls.append(kwargs)
        return original_prepare(*args, **kwargs)

    monkeypatch.setattr(
        functional_check.patch_series_gate, "vet_producer_pair_closure", open_gate
    )
    monkeypatch.setattr(functional_check, "prepare_claim_admission", capture_admission)
    monkeypatch.setattr(
        functional_check,
        "_run_codex",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("judge ran before exact v2 claim admission")
        ),
    )

    verdict = functional_check.check(
        repo,
        "feature/playable",
        patch_series_branch="ai-org/patch-series/v2",
        node_key="root",
    )

    assert verdict["ok"] is False
    assert verdict["notes"] == "independent referee was not invoked"
    assert gate_calls == [(
        "ai-org/patch-series/v2",
        "producer_functional_acceptance",
        frozen_series_oid,
    )]
    assert admission_calls[0]["frozen_contribution_oid"] == contribution_oid
    assert admission_calls[0]["frozen_series_oid"] == frozen_series_oid
    source_vector = admission_calls[0]["frozen_source_vector"]
    assert source_vector.contribution_ref == "feature/playable"
    assert source_vector.contribution_oid == contribution_oid
    assert source_vector.series_ref == "ai-org/patch-series/v2"
    assert source_vector.series_oid == frozen_series_oid
    assert source_vector.authority_notes_ref == (
        functional_check.ACCEPTANCE_AUTHORITY_NOTES_REF
    )
    assert {item["where"] for item in verdict["blockers"]} == {
        "producer-task-binding.cue",
        "producer-completion-assertion.cue",
        "implementation-result",
    }
    assert _git(repo, "rev-parse", "refs/heads/feature/playable") == contribution_oid


def test_v2_acceptance_freezes_validation_sources_before_judge_and_publication(
    tmp_path, monkeypatch
):
    repo, series_branch, contribution_branch, series_oid, _binding_commit = (
        _producer_claim_repo(tmp_path)
    )
    validation_sources = []

    monkeypatch.setattr(
        functional_check.patch_series_gate,
        "vet_producer_pair_closure",
        lambda *_args, **_kwargs: SimpleNamespace(blocked=False),
    )

    def capture_acknowledgement(*_args, **kwargs):
        validation_sources.append(
            ("patchwork", kwargs.get("frozen_patch_series_oid"))
        )
        return None

    def capture_must_answer(*_args, **kwargs):
        validation_sources.append(
            ("must-answer", kwargs.get("frozen_patch_series_oid"))
        )
        return None

    def judge(_worktree, _prompt, out_file, _schema_file):
        out_file.write_text(
            json.dumps(
                {"reachable": True, "blockers": [], "notes": "goal reached"}
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess([], 0, stdout="", stderr="")

    monkeypatch.setattr(
        functional_check,
        "_patchwork_check_acknowledgement_rejection",
        capture_acknowledgement,
    )
    monkeypatch.setattr(
        functional_check,
        "_must_answer_questions_rejection",
        capture_must_answer,
    )
    monkeypatch.setattr(functional_check, "_run_codex", judge)

    verdict = functional_check.check(
        repo,
        contribution_branch,
        patch_series_branch=series_branch,
        node_key="root",
        root_generation_snapshot=SimpleNamespace(
            frozen_oid=series_oid,
            generation=patch_series_bodies.ROOT_GENERATION_V2,
            disposition=patch_series_bodies.ROOT_DISPOSITION_V2_READY,
            detail="",
            lifecycle_ready=True,
            has_generation_members=True,
        ),
    )

    assert verdict["ok"] is True
    assert validation_sources == [
        ("patchwork", series_oid),
        ("must-answer", series_oid),
    ]
    assert functional_check.current_tip_accepted(repo, contribution_branch) is True


def test_structurally_valid_but_unrederived_admission_is_not_accepted(tmp_path):
    repo = _repo(tmp_path)
    contribution_oid = _git(repo, "rev-parse", "refs/heads/feature/playable")
    forged_branch = "ai-org/contrib/structurally-valid"
    git_wrapper.update_ref(repo, f"refs/heads/{forged_branch}", contribution_oid)
    source = {
        "series_snapshot_oid": "1" * 40,
        "canonical_root_body_sha256": "a" * 64,
        "scope_decomposition_commit_oid": "2" * 40,
        "scope_decomposition_body_sha256": "b" * 64,
    }
    promise_binding = {
        "obligation_id": "obligation:one",
        "evidence_coordinate": "producer-evidence/one/producer-promise.cue",
        "promise_commit_oid": contribution_oid,
        "promise_body_sha256": "c" * 64,
    }
    body = {
        "evaluated_contribution_oid": contribution_oid,
        "evaluated_at": "2026-07-14T00:00:00Z",
        "source_snapshot": source,
        "authority_notes_ref_oid": functional_check.ZERO_OID,
        "required_obligation_ids": ["obligation:one"],
        "promise_bindings": [promise_binding],
        "task_binding_body_sha256": "d" * 64,
        "assertions": [{
            "obligation_id": "obligation:one",
            "assertion_commit_oid": contribution_oid,
            "assertion_body_sha256": "e" * 64,
        }],
        "implementation_result_body_sha256": "f" * 64,
        "admitted": True,
    }
    prepared = BodyCodecClient().prepare(
        patch_series_bodies.CLAIM_ADMISSION_CONTEXT,
        body,
        expected=patch_series_bodies.CLAIM_ADMISSION_CONTRACT,
    )
    admission = functional_check.ClaimAdmissionPreparation(
        "admitted",
        contribution_oid,
        functional_check.ZERO_OID,
        body,
        bytes(prepared.canonical.data),
        prepared.canonical.sha256,
        ("goal:one",),
    )

    published = functional_check._publish_registered_verdict(
        repo,
        f"refs/heads/{forged_branch}",
        admission,
        {"ok": True, "reachable": True, "blockers": [], "notes": "goal reached"},
    )

    assert published["ok"] is True
    assert published["status"] == "sealed"
    assert _git(repo, "rev-parse", f"refs/heads/{forged_branch}") == published["verdict_commit"]
    assert _git(repo, "rev-parse", functional_check.ACCEPTANCE_AUTHORITY_NOTES_REF) == published["notes_commit"]
    assert functional_check.current_tip_has_registered_verdict(repo, forged_branch) is True
    assert functional_check.current_tip_has_terminal_verdict(repo, forged_branch) is False
    assert functional_check.current_tip_accepted(repo, forged_branch) is False


def test_exact_sealed_verdict_is_atomic_and_a_later_tip_invalidates_it(tmp_path):
    repo, series_branch, contribution_branch, series_oid, _binding_commit = (
        _producer_claim_repo(tmp_path)
    )
    admission = functional_check.prepare_claim_admission(
        repo,
        contribution_branch,
        frozen_series_oid=series_oid,
        expected_contribution_branch=contribution_branch,
    )
    assert admission.admitted is True
    published = functional_check._publish_registered_verdict(
        repo,
        f"refs/heads/{contribution_branch}",
        admission,
        {"ok": True, "reachable": True, "blockers": [], "notes": "goal reached"},
    )

    assert published["ok"] is True
    assert published["status"] == "sealed"
    assert published["prepared_object_oids"] == {
        "verdict_commit_oid": published["verdict_commit"],
        "acceptance_authority_notes_commit_oid": published["notes_commit"],
    }
    assert published["resulting_ref_oids"] == {
        f"refs/heads/{contribution_branch}": published["verdict_commit"],
        functional_check.ACCEPTANCE_AUTHORITY_NOTES_REF: published["notes_commit"],
    }
    seal_raw = git_wrapper.show_file_bytes(
        repo, published["notes_commit"], published["verdict_commit"]
    )
    assert seal_raw is not None
    seal = patch_series_bodies.read_acceptance_authority_seal(seal_raw)
    assert seal["contribution_ref"] == f"refs/heads/{contribution_branch}"
    assert functional_check.current_tip_has_registered_verdict(
        repo, contribution_branch
    ) is True
    assert functional_check.current_tip_has_terminal_verdict(
        repo, contribution_branch
    ) is True
    assert functional_check.current_tip_accepted(repo, contribution_branch) is True

    _git(repo, "checkout", series_branch)
    _git(repo, "commit", "--allow-empty", "-m", "patch series: later source tip")
    _git(repo, "checkout", "main")
    assert functional_check.current_tip_accepted(repo, contribution_branch) is True

    _git(repo, "checkout", contribution_branch)
    _git(repo, "commit", "--allow-empty", "-m", "producer: later contribution tip")
    _git(repo, "checkout", "main")
    assert functional_check.current_tip_has_registered_verdict(
        repo, contribution_branch
    ) is False
    assert functional_check.current_tip_has_terminal_verdict(
        repo, contribution_branch
    ) is False
    assert functional_check.current_tip_accepted(repo, contribution_branch) is False


def test_current_tip_predicate_rejects_seal_copied_to_another_contribution_ref(
    tmp_path,
):
    repo, _series_branch, contribution_branch, series_oid, _binding_commit = (
        _producer_claim_repo(tmp_path)
    )
    admission = functional_check.prepare_claim_admission(
        repo,
        contribution_branch,
        frozen_series_oid=series_oid,
        expected_contribution_branch=contribution_branch,
    )
    published = functional_check._publish_registered_verdict(
        repo,
        f"refs/heads/{contribution_branch}",
        admission,
        {"ok": True, "reachable": True, "blockers": [], "notes": "goal reached"},
    )
    assert published["ok"] is True

    copied_branch = "ai-org/contrib/copied-seal"
    copied_ref = f"refs/heads/{copied_branch}"
    git_wrapper.update_ref(repo, copied_ref, published["verdict_commit"])

    assert functional_check.current_tip_accepted(repo, contribution_branch) is True
    assert functional_check.current_tip_accepted(repo, copied_branch) is False
    assert functional_check.current_tip_has_terminal_verdict(
        repo, copied_branch
    ) is False
    notes_oid = git_wrapper.head_sha(
        repo, functional_check.ACCEPTANCE_AUTHORITY_NOTES_REF
    )
    assert functional_check.sealed_verdict_accepted(
        repo,
        published["verdict_commit"],
        frozen_authority_notes_oid=notes_oid,
        expected_contribution_branch=contribution_branch,
    ) is True
    assert functional_check.sealed_verdict_accepted(
        repo,
        published["verdict_commit"],
        frozen_authority_notes_oid=notes_oid,
        expected_contribution_branch=copied_branch,
    ) is False


def test_seal_verifier_rejects_a_direct_contribution_ref_mismatch(tmp_path):
    repo, _series_branch, contribution_branch, series_oid, _binding_commit = (
        _producer_claim_repo(tmp_path)
    )
    admission = functional_check.prepare_claim_admission(
        repo,
        contribution_branch,
        frozen_series_oid=series_oid,
        expected_contribution_branch=contribution_branch,
    )
    published = functional_check._publish_registered_verdict(
        repo,
        f"refs/heads/{contribution_branch}",
        admission,
        {"ok": True, "reachable": True, "blockers": [], "notes": "goal reached"},
    )
    assert published["ok"] is True

    seal_raw = git_wrapper.show_file_bytes(
        repo, published["notes_commit"], published["verdict_commit"]
    )
    assert seal_raw is not None
    seal = patch_series_bodies.read_acceptance_authority_seal(seal_raw)
    legacy_seal = dict(seal)
    legacy_seal.pop("contribution_ref")
    legacy_prepared = BodyCodecClient().prepare(
        patch_series_bodies.ACCEPTANCE_AUTHORITY_SEAL_CONTEXT,
        legacy_seal,
        expected=patch_series_bodies.ACCEPTANCE_AUTHORITY_SEAL_CONTRACT,
    )
    legacy_notes = git_wrapper.create_ref_with_files(
        repo,
        "refs/notes/ai-org/legacy-acceptance-fixture",
        {
            published["verdict_commit"]: legacy_prepared.canonical.data.decode(
                "utf-8", errors="strict"
            )
        },
        subject="acceptance authority: legacy seal fixture",
    )["commit"]

    assert functional_check.sealed_verdict_accepted(
        repo,
        published["verdict_commit"],
        frozen_authority_notes_oid=legacy_notes,
    ) is True
    assert functional_check.sealed_verdict_accepted(
        repo,
        published["verdict_commit"],
        frozen_authority_notes_oid=legacy_notes,
        expected_contribution_branch=contribution_branch,
    ) is False

    seal["contribution_ref"] = "refs/heads/ai-org/contrib/copied-seal"
    prepared = BodyCodecClient().prepare(
        patch_series_bodies.ACCEPTANCE_AUTHORITY_SEAL_CONTEXT,
        seal,
        expected=patch_series_bodies.ACCEPTANCE_AUTHORITY_SEAL_CONTRACT,
    )
    tampered_notes = git_wrapper.create_ref_with_files(
        repo,
        "refs/notes/ai-org/mismatched-acceptance-fixture",
        {
            published["verdict_commit"]: prepared.canonical.data.decode(
                "utf-8", errors="strict"
            )
        },
        subject="acceptance authority: mismatched contribution ref",
    )["commit"]

    assert functional_check.sealed_verdict_accepted(
        repo,
        published["verdict_commit"],
        frozen_authority_notes_oid=tampered_notes,
    ) is False


def test_blocked_registered_verdict_is_atomically_sealed_but_not_accepted(tmp_path):
    repo, _series_branch, contribution_branch, series_oid, _binding_commit = (
        _producer_claim_repo(tmp_path)
    )
    admission = functional_check.prepare_claim_admission(
        repo,
        contribution_branch,
        frozen_series_oid=series_oid,
        expected_contribution_branch=contribution_branch,
    )

    published = functional_check._publish_registered_verdict(
        repo,
        f"refs/heads/{contribution_branch}",
        admission,
        {
            "ok": False,
            "reachable": False,
            "blockers": [{"where": "app.py", "why": "goal is blocked"}],
            "notes": "independent rejection",
        },
    )

    assert published["ok"] is True
    assert published["status"] == "sealed"
    assert (
        git_wrapper.head_sha(repo, functional_check.ACCEPTANCE_AUTHORITY_NOTES_REF)
        == published["notes_commit"]
    )
    assert functional_check.current_tip_has_terminal_verdict(
        repo, contribution_branch
    ) is True
    assert functional_check.current_tip_accepted(repo, contribution_branch) is False


def test_current_tip_predicate_rejects_a_tip_move_during_seal_verification(
    tmp_path, monkeypatch
):
    repo, _series_branch, contribution_branch, series_oid, _binding_commit = (
        _producer_claim_repo(tmp_path)
    )
    admission = functional_check.prepare_claim_admission(
        repo,
        contribution_branch,
        frozen_series_oid=series_oid,
        expected_contribution_branch=contribution_branch,
    )
    published = functional_check._publish_registered_verdict(
        repo,
        f"refs/heads/{contribution_branch}",
        admission,
        {"ok": True, "reachable": True, "blockers": [], "notes": "goal reached"},
    )
    assert published["ok"] is True

    def move_tip_while_verifying(*_args, **_kwargs):
        _git(repo, "checkout", contribution_branch)
        _git(repo, "commit", "--allow-empty", "-m", "producer: concurrent tip")
        _git(repo, "checkout", "main")
        return True

    monkeypatch.setattr(
        functional_check, "sealed_verdict_accepted", move_tip_while_verifying
    )

    assert functional_check.current_tip_accepted(repo, contribution_branch) is False


def test_current_tip_predicate_rejects_a_notes_move_during_seal_verification(
    tmp_path, monkeypatch
):
    repo, _series_branch, contribution_branch, series_oid, _binding_commit = (
        _producer_claim_repo(tmp_path)
    )
    admission = functional_check.prepare_claim_admission(
        repo,
        contribution_branch,
        frozen_series_oid=series_oid,
        expected_contribution_branch=contribution_branch,
    )
    published = functional_check._publish_registered_verdict(
        repo,
        f"refs/heads/{contribution_branch}",
        admission,
        {"ok": True, "reachable": True, "blockers": [], "notes": "goal reached"},
    )
    assert published["ok"] is True

    def move_notes_while_verifying(*_args, **_kwargs):
        old_notes = _git(
            repo, "rev-parse", functional_check.ACCEPTANCE_AUTHORITY_NOTES_REF
        )
        git_wrapper.create_ref_with_files(
            repo,
            functional_check.ACCEPTANCE_AUTHORITY_NOTES_REF,
            {"concurrent-note": "verifier: concurrent\n"},
            subject="acceptance authority: concurrent note",
            parent=old_notes,
            inherit_parent_tree=True,
        )
        return True

    monkeypatch.setattr(
        functional_check, "sealed_verdict_accepted", move_notes_while_verifying
    )

    assert functional_check.current_tip_accepted(repo, contribution_branch) is False


def test_seal_publication_rejects_a_racing_notes_tip_without_partial_branch_update(tmp_path):
    repo, _series_branch, contribution_branch, series_oid, _binding_commit = (
        _producer_claim_repo(tmp_path)
    )
    admission = functional_check.prepare_claim_admission(
        repo,
        contribution_branch,
        frozen_series_oid=series_oid,
        expected_contribution_branch=contribution_branch,
    )
    assert admission.admitted is True
    branch_before = _git(repo, "rev-parse", contribution_branch)
    racing_note = git_wrapper.create_ref_with_files(
        repo,
        functional_check.ACCEPTANCE_AUTHORITY_NOTES_REF,
        {"racing-note.cue": "writer: concurrent\n"},
        subject="acceptance authority: concurrent writer",
    )["commit"]

    published = functional_check._publish_registered_verdict(
        repo,
        f"refs/heads/{contribution_branch}",
        admission,
        {"ok": True, "reachable": True, "blockers": [], "notes": "goal reached"},
    )

    assert published["ok"] is False
    assert published["status"] == "atomic_ref_cas_rejected"
    assert _git(repo, "rev-parse", contribution_branch) == branch_before
    assert _git(repo, "rev-parse", functional_check.ACCEPTANCE_AUTHORITY_NOTES_REF) == racing_note
    assert published["resulting_ref_oids"] == {
        f"refs/heads/{contribution_branch}": branch_before,
        functional_check.ACCEPTANCE_AUTHORITY_NOTES_REF: racing_note,
    }


def test_seal_publication_rejects_a_racing_branch_without_publishing_notes(tmp_path):
    repo, _series_branch, contribution_branch, series_oid, _binding_commit = (
        _producer_claim_repo(tmp_path)
    )
    admission = functional_check.prepare_claim_admission(
        repo,
        contribution_branch,
        frozen_series_oid=series_oid,
        expected_contribution_branch=contribution_branch,
    )
    assert admission.admitted is True
    _git(repo, "checkout", contribution_branch)
    _git(repo, "commit", "--allow-empty", "-m", "producer: racing contribution")
    racing_tip = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "main")

    published = functional_check._publish_registered_verdict(
        repo,
        f"refs/heads/{contribution_branch}",
        admission,
        {"ok": True, "reachable": True, "blockers": [], "notes": "goal reached"},
    )

    assert published["ok"] is False
    assert published["status"] == "atomic_ref_cas_rejected"
    assert _git(repo, "rev-parse", contribution_branch) == racing_tip
    assert published["resulting_ref_oids"] == {
        f"refs/heads/{contribution_branch}": racing_tip,
        functional_check.ACCEPTANCE_AUTHORITY_NOTES_REF: None,
    }
    assert subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "show-ref",
            "--verify",
            "--quiet",
            functional_check.ACCEPTANCE_AUTHORITY_NOTES_REF,
        ]
    ).returncode != 0


def test_seal_publication_atomically_rejects_a_racing_frozen_series_tip(
    tmp_path, monkeypatch
):
    repo, series_branch, contribution_branch, series_oid, _binding_commit = (
        _producer_claim_repo(tmp_path)
    )
    contribution_oid = _git(repo, "rev-parse", contribution_branch)
    source_vector = functional_check.freeze_acceptance_source_vector(
        repo,
        contribution_branch,
        series_branch,
        contribution_oid=contribution_oid,
        series_oid=series_oid,
    )
    admission = functional_check.prepare_claim_admission(
        repo,
        contribution_branch,
        expected_contribution_branch=contribution_branch,
        frozen_source_vector=source_vector,
    )
    assert admission.admitted is True
    branch_before = _git(repo, "rev-parse", contribution_branch)
    real_update_refs_atomic = git_wrapper.update_refs_atomic
    observed_updates = {}

    def race_series_then_publish(repo_path, updates):
        observed_updates.update(updates)
        git_wrapper.create_ref_with_files(
            repo_path,
            f"refs/heads/{series_branch}",
            {"series-concurrent-change": "later series source\n"},
            subject="patch series: concurrent source change",
            parent=series_oid,
            inherit_parent_tree=True,
        )
        return real_update_refs_atomic(repo_path, updates)

    monkeypatch.setattr(
        git_wrapper, "update_refs_atomic", race_series_then_publish
    )

    published = functional_check._publish_registered_verdict(
        repo,
        f"refs/heads/{contribution_branch}",
        admission,
        {"ok": True, "reachable": True, "blockers": [], "notes": "goal reached"},
    )

    assert observed_updates[f"refs/heads/{series_branch}"] == (
        series_oid,
        series_oid,
    )
    assert published["ok"] is False
    assert published["status"] == "atomic_ref_cas_rejected"
    assert _git(repo, "rev-parse", contribution_branch) == branch_before
    assert _git(repo, "rev-parse", series_branch) != series_oid
    assert subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "show-ref",
            "--verify",
            "--quiet",
            functional_check.ACCEPTANCE_AUTHORITY_NOTES_REF,
        ]
    ).returncode != 0


def test_seal_publication_rejects_an_already_stale_source_before_materializing(
    tmp_path, monkeypatch
):
    repo, series_branch, contribution_branch, series_oid, _binding_commit = (
        _producer_claim_repo(tmp_path)
    )
    contribution_oid = _git(repo, "rev-parse", contribution_branch)
    source_vector = functional_check.freeze_acceptance_source_vector(
        repo,
        contribution_branch,
        series_branch,
        contribution_oid=contribution_oid,
        series_oid=series_oid,
    )
    admission = functional_check.prepare_claim_admission(
        repo,
        contribution_branch,
        expected_contribution_branch=contribution_branch,
        frozen_source_vector=source_vector,
    )
    assert admission.admitted is True
    git_wrapper.create_ref_with_files(
        repo,
        f"refs/heads/{series_branch}",
        {"series-concurrent-change": "later series source\n"},
        subject="patch series: source moved before publication",
        parent=series_oid,
        inherit_parent_tree=True,
    )
    monkeypatch.setattr(
        git_wrapper,
        "create_ref_with_files",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("verdict materialization ran for a stale source vector")
        ),
    )

    published = functional_check._publish_registered_verdict(
        repo,
        f"refs/heads/{contribution_branch}",
        admission,
        {"ok": True, "reachable": True, "blockers": [], "notes": "goal reached"},
    )

    assert published["ok"] is False
    assert published["status"] == "frozen_source_moved"
    assert published["stale_coordinates"] == (series_branch,)
    assert published["prepared_object_oids"] == {}
    assert _git(repo, "rev-parse", contribution_branch) == contribution_oid
    assert subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "show-ref",
            "--verify",
            "--quiet",
            functional_check.ACCEPTANCE_AUTHORITY_NOTES_REF,
        ]
    ).returncode != 0


def test_seal_prepublication_failure_leaves_branch_and_notes_unchanged(
    tmp_path, monkeypatch
):
    repo, _series_branch, contribution_branch, series_oid, _binding_commit = (
        _producer_claim_repo(tmp_path)
    )
    admission = functional_check.prepare_claim_admission(
        repo,
        contribution_branch,
        frozen_series_oid=series_oid,
        expected_contribution_branch=contribution_branch,
    )
    branch_before = _git(repo, "rev-parse", contribution_branch)

    monkeypatch.setattr(
        git_wrapper,
        "update_refs_atomic",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("injected before ref transaction")
        ),
    )

    with pytest.raises(RuntimeError, match="injected before ref transaction"):
        functional_check._publish_registered_verdict(
            repo,
            f"refs/heads/{contribution_branch}",
            admission,
            {
                "ok": True,
                "reachable": True,
                "blockers": [],
                "notes": "goal reached",
            },
        )

    assert _git(repo, "rev-parse", contribution_branch) == branch_before
    assert subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "show-ref",
            "--verify",
            "--quiet",
            functional_check.ACCEPTANCE_AUTHORITY_NOTES_REF,
        ]
    ).returncode != 0


def test_seal_publication_atomically_guards_the_complete_frozen_source_vector(
    tmp_path,
):
    repo, series_branch, contribution_branch, series_oid, _binding_commit = (
        _producer_claim_repo(tmp_path)
    )
    contribution_oid = _git(repo, "rev-parse", contribution_branch)
    source_vector = functional_check.freeze_acceptance_source_vector(
        repo,
        contribution_branch,
        series_branch,
        contribution_oid=contribution_oid,
        series_oid=series_oid,
    )
    admission = functional_check.prepare_claim_admission(
        repo,
        contribution_branch,
        expected_contribution_branch=contribution_branch,
        frozen_source_vector=source_vector,
    )
    assert admission.admitted is True

    published = functional_check._publish_registered_verdict(
        repo,
        f"refs/heads/{contribution_branch}",
        admission,
        {"ok": True, "reachable": True, "blockers": [], "notes": "goal reached"},
    )

    assert published["ok"] is True
    assert _git(repo, "rev-parse", series_branch) == series_oid
    assert functional_check.current_tip_accepted(repo, contribution_branch) is True


def test_series_move_after_admission_rejects_seal_without_partial_publication(
    tmp_path,
):
    repo, series_branch, contribution_branch, series_oid, _binding_commit = (
        _producer_claim_repo(tmp_path)
    )
    contribution_oid = _git(repo, "rev-parse", contribution_branch)
    source_vector = functional_check.freeze_acceptance_source_vector(
        repo,
        contribution_branch,
        series_branch,
        contribution_oid=contribution_oid,
        series_oid=series_oid,
    )
    admission = functional_check.prepare_claim_admission(
        repo,
        contribution_branch,
        expected_contribution_branch=contribution_branch,
        frozen_source_vector=source_vector,
    )
    assert admission.admitted is True

    _git(repo, "checkout", series_branch)
    _git(repo, "commit", "--allow-empty", "-m", "patch series: racing source")
    racing_series_tip = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "main")
    published = functional_check._publish_registered_verdict(
        repo,
        f"refs/heads/{contribution_branch}",
        admission,
        {"ok": True, "reachable": True, "blockers": [], "notes": "goal reached"},
    )

    assert published["ok"] is False
    assert published["status"] == "atomic_ref_cas_rejected"
    assert _git(repo, "rev-parse", contribution_branch) == contribution_oid
    assert _git(repo, "rev-parse", series_branch) == racing_series_tip
    assert subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "show-ref",
            "--verify",
            "--quiet",
            functional_check.ACCEPTANCE_AUTHORITY_NOTES_REF,
        ]
    ).returncode != 0


def test_check_passes_through_when_patchwork_acknowledgement_matches(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _write_patchwork_ack_fixture(repo, {"counter": 2}, branch="ai-org/contrib/ack-match")
    real_run = subprocess.run

    def fake_run(cmd, **kwargs):
        if cmd[0] == "git":
            return real_run(cmd, **kwargs)
        out_file = Path(cmd[cmd.index("-o") + 1])
        out_file.write_text(json.dumps({"reachable": True, "blockers": [], "notes": "reachable"}), encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(functional_check.subprocess, "run", fake_run)

    verdict = functional_check.check(
        repo,
        "ai-org/contrib/ack-match",
        patch_series_branch="ai-org/patch-series/ack-source",
        node_key="leaf",
    )

    assert verdict["ok"] is True
    assert verdict["blockers"] == []
    assert _git(repo, "log", "-1", "--format=%s", "refs/heads/ai-org/contrib/ack-match") == "acceptance: reachable"


def test_check_rejects_patchwork_acknowledgement_mismatch(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _write_patchwork_ack_fixture(repo, {"counter": 1}, branch="ai-org/contrib/ack-mismatch")
    real_run = subprocess.run

    def fake_run(cmd, **kwargs):
        if cmd[0] == "git":
            return real_run(cmd, **kwargs)
        out_file = Path(cmd[cmd.index("-o") + 1])
        out_file.write_text(json.dumps({"reachable": True, "blockers": [], "notes": "reachable"}), encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(functional_check.subprocess, "run", fake_run)

    verdict = functional_check.check(
        repo,
        "ai-org/contrib/ack-mismatch",
        patch_series_branch="ai-org/patch-series/ack-source",
        node_key="leaf",
    )

    assert verdict["ok"] is False
    assert verdict["blockers"][0]["where"] == "implementation-result.json"
    assert "patchwork_check_acknowledgement_mismatch" in verdict["blockers"][0]["why"]
    assert '"mismatched": ["counter"]' in verdict["blockers"][0]["why"]
    assert _git(repo, "log", "-1", "--format=%s", "refs/heads/ai-org/contrib/ack-mismatch") == "acceptance: blocked"


def test_check_appends_patchwork_acknowledgement_blocker_to_codex_blockers(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _write_patchwork_ack_fixture(repo, {"counter": 1}, branch="ai-org/contrib/ack-and-codex")
    real_run = subprocess.run

    def fake_run(cmd, **kwargs):
        if cmd[0] == "git":
            return real_run(cmd, **kwargs)
        out_file = Path(cmd[cmd.index("-o") + 1])
        out_file.write_text(
            json.dumps(
                {
                    "reachable": False,
                    "blockers": [{"where": "app.py:1", "why": "codex blocker"}],
                    "notes": "codex notes",
                }
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(functional_check.subprocess, "run", fake_run)

    verdict = functional_check.check(
        repo,
        "ai-org/contrib/ack-and-codex",
        patch_series_branch="ai-org/patch-series/ack-source",
        node_key="leaf",
    )

    assert verdict["ok"] is False
    assert verdict["blockers"][0] == {"where": "app.py:1", "why": "codex blocker"}
    assert verdict["blockers"][1]["where"] == "implementation-result.json"
    assert "patchwork_check_acknowledgement_mismatch" in verdict["blockers"][1]["why"]
    assert verdict["notes"] == "codex notes"


def test_check_rejects_patchwork_acknowledgement_omission(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _write_patchwork_ack_fixture(repo, {}, branch="ai-org/contrib/ack-omission")
    real_run = subprocess.run

    def fake_run(cmd, **kwargs):
        if cmd[0] == "git":
            return real_run(cmd, **kwargs)
        out_file = Path(cmd[cmd.index("-o") + 1])
        out_file.write_text(json.dumps({"reachable": True, "blockers": [], "notes": "reachable"}), encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(functional_check.subprocess, "run", fake_run)

    verdict = functional_check.check(
        repo,
        "ai-org/contrib/ack-omission",
        patch_series_branch="ai-org/patch-series/ack-source",
        node_key="leaf",
    )

    assert verdict["ok"] is False
    assert "patchwork_check_acknowledgement_mismatch" in verdict["blockers"][0]["why"]
    assert '"missing": ["counter"]' in verdict["blockers"][0]["why"]


def test_check_rejects_spoofed_patchwork_acknowledgement_anchor(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _write_patchwork_ack_fixture(
        repo,
        {},
        branch="ai-org/contrib/ack-spoof",
        declared_branch="ai-org/patch-series/spoof-source",
        declared_node_key="spoof",
    )
    _git(repo, "checkout", "-B", "ai-org/patch-series/spoof-source", "main")
    (repo / "patch-series-manifest.json").write_text(
        json.dumps(
            {
                "schema": "patch_series-network-node-v1",
                "child_key": "root",
                "node_path": ".",
                "lifecycle_status": "merged_into_subsystem_tree",
                "edges": [],
                "declared_patchwork_checks": [],
            }
        ),
        encoding="utf-8",
    )
    (repo / "sub" / "spoof").mkdir(parents=True, exist_ok=True)
    (repo / "sub" / "spoof" / "patch-series-manifest.json").write_text(
        json.dumps(
            {
                "schema": "patch_series-network-node-v1",
                "child_key": "spoof",
                "node_path": "sub/spoof",
                "lifecycle_status": "ready_for_patch_authoring",
                "edges": [],
            }
        ),
        encoding="utf-8",
    )
    _git(repo, "add", "patch-series-manifest.json", "sub/spoof/patch-series-manifest.json")
    _git(repo, "commit", "-m", "network: spoof source")
    _git(repo, "checkout", "main")
    real_run = subprocess.run

    def fake_run(cmd, **kwargs):
        if cmd[0] == "git":
            return real_run(cmd, **kwargs)
        out_file = Path(cmd[cmd.index("-o") + 1])
        out_file.write_text(json.dumps({"reachable": True, "blockers": [], "notes": "reachable"}), encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(functional_check.subprocess, "run", fake_run)

    verdict = functional_check.check(
        repo,
        "ai-org/contrib/ack-spoof",
        patch_series_branch="ai-org/patch-series/ack-source",
        node_key="leaf",
    )

    assert verdict["ok"] is False
    assert "anchor_mismatch" in verdict["blockers"][0]["why"]
    assert "ai-org/patch-series/spoof-source" in verdict["blockers"][0]["why"]


def test_check_rejects_patchwork_acknowledgement_type_coercion(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _write_patchwork_ack_fixture(repo, {"counter": 2.0}, branch="ai-org/contrib/ack-type")
    real_run = subprocess.run

    def fake_run(cmd, **kwargs):
        if cmd[0] == "git":
            return real_run(cmd, **kwargs)
        out_file = Path(cmd[cmd.index("-o") + 1])
        out_file.write_text(json.dumps({"reachable": True, "blockers": [], "notes": "reachable"}), encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(functional_check.subprocess, "run", fake_run)

    verdict = functional_check.check(
        repo,
        "ai-org/contrib/ack-type",
        patch_series_branch="ai-org/patch-series/ack-source",
        node_key="leaf",
    )

    assert verdict["ok"] is False
    assert '"mismatched": ["counter"]' in verdict["blockers"][0]["why"]
    assert '"actual": {"counter": 2.0}' in verdict["blockers"][0]["why"]


def test_check_rejects_stale_patchwork_acknowledgement_after_new_event(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _write_patchwork_ack_fixture(repo, {"counter": 2}, branch="ai-org/contrib/ack-stale")
    _git(repo, "checkout", "ai-org/patch-series/ack-source")
    with (repo / "patchwork-check-events.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"var": "counter", "op": "inc", "value": 1}) + "\n")
    _git(repo, "add", "patchwork-check-events.jsonl")
    _git(repo, "commit", "-m", "network: append stale event")
    _git(repo, "checkout", "main")
    real_run = subprocess.run

    def fake_run(cmd, **kwargs):
        if cmd[0] == "git":
            return real_run(cmd, **kwargs)
        out_file = Path(cmd[cmd.index("-o") + 1])
        out_file.write_text(json.dumps({"reachable": True, "blockers": [], "notes": "reachable"}), encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(functional_check.subprocess, "run", fake_run)

    verdict = functional_check.check(
        repo,
        "ai-org/contrib/ack-stale",
        patch_series_branch="ai-org/patch-series/ack-source",
        node_key="leaf",
    )

    assert verdict["ok"] is False
    assert '"expected": {"counter": 3}' in verdict["blockers"][0]["why"]
    assert '"actual": {"counter": 2}' in verdict["blockers"][0]["why"]


def test_check_requires_patchwork_acknowledgement_on_threaded_anchor(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _write_patchwork_ack_fixture(repo, {"counter": 2}, branch="review/ref-without-prefix")
    _git(repo, "checkout", "review/ref-without-prefix")
    (repo / "implementation-result.json").unlink()
    _git(repo, "add", "-u", "implementation-result.json")
    _git(repo, "commit", "-m", "implement: missing result")
    _git(repo, "checkout", "main")
    real_run = subprocess.run

    def fake_run(cmd, **kwargs):
        if cmd[0] == "git":
            return real_run(cmd, **kwargs)
        out_file = Path(cmd[cmd.index("-o") + 1])
        out_file.write_text(json.dumps({"reachable": True, "blockers": [], "notes": "reachable"}), encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(functional_check.subprocess, "run", fake_run)

    verdict = functional_check.check(
        repo,
        "review/ref-without-prefix",
        patch_series_branch="ai-org/patch-series/ack-source",
        node_key="leaf",
    )

    assert verdict["ok"] is False
    assert "implementation-result.json is missing" in verdict["blockers"][0]["why"]


def test_check_rejects_series_acknowledgement_when_anchor_is_null(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _write_patchwork_ack_fixture(repo, {"counter": 2}, branch="ai-org/contrib/null-anchor")
    real_run = subprocess.run

    def fake_run(cmd, **kwargs):
        if cmd[0] == "git":
            return real_run(cmd, **kwargs)
        out_file = Path(cmd[cmd.index("-o") + 1])
        out_file.write_text(json.dumps({"reachable": True, "blockers": [], "notes": "reachable"}), encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(functional_check.subprocess, "run", fake_run)

    verdict = functional_check.check(repo, "ai-org/contrib/null-anchor")

    assert verdict["ok"] is False
    assert verdict["blockers"][0] == {
        "where": "functional_check",
        "why": 'patchwork_check_acknowledgement_mismatch: {"type": "anchor_missing"}',
    }
    assert _git(repo, "log", "-1", "--format=%s", "refs/heads/ai-org/contrib/null-anchor") == "acceptance: blocked"


def test_check_reports_non_utf8_patchwork_acknowledgement_as_typed_blocker(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _write_patchwork_ack_fixture(repo, {"counter": 2}, branch="ai-org/contrib/ack-non-utf8")
    _git(repo, "checkout", "ai-org/contrib/ack-non-utf8")
    (repo / "implementation-result.json").write_bytes(b"\xff\xfe")
    _git(repo, "add", "implementation-result.json")
    _git(repo, "commit", "-m", "implement: non utf8 result")
    _git(repo, "checkout", "main")
    real_run = subprocess.run

    def fake_run(cmd, **kwargs):
        if cmd[0] == "git":
            return real_run(cmd, **kwargs)
        out_file = Path(cmd[cmd.index("-o") + 1])
        out_file.write_text(json.dumps({"reachable": True, "blockers": [], "notes": "reachable"}), encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(functional_check.subprocess, "run", fake_run)

    verdict = functional_check.check(
        repo,
        "ai-org/contrib/ack-non-utf8",
        patch_series_branch="ai-org/patch-series/ack-source",
        node_key="leaf",
    )

    assert verdict["ok"] is False
    assert "patchwork_check_acknowledgement_mismatch" in verdict["blockers"][0]["why"]
    assert "invalid UTF-8" in verdict["blockers"][0]["why"]


def _write_patchwork_ack_fixture(
    repo: Path,
    acknowledged: dict[str, object],
    *,
    branch: str,
    declared_branch: str = "ai-org/patch-series/ack-source",
    declared_node_key: str = "leaf",
) -> None:
    _git(repo, "checkout", "-B", "ai-org/patch-series/ack-source", "main")
    (repo / "patch-series-manifest.json").write_text(
        json.dumps(
            {
                "schema": "patch_series-network-node-v1",
                "child_key": "root",
                "node_path": ".",
                "lifecycle_status": "merged_into_subsystem_tree",
                "edges": [],
                "declared_patchwork_checks": [{"name": "counter", "type": "counter"}],
            }
        ),
        encoding="utf-8",
    )
    (repo / "sub" / "leaf").mkdir(parents=True, exist_ok=True)
    (repo / "sub" / "leaf" / "patch-series-manifest.json").write_text(
        json.dumps(
            {
                "schema": "patch_series-network-node-v1",
                "child_key": "leaf",
                "node_path": "sub/leaf",
                "lifecycle_status": "ready_for_patch_authoring",
                "edges": [],
            }
        ),
        encoding="utf-8",
    )
    (repo / "patchwork-check-events.jsonl").write_text(json.dumps({"var": "counter", "op": "set", "value": 2}) + "\n", encoding="utf-8")
    _git(repo, "add", "patch-series-manifest.json", "sub/leaf/patch-series-manifest.json", "patchwork-check-events.jsonl")
    _git(repo, "commit", "-m", "network: ack source")
    _git(repo, "checkout", "-B", branch, "main")
    (repo / "app.py").write_text("GOAL = 'reachable'\n", encoding="utf-8")
    (repo / "implementation-result.json").write_text(
        json.dumps(
            {
                "patch_series_branch": declared_branch,
                "node_key": declared_node_key,
                "acknowledged_patchwork_checks": acknowledged,
            }
        ),
        encoding="utf-8",
    )
    _git(repo, "add", "app.py", "implementation-result.json")
    _git(repo, "commit", "-m", "implement: ack")
    _git(repo, "checkout", "main")
