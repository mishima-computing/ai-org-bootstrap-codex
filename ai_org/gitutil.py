"""Small host-agnostic Git helpers."""
from __future__ import annotations

import logging
import os
from pathlib import Path
import subprocess
from typing import Any

log = logging.getLogger(__name__)


def push_ref(repo: str | Path, ref: str, remote: str | None = None) -> dict[str, Any]:
    """Best-effort push of ``ref`` to a configured Git remote.

    Remote resolution is explicit arg, then ``AI_ORG_REMOTE``, then ``origin``.
    Missing remote configuration is a local-only development mode and is
    reported as a graceful skip.
    """
    repo = Path(repo)
    selected_remote = _selected_remote(remote)
    if not selected_remote:
        result = {"status": "skipped", "reason": "no remote", "remote": None, "ref": ref}
        log.info("skipping push of %s: no remote configured", ref)
        return result

    remote_check = subprocess.run(
        ["git", "-C", str(repo), "remote", "get-url", selected_remote],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if remote_check.returncode != 0:
        result = {"status": "skipped", "reason": "no remote", "remote": selected_remote, "ref": ref}
        log.info("skipping push of %s: remote %s does not exist", ref, selected_remote)
        return result

    push = subprocess.run(
        ["git", "-C", str(repo), "push", selected_remote, ref],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    result = {
        "status": "pushed" if push.returncode == 0 else "failed",
        "remote": selected_remote,
        "ref": ref,
        "returncode": push.returncode,
        "stdout": push.stdout,
        "stderr": push.stderr,
    }
    if push.returncode == 0:
        log.info("pushed %s to %s", ref, selected_remote)
    else:
        log.warning("failed to push %s to %s: %s", ref, selected_remote, push.stderr.strip())
    return result


def _selected_remote(remote: str | None) -> str | None:
    selected = remote if remote is not None else os.environ.get("AI_ORG_REMOTE", "origin")
    selected = selected.strip()
    return selected or None
