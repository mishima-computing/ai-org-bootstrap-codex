from __future__ import annotations

from pathlib import Path
import subprocess

from ai_org import tracking


def test_list_rfcs_returns_ids_from_rfc_branches(tmp_path):
    repo = _init_repo(tmp_path)
    _branch_with_commit(repo, "ai-org/rfc/alpha", "rfc: alpha")
    _branch_with_commit(repo, "ai-org/rfc/beta", "rfc: beta")
    _branch_with_commit(repo, "other/topic", "other")

    assert tracking.list_rfcs(repo) == ["alpha", "beta"]


def test_proposed_stage_and_next_action(tmp_path):
    repo = _init_repo(tmp_path)
    _branch_with_commit(repo, "ai-org/rfc/proposed", "rfc: proposed")

    assert tracking.stage_of(repo, "proposed") == "proposed"
    assert tracking.next_action(repo, "proposed") == "review"


def test_direction_ok_stage_and_next_action(tmp_path):
    repo = _init_repo(tmp_path)
    _branch_with_commit(repo, "ai-org/rfc/direction-ok", "rfc: proposed")
    _commit_on_branch(repo, "ai-org/rfc/direction-ok", "rfc: direction-ok")

    assert tracking.stage_of(repo, "direction-ok") == "direction-ok"
    assert tracking.next_action(repo, "direction-ok") == "make"


def test_accepted_stage_and_next_action(tmp_path):
    repo = _init_repo(tmp_path)
    _branch_with_commit(repo, "ai-org/rfc/accepted", "rfc: direction-ok")
    _branch_with_commit(repo, "ai-org/contrib/accepted", "patch: accepted")
    _commit_on_branch(repo, "ai-org/contrib/accepted", "acceptance: reachable")

    assert tracking.stage_of(repo, "accepted") == "accepted"
    assert tracking.next_action(repo, "accepted") == "subsystem"


def test_in_subsystem_stage_and_next_action(tmp_path):
    repo = _init_repo(tmp_path)
    _branch_with_commit(repo, "ai-org/rfc/in-subsystem", "rfc: direction-ok")
    _branch_with_commit(repo, "ai-org/contrib/in-subsystem", "patch: in-subsystem")
    _commit_on_branch(repo, "ai-org/contrib/in-subsystem", "acceptance: reachable")
    _branch_at(repo, "ai-org/subsystem", "ai-org/contrib/in-subsystem")

    assert tracking.stage_of(repo, "in-subsystem") == "in-subsystem"
    assert tracking.next_action(repo, "in-subsystem") == "mainline"


def test_merged_stage_and_next_action(tmp_path):
    repo = _init_repo(tmp_path)
    _branch_with_commit(repo, "ai-org/rfc/merged", "rfc: direction-ok")
    _branch_with_commit(repo, "ai-org/contrib/merged", "patch: merged")
    _commit_on_branch(repo, "ai-org/contrib/merged", "acceptance: reachable")
    _branch_at(repo, "ai-org/subsystem", "ai-org/contrib/merged")
    _branch_at(repo, "ai-org/mainline", "ai-org/contrib/merged")

    assert tracking.stage_of(repo, "merged") == "merged"
    assert tracking.next_action(repo, "merged") == "done"


def test_rejected_stage_and_next_action(tmp_path):
    repo = _init_repo(tmp_path)
    _branch_with_commit(repo, "ai-org/rfc/rejected", "rfc: proposed")
    _commit_on_branch(repo, "ai-org/rfc/rejected", "rfc: direction-ok")
    _commit_on_branch(repo, "ai-org/rfc/rejected", "rfc: nak")

    assert tracking.stage_of(repo, "rejected") == "rejected"
    assert tracking.next_action(repo, "rejected") == "none"


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.name", "Tracking Test")
    _git(repo, "config", "user.email", "tracking-test@example.invalid")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "base")
    _git(repo, "branch", "-M", "master")
    return repo


def _branch_with_commit(repo: Path, branch: str, message: str) -> None:
    _git(repo, "checkout", "-B", branch, "master")
    _write_branch_file(repo, branch, message)
    _git(repo, "checkout", "master")


def _commit_on_branch(repo: Path, branch: str, message: str) -> None:
    _git(repo, "checkout", branch)
    _write_branch_file(repo, branch, message)
    _git(repo, "checkout", "master")


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
