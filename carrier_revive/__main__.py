"""CLI entry point: python -m carrier_revive {list,show,revive}."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime

from . import scanner
from .procs import find_live_matches, ps_snapshot
from .revive import DEFAULT_PROMPT, ReviveError, run_revive


def _human_age(then: datetime | None, now: datetime | None = None) -> str:
    if then is None:
        return "?"
    now = now or datetime.now()
    secs = max(0, int((now - then).total_seconds()))
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs // 60}m"
    if secs < 86400:
        return f"{secs // 3600}h{(secs % 3600) // 60:02d}m"
    return f"{secs // 86400}d{(secs % 86400) // 3600}h"


def _snippet(text: str, width: int = 80) -> str:
    flat = " ".join(text.split())
    return flat[:width] + ("…" if len(flat) > width else "")


def cmd_list(args: argparse.Namespace) -> int:
    sessions = scanner.scan_sessions(days=args.days)
    if not sessions:
        print(f"No sessions found in the last {args.days} day(s).")
        return 0
    entries = ps_snapshot()  # one snapshot for all rows
    header = f"{'UUID':<9} {'START':<16} {'AGE':<7} {'CWD':<3} {'LIVE':<4} {'WT':<2} PROMPT | CWD"
    print(header)
    print("-" * len(header))
    for s in sessions:
        live = find_live_matches(s.uuid, s.cwd, entries=entries)
        start = s.start.strftime("%Y-%m-%d %H:%M") if s.start else "?"
        row = (
            f"{s.short_uuid:<9} {start:<16} {_human_age(s.last_activity):<7} "
            f"{'ok' if s.cwd_exists else 'GONE':<3} "
            f"{'yes' if live else '-':<4} "
            f"{'wt' if s.is_preserved_worktree else '-':<2} "
            f"{_snippet(s.first_prompt, 80)} | {s.cwd}"
        )
        print(row)
    return 0


def _resolve_or_exit(prefix: str, days: int) -> scanner.Session:
    sessions = scanner.scan_sessions(days=days)
    session, matches = scanner.resolve_session(sessions, prefix)
    if session is not None:
        return session
    if not matches:
        print(f"error: no session matches uuid prefix {prefix!r} "
              f"in the last {days} day(s); try --days", file=sys.stderr)
        raise SystemExit(2)
    print(f"error: uuid prefix {prefix!r} is ambiguous ({len(matches)} matches):",
          file=sys.stderr)
    for m in matches:
        print(f"  {m.uuid}  {m.cwd}", file=sys.stderr)
    raise SystemExit(2)


def cmd_show(args: argparse.Namespace) -> int:
    s = _resolve_or_exit(args.uuid_prefix, args.days)
    detail = scanner.analyze_session(s.path, tail_events=args.tail)
    live = find_live_matches(s.uuid, s.cwd)
    print(f"uuid:            {s.uuid}")
    print(f"rollout:         {s.path}")
    print(f"started:         {s.start}")
    print(f"last activity:   {s.last_activity} ({_human_age(s.last_activity)} ago)")
    print(f"cwd:             {s.cwd}  [{'exists' if s.cwd_exists else 'MISSING'}]")
    print(f"preserved wt:    {'yes' if s.is_preserved_worktree else 'no'}")
    print(f"live:            {'YES' if live else 'no'}")
    for entry, reason in live:
        print(f"  pid {entry.pid} ({reason}): {entry.command[:120]}")
    print(f"apply_patch:     {detail['apply_patch_count']} call(s)")
    print(f"total events:    {detail['total_events']}")
    print(f"last {args.tail} events:  {', '.join(detail['last_event_types'])}")
    print(f"first prompt:    {_snippet(s.first_prompt, 200)}")
    print(f"last agent msg:  {_snippet(detail['last_agent_message'], 400)}")
    return 0


def cmd_revive(args: argparse.Namespace) -> int:
    # Gate (a): unique resolution.
    if args.last:
        sessions = scanner.scan_sessions(days=args.days)
        if not sessions:
            print("error: no sessions found; try --days", file=sys.stderr)
            return 2
        session = sessions[0]
    elif args.uuid_prefix:
        session = _resolve_or_exit(args.uuid_prefix, args.days)
    else:
        print("error: give a uuid prefix or --last", file=sys.stderr)
        return 2
    # Gate (b) input: real liveness matches (tests inject their own).
    live = find_live_matches(session.uuid, session.cwd)
    try:
        return run_revive(
            session,
            prompt=args.prompt,
            dry_run=args.dry_run,
            detach=args.detach,
            live_matches=live,
        )
    except ReviveError as e:
        print(str(e), file=sys.stderr)
        return e.code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m carrier_revive",
        description="Revive dead Codex carrier sessions against their work trees.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="list recent sessions, newest first")
    p_list.add_argument("--days", type=int, default=2, help="lookback window (default 2)")
    p_list.set_defaults(func=cmd_list)

    p_show = sub.add_parser("show", help="show one session's detail")
    p_show.add_argument("uuid_prefix")
    p_show.add_argument("--days", type=int, default=2)
    p_show.add_argument("--tail", type=int, default=10, help="last N event types")
    p_show.set_defaults(func=cmd_show)

    p_rev = sub.add_parser("revive", help="resume a dead session in its recorded cwd")
    p_rev.add_argument("uuid_prefix", nargs="?", default=None)
    p_rev.add_argument("--last", action="store_true",
                       help="pick the newest scanned session (still gated on liveness)")
    p_rev.add_argument("--days", type=int, default=2)
    p_rev.add_argument("--prompt", default=DEFAULT_PROMPT)
    p_rev.add_argument("--dry-run", action="store_true",
                       help="print the exact command without running it")
    p_rev.add_argument("--detach", action="store_true",
                       help="nohup-detach with a log file under the target cwd")
    p_rev.set_defaults(func=cmd_revive)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
