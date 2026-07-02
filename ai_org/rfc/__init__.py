"""RFC phase pull entry: process off-git inbox requests, author revisions, then review RFCs.

Lifecycle:
1. Requesters run ``python -m ai_org.rfc.submit <repo> <request>``.
2. ``submit`` writes the raw request to the off-git inbox
   ``<repo>/.ai-org/inbox`` or ``AI_ORG_INBOX``.
3. ``pull`` takes one unprocessed inbox item first and runs the receive gate.
4. A needs-revision branch is author-reformed on the same RFC branch as vN+1.
5. Only a promoted RFC becomes a git branch at ``ai-org/rfc/<id>``. Requests
   that need work or are rejected stay as processed inbox records.

Memento: LKML-style review means reviewers object and the author reworks; v2 is
same-branch with a Changes-since body. Review records are append-only. Accepted
RFCs receive serial tags on direction-ok. AI Org adds a bounded-rounds backstop,
which diverges from kernel practice to keep the RFC phase from running forever.
"""
from __future__ import annotations

import json
import inspect
from pathlib import Path
import shutil
from typing import Any, Mapping

import ai_org.log as org_log
from ai_org import git_wrapper
from ai_org.rfc import decompose as decomposition
from ai_org.rfc import lineage
from ai_org.rfc import receive
from ai_org.rfc import review
from ai_org.rfc import submit as submission


RFC_PREFIX = "ai-org/rfc/"


def decompose(repo, rfc_id_or_branch: str, **kwargs):
    """Deprecated: use refine for new RFC lineage splits."""
    return decomposition.decompose(repo, rfc_id_or_branch, **kwargs)


def refine(repo, rfc_id_or_branch: str, **kwargs):
    """Split an oversized direction-ok RFC into lineage child RFC branches."""
    return lineage.refine(repo, rfc_id_or_branch, **kwargs)


def pull(repo, *, progress_path: str | Path | None = None):
    """Process one inbox request, author revision, review, or lineage item."""
    ctx = org_log.RunContext(repo=repo, stage="rfc.pull")
    intake_result = _pull_inbox(repo, progress_path=progress_path, ctx=ctx)
    if intake_result is not None:
        return intake_result

    for branch in sorted(git_wrapper.branches(repo, f"{RFC_PREFIX}*")):
        if _is_terminal_rfc(repo, branch):
            continue
        if _latest_needs_revision_is_head(repo, branch) and _has_review_round_record(repo, branch):
            org_log.emit("rfc.pull.item_selected", {"kind": "author_revision", "branch": branch}, ctx=ctx)
            return receive.reform_rfc(repo, branch.removeprefix(RFC_PREFIX))

    for branch in sorted(git_wrapper.branches(repo, f"{RFC_PREFIX}*")):
        if _is_terminal_rfc(repo, branch):
            continue
        if _latest_needs_revision_is_head(repo, branch):
            continue
        org_log.emit("rfc.pull.item_selected", {"kind": "review", "branch": branch}, ctx=ctx)
        return review.run_rfc_review(repo, branch.removeprefix(RFC_PREFIX))

    for branch in sorted(git_wrapper.branches(repo, f"{RFC_PREFIX}*")):
        if lineage.split_pending(repo, branch):
            org_log.emit("rfc.pull.item_selected", {"kind": "lineage_refine", "branch": branch}, ctx=ctx)
            return lineage.refine(repo, branch)

    for branch in sorted(git_wrapper.branches(repo, f"{RFC_PREFIX}*")):
        if lineage.rebaseline_pending(repo, branch):
            org_log.emit("rfc.pull.item_selected", {"kind": "lineage_rebaseline", "branch": branch}, ctx=ctx)
            return lineage.rebaseline(repo, branch)

    for branch in sorted(git_wrapper.branches(repo, f"{RFC_PREFIX}*")):
        if lineage.stale_revalidation_pending(repo, branch):
            org_log.emit("rfc.pull.item_selected", {"kind": "lineage_revalidate", "branch": branch}, ctx=ctx)
            return lineage.revalidate_stale(repo, branch)

    for branch in sorted(git_wrapper.branches(repo, f"{RFC_PREFIX}*")):
        if lineage.coarse_ready(repo, branch):
            org_log.emit("rfc.pull.item_selected", {"kind": "lineage_elaborate", "branch": branch}, ctx=ctx)
            return lineage.elaborate(repo, branch)
    return None


def _is_terminal_rfc(repo, branch: str) -> bool:
    if git_wrapper.has_subject(repo, branch, "rfc: nak"):
        return True
    default = git_wrapper.default_branch(repo)
    if branch != default and git_wrapper.is_ancestor(repo, branch, default):
        return True
    for subject in git_wrapper.log_subjects(repo, branch):
        if "rfc: needs-revision round " in subject:
            return False
        if subject.startswith("rfc v"):
            return False
        if "rfc: direction-ok" in subject:
            return True
    return False


def _latest_needs_revision_is_head(repo, branch: str) -> bool:
    subjects = git_wrapper.log_subjects(repo, branch)
    for index, subject in enumerate(subjects):
        if "rfc: direction-ok" in subject or "rfc: nak" in subject:
            return False
        if "rfc: needs-revision round " in subject:
            return index == 0
    return False


def _has_review_round_record(repo, branch: str) -> bool:
    rfc_id = branch.removeprefix(RFC_PREFIX)
    return any((Path(repo) / ".ai-org" / "review" / rfc_id).glob("round-*.json"))


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
            "rfc_pull.inbox_dequeued",
            {"inbox_id": envelope["id"], "path": str(path)},
            ctx=ctx.child(request_id=str(envelope["id"])),
        )
    result = _call_intake(
        request,
        repo,
        progress_path=progress_path,
        ctx=ctx.child(request_id=str(envelope["id"])) if ctx is not None else None,
    )
    if ctx is not None:
        org_log.emit(
            "intake.completed",
            {"inbox_id": envelope["id"], "status": result.get("status"), "ok": result.get("ok")},
            ctx=ctx.child(request_id=str(envelope["id"])),
        )
    _mark_processed(inbox, path, envelope, result)
    return result


def _call_intake(
    request: Mapping[str, Any],
    repo,
    *,
    progress_path: str | Path | None,
    ctx: org_log.RunContext | None,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {"progress_path": progress_path}
    try:
        parameters = inspect.signature(receive.intake).parameters
    except (TypeError, ValueError):
        parameters = {}
    if "ctx" in parameters:
        kwargs["ctx"] = ctx
    return receive.intake(request, repo, **kwargs)


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


def _result_record(result: Mapping[str, Any]) -> dict[str, Any]:
    status = str(result.get("status", "unknown"))
    record: dict[str, Any] = {"status": status}
    if status == "promoted":
        if isinstance(result.get("branch"), str):
            record["rfc_branch"] = result["branch"]
        if isinstance(result.get("id"), str):
            record["rfc_id"] = result["id"]
    for field in ("error", "failed_step", "violations"):
        if field in result:
            record[field] = result[field]
    record["intake_result"] = dict(result)
    return record
