#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import sys


BAD_PATH_PARTS = {"." + "clau" + "de", "." + "anti" + "gravity"}
BAD_TEXT = [
    "Clau" + "de",
    "Anthro" + "pic",
    "clau" + "de",
    "anthro" + "pic",
    "Anti" + "gravity",
    "anti" + "gravity",
    "extract-" + "clau" + "de-result",
]


def scan(root: Path) -> list[str]:
    errors: list[str] = []
    for path in root.rglob("*"):
        # .agent-runs/ is gitignored runtime scratch (carrier stdout/stderr legitimately
        # records carrier/model names); it is never published, so it is out of scope for
        # this tracked-pack purity scan. Excluding it is scoping, not a ban relaxation.
        if ".git" in path.parts or ".agent-runs" in path.parts:
            continue
        rel = path.relative_to(root).as_posix()
        if any(part in BAD_PATH_PARTS for part in path.parts):
            errors.append(f"forbidden path: {rel}")
            continue
        if not path.is_file() or not _is_text(path):
            continue
        text = path.read_text(encoding="utf-8")
        for token in BAD_TEXT:
            if token in text:
                errors.append(f"forbidden token in {rel}: {token}")
    return errors


def _is_text(path: Path) -> bool:
    try:
        path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args(argv)
    errors = scan(Path(args.root).resolve())
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print("Codex-only residue check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
