from __future__ import annotations

from pathlib import Path
import subprocess

from ai_org.contribution import implement
from ai_org.rfc.task import Task


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
    (repo / "README.md").write_text("base\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "base")
    return repo


def test_run_creates_worktree_off_base_and_calls_carrier(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    base = _git(repo, "rev-parse", "HEAD")
    calls = []

    def fake_run_codex(worktree, prompt, sandbox, *, out_file, resume_session=None, **kwargs):
        worktree = Path(worktree)
        calls.append(
            {
                "worktree": worktree,
                "prompt": prompt,
                "sandbox": sandbox,
                "out_file": Path(out_file),
                "resume_session": resume_session,
            }
        )
        assert _git(worktree, "rev-parse", "HEAD") == base
        (worktree / "feature.txt").write_text("implemented\n")
        _git(worktree, "add", "feature.txt")
        _git(worktree, "commit", "-m", "implement feature")
        return {"ok": True, "session_id": "sess-1", "last_message": "done", "events": 1}

    monkeypatch.setattr(implement.carrier, "run_codex", fake_run_codex)
    task = Task(
        id="task-1",
        objective="Add a feature",
        contract="feature.txt exists",
        base_sha=base,
        scope=["feature.txt"],
    )

    result = implement.run(task, repo=repo)

    assert result == {
        "branch": "refs/heads/contrib/task-1",
        "session_id": "sess-1",
        "ok": True,
    }
    assert calls[0]["sandbox"] == "workspace-write"
    assert calls[0]["resume_session"] is None
    assert not calls[0]["worktree"].exists()
    assert _git(repo, "show", "refs/heads/contrib/task-1:feature.txt") == "implemented"
    assert "objective:\nAdd a feature" in calls[0]["prompt"]
    assert "- feature.txt" in calls[0]["prompt"]


def test_run_resume_uses_existing_branch_and_session_delta(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    base = _git(repo, "rev-parse", "HEAD")
    calls = []

    def fake_run_codex(worktree, prompt, sandbox, *, out_file, resume_session=None, **kwargs):
        calls.append(
            {
                "worktree": Path(worktree),
                "prompt": prompt,
                "sandbox": sandbox,
                "resume_session": resume_session,
            }
        )
        return {"ok": True, "session_id": resume_session or "sess-1", "last_message": "done", "events": 1}

    monkeypatch.setattr(implement.carrier, "run_codex", fake_run_codex)
    task = Task(
        id="task-2",
        objective="Add another feature",
        contract="contract",
        base_sha=base,
        scope=["README.md"],
    )

    first = implement.run(task, repo=repo)
    second = implement.run(
        task,
        repo=repo,
        feedback="Acceptance found a missing edge case.",
        resume_session="sess-1",
        branch_ref=first["branch"],
    )

    assert second["branch"] == first["branch"]
    assert calls[1]["sandbox"] == "workspace-write"
    assert calls[1]["resume_session"] == "sess-1"
    assert "Acceptance found a missing edge case." in calls[1]["prompt"]
    assert "Use only this feedback delta" in calls[1]["prompt"]
    assert "objective:" not in calls[1]["prompt"]
    assert not calls[1]["worktree"].exists()
