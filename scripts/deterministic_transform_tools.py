"""Deterministic transform tools used by the controller's tool-route.

Slice-1 intentionally exposes one tool: ``rename-codemod``. The tool is an
untrusted generator: it may propose and apply a mechanical edit, but its
``self_verify`` result is only consistency evidence. Linon still reviews the
tool leaf.
"""
from __future__ import annotations

import ast
import io
import shutil
import subprocess
import re
import sys
import tokenize
from dataclasses import asdict, dataclass
from pathlib import Path


TEXT_SUFFIXES = {
    ".cfg", ".cjs", ".css", ".html", ".ini", ".js", ".json", ".jsx", ".md", ".mjs",
    ".py", ".pyi", ".sh", ".toml", ".ts", ".tsx", ".txt", ".yaml", ".yml",
}
TEXT_FILENAMES = {"Dockerfile", "Makefile", "README"}
SKIP_DIRS = {
    ".agent-runs", ".cache", ".git", ".mypy_cache", ".nox", ".pytest_cache", ".ruff_cache",
    ".tox", ".venv", "__pycache__", "archive", "archives", "artifacts", "build", "coverage",
    "dist", "generated", "htmlcov", "node_modules", "tmp", "venv",
}


@dataclass(frozen=True)
class RenameParams:
    old: str
    new: str
    replacements: dict[str, str]
    objective: str = ""


class RenameCodemod:
    id = "rename-codemod"

    def can_handle(self, repo: str | Path, params: dict | None) -> bool:
        parsed = _coerce_params(params)
        if parsed is None:
            return False
        root = Path(repo)
        refs = _discover(root, include_excluded=True)
        if refs:
            return any(_verdict(ref) == "rename_candidate" for ref in refs)
        return any(_contains_any_mapping_key(path, parsed.replacements) for path in _iter_text_files(root))

    def apply(self, repo: str | Path, params: dict | None) -> dict:
        parsed = _coerce_params(params)
        if parsed is None:
            return _escalate("rename parameters are missing or ambiguous")

        root = Path(repo).resolve()
        excluded: list[dict] = []
        residual: list[dict] = []
        scope: set[str] = set()
        files_changed: list[str] = []

        refs = _discover(root, include_excluded=True)
        protected_by_file: dict[str, set[int]] = {}
        candidate_by_file: dict[str, set[int]] = {}
        for ref in refs:
            line = getattr(ref, "line_number", None)
            if not isinstance(line, int):
                continue
            rel = str(getattr(ref, "path", ""))
            if _verdict(ref) == "protected_exclusion":
                protected_by_file.setdefault(rel, set()).add(line)
                excluded.append({
                    "file": rel,
                    "line": line,
                    "token": str(getattr(ref, "token", "")),
                    "reason": str(getattr(ref, "exclusion_reason", "")) or "protected real reference",
                })
            elif _verdict(ref) == "rename_candidate":
                candidate_by_file.setdefault(rel, set()).add(line)
                scope.add(rel)

        if not refs:
            for path in _iter_text_files(root):
                rel = path.relative_to(root).as_posix()
                if _contains_any_mapping_key(path, parsed.replacements):
                    candidate_by_file[rel] = set()
                    scope.add(rel)

        for rel in sorted(scope):
            path = root / rel
            original = path.read_text(encoding="utf-8", errors="replace")
            changed = _rewrite_text(
                original,
                parsed.replacements,
                candidate_lines=candidate_by_file.get(rel) or None,
                protected_lines=protected_by_file.get(rel) or set(),
            )
            if changed != original:
                path.write_text(changed, encoding="utf-8")
                files_changed.append(rel)
                if _is_oracle_path(rel):
                    residual.append({
                        "file": rel,
                        "reason": "tool edited a test/oracle file; mechanical rewrite is not an independent oracle",
                    })
                if _changed_string_or_comment(original, changed, parsed.replacements):
                    residual.append({
                        "file": rel,
                        "reason": "string/comment marker changed; AST binding cannot prove semantic intent",
                    })

        renamed = _rename_matching_files(root, parsed.replacements)
        for item in renamed:
            if item not in files_changed:
                files_changed.append(item)
            scope.add(item)

        stale = _stale_candidate_refs(root, parsed, protected_by_file)
        self_verify = {
            "passed": not stale,
            "checks": ["candidate closure contains no stale old-token references"],
            "stale_references": stale,
        }
        return {
            "tool_id": self.id,
            "files_changed": sorted(files_changed),
            "scope": sorted(scope | set(files_changed)),
            "excluded": sorted(excluded, key=lambda x: (x.get("file", ""), x.get("line", 0), x.get("token", ""))),
            "self_verify": self_verify,
            "escalate": [] if self_verify["passed"] else [{
                "reason": "stale rename candidates remain after codemod",
                "stale_references": stale,
            }],
            "residual_unverifiable": _dedupe_residual(residual),
        }


@dataclass(frozen=True)
class MoveParams:
    source: str
    destination: str
    objective: str = ""


class MoveRelocateTool:
    id = "move-relocate"

    def can_handle(self, repo: str | Path, params: dict | None) -> bool:
        parsed = _coerce_move_params(params)
        if parsed is None:
            return False
        root = Path(repo)
        src = root / parsed.source
        dst = root / parsed.destination
        return src.is_file() and not dst.exists() and src.suffix == ".py" and dst.suffix == ".py"

    def apply(self, repo: str | Path, params: dict | None) -> dict:
        parsed = _coerce_move_params(params)
        if parsed is None:
            return _tool_escalate(self.id, "move parameters are missing or ambiguous")
        root = Path(repo).resolve()
        if not self.can_handle(root, asdict(parsed)):
            return _tool_escalate(self.id, "move source/destination is not a supported Python file move")

        old_module = _module_name(parsed.source)
        new_module = _module_name(parsed.destination)
        src = root / parsed.source
        dst = root / parsed.destination
        dst.parent.mkdir(parents=True, exist_ok=True)
        src.rename(dst)

        files_changed = {parsed.source, parsed.destination}
        residual: list[dict] = []
        for path in _iter_text_files(root):
            if path.suffix != ".py":
                continue
            rel = path.relative_to(root).as_posix()
            before = path.read_text(encoding="utf-8", errors="replace")
            after = _rewrite_python_module_refs(before, old_module, new_module)
            if after != before:
                path.write_text(after, encoding="utf-8")
                files_changed.add(rel)
                if _is_oracle_path(rel):
                    residual.append({"file": rel, "reason": "tool edited a test/oracle file; mechanical rewrite is not an independent oracle"})
                if _changed_string_or_comment(before, after, {old_module: new_module}):
                    residual.append({"file": rel, "reason": "string/comment marker changed; AST binding cannot prove semantic intent"})

        stale = _stale_text_hits(root, old_module, files=files_changed)
        return _tool_result(
            self.id,
            files_changed=sorted(files_changed),
            scope=sorted(files_changed),
            excluded=[],
            checks=["moved Python file exists at destination", "import/reference closure contains no stale old module"],
            stale=stale,
            residual=residual,
            escalate_reason="stale move references remain after relocate" if stale else None,
        )


@dataclass(frozen=True)
class ImportHygieneParams:
    files: tuple[str, ...] = ()
    add_missing: tuple[dict, ...] = ()
    objective: str = ""


class ImportHygieneTool:
    id = "import-hygiene"

    def can_handle(self, repo: str | Path, params: dict | None) -> bool:
        parsed = _coerce_import_params(params)
        if parsed is None:
            return False
        files = _target_python_files(Path(repo), parsed.files)
        return bool(files)

    def apply(self, repo: str | Path, params: dict | None) -> dict:
        parsed = _coerce_import_params(params)
        if parsed is None:
            return _tool_escalate(self.id, "import hygiene parameters are missing or ambiguous")
        root = Path(repo).resolve()
        files_changed: list[str] = []
        residual: list[dict] = []
        for path in _target_python_files(root, parsed.files):
            rel = path.relative_to(root).as_posix()
            before = path.read_text(encoding="utf-8", errors="replace")
            after, file_residual = _rewrite_imports(before, parsed.add_missing)
            if after != before:
                path.write_text(after, encoding="utf-8")
                files_changed.append(rel)
                residual.extend({"file": rel, **item} for item in file_residual)
                if _is_oracle_path(rel):
                    residual.append({"file": rel, "reason": "tool edited a test/oracle file; mechanical rewrite is not an independent oracle"})
        stale = []
        for rel in files_changed:
            ok, detail = _python_parses(root / rel)
            if not ok:
                stale.append({"file": rel, "token": "syntax", "detail": detail})
        return _tool_result(
            self.id,
            files_changed=sorted(files_changed),
            scope=sorted(files_changed),
            excluded=[],
            checks=["imports sorted", "unused import aliases removed", "requested missing imports added", "Python AST parses"],
            stale=stale,
            residual=residual,
            escalate_reason="import hygiene produced a file that does not parse" if stale else None,
        )


@dataclass(frozen=True)
class FormatLintParams:
    files: tuple[str, ...] = ()
    objective: str = ""


class FormatLintFixTool:
    id = "format-lint-fix"

    def can_handle(self, repo: str | Path, params: dict | None) -> bool:
        parsed = _coerce_format_params(params)
        if parsed is None:
            return False
        return bool(_target_format_files(Path(repo), parsed.files))

    def apply(self, repo: str | Path, params: dict | None) -> dict:
        parsed = _coerce_format_params(params)
        if parsed is None:
            return _tool_escalate(self.id, "format/lint parameters are missing or ambiguous")
        root = Path(repo).resolve()
        before = _snapshot_files(root)
        commands = _format_commands(root, parsed.files)
        command_reports: list[dict] = []
        for cmd in commands:
            proc = subprocess.run(cmd, cwd=root, capture_output=True, text=True, check=False)
            command_reports.append({
                "cmd": cmd,
                "returncode": proc.returncode,
                "stdout": proc.stdout[-2000:],
                "stderr": proc.stderr[-2000:],
            })
        if not commands:
            _fallback_format(root, parsed.files)
            command_reports.append({"cmd": ["fallback-format"], "returncode": 0, "stdout": "", "stderr": ""})
        after = _snapshot_files(root)
        files_changed = sorted(rel for rel, text in after.items() if before.get(rel) != text)
        failed = [r for r in command_reports if r["returncode"] != 0]
        residual = [
            {"file": rel, "reason": "tool edited a test/oracle file; mechanical rewrite is not an independent oracle"}
            for rel in files_changed if _is_oracle_path(rel)
        ]
        return {
            "tool_id": self.id,
            "files_changed": files_changed,
            "scope": sorted(set(files_changed) | set(_target_format_relpaths(root, parsed.files))),
            "excluded": [],
            "self_verify": {
                "passed": not failed,
                "checks": ["repo formatter/linter fixer exited zero" if commands else "fallback whitespace formatter ran"],
                "command_reports": command_reports,
                "idempotence_is_not_linon_substitute": True,
            },
            "escalate": [] if not failed else [{"reason": "formatter/linter fixer failed", "commands": failed}],
            "residual_unverifiable": _dedupe_residual(residual),
        }


@dataclass(frozen=True)
class SignatureChangeParams:
    function: str
    new_signature: str
    files: tuple[str, ...] = ()
    objective: str = ""


class SignatureChangeTool:
    id = "signature-change"

    def can_handle(self, repo: str | Path, params: dict | None) -> bool:
        parsed = _coerce_signature_params(params)
        if parsed is None:
            return False
        return any(_find_function_signature(path, parsed.function) for path in _target_python_files(Path(repo), parsed.files))

    def apply(self, repo: str | Path, params: dict | None) -> dict:
        parsed = _coerce_signature_params(params)
        if parsed is None:
            return _tool_escalate(self.id, "signature-change parameters are missing or ambiguous")
        root = Path(repo).resolve()
        old_args: list[str] | None = None
        new_args = _signature_arg_names(parsed.new_signature)
        if not new_args:
            return _tool_escalate(self.id, "new_signature does not parse")
        files_changed: list[str] = []
        residual: list[dict] = []
        for path in _target_python_files(root, parsed.files):
            found = _find_function_signature(path, parsed.function)
            if found and old_args is None:
                old_args = found["args"]
        if old_args is None:
            return _tool_escalate(self.id, f"function {parsed.function!r} was not found")
        keyword_map = {old: new for old, new in zip(old_args, new_args) if old != new}
        for path in _target_python_files(root, parsed.files):
            rel = path.relative_to(root).as_posix()
            before = path.read_text(encoding="utf-8", errors="replace")
            after = _rewrite_signature_text(before, parsed.function, parsed.new_signature, keyword_map)
            if after != before:
                path.write_text(after, encoding="utf-8")
                files_changed.append(rel)
                if _is_oracle_path(rel):
                    residual.append({"file": rel, "reason": "tool edited a test/oracle file; mechanical rewrite is not an independent oracle"})
        stale = []
        for rel in files_changed:
            ok, detail = _python_parses(root / rel)
            if not ok:
                stale.append({"file": rel, "token": "syntax", "detail": detail})
        return _tool_result(
            self.id,
            files_changed=sorted(files_changed),
            scope=sorted(files_changed),
            excluded=[],
            checks=["function definition signature replaced", "keyword call sites updated", "Python AST parses"],
            stale=stale,
            residual=residual,
            escalate_reason="signature change produced a file that does not parse" if stale else None,
        )


TOOLS = {
    RenameCodemod.id: RenameCodemod(),
    MoveRelocateTool.id: MoveRelocateTool(),
    ImportHygieneTool.id: ImportHygieneTool(),
    FormatLintFixTool.id: FormatLintFixTool(),
    SignatureChangeTool.id: SignatureChangeTool(),
}


def tool_ids() -> list[str]:
    return sorted(TOOLS)


def can_handle_tool(tool_id: str, repo: str | Path, params: dict | None) -> bool:
    tool = TOOLS.get(tool_id)
    return bool(tool and tool.can_handle(repo, params))


def apply_tool(tool_id: str, repo: str | Path, params: dict | None) -> dict:
    tool = TOOLS.get(tool_id)
    if tool is None:
        return _tool_escalate(tool_id or "deterministic-transform-tool", "unknown deterministic transform tool")
    return tool.apply(repo, params)


def can_handle(repo: str | Path, params: dict | None) -> bool:
    tool_id = _tool_id_from_params(params) or RenameCodemod.id
    return can_handle_tool(tool_id, repo, params)


def apply(repo: str | Path, params: dict | None) -> dict:  # noqa: A001 - direct-call tool convention
    tool_id = _tool_id_from_params(params) or RenameCodemod.id
    return apply_tool(tool_id, repo, params)


def _tool_id_from_params(params: dict | None) -> str | None:
    if not isinstance(params, dict):
        return None
    value = params.get("tool_id") or params.get("kind")
    if isinstance(value, str) and value in TOOLS:
        return value
    return None


def _coerce_params(params: dict | None) -> RenameParams | None:
    if not isinstance(params, dict):
        return None
    old = str(params.get("old") or "").strip()
    new = str(params.get("new") or "").strip()
    if not old or not new or old == new:
        return None
    replacements = params.get("replacements")
    if not isinstance(replacements, dict):
        replacements = _default_replacements(old, new)
    clean = {str(k): str(v) for k, v in replacements.items() if isinstance(k, str) and k}
    if not clean:
        return None
    return RenameParams(old=old, new=new, replacements=clean, objective=str(params.get("objective") or ""))


def _coerce_move_params(params: dict | None) -> MoveParams | None:
    if not isinstance(params, dict):
        return None
    source = str(params.get("source") or params.get("old_path") or "").strip().strip("`'\"")
    destination = str(params.get("destination") or params.get("new_path") or "").strip().strip("`'\"")
    if not source or not destination or source == destination or _unsafe_rel(source) or _unsafe_rel(destination):
        return None
    return MoveParams(source=source, destination=destination, objective=str(params.get("objective") or ""))


def _coerce_import_params(params: dict | None) -> ImportHygieneParams | None:
    if not isinstance(params, dict):
        return None
    files = tuple(_clean_rel_list(params.get("files") or params.get("scope") or []))
    add_missing = params.get("add_missing") or []
    if isinstance(add_missing, dict):
        add_missing = [add_missing]
    if not isinstance(add_missing, list):
        return None
    clean_missing = tuple(dict(item) for item in add_missing if isinstance(item, dict) and item.get("module"))
    return ImportHygieneParams(files=files, add_missing=clean_missing, objective=str(params.get("objective") or ""))


def _coerce_format_params(params: dict | None) -> FormatLintParams | None:
    if not isinstance(params, dict):
        return None
    files = tuple(_clean_rel_list(params.get("files") or params.get("scope") or []))
    return FormatLintParams(files=files, objective=str(params.get("objective") or ""))


def _coerce_signature_params(params: dict | None) -> SignatureChangeParams | None:
    if not isinstance(params, dict):
        return None
    function = str(params.get("function") or params.get("symbol") or "").strip()
    new_signature = str(params.get("new_signature") or "").strip()
    files = tuple(_clean_rel_list(params.get("files") or params.get("scope") or []))
    if not function or not new_signature:
        return None
    return SignatureChangeParams(function=function, new_signature=new_signature, files=files,
                                 objective=str(params.get("objective") or ""))


def _unsafe_rel(rel: str) -> bool:
    p = Path(rel)
    return p.is_absolute() or ".." in p.parts


def _clean_rel_list(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value
            if isinstance(item, str) and item.strip() and not _unsafe_rel(str(item).strip())]


def _default_replacements(old: str, new: str) -> dict[str, str]:
    old_snake = old.replace("-", "_")
    new_snake = new.replace("-", "_")
    replacements: dict[str, str] = {}

    def add(key: str, value: str) -> None:
        if key and key not in replacements:
            replacements[key] = value

    add(old, new)
    add(old_snake, new_snake)
    add(old_snake.upper(), new_snake.upper())
    add(old_snake.capitalize(), new_snake.capitalize())
    add(old_snake.replace("_", "-"), new_snake.replace("_", "-"))
    return replacements


def _module_name(rel: str) -> str:
    p = Path(rel)
    parts = list(p.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _rewrite_python_module_refs(text: str, old_module: str, new_module: str) -> str:
    replacements = {
        old_module: new_module,
        old_module.replace(".", "/") + ".py": new_module.replace(".", "/") + ".py",
    }
    return _replace_dotted_tokens(text, replacements)


def _replace_dotted_tokens(text: str, replacements: dict[str, str]) -> str:
    out = text
    for old, new in sorted(replacements.items(), key=lambda item: len(item[0]), reverse=True):
        if "." in old and "/" not in old:
            out = re.sub(rf"(?<![A-Za-z0-9_]){re.escape(old)}(?![A-Za-z0-9_])", new, out)
        else:
            out = out.replace(old, new)
    return out


def _stale_text_hits(root: Path, token: str, *, files: set[str] | list[str] | None = None) -> list[dict]:
    selected = set(files or [])
    stale: list[dict] = []
    for path in _iter_text_files(root):
        rel = path.relative_to(root).as_posix()
        if selected and rel not in selected:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line_no, line in enumerate(text.splitlines(), start=1):
            if token in line:
                stale.append({"file": rel, "line": line_no, "token": token})
    return stale


def _target_python_files(root: Path, files: tuple[str, ...] | list[str]) -> list[Path]:
    selected = [root / rel for rel in files] if files else list(root.rglob("*.py"))
    out = []
    for path in selected:
        try:
            rel_parts = set(path.resolve().relative_to(root.resolve()).parts)
        except ValueError:
            continue
        if path.is_file() and path.suffix == ".py" and not (rel_parts & SKIP_DIRS):
            out.append(path)
    return sorted(dict.fromkeys(out))


def _target_format_files(root: Path, files: tuple[str, ...] | list[str]) -> list[Path]:
    selected = [root / rel for rel in files] if files else list(_iter_text_files(root))
    out = []
    for path in selected:
        try:
            rel_parts = set(path.resolve().relative_to(root.resolve()).parts)
        except ValueError:
            continue
        if path.is_file() and not (rel_parts & SKIP_DIRS):
            out.append(path)
    return sorted(dict.fromkeys(out))


def _target_format_relpaths(root: Path, files: tuple[str, ...] | list[str]) -> list[str]:
    return [p.relative_to(root).as_posix() for p in _target_format_files(root, files)]


def _python_parses(path: Path) -> tuple[bool, str]:
    try:
        ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        return True, ""
    except SyntaxError as exc:
        return False, f"{exc.msg} at line {exc.lineno}"
    except OSError as exc:
        return False, str(exc)


def _rewrite_imports(text: str, add_missing: tuple[dict, ...]) -> tuple[str, list[dict]]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return text, [{"reason": "file does not parse; import hygiene skipped"}]
    used = _used_names(tree)
    lines = text.splitlines()
    import_nodes = [node for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))]
    if not import_nodes:
        insert_at = 0
        end_at = -1
    else:
        insert_at = min(node.lineno for node in import_nodes) - 1
        end_at = max(getattr(node, "end_lineno", node.lineno) for node in import_nodes) - 1
    kept: list[str] = []
    residual: list[dict] = []
    for node in import_nodes:
        if isinstance(node, ast.Import):
            aliases = [a for a in node.names if (a.asname or a.name.split(".", 1)[0]) in used]
            for alias in node.names:
                if alias not in aliases:
                    residual.append({"reason": f"removed unused import {alias.name}"})
            kept.extend(_format_import_alias(alias) for alias in aliases)
        elif isinstance(node, ast.ImportFrom):
            if node.module == "__future__":
                kept.append(_source_segment(text, node) or lines[node.lineno - 1])
                continue
            aliases = [a for a in node.names if a.name == "*" or (a.asname or a.name) in used]
            for alias in node.names:
                if alias not in aliases:
                    residual.append({"reason": f"removed unused import {alias.name}"})
            if aliases:
                names = ", ".join(_alias_name(a) for a in sorted(aliases, key=lambda a: a.name))
                dots = "." * int(node.level or 0)
                kept.append(f"from {dots}{node.module or ''} import {names}")
    for item in add_missing:
        module = str(item.get("module") or "")
        name = item.get("name")
        if isinstance(name, str) and name:
            line = f"from {module} import {name}"
            binding = str(item.get("as") or name)
        else:
            line = f"import {module}"
            binding = str(item.get("as") or module.split(".", 1)[0])
        if binding not in used and not item.get("force"):
            residual.append({"reason": f"requested missing import {binding} is not referenced"})
        if line not in kept:
            kept.append(line)
    kept = sorted(dict.fromkeys(kept), key=lambda s: (0 if s.startswith("from __future__") else 1, s))
    before = lines[:insert_at]
    after = lines[end_at + 1:] if end_at >= insert_at else lines[insert_at:]
    block = kept + ([""] if kept and after and after[0].strip() else [])
    return "\n".join(before + block + after) + ("\n" if text.endswith("\n") else ""), residual


def _used_names(tree: ast.AST) -> set[str]:
    used: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            used.add(node.id)
        elif isinstance(node, ast.Attribute):
            used.add(node.attr)
    return used


def _format_import_alias(alias: ast.alias) -> str:
    return "import " + _alias_name(alias)


def _alias_name(alias: ast.alias) -> str:
    return f"{alias.name} as {alias.asname}" if alias.asname else alias.name


def _source_segment(text: str, node: ast.AST) -> str | None:
    try:
        return ast.get_source_segment(text, node)
    except Exception:  # noqa: BLE001
        return None


def _snapshot_files(root: Path) -> dict[str, str]:
    out = {}
    for path in _iter_text_files(root):
        try:
            out[path.relative_to(root).as_posix()] = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            pass
    return out


def _format_commands(root: Path, files: tuple[str, ...]) -> list[list[str]]:
    rels = _target_format_relpaths(root, files)
    py_files = [rel for rel in rels if rel.endswith(".py")]
    js_files = [rel for rel in rels if Path(rel).suffix in {".js", ".jsx", ".ts", ".tsx", ".json", ".css", ".md"}]
    go_files = [rel for rel in rels if rel.endswith(".go")]
    commands: list[list[str]] = []
    if py_files and shutil.which("ruff"):
        commands.append(["ruff", "check", "--fix", *py_files])
        commands.append(["ruff", "format", *py_files])
    elif py_files:
        black_spec = importlib_spec("black")
        if black_spec is not None:
            commands.append([sys.executable, "-m", "black", *py_files])
    if go_files and shutil.which("gofmt"):
        commands.append(["gofmt", "-w", *go_files])
    if js_files and shutil.which("prettier"):
        commands.append(["prettier", "--write", *js_files])
    return commands


def importlib_spec(name: str):
    try:
        import importlib.util
        return importlib.util.find_spec(name)
    except Exception:  # noqa: BLE001
        return None


def _fallback_format(root: Path, files: tuple[str, ...]) -> None:
    for path in _target_format_files(root, files):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        lines = [line.rstrip() for line in text.splitlines()]
        path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def _find_function_signature(path: Path, function: str) -> dict | None:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(text)
    except (OSError, SyntaxError):
        return None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function:
            args = [a.arg for a in node.args.posonlyargs + node.args.args + node.args.kwonlyargs]
            return {"line": node.lineno, "args": args}
    return None


def _signature_arg_names(signature: str) -> list[str]:
    try:
        tree = ast.parse(signature if signature.lstrip().startswith("def ") else f"def {signature}: pass")
    except SyntaxError:
        try:
            tree = ast.parse(f"def _x({signature}): pass")
        except SyntaxError:
            return []
    fn = next((n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)), None)
    if fn is None:
        return []
    return [a.arg for a in fn.args.posonlyargs + fn.args.args + fn.args.kwonlyargs]


def _rewrite_signature_text(text: str, function: str, new_signature: str, keyword_map: dict[str, str]) -> str:
    lines = text.splitlines(keepends=True)
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return text
    for node in sorted((n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and n.name == function), key=lambda n: n.lineno, reverse=True):
        line = lines[node.lineno - 1]
        indent = line[:len(line) - len(line.lstrip())]
        prefix = "async def " if isinstance(node, ast.AsyncFunctionDef) else "def "
        sig = new_signature.strip()
        if sig.startswith("def "):
            sig = sig[4:].strip()
        if sig.startswith(function):
            replacement = f"{indent}{prefix}{sig}"
        else:
            replacement = f"{indent}{prefix}{function}({sig})"
        if not replacement.rstrip().endswith(":"):
            replacement = replacement.rstrip() + ":"
        lines[node.lineno - 1] = replacement + ("\n" if line.endswith("\n") else "")
    rewritten = "".join(lines)
    for old, new in keyword_map.items():
        rewritten = re.sub(rf"(?P<fn>\b{re.escape(function)}\s*\([^)]*?)\b{re.escape(old)}\s*=",
                           rf"\g<fn>{new}=", rewritten)
    return rewritten


def _tool_result(tool_id: str, *, files_changed: list[str], scope: list[str], excluded: list[dict],
                 checks: list[str], stale: list[dict], residual: list[dict],
                 escalate_reason: str | None) -> dict:
    return {
        "tool_id": tool_id,
        "files_changed": files_changed,
        "scope": scope,
        "excluded": sorted(excluded, key=lambda x: (x.get("file", ""), x.get("line", 0), x.get("token", ""))),
        "self_verify": {
            "passed": not stale,
            "checks": checks,
            "stale_references": stale,
        },
        "escalate": [] if not stale else [{"reason": escalate_reason or "tool self verification failed",
                                           "stale_references": stale}],
        "residual_unverifiable": _dedupe_residual(residual),
    }


def _tool_escalate(tool_id: str, reason: str) -> dict:
    return {
        "tool_id": tool_id,
        "files_changed": [],
        "scope": [],
        "excluded": [],
        "self_verify": {"passed": False, "checks": [], "stale_references": []},
        "escalate": [{"reason": reason}],
        "residual_unverifiable": [],
    }


def _discover(root: Path, *, include_excluded: bool):
    try:
        sys.path.insert(0, str(root))
        from rename.discovery import discover_references  # type: ignore
        return list(discover_references(root, include_excluded=include_excluded))
    except Exception:  # noqa: BLE001 - optional repo-local helper
        return []
    finally:
        try:
            sys.path.remove(str(root))
        except ValueError:
            pass


def _verdict(ref) -> str:
    verdict = getattr(ref, "guard_verdict", "")
    return str(getattr(verdict, "value", verdict))


def _iter_text_files(root: Path):
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        parts = set(path.relative_to(root).parts)
        if parts & SKIP_DIRS:
            continue
        if path.suffix.lower() in TEXT_SUFFIXES or path.name in TEXT_FILENAMES:
            yield path


def _contains_any_mapping_key(path: Path, replacements: dict[str, str]) -> bool:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return any(old in text for old in replacements)


def _rewrite_text(text: str, replacements: dict[str, str], *, candidate_lines: set[int] | None,
                  protected_lines: set[int]) -> str:
    out: list[str] = []
    for line_no, line in enumerate(text.splitlines(keepends=True), start=1):
        if line_no in protected_lines:
            out.append(line)
            continue
        if candidate_lines is not None and line_no not in candidate_lines:
            out.append(line)
            continue
        out.append(_rewrite_line(line, replacements))
    return "".join(out)


def _rewrite_line(line: str, replacements: dict[str, str]) -> str:
    if not any(old in line for old in replacements):
        return line
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(line).readline))
    except tokenize.TokenError:
        return _replace_tokens(line, replacements)
    changed = []
    for tok in tokens:
        if tok.type == tokenize.NAME:
            changed.append(tok._replace(string=replacements.get(tok.string, tok.string)))
        else:
            changed.append(tok._replace(string=_replace_tokens(tok.string, replacements)))
    return tokenize.untokenize(changed)


def _replace_tokens(text: str, replacements: dict[str, str]) -> str:
    out = text
    for old in sorted(replacements, key=len, reverse=True):
        new = replacements[old]
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", old):
            out = re.sub(rf"(?<![A-Za-z0-9_]){re.escape(old)}(?![A-Za-z0-9_])", new, out)
        else:
            out = out.replace(old, new)
    return out


def _rename_matching_files(root: Path, replacements: dict[str, str]) -> list[str]:
    changed: list[str] = []
    for path in sorted(_iter_text_files(root), key=lambda p: len(p.parts), reverse=True):
        rel = path.relative_to(root).as_posix()
        new_rel = _replace_tokens(rel, replacements)
        if new_rel == rel:
            continue
        dest = root / new_rel
        if dest.exists():
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        path.rename(dest)
        changed.append(new_rel)
    return changed


def _stale_candidate_refs(root: Path, params: RenameParams, protected_by_file: dict[str, set[int]]) -> list[dict]:
    stale: list[dict] = []
    refs = _discover(root, include_excluded=True)
    if refs:
        for ref in refs:
            rel = str(getattr(ref, "path", ""))
            line = getattr(ref, "line_number", None)
            if _verdict(ref) == "rename_candidate" and line not in protected_by_file.get(rel, set()):
                token = str(getattr(ref, "token", ""))
                if any(old.lower() in token.lower() for old in params.replacements):
                    stale.append({"file": rel, "line": line, "token": token})
        return stale
    for path in _iter_text_files(root):
        rel = path.relative_to(root).as_posix()
        for line_no, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
            if any(old in line for old in params.replacements):
                stale.append({"file": rel, "line": line_no, "token": params.old})
    return stale


def _is_oracle_path(rel: str) -> bool:
    p = rel.replace("\\", "/")
    name = Path(p).name
    return name.startswith("test_") or "/test_" in p or p.startswith("tests/") or "/tests/" in p


def _changed_string_or_comment(before: str, after: str, replacements: dict[str, str]) -> bool:
    if before == after:
        return False
    markers = set(replacements.values())
    try:
        before_tokens = list(tokenize.generate_tokens(io.StringIO(before).readline))
        after_tokens = list(tokenize.generate_tokens(io.StringIO(after).readline))
    except tokenize.TokenError:
        return True
    for tok in after_tokens:
        if tok.type in {tokenize.STRING, tokenize.COMMENT} and any(marker in tok.string for marker in markers):
            return True
    return False


def _dedupe_residual(items: list[dict]) -> list[dict]:
    seen = set()
    out = []
    for item in items:
        key = (item.get("file"), item.get("reason"))
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _escalate(reason: str) -> dict:
    return {
        "tool_id": RenameCodemod.id,
        "files_changed": [],
        "scope": [],
        "excluded": [],
        "self_verify": {"passed": False, "checks": [], "stale_references": []},
        "escalate": [{"reason": reason}],
        "residual_unverifiable": [],
    }
