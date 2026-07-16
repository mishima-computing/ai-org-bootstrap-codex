"""Open-series discovery: ls-remote + committed artifacts, nothing else.

OPEN is the TASK's status ONLY (brief 23 addendum, no-lock law). A series/leaf
is OPEN when all of:
  1. the series branch carries `patch_series: direction-ok`, no
     `patch_series: nak`, and is not an ancestor of the default branch;
  2. its lifecycle canonicalizes to ready_for_patch_authoring and is not
     stale/blocked (root: patch-series-metadata.json; leaf: node manifest);
  3. the common producer-pair and exact-scope vet admits authorability from
     the same frozen remote-series OID used for the row projection.

Memento: authoring announcements and existing contribution branches are
REPORTED as visibility fields on each row but NEVER enter the open predicate —
taking does not change "open" (家族間重複=競争: two families on one open series
is intended competition, resolved downstream at review). Do not add an
exclusion clause here; that would silently re-derive the retired lock.

Ordering is deterministic (sorted branch names, sorted node paths) — no
scheduler, no assignment.
"""
from __future__ import annotations

import json
from typing import Any, Mapping

from ai_org import git_wrapper, network_bodies, patch_series_bodies
from ai_org.patch_author import announcements, producer_lifecycle
from ai_org.patchwork_queue import patch_series_gate


SERIES_HEADS_PREFIX = "refs/heads/ai-org/patch-series/"
CONTRIB_HEADS_PREFIX = "refs/heads/ai-org/contrib/"
PRODUCER_PAIR_VET_FAILURE_REASON = "producer-pair-vet-failed"
PRODUCER_AUTHORABILITY_CLOSED_REASON = "producer-authorability-closed"

# The complete refspec set a patch-author clone needs: series branches
# (discovery), contrib branches (submitted projection), authoring
# announcements (visibility), subsystem/mainline (merged projection), serial
# tags (serial_for_ref would otherwise silently return None in a clone), and
# semantic notes.
_WILDCARD_REFSPEC_TEMPLATES = (
    "+refs/heads/ai-org/patch-series/*:refs/remotes/{remote}/ai-org/patch-series/*",
    "+refs/heads/ai-org/contrib/*:refs/remotes/{remote}/ai-org/contrib/*",
    "+refs/ai-org/authoring-announcements/*:refs/ai-org/authoring-announcements/*",
    "+refs/ai-org/request-outcomes/*:refs/ai-org/request-outcomes/*",
    "+refs/tags/ai-org/serial/*:refs/tags/ai-org/serial/*",
)
_SINGLE_REFSPEC_TEMPLATES = {
    "refs/heads/ai-org/subsystem": "+refs/heads/ai-org/subsystem:refs/remotes/{remote}/ai-org/subsystem",
    "refs/heads/ai-org/mainline": "+refs/heads/ai-org/mainline:refs/remotes/{remote}/ai-org/mainline",
    "refs/notes/ai-org/semantic-status": "+refs/notes/ai-org/semantic-status:refs/notes/ai-org/semantic-status",
}


def sync(repo, remote: str = "origin") -> dict[str, Any]:
    """Fetch the patch-author refspec set; typed fail-closed result with remote refs."""
    listed = git_wrapper.ls_remote(repo, remote)
    if not listed.get("ok"):
        return {"ok": False, "error_type": "remote_unavailable", "detail": listed.get("detail", "")}
    remote_refs: dict[str, str] = listed["refs"]
    refspecs = [template.format(remote=remote) for template in _WILDCARD_REFSPEC_TEMPLATES]
    # Single-ref fetches hard-fail when the ref is absent on the remote, so
    # they are included only once ls-remote proves them present.
    for ref, template in _SINGLE_REFSPEC_TEMPLATES.items():
        if ref in remote_refs:
            refspecs.append(template.format(remote=remote))
    fetched = git_wrapper.fetch_refspecs(repo, remote, refspecs)
    if not fetched.get("ok"):
        return {"ok": False, "error_type": "fetch_failed", "detail": fetched.get("detail", "")}
    # Withdrawn announcements are DELETED refs; prune locally so absence
    # projects correctly (fetch without prune would keep the stale local copy).
    local_announcements = set(git_wrapper.list_refs(repo, announcements.ANNOUNCEMENT_REF_PREFIX.rstrip("/")))
    for ref in sorted(local_announcements - set(remote_refs)):
        git_wrapper.delete_local_ref(repo, ref)
    return {"ok": True, "remote_refs": remote_refs}


def list_open(repo, remote: str = "origin") -> dict[str, Any]:
    """Project every series address on the canonical remote. Never raises."""
    synced = sync(repo, remote)
    if not synced.get("ok"):
        return synced
    remote_refs: Mapping[str, str] = synced["remote_refs"]
    rows: list[dict[str, Any]] = []
    series_branches = sorted(
        ref.removeprefix("refs/heads/") for ref in remote_refs if ref.startswith(SERIES_HEADS_PREFIX)
    )
    for series_branch in series_branches:
        rows.extend(_series_rows(repo, remote, remote_refs, series_branch))
    return {"ok": True, "rows": rows}


def _series_rows(
    repo,
    remote: str,
    remote_refs: Mapping[str, str],
    series_branch: str,
) -> list[dict[str, Any]]:
    series_ref = f"refs/remotes/{remote}/{series_branch}"
    series_id = announcements.series_id_for_branch(series_branch)

    # Freeze the fetched series tip, then run the shared producer-pair and
    # exact-scope boundary before any route-specific discovery predicate.  In
    # particular, direction/NAK/merged checks and manifest enumeration must
    # not decide a row from the moving remote-tracking ref before the vet.
    frozen_root_oid = git_wrapper.head_sha(repo, series_ref) or ""
    authorability_decision = patch_series_gate.vet_stateless_authorability(
        repo,
        series_branch,
        "no_network_discovery",
        frozen_root_oid=frozen_root_oid or None,
    )
    frozen_ref = authorability_decision.source.frozen_root_oid or frozen_root_oid
    root_snapshot = patch_series_gate.vetted_root_snapshot(
        repo, authorability_decision.source
    )

    series_reasons: list[str] = []
    if not git_wrapper.has_subject(repo, frozen_ref, "patch_series: direction-ok"):
        series_reasons.append("not_direction_ok")
    if git_wrapper.has_subject(repo, frozen_ref, "patch_series: nak"):
        series_reasons.append("nak")
    default = _default_ref(repo, remote)
    if default and git_wrapper.is_ancestor(repo, frozen_ref, default):
        series_reasons.append("merged_into_default")

    authorability_reason = ""
    if authorability_decision.blocked:
        authorability_reason = (
            PRODUCER_PAIR_VET_FAILURE_REASON
            if not authorability_decision.vet_passed
            else PRODUCER_AUTHORABILITY_CLOSED_REASON
        )
    rows: list[dict[str, Any]] = []
    for node_path, lifecycle_reasons, predeclared in _authorable_nodes(
        repo, frozen_ref, series_id, root_snapshot=root_snapshot
    ):
        child_key = announcements.child_key_for_node_path(node_path)
        address_announcements = [
            {
                "ref": evaluation["ref"],
                "author": dict(evaluation["record"]["author"]),
                "contrib_branch": str(evaluation["record"]["contrib_branch"]),
            }
            for evaluation in announcements.list_for_address(repo, series_id, child_key)
            if evaluation["state"] == "authoring" and isinstance(evaluation.get("record"), Mapping)
        ]
        # OPEN = task status only. Contract authorability is task state;
        # announcements and contribution branches remain visibility below,
        # never predicate inputs (no-lock law).
        reasons = [*series_reasons, *lifecycle_reasons]
        if authorability_reason and authorability_reason not in reasons:
            reasons.append(authorability_reason)
        announced_branches = [entry["contrib_branch"] for entry in address_announcements]
        published = sorted(
            branch
            for branch in {predeclared, *announced_branches}
            if f"refs/heads/{branch}" in remote_refs
            and producer_lifecycle.has_implementation_submission(
                repo, f"refs/remotes/{remote}/{branch}"
            )
        )
        rows.append(
            {
                "branch": series_branch,
                "node_path": node_path,
                "address": series_branch if node_path == "." else f"{series_branch}:{node_path}",
                "contrib_branch": predeclared,
                "announcements": address_announcements,
                "published_contrib_branches": published,
                "open": not reasons,
                "reasons": reasons,
                "root_generation": root_snapshot.identity(),
                "authorability_decision": authorability_decision.as_dict(),
            }
        )
    return rows


def _authorable_nodes(
    repo,
    series_ref: str,
    series_id: str,
    *,
    root_snapshot: patch_series_bodies.RootGenerationSnapshot | None = None,
) -> list[tuple[str, list[str], str]]:
    """Yield (node_path, lifecycle_reasons, predeclared_contrib_branch) per address."""
    root_snapshot = root_snapshot or patch_series_bodies.classify_root_generation(
        repo, series_ref
    )
    if root_snapshot.lifecycle_blocked:
        return [
            (
                ".",
                [root_snapshot.disposition],
                f"{announcements.CONTRIB_BRANCH_PREFIX}{series_id}",
            )
        ]
    frozen_ref = root_snapshot.frozen_oid or series_ref
    manifest_paths = sorted(
        path
        for path in git_wrapper.tree_files(repo, frozen_ref)
        if patch_series_gate._is_direct_node_manifest_path(path)
    )
    if not manifest_paths:
        metadata = _read_committed_json(repo, frozen_ref, "patch-series-metadata.json")
        reasons: list[str] = []
        raw_status = str(metadata.get("lifecycle_status", "")) if metadata else ""
        if raw_status == "stale" or raw_status.startswith("blocked:"):
            reasons.append(f"lifecycle:{raw_status}")
        return [(".", reasons, f"{announcements.CONTRIB_BRANCH_PREFIX}{series_id}")]

    nodes: list[tuple[str, list[str], str]] = []
    for manifest_path in manifest_paths:
        node_path = manifest_path.rsplit("/", 1)[0]
        manifest = _read_committed_json(repo, frozen_ref, manifest_path)
        if not manifest:
            nodes.append((node_path, ["manifest_unreadable"], f"{announcements.CONTRIB_BRANCH_PREFIX}{series_id}-{node_path.rsplit('/', 1)[-1]}"))
            continue
        reasons = []
        raw_status = str(manifest.get("lifecycle_status", ""))
        canonical = patch_series_gate._canonical_lifecycle(manifest)
        if raw_status == "stale" or raw_status.startswith("blocked:"):
            reasons.append(f"lifecycle:{raw_status}")
        elif canonical != "ready_for_patch_authoring":
            reasons.append(f"lifecycle:{canonical}")
        declared = manifest.get("contrib_branch")
        predeclared = (
            declared
            if isinstance(declared, str) and declared.startswith(announcements.CONTRIB_BRANCH_PREFIX)
            else f"{announcements.CONTRIB_BRANCH_PREFIX}{series_id}-{node_path.rsplit('/', 1)[-1]}"
        )
        nodes.append((node_path, reasons, predeclared))
    return nodes


def _read_committed_json(repo, ref: str, path: str) -> dict[str, Any]:
    classified = network_bodies.classify_path(path)
    if classified is not None:
        role, _canonical, _historical = classified
        if role in {"root_manifest", "child_manifest"}:
            node_path = "." if role == "root_manifest" else path.rsplit("/", 1)[0]
            selected = network_bodies.resolve_manifest_input(repo, ref, node_path)
            if selected.state == "registered":
                return dict(selected.manifest or {})
            if selected.state != "legacy":
                return {}
        try:
            body = network_bodies.read_network_body(repo, ref, path)
        except Exception:
            return {}
        return dict(body) if isinstance(body, Mapping) else {}
    text = git_wrapper.show_file(repo, ref, path)
    if text is None:
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return dict(parsed) if isinstance(parsed, Mapping) else {}


def _default_ref(repo, remote: str) -> str:
    name = git_wrapper.remote_default_branch(repo, remote)
    if name:
        candidate = f"refs/remotes/{remote}/{name}"
        if git_wrapper.head_sha(repo, candidate) is not None:
            return candidate
    try:
        return git_wrapper.default_branch(repo)
    except RuntimeError:
        return ""
