#!/usr/bin/env python3
"""A correct tiny CLI: --help -> 0, no args -> 2 (stderr), bad flag -> 2 (stderr)."""
import sys


def main(argv):
    if "--help" in argv:
        print("usage: app [--greet NAME]")
        return 0
    if not argv:
        sys.stderr.write("error: no arguments given\n")
        return 2
    if argv[0] == "--greet" and len(argv) == 2:
        print(f"hello {argv[1]}")
        return 0
    sys.stderr.write("error: invalid arguments\n")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
