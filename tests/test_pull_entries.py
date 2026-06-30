from __future__ import annotations

from pathlib import Path
import subprocess

from ai_org import merge, patch, rfc


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


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return result.stdout.strip()
