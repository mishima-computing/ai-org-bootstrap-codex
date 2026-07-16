from __future__ import annotations

import json
from pathlib import Path
import subprocess

import ai_org.log as org_log
from ai_org import maintainer_merge, patchwork_queue as patch_series
from ai_org import git_wrapper
from ai_org.patchwork_queue import submit as submit_module


def test_patch_series_pull_reviews_one_unreviewed_patch_series(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    _commit_on_branch(repo, "ai-org/patch-series/already-ok", "patch_series: direction-ok")
    _commit_on_branch(repo, "ai-org/patch-series/already-nak", "patch_series: nak")
    _commit_on_branch(repo, "ai-org/patch-series/waiting-v2", "patch_series: needs-revision round 1")
    _commit_on_branch(repo, "ai-org/patch-series/pending", "propose patch_series")
    calls = []
    result = {"status": "reviewed"}

    def fake_review(repo_arg, patch_series_id):
        calls.append((repo_arg, patch_series_id))
        return result

    monkeypatch.setattr(patch_series.review, "run_patch_series_review", fake_review)

    assert patch_series.pull(repo) is result
    assert calls == [(repo, "pending")]


def test_patch_series_pull_returns_none_when_no_patch_series_is_pending(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    _commit_on_branch(repo, "ai-org/patch-series/already-ok", "patch_series: direction-ok")
    _commit_on_branch(repo, "ai-org/patch-series/already-nak", "patch_series: nak")
    _commit_on_branch(repo, "ai-org/patch-series/waiting-v2", "patch_series: needs-revision round 1")
    monkeypatch.setattr(
        patch_series.review,
        "run_patch_series_review",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not review")),
    )

    assert patch_series.pull(repo) is None


def test_patch_series_pull_processes_one_inbox_item_then_falls_back_to_review(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    inbox_id = _write_inbox_request(repo, "Build inbox promoted request.")
    calls = []

    def fake_intake(request, repo_arg, **kwargs):
        calls.append((request, repo_arg, kwargs))
        git_wrapper.create_branch_with_files(
            repo_arg,
            "ai-org/patch-series/inbox-promoted",
            "main",
            {
                "patch-series-cover-letter.json": {"working_title": "Inbox Promoted"},
                "technical-approach-plan.json": {"approach": "test"},
            },
            commit_message="patch_series: receive Inbox Promoted",
        )
        return {
            "ok": True,
            "status": "promoted",
            "id": "inbox-promoted",
            "branch": "ai-org/patch-series/inbox-promoted",
        }

    review_result = {"status": "reviewed"}
    review_calls = []
    monkeypatch.setattr(patch_series.receive, "intake", fake_intake)
    monkeypatch.setattr(
        patch_series.review,
        "run_patch_series_review",
        lambda repo_arg, patch_series_id: review_calls.append((repo_arg, patch_series_id)) or review_result,
    )

    result = patch_series.pull(repo)

    assert result["status"] == "promoted"
    assert calls == [
        (
            {"raw_request": "Build inbox promoted request."},
            repo,
            {"progress_path": None},
        )
    ]
    assert _git(repo, "show", "ai-org/patch-series/inbox-promoted:patch-series-cover-letter.json")
    assert _git(repo, "show", "ai-org/patch-series/inbox-promoted:technical-approach-plan.json")
    processed = submit_module.inbox_dir(repo) / "processed"
    assert (processed / f"{inbox_id}.json").exists()
    result_record = json.loads((processed / f"{inbox_id}.result.json").read_text(encoding="utf-8"))
    assert result_record["status"] == "promoted"
    assert result_record["patch_series_branch"] == "ai-org/patch-series/inbox-promoted"
    assert result_record["patch_series_id"] == "inbox-promoted"

    assert patch_series.pull(repo) is review_result
    assert calls == [
        (
            {"raw_request": "Build inbox promoted request."},
            repo,
            {"progress_path": None},
        )
    ]
    assert review_calls == [(repo, "inbox-promoted")]


def test_patch_series_pull_orders_inbox_before_author_reform_before_review(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    _write_inbox_request(repo, "Build inbox request.")
    _commit_on_branch(repo, "ai-org/patch-series/waiting-v2", "patch_series: needs-revision round 1")
    _write_review_round(repo, "waiting-v2")
    _commit_on_branch(repo, "ai-org/patch-series/reviewable", "propose patch_series")
    calls: list[str] = []

    def fake_intake(_request, _repo_arg, **_kwargs):
        calls.append("inbox")
        return {"status": "needs_work"}

    def fake_reform(_repo_arg, patch_series_id):
        calls.append(f"reform:{patch_series_id}")
        return {"status": "reformed"}

    def fake_review(_repo_arg, patch_series_id):
        calls.append(f"review:{patch_series_id}")
        return {"status": "reviewed"}

    monkeypatch.setattr(patch_series.receive, "intake", fake_intake)
    monkeypatch.setattr(patch_series.receive, "reform_patch_series", fake_reform)
    monkeypatch.setattr(patch_series.review, "run_patch_series_review", fake_review)

    assert patch_series.pull(repo)["status"] == "needs_work"
    assert patch_series.pull(repo)["status"] == "reformed"
    _empty_commit_on_existing_branch(repo, "ai-org/patch-series/waiting-v2", "patch_series v2: Waiting")
    assert patch_series.pull(repo)["status"] == "reviewed"
    assert calls == ["inbox", "reform:waiting-v2", "review:reviewable"]


def test_needs_revision_branch_is_reviewable_after_v2_commit(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    _commit_on_branch(repo, "ai-org/patch-series/waiting-v2", "patch_series: needs-revision round 1")
    _write_review_round(repo, "waiting-v2")
    _empty_commit_on_existing_branch(repo, "ai-org/patch-series/waiting-v2", "patch_series v2: Waiting")
    review_calls = []
    monkeypatch.setattr(
        patch_series.review,
        "run_patch_series_review",
        lambda repo_arg, patch_series_id: review_calls.append((repo_arg, patch_series_id)) or {"status": "reviewed"},
    )
    monkeypatch.setattr(
        patch_series.receive,
        "reform_patch_series",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not reform")),
    )

    assert patch_series.pull(repo)["status"] == "reviewed"
    assert review_calls == [(repo, "waiting-v2")]


def test_newer_needs_revision_reopens_direction_ok_branch_for_reform(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    _commit_on_branch(repo, "ai-org/patch-series/reopened", "patch_series: direction-ok")
    _empty_commit_on_existing_branch(repo, "ai-org/patch-series/reopened", "patch_series: needs-revision round 1")
    _write_review_round(repo, "reopened")
    calls = []
    monkeypatch.setattr(
        patch_series.receive,
        "reform_patch_series",
        lambda repo_arg, patch_series_id: calls.append((repo_arg, patch_series_id)) or {"status": "reformed"},
    )
    monkeypatch.setattr(
        patch_series.review,
        "run_patch_series_review",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not review")),
    )

    assert patch_series.pull(repo)["status"] == "reformed"
    assert calls == [(repo, "reopened")]


def test_reformed_direction_ok_branch_is_reviewable_after_v2_commit(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    _commit_on_branch(repo, "ai-org/patch-series/reopened", "patch_series: direction-ok")
    _empty_commit_on_existing_branch(repo, "ai-org/patch-series/reopened", "patch_series: needs-revision round 1")
    _write_review_round(repo, "reopened")
    _empty_commit_on_existing_branch(repo, "ai-org/patch-series/reopened", "patch_series v2: Reopened")
    calls = []
    monkeypatch.setattr(
        patch_series.receive,
        "reform_patch_series",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not reform")),
    )
    monkeypatch.setattr(
        patch_series.review,
        "run_patch_series_review",
        lambda repo_arg, patch_series_id: calls.append((repo_arg, patch_series_id)) or {"status": "reviewed"},
    )

    assert patch_series.pull(repo)["status"] == "reviewed"
    assert calls == [(repo, "reopened")]


def test_patch_series_pull_needs_work_moves_inbox_record_without_git_branch_or_retry(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    inbox_id = _write_inbox_request(repo, "Build inbox needs work request.")
    calls = []

    def fake_intake(request, repo_arg, **kwargs):
        calls.append(request)
        return {
            "ok": False,
            "status": "needs_work",
            "error": "Could not form approach.",
            "failed_step": "select_approach",
        }

    monkeypatch.setattr(patch_series.receive, "intake", fake_intake)
    monkeypatch.setattr(
        patch_series.review,
        "run_patch_series_review",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not review")),
    )

    result = patch_series.pull(repo)

    assert result["status"] == "needs_work"
    assert _git_status(repo, "show-ref", "--verify", "--quiet", "refs/heads/ai-org/patch-series/inbox-needs-work") != 0
    processed = submit_module.inbox_dir(repo) / "processed"
    result_record = json.loads((processed / f"{inbox_id}.result.json").read_text(encoding="utf-8"))
    assert result_record["status"] == "needs_work"
    assert result_record["error"] == "Could not form approach."
    assert result_record["failed_step"] == "select_approach"
    outcome_ref = f"refs/ai-org/request-outcomes/{inbox_id}"
    assert _git(repo, "show-ref", "--verify", outcome_ref)
    provenance = git_wrapper.read_request_provenance(repo, outcome_ref)
    outcome = git_wrapper.read_request_outcome(repo, outcome_ref)
    assert provenance["request_id"] == inbox_id
    assert provenance["raw_request"] == "Build inbox needs work request."
    assert outcome["request_id"] == inbox_id
    assert outcome["status"] == "needs_work"
    supervisor = next((Path(repo) / ".ai-org" / "log" / "runs").glob("*/run-*/supervisor.jsonl"))
    events = [json.loads(line) for line in supervisor.read_text(encoding="utf-8").splitlines()]
    outcome = [event for event in events if event["event_type"] == "patch_series.pull.outcome"][-1]
    assert outcome["payload"] == {"failed_step": "select_approach", "status": "needs_work"}
    projections = org_log.rebuild_projections(repo, supervisor.parent.name)
    assert projections["run_status"]["status"] == "completed"
    assert projections["run_status"]["outcome"] == "needs_work"
    assert projections["run_status"]["failed_step"] == "select_approach"
    assert "completed outcome=needs_work failed_step=select_approach" in projections["canonical_run_line"]["line"]
    assert patch_series.pull(repo) is None
    assert calls == [{"raw_request": "Build inbox needs work request."}]


def test_terminal_publication_failure_leaves_inbox_and_prior_results_unchanged(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    inbox_id = _write_inbox_request(repo, "Build unpublished request.")
    inbox = submit_module.inbox_dir(repo)
    prior = inbox / "processed" / "prior.result.json"
    prior.write_text('{"status":"prior"}\n', encoding="utf-8")
    before = prior.read_bytes()
    monkeypatch.setattr(
        patch_series.receive,
        "intake",
        lambda *_args, **_kwargs: {"ok": False, "status": "needs_work"},
    )
    monkeypatch.setattr(
        patch_series,
        "_record_terminal_request_ref",
        lambda *_args, **_kwargs: git_wrapper.GitPublicationResult(
            "rejected", f"refs/ai-org/request-outcomes/{inbox_id}", "",
            failure=git_wrapper.GitBodyFailure("GIT_CAS", "request-outcomes", "injected"),
        ),
    )

    result = patch_series.pull(repo)

    assert result["status"] == "publication_failed"
    assert (inbox / f"{inbox_id}.json").exists()
    assert not (inbox / "processed" / f"{inbox_id}.json").exists()
    assert prior.read_bytes() == before


def test_injected_terminal_publication_failure_after_preparation_preserves_external_state(
    tmp_path, monkeypatch
):
    repo = _init_repo(tmp_path)
    inbox_id = _write_inbox_request(repo, "Build an injected unpublished request.")
    inbox = submit_module.inbox_dir(repo)
    prior = inbox / "processed" / "prior.result.json"
    prior.write_text('{"status":"prior"}\n', encoding="utf-8")
    prior_before = prior.read_bytes()
    monkeypatch.setattr(
        patch_series.receive,
        "intake",
        lambda *_args, **_kwargs: {"ok": False, "status": "needs_work"},
    )
    publish = git_wrapper.publish_terminal_request
    prepared_commits: list[str] = []

    def reject_after_preparation(repo_arg, prepared):
        prepared_commits.append(prepared.commit_oid)
        return publish(repo_arg, prepared, inject_failure=True)

    monkeypatch.setattr(git_wrapper, "publish_terminal_request", reject_after_preparation)

    result = patch_series.pull(repo)

    ref = f"refs/ai-org/request-outcomes/{inbox_id}"
    assert result["status"] == "publication_failed"
    assert len(prepared_commits) == 1
    assert (
        _git(repo, "rev-parse", "--verify", f"{prepared_commits[0]}^{{commit}}")
        == prepared_commits[0]
    )
    assert git_wrapper.head_sha(repo, ref) is None
    assert (inbox / f"{inbox_id}.json").exists()
    assert not (inbox / "processed" / f"{inbox_id}.json").exists()
    assert not (inbox / "processed" / f"{inbox_id}.result.json").exists()
    assert prior.read_bytes() == prior_before


def test_historical_pair_mismatch_leaves_inbox_and_custom_ref_unchanged(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    inbox_id = _write_inbox_request(repo, "Build against mismatched history.")
    inbox = submit_module.inbox_dir(repo)
    ref = f"refs/ai-org/request-outcomes/{inbox_id}"
    parent = git_wrapper.head_sha(repo, "main")
    assert parent is not None
    historical = git_wrapper.create_ref_with_files(
        repo, ref,
        {
            git_wrapper.REQUEST_PROVENANCE_ALIASES[0]: {
                "request_id": "different-request",
                "payload_sha256": "0" * 64,
                "raw_request": "historical",
                "request_payload": {},
                "memento": "historical provenance",
            },
            git_wrapper.REQUEST_OUTCOME_ALIASES[0]: {
                "request_id": inbox_id,
                "status": "needs_work",
                "result": {},
                "memento": "historical outcome",
            },
        },
        subject="mismatched historical outcome", parent=parent,
    )
    monkeypatch.setattr(
        patch_series.receive,
        "intake",
        lambda *_args, **_kwargs: {"ok": False, "status": "needs_work"},
    )

    result = patch_series.pull(repo)

    assert result["status"] == "publication_failed"
    assert (inbox / f"{inbox_id}.json").exists()
    assert not (inbox / "processed" / f"{inbox_id}.result.json").exists()
    assert git_wrapper.head_sha(repo, ref) == historical["commit"]


def test_inbox_state_is_ignored_by_git_after_submit_and_pull_cycle(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    (repo / ".gitignore").write_text(".pytest_cache/\n", encoding="utf-8")
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-m", "track gitignore")
    submit_module.submit(repo, "Build an ignored inbox request.")

    def fake_intake(_request, repo_arg, **_kwargs):
        git_wrapper.create_branch_with_files(
            repo_arg,
            "ai-org/patch-series/ignored-inbox",
            "main",
            {"patch-series-cover-letter.json": {"ok": True}, "technical-approach-plan.json": {"ok": True}},
            commit_message="patch_series: receive Ignored Inbox",
        )
        return {
            "ok": True,
            "status": "promoted",
            "id": "ignored-inbox",
            "branch": "ai-org/patch-series/ignored-inbox",
        }

    monkeypatch.setattr(patch_series.receive, "intake", fake_intake)

    assert patch_series.pull(repo)["status"] == "promoted"
    status = _git(repo, "status", "--short")

    assert ".ai-org/" not in status
    assert ".ai-org" not in status
    assert " .gitignore" in status or ".gitignore" in status


def test_merge_pull_integrates_one_accepted_contribution_first(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    _commit_on_branch(repo, "ai-org/contrib/not-accepted", "contribution exists")
    _commit_on_branch(repo, "ai-org/contrib/ready", "acceptance: reachable")
    calls = []
    result = {"accept": True, "ref": "refs/heads/ai-org/subsystem", "reasons": ["ok"]}

    def fake_subsystem(repo_arg, branch):
        calls.append((repo_arg, branch))
        return result

    monkeypatch.setattr(maintainer_merge.subsystem, "review_and_integrate", fake_subsystem)
    monkeypatch.setattr(
        maintainer_merge.mainline,
        "review_and_integrate",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not mainline")),
    )

    assert maintainer_merge.pull(repo) is result
    assert calls == [(repo, "ai-org/contrib/ready")]


def test_merge_pull_integrates_subsystem_when_no_contribution_is_pending(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    _git(repo, "branch", "ai-org/mainline", "main")
    _commit_on_branch(repo, "ai-org/subsystem", "subsystem work")
    calls = []
    result = {"accept": True, "ref": "refs/heads/ai-org/mainline", "reasons": ["ready"]}

    def fake_mainline(repo_arg):
        calls.append(repo_arg)
        return result

    monkeypatch.setattr(
        maintainer_merge.subsystem,
        "review_and_integrate",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not subsystem")),
    )
    monkeypatch.setattr(maintainer_merge.mainline, "review_and_integrate", fake_mainline)

    assert maintainer_merge.pull(repo) is result
    assert calls == [repo]


def test_merge_pull_returns_none_when_no_integration_is_pending(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    _git(repo, "branch", "ai-org/subsystem", "main")
    _git(repo, "branch", "ai-org/mainline", "main")
    monkeypatch.setattr(
        maintainer_merge.subsystem,
        "review_and_integrate",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not subsystem")),
    )
    monkeypatch.setattr(
        maintainer_merge.mainline,
        "review_and_integrate",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not mainline")),
    )

    assert maintainer_merge.pull(repo) is None


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.name", "Pull Test")
    _git(repo, "config", "user.email", "pull-test@example.invalid")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "base")
    _git(repo, "branch", "-M", "main")
    return repo


def _commit_on_branch(repo: Path, branch: str, subject: str) -> None:
    _git(repo, "checkout", "-B", branch, "main")
    _git(repo, "commit", "--allow-empty", "-m", subject)
    _git(repo, "checkout", "main")


def _empty_commit_on_existing_branch(repo: Path, branch: str, subject: str) -> None:
    _git(repo, "checkout", branch)
    _git(repo, "commit", "--allow-empty", "-m", subject)
    _git(repo, "checkout", "main")


def _write_inbox_request(repo: Path, raw_request: str) -> str:
    inbox = submit_module.ensure_inbox(repo)
    inbox_id = raw_request.lower().replace(".", "").replace(" ", "-")
    path = inbox / f"{inbox_id}.json"
    path.write_text(
        json.dumps({"id": inbox_id, "submitted_at": "2026-07-02T00:00:00+00:00", "request": {"raw_request": raw_request}}),
        encoding="utf-8",
    )
    return inbox_id


def _write_review_round(repo: Path, patch_series_id: str) -> None:
    directory = repo / ".ai-org" / "review" / patch_series_id
    directory.mkdir(parents=True, exist_ok=True)
    record = {
        "patch_series_id": patch_series_id,
        "branch": f"ai-org/patch-series/{patch_series_id}",
        "round": 1,
        "objections": [
            {
                "objection_id": "approach:1",
                "anchor_node_ids": ["decision:one"],
                "axis": "approach",
                "type": "blocking",
                "claim": "Needs v2.",
                "evidence": [],
                "impact": "Review cannot proceed.",
                "requested_author_action": "revise_subtree",
                "status": "open",
            }
        ],
        "verdict": "needs_revision",
    }
    (directory / "round-1.json").write_text(json.dumps(record) + "\n", encoding="utf-8")


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return result.stdout.strip()


def _git_status(repo: Path, *args: str) -> int:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return result.returncode
