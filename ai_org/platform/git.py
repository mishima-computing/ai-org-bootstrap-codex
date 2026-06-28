"""Small host-agnostic Git helpers."""
from __future__ import annotations

import logging
import os
from pathlib import Path
import re
import subprocess
from typing import Any

log = logging.getLogger(__name__)

_ZERO_OID = "0" * 40


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


def update_ref(repo: str | Path, ref: str, target: str) -> None:
    run(Path(repo), "update-ref", ref, target)


def update_ref_cas(repo: str | Path, ref: str, new_oid: str, expected_old_oid: str | None) -> bool:
    old = expected_old_oid or _ZERO_OID
    result = run(Path(repo), "update-ref", ref, new_oid, old, check=False)
    return result.returncode == 0


def ref_exists(repo: str | Path, ref: str) -> bool:
    result = run(Path(repo), "show-ref", "--verify", "--quiet", ref, check=False)
    return result.returncode == 0


def mainline_contains_all(repo: str | Path, refs: list[str], mainline_ref: str) -> bool:
    repo_path = Path(repo)
    if not refs or not ref_exists(repo_path, mainline_ref):
        return False
    for ref in refs:
        result = run(repo_path, "merge-base", "--is-ancestor", ref, mainline_ref, check=False)
        if result.returncode != 0:
            return False
    return True


def ref_component(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip(".-/")
    return cleaned or "patch"


def run(
    repo: Path,
    *args: str,
    input: str | None = None,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        input=input,
        check=check,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )


def _selected_remote(remote: str | None) -> str | None:
    selected = remote if remote is not None else os.environ.get("AI_ORG_REMOTE", "origin")
    selected = selected.strip()
    return selected or None
