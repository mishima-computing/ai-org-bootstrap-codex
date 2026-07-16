"""Own-clone implementation under an authoring announcement — the PARENT.

The author's OWN announcement record is the binding pointer for this family:
it names the series address, this author's contribution branch, and the
identity every patch commit must carry. Reading your own announcement is
intra-family coordination, not a gate: no other family's state is ever
consulted, and no one is refused work here (家族間重複=競争).

Parent/child structure (brief 23 addendum): THIS entry is the parent — the
patch_author process holding the author identity. It materializes the series,
then hands the committed patch plan to code_worker, whose children implement
disjoint item partitions under the parent's identity (children carry no
external identity of their own). Partitioning is the ONLY conflict prevention
and it is intra-family only.

Acceptance never runs here — it is canonical-side and independent: after
submission, `acceptance: blocked` markers on the canonical contribution branch
come back as feedback, and re-implementation appends fast-forward commits to
the same branch (code_worker.address_feedback).
"""
from __future__ import annotations

from typing import Any

from ai_org import git_wrapper
from ai_org.patch_author import announcements, code_worker


def implement_announced(
    repo,
    announcement_ref: str,
    *,
    remote: str = "origin",
    feedback: Any = None,
    attempt: int = 1,
    ctx: code_worker.org_log.RunContext | None = None,
) -> dict[str, Any]:
    """Implement the announced address in this clone; typed fail-closed result."""
    evaluation = announcements.evaluate(repo, announcement_ref)
    if evaluation["state"] != "authoring":
        return {
            "ok": False,
            "status": "announcement_unusable",
            "ref": announcement_ref,
            "evaluation": evaluation,
        }
    record = evaluation["record"]
    series_branch = record["series_branch"]
    series_ref = f"refs/remotes/{remote}/{series_branch}"
    if git_wrapper.head_sha(repo, series_ref) is None:
        series_ref = series_branch

    materialized = _materialize_local_series_branch(repo, series_branch, series_ref)
    if not materialized.get("ok"):
        return materialized

    if feedback is not None:
        # Cross-clone re-implement loop: acceptance blocked -> bounded reactive
        # repair appended fast-forward to the already-published branch.
        if ctx is None:
            return code_worker.address_feedback(repo, record, feedback=feedback, attempt=attempt)
        return code_worker.address_feedback(
            repo,
            record,
            feedback=feedback,
            attempt=attempt,
            ctx=ctx,
        )
    if ctx is None:
        return code_worker.drive(repo, record, attempt=attempt)
    return code_worker.drive(repo, record, attempt=attempt, ctx=ctx)


def _materialize_local_series_branch(repo, series_branch: str, series_ref: str) -> dict[str, Any]:
    """Point the local series branch at the fetched head so git-show reads resolve.

    The clone is private until publish, so force-moving the local series branch
    to the remote-tracking head is the P5-normal refresh, not history rewriting.
    """
    head = git_wrapper.head_sha(repo, series_ref)
    if head is None:
        return {"ok": False, "status": "series_missing", "series_branch": series_branch}
    if series_ref == series_branch:
        return {"ok": True}
    updated = git_wrapper.update_ref(repo, f"refs/heads/{series_branch}", head)
    if not updated.get("ok"):
        return {"ok": False, "status": "series_ref_update_failed", "detail": updated.get("detail", "")}
    return {"ok": True}
