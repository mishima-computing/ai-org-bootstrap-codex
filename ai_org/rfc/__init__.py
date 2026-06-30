"""RFC phase pull entry: find a groundable, reviewable item and act.

Re-exports pull for the RFC review stage.
"""
from __future__ import annotations

from ai_org import git_wrapper
from ai_org.rfc import review


RFC_PREFIX = "ai-org/rfc/"


def pull(repo):
    """Review one proposed RFC branch, if any is still pending."""
    for branch in sorted(git_wrapper.branches(repo, f"{RFC_PREFIX}*")):
        if git_wrapper.has_subject(repo, branch, "rfc: direction-ok"):
            continue
        if git_wrapper.has_subject(repo, branch, "rfc: nak"):
            continue
        return review.run_rfc_review(repo, branch.removeprefix(RFC_PREFIX))
    return None
