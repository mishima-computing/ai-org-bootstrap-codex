from __future__ import annotations

from types import SimpleNamespace
import subprocess

from ai_org import driver
from ai_org.platform.git import push_ref
from ai_org.rfc.receive import RFC
from ai_org.rfc.task import Task


def test_advance_pushes_visible_refs_to_bare_origin(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    remote = _init_bare_remote(tmp_path)
    _git(repo, "remote", "add", "origin", str(remote))
    monkeypatch.delenv("AI_ORG_REMOTE", raising=False)

    rfc = RFC(title="T", problem="P", proposed_change="C")
    head = _git(repo, "rev-parse", "HEAD").stdout.strip()

    monkeypatch.setattr(driver.review, "run_rfc_review", lambda _rfc, _repo: _review_result("direction-ok"))
    monkeypatch.setattr(driver.decompose, "decompose", lambda _rfc, _repo: [Task(id="p1", objective="O", base_sha=head)])

    def make(_rfc, task):
        return _commit_on_branch(repo, f"refs/heads/ai-org/contrib/{task.id}", f"{task.id}.txt")

    def integrate_subsystem(branch, _repo):
        ref = branch.replace("refs/heads/ai-org/contrib/", "refs/heads/ai-org/subsystem/")
        _git(repo, "update-ref", ref, branch)
        return ref

    def integrate_mainline(refs, _repo):
        _git(repo, "update-ref", driver.MAINLINE_REF, refs[-1])
        return driver.MAINLINE_REF

    monkeypatch.setattr(driver.patch, "make", make)
    monkeypatch.setattr(driver.subsystem, "review_and_integrate", integrate_subsystem)
    monkeypatch.setattr(driver.mainline, "review_and_integrate", integrate_mainline)

    records = [driver.advance(rfc, repo) for _ in range(5)]

    assert records[2]["push"]["status"] == "pushed"
    assert records[2]["state_push"]["status"] == "pushed"
    assert records[3]["push"]["status"] == "pushed"
    assert records[4]["push"]["status"] == "pushed"
    assert _ref_exists(remote, "refs/heads/ai-org/contrib/p1")
    assert _ref_exists(remote, "refs/heads/ai-org/subsystem/p1")
    assert _ref_exists(remote, driver.MAINLINE_REF)
    assert _ref_exists(remote, driver.STATE_REF)


def test_push_ref_skips_gracefully_without_remote(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    monkeypatch.delenv("AI_ORG_REMOTE", raising=False)

    result = push_ref(repo, "refs/heads/main")

    assert result == {
        "status": "skipped",
        "reason": "no remote",
        "remote": "origin",
        "ref": "refs/heads/main",
    }


def test_push_ref_uses_env_remote_when_no_arg(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    remote = _init_bare_remote(tmp_path)
    _git(repo, "remote", "add", "upstream", str(remote))
    monkeypatch.setenv("AI_ORG_REMOTE", "upstream")

    result = push_ref(repo, "refs/heads/main")

    assert result["status"] == "pushed"
    assert result["remote"] == "upstream"
    assert _ref_exists(remote, "refs/heads/main")


def test_push_ref_arg_overrides_env_remote(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    remote = _init_bare_remote(tmp_path)
    _git(repo, "remote", "add", "origin", str(remote))
    monkeypatch.setenv("AI_ORG_REMOTE", "missing")

    result = push_ref(repo, "refs/heads/main", remote="origin")

    assert result["status"] == "pushed"
    assert result["remote"] == "origin"
    assert _ref_exists(remote, "refs/heads/main")


def _review_result(status: str):
    return SimpleNamespace(
        status=status,
        rounds=1,
        final_view="",
        resolved=[],
        unresolved=[],
        history=[],
        escalation_reason="",
    )


def _init_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "config", "user.email", "test@example.invalid")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "base")
    _git(repo, "branch", "-M", "main")
    return repo


def _init_bare_remote(tmp_path):
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "--bare", str(remote))
    return remote


def _commit_on_branch(repo, ref, filename):
    branch = ref.removeprefix("refs/heads/")
    _git(repo, "checkout", "-B", branch, "main")
    (repo / filename).write_text(f"{filename}\n", encoding="utf-8")
    _git(repo, "add", filename)
    _git(repo, "commit", "-m", f"add {filename}")
    _git(repo, "checkout", "main")
    return ref


def _ref_exists(repo, ref):
    return (
        subprocess.run(
            ["git", "-C", str(repo), "show-ref", "--verify", "--quiet", ref],
            check=False,
        ).returncode
        == 0
    )


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
