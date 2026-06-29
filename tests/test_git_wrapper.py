from __future__ import annotations

from pathlib import Path
import subprocess

from ai_org import git_wrapper


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


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
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
