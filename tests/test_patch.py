from __future__ import annotations

import json
from pathlib import Path
import subprocess

from ai_org import patch


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
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    (repo / "rfc.json").write_text(
        json.dumps(
            {
                "title": "Add Feature File",
                "problem": "The repo lacks a feature marker.",
                "proposed_change": "Create feature.txt with the implemented marker.",
                "interface_sketch": "feature.txt",
                "notes": "Keep the change focused.",
            }
        ),
        encoding="utf-8",
    )
    _git(repo, "add", "README.md", "rfc.json")
    _git(repo, "commit", "-m", "rfc")
    return repo


def test_make_returns_reachable_attempt_one(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    calls = []

    def fake_run(repo_arg, *, rfc_path="rfc.json", feedback=None, attempt=1):
        calls.append({"repo": repo_arg, "rfc_path": rfc_path, "feedback": feedback, "attempt": attempt})
        return {"ok": True, "branch": "ai-org/contrib/add-feature-file"}

    verdict = {"ok": True, "reachable": True, "blockers": [], "notes": "reachable"}
    checked = []

    monkeypatch.setattr(patch.implement, "run", fake_run)
    monkeypatch.setattr(
        patch.functional_check,
        "check",
        lambda repo_arg, branch: checked.append((repo_arg, branch)) or verdict,
    )

    assert patch.make(repo) == {
        "ok": True,
        "branch": "ai-org/contrib/add-feature-file",
        "verdict": verdict,
        "attempts": 1,
    }
    assert calls == [{"repo": repo, "rfc_path": "rfc.json", "feedback": None, "attempt": 1}]
    assert checked == [(repo, "ai-org/contrib/add-feature-file")]


def test_make_retries_with_acceptance_blockers_as_feedback(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    calls = []
    blockers = [{"where": "app.py:12", "why": "missing edge case"}]
    verdicts = iter(
        [
            {"ok": False, "reachable": False, "blockers": blockers, "notes": "blocked"},
            {"ok": True, "reachable": True, "blockers": [], "notes": "reachable"},
        ]
    )

    def fake_run(repo_arg, *, rfc_path="rfc.json", feedback=None, attempt=1):
        calls.append({"repo": repo_arg, "rfc_path": rfc_path, "feedback": feedback, "attempt": attempt})
        suffix = "" if attempt == 1 else f"-a{attempt}"
        return {"ok": True, "branch": f"ai-org/contrib/add-feature-file{suffix}"}

    monkeypatch.setattr(patch.implement, "run", fake_run)
    monkeypatch.setattr(patch.functional_check, "check", lambda _repo_arg, _branch: next(verdicts))

    result = patch.make(repo)

    assert result["ok"] is True
    assert result["branch"] == "ai-org/contrib/add-feature-file-a2"
    assert result["attempts"] == 2
    assert calls == [
        {"repo": repo, "rfc_path": "rfc.json", "feedback": None, "attempt": 1},
        {"repo": repo, "rfc_path": "rfc.json", "feedback": blockers, "attempt": 2},
    ]


def test_make_returns_blocked_after_cap(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    calls = []
    verdict = {
        "ok": False,
        "reachable": False,
        "blockers": [{"where": "app.py:12", "why": "still blocked"}],
        "notes": "blocked",
    }

    def fake_run(repo_arg, *, rfc_path="rfc.json", feedback=None, attempt=1):
        calls.append({"repo": repo_arg, "rfc_path": rfc_path, "feedback": feedback, "attempt": attempt})
        suffix = "" if attempt == 1 else f"-a{attempt}"
        return {"ok": True, "branch": f"ai-org/contrib/add-feature-file{suffix}"}

    monkeypatch.setattr(patch.implement, "run", fake_run)
    monkeypatch.setattr(patch.functional_check, "check", lambda _repo_arg, _branch: verdict)

    assert patch.make(repo, cap=3) == {
        "ok": False,
        "branch": "ai-org/contrib/add-feature-file-a3",
        "verdict": verdict,
        "attempts": 3,
    }
    assert [call["attempt"] for call in calls] == [1, 2, 3]
    assert [call["feedback"] for call in calls] == [
        None,
        verdict["blockers"],
        verdict["blockers"],
    ]
