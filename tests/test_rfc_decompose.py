from __future__ import annotations

import importlib
import json
import subprocess
from pathlib import Path

import pytest

from ai_org import rfc as rfc_package


decompose_module = importlib.import_module("ai_org.rfc.decompose")


def test_too_big_rfc_decomposes_into_child_branches_with_dependency_graph(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    _write_rfc_branch(repo, "ai-org/rfc/big-feature", _rfc("Big Feature", "Build prep and behavior together."))
    base_commit = _git(repo, "rev-parse", "main")
    prompts: list[str] = []

    def handler(cmd):
        prompt = cmd[-1]
        prompts.append(prompt)
        if "RFC branch: ai-org/rfc/big-feature\n" in prompt:
            return _split(False, [_child("prep-api", "Prep API", [], ["api-ready"], "prep", 1), _child(
                "behavior-ui",
                "Behavior UI",
                ["api-ready"],
                ["ui-ready"],
                "behavior",
                2,
            )])
        return _split(True, [])

    _install_codex_fake(monkeypatch, handler)

    result = decompose_module.decompose(repo, "big-feature")

    assert result["ok"] is True
    assert result["status"] == "decomposed"
    assert [child["id"] for child in result["children"]] == [
        "big-feature-prep-api",
        "big-feature-behavior-ui",
    ]
    assert _git(repo, "rev-parse", "HEAD") == base_commit

    prep = json.loads(_git(repo, "show", "ai-org/rfc/big-feature-prep-api:rfc.json"))
    assert prep["title"] == "Prep API"
    assert set(prep) == set(decompose_module.RFC_FIELDS)

    behavior_meta = json.loads(_git(repo, "show", "ai-org/rfc/big-feature-behavior-ui:rfc-metadata.json"))
    assert behavior_meta["parent"] == "ai-org/rfc/big-feature"
    assert behavior_meta["base_commit"] == base_commit
    assert behavior_meta["depends_on"] == ["api-ready"]
    assert behavior_meta["provides"] == ["ui-ready"]

    graph = json.loads(_git(repo, "show", "ai-org/rfc/big-feature:rfc-decomposition.json"))
    assert graph["base_commit"] == base_commit
    assert [child["id"] for child in graph["children"]] == [
        "big-feature-prep-api",
        "big-feature-behavior-ui",
    ]
    assert graph["edges"] == [
        {"from": "big-feature-prep-api", "to": "big-feature-behavior-ui", "via": "api-ready"}
    ]
    assert "Cut by subsystem and ownership first" in prompts[0]
    assert "Every child must leave the system working" in prompts[0]


def test_right_sized_rfc_does_not_decompose(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    _write_rfc_branch(repo, "ai-org/rfc/small-feature", _rfc("Small Feature", "Add one focused check."))
    calls = 0

    def handler(cmd):
        nonlocal calls
        calls += 1
        return _split(True, [])

    _install_codex_fake(monkeypatch, handler)

    result = rfc_package.decompose(repo, "small-feature")

    assert result["ok"] is True
    assert result["status"] == "right-sized"
    assert calls == 1
    missing = subprocess.run(
        ["git", "-C", str(repo), "show", "ai-org/rfc/small-feature:rfc-decomposition.json"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert missing.returncode != 0
    assert _git(repo, "branch", "--list", "ai-org/rfc/*", "--format=%(refname:short)") == "ai-org/rfc/small-feature"


def test_recursion_stops_at_right_sized_child_or_depth_guard(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    _write_rfc_branch(repo, "ai-org/rfc/root", _rfc("Root", "Split a broad feature."))
    calls: list[str] = []

    def handler(cmd):
        prompt = cmd[-1]
        if "RFC branch: ai-org/rfc/root\n" in prompt:
            calls.append("root")
            return _split(False, [_child("mixed-child", "Mixed Child", [], ["mixed-ready"], "integration", 1)])
        if "RFC branch: ai-org/rfc/root-mixed-child\n" in prompt:
            calls.append("child")
            return _split(False, [_child("grandchild", "Grandchild", [], ["grandchild-ready"], "prep", 1)])
        raise AssertionError(prompt)

    _install_codex_fake(monkeypatch, handler)

    result = decompose_module.decompose(repo, "root", max_depth=1)

    assert result["ok"] is True
    assert result["status"] == "decomposed"
    assert calls == ["root", "child"]
    assert result["blocked_by_depth_guard"] == ["root-mixed-child"]
    assert json.loads(_git(repo, "show", "ai-org/rfc/root-mixed-child:rfc-metadata.json"))["provides"] == [
        "mixed-ready"
    ]
    child_tracking = json.loads(_git(repo, "show", "ai-org/rfc/root-mixed-child:rfc-decomposition.json"))
    assert child_tracking["blocked_by_depth_guard"] == ["root-mixed-child"]
    missing = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--verify", "refs/heads/ai-org/rfc/root-mixed-child-grandchild"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert missing.returncode != 0


def test_decomposition_schema_is_codex_valid():
    schema = decompose_module.SPLIT_SCHEMA
    serialized = json.dumps(schema)
    assert "allOf" not in serialized
    assert "anyOf" not in serialized
    assert "oneOf" not in serialized
    _assert_required_is_all_properties(schema)
    _assert_required_is_all_properties(schema["properties"]["children"]["items"])


def _assert_required_is_all_properties(schema: dict) -> None:
    assert schema["additionalProperties"] is False
    assert sorted(schema["required"]) == sorted(schema["properties"])


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "decompose-test@example.invalid")
    _git(repo, "config", "user.name", "Decompose Test")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "base")
    _git(repo, "branch", "-M", "main")
    return repo


def _write_rfc_branch(repo: Path, branch: str, rfc: dict[str, object]) -> None:
    _git(repo, "checkout", "-B", branch, "main")
    (repo / "rfc.json").write_text(json.dumps(rfc, indent=2) + "\n", encoding="utf-8")
    _git(repo, "add", "rfc.json")
    _git(repo, "commit", "-m", "rfc")
    _git(repo, "checkout", "main")


def _rfc(title: str, problem: str) -> dict[str, object]:
    return {
        "title": title,
        "problem": problem,
        "proposal": f"Implement {title}.",
        "alternatives": ["Keep the current behavior."],
        "intended_users": "Contributors.",
        "affected_area": "ai_org.rfc",
        "impact": "The RFC phase can hand off clearer work.",
        "context": "Test RFC.",
    }


def _split(right_sized: bool, children: list[dict[str, object]]) -> dict[str, object]:
    return {
        "right_sized": right_sized,
        "summary_sentence": "This RFC is a single contribution." if right_sized else "This RFC needs child RFCs.",
        "sizing_reason": "Single owner." if right_sized else "Mixed prep and behavior.",
        "children": children,
    }


def _child(
    child_id: str,
    title: str,
    depends_on: list[str],
    provides: list[str],
    change_kind: str,
    order: int,
) -> dict[str, object]:
    data = _rfc(title, f"{title} is needed as a coherent child RFC.")
    data.update(
        {
            "id": child_id,
            "depends_on": depends_on,
            "provides": provides,
            "subsystem": "ai_org.rfc",
            "owner": "rfc phase",
            "change_kind": change_kind,
            "order": order,
            "working_state": "The repository remains usable after this child lands.",
        }
    )
    return data


def _install_codex_fake(monkeypatch: pytest.MonkeyPatch, handler) -> None:
    real_run = decompose_module.subprocess.run

    def fake_run(cmd, *args, **kwargs):
        if cmd and cmd[0] == "codex":
            out_file = Path(cmd[cmd.index("-o") + 1])
            schema = json.loads(Path(cmd[cmd.index("--output-schema") + 1]).read_text(encoding="utf-8"))
            assert schema == decompose_module.SPLIT_SCHEMA
            out_file.write_text(json.dumps(handler(cmd)), encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(decompose_module.subprocess, "run", fake_run)


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return result.stdout.strip()
