from __future__ import annotations

import json
import subprocess

from ai_org.merge import mainline


def test_mainline_accept_merges_subsystem_branch(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    _commit_on_branch(repo, "ai-org/subsystem", "subsystem.txt")
    _mock_codex(monkeypatch, {"accept": True, "reasons": ["ready"]})

    result = mainline.review_and_integrate(repo)

    assert result == {
        "accept": True,
        "ref": "refs/heads/ai-org/mainline",
        "reasons": ["ready"],
    }
    assert _ref_exists(repo, "refs/heads/ai-org/mainline")
    assert _file_at_ref(repo, "ai-org/mainline", "subsystem.txt") == "subsystem.txt\n"


def test_mainline_reject_does_not_merge_subsystem_branch(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    _commit_on_branch(repo, "ai-org/subsystem", "subsystem.txt")
    _mock_codex(monkeypatch, {"accept": False, "reasons": ["not ready"]})

    result = mainline.review_and_integrate(repo)

    assert result == {"accept": False, "ref": None, "reasons": ["not ready"]}
    assert not _ref_exists(repo, "refs/heads/ai-org/mainline")


def _mock_codex(monkeypatch, verdict):
    real_run = subprocess.run

    def fake_run(cmd, *args, **kwargs):
        if isinstance(cmd, list) and cmd[:2] == ["codex", "exec"]:
            out_file = cmd[cmd.index("-o") + 1]
            with open(out_file, "w", encoding="utf-8") as fh:
                json.dump(verdict, fh)
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(mainline.subprocess, "run", fake_run)


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


def _commit_on_branch(repo, branch, filename):
    _git(repo, "checkout", "-B", branch, "main")
    (repo / filename).write_text(f"{filename}\n", encoding="utf-8")
    _git(repo, "add", filename)
    _git(repo, "commit", "-m", f"add {filename}")
    _git(repo, "checkout", "main")


def _ref_exists(repo, ref):
    return (
        subprocess.run(
            ["git", "-C", str(repo), "show-ref", "--verify", "--quiet", ref],
            check=False,
        ).returncode
        == 0
    )


def _file_at_ref(repo, ref, path):
    result = subprocess.run(
        ["git", "-C", str(repo), "show", f"{ref}:{path}"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return result.stdout


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
