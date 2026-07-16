"""Deterministic subsystem-maintainer acceptance and series hand-off boundaries."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import tempfile
from typing import Any, Iterator, Mapping

from ai_org import git_wrapper
import ai_org.log as org_log
from ai_org.patchwork_queue import patch_series_gate


SUBSYSTEM_BRANCH = "ai-org/subsystem"
MAINLINE_BRANCH = "ai-org/mainline"
ACCEPTANCE_SUBJECT = "acceptance: reachable"
ACCEPTANCE_BODY = (
    "Requester-fiat acceptance: this empty-headed maintainer form accepts the "
    "contribution without an independent judgment."
)

# Memento: the canonical flow has two durable boundaries. Per-leaf acceptance
# advances the subsystem tree and DEFAULT so successor leaves inherit accepted
# work. Only the once-per-series hand-off advances mainline.


def accept_leaf(
    repo: str | Path,
    contrib_branch: str,
    *,
    ctx: org_log.RunContext | None = None,
) -> dict[str, Any]:
    """Accept one contribution into the subsystem tree and fast-forward DEFAULT."""
    repo_path = Path(repo).resolve()
    stage = "subsystem.accept_leaf"
    log_ctx = (ctx or org_log.RunContext(repo=repo_path, stage=stage)).child(stage=stage)
    org_log.emit(f"{stage}.started", {"contrib_branch": contrib_branch}, ctx=log_ctx)
    try:
        result = _accept_leaf(repo_path, contrib_branch)
    except RuntimeError as exc:
        result = {
            "ok": False,
            "status": "git_error",
            "contrib_branch": contrib_branch,
            "detail": str(exc),
        }
    org_log.emit(
        f"{stage}.result",
        result,
        ctx=log_ctx,
        severity="info" if result.get("ok") else "error",
    )
    return result


def _accept_leaf(repo: Path, contrib_branch: str) -> dict[str, Any]:
    if not git_wrapper.branch_exists(repo, contrib_branch):
        return {
            "ok": False,
            "status": "contrib_branch_missing",
            "contrib_branch": contrib_branch,
        }

    default = _local_default_branch(repo)
    marker = git_wrapper.commit_empty(
        repo,
        contrib_branch,
        ACCEPTANCE_SUBJECT,
        body=ACCEPTANCE_BODY,
    )["commit"]

    if not git_wrapper.branch_exists(repo, SUBSYSTEM_BRANCH):
        git_wrapper._git_required(repo, "branch", SUBSYSTEM_BRANCH, default)

    with _branch_worktree(repo, SUBSYSTEM_BRANCH, prefix="ai-org-subsystem-") as worktree:
        git_wrapper._git_required(
            worktree,
            *git_wrapper.identity_config_args(),
            "merge",
            "--no-ff",
            "-m",
            f"subsystem: accept {contrib_branch}",
            marker,
        )
        subsystem_commit = _required_head(repo, SUBSYSTEM_BRANCH)

    _fast_forward_branch(repo, default, subsystem_commit)

    return {
        "ok": True,
        "contrib_branch": contrib_branch,
        "marker_commit": marker,
        "subsystem_commit": subsystem_commit,
        "default_branch": default,
    }


def handoff_series(
    repo: str | Path,
    series_branch: str,
    *,
    ctx: org_log.RunContext | None = None,
) -> dict[str, Any]:
    """Hand one completely resolved patch series from subsystem to mainline."""
    repo_path = Path(repo).resolve()
    normalized = patch_series_gate._patch_series_branch(series_branch)
    series_slug = patch_series_gate._patch_series_id(normalized)
    stage = "subsystem.handoff_series"
    log_ctx = (
        ctx
        or org_log.RunContext(
            repo=repo_path,
            patch_series_id=series_slug,
            stage=stage,
        )
    ).child(patch_series_id=series_slug, stage=stage)
    org_log.emit(f"{stage}.started", {"series_branch": normalized}, ctx=log_ctx)
    try:
        result = _handoff_series(repo_path, normalized, series_slug)
    except RuntimeError as exc:
        result = {
            "ok": False,
            "status": "git_error",
            "series_branch": normalized,
            "detail": str(exc),
        }
    severity = "info" if result.get("ok") else (
        "warning" if result.get("status") == "series_incomplete" else "error"
    )
    org_log.emit(f"{stage}.result", result, ctx=log_ctx, severity=severity)
    return result


def _handoff_series(repo: Path, series_branch: str, series_slug: str) -> dict[str, Any]:
    leaves, unresolved = _series_leaf_snapshot(repo, series_branch)
    if unresolved:
        return {
            "ok": False,
            "status": "series_incomplete",
            "series_branch": series_branch,
            "unresolved": unresolved,
        }

    subject = f"mainline: accept series {series_slug}"
    existing = _mainline_record_commit(repo, subject)
    if existing:
        return {
            "ok": True,
            "status": "already-current",
            "series_branch": series_branch,
            "mainline_commit": existing,
            "leaves": leaves,
        }

    series_tip = git_wrapper.head_sha(repo, series_branch)
    subsystem_tip = git_wrapper.head_sha(repo, SUBSYSTEM_BRANCH)
    if not series_tip:
        return {
            "ok": False,
            "status": "series_incomplete",
            "series_branch": series_branch,
            "unresolved": leaves or [_leaf_row(".", "", "")],
        }
    if not subsystem_tip:
        return {
            "ok": False,
            "status": "subsystem_missing",
            "series_branch": series_branch,
        }

    if git_wrapper.branch_exists(repo, MAINLINE_BRANCH):
        mainline_tip = _required_head(repo, MAINLINE_BRANCH)
        if git_wrapper.is_ancestor(repo, subsystem_tip, mainline_tip):
            return {
                "ok": True,
                "status": "already-current",
                "series_branch": series_branch,
                "mainline_commit": mainline_tip,
                "leaves": leaves,
            }
    else:
        base = _base_before_series_acceptance(repo, subsystem_tip, leaves)
        if not base:
            return {
                "ok": False,
                "status": "mainline_base_missing",
                "series_branch": series_branch,
            }
        git_wrapper._git_required(repo, "branch", MAINLINE_BRANCH, base)

    body = _handoff_body(
        leaves,
        series_tip=series_tip,
        subsystem_tip=subsystem_tip,
    )

    # Memento: this is deliberately the empty-headed maintainer. The permanent
    # judgment slot is this function boundary; this first form always accepts
    # once the gate's Git-derived leaf-resolution precondition is satisfied.
    with _branch_worktree(repo, MAINLINE_BRANCH, prefix="ai-org-mainline-") as worktree:
        git_wrapper._git_required(
            worktree,
            *git_wrapper.identity_config_args(),
            "merge",
            "--no-ff",
            "-m",
            subject,
            "-m",
            body,
            subsystem_tip,
        )
        mainline_commit = _required_head(repo, MAINLINE_BRANCH)

    return {
        "ok": True,
        "series_branch": series_branch,
        "mainline_commit": mainline_commit,
        "leaves": leaves,
    }


def _series_leaf_snapshot(
    repo: Path,
    series_branch: str,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    network = patch_series_gate._load_manifest_network_from_git(
        repo,
        series_branch,
        logical_branch=series_branch,
    )
    manifests = [
        dict(value)
        for value in network.get("nodes", {}).values()
        if isinstance(value, Mapping)
    ]
    by_path = {
        str(manifest.get("node_path") or "."): manifest
        for manifest in manifests
    }
    root = by_path.get(".", {})
    declared_paths = {
        str(child.get("node_path") or "")
        for child in root.get("children", [])
        if isinstance(child, Mapping) and str(child.get("node_path") or "")
    }
    child_paths = {path for path in by_path if path != "."}
    leaf_paths = sorted(declared_paths | child_paths)
    if not leaf_paths:
        leaf_paths = ["."]

    structural_error = bool(network.get("errors"))
    leaves: list[dict[str, str]] = []
    unresolved: list[dict[str, str]] = []
    for node_path in leaf_paths:
        manifest = by_path.get(node_path, {})
        if node_path == ".":
            contrib_branch = patch_series_gate._contrib_branch_for_patch_series_branch(
                repo,
                series_branch,
            )
        else:
            contrib_branch = str(manifest.get("contrib_branch") or "")
        tip = git_wrapper.head_sha(repo, contrib_branch) if contrib_branch else None
        row = _leaf_row(node_path, contrib_branch, tip or "")
        leaves.append(row)
        address = series_branch if node_path == "." else f"{series_branch}:{node_path}"
        resolved = False
        if not structural_error and manifest and contrib_branch and tip:
            try:
                resolved = patch_series_gate.resolved(repo, address)
            except RuntimeError:
                resolved = False
        if not resolved:
            unresolved.append(row)
    return leaves, unresolved


def _leaf_row(node_path: str, contrib_branch: str, tip_sha: str) -> dict[str, str]:
    return {
        "node_path": node_path,
        "contrib_branch": contrib_branch,
        "tip_sha": tip_sha,
    }


def _handoff_body(
    leaves: list[dict[str, str]],
    *,
    series_tip: str,
    subsystem_tip: str,
) -> str:
    lines = ["Leaves:"]
    lines.extend(
        f"- {row['node_path']} | {row['contrib_branch']} | {row['tip_sha']}"
        for row in leaves
    )
    lines.extend(
        [
            "",
            f"Series branch tip: {series_tip}",
            f"Subsystem tip: {subsystem_tip}",
            "",
            (
                f"Counts: leaves={len(leaves)} resolved={len(leaves)} "
                "unresolved=0"
            ),
        ]
    )
    return "\n".join(lines)


def _mainline_record_commit(repo: Path, subject: str) -> str | None:
    current = git_wrapper.head_sha(repo, MAINLINE_BRANCH)
    seen: set[str] = set()
    while current and current not in seen:
        seen.add(current)
        shown = git_wrapper._git(repo, "show", "-s", "--format=%s", current)
        parents = git_wrapper.parent_commits(repo, current)
        if shown.returncode == 0 and shown.stdout.strip() == subject and len(parents) >= 2:
            return current
        current = parents[0] if parents else None
    return None


def _base_before_series_acceptance(
    repo: Path,
    subsystem_tip: str,
    leaves: list[dict[str, str]],
) -> str | None:
    leaf_tips = {row["tip_sha"] for row in leaves if row["tip_sha"]}
    matched: set[str] = set()
    current: str | None = subsystem_tip
    candidate: str | None = None
    seen: set[str] = set()
    while current and current not in seen:
        seen.add(current)
        parents = git_wrapper.parent_commits(repo, current)
        if not parents:
            break
        accepted_here = leaf_tips.intersection(parents[1:])
        if accepted_here:
            matched.update(accepted_here)
            candidate = parents[0]
            if matched == leaf_tips:
                break
        current = parents[0]
    return candidate if matched == leaf_tips else None


def _local_default_branch(repo: Path) -> str:
    default = git_wrapper.default_branch(repo)
    if default.startswith("refs/heads/"):
        default = default.removeprefix("refs/heads/")
    if default.startswith("origin/"):
        local = default.removeprefix("origin/")
        if git_wrapper.branch_exists(repo, local):
            default = local
    if not git_wrapper.branch_exists(repo, default):
        raise RuntimeError(f"local default branch is missing: {default}")
    return default


def _fast_forward_branch(repo: Path, branch: str, commit: str) -> None:
    if not git_wrapper.is_ancestor(repo, branch, commit):
        raise RuntimeError(f"{branch} cannot fast-forward to {commit}")
    if git_wrapper.current_branch(repo) == branch:
        git_wrapper._git_required(repo, "merge", "--ff-only", commit)
        return
    with _branch_worktree(repo, branch, prefix="ai-org-default-") as worktree:
        git_wrapper._git_required(worktree, "merge", "--ff-only", commit)


@contextmanager
def _branch_worktree(repo: Path, branch: str, *, prefix: str) -> Iterator[Path]:
    with tempfile.TemporaryDirectory(prefix=prefix) as temp_dir:
        worktree = Path(temp_dir) / "worktree"
        added = git_wrapper._git(repo, "worktree", "add", str(worktree), branch)
        if added.returncode != 0:
            raise RuntimeError(added.stderr.strip() or added.stdout.strip() or "git worktree add failed")
        try:
            yield worktree
        finally:
            git_wrapper._git(repo, "worktree", "remove", "--force", str(worktree))


def _required_head(repo: Path, ref: str) -> str:
    commit = git_wrapper.head_sha(repo, ref)
    if not commit:
        raise RuntimeError(f"Git ref has no commit: {ref}")
    return commit
