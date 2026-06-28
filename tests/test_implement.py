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
        scope=["*.txt"],
    )

    result = implement.run(task, repo=repo)

    assert result == {
        "branch": "refs/heads/contrib/task-1",
        "session_id": "sess-1",
        "ok": True,
    }
    assert len(calls) == 1
    assert calls[0]["sandbox"] == "workspace-write"
    assert calls[0]["resume_session"] is None
    assert not calls[0]["worktree"].exists()
    assert _git(repo, "show", "refs/heads/contrib/task-1:feature.txt") == "implemented"
    assert "objective:\nAdd a feature" in calls[0]["prompt"]
    assert "- *.txt" in calls[0]["prompt"]


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


def test_run_self_check_resumes_session_and_converges(tmp_path, monkeypatch):
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
                "resume_session": resume_session,
            }
        )
        if resume_session:
            (worktree / "status.txt").write_text("pass\n")
            _git(worktree, "add", "status.txt")
            _git(worktree, "commit", "-m", "fix self-check")
            return {"ok": True, "session_id": resume_session, "last_message": "fixed", "events": 1}

        (worktree / "status.txt").write_text("fail\n")
        _git(worktree, "add", "status.txt")
        _git(worktree, "commit", "-m", "initial implementation")
        return {"ok": True, "session_id": "sess-1", "last_message": "done", "events": 1}

    monkeypatch.setattr(implement.carrier, "run_codex", fake_run_codex)
    task = Task(
        id="task-self-check",
        objective="Make the status pass",
        contract="status.txt says pass",
        base_sha=base,
        scope=["status.txt"],
        checks=['test "$(cat status.txt)" = pass || { echo "status was $(cat status.txt)"; exit 7; }'],
    )

    result = implement.run(task, repo=repo)

    assert result == {
        "branch": "refs/heads/contrib/task-self-check",
        "session_id": "sess-1",
        "ok": True,
    }
    assert [call["resume_session"] for call in calls] == [None, "sess-1"]
    assert "Deterministic self-check failed" in calls[1]["prompt"]
    assert "status was fail" in calls[1]["prompt"]
    assert calls[1]["resume_session"] == "sess-1"
    assert _git(repo, "show", "refs/heads/contrib/task-self-check:status.txt") == "pass"


def test_run_self_check_cap_exhausted_returns_not_ok(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    base = _git(repo, "rev-parse", "HEAD")
    calls = []

    def fake_run_codex(worktree, prompt, sandbox, *, out_file, resume_session=None, **kwargs):
        worktree = Path(worktree)
        calls.append({"prompt": prompt, "resume_session": resume_session})
        if resume_session:
            attempts = worktree / "attempts.txt"
            previous = attempts.read_text() if attempts.exists() else ""
            attempts.write_text(previous + "resume\n")
            _git(worktree, "add", "attempts.txt")
            _git(worktree, "commit", "-m", "try self-check fix")
            return {"ok": True, "session_id": resume_session, "last_message": "retry", "events": 1}

        (worktree / "attempts.txt").write_text("initial\n")
        _git(worktree, "add", "attempts.txt")
        _git(worktree, "commit", "-m", "initial implementation")
        return {"ok": True, "session_id": "sess-1", "last_message": "done", "events": 1}

    monkeypatch.setattr(implement.carrier, "run_codex", fake_run_codex)
    task = Task(
        id="task-self-check-fails",
        objective="Make checks pass",
        contract="check passes",
        base_sha=base,
        scope=["attempts.txt"],
        checks=['echo "still failing"; exit 4'],
    )

    result = implement.run(task, repo=repo)

    assert result == {
        "branch": "refs/heads/contrib/task-self-check-fails",
        "session_id": "sess-1",
        "ok": False,
    }
    assert len(calls) == 1 + implement.SELF_CHECK_CAP
    assert [call["resume_session"] for call in calls] == [None] + ["sess-1"] * implement.SELF_CHECK_CAP
    assert all("still failing" in call["prompt"] for call in calls[1:])


def test_run_empty_checks_skips_self_check_loop(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    base = _git(repo, "rev-parse", "HEAD")
    calls = []

    def fake_run_codex(worktree, prompt, sandbox, *, out_file, resume_session=None, **kwargs):
        calls.append({"prompt": prompt, "resume_session": resume_session})
        return {"ok": True, "session_id": "sess-1", "last_message": "done", "events": 1}

    monkeypatch.setattr(implement.carrier, "run_codex", fake_run_codex)
    task = Task(
        id="task-no-checks",
        objective="No checks",
        contract="",
        base_sha=base,
        checks=[],
    )

    result = implement.run(task, repo=repo)

    assert result == {
        "branch": "refs/heads/contrib/task-no-checks",
        "session_id": "sess-1",
        "ok": True,
    }
    assert [call["resume_session"] for call in calls] == [None]


def test_run_scope_deviation_resumes_and_returns_not_ok_when_persistent(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    base = _git(repo, "rev-parse", "HEAD")
    calls = []

    def fake_run_codex(worktree, prompt, sandbox, *, out_file, resume_session=None, **kwargs):
        worktree = Path(worktree)
        calls.append({"prompt": prompt, "resume_session": resume_session})
        if resume_session:
            previous = (worktree / "outside.txt").read_text()
            (worktree / "outside.txt").write_text(previous + "still outside\n")
            _git(worktree, "add", "outside.txt")
            _git(worktree, "commit", "-m", "persist outside scope")
            return {"ok": True, "session_id": resume_session, "last_message": "retry", "events": 1}

        (worktree / "allowed.txt").write_text("allowed\n")
        (worktree / "outside.txt").write_text("outside\n")
        _git(worktree, "add", "allowed.txt", "outside.txt")
        _git(worktree, "commit", "-m", "implement with stray file")
        return {"ok": True, "session_id": "sess-1", "last_message": "done", "events": 1}

    monkeypatch.setattr(implement.carrier, "run_codex", fake_run_codex)
    task = Task(
        id="task-scope-deviation",
        objective="Write the allowed file",
        contract="allowed.txt exists",
        base_sha=base,
        scope=["allowed.txt"],
    )

    result = implement.run(task, repo=repo)

    assert result == {
        "branch": "refs/heads/contrib/task-scope-deviation",
        "session_id": "sess-1",
        "ok": False,
    }
    assert len(calls) == 1 + implement.SELF_CHECK_CAP
    assert [call["resume_session"] for call in calls] == [None] + ["sess-1"] * implement.SELF_CHECK_CAP
    assert "scope deviation:" in calls[1]["prompt"]
    assert "outside.txt" in calls[1]["prompt"]
    assert "allowed task.scope:\n- allowed.txt" in calls[1]["prompt"]
    assert "revert the out-of-scope edits" in calls[1]["prompt"]


def test_run_pre_localization_includes_relevant_existing_code(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    (repo / "src").mkdir()
    (repo / "src" / "payments.py").write_text(
        "def calculate_invoice_total(items):\n"
        "    return sum(item.price for item in items)\n"
    )
    (repo / "docs").mkdir()
    (repo / "docs" / "notes.md").write_text("unrelated notes\n")
    _git(repo, "add", "src/payments.py", "docs/notes.md")
    _git(repo, "commit", "-m", "add existing code")
    base = _git(repo, "rev-parse", "HEAD")
    calls = []

    def fake_run_codex(worktree, prompt, sandbox, *, out_file, resume_session=None, **kwargs):
        calls.append({"prompt": prompt, "resume_session": resume_session})
        return {"ok": True, "session_id": "sess-1", "last_message": "done", "events": 1}

    monkeypatch.setattr(implement.carrier, "run_codex", fake_run_codex)
    task = Task(
        id="task-grounding",
        objective="Update invoice total calculation",
        contract="invoice totals are calculated correctly",
        base_sha=base,
        scope=["src/payments.py"],
    )

    result = implement.run(task, repo=repo)

    assert result["ok"] is True
    assert "relevant existing code" in calls[0]["prompt"]
    assert "src/payments.py:1 def calculate_invoice_total(items):" in calls[0]["prompt"]
    assert "docs/notes.md" not in calls[0]["prompt"]


def test_run_empty_scope_skips_scope_deviation_check(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    base = _git(repo, "rev-parse", "HEAD")
    calls = []

    def fake_run_codex(worktree, prompt, sandbox, *, out_file, resume_session=None, **kwargs):
        worktree = Path(worktree)
        calls.append({"prompt": prompt, "resume_session": resume_session})
        (worktree / "outside.txt").write_text("allowed because scope is empty\n")
        _git(worktree, "add", "outside.txt")
        _git(worktree, "commit", "-m", "change with empty scope")
        return {"ok": True, "session_id": "sess-1", "last_message": "done", "events": 1}

    monkeypatch.setattr(implement.carrier, "run_codex", fake_run_codex)
    task = Task(
        id="task-empty-scope-deviation-skip",
        objective="Make an unconstrained change",
        contract="change exists",
        base_sha=base,
        scope=[],
    )

    result = implement.run(task, repo=repo)

    assert result == {
        "branch": "refs/heads/contrib/task-empty-scope-deviation-skip",
        "session_id": "sess-1",
        "ok": True,
    }
    assert len(calls) == 1
