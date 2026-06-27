#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def is_embedded(source: bytes, composed: bytes) -> bool:
    if source in composed:
        return True
    if source.endswith(b"\n") and source[:-1] in composed:
        return True
    return source + b"\n" in composed


def verify_embed(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Verify source artifact bytes are embedded in a composed file.")
    parser.add_argument("--verify-embed", action="store_true")
    parser.add_argument("--composed", required=True)
    parser.add_argument("--source", action="append", required=True)
    args = parser.parse_args(argv)

    errors: list[str] = []
    try:
        composed = Path(args.composed).read_bytes()
    except OSError as exc:
        composed = b""
        errors.append(f"{args.composed}: {exc}")

    sources = []
    for raw in args.source:
        path = Path(raw)
        try:
            source = path.read_bytes()
            embedded = not errors and is_embedded(source, composed)
            sources.append({
                "path": str(path),
                "sha256": sha256_bytes(source),
                "embedded": embedded,
            })
        except OSError as exc:
            errors.append(f"{raw}: {exc}")
            sources.append({
                "path": raw,
                "sha256": None,
                "embedded": False,
            })

    exit_code = 0 if not errors and all(item["embedded"] for item in sources) else 1
    payload = {
        "status": "pass" if exit_code == 0 else "fail",
        "exit_code": exit_code,
        "composed": args.composed,
        "sources": sources,
    }
    if errors:
        payload["errors"] = errors
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return exit_code


def main(argv: list[str]) -> int:
    if argv and argv[0] == "--verify-embed":
        return verify_embed(argv)

    items = []
    for raw in argv:
        path = Path(raw)
        if path.is_file():
            items.append({"path": str(path), "sha256": sha256(path)})

    print(json.dumps({
        "status": "pass",
        "exit_code": 0,
        "artifacts": items,
    }, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
