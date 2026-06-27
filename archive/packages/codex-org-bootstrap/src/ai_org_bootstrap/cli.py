from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys

from ai_org_bootstrap.pack import find_repo_root
from ai_org_bootstrap.scripts.validate_pack import main as validate_main


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aob")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("validate")
    registry = sub.add_parser("registry")
    registry_sub = registry.add_subparsers(dest="registry_command", required=True)
    registry_sub.add_parser("check")
    merge_gate = sub.add_parser("merge-gate")
    merge_gate.add_argument("pr")
    merge_gate.add_argument("--repo")
    merge_gate.add_argument("--out")
    merge_gate.add_argument("--method", default="merge")
    merge_gate.add_argument("--check-only", action="store_true")
    args = parser.parse_args(argv)

    if args.command == "validate":
        return validate_main([])
    if args.command == "registry":
        return validate_main([])
    if args.command == "merge-gate":
        root = find_repo_root()
        cmd = [sys.executable, str(root / "scripts" / "merge-gate.py"), args.pr, "--method", args.method]
        if args.repo:
            cmd += ["--repo", args.repo]
        if args.out:
            cmd += ["--out", args.out]
        if args.check_only:
            cmd.append("--check-only")
        return subprocess.call(cmd)
    raise AssertionError(f"unhandled command {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
