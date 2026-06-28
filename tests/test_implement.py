from __future__ import annotations

import json
from pathlib import Path
import subprocess

from ai_org.patch import implement


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


def test_run_reads_head_rfc_lets_codex_edit_worktree_and_commits_branch(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    real_run = subprocess.run
    codex_calls = []

    def fake_run(cmd, *args, **kwargs):
        if cmd[:2] != ["codex", "exec"]:
            return real_run(cmd, *args, **kwargs)

        codex_calls.append(cmd)
        worktree = Path(cmd[cmd.index("-C") + 1])
        out_file = Path(cmd[cmd.index("-o") + 1])
        (worktree / "feature.txt").write_text("implemented\n", encoding="utf-8")
        out_file.write_text("done\n", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(implement.subprocess, "run", fake_run)

    result = implement.run(repo)

    assert result["ok"] is True
    assert result["branch"] == "ai-org/contrib/add-feature-file"
    assert result["commit"] == _git(repo, "rev-parse", "refs/heads/ai-org/contrib/add-feature-file")
    assert _git(repo, "show-ref", "--verify", "refs/heads/ai-org/contrib/add-feature-file")
    assert _git(repo, "show", f"{result['commit']}:feature.txt") == "implemented"
    assert len(codex_calls) == 1
    assert codex_calls[0][:6] == ["codex", "exec", "--sandbox", "workspace-write", "-C", codex_calls[0][5]]
    assert codex_calls[0][6] == "-o"
    assert "title:\nAdd Feature File" in codex_calls[0][-1]
    assert "proposed_change:\nCreate feature.txt with the implemented marker." in codex_calls[0][-1]


def test_run_fail_closed_when_rfc_missing_at_head(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "base")

    result = implement.run(repo)

    assert result["ok"] is False
    assert result["error"] == "rfc.json missing at HEAD"


def test_run_fail_closed_when_codex_writes_no_output_file(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    real_run = subprocess.run

    def fake_run(cmd, *args, **kwargs):
        if cmd[:2] != ["codex", "exec"]:
            return real_run(cmd, *args, **kwargs)
        worktree = Path(cmd[cmd.index("-C") + 1])
        (worktree / "feature.txt").write_text("implemented\n", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(implement.subprocess, "run", fake_run)

    result = implement.run(repo)

    assert result["ok"] is False
    assert result["error"] == "codex implementation failed"
    assert result["output_exists"] is False
    assert _git(repo, "rev-parse", "HEAD") == _git(
        repo, "rev-parse", "refs/heads/ai-org/contrib/add-feature-file"
    )
