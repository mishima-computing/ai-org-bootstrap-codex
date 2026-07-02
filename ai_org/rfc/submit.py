"""Requester entrance for RFC submissions.

Requests are written to an off-git inbox at ``<repo>/.ai-org/inbox`` by default,
or ``AI_ORG_INBOX`` when set. Git stores only promoted RFC results; raw request
records stay in the inbox and are processed by ``ai_org.rfc.pull``.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Mapping, Sequence

import ai_org.rfc.receive as receive


INBOX_ENV = "AI_ORG_INBOX"
GITIGNORE_ENTRY = ".ai-org/"


def submit(repo: str | Path, request_source: str) -> dict[str, str]:
    """Validate and store a raw request in the off-git inbox."""
    repo_path = Path(repo).resolve()
    inbox = ensure_inbox(repo_path)
    request = parse_request(request_source)
    request = receive.receive(request)
    request_id = _next_id(inbox, _slug(str(request["raw_request"])))
    envelope = {
        "id": request_id,
        "submitted_at": datetime.now(timezone.utc).isoformat(),
        "request": request,
    }
    path = inbox / f"{request_id}.json"
    path.write_text(json.dumps(envelope, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"id": request_id, "path": str(path)}


def parse_request(source: str) -> dict[str, Any]:
    """Parse a JSON file path, JSON object string, or plain text request."""
    path = Path(source)
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Request JSON file {path} is invalid JSON: {exc}") from exc
        if not isinstance(loaded, dict):
            raise ValueError(f"Request JSON file {path} must contain a JSON object.")
        return loaded

    stripped = source.strip()
    if stripped.startswith("{"):
        try:
            loaded = json.loads(source)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Request JSON string is invalid JSON: {exc}") from exc
        if not isinstance(loaded, dict):
            raise ValueError("Request JSON string must contain a JSON object.")
        return loaded

    return {"raw_request": source}


def ensure_inbox(repo: str | Path) -> Path:
    """Create the inbox directories and protect the default inbox with .gitignore."""
    repo_path = Path(repo).resolve()
    inbox = inbox_dir(repo_path)
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / "processed").mkdir(parents=True, exist_ok=True)
    if INBOX_ENV not in os.environ:
        ensure_gitignore_entry(repo_path)
    return inbox


def inbox_dir(repo: str | Path) -> Path:
    """Return the off-git inbox path for repo."""
    override = os.environ.get(INBOX_ENV)
    if override:
        return Path(override).expanduser().resolve()
    return Path(repo).resolve() / ".ai-org" / "inbox"


def ensure_gitignore_entry(repo: str | Path) -> None:
    """Append .ai-org/ to .gitignore when the repo does not already ignore it."""
    repo_path = Path(repo).resolve()
    gitignore = repo_path / ".gitignore"
    text = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
    if _has_ai_org_ignore(text):
        return
    prefix = "" if not text or text.endswith("\n") else "\n"
    gitignore.write_text(f"{text}{prefix}{GITIGNORE_ENTRY}\n", encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m ai_org.rfc.submit",
        description="Submit a raw RFC request to the off-git AI Org inbox.",
    )
    parser.add_argument("repo", help="target repository")
    parser.add_argument("request", help="JSON file path, JSON object string, or plain text request")
    args = parser.parse_args(argv)
    try:
        result = submit(args.repo, args.request)
    except (OSError, TypeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"id: {result['id']}")
    print(f"path: {result['path']}")
    return 0


def _has_ai_org_ignore(text: str) -> bool:
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        normalized = stripped.lstrip("/").rstrip("/")
        if normalized == ".ai-org":
            return True
    return False


def _next_id(inbox: Path, slug: str) -> str:
    existing = {
        path.name.removesuffix(".json").removesuffix(".result")
        for path in [*inbox.glob("*.json"), *(inbox / "processed").glob("*.json")]
    }
    if slug not in existing:
        return slug
    index = 2
    while f"{slug}-{index}" in existing:
        index += 1
    return f"{slug}-{index}"


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return slug[:64].strip("-") or "request"


if __name__ == "__main__":
    raise SystemExit(main())
