"""Run python -m ai_org.patch by calling patch.pull."""
from __future__ import annotations

import sys

from ai_org.patch import pull


def main() -> None:
    repo = sys.argv[1] if len(sys.argv) > 1 else "."
    print(pull(repo))


if __name__ == "__main__":
    main()
