"""Thin CLI over the judge library. Nothing lives only here.

    python -m judge trace <vessel> <branch> <body-path>
        [--node <json-path>] [--term <text>] [--at-commit <sha>]
        [--days N] [--json] [--sessions-root PATH]
    python -m judge calls <vessel> <run-dir-or-latest>
        [--json] [--no-sessions] [--days N] [--sessions-root PATH]
"""

from __future__ import annotations

import argparse
import json
import sys

from . import calls as calls_api
from . import trace as trace_api
from .report import render, render_calls_table


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="judge",
        description=("Deterministic provenance judge: which Codex session "
                     "wrote a committed body text, when, with what recorded "
                     "thinking. Read-only; no LLM."),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_trace = sub.add_parser("trace", help="full provenance chain")
    p_trace.add_argument("vessel")
    p_trace.add_argument("branch")
    p_trace.add_argument("body_path")
    p_trace.add_argument("--node", help="json-path like /problem/constraints/0")
    p_trace.add_argument("--term", help="search text; chain runs on its first occurrence")
    p_trace.add_argument("--at-commit", dest="at_commit", default=None,
                         help="origin mode: resolve the body/target AS OF "
                              "this commit and trace the commit that "
                              "introduced that text (works for since-"
                              "deleted text)")
    p_trace.add_argument("--days", type=int, default=3,
                         help="fallback session-scan window (days)")
    p_trace.add_argument("--json", action="store_true", dest="as_json")
    p_trace.add_argument("--sessions-root", default=None)

    p_calls = sub.add_parser("calls", help="list a run's carrier calls")
    p_calls.add_argument("vessel")
    p_calls.add_argument("run", help="run dir or 'latest'")
    p_calls.add_argument("--json", action="store_true", dest="as_json")
    p_calls.add_argument("--no-sessions", action="store_true",
                         help="skip session matching (faster)")
    p_calls.add_argument("--days", type=int, default=3)
    p_calls.add_argument("--sessions-root", default=None)

    args = parser.parse_args(argv)

    if args.command == "trace":
        report = trace_api(
            args.vessel, args.branch, args.body_path,
            node=args.node, term=args.term,
            sessions_root=args.sessions_root, fallback_days=args.days,
            at_commit=args.at_commit)
        if args.as_json:
            print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
        else:
            print(render(report))
        return 0 if not report.broken_link else 1

    if args.command == "calls":
        try:
            rows = calls_api(
                args.vessel, args.run,
                sessions_root=args.sessions_root,
                match_sessions=not args.no_sessions,
                fallback_days=args.days)
        except FileNotFoundError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        if args.as_json:
            print(json.dumps([c.to_dict() for c in rows],
                             ensure_ascii=False, indent=2))
        else:
            print(render_calls_table(rows))
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())
