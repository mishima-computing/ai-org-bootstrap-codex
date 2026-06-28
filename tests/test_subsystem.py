from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import subprocess

from ai_org.merge import subsystem


def test_subsystem_accept_merges_contribution_branch(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    contribution = _commit_on_branch(repo, "contrib/p1", "p1.txt")
    _write_fake_codex(tmp_path, {"accept": True, "reasons": ["fits"]})
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")

    result = subsystem.review_and_integrate(repo, contribution)

    assert result == {
        "accept": True,
        "ref": "refs/heads/ai-org/subsystem",
        "reasons": ["fits"],
    }
    assert _ref_exists(repo, "refs/heads/ai-org/subsystem")
    assert _is_ancestor(repo, contribution, "refs/heads/ai-org/subsystem")
    assert _git(repo, "show", "refs/heads/ai-org/subsystem:p1.txt").stdout == "p1.txt\n"


def test_subsystem_reject_does_not_merge_contribution_branch(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    contribution = _commit_on_branch(repo, "contrib/p1", "p1.txt")
    _write_fake_codex(tmp_path, {"accept": False, "reasons": ["too risky"]})
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")

    result = subsystem.review_and_integrate(repo, contribution)

    assert result == {
        "accept": False,
        "ref": None,
        "reasons": ["too risky"],
    }
    assert not _ref_exists(repo, "refs/heads/ai-org/subsystem")


def test_subsystem_verdict_schema_obeys_codex_constraints():
    schema = subsystem._VERDICT

    assert "allOf" not in json.dumps(schema)
    assert "anyOf" not in json.dumps(schema)
    assert "oneOf" not in json.dumps(schema)
    assert "if" not in schema
    assert "then" not in schema
    assert schema["additionalProperties"] is False
    assert sorted(schema["required"]) == sorted(schema["properties"])


def _write_fake_codex(tmp_path: Path, verdict: dict[str, object]) -> Path:
    path = tmp_path / "codex"
    path.write_text(
        f"""#!/usr/bin/env python3
import json
import pathlib
import sys

argv = sys.argv[1:]
schema = json.loads(pathlib.Path(argv[argv.index("--output-schema") + 1]).read_text())
assert "allOf" not in json.dumps(schema)
assert "anyOf" not in json.dumps(schema)
assert "oneOf" not in json.dumps(schema)
assert schema["additionalProperties"] is False
assert sorted(schema["required"]) == sorted(schema["properties"])
out = pathlib.Path(argv[argv.index("-o") + 1])
out.write_text({json.dumps(json.dumps(verdict))})
""",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def _init_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "config", "user.email", "test@example.invalid")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "base")
    _git(repo, "branch", "-M", "master")
    return repo


def _commit_on_branch(repo, branch, filename):
    _git(repo, "checkout", "-B", branch, "master")
    (repo / filename).write_text(f"{filename}\n", encoding="utf-8")
    _git(repo, "add", filename)
    _git(repo, "commit", "-m", f"add {filename}")
    _git(repo, "checkout", "master")
    return branch


def _ref_exists(repo, ref):
    return (
        subprocess.run(
            ["git", "-C", str(repo), "show-ref", "--verify", "--quiet", ref],
            check=False,
        ).returncode
        == 0
    )


def _is_ancestor(repo, ancestor, descendant):
    return (
        subprocess.run(
            ["git", "-C", str(repo), "merge-base", "--is-ancestor", ancestor, descendant],
            check=False,
        ).returncode
        == 0
    )


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
