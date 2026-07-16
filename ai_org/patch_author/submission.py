"""Submission: publish the contribution branch, entering the submitted projection.

`submitted_for_maintainer_review` is never written anywhere: it is the
projection "an announced author's contribution branch exists on the canonical
remote", entered by the create-only push below with zero state writes. The
patch author never pushes acceptance markers — self-issued acceptance is the
consumer-fabrication hole the canonical-side judge exists to close.

No-lock law: nothing here checks other families. The only fail-closed cases
are about THIS author's own output: identity chain-of-custody (the published
branch's commits must carry the announced author identity) and the
public-history mutability boundary (never rewrite an already-published
branch). A create-only rejection means another family raced this exact branch
NAME — resolution is picking a different name (re-announce), never contesting
theirs.
"""
from __future__ import annotations

from typing import Any

from ai_org import git_wrapper
from ai_org.patch_author import announcements, producer_lifecycle


def submit(repo, announcement_ref: str, *, remote: str = "origin") -> dict[str, Any]:
    """Push the announced contribution branch create-only; typed fail-closed result."""
    # A stale clone may not contain a compatibility alias created by another
    # process. Submission is therefore grounded in the remote canonical/legacy
    # pair so coexistence cannot release an announcement body.
    evaluation = announcements.evaluate_remote(repo, remote, announcement_ref)
    if evaluation["state"] != "authoring":
        return {"ok": False, "status": "announcement_unusable", "ref": announcement_ref, "evaluation": evaluation}
    record = evaluation["record"]

    contrib_branch = record["contrib_branch"]
    head = git_wrapper.head_sha(repo, contrib_branch)
    if head is None:
        return {"ok": False, "status": "contrib_branch_missing", "branch": contrib_branch}
    if producer_lifecycle.is_promise_only_contribution(repo, contrib_branch):
        return {"ok": False, "status": "implementation_missing", "branch": contrib_branch}
    author = git_wrapper.commit_author(repo, contrib_branch)
    if not announcements.contribution_author_matches(repo, contrib_branch, record):
        # The announcement's author must match the published branch's author:
        # the deterministic chain-of-custody check the design puts at submission.
        return {"ok": False, "status": "author_identity_mismatch", "branch": contrib_branch, "commit_author": author, "announced_author": record["author"]}

    remote_ref = f"refs/heads/{contrib_branch}"
    listed = git_wrapper.ls_remote(repo, remote, remote_ref)
    if not listed.get("ok"):
        return {"ok": False, "status": "remote_unavailable", "detail": listed.get("detail", "")}
    remote_sha = listed["refs"].get(remote_ref, "")
    if not remote_sha:
        pushed = git_wrapper.push_create(repo, remote, remote_ref, head)
        if pushed.get("ok"):
            return {"ok": True, "status": "submitted_for_maintainer_review", "branch": contrib_branch, "commit": head}
        # A rejected create means another family published this exact branch
        # name first (a naming race the announcement visibility did not catch).
        # Fail closed; the caller re-announces to pick a fresh suffixed name.
        status = "branch_name_taken" if pushed.get("status") == "rejected" else "push_failed"
        return {"ok": False, "status": status, "branch": contrib_branch, "detail": pushed.get("detail", "")}

    if remote_sha == head:
        return {"ok": True, "status": "already_submitted", "branch": contrib_branch, "commit": head}
    fetched = git_wrapper.fetch_refspecs(repo, remote, [f"+{remote_ref}:refs/remotes/{remote}/{contrib_branch}"])
    if not fetched.get("ok"):
        return {"ok": False, "status": "remote_unavailable", "detail": fetched.get("detail", "")}
    if not git_wrapper.is_ancestor(repo, remote_sha, contrib_branch):
        remote_author = git_wrapper.commit_author(repo, f"refs/remotes/{remote}/{contrib_branch}")
        if remote_author and not announcements.contribution_author_matches(
            repo, f"refs/remotes/{remote}/{contrib_branch}", record
        ):
            # Another family won the NAME (a naming race the announcement
            # visibility did not catch). Their published branch is never
            # contested or rewritten; this author re-announces for a fresh
            # suffixed name. Competition on content stays; arbitration does not.
            return {"ok": False, "status": "branch_name_taken", "branch": contrib_branch, "remote_commit": remote_sha, "remote_author": remote_author}
        # Never rewrite a published contribution branch (public-history
        # mutability boundary); a divergent local branch is a protocol error.
        return {"ok": False, "status": "non_fast_forward", "branch": contrib_branch, "remote_commit": remote_sha, "local_commit": head}
    pushed = git_wrapper.push_cas(repo, remote, remote_ref, head, remote_sha)
    if pushed.get("ok"):
        return {"ok": True, "status": "resubmitted_for_maintainer_review", "branch": contrib_branch, "commit": head}
    status = "branch_name_taken" if pushed.get("status") == "rejected" else "push_failed"
    return {"ok": False, "status": status, "branch": contrib_branch, "detail": pushed.get("detail", "")}
