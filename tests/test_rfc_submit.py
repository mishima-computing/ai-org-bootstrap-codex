from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from ai_org.rfc import submit as submit_module


def test_submit_accepts_plain_json_string_and_json_file_with_collision_ids(tmp_path):
    repo = _init_repo(tmp_path)
    request_file = tmp_path / "request.json"
    request_file.write_text(json.dumps({"raw_request": "JSON file request"}), encoding="utf-8")

    plain = submit_module.submit(repo, "Plain text request")
    duplicate = submit_module.submit(repo, "Plain text request")
    json_string = submit_module.submit(repo, '{"raw_request": "JSON string request"}')
    json_file = submit_module.submit(repo, str(request_file))

    assert plain["id"] == "plain-text-request"
    assert duplicate["id"] == "plain-text-request-2"
    assert json_string["id"] == "json-string-request"
    assert json_file["id"] == "json-file-request"
    assert _request_at(plain["path"])["raw_request"] == "Plain text request"
    assert _request_at(duplicate["path"])["raw_request"] == "Plain text request"
    assert _request_at(json_string["path"])["raw_request"] == "JSON string request"
    assert _request_at(json_file["path"])["raw_request"] == "JSON file request"
    assert (repo / ".gitignore").read_text(encoding="utf-8").splitlines()[-1] == ".ai-org/"


def test_submit_module_reports_argv_errors_cleanly(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "ai_org.rfc.submit"],
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    assert result.returncode == 2
    assert "usage: python -m ai_org.rfc.submit" in result.stderr


def _request_at(path: str) -> dict[str, object]:
    envelope = json.loads(Path(path).read_text(encoding="utf-8"))
    return envelope["request"]


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.name", "Submit Test")
    _git(repo, "config", "user.email", "submit-test@example.invalid")
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
