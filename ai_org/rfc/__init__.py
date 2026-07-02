"""RFC phase pull entry: process off-git inbox requests, then review RFCs.

Lifecycle:
1. Requesters run ``python -m ai_org.rfc.submit <repo> <request>``.
2. ``submit`` writes the raw request to the off-git inbox
   ``<repo>/.ai-org/inbox`` or ``AI_ORG_INBOX``.
3. ``pull`` takes one unprocessed inbox item first and runs the receive gate.
4. Only a promoted RFC becomes a git branch at ``ai-org/rfc/<id>``. Requests
   that need work or are rejected stay as processed inbox records.
"""
from __future__ import annotations

import json
from pathlib import Path
import shutil
from typing import Any, Mapping

from ai_org import git_wrapper
from ai_org.rfc import decompose as decomposition
from ai_org.rfc import receive
from ai_org.rfc import review
from ai_org.rfc import submit as submission


RFC_PREFIX = "ai-org/rfc/"


def decompose(repo, rfc_id_or_branch: str, **kwargs):
    """Decompose an oversized RFC branch into child RFC branches."""
    return decomposition.decompose(repo, rfc_id_or_branch, **kwargs)


def pull(repo, *, progress_path: str | Path | None = None):
    """Process one inbox request, or review one proposed RFC branch."""
    intake_result = _pull_inbox(repo, progress_path=progress_path)
    if intake_result is not None:
        return intake_result

    for branch in sorted(git_wrapper.branches(repo, f"{RFC_PREFIX}*")):
        if git_wrapper.has_subject(repo, branch, "rfc: direction-ok"):
            continue
        if git_wrapper.has_subject(repo, branch, "rfc: nak"):
            continue
        return review.run_rfc_review(repo, branch.removeprefix(RFC_PREFIX))
    return None


def _pull_inbox(repo, *, progress_path: str | Path | None = None) -> dict[str, Any] | None:
    inbox = submission.inbox_dir(repo)
    if not inbox.exists():
        return None
    files = _unprocessed_inbox_files(inbox)
    if not files:
        return None

    path = files[0]
    envelope = _read_inbox_envelope(path)
    request = envelope["request"]
    result = receive.intake(request, repo, progress_path=progress_path)
    _mark_processed(inbox, path, envelope, result)
    return result


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
