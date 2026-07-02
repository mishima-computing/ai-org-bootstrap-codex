"""Patch stage: produce an implemented AND accepted contribution branch.

The patch stage contains two parts with DISTINCT actors:
  - Implement  : the Contributor writes the code (the only code author/fixer).
  - Acceptance : an INDEPENDENT goal-reachability check.

The implement<->acceptance revise loop is INTERNAL: acceptance fail -> the
Contributor re-implements -> re-check, bounded by cap.

UNRESOLVED — rebase & resend (deliberately deferred; recorded here so it isn't forgotten):
  When a contribution conflicts at merge (the subsystem/mainline moved under it), TWO things are mixed and
  must be kept SEPARATE per our core rule "only the Contributor fixes code; everyone else judges":
    - JUDGMENT  (conflict detected -> send it back; and whether the rebased result is sound): git detects the
      conflict mechanically; deciding to send back is the maintainer's call; SUSPECTING the resolution (a
      rebase compiles + passes tests yet is semantically wrong) is an adversarial-review call = LINON's role
      (not yet placed in this build).
    - FIXING    (actually rebasing onto the moved base and resolving the overlap): this is the CONTRIBUTOR's
      job and belongs HERE — re-do the contribution against the CURRENT subsystem tip (reuse make/implement
      with base = the moved target), bounded by a cap.
  STATUS: not built. Today a conflicting contribution is cleanly rejected (merge worktree discarded, ref
  untouched — see merge/__init__) but is then re-selected + re-rejected by merge.pull with no progress.
  TODO: on conflict, mark the contribution stale and have the Contributor rebase-and-resend (fix), with the
  judgment of the result kept separate (maintainer / Linon).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from ai_org import git_wrapper

from . import functional_check, implement


RFC_PREFIX = "ai-org/rfc/"
CONTRIB_PREFIX = "ai-org/contrib/"


def make(repo, rfc_id_or_branch: str, rfc_path: str = "rfc.json", cap: int = 3) -> dict:
    """Produce an accepted contribution branch, retrying rejected attempts."""
    feedback = None
    branch = None
    verdict = None
    for attempt in range(1, cap + 1):
        result = implement.run(repo, rfc_id_or_branch, rfc_path=rfc_path, feedback=feedback, attempt=attempt)
        branch = result["branch"]
        verdict = functional_check.check(repo, branch)
        if verdict["ok"]:
            return {"ok": True, "branch": branch, "verdict": verdict, "attempts": attempt}
        feedback = verdict["blockers"]
    return {"ok": False, "branch": branch, "verdict": verdict, "attempts": cap}


def pull(repo):
    """Implement one direction-ok RFC that has no contribution branch yet."""
    for branch in sorted(git_wrapper.branches(repo, f"{RFC_PREFIX}*")):
        if not git_wrapper.has_subject(repo, branch, "rfc: direction-ok"):
            continue
        if _lineage_blocked(repo, branch):
            continue
        rfc_id = branch.removeprefix(RFC_PREFIX)
        if git_wrapper.branch_exists(repo, f"{CONTRIB_PREFIX}{rfc_id}"):
            continue
        return make(repo, rfc_id)
    return None


def _lineage_blocked(repo, branch: str) -> bool:
    metadata = _read_metadata(Path(repo), branch)
    status = str(metadata.get("lifecycle_status", ""))
    return status == "stale" or status.startswith("blocked:")


def _read_metadata(repo: Path, branch: str) -> dict[str, Any]:
    raw = git_wrapper.show_file(repo, branch, "rfc-metadata.json")
    if raw is None:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return dict(parsed) if isinstance(parsed, Mapping) else {}
