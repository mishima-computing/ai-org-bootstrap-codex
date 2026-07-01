from __future__ import annotations

import importlib
import json
import subprocess
from pathlib import Path

import pytest

from ai_org import git_wrapper
from ai_org import rfc as rfc_package


decompose_module = importlib.import_module("ai_org.rfc.decompose")


def test_too_big_rfc_decomposes_into_topology_and_semantic_notes(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    _write_rfc_branch(repo, "ai-org/rfc/big-feature", _rfc("Big Feature", "Build prep and behavior together."))
    base_commit = git_wrapper.head_sha(repo, "main")
    prompts: list[str] = []

    def handler(prompt: str):
        prompts.append(prompt)
        if "RFC branch: ai-org/rfc/big-feature\n" in prompt:
            return _split(
                False,
                [
                    _child("prep-api", "Prep API", [], ["api-ready"], "prep", 1),
                    _child("behavior-ui", "Behavior UI", ["api-ready"], ["ui-ready"], "behavior", 2),
                    _child("docs-help", "Docs Help", [], ["docs-ready"], "integration", 3),
                ],
            )
        return _split(True, [])

    _install_codex_fake(monkeypatch, handler)

    result = decompose_module.decompose(repo, "big-feature")

    assert result["ok"] is True
    assert result["status"] == "decomposed"
    assert [child["id"] for child in result["children"]] == [
        "big-feature-prep-api",
        "big-feature-behavior-ui",
        "big-feature-docs-help",
    ]
    assert _git(repo, "rev-parse", "HEAD") == base_commit

    prep_branch = "ai-org/rfc/big-feature-prep-api"
    behavior_branch = "ai-org/rfc/big-feature-behavior-ui"
    docs_branch = "ai-org/rfc/big-feature-docs-help"
    prep = json.loads(_git(repo, "show", f"{prep_branch}:rfc.json"))
    assert prep["working_title"] == "Prep API"
    assert set(prep) == set(decompose_module.RFC_FIELDS)
    assert prep["tech_stack"]["framework"] == "repo-native Python modules"

    assert git_wrapper.is_ancestor(repo, prep_branch, behavior_branch) is True
    assert git_wrapper.is_ancestor(repo, prep_branch, docs_branch) is False
    assert git_wrapper.is_ancestor(repo, docs_branch, prep_branch) is False
    assert git_wrapper.is_ancestor(repo, behavior_branch, docs_branch) is False
    assert result["dependency_graph"] == [{"from": prep_branch, "to": behavior_branch}]
    assert git_wrapper.dependency_graph(repo, [prep_branch, behavior_branch, docs_branch]) == result["dependency_graph"]

    assert git_wrapper.read_semantic(repo, behavior_branch) == {
        "change_kind": "behavior",
        "subsystem": "ai_org.rfc",
        "owner": "rfc phase",
        "working_state": "The repository remains usable after this child lands.",
    }
    assert result["children"][1]["change_kind"] == "behavior"

    for branch in ("ai-org/rfc/big-feature", prep_branch, behavior_branch, docs_branch):
        assert git_wrapper.file_exists(repo, branch, "rfc-metadata.json") is False
        assert git_wrapper.file_exists(repo, branch, "rfc-decomposition.json") is False
    assert "Cut by subsystem and ownership first" in prompts[0]
    assert "Every child must leave the system working" in prompts[0]
    assert "Git ancestry is the source of truth for dependencies" in prompts[0]


def test_right_sized_rfc_does_not_decompose(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    _write_rfc_branch(repo, "ai-org/rfc/small-feature", _rfc("Small Feature", "Add one focused check."))
    calls = 0

    def handler(prompt: str):
        nonlocal calls
        calls += 1
        return _split(True, [])

    _install_codex_fake(monkeypatch, handler)

    result = rfc_package.decompose(repo, "small-feature")

    assert result["ok"] is True
    assert result["status"] == "right-sized"
    assert calls == 1
    assert git_wrapper.file_exists(repo, "ai-org/rfc/small-feature", "rfc-decomposition.json") is False
    assert git_wrapper.branches(repo, "ai-org/rfc/*") == ["ai-org/rfc/small-feature"]


def test_recursion_stops_at_right_sized_child_or_depth_guard(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    _write_rfc_branch(repo, "ai-org/rfc/root", _rfc("Root", "Split a broad feature."))
    calls: list[str] = []

    def handler(prompt: str):
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
    assert git_wrapper.read_semantic(repo, "ai-org/rfc/root-mixed-child")["change_kind"] == "integration"
    assert git_wrapper.file_exists(repo, "ai-org/rfc/root-mixed-child", "rfc-decomposition.json") is False
    assert git_wrapper.branch_exists(repo, "ai-org/rfc/root-mixed-child-grandchild") is False


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
    git_wrapper.create_branch_with_files(
        repo,
        branch,
        "main",
        {"rfc.json": rfc},
        commit_message="rfc",
    )


def _rfc(title: str, problem: str) -> dict[str, object]:
    return {
        "raw_request": f"{title}: {problem}",
        "working_title": title,
        "request_type": "feature",
        "problem_or_motivation": problem,
        "intended_users_or_jobs": "Contributors need right-sized RFC work.",
        "desired_outcomes_success": f"{title} can be implemented as a coherent child RFC.",
        "affected_area_platform": "ai_org.rfc",
        "tech_stack": {
            "build_strategy": "framework_based",
            "engine": "",
            "framework": "repo-native Python modules",
            "language": "Python",
            "platform": "CLI",
            "rationale": "Use the repository's existing Python modules.",
            "provenance": "requester_specified",
        },
        "background_facts": "Test RFC.",
        "constraints_assumptions": [],
        "references": [],
        "grounding_provenance": "Test fixture grounding.",
        "open_questions": [],
        "non_goals_out_of_scope": [],
        "proposal_hint": f"Implement {title}.",
        "alternatives_considered": ["Keep the current behavior."],
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
    data["tech_stack"] = {
        "build_strategy": "",
        "engine": "",
        "framework": "",
        "language": "",
        "platform": "",
        "rationale": "Inherit the parent stack unless this child overrides it.",
        "provenance": "unspecified",
    }
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
    def fake_run_json(repo: Path, **kwargs):
        assert repo.exists()
        assert kwargs["schema"] == decompose_module.SPLIT_SCHEMA
        assert kwargs["schema_filename"] == "rfc-split.schema.json"
        assert kwargs["output_filename"] == "rfc-split.json"
        return {"ok": True, "raw": json.dumps(handler(kwargs["prompt"]))}

    monkeypatch.setattr(decompose_module.codex_exec, "run_json", fake_run_json)


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return result.stdout.strip()
