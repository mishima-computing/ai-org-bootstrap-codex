from __future__ import annotations

import json
from pathlib import Path
import stat
import subprocess

from ai_org.patch import functional_check
from ai_org.rfc.receive import RFC


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
    (repo / "app.py").write_text("BASE = True\n")
    _git(repo, "add", "app.py")
    _git(repo, "commit", "-m", "base")
    _git(repo, "checkout", "-b", "feature/mona")
    (repo / "app.py").write_text("GOAL = 'reachable'\n")
    _git(repo, "add", "app.py")
    _git(repo, "commit", "-m", "feature")
    feature_sha = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-")
    return repo


def test_check_returns_reachable_verdict_from_read_only_branch_worktree(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    feature_sha = _git(repo, "rev-parse", "refs/heads/feature/mona")
    calls = []

    def fake_run_codex(worktree, prompt, sandbox, *, out_file, output_schema=None, **kwargs):
        worktree = Path(worktree)
        output_schema = Path(output_schema)
        app_file = worktree / "app.py"
        calls.append(
            {
                "worktree": worktree,
                "prompt": prompt,
                "sandbox": sandbox,
                "out_file": Path(out_file),
                "output_schema": output_schema,
            }
        )
        assert _git(worktree, "rev-parse", "HEAD") == feature_sha
        assert app_file.read_text() == "GOAL = 'reachable'\n"
        assert app_file.stat().st_mode & stat.S_IWUSR == 0
        schema = json.loads(output_schema.read_text())
        assert schema["required"] == ["reachable", "blockers", "notes"]
        return {
            "ok": True,
            "session_id": "sess-mona",
            "last_message": json.dumps(
                {
                    "reachable": True,
                    "blockers": [],
                    "notes": "USER reaches the goal through app.py:1.",
                }
            ),
            "events": 1,
        }

    monkeypatch.chdir(repo)
    monkeypatch.setattr(functional_check.carrier, "run_codex", fake_run_codex)
    rfc = RFC(
        title="Reach the feature",
        problem="The user needs to complete the Mona goal.",
        proposed_change="Wire the feature end-to-end.",
        interface_sketch="Call app.py",
        notes="Watch for false success.",
    )

    verdict = functional_check.check(rfc, "refs/heads/feature/mona")

    assert verdict == {
        "ok": True,
        "reachable": True,
        "blockers": [],
        "notes": "USER reaches the goal through app.py:1.",
    }
    assert len(calls) == 1
    call = calls[0]
    assert call["sandbox"] == "read-only"
    assert not call["worktree"].exists()
    assert call["output_schema"].name == "functional-verdict.schema.json"
    assert "problem / user goal:\nThe user needs to complete the Mona goal." in call["prompt"]
    assert "proposed change:\nWire the feature end-to-end." in call["prompt"]
    assert "Use exactly two personas: USER and APP." in call["prompt"]
    assert "USER is stubborn" in call["prompt"]
    assert "APP answers only from real source citations in file:line form" in call["prompt"]
    assert "Do not launch the app" in call["prompt"]


def test_check_surfaces_not_reachable_blockers(tmp_path, monkeypatch):
    repo = _repo(tmp_path)

    def fake_run_codex(worktree, prompt, sandbox, *, out_file, output_schema=None, **kwargs):
        return {
            "ok": True,
            "session_id": "sess-mona",
            "last_message": json.dumps(
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
            "events": 1,
        }

    monkeypatch.chdir(repo)
    monkeypatch.setattr(functional_check.carrier, "run_codex", fake_run_codex)

    verdict = functional_check.check(
        RFC(title="T", problem="Reach G", proposed_change="Add a path"),
        "refs/heads/feature/mona",
    )

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
