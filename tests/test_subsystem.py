from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import subprocess

from ai_org import git_wrapper
from ai_org import subsystem as subsystem_boundary
from ai_org.maintainer_merge import subsystem


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


def test_accept_leaf_records_marker_and_advances_subsystem_and_default_only(
    tmp_path, monkeypatch
):
    repo = _init_boundary_repo(tmp_path)
    contrib_branch = "ai-org/contrib/demo-alpha"
    previous_contrib_tip = _write_boundary_contribution(
        repo, contrib_branch, "alpha.txt"
    )
    previous_mainline_tip = git_wrapper.head_sha(repo, "ai-org/mainline")
    events = _record_boundary_events(monkeypatch)

    result = subsystem_boundary.accept_leaf(repo, contrib_branch)

    assert result == {
        "ok": True,
        "contrib_branch": contrib_branch,
        "marker_commit": result["marker_commit"],
        "subsystem_commit": result["subsystem_commit"],
        "default_branch": "main",
    }
    assert _git(repo, "show", "-s", "--format=%s", result["marker_commit"]).stdout.strip() == "acceptance: reachable"
    marker_body = _git(
        repo, "show", "-s", "--format=%b", result["marker_commit"]
    ).stdout.strip()
    assert "requester-fiat" in marker_body.lower()
    assert _git(repo, "rev-parse", f"{result['marker_commit']}^").stdout.strip() == previous_contrib_tip

    subsystem_parents = _git(
        repo, "show", "-s", "--format=%P", result["subsystem_commit"]
    ).stdout.split()
    assert len(subsystem_parents) == 2
    assert subsystem_parents[1] == result["marker_commit"]
    assert git_wrapper.head_sha(repo, "ai-org/subsystem") == result["subsystem_commit"]
    assert git_wrapper.head_sha(repo, "main") == result["subsystem_commit"]
    assert git_wrapper.head_sha(repo, "ai-org/mainline") == previous_mainline_tip
    assert [event_type for event_type, _payload in events] == [
        "subsystem.accept_leaf.started",
        "subsystem.accept_leaf.result",
    ]


def test_handoff_series_refuses_unresolved_leaves_without_moving_refs(
    tmp_path, monkeypatch
):
    repo = _init_boundary_repo(tmp_path)
    series_branch = "ai-org/patch-series/demo-series"
    leaves = [
        ("sub/alpha", "ai-org/contrib/demo-alpha"),
        ("sub/beta", "ai-org/contrib/demo-beta"),
    ]
    _write_series_fixture(repo, series_branch, leaves)
    for node_path, contrib_branch in leaves:
        _write_boundary_contribution(
            repo, contrib_branch, f"{Path(node_path).name}.txt"
        )
    refs_before = _head_refs(repo)
    events = _record_boundary_events(monkeypatch)

    result = subsystem_boundary.handoff_series(repo, series_branch)

    assert result["ok"] is False
    assert result["status"] == "series_incomplete"
    assert [row["node_path"] for row in result["unresolved"]] == [
        "sub/alpha",
        "sub/beta",
    ]
    assert _head_refs(repo) == refs_before
    assert [event_type for event_type, _payload in events] == [
        "subsystem.handoff_series.started",
        "subsystem.handoff_series.result",
    ]


def test_handoff_series_records_every_leaf_and_is_exactly_once(tmp_path):
    repo = _init_boundary_repo(tmp_path)
    series_branch = "ai-org/patch-series/demo-series"
    declared_leaves = [
        ("sub/beta", "ai-org/contrib/demo-beta"),
        ("sub/alpha", "ai-org/contrib/demo-alpha"),
    ]
    _write_series_fixture(repo, series_branch, declared_leaves)

    alpha_tip = _write_boundary_contribution(
        repo, "ai-org/contrib/demo-alpha", "alpha.txt"
    )
    alpha_acceptance = subsystem_boundary.accept_leaf(
        repo, "ai-org/contrib/demo-alpha"
    )
    assert git_wrapper.is_ancestor(repo, alpha_tip, alpha_acceptance["marker_commit"])

    # The second leaf intentionally starts from the default branch after the
    # first acceptance, proving the successor-base operational requirement.
    beta_tip = _write_boundary_contribution(
        repo, "ai-org/contrib/demo-beta", "beta.txt"
    )
    beta_acceptance = subsystem_boundary.accept_leaf(
        repo, "ai-org/contrib/demo-beta"
    )
    assert git_wrapper.is_ancestor(repo, alpha_acceptance["subsystem_commit"], beta_tip)

    series_tip = git_wrapper.head_sha(repo, series_branch)
    subsystem_tip = git_wrapper.head_sha(repo, "ai-org/subsystem")
    mainline_before = git_wrapper.head_sha(repo, "ai-org/mainline")

    first = subsystem_boundary.handoff_series(repo, series_branch)

    expected_leaves = [
        {
            "node_path": "sub/alpha",
            "contrib_branch": "ai-org/contrib/demo-alpha",
            "tip_sha": alpha_acceptance["marker_commit"],
        },
        {
            "node_path": "sub/beta",
            "contrib_branch": "ai-org/contrib/demo-beta",
            "tip_sha": beta_acceptance["marker_commit"],
        },
    ]
    assert first == {
        "ok": True,
        "series_branch": series_branch,
        "mainline_commit": first["mainline_commit"],
        "leaves": expected_leaves,
    }
    assert first["mainline_commit"] != mainline_before
    assert git_wrapper.head_sha(repo, "ai-org/mainline") == first["mainline_commit"]
    assert _git(
        repo, "show", "-s", "--format=%s", first["mainline_commit"]
    ).stdout.strip() == "mainline: accept series demo-series"

    mainline_parents = _git(
        repo, "show", "-s", "--format=%P", first["mainline_commit"]
    ).stdout.split()
    assert mainline_parents == [mainline_before, subsystem_tip]
    body = _git(
        repo, "show", "-s", "--format=%b", first["mainline_commit"]
    ).stdout
    body_lines = body.splitlines()
    for leaf in expected_leaves:
        assert any(
            leaf["node_path"] in line
            and leaf["contrib_branch"] in line
            and leaf["tip_sha"] in line
            for line in body_lines
        )
    assert series_tip in body
    assert subsystem_tip in body
    assert any("count" in line.lower() and "2" in line for line in body_lines)

    second = subsystem_boundary.handoff_series(repo, series_branch)

    assert second["ok"] is True
    assert second["mainline_commit"] == first["mainline_commit"]
    assert git_wrapper.head_sha(repo, "ai-org/mainline") == first["mainline_commit"]
    assert git_wrapper.log_subjects(repo, "ai-org/mainline").count(
        "mainline: accept series demo-series"
    ) == 1


def _init_boundary_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "boundary-repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.name", "Boundary Test")
    _git(repo, "config", "user.email", "boundary-test@example.invalid")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "base")
    _git(repo, "branch", "-M", "main")
    _git(repo, "branch", "ai-org/mainline", "main")
    return repo


def _write_boundary_contribution(
    repo: Path, branch: str, filename: str
) -> str:
    result = git_wrapper.create_branch_with_files(
        repo,
        branch,
        "main",
        {filename: f"implementation for {branch}\n"},
        commit_message=f"implement: {Path(filename).stem}",
    )
    return result["commit"]


def _write_series_fixture(
    repo: Path,
    series_branch: str,
    leaves: list[tuple[str, str]],
) -> str:
    children = []
    files: dict[str, object] = {}
    for node_path, contrib_branch in leaves:
        child_key = Path(node_path).name
        children.append(
            {
                "id": f"demo-series:{node_path}",
                "serial_id": "",
                "child_key": child_key,
                "node_path": node_path,
                "address": f"{series_branch}:{node_path}",
                "allowed_subtree": f"{node_path}/",
                "contrib_branch": contrib_branch,
                "lifecycle_status": "submitted_for_maintainer_review",
                "edges": [],
                "scope_item_ids": [],
            }
        )
        files[f"{node_path}/patch-series-manifest.json"] = {
            "schema": "patch_series-network-node-v1",
            "identity_stage": "forming",
            "id": f"demo-series:{node_path}",
            "serial_id": "",
            "child_key": child_key,
            "node_path": node_path,
            "parent_branch": series_branch,
            "parent_node_path": ".",
            "relation_from_parent": "split-into",
            "split_operator": "AND",
            "edges": [],
            "scope_item_ids": [],
            "lifecycle_status": "submitted_for_maintainer_review",
            "ownership": {"request_owner": "parent", "interior_owner": "child"},
            "write_scope": {"allowed_subtree": f"{node_path}/"},
            "contrib_branch": contrib_branch,
        }

    files["patch-series-manifest.json"] = {
        "schema": "patch_series-network-node-v1",
        "identity_stage": "serialized-parent",
        "node_path": ".",
        "branch": series_branch,
        "relation_from_parent": "root",
        "lifecycle_status": "active",
        "ownership": {"request_owner": "requester", "interior_owner": "parent"},
        "write_scope": {"allowed_subtree": "."},
        "scope_item_ids": [],
        "parent_retained_scope_ids": [],
        "children": [
            {
                "child_key": child["child_key"],
                "node_path": child["node_path"],
                "lifecycle_status": child["lifecycle_status"],
                "edges": [],
            }
            for child in children
        ],
    }
    files["series-coverage-ledger.json"] = {
        "schema": "series-coverage-ledger-v1",
        "ledger_revision": 1,
        "supersedes_ledger_commit": "",
        "rebaselined_from_escalation": {},
        "parent_branch": series_branch,
        "relation": "split-into",
        "split_operator": "AND",
        "scope_items": [],
        "coverage": [],
        "parent_retained_scope_ids": [],
        "children": children,
    }
    result = git_wrapper.create_branch_with_files(
        repo,
        series_branch,
        "main",
        files,
        commit_message="network: deterministic series fixture",
    )
    return result["commit"]


def _head_refs(repo: Path) -> dict[str, str]:
    lines = _git(
        repo,
        "for-each-ref",
        "--format=%(refname) %(objectname)",
        "refs/heads",
    ).stdout.splitlines()
    return dict(line.split(" ", 1) for line in lines)


def _record_boundary_events(monkeypatch) -> list[tuple[str, dict[str, object]]]:
    events: list[tuple[str, dict[str, object]]] = []

    def record(event_type, payload=None, **_kwargs):
        events.append((event_type, dict(payload or {})))
        return {"event_id": f"event-{len(events)}"}

    monkeypatch.setattr(subsystem_boundary.org_log, "emit", record)
    return events


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
    _git(repo, "branch", "-M", "main")
    return repo


def _commit_on_branch(repo, branch, filename):
    _git(repo, "checkout", "-B", branch, "main")
    (repo / filename).write_text(f"{filename}\n", encoding="utf-8")
    _git(repo, "add", filename)
    _git(repo, "commit", "-m", f"add {filename}")
    _git(repo, "checkout", "main")
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
