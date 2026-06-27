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


def test_move_relocate_moves_python_module_and_rewrites_import_closure():
    with tempfile.TemporaryDirectory() as d:
        repo = Path(d)
        _write(repo, "pkg/old_mod.py", "VALUE = 1\n")
        _write(repo, "app.py", "from pkg.old_mod import VALUE\nprint(VALUE)\n")

        result = tools.apply_tool("move-relocate", repo, {
            "source": "pkg/old_mod.py",
            "destination": "pkg/new_mod.py",
        })

        assert (repo / "pkg/new_mod.py").is_file(), result
        assert not (repo / "pkg/old_mod.py").exists(), result
        assert "from pkg.new_mod import VALUE" in (repo / "app.py").read_text(encoding="utf-8")
        assert result["self_verify"]["passed"], result
    print("ok  move-relocate moves a Python module and rewrites import closure")


def test_import_hygiene_sorts_removes_unused_and_adds_requested_import():
    with tempfile.TemporaryDirectory() as d:
        repo = Path(d)
        _write(repo, "mod.py", "import sys\nimport os\n\nprint(path.basename('x'))\n")

        result = tools.apply_tool("import-hygiene", repo, {
            "files": ["mod.py"],
            "add_missing": [{"module": "os", "name": "path", "force": True}],
        })

        text = (repo / "mod.py").read_text(encoding="utf-8")
        assert "import sys" not in text, text
        assert "from os import path" in text, text
        assert result["self_verify"]["passed"], result
    print("ok  import-hygiene sorts/removes unused imports and adds requested missing imports")


def test_format_lint_fix_fallback_is_idempotent_self_check_only():
    with tempfile.TemporaryDirectory() as d:
        repo = Path(d)
        _write(repo, "loose.py", "x = 1   \n")

        result = tools.apply_tool("format-lint-fix", repo, {"files": ["loose.py"]})

        assert (repo / "loose.py").read_text(encoding="utf-8") == "x = 1\n"
        assert result["self_verify"]["passed"], result
        assert result["self_verify"].get("idempotence_is_not_linon_substitute"), result
    print("ok  format-lint-fix fallback formats whitespace but marks idempotence as non-substitute")


def test_signature_change_rewrites_definition_and_keyword_call_sites():
    with tempfile.TemporaryDirectory() as d:
        repo = Path(d)
        _write(repo, "api.py", "def fetch(url, timeout):\n    return url, timeout\n\nfetch(url='x', timeout=3)\n")

        result = tools.apply_tool("signature-change", repo, {
            "function": "fetch",
            "new_signature": "fetch(endpoint, timeout=10)",
            "files": ["api.py"],
        })

        text = (repo / "api.py").read_text(encoding="utf-8")
        assert "def fetch(endpoint, timeout=10):" in text, text
        assert "fetch(endpoint='x', timeout=3)" in text, text
        assert result["self_verify"]["passed"], result
    print("ok  signature-change updates the function definition and keyword call sites")


if __name__ == "__main__":
    test_rename_codemod_reports_test_oracle_edit_as_residual_unverifiable()
    test_move_relocate_moves_python_module_and_rewrites_import_closure()
    test_import_hygiene_sorts_removes_unused_and_adds_requested_import()
    test_format_lint_fix_fallback_is_idempotent_self_check_only()
    test_signature_change_rewrites_definition_and_keyword_call_sites()
