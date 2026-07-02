from __future__ import annotations

import json
from pathlib import Path
import subprocess

from ai_org.patch import functional_check
from ai_org.rfc.field_registry import empty_user_experience_requirements


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
    (repo / "rfc.json").write_text(
        json.dumps(
            {
                "raw_request": "Expose the playable feature through app.py.",
                "working_title": "Playable feature",
                "request_type": "feature",
                "problem_or_motivation": "A real user needs to reach the feature.",
                "intended_users_or_jobs": "Application users need reachable behavior.",
                "desired_outcomes_success": "Users can reach the feature.",
                "affected_area_platform": "app.py",
                "tech_stack": {
                    "build_strategy": "framework_based",
                    "engine": "",
                    "framework": "repo-native Python files",
                    "language": "Python",
                    "platform": "CLI",
                    "rationale": "Use the repository's existing Python file layout.",
                    "provenance": "requester_specified",
                },
                "user_experience_requirements": empty_user_experience_requirements(),
                "background_facts": "Acceptance checks reachability.",
                "constraints_assumptions": [],
                "references": [],
                "grounding_provenance": "Test fixture grounding.",
                "open_questions": [],
                "non_goals_out_of_scope": [],
                "proposal_hint": "Expose the feature through app.py.",
                "alternatives_considered": ["Leave app.py without a reachable feature."],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (repo / "app.py").write_text("BASE = True\n", encoding="utf-8")
    _git(repo, "add", "rfc.json", "app.py")
    _git(repo, "commit", "-m", "base")
    _git(repo, "branch", "-M", "main")
    _git(repo, "checkout", "-b", "feature/playable")
    (repo / "app.py").write_text("GOAL = 'reachable'\n", encoding="utf-8")
    _git(repo, "add", "app.py")
    _git(repo, "commit", "-m", "feature")
    _git(repo, "checkout", "main")
    return repo


def test_check_returns_reachable_and_commits_acceptance_to_branch(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    before = _git(repo, "rev-parse", "refs/heads/feature/playable")
    real_run = subprocess.run
    codex_calls = []

    def fake_run(cmd, **kwargs):
        if cmd[0] == "git":
            return real_run(cmd, **kwargs)
        assert cmd[:4] == ["codex", "exec", "--sandbox", "read-only"]
        worktree = Path(cmd[cmd.index("-C") + 1])
        out_file = Path(cmd[cmd.index("-o") + 1])
        schema_file = Path(cmd[cmd.index("--output-schema") + 1])
        codex_calls.append({"cmd": cmd, "worktree": worktree, "schema_file": schema_file})
        assert _git(worktree, "rev-parse", "HEAD") == before
        assert (worktree / "app.py").read_text(encoding="utf-8") == "GOAL = 'reachable'\n"
        assert json.loads(schema_file.read_text(encoding="utf-8")) == functional_check.VERDICT_SCHEMA
        out_file.write_text(
            json.dumps(
                {
                    "reachable": True,
                    "blockers": [],
                    "notes": "USER reaches the goal through app.py:1.",
                }
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(functional_check.subprocess, "run", fake_run)

    verdict = functional_check.check(repo, "refs/heads/feature/playable")

    after = _git(repo, "rev-parse", "refs/heads/feature/playable")
    assert verdict == {
        "ok": True,
        "reachable": True,
        "blockers": [],
        "notes": "USER reaches the goal through app.py:1.",
    }
    assert after != before
    assert _git(repo, "log", "-1", "--format=%s", "refs/heads/feature/playable") == "acceptance: reachable"
    assert _git(repo, "rev-parse", "HEAD") != after
    assert len(codex_calls) == 1
    assert not codex_calls[0]["worktree"].exists()
    assert "Return only JSON matching the provided schema." in codex_calls[0]["cmd"][-1]


def test_check_returns_blocked_and_commits_blockers_to_branch(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    before = _git(repo, "rev-parse", "refs/heads/feature/playable")
    real_run = subprocess.run

    def fake_run(cmd, **kwargs):
        if cmd[0] == "git":
            return real_run(cmd, **kwargs)
        out_file = Path(cmd[cmd.index("-o") + 1])
        out_file.write_text(
            json.dumps(
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
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(functional_check.subprocess, "run", fake_run)

    verdict = functional_check.check(repo, "feature/playable")

    after = _git(repo, "rev-parse", "refs/heads/feature/playable")
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
    assert after != before
    assert _git(repo, "log", "-1", "--format=%s", "refs/heads/feature/playable") == "acceptance: blocked"
    assert "app.py:1: The branch sets a constant" in _git(
        repo,
        "log",
        "-1",
        "--format=%b",
        "refs/heads/feature/playable",
    )
