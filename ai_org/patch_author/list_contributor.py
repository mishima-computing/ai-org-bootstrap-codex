"""Mailing-list entry point for the first list-reading patch contributor.

Memento: the minimal ring is gate offer -> take -> announce ->
implement.  This module deliberately stops after announce; the caller decides
when implementation starts.  Taking is visible competition, never a lock or a
reservation, so another family's take cannot suppress this reader's take.
"""
from __future__ import annotations

from pathlib import Path
import re
from typing import Any, Mapping

from ai_org import git_wrapper, mailing_list
from ai_org.patch_author import announcements


_REMOTE_URL_RE = re.compile(
    r"(?:[a-z][a-z0-9+.-]*://)|(?:^[^/\\:]+(?:@[^/\\:]+)?:)",
    re.IGNORECASE,
)
_DEFAULT_CONFIDENCE = "unscored"


def take_open_offers(author_repo, *, reader="patch_author", ctx=None) -> dict:
    """Take fresh origin-list offers and publish this family's announcements.

    The origin must be a directly accessible local repository because list
    posts and reader cursors are custom refs outside the author clone's normal
    fetch/push refspecs.  Failures are returned as typed data.
    """
    repo_path = Path(author_repo).resolve()
    origin = _local_origin_repository(repo_path)
    if not origin.get("ok"):
        return _failure_result(origin)

    origin_repo = origin["path"]
    identity = git_wrapper.engine_identity()
    normalized_identity = announcements.normalize_noreply_identity(
        identity["name"], identity["email"]
    )
    if normalized_identity is None:
        return _failure_result(
            {
                "status": "author_identity_invalid",
                "failure": {
                    "type": "author_identity_invalid",
                    "detail": "the repository default identity must be a noreply identity",
                },
            }
        )
    family = announcements.author_slug(normalized_identity["email"])
    if not family:
        return _failure_result(
            {
                "status": "author_slug_empty",
                "failure": {
                    "type": "author_slug_empty",
                    "detail": "the repository default identity does not form an author slug",
                },
            }
        )

    try:
        after = mailing_list.cursor(origin_repo, reader)
        offers = mailing_list.read(origin_repo, since=after, kinds={"offer"})
    except Exception as exc:  # noqa: BLE001 - list reads must return typed failures.
        return _failure_result(
            {
                "status": "list_read_failed",
                "failure": {"type": "list_read_failed", "detail": str(exc)},
            }
        )

    result = {"ok": True, "taken": [], "announced_refs": [], "skipped": []}
    for offer in offers:
        parsed = _offer_address(offer)
        if not parsed.get("ok"):
            result["ok"] = False
            result["skipped"].append(parsed["failure"])
            break
        series = parsed["series"]
        node = parsed["node"]
        address = parsed["address"]
        try:
            take = mailing_list.post(
                origin_repo,
                kind="take",
                subject=address,
                refs={
                    "node": node,
                    "series": series,
                    "family": family,
                    # Confidence is durable evidence only; it is not an admission
                    # or arbitration signal in this no-lock form.
                    "confidence": _DEFAULT_CONFIDENCE,
                },
                ctx=ctx,
            )
        except Exception as exc:  # noqa: BLE001 - one failed offer stays unread.
            take = {"ok": False, "error": str(exc)}
        if not take.get("ok"):
            result["ok"] = False
            result["skipped"].append(
                {
                    "offer": offer["commit"],
                    "address": address,
                    "reason": "take_post_failed",
                    "detail": str(take.get("error") or "could not post take"),
                }
            )
            break

        try:
            announced = announcements.announce(
                repo_path,
                "origin",
                series,
                node_path=node,
                author_name=normalized_identity["name"],
                author_email=normalized_identity["email"],
            )
        except Exception as exc:  # noqa: BLE001 - one failed offer stays unread.
            announced = {"ok": False, "status": "announce_failed", "detail": str(exc)}
        if not announced.get("ok"):
            result["ok"] = False
            result["skipped"].append(
                {
                    "offer": offer["commit"],
                    "address": address,
                    "reason": "announce_failed",
                    "detail": str(announced.get("status") or "could not announce"),
                }
            )
            break

        try:
            advanced = mailing_list.advance_cursor(origin_repo, reader, offer["commit"])
        except Exception as exc:  # noqa: BLE001 - cursor failures are typed.
            advanced = {"ok": False, "error": str(exc)}
        if not advanced.get("ok"):
            result["ok"] = False
            result["skipped"].append(
                {
                    "offer": offer["commit"],
                    "address": address,
                    "reason": "cursor_advance_failed",
                    "detail": str(advanced.get("error") or "could not advance cursor"),
                }
            )
            break

        result["taken"].append(address)
        result["announced_refs"].append(announced["ref"])
    return result


def _local_origin_repository(author_repo: Path) -> dict[str, Any]:
    configured = git_wrapper._git(  # noqa: SLF001 - Git config is read through the engine gateway.
        author_repo, "config", "--get", "remote.origin.url"
    )
    if configured.returncode != 0 or not configured.stdout.strip():
        return {
            "ok": False,
            "status": "origin_not_configured",
            "failure": {
                "type": "origin_not_configured",
                "detail": "the author repository has no origin remote",
            },
        }
    remote = configured.stdout.strip()
    if "\x00" in remote or _REMOTE_URL_RE.search(remote):
        return {
            "ok": False,
            "status": "origin_not_local_path",
            "failure": {
                "type": "origin_not_local_path",
                "detail": "the origin remote must be a local filesystem path",
            },
        }

    path = Path(remote).expanduser()
    if not path.is_absolute():
        path = author_repo / path
    path = path.resolve()
    probed = git_wrapper._git(  # noqa: SLF001 - repository validation through the engine gateway.
        path, "rev-parse", "--git-dir"
    )
    if probed.returncode != 0:
        return {
            "ok": False,
            "status": "origin_repository_invalid",
            "failure": {
                "type": "origin_repository_invalid",
                "detail": "the origin path is not an accessible Git repository",
            },
        }
    return {"ok": True, "path": path}


def _offer_address(offer: Mapping[str, Any]) -> dict[str, Any]:
    refs = offer.get("refs")
    series = refs.get("series") if isinstance(refs, Mapping) else None
    node = refs.get("node") if isinstance(refs, Mapping) else None
    if (
        not isinstance(series, str)
        or not series.startswith(announcements.SERIES_BRANCH_PREFIX)
        or not isinstance(node, str)
        or not (node == "." or re.fullmatch(r"sub/[a-z0-9_]+", node))
    ):
        return {
            "ok": False,
            "failure": {
                "offer": str(offer.get("commit") or ""),
                "address": str(offer.get("subject") or ""),
                "reason": "offer_refs_invalid",
            },
        }
    address = series if node == "." else f"{series}:{node}"
    if offer.get("subject") != address:
        return {
            "ok": False,
            "failure": {
                "offer": str(offer.get("commit") or ""),
                "address": address,
                "reason": "offer_subject_invalid",
            },
        }
    return {"ok": True, "series": series, "node": node, "address": address}


def _failure_result(failure: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "ok": False,
        "status": failure["status"],
        "failure": dict(failure["failure"]),
        "taken": [],
        "announced_refs": [],
        "skipped": [],
    }
