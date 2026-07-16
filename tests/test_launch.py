from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time


def test_launch_selftest_detaches_worker_and_parent_exits_immediately(tmp_path):
    repo = _init_repo(tmp_path)
    artifacts_dir = tmp_path / "artifacts"
    started = time.monotonic()

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ai_org.launch",
            str(repo),
            "Launch selftest request",
            "--artifacts-dir",
            str(artifacts_dir),
            "--_selftest",
        ],
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    elapsed = time.monotonic() - started

    assert result.returncode == 0, result.stderr
    assert elapsed < 2.0
    lines = result.stdout.strip().splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["run"] == "started"
    assert payload["repo"] == str(repo)
    assert payload["worker_pid"] > 0
    assert Path(payload["log"]) == artifacts_dir / "run.log"
    assert Path(payload["progress"]) == artifacts_dir / "progress.json"
    assert len(list((repo / ".ai-org" / "inbox").glob("*.json"))) == 1

    marker_path = artifacts_dir / "selftest_marker.json"
    result_path = artifacts_dir / "intake_result.json"
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline and not (marker_path.exists() and result_path.exists()):
        time.sleep(0.1)

    assert marker_path.exists()
    assert result_path.exists()
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    assert marker["pid"] == payload["worker_pid"]
    assert marker["sid"] != os.getsid(0)
    assert marker["pgid"] == marker["pid"]

    log_path = Path(payload["log"])
    progress_path = Path(payload["progress"])
    assert log_path.exists()
    assert progress_path.exists()
    assert "launch.worker.started" in log_path.read_text(encoding="utf-8")
    assert json.loads(progress_path.read_text(encoding="utf-8"))["status"] == "selftest_done"
    assert json.loads(result_path.read_text(encoding="utf-8"))["status"] == "selftest_done"


def test_launch_without_repo_path_creates_fresh_workspace_and_prints_path(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    env = os.environ.copy()
    env["HOME"] = str(home)
    started = time.monotonic()

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ai_org.launch",
            "Build a clean launch workspace",
            "--_selftest",
        ],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    elapsed = time.monotonic() - started

    assert result.returncode == 0, result.stderr
    assert elapsed < 2.0
    payload = json.loads(result.stdout.strip())
    repo = Path(payload["repo"])
    assert payload["workspace"] == str(repo)
    assert payload["workspace_created"] is True
    assert repo.parent == home / "aiorg_workspaces"
    assert repo.name.endswith("-build-a-clean-launch-workspace")
    assert (repo / ".git").is_dir()
    assert _git(repo, "rev-list", "--count", "HEAD") == "1"
    assert _git(repo, "branch", "--show-current") == "main"
    assert len(list((repo / ".ai-org" / "inbox").glob("*.json"))) == 1

    result_path = Path(payload["log"]).with_name("intake_result.json")
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline and not result_path.exists():
        time.sleep(0.1)
    assert result_path.exists()


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.name", "Launch Test")
    _git(repo, "config", "user.email", "launch-test@example.invalid")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "base")
    _git(repo, "branch", "-M", "main")
    return repo


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return result.stdout.strip()
