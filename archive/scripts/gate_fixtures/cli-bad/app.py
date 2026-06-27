#!/usr/bin/env python3
"""A BUGGY CLI: it exits 0 on no-args instead of the contract's exit 2. The conformance gate MUST catch this
(the contract's no-arg example asserts exit 2 + an 'error' on stderr). --help is correct, so the gate fails
precisely on the leaked exit code rather than on a missing build."""
import sys


def main(argv):
    if "--help" in argv:
        print("usage: app [--greet NAME]")
        return 0
    if not argv:
        print("nothing to do")          # BUG: should write 'error' to stderr and exit 2
        return 0                          # BUG: should be 2
    if argv[0] == "--greet" and len(argv) == 2:
        print(f"hello {argv[1]}")
        return 0
    sys.stderr.write("error: invalid arguments\n")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
