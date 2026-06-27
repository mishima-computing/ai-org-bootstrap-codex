from __future__ import annotations

from pathlib import Path


def find_repo_root(start: str | Path | None = None) -> Path:
    current = Path(start or ".").resolve()
    for candidate in [current, *current.parents]:
        if (candidate / "registry" / "runtime-registry.yaml").is_file():
            return candidate
        if (candidate / ".git").exists() and (candidate / "README.md").is_file():
            return candidate
    raise FileNotFoundError("Could not locate AI Org Bootstrap Codex repository root.")
