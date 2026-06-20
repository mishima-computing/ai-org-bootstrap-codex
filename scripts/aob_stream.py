#!/usr/bin/env python3
import argparse
import json
import sys
from collections import deque


def non_negative_int(value):
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a non-negative integer")
    if number < 0:
        raise argparse.ArgumentTypeError("must be a non-negative integer")
    return number


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Filter JSONL stream events.")
    parser.add_argument("path", help="JSON-lines stream file, or '-' for stdin")
    parser.add_argument("--source", help="match the top-level source field exactly")
    parser.add_argument("--type", dest="event_type", help="match the top-level type field exactly")
    parser.add_argument("--last", type=non_negative_int, help="print only the final N matches")
    return parser.parse_args(argv)


def iter_events(lines):
    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            yield event


def event_matches(event, source=None, event_type=None):
    if source is not None and event.get("source") != source:
        return False
    if event_type is not None and event.get("type") != event_type:
        return False
    return True


def emit_events(lines, source=None, event_type=None, last=None, output=None):
    output = sys.stdout if output is None else output

    if last is None:
        for event in iter_events(lines):
            if event_matches(event, source, event_type):
                print(json.dumps(event, separators=(",", ":")), file=output)
        return

    retained = deque(maxlen=last)
    for event in iter_events(lines):
        if event_matches(event, source, event_type):
            retained.append(event)

    for event in retained:
        print(json.dumps(event, separators=(",", ":")), file=output)


def main(argv=None):
    args = parse_args(argv)

    if args.path == "-":
        try:
            emit_events(sys.stdin, args.source, args.event_type, args.last)
        except (OSError, UnicodeError) as exc:
            print(f"aob_stream.py: error: {exc}", file=sys.stderr)
            return 2
        return 0

    try:
        with open(args.path, "r", encoding="utf-8") as stream:
            emit_events(stream, args.source, args.event_type, args.last)
    except (OSError, UnicodeError) as exc:
        print(f"aob_stream.py: error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
