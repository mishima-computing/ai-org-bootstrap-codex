from __future__ import annotations

import json
from pathlib import Path
import subprocess

from ai_org.patch import implement
from ai_org.rfc.field_registry import empty_user_experience_requirements

RFC_ID = "add-feature-file"
RFC_BRANCH = f"ai-org/rfc/{RFC_ID}"


def _rfc_view(title: str = "Add Feature File") -> dict[str, object]:
    return {
        "raw_request": f"{title}: create feature.txt with the implemented marker.",
        "working_title": title,
        "request_type": "feature",
        "problem_or_motivation": "The repo lacks a feature marker.",
        "intended_users_or_jobs": "Repository contributors need a visible implemented marker.",
        "desired_outcomes_success": "A marker file appears on the contribution branch.",
        "affected_area_platform": "feature.txt",
        "tech_stack": {
            "build_strategy": "framework_based",
            "engine": "",
            "framework": "repo-native files",
            "language": "text",
            "platform": "repository",
            "rationale": "Use the existing repository file layout.",
            "provenance": "requester_specified",
        },
        "user_experience_requirements": empty_user_experience_requirements(),
        "background_facts": "Keep the change focused.",
        "constraints_assumptions": [],
        "references": [],
        "grounding_provenance": "Test fixture grounding.",
        "open_questions": [],
        "non_goals_out_of_scope": [],
        "proposal_hint": "Create feature.txt with the implemented marker.",
        "alternatives_considered": ["Leave the repo without a feature marker."],
    }


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return result.stdout.strip()


def _repo(tmp_path: Path, rfc_id: str = RFC_ID, title: str = "Add Feature File") -> Path:
    repo = tmp_path / "repo"
    rfc_branch = f"ai-org/rfc/{rfc_id}"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "base")
    _git(repo, "branch", "-M", "main")
    _git(repo, "checkout", "-B", rfc_branch, "main")
    (repo / "rfc.json").write_text(
        json.dumps(
            _rfc_view(title)
        ),
        encoding="utf-8",
    )
    _git(repo, "add", "rfc.json")
    _git(repo, "commit", "-m", "rfc")
    _git(repo, "checkout", "main")
    return repo


def test_run_reads_rfc_branch_lets_codex_edit_worktree_and_commits_branch(tmp_path, monkeypatch):
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

    result = implement.run(repo, RFC_ID)

    assert result["ok"] is True
    assert result["branch"] == "ai-org/contrib/add-feature-file"
    assert result["commit"] == _git(repo, "rev-parse", "refs/heads/ai-org/contrib/add-feature-file")
    assert _git(repo, "show-ref", "--verify", "refs/heads/ai-org/contrib/add-feature-file")
    assert _git(repo, "show", f"{result['commit']}:feature.txt") == "implemented"
    assert len(codex_calls) == 1
    assert codex_calls[0][:6] == ["codex", "exec", "--sandbox", "workspace-write", "-C", codex_calls[0][5]]
    assert codex_calls[0][6] == "-o"
    assert "working_title:\nAdd Feature File" in codex_calls[0][-1]
    assert "proposal_hint:\nCreate feature.txt with the implemented marker." in codex_calls[0][-1]
    assert "alternatives_considered:\n- Leave the repo without a feature marker." in codex_calls[0][-1]


def test_run_uses_stable_rfc_id_for_contribution_branch_not_refined_title(tmp_path, monkeypatch):
    rfc_id = "stable-rfc-id"
    repo = _repo(tmp_path, rfc_id=rfc_id, title="Review Refined Title")
    real_run = subprocess.run

    def fake_run(cmd, *args, **kwargs):
        if cmd[:2] != ["codex", "exec"]:
            return real_run(cmd, *args, **kwargs)

        worktree = Path(cmd[cmd.index("-C") + 1])
        out_file = Path(cmd[cmd.index("-o") + 1])
        (worktree / "feature.txt").write_text("implemented\n", encoding="utf-8")
        out_file.write_text("done\n", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(implement.subprocess, "run", fake_run)

    result = implement.run(repo, f"ai-org/rfc/{rfc_id}")

    assert result["ok"] is True
    assert result["branch"] == "ai-org/contrib/stable-rfc-id"
    assert _git(repo, "show-ref", "--verify", "refs/heads/ai-org/contrib/stable-rfc-id")


def test_run_excludes_pycache_and_pyc_files_from_commit(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    real_run = subprocess.run

    def fake_run(cmd, *args, **kwargs):
        if cmd[:2] != ["codex", "exec"]:
            return real_run(cmd, *args, **kwargs)

        worktree = Path(cmd[cmd.index("-C") + 1])
        out_file = Path(cmd[cmd.index("-o") + 1])
        (worktree / "app.py").write_text("print('implemented')\n", encoding="utf-8")
        cache_dir = worktree / "__pycache__"
        cache_dir.mkdir()
        (cache_dir / "app.cpython-312.pyc").write_bytes(b"bytecode")
        nested_cache_dir = worktree / "pkg" / "__pycache__"
        nested_cache_dir.mkdir(parents=True)
        (nested_cache_dir / "mod.cpython-312.pyc").write_bytes(b"bytecode")
        (worktree / "root.pyc").write_bytes(b"bytecode")
        out_file.write_text("done\n", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(implement.subprocess, "run", fake_run)

    result = implement.run(repo, RFC_ID)

    assert result["ok"] is True
    tree_files = _git(repo, "ls-tree", "-r", "--name-only", result["commit"]).splitlines()
    assert "app.py" in tree_files
    assert "__pycache__/app.cpython-312.pyc" not in tree_files
    assert "pkg/__pycache__/mod.cpython-312.pyc" not in tree_files
    assert "root.pyc" not in tree_files
    assert not any(path.endswith(".pyc") or "__pycache__/" in path for path in tree_files)


def test_patch_handoff_check_accepts_registry_and_rejects_missing_required_field():
    rfc = _rfc_view()
    assert implement._is_common_8(rfc) is True
    rfc.pop("grounding_provenance")
    assert implement._is_common_8(rfc) is False


def test_run_fail_closed_when_rfc_missing_on_rfc_branch(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "base")
    _git(repo, "branch", "-M", "main")
    _git(repo, "checkout", "-B", RFC_BRANCH, "main")
    _git(repo, "checkout", "main")

    result = implement.run(repo, RFC_ID)

    assert result["ok"] is False
    assert result["error"] == f"rfc.json missing at {RFC_BRANCH}"


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

    result = implement.run(repo, RFC_ID)

    assert result["ok"] is False
    assert result["error"] == "codex implementation failed"
    assert result["output_exists"] is False
    assert _git(repo, "rev-parse", "HEAD") == _git(
        repo, "rev-parse", "refs/heads/ai-org/contrib/add-feature-file"
    )
