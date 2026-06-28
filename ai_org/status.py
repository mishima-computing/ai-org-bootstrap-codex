"""CLI entrypoint for inspecting AI Org Git state."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .driver import status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Report AI Org state from standard Git branches.")
    parser.add_argument("--repo", default=".", help="Target Git repository.")
    args = parser.parse_args(argv)
    print(json.dumps(status(Path(args.repo)), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
