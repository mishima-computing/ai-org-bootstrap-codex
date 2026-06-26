#!/usr/bin/env python3
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import deterministic_transform_tools as tools  # noqa: E402


def _write(repo: Path, rel: str, text: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_rename_codemod_reports_test_oracle_edit_as_residual_unverifiable():
    with tempfile.TemporaryDirectory() as d:
        repo = Path(d)
        _write(repo, "pkg/scaffold.py", "def scaffold_runner():\n    return 'scaffold'\n")
        _write(repo, "test_scaffold.py", "from pkg.scaffold import scaffold_runner\n")

        result = tools.apply(repo, {
            "old": "scaffold",
            "new": "demo_org",
            "replacements": {
                "pkg/scaffold.py": "pkg/demo_org.py",
                "pkg.scaffold": "pkg.demo_org",
                "scaffold_runner": "demo_runner",
                "scaffold": "demo_org",
            },
        })

        assert "test_scaffold.py" in result["files_changed"], result
        assert any(item["file"] == "test_scaffold.py" for item in result["residual_unverifiable"]), result
        assert result["self_verify"]["passed"], result
    print("ok  rename-codemod marks tool edits to test/oracle files residual_unverifiable")


if __name__ == "__main__":
    test_rename_codemod_reports_test_oracle_edit_as_residual_unverifiable()
