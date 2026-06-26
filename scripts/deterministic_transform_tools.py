"""Deterministic transform tools used by the controller's tool-route.

Slice-1 intentionally exposes one tool: ``rename-codemod``. The tool is an
untrusted generator: it may propose and apply a mechanical edit, but its
``self_verify`` result is only consistency evidence. Linon still reviews the
tool leaf.
"""
from __future__ import annotations

import io
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


def can_handle(repo: str | Path, params: dict | None) -> bool:
    return RenameCodemod().can_handle(repo, params)


def apply(repo: str | Path, params: dict | None) -> dict:  # noqa: A001 - direct-call tool convention
    return RenameCodemod().apply(repo, params)


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


def _default_replacements(old: str, new: str) -> dict[str, str]:
    old_snake = old.replace("-", "_")
    new_snake = new.replace("-", "_")
    return {
        old: new,
        old_snake: new_snake,
        old_snake.upper(): new_snake.upper(),
        old_snake.capitalize(): new_snake.capitalize(),
        old_snake.replace("_", "-"): new_snake.replace("_", "-"),
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

