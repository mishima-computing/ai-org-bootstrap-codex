"""Run python -m ai_org.patch_author <command> against a patch author clone.

CLI verbs [RATIFIED, brief 23 addendum]: list / announce-authoring-intent /
implement / submit / withdraw-authoring-intent. The retired reservation verbs
must not come back: 名前はプロンプト — verb names that read like reservations
prompt reservation semantics in every model that reads them.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from ai_org.patch_author import announcements, discovery, submission, work


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m ai_org.patch_author")
    parser.add_argument("--remote", default="origin")
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="Project open series from the canonical remote.")
    list_parser.add_argument("repo")

    announce_parser = subparsers.add_parser(
        "announce-authoring-intent",
        help="Announce authoring intent on this author's own visibility ref (never a lock).",
    )
    announce_parser.add_argument("repo")
    announce_parser.add_argument("series_branch")
    announce_parser.add_argument("--node-path", default=".")
    announce_parser.add_argument("--name", required=True)
    announce_parser.add_argument("--email", required=True)

    implement_parser = subparsers.add_parser("implement", help="Implement the announced address in this clone.")
    implement_parser.add_argument("repo")
    implement_parser.add_argument("announcement_ref")
    implement_parser.add_argument("--feedback", default=None)
    implement_parser.add_argument("--attempt", type=int, default=1)

    submit_parser = subparsers.add_parser("submit", help="Publish the contribution branch (create-only push).")
    submit_parser.add_argument("repo")
    submit_parser.add_argument("announcement_ref")

    withdraw_parser = subparsers.add_parser(
        "withdraw-authoring-intent",
        help="Withdraw authoring intent (announcement ref removal).",
    )
    withdraw_parser.add_argument("repo")
    withdraw_parser.add_argument("announcement_ref")
    withdraw_parser.add_argument("--email", required=True)

    args = parser.parse_args(argv)
    result = _dispatch(args)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("ok") else 1


def _dispatch(args: argparse.Namespace) -> dict[str, Any]:
    if args.command == "list":
        return discovery.list_open(args.repo, args.remote)
    if args.command == "announce-authoring-intent":
        return announcements.announce(
            args.repo,
            args.remote,
            args.series_branch,
            node_path=args.node_path,
            author_name=args.name,
            author_email=args.email,
        )
    if args.command == "implement":
        return work.implement_announced(
            args.repo,
            args.announcement_ref,
            remote=args.remote,
            feedback=args.feedback,
            attempt=args.attempt,
        )
    if args.command == "submit":
        return submission.submit(args.repo, args.announcement_ref, remote=args.remote)
    if args.command == "withdraw-authoring-intent":
        return announcements.withdraw(args.repo, args.remote, args.announcement_ref, author_email=args.email)
    return {"ok": False, "status": "unknown_command", "command": args.command}


if __name__ == "__main__":
    sys.exit(main())
