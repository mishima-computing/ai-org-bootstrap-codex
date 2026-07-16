"""patch series phase pull entry: process off-git inbox requests, author revisions, then review patch seriess.

Lifecycle:
1. Requesters run ``python -m ai_org.patchwork_queue.submit <repo> <request>``.
2. ``submit`` writes the raw request to the off-git inbox
   ``<repo>/.ai-org/inbox`` or ``AI_ORG_INBOX``.
3. ``pull`` takes one unprocessed inbox item first and runs the receive gate.
4. A needs-revision branch is author-reformed on the same patch series branch as vN+1.
5. Only a promoted patch series becomes a git branch at ``ai-org/patch-series/<id>``. Requests
   that need work or are rejected stay as processed inbox records.

Memento: LKML-style review means reviewers object and the author reworks; v2 is
same-branch with a Changes-since body. Review records are append-only. Accepted
patch seriess receive serial tags on direction-ok. AI Org adds a bounded-rounds backstop,
which diverges from kernel practice to keep the patch series phase from running forever.

Memento: .ai-org/log/runs is observability, not state. Resume and pull logic must
not read logs to decide inbox, review, revision, or lineage work.
"""
from __future__ import annotations

import json
import inspect
from pathlib import Path
import re
import shutil
from typing import Any, Mapping

import ai_org.log as org_log
from ai_org import git_wrapper
from ai_org.patchwork_queue import patch_series_gate as network
from ai_org.patchwork_queue import receive
from ai_org.patchwork_queue import review
from ai_org.patchwork_queue import submit as submission


WORK_ORDER_PREFIX = "ai-org/patch-series/"


def refine(repo, patch_series_id_or_branch: str, **kwargs):
    """Split an oversized direction-ok patch series into nested network child nodes."""
    return network.refine(repo, patch_series_id_or_branch, **kwargs)


def pull(repo, *, progress_path: str | Path | None = None):
    """Process one inbox request, author revision, review, or network item."""
    ctx = org_log.RunContext(repo=repo, stage="patch_series.pull")
    intake_result = _pull_inbox(repo, progress_path=progress_path, ctx=ctx)
    if intake_result is not None:
        return _emit_pull_outcome(intake_result, ctx)

    for branch in sorted(git_wrapper.branches(repo, f"{WORK_ORDER_PREFIX}*")):
        if _is_terminal_patch_series(repo, branch):
            continue
        experience_pending = receive.implementation_experience_pending(repo, branch)
        if (_latest_needs_revision_is_head(repo, branch) or experience_pending) and _has_review_round_record(repo, branch):
            org_log.emit("patch_series.pull.item_selected", {"kind": "author_revision", "branch": branch}, ctx=ctx)
            # Memento: reform runs under the pull's RunContext so its codex calls
            # (including decision-step evaluation work) land in the pull's run dir,
            # not orphan adapter-boundary run dirs.
            result = _call_with_optional_ctx(
                receive.reform_patch_series,
                repo,
                branch.removeprefix(WORK_ORDER_PREFIX),
                ctx=ctx.child(stage="patch_series.reform"),
            )
            return _emit_pull_outcome(result, ctx)

    for branch in sorted(git_wrapper.branches(repo, f"{WORK_ORDER_PREFIX}*")):
        if _is_terminal_patch_series(repo, branch):
            continue
        if _latest_needs_revision_is_head(repo, branch):
            continue
        org_log.emit("patch_series.pull.item_selected", {"kind": "review", "branch": branch}, ctx=ctx)
        result = _call_with_optional_ctx(
            review.run_patch_series_review,
            repo,
            branch.removeprefix(WORK_ORDER_PREFIX),
            ctx=ctx.child(stage="patch_series.review"),
        )
        return _emit_pull_outcome(result, ctx)

    for branch in sorted(git_wrapper.branches(repo, f"{WORK_ORDER_PREFIX}*")):
        if network.split_pending(repo, branch):
            org_log.emit("patch_series.pull.item_selected", {"kind": "lineage_refine", "branch": branch}, ctx=ctx)
            result = _call_with_optional_ctx(network.refine, repo, branch, ctx=ctx.child(stage="patch_series.network.refine"))
            return _emit_pull_outcome(result, ctx)

    for branch in sorted(git_wrapper.branches(repo, f"{WORK_ORDER_PREFIX}*")):
        if network.rebaseline_pending(repo, branch):
            org_log.emit("patch_series.pull.item_selected", {"kind": "lineage_rebaseline", "branch": branch}, ctx=ctx)
            result = _call_with_optional_ctx(network.rebaseline, repo, branch, ctx=ctx.child(stage="patch_series.network.rebaseline"))
            return _emit_pull_outcome(result, ctx)

    for branch in sorted(git_wrapper.branches(repo, f"{WORK_ORDER_PREFIX}*")):
        if network.stale_revalidation_pending(repo, branch):
            org_log.emit("patch_series.pull.item_selected", {"kind": "lineage_revalidate", "branch": branch}, ctx=ctx)
            result = _call_with_optional_ctx(network.revalidate_stale, repo, branch, ctx=ctx.child(stage="patch_series.network.revalidate"))
            return _emit_pull_outcome(result, ctx)

    for item in network.ready_nested_elaboration(repo):
        org_log.emit(
            "patch_series.pull.item_selected",
            {"kind": "lineage_elaborate", "branch": item["branch"], "node_path": item["node_path"]},
            ctx=ctx,
        )
        result = _call_with_optional_ctx(network.elaborate, repo, item["address"], ctx=ctx.child(stage="patch_series.network.elaborate"))
        return _emit_pull_outcome(result, ctx)

    for branch in sorted(git_wrapper.branches(repo, f"{WORK_ORDER_PREFIX}*")):
        if network.coarse_ready(repo, branch):
            org_log.emit("patch_series.pull.item_selected", {"kind": "lineage_elaborate", "branch": branch}, ctx=ctx)
            result = _call_with_optional_ctx(network.elaborate, repo, branch, ctx=ctx.child(stage="patch_series.network.elaborate"))
            return _emit_pull_outcome(result, ctx)
    return None


def _emit_pull_outcome(result: dict[str, Any] | None, ctx: org_log.RunContext):
    payload = {
        "status": str(result.get("status") or "unknown") if isinstance(result, Mapping) else "unknown",
        "failed_step": result.get("failed_step") if isinstance(result, Mapping) else None,
    }
    if isinstance(result, Mapping) and result.get("failure_mode"):
        payload["failure_mode"] = result.get("failure_mode")
    if isinstance(result, Mapping):
        for key in (
            "route",
            "frozen_oid",
            "diagnostic",
            "authorability_decision",
        ):
            if result.get(key) is not None:
                payload[key] = result[key]
    org_log.emit("patch_series.pull.outcome", payload, ctx=ctx)
    return result


def _is_terminal_patch_series(repo, branch: str) -> bool:
    if git_wrapper.has_subject(repo, branch, "patch_series: nak"):
        return True
    default = git_wrapper.default_branch(repo)
    if branch != default and git_wrapper.is_ancestor(repo, branch, default):
        return True
    for subject in git_wrapper.log_subjects(repo, branch):
        if "patch_series: needs-revision round " in subject:
            return False
        if subject.startswith("patch_series v"):
            return False
        if "patch_series: direction-ok" in subject:
            return True
    return False


def _latest_needs_revision_is_head(repo, branch: str) -> bool:
    subjects = git_wrapper.log_subjects(repo, branch)
    for subject in subjects:
        if "patch_series: direction-ok" in subject or "patch_series: nak" in subject:
            return False
        if "patch_series: needs-revision round " in subject:
            return True
        if subject.startswith("patch_series v"):
            return False
    return False


def _has_review_round_record(repo, branch: str) -> bool:
    return review.has_review_round_record(repo, branch)


def _pull_inbox(
    repo,
    *,
    progress_path: str | Path | None = None,
    ctx: org_log.RunContext | None = None,
) -> dict[str, Any] | None:
    inbox = submission.inbox_dir(repo)
    if not inbox.exists():
        return None
    files = _unprocessed_inbox_files(inbox)
    if not files:
        return None

    path = files[0]
    envelope = _read_inbox_envelope(path)
    request = envelope["request"]
    if ctx is not None:
        org_log.emit(
            "patch_series_pull.inbox_dequeued",
            {"inbox_id": envelope["id"], "path": str(path)},
            ctx=ctx.child(request_id=str(envelope["id"])),
        )
    result = _call_intake(
        request,
        repo,
        progress_path=progress_path,
        ctx=ctx.child(request_id=str(envelope["id"])) if ctx is not None else None,
        request_provenance={"request_id": envelope["id"], "payload": request},
    )
    if ctx is not None:
        org_log.emit(
            "intake.completed",
            {"inbox_id": envelope["id"], "status": result.get("status"), "ok": result.get("ok")},
            ctx=ctx.child(request_id=str(envelope["id"])),
        )
    if result.get("status") != "promoted":
        publication = _record_terminal_request_ref(Path(repo), envelope, result)
        if not publication.ok:
            return {
                "ok": False,
                "status": "publication_failed",
                "failed_step": "request_outcome_publication",
                "error": str(publication.failure or "request outcome publication failed"),
            }
    _mark_processed(inbox, path, envelope, result)
    return result


def _call_intake(
    request: Mapping[str, Any],
    repo,
    *,
    progress_path: str | Path | None,
    ctx: org_log.RunContext | None,
    request_provenance: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {"progress_path": progress_path}
    try:
        parameters = inspect.signature(receive.intake).parameters
    except (TypeError, ValueError):
        parameters = {}
    if "ctx" in parameters:
        kwargs["ctx"] = ctx
    if "request_provenance" in parameters:
        kwargs["request_provenance"] = request_provenance
    return receive.intake(request, repo, **kwargs)


def _call_with_optional_ctx(func, *args, ctx: org_log.RunContext):
    try:
        parameters = inspect.signature(func).parameters
    except (TypeError, ValueError):
        parameters = {}
    if "ctx" in parameters:
        return func(*args, ctx=ctx)
    return func(*args)


def _unprocessed_inbox_files(inbox: Path) -> list[Path]:
    files = [
        path
        for path in inbox.glob("*.json")
        if path.is_file() and not path.name.endswith(".result.json")
    ]
    return sorted(files, key=lambda path: (path.stat().st_mtime, path.name))


def _read_inbox_envelope(path: Path) -> dict[str, Any]:
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, Mapping):
        raise ValueError(f"Inbox file {path} must contain a JSON object.")
    request = loaded.get("request")
    if not isinstance(request, Mapping):
        raise ValueError(f"Inbox file {path} must contain a request object.")
    envelope = dict(loaded)
    envelope["request"] = dict(request)
    if not isinstance(envelope.get("id"), str) or not envelope["id"]:
        envelope["id"] = path.stem
    return envelope


def _mark_processed(inbox: Path, path: Path, envelope: Mapping[str, Any], result: Mapping[str, Any]) -> None:
    processed = inbox / "processed"
    processed.mkdir(parents=True, exist_ok=True)
    inbox_id = str(envelope.get("id") or path.stem)
    processed_request = processed / f"{inbox_id}.json"
    processed_result = processed / f"{inbox_id}.result.json"
    shutil.move(str(path), processed_request)
    processed_result.write_text(
        json.dumps(_result_record(result), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _record_terminal_request_ref(
    repo: Path, envelope: Mapping[str, Any], result: Mapping[str, Any]
) -> git_wrapper.GitPublicationResult:
    inbox_id = str(envelope.get("id") or "request")
    request = envelope.get("request") if isinstance(envelope.get("request"), Mapping) else {}
    status = str(result.get("status") or "unknown")
    safe_id = re.sub(r"[^A-Za-z0-9._/-]+", "-", inbox_id).strip("/.") or "request"
    ref = f"refs/ai-org/request-outcomes/{safe_id}"
    provenance = receive._request_provenance_record(  # noqa: SLF001 - pull owns inbox envelope provenance.
        request,
        {"request_id": inbox_id, "payload": request},
    )
    outcome = {
        "request_id": inbox_id,
        "status": status,
        "result": dict(result),
        "memento": (
            "Rejected and non-promoted requests are terminal request history, not reviewable "
            "patch-series branches. This lightweight ref preserves provenance without making "
            "local processed inbox bookkeeping authoritative."
        ),
    }
    expected = git_wrapper.head_sha(repo, ref) or ""
    parent = expected or git_wrapper.head_sha(repo, git_wrapper.default_branch(repo))
    if parent is None:
        return git_wrapper.GitPublicationResult(
            "rejected", ref, expected,
            failure=git_wrapper.GitBodyFailure("GIT_PARENT", ref, "default-parent"),
        )
    try:
        prepared = git_wrapper.prepare_terminal_request(
            repo, ref, provenance, outcome, parent=parent, expected_ref_oid=expected
        )
    except git_wrapper.GitBodyFailure as failure:
        return git_wrapper.GitPublicationResult("rejected", ref, expected, failure=failure)
    return git_wrapper.publish_terminal_request(repo, prepared)


def _result_record(result: Mapping[str, Any]) -> dict[str, Any]:
    status = str(result.get("status", "unknown"))
    record: dict[str, Any] = {"status": status}
    if status == "promoted":
        if isinstance(result.get("branch"), str):
            record["patch_series_branch"] = result["branch"]
        if isinstance(result.get("id"), str):
            record["patch_series_id"] = result["id"]
    for field in ("error", "failed_step", "violations"):
        if field in result:
            record[field] = result[field]
    record["intake_result"] = dict(result)
    return record
