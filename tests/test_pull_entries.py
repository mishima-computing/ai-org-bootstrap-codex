from __future__ import annotations

import json
from pathlib import Path
import subprocess

from ai_org import merge, patch, rfc
from ai_org import git_wrapper
from ai_org.rfc import submit as submit_module


def test_rfc_pull_reviews_one_unreviewed_rfc(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    _commit_on_branch(repo, "ai-org/rfc/already-ok", "rfc: direction-ok")
    _commit_on_branch(repo, "ai-org/rfc/already-nak", "rfc: nak")
    _commit_on_branch(repo, "ai-org/rfc/pending", "propose rfc")
    calls = []
    result = {"status": "reviewed"}

    def fake_review(repo_arg, rfc_id):
        calls.append((repo_arg, rfc_id))
        return result

    monkeypatch.setattr(rfc.review, "run_rfc_review", fake_review)

    assert rfc.pull(repo) is result
    assert calls == [(repo, "pending")]


def test_rfc_pull_returns_none_when_no_rfc_is_pending(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    _commit_on_branch(repo, "ai-org/rfc/already-ok", "rfc: direction-ok")
    _commit_on_branch(repo, "ai-org/rfc/already-nak", "rfc: nak")
    monkeypatch.setattr(
        rfc.review,
        "run_rfc_review",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not review")),
    )

    assert rfc.pull(repo) is None


def test_rfc_pull_processes_one_inbox_item_then_falls_back_to_review(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    inbox_id = _write_inbox_request(repo, "Build inbox promoted request.")
    calls = []

    def fake_intake(request, repo_arg, **kwargs):
        calls.append((request, repo_arg, kwargs))
        git_wrapper.create_branch_with_files(
            repo_arg,
            "ai-org/rfc/inbox-promoted",
            "main",
            {
                "rfc.json": {"working_title": "Inbox Promoted"},
                "technical-approach.json": {"approach": "test"},
            },
            commit_message="rfc: receive Inbox Promoted",
        )
        return {
            "ok": True,
            "status": "promoted",
            "id": "inbox-promoted",
            "branch": "ai-org/rfc/inbox-promoted",
        }

    review_result = {"status": "reviewed"}
    review_calls = []
    monkeypatch.setattr(rfc.receive, "intake", fake_intake)
    monkeypatch.setattr(
        rfc.review,
        "run_rfc_review",
        lambda repo_arg, rfc_id: review_calls.append((repo_arg, rfc_id)) or review_result,
    )

    result = rfc.pull(repo)

    assert result["status"] == "promoted"
    assert calls == [
        (
            {"raw_request": "Build inbox promoted request."},
            repo,
            {"progress_path": None},
        )
    ]
    assert _git(repo, "show", "ai-org/rfc/inbox-promoted:rfc.json")
    assert _git(repo, "show", "ai-org/rfc/inbox-promoted:technical-approach.json")
    processed = submit_module.inbox_dir(repo) / "processed"
    assert (processed / f"{inbox_id}.json").exists()
    result_record = json.loads((processed / f"{inbox_id}.result.json").read_text(encoding="utf-8"))
    assert result_record["status"] == "promoted"
    assert result_record["rfc_branch"] == "ai-org/rfc/inbox-promoted"
    assert result_record["rfc_id"] == "inbox-promoted"

    assert rfc.pull(repo) is review_result
    assert calls == [
        (
            {"raw_request": "Build inbox promoted request."},
            repo,
            {"progress_path": None},
        )
    ]
    assert review_calls == [(repo, "inbox-promoted")]


def test_rfc_pull_needs_work_moves_inbox_record_without_git_branch_or_retry(tmp_path, monkeypatch):
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

    monkeypatch.setattr(rfc.receive, "intake", fake_intake)
    monkeypatch.setattr(
        rfc.review,
        "run_rfc_review",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not review")),
    )

    result = rfc.pull(repo)

    assert result["status"] == "needs_work"
    assert _git_status(repo, "show-ref", "--verify", "--quiet", "refs/heads/ai-org/rfc/inbox-needs-work") != 0
    processed = submit_module.inbox_dir(repo) / "processed"
    result_record = json.loads((processed / f"{inbox_id}.result.json").read_text(encoding="utf-8"))
    assert result_record["status"] == "needs_work"
    assert result_record["error"] == "Could not form approach."
    assert result_record["failed_step"] == "select_approach"
    assert rfc.pull(repo) is None
    assert calls == [{"raw_request": "Build inbox needs work request."}]


def test_inbox_state_is_ignored_by_git_after_submit_and_pull_cycle(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    (repo / ".gitignore").write_text(".pytest_cache/\n", encoding="utf-8")
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-m", "track gitignore")
    submit_module.submit(repo, "Build an ignored inbox request.")

    def fake_intake(_request, repo_arg, **_kwargs):
        git_wrapper.create_branch_with_files(
            repo_arg,
            "ai-org/rfc/ignored-inbox",
            "main",
            {"rfc.json": {"ok": True}, "technical-approach.json": {"ok": True}},
            commit_message="rfc: receive Ignored Inbox",
        )
        return {
            "ok": True,
            "status": "promoted",
            "id": "ignored-inbox",
            "branch": "ai-org/rfc/ignored-inbox",
        }

    monkeypatch.setattr(rfc.receive, "intake", fake_intake)

    assert rfc.pull(repo)["status"] == "promoted"
    status = _git(repo, "status", "--short")

    assert ".ai-org/" not in status
    assert ".ai-org" not in status
    assert " .gitignore" in status or ".gitignore" in status


def test_patch_pull_implements_one_direction_ok_rfc_without_contribution(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    _commit_on_branch(repo, "ai-org/rfc/no-direction", "propose rfc")
    _commit_on_branch(repo, "ai-org/rfc/ready", "rfc: direction-ok")
    _commit_on_branch(repo, "ai-org/rfc/with-contrib", "rfc: direction-ok")
    _commit_on_branch(repo, "ai-org/contrib/with-contrib", "contribution exists")
    calls = []
    result = {"ok": True, "branch": "ai-org/contrib/ready"}

    def fake_make(repo_arg, rfc_id):
        calls.append((repo_arg, rfc_id))
        return result

    monkeypatch.setattr(patch, "make", fake_make)

    assert patch.pull(repo) is result
    assert calls == [(repo, "ready")]


def test_patch_pull_does_not_reselect_rfc_after_make_creates_matching_contribution_branch(
    tmp_path, monkeypatch
):
    repo = _init_repo(tmp_path)
    rfc_id = "stable-rfc-id"
    _commit_on_branch(repo, f"ai-org/rfc/{rfc_id}", "rfc: direction-ok")
    calls = []

    def fake_make(repo_arg, selected_rfc_id):
        calls.append((repo_arg, selected_rfc_id))
        branch = f"ai-org/contrib/{selected_rfc_id}"
        _git(repo_arg, "branch", branch, "main")
        return {"ok": True, "branch": branch}

    monkeypatch.setattr(patch, "make", fake_make)

    result = patch.pull(repo)

    assert result == {"ok": True, "branch": f"ai-org/contrib/{rfc_id}"}
    assert result["branch"] == "ai-org/contrib/stable-rfc-id"
    assert patch.pull(repo) is None
    assert calls == [(repo, rfc_id)]


def test_patch_pull_returns_none_when_no_rfc_needs_contribution(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    _commit_on_branch(repo, "ai-org/rfc/no-direction", "propose rfc")
    _commit_on_branch(repo, "ai-org/rfc/with-contrib", "rfc: direction-ok")
    _commit_on_branch(repo, "ai-org/contrib/with-contrib", "contribution exists")
    monkeypatch.setattr(
        patch,
        "make",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not implement")),
    )

    assert patch.pull(repo) is None


def test_merge_pull_integrates_one_accepted_contribution_first(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    _commit_on_branch(repo, "ai-org/contrib/not-accepted", "contribution exists")
    _commit_on_branch(repo, "ai-org/contrib/ready", "acceptance: reachable")
    calls = []
    result = {"accept": True, "ref": "refs/heads/ai-org/subsystem", "reasons": ["ok"]}

    def fake_subsystem(repo_arg, branch):
        calls.append((repo_arg, branch))
        return result

    monkeypatch.setattr(merge.subsystem, "review_and_integrate", fake_subsystem)
    monkeypatch.setattr(
        merge.mainline,
        "review_and_integrate",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not mainline")),
    )

    assert merge.pull(repo) is result
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
        merge.subsystem,
        "review_and_integrate",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not subsystem")),
    )
    monkeypatch.setattr(merge.mainline, "review_and_integrate", fake_mainline)

    assert merge.pull(repo) is result
    assert calls == [repo]


def test_merge_pull_returns_none_when_no_integration_is_pending(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    _git(repo, "branch", "ai-org/subsystem", "main")
    _git(repo, "branch", "ai-org/mainline", "main")
    monkeypatch.setattr(
        merge.subsystem,
        "review_and_integrate",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not subsystem")),
    )
    monkeypatch.setattr(
        merge.mainline,
        "review_and_integrate",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not mainline")),
    )

    assert merge.pull(repo) is None


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


def _write_inbox_request(repo: Path, raw_request: str) -> str:
    inbox = submit_module.ensure_inbox(repo)
    inbox_id = raw_request.lower().replace(".", "").replace(" ", "-")
    path = inbox / f"{inbox_id}.json"
    path.write_text(
        json.dumps({"id": inbox_id, "submitted_at": "2026-07-02T00:00:00+00:00", "request": {"raw_request": raw_request}}),
        encoding="utf-8",
    )
    return inbox_id


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
