#!/usr/bin/env python3
"""Profile evidence provenance gate.

This gate verifies provenance and structure only (authorized git-tracked profile,
git-bound diff, every obligation backed by a distinct evidence_ref resolving to a
real added code line, required-evidence kinds present). It does NOT judge whether
the cited code semantically satisfies the obligation -- that is delegated to
adversarial Linon review.
"""
from __future__ import annotations

import argparse
import ast
import io
import json
import re
import subprocess
import sys
import tempfile
import tokenize
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = ROOT / "fixtures" / "profile-evidence"
PROFILE_CARD_DIR = ROOT / ".agent-org" / "knowledge" / "ui"

STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "in",
    "into",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "their",
    "this",
    "to",
    "with",
}

LOW_INFO_TOKENS = {
    "all",
    "anything",
    "everything",
    "generally",
    "nice",
    "nicely",
    "overall",
    "proceed",
    "stuff",
    "thing",
    "things",
    "whatever",
    "should",
}

GENERIC_TOKENS = {
    "profile",
    "system",
    "checker",
    "state",
    "user",
    "request",
    "data",
    "line",
    "lines",
    "file",
    "files",
    "code",
}

ACTION_TERMS = {
    "add",
    "allow",
    "attach",
    "back",
    "bind",
    "block",
    "check",
    "derive",
    "encrypt",
    "enforce",
    "expose",
    "grant",
    "persist",
    "reject",
    "require",
    "resolve",
    "rotate",
    "store",
    "validate",
    "verify",
}

PROTECTIVE_TERMS = {"block", "encrypt", "reject", "require", "validate", "verify"}
RISKY_TERMS = {"allow", "expose", "grant", "persist", "plaintext", "raw", "unrestricted"}
NEGATION_TERMS = {"deny", "never", "no", "not", "reject", "unless", "without"}

HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
TOKEN_RE = re.compile(r"[a-z0-9]+")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def rel_fixture(case_name: str, name: str) -> Path:
    return FIXTURE_DIR / case_name / name


def substantive_tokens(value: str) -> set[str]:
    tokens: set[str] = set()
    for raw in TOKEN_RE.findall(value.lower()):
        if raw in STOP_WORDS or raw in LOW_INFO_TOKENS or raw.isdigit() or len(raw) <= 2:
            continue
        token = raw
        if len(token) > 5 and token.endswith("ing"):
            stem = token[:-3]
            token = f"{stem}e" if f"{stem}e" in ACTION_TERMS else stem
        elif len(token) > 4 and token.endswith("ed"):
            token = token[:-2]
        elif len(token) > 4 and token.endswith("s"):
            token = token[:-1]
        if token in LOW_INFO_TOKENS:
            continue
        tokens.add(token)
    return tokens


def distinctive_tokens(value: str) -> set[str]:
    return {token for token in substantive_tokens(value) if token not in GENERIC_TOKENS}


def action_tokens(value: str) -> set[str]:
    terms = substantive_tokens(value)
    return terms & ACTION_TERMS


def obligation_errors(obligation: object, _criterion_text: str, label: str) -> list[str]:
    if not isinstance(obligation, str):
        return [f"{label}: obligation must be a string"]
    return []


def parse_ref(ref: object, label: str) -> tuple[str, int] | None:
    if not isinstance(ref, str) or ":" not in ref:
        return None
    path, line_text = ref.rsplit(":", 1)
    if not path or not line_text.isdigit():
        return None
    line = int(line_text)
    if line <= 0:
        return None
    return path, line


class DiffError(ValueError):
    pass


def decode_git_path_token(token: str) -> str:
    if len(token) < 2 or not token.startswith('"') or not token.endswith('"'):
        return token

    raw = token[1:-1]
    output = bytearray()
    simple_escapes = {
        "a": b"\a",
        "b": b"\b",
        "t": b"\t",
        "n": b"\n",
        "v": b"\v",
        "f": b"\f",
        "r": b"\r",
        '"': b'"',
        "\\": b"\\",
    }
    index = 0
    while index < len(raw):
        char = raw[index]
        if char != "\\":
            output.extend(char.encode("utf-8"))
            index += 1
            continue

        index += 1
        if index >= len(raw):
            output.extend(b"\\")
            break
        escaped = raw[index]
        if escaped in "01234567":
            end = index
            while end < len(raw) and end < index + 3 and raw[end] in "01234567":
                end += 1
            output.append(int(raw[index:end], 8))
            index = end
            continue
        output.extend(simple_escapes.get(escaped, escaped.encode("utf-8")))
        index += 1
    return output.decode("utf-8")


def strip_git_side_prefix(path: str, side: str) -> str:
    prefix = f"{side}/"
    return path[2:] if path.startswith(prefix) else path


def _scan_c_quoted_token(text: str, start: int = 0) -> tuple[str, int] | None:
    if start >= len(text) or text[start] != '"':
        return None
    index = start + 1
    escaped = False
    while index < len(text):
        char = text[index]
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == '"':
            index += 1
            return text[start:index], index
        index += 1
    return None


def _header_path_field(raw: str) -> str:
    field = raw.rstrip("\r")
    if field.startswith('"'):
        scanned = _scan_c_quoted_token(field)
        if scanned is not None:
            return decode_git_path_token(scanned[0])
        return decode_git_path_token(field)
    if "\t" in field:
        field = field.split("\t", 1)[0]
    return decode_git_path_token(field)


def parse_diff_git_new_path(line: str) -> str | None:
    text = line[len("diff --git "):]
    first = _scan_c_quoted_token(text)
    if first is not None:
        left_token, index = first
        while index < len(text) and text[index].isspace():
            index += 1
        second = _scan_c_quoted_token(text, index)
        if second is None:
            return None
        return strip_git_side_prefix(decode_git_path_token(second[0]), "b")

    marker = " b/"
    if text.startswith("a/") and marker in text:
        return text.rsplit(marker, 1)[1]
    return None


def parse_added_lines(diff_text: str) -> dict[str, dict[int, str]]:
    added: dict[str, dict[int, str]] = {}
    diff_file: str | None = None
    saw_matching_plus = False
    current_file: str | None = None
    new_line: int | None = None
    in_hunk = False
    last_consumed_by_file: dict[str, int] = {}

    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            diff_file = parse_diff_git_new_path(line)
            saw_matching_plus = False
            current_file = None
            new_line = None
            in_hunk = False
            continue

        if not in_hunk and line.startswith("+++ "):
            target = _header_path_field(line[4:])
            if target == "/dev/null":
                current_file = None
                saw_matching_plus = True
            elif target.startswith("b/"):
                plus_file = target[2:]
                if diff_file is not None and plus_file != diff_file:
                    raise DiffError(f"diff: +++ b/{plus_file} does not match diff --git b/{diff_file}")
                current_file = plus_file
                added.setdefault(current_file, {})
                saw_matching_plus = True
            else:
                raise DiffError(f"diff: unsupported +++ header {target!r}; expected b/<path>")
            new_line = None
            continue

        match = HUNK_RE.match(line)
        if match:
            if not saw_matching_plus:
                target = diff_file if diff_file is not None else "<unknown>"
                raise DiffError(f"diff: hunk for {target} lacks matching +++ b/{target} header")
            if current_file is not None and new_line is not None:
                last_consumed_by_file[current_file] = max(last_consumed_by_file.get(current_file, 0), new_line - 1)
            new_line = int(match.group(1))
            in_hunk = True
            if current_file is not None and new_line <= last_consumed_by_file.get(current_file, 0):
                raise DiffError(
                    f"diff: non-monotonic overlapping hunk for {current_file}; new start {new_line} "
                    f"does not exceed previous consumed line {last_consumed_by_file[current_file]}"
                )
            continue

        if current_file is None or new_line is None:
            continue

        if line.startswith("+"):
            added.setdefault(current_file, {})[new_line] = line[1:]
            new_line += 1
        elif line.startswith(" ") or line == "":
            new_line += 1
        elif line.startswith("-") or line.startswith("\\"):
            continue

    return added


def git_run(
    args: list[str],
    *,
    repo_root: Path = ROOT,
    env: dict[str, str] | None = None,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=False,
        env=env,
        input=input_text,
    )


def resolve_commit(ref: str, repo_root: Path = ROOT) -> tuple[str | None, str | None]:
    proc = git_run(["rev-parse", "--verify", f"{ref}^{{commit}}"], repo_root=repo_root)
    if proc.returncode != 0:
        return None, proc.stderr.strip()
    return proc.stdout.strip(), None


def is_ancestor(ancestor: str, descendant: str, repo_root: Path = ROOT) -> bool:
    return git_run(["merge-base", "--is-ancestor", ancestor, descendant], repo_root=repo_root).returncode == 0


def merge_base(left: str, right: str, repo_root: Path = ROOT) -> tuple[str | None, str | None]:
    proc = git_run(["merge-base", left, right], repo_root=repo_root)
    if proc.returncode != 0:
        return None, proc.stderr.strip()
    return proc.stdout.strip(), None


def tracked_profile_cards(head: str = "HEAD", repo_root: Path = ROOT) -> set[str]:
    proc = git_run(["ls-files", ".agent-org/knowledge/ui/*.md"], repo_root=repo_root)
    if proc.returncode != 0:
        return set()
    cards: set[str] = set()
    for raw_path in proc.stdout.splitlines():
        path = Path(raw_path)
        if path.name == "README.md":
            continue
        exists_at_head = git_run(["cat-file", "-e", f"{head}:{raw_path}"], repo_root=repo_root).returncode == 0
        if exists_at_head:
            cards.add(path.stem)
    return cards


def validate_authorized_profile(profile_id: str, objective_profiles: set[str], card_profiles: set[str], label: str) -> list[str]:
    if profile_id not in objective_profiles and profile_id not in card_profiles:
        return [f"{label}: unauthorized uncarded profile {profile_id!r}"]
    if profile_id not in objective_profiles:
        return [f"{label}: unauthorized profile {profile_id!r}; not listed in objective.authorized_profiles"]
    if profile_id not in card_profiles:
        return [f"{label}: unauthorized uncarded profile {profile_id!r}; tracked profile card is missing"]
    return []


def evidence_kind(entry: dict[str, object]) -> str:
    kind = entry.get("kind")
    if isinstance(kind, str) and kind.strip():
        return kind
    return "implementation_evidence"


def git_diff_text(base: str | None, head: str, protected_ref: str = "main", repo_root: Path = ROOT) -> tuple[str, list[str]]:
    errors: list[str] = []
    head_oid, head_error = resolve_commit(head, repo_root)
    if head_oid is None:
        return "", [f"git diff: cannot resolve head {head!r}: {head_error}"]

    protected_oid, _protected_error = resolve_commit(protected_ref, repo_root)
    independent_protected = protected_oid is not None and protected_oid != head_oid
    fallback_base = f"{head_oid}^"
    expected_fallback_oid: str | None = None

    if not independent_protected:
        expected_fallback_oid, fallback_error = resolve_commit(fallback_base, repo_root)
        if expected_fallback_oid is None:
            return "", [*errors, f"git diff: cannot resolve fallback base {fallback_base!r}: {fallback_error}"]

    if base is None:
        if independent_protected:
            expected_base, merge_error = merge_base(head_oid, protected_oid, repo_root)
            if expected_base is None:
                return "", [f"git diff: cannot compute merge-base for {head} and {protected_ref}: {merge_error}"]
            base = expected_base
        else:
            base = fallback_base
    base_oid, base_error = resolve_commit(base, repo_root)
    if base_oid is None:
        return "", [*errors, f"git diff: cannot resolve base {base!r}: {base_error}"]

    if independent_protected:
        if not is_ancestor(protected_oid, head_oid, repo_root):
            errors.append(f"git diff: protected ref {protected_ref} is not an ancestor of head {head}")
        expected_base, merge_error = merge_base(head_oid, protected_oid, repo_root)
        if expected_base is None:
            errors.append(f"git diff: cannot compute merge-base for {head} and {protected_ref}: {merge_error}")
        elif base_oid != expected_base:
            errors.append(
                f"git diff: base {base} must equal merge-base(head, protected) {expected_base}"
            )
    elif base_oid != expected_fallback_oid:
        errors.append(f"git diff: base {base} must equal single-commit fallback base {fallback_base}")

    if not is_ancestor(base_oid, head_oid, repo_root) and base_oid != head_oid:
        errors.append(f"git diff: base {base} is not an ancestor of head {head}")
    if errors:
        return "", errors

    proc = git_run(["-c", "core.quotepath=false", "diff", f"{base_oid}..{head_oid}"], repo_root=repo_root)
    if proc.returncode != 0:
        errors.append(f"git diff: failed for {base_oid}..{head_oid}: {proc.stderr.strip()}")
    return proc.stdout, errors


def diff_path_allowed(diff_path: Path) -> bool:
    try:
        diff_path.resolve().relative_to(FIXTURE_DIR.resolve())
    except ValueError:
        return False
    return True


def validate_fixture_diff(diff_path: Path) -> list[str]:
    if not diff_path_allowed(diff_path):
        return [f"diff: caller-supplied --diff is only allowed for profile-evidence self-test fixtures: {diff_path}"]
    proc = subprocess.run(
        ["git", "apply", "--check", str(diff_path)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        return [f"diff: fixture patch is not git apply --check clean: {proc.stderr.strip()}"]
    return []


def list_strings(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def list_ints(value: object) -> list[int]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, int) and not isinstance(item, bool)]


CODE_KEYWORD_RE = re.compile(
    r"\b(def|class|if|elif|else|for|while|try|except|finally|with|return|raise|assert|import|from|yield|await|match|case|pass|del|global|nonlocal)\b"
)
ASSIGNMENT_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\b\s*(?::=|[+\-*/%|&^]?=(?!=))")
CALL_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?\s*\(")


def is_bare_string_literal(stripped: str) -> bool:
    candidate = stripped[:-1].rstrip() if stripped.endswith(",") else stripped
    try:
        parsed = ast.parse(candidate, mode="eval")
    except SyntaxError:
        return False
    return is_string_literal_expr(parsed.body)


def is_string_literal_expr(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant) and isinstance(node.value, (str, bytes)):
        return True
    if isinstance(node, ast.JoinedStr):
        return True
    if isinstance(node, ast.Tuple) and len(node.elts) == 1:
        return is_string_literal_expr(node.elts[0])
    return False


def is_assignable_lvalue(node: ast.AST) -> bool:
    if isinstance(node, (ast.Name, ast.Attribute, ast.Subscript)):
        return True
    if isinstance(node, (ast.Tuple, ast.List)):
        return all(is_assignable_lvalue(item) for item in node.elts)
    return False


def _add_node_line(lines: set[int], node: ast.AST) -> None:
    line = getattr(node, "lineno", None)
    if isinstance(line, int):
        lines.add(line)


def _expr_has_executable_structure(node: ast.AST) -> bool:
    if isinstance(node, (ast.Constant, ast.JoinedStr, ast.Name)):
        return False
    return any(isinstance(child, (ast.Call, ast.Await, ast.Yield, ast.YieldFrom, ast.NamedExpr)) for child in ast.walk(node))


def python_code_structure_lines(source: str) -> set[int]:
    try:
        parsed = ast.parse(source)
    except SyntaxError:
        return set()

    lines: set[int] = set()
    for node in ast.walk(parsed):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            _add_node_line(lines, node)
            for decorator in node.decorator_list:
                _add_node_line(lines, decorator)
            continue
        if isinstance(node, (ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try, ast.With, ast.AsyncWith, ast.Match)):
            _add_node_line(lines, node)
            continue
        if isinstance(node, (ast.Return, ast.Raise, ast.Assert, ast.Import, ast.ImportFrom)):
            _add_node_line(lines, node)
            continue
        if isinstance(node, (ast.Pass, ast.Break, ast.Continue, ast.Delete, ast.Global, ast.Nonlocal)):
            _add_node_line(lines, node)
            continue
        if isinstance(node, ast.Assign) and any(is_assignable_lvalue(target) for target in node.targets):
            _add_node_line(lines, node)
            continue
        if isinstance(node, ast.AnnAssign) and node.value is not None and is_assignable_lvalue(node.target):
            _add_node_line(lines, node)
            continue
        if isinstance(node, ast.AugAssign) and is_assignable_lvalue(node.target):
            _add_node_line(lines, node)
            continue
        if isinstance(node, ast.NamedExpr) and is_assignable_lvalue(node.target):
            _add_node_line(lines, node)
            continue
        if isinstance(node, ast.Expr) and _expr_has_executable_structure(node.value):
            _add_node_line(lines, node)
    return lines


def string_stripped_source(source: str) -> str:
    pieces: list[str] = []
    try:
        tokens = tokenize.generate_tokens(io.StringIO(source).readline)
        for token in tokens:
            if token.type in {tokenize.ENCODING, tokenize.ENDMARKER}:
                continue
            if token.type in {tokenize.STRING, tokenize.COMMENT}:
                pieces.append(" ")
            else:
                pieces.append(token.string)
    except tokenize.TokenError:
        return source
    return " ".join(pieces)


def has_lexical_code_structure(added_text: str) -> bool:
    stripped = added_text.strip()
    if not stripped or stripped.startswith("#") or is_bare_string_literal(stripped):
        return False
    code_only = string_stripped_source(stripped)
    return bool(CODE_KEYWORD_RE.search(code_only) or ASSIGNMENT_RE.search(code_only) or CALL_RE.search(code_only))


def load_new_file_content(path: str, diff_text: str, head: str, repo_root: Path) -> str | None:
    proc = git_run(["show", f"{head}:{path}"], repo_root=repo_root)
    if proc.returncode == 0:
        return proc.stdout
    return materialize_patch_files(diff_text).get(path)


def code_structure_lines_by_path(
    added_lines: dict[str, dict[int, str]],
    diff_text: str,
    head: str,
    repo_root: Path,
) -> dict[str, set[int]]:
    structured: dict[str, set[int]] = {}
    for path, lines in added_lines.items():
        if path.endswith(".py"):
            content = load_new_file_content(path, diff_text, head, repo_root)
            structured[path] = python_code_structure_lines(content) if content is not None else set()
        else:
            structured[path] = {line_no for line_no, text in lines.items() if has_lexical_code_structure(text)}
    return structured


def criterion_texts(criteria: list[object], refs: list[int], label: str, errors: list[str]) -> str:
    texts: list[str] = []
    if not refs:
        errors.append(f"{label}: acceptance_criteria_refs must cite at least one criterion")
    for ref in refs:
        if ref < 0 or ref >= len(criteria):
            errors.append(f"{label}: acceptance_criteria_refs index out of range: {ref}")
        elif isinstance(criteria[ref], str):
            texts.append(criteria[ref])
        else:
            errors.append(f"{label}: cited criterion {ref} must be a string")
    return " ".join(texts)


def implementation_evidence(evidence_doc: object) -> list[object]:
    if isinstance(evidence_doc, dict):
        value = evidence_doc.get("implementation_evidence")
        if isinstance(value, list):
            return value
    if isinstance(evidence_doc, list):
        return evidence_doc
    return []


def validate_documents(
    objective: object,
    contract: object,
    evidence_doc: object,
    diff_text: str,
    proposal: object | None,
    diff_errors: list[str] | None = None,
    head: str = "HEAD",
    repo_root: Path = ROOT,
) -> list[str]:
    errors: list[str] = []
    if diff_errors:
        errors.extend(diff_errors)
    if not isinstance(objective, dict):
        return ["objective: expected object"]
    if not isinstance(contract, dict):
        return ["contract: expected object"]

    objective_profiles = set(list_strings(objective.get("authorized_profiles")))
    card_profiles = tracked_profile_cards(head, repo_root)
    if not objective_profiles:
        errors.append("objective.authorized_profiles: must list at least one authorized profile")

    if isinstance(proposal, dict):
        for profile_id in list_strings(proposal.get("selected_profiles")):
            errors.extend(validate_authorized_profile(profile_id, objective_profiles, card_profiles, "proposal.selected_profiles"))

    criteria = contract.get("acceptance_criteria")
    if not isinstance(criteria, list):
        criteria = []
        errors.append("contract.acceptance_criteria: expected array")

    applications = contract.get("profile_applications")
    if not isinstance(applications, list):
        applications = []
        errors.append("contract.profile_applications: expected array")

    app_by_profile: dict[str, list[dict[str, object]]] = {}
    known_obligations: dict[str, set[str]] = {}

    for index, app in enumerate(applications):
        label = f"contract.profile_applications[{index}]"
        if not isinstance(app, dict):
            errors.append(f"{label}: expected object")
            continue
        profile_id = app.get("profile_id")
        if not isinstance(profile_id, str):
            errors.append(f"{label}.profile_id: expected string")
            continue
        errors.extend(validate_authorized_profile(profile_id, objective_profiles, card_profiles, f"{label}.profile_id"))

        refs = list_ints(app.get("acceptance_criteria_refs"))
        criterion_text = criterion_texts(criteria, refs, label, errors)
        obligations = list_strings(app.get("contract_obligations"))
        required_evidence = list_strings(app.get("required_evidence"))
        if not obligations:
            errors.append(f"{label}.contract_obligations: must include at least one obligation")
        if not required_evidence:
            errors.append(f"{label}.required_evidence: must include at least one required evidence kind")
        for obligation in obligations:
            errors.extend(obligation_errors(obligation, criterion_text, f"{label}.contract_obligations"))
            known_obligations.setdefault(profile_id, set()).add(obligation)
        app_by_profile.setdefault(profile_id, []).append({"refs": refs, "criterion_text": criterion_text, "required_evidence": required_evidence})

    try:
        added_lines = parse_added_lines(diff_text)
    except DiffError as exc:
        added_lines = {}
        errors.append(str(exc))
    if not added_lines:
        errors.append("diff: no added lines found")
    structured_lines = code_structure_lines_by_path(added_lines, diff_text, head, repo_root) if added_lines else {}

    evidence_entries = implementation_evidence(evidence_doc)
    if not evidence_entries:
        errors.append("implementation_evidence: must include at least one entry")

    backed_refs: dict[tuple[str, str], set[str]] = {}
    ref_owners: dict[str, tuple[str, str]] = {}
    ref_kinds: dict[str, str] = {}
    satisfied_kinds: dict[str, set[str]] = {}

    for index, entry in enumerate(evidence_entries):
        label = f"implementation_evidence[{index}]"
        entry_errors: list[str] = []
        if not isinstance(entry, dict):
            errors.append(f"{label}: expected object")
            continue
        profile_id = entry.get("profile_id")
        if not isinstance(profile_id, str):
            errors.append(f"{label}.profile_id: expected string")
            continue
        entry_errors.extend(validate_authorized_profile(profile_id, objective_profiles, card_profiles, f"{label}.profile_id"))

        obligation = entry.get("obligation")
        profile_apps = app_by_profile.get(profile_id, [])
        criterion_text = " ".join(str(app.get("criterion_text", "")) for app in profile_apps)
        entry_errors.extend(obligation_errors(obligation, criterion_text, f"{label}.obligation"))
        if isinstance(obligation, str) and obligation not in known_obligations.get(profile_id, set()):
            entry_errors.append(f"{label}.obligation: not declared in contract profile_applications for {profile_id!r}")

        ref = entry.get("evidence_ref")
        parsed_ref = parse_ref(ref, label)
        if parsed_ref is None:
            entry_errors.append(f"{label}.evidence_ref: unresolved evidence_ref {ref!r}")
            errors.extend(entry_errors)
            continue
        path, line = parsed_ref
        if path not in added_lines:
            entry_errors.append(f"{label}.evidence_ref: unresolved evidence_ref {path}:{line}; file is not changed in diff")
        elif line not in added_lines[path]:
            entry_errors.append(
                f"{label}.evidence_ref: {path}:{line} is outside added-line range in diff"
            )
        else:
            if line not in structured_lines.get(path, set()):
                entry_errors.append(f"{label}.evidence_ref: {path}:{line} is not an added code-structure line")

        ref_key = f"{path}:{line}"
        kind = evidence_kind(entry)
        previous_kind = ref_kinds.get(ref_key)
        if previous_kind is not None and previous_kind != kind:
            entry_errors.append(
                f"{label}.evidence_ref: {ref_key} cannot satisfy distinct required_evidence kinds "
                f"{previous_kind!r} and {kind!r}"
            )
        if isinstance(obligation, str):
            owner = (profile_id, obligation)
            previous_owner = ref_owners.get(ref_key)
            if previous_owner is not None and previous_owner != owner:
                entry_errors.append(f"{label}.evidence_ref: {ref_key} is reused for a distinct obligation")
            elif not entry_errors:
                ref_owners[ref_key] = owner
                ref_kinds[ref_key] = kind
                backed_refs.setdefault(owner, set()).add(ref_key)
                satisfied_kinds.setdefault(profile_id, set()).add(kind)
        errors.extend(entry_errors)

    for profile_id, obligations in known_obligations.items():
        for obligation in obligations:
            if not backed_refs.get((profile_id, obligation)):
                errors.append(f"contract obligation {obligation!r} for profile {profile_id!r} has no backing evidence")

    for profile_id, apps in app_by_profile.items():
        for app_index, app in enumerate(apps):
            for required_kind in app.get("required_evidence", []):
                if isinstance(required_kind, str) and required_kind not in satisfied_kinds.get(profile_id, set()):
                    errors.append(f"profile {profile_id!r} required_evidence {required_kind!r} not satisfied")

    return errors


def validate_paths(
    objective_path: Path,
    contract_path: Path,
    evidence_path: Path,
    diff_path: Path | None = None,
    proposal_path: Path | None = None,
    base: str | None = None,
    head: str = "HEAD",
    *,
    allow_fixture_diff: bool = False,
    protected_ref: str = "main",
    repo_root: Path = ROOT,
) -> list[str]:
    objective = load_json(objective_path)
    contract = load_json(contract_path)
    evidence_doc = load_json(evidence_path)
    diff_errors: list[str] = []
    if diff_path is not None and allow_fixture_diff:
        diff_text = diff_path.read_text(encoding="utf-8")
        diff_errors = validate_fixture_diff(diff_path)
    else:
        diff_text, diff_errors = git_diff_text(base, head, protected_ref, repo_root)
        if diff_path is not None:
            diff_errors.append("diff: --diff is only honored under --self-test; real runs use git-bound diff")
    proposal = load_json(proposal_path) if proposal_path is not None else None
    return validate_documents(objective, contract, evidence_doc, diff_text, proposal, diff_errors, head=head, repo_root=repo_root)


SELF_TESTS = {
    "honest-rate-limiter": (True, []),
    "angle9-polarity": (True, []),
    "forged-objective": (False, ["unauthorized uncarded profile"]),
    "forged-diff": (False, ["fixture patch is not git apply --check clean"]),
    "angle8-misattrib": (False, ["lacks matching +++"]),
    "tokenstuff-comment": (False, ["not an added code-structure line"]),
    "angle6-degenerate": (False, ["not an added code-structure line"]),
    "single-quote-string": (False, ["not an added code-structure line"]),
    "cjk-path": (True, []),
    "structural-lvalues": (True, []),
    "overlapping-hunk": (False, ["non-monotonic overlapping hunk"]),
    "partial-coverage": (False, ["has no backing evidence"]),
    "required-evidence-unmet": (False, ["required_evidence 'security_review_report' not satisfied"]),
    "forged-security-kind": (False, ["cannot satisfy distinct required_evidence kinds"]),
}


def materialize_patch_files(diff_text: str) -> dict[str, str]:
    files: dict[str, list[str]] = {}
    current_file: str | None = None
    in_hunk = False
    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            current_file = None
            in_hunk = False
            continue
        if not in_hunk and line.startswith("+++ "):
            target = _header_path_field(line[4:])
            if target.startswith("b/"):
                current_file = target[2:]
                files.setdefault(current_file, [])
            else:
                current_file = None
            continue
        if HUNK_RE.match(line):
            in_hunk = True
            continue
        if current_file is None or not in_hunk:
            continue
        if line.startswith("+"):
            files[current_file].append(line[1:])
        elif line.startswith(" "):
            files[current_file].append(line[1:])
    return {path: "\n".join(lines) + ("\n" if lines else "") for path, lines in files.items()}


def run_fixture_through_git(case_name: str, proposal_path: Path | None) -> list[str]:
    diff_path = rel_fixture(case_name, "diff.patch")
    fixture_errors = validate_fixture_diff(diff_path)
    if fixture_errors:
        return fixture_errors

    with tempfile.TemporaryDirectory(prefix="profile-evidence-git-") as tmp:
        repo_root = Path(tmp)
        for args in (
            ["init"],
            ["config", "user.email", "profile-evidence@example.invalid"],
            ["config", "user.name", "Profile Evidence Self Test"],
        ):
            proc = git_run(args, repo_root=repo_root)
            if proc.returncode != 0:
                return [f"{case_name}: git {' '.join(args)} failed: {proc.stderr.strip()}"]

        card_proc = git_run(["ls-files", ".agent-org/knowledge/ui/*.md"])
        if card_proc.returncode != 0:
            return [f"{case_name}: git ls-files profile cards failed: {card_proc.stderr.strip()}"]
        for raw_path in card_proc.stdout.splitlines():
            source = ROOT / raw_path
            target = repo_root / raw_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

        add_cards = git_run(["add", ".agent-org/knowledge/ui"], repo_root=repo_root)
        if add_cards.returncode != 0:
            return [f"{case_name}: git add profile cards failed: {add_cards.stderr.strip()}"]
        base_commit = git_run(["commit", "-m", "base profile cards"], repo_root=repo_root)
        if base_commit.returncode != 0:
            return [f"{case_name}: git commit base failed: {base_commit.stderr.strip()}"]
        base_proc = git_run(["rev-parse", "HEAD"], repo_root=repo_root)
        if base_proc.returncode != 0:
            return [f"{case_name}: git rev-parse base failed: {base_proc.stderr.strip()}"]
        base = base_proc.stdout.strip()

        for path, content in materialize_patch_files(diff_path.read_text(encoding="utf-8")).items():
            target = repo_root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        add_patch = git_run(["add", "."], repo_root=repo_root)
        if add_patch.returncode != 0:
            return [f"{case_name}: git add fixture files failed: {add_patch.stderr.strip()}"]
        head_commit = git_run(["commit", "-m", f"profile evidence fixture {case_name}"], repo_root=repo_root)
        if head_commit.returncode != 0:
            return [f"{case_name}: git commit fixture failed: {head_commit.stderr.strip()}"]
        head_proc = git_run(["rev-parse", "HEAD"], repo_root=repo_root)
        if head_proc.returncode != 0:
            return [f"{case_name}: git rev-parse head failed: {head_proc.stderr.strip()}"]
        head = head_proc.stdout.strip()

        return validate_paths(
            rel_fixture(case_name, "objective.json"),
            rel_fixture(case_name, "contract.json"),
            rel_fixture(case_name, "evidence.json"),
            None,
            proposal_path,
            base,
            head,
            protected_ref=head,
            repo_root=repo_root,
        )


def write_temp_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def run_range_binding_self_test() -> list[str]:
    with tempfile.TemporaryDirectory(prefix="profile-evidence-range-") as tmp:
        repo_root = Path(tmp)
        for args in (
            ["init"],
            ["checkout", "-b", "main"],
            ["config", "user.email", "profile-evidence@example.invalid"],
            ["config", "user.name", "Profile Evidence Self Test"],
        ):
            proc = git_run(args, repo_root=repo_root)
            if proc.returncode != 0:
                return [f"range-binding: git {' '.join(args)} failed: {proc.stderr.strip()}"]

        card_proc = git_run(["ls-files", ".agent-org/knowledge/ui/*.md"])
        if card_proc.returncode != 0:
            return [f"range-binding: git ls-files profile cards failed: {card_proc.stderr.strip()}"]
        for raw_path in card_proc.stdout.splitlines():
            source = ROOT / raw_path
            target = repo_root / raw_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

        add_cards = git_run(["add", ".agent-org/knowledge/ui"], repo_root=repo_root)
        if add_cards.returncode != 0:
            return [f"range-binding: git add profile cards failed: {add_cards.stderr.strip()}"]
        base_commit = git_run(["commit", "-m", "base profile cards"], repo_root=repo_root)
        if base_commit.returncode != 0:
            return [f"range-binding: git commit base failed: {base_commit.stderr.strip()}"]
        ancestor_proc = git_run(["rev-parse", "HEAD"], repo_root=repo_root)
        if ancestor_proc.returncode != 0:
            return [f"range-binding: git rev-parse ancestor failed: {ancestor_proc.stderr.strip()}"]
        older_ancestor = ancestor_proc.stdout.strip()

        prior_path = repo_root / "tmp_profile_evidence" / "prior.py"
        prior_path.parent.mkdir(parents=True, exist_ok=True)
        prior_path.write_text("def prior_guard():\n    return True\n", encoding="utf-8")
        add_prior = git_run(["add", "tmp_profile_evidence/prior.py"], repo_root=repo_root)
        if add_prior.returncode != 0:
            return [f"range-binding: git add prior failed: {add_prior.stderr.strip()}"]
        protected_commit = git_run(["commit", "-m", "protected prior code"], repo_root=repo_root)
        if protected_commit.returncode != 0:
            return [f"range-binding: git commit protected failed: {protected_commit.stderr.strip()}"]
        protected_proc = git_run(["rev-parse", "HEAD"], repo_root=repo_root)
        if protected_proc.returncode != 0:
            return [f"range-binding: git rev-parse protected failed: {protected_proc.stderr.strip()}"]
        protected_base = protected_proc.stdout.strip()

        feature_branch = git_run(["checkout", "-b", "feature"], repo_root=repo_root)
        if feature_branch.returncode != 0:
            return [f"range-binding: git checkout feature failed: {feature_branch.stderr.strip()}"]
        pr_path = repo_root / "tmp_profile_evidence" / "pr.py"
        pr_path.write_text("def pr_guard():\n    return True\n", encoding="utf-8")
        add_pr = git_run(["add", "tmp_profile_evidence/pr.py"], repo_root=repo_root)
        if add_pr.returncode != 0:
            return [f"range-binding: git add pr failed: {add_pr.stderr.strip()}"]
        pr_commit = git_run(["commit", "-m", "feature evidence"], repo_root=repo_root)
        if pr_commit.returncode != 0:
            return [f"range-binding: git commit feature failed: {pr_commit.stderr.strip()}"]
        head_proc = git_run(["rev-parse", "HEAD"], repo_root=repo_root)
        if head_proc.returncode != 0:
            return [f"range-binding: git rev-parse head failed: {head_proc.stderr.strip()}"]
        head = head_proc.stdout.strip()

        objective_path = repo_root / "objective.json"
        contract_path = repo_root / "contract.json"
        attack_evidence_path = repo_root / "attack-evidence.json"
        honest_evidence_path = repo_root / "honest-evidence.json"
        objective = {"authorized_profiles": ["ui-information-design"]}
        contract = {
            "acceptance_criteria": ["The implementation adds a profile evidence code line."],
            "profile_applications": [
                {
                    "profile_id": "ui-information-design",
                    "source_proposal": "conservative",
                    "contract_obligations": ["Add a profile evidence code line"],
                    "acceptance_criteria_refs": [0],
                    "required_evidence": ["implementation_evidence"],
                }
            ],
        }
        write_temp_json(objective_path, objective)
        write_temp_json(contract_path, contract)
        write_temp_json(
            attack_evidence_path,
            {
                "implementation_evidence": [
                    {
                        "profile_id": "ui-information-design",
                        "obligation": "Add a profile evidence code line",
                        "evidence_ref": "tmp_profile_evidence/prior.py:1",
                        "verification": "python3 scripts/profile-evidence-check.py --self-test",
                    }
                ]
            },
        )
        write_temp_json(
            honest_evidence_path,
            {
                "implementation_evidence": [
                    {
                        "profile_id": "ui-information-design",
                        "obligation": "Add a profile evidence code line",
                        "evidence_ref": "tmp_profile_evidence/pr.py:1",
                        "verification": "python3 scripts/profile-evidence-check.py --self-test",
                    }
                ]
            },
        )

        attack_errors = validate_paths(
            objective_path,
            contract_path,
            attack_evidence_path,
            None,
            None,
            older_ancestor,
            head,
            protected_ref="main",
            repo_root=repo_root,
        )
        if not any("must equal merge-base(head, protected)" in error for error in attack_errors):
            return [f"range-binding: widened base was not rejected with merge-base binding: {attack_errors}"]

        honest_errors = validate_paths(
            objective_path,
            contract_path,
            honest_evidence_path,
            None,
            None,
            protected_base,
            head,
            protected_ref="main",
            repo_root=repo_root,
        )
        if honest_errors:
            return [f"range-binding: honest protected range rejected: {honest_errors}"]
    return []


def run_real_git_probe(
    case_name: str,
    files: dict[str, str],
    evidence_refs: list[tuple[str, int]],
) -> list[str]:
    with tempfile.TemporaryDirectory(prefix=f"profile-evidence-{case_name}-") as tmp:
        repo_root = Path(tmp)
        for args in (
            ["init"],
            ["config", "user.email", "profile-evidence@example.invalid"],
            ["config", "user.name", "Profile Evidence Self Test"],
        ):
            proc = git_run(args, repo_root=repo_root)
            if proc.returncode != 0:
                return [f"{case_name}: git {' '.join(args)} failed: {proc.stderr.strip()}"]

        card_proc = git_run(["ls-files", ".agent-org/knowledge/ui/*.md"])
        if card_proc.returncode != 0:
            return [f"{case_name}: git ls-files profile cards failed: {card_proc.stderr.strip()}"]
        for raw_path in card_proc.stdout.splitlines():
            source = ROOT / raw_path
            target = repo_root / raw_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

        add_cards = git_run(["add", ".agent-org/knowledge/ui"], repo_root=repo_root)
        if add_cards.returncode != 0:
            return [f"{case_name}: git add profile cards failed: {add_cards.stderr.strip()}"]
        base_commit = git_run(["commit", "-m", "base profile cards"], repo_root=repo_root)
        if base_commit.returncode != 0:
            return [f"{case_name}: git commit base failed: {base_commit.stderr.strip()}"]
        base_proc = git_run(["rev-parse", "HEAD"], repo_root=repo_root)
        if base_proc.returncode != 0:
            return [f"{case_name}: git rev-parse base failed: {base_proc.stderr.strip()}"]
        base = base_proc.stdout.strip()

        for raw_path, content in files.items():
            target = repo_root / raw_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        add_files = git_run(["add", "."], repo_root=repo_root)
        if add_files.returncode != 0:
            return [f"{case_name}: git add files failed: {add_files.stderr.strip()}"]
        head_commit = git_run(["commit", "-m", f"profile evidence probe {case_name}"], repo_root=repo_root)
        if head_commit.returncode != 0:
            return [f"{case_name}: git commit probe failed: {head_commit.stderr.strip()}"]
        head_proc = git_run(["rev-parse", "HEAD"], repo_root=repo_root)
        if head_proc.returncode != 0:
            return [f"{case_name}: git rev-parse head failed: {head_proc.stderr.strip()}"]
        head = head_proc.stdout.strip()

        objective_path = repo_root / "objective.json"
        contract_path = repo_root / "contract.json"
        evidence_path = repo_root / "evidence.json"
        obligations = [f"{case_name} obligation {index + 1}" for index in range(len(evidence_refs))]
        write_temp_json(objective_path, {"authorized_profiles": ["ui-information-design"]})
        write_temp_json(
            contract_path,
            {
                "acceptance_criteria": obligations,
                "profile_applications": [
                    {
                        "profile_id": "ui-information-design",
                        "source_proposal": "conservative",
                        "contract_obligations": obligations,
                        "acceptance_criteria_refs": list(range(len(obligations))),
                        "required_evidence": ["implementation_evidence"],
                    }
                ],
            },
        )
        write_temp_json(
            evidence_path,
            {
                "implementation_evidence": [
                    {
                        "profile_id": "ui-information-design",
                        "obligation": obligation,
                        "evidence_ref": f"{path}:{line}",
                        "verification": "python3 scripts/profile-evidence-check.py --self-test",
                    }
                    for obligation, (path, line) in zip(obligations, evidence_refs)
                ]
            },
        )

        return validate_paths(
            objective_path,
            contract_path,
            evidence_path,
            None,
            None,
            base,
            head,
            protected_ref=head,
            repo_root=repo_root,
        )


def run_parser_ast_self_test() -> list[str]:
    probes: list[tuple[str, dict[str, str], list[tuple[str, int]], bool, list[str]]] = [
        (
            "valueless-annassign",
            {"tmp_profile_evidence/ann.py": "x: int\n"},
            [("tmp_profile_evidence/ann.py", 1)],
            False,
            ["not an added code-structure line"],
        ),
        (
            "multiline-string-interior",
            {
                "tmp_profile_evidence/story.py": (
                    "def story():\n"
                    "    text = \"\"\"\n"
                    "    raise RuntimeError\n"
                    "    \"\"\"\n"
                    "    return text\n"
                )
            },
            [("tmp_profile_evidence/story.py", 3)],
            False,
            ["not an added code-structure line"],
        ),
        (
            "plus-prefixed-added-line",
            {"tmp_profile_evidence/plus.py": "def bump(count):\n    ++count\n    return count\n"},
            [("tmp_profile_evidence/plus.py", 3)],
            True,
            [],
        ),
        (
            "space-path",
            {"tmp_profile_evidence/p q.py": "def path_with_space():\n    return True\n"},
            [("tmp_profile_evidence/p q.py", 1)],
            True,
            [],
        ),
        (
            "decorator-del-global-aug-ann",
            {
                "tmp_profile_evidence/structures.py": (
                    "import functools\n"
                    "\n"
                    "@functools.cache\n"
                    "def cached():\n"
                    "    return 1\n"
                    "\n"
                    "def mutate(items, mapping):\n"
                    "    global total\n"
                    "    del mapping['k']\n"
                    "    items[0] += 1\n"
                    "    n: int = 1\n"
                    "    return n\n"
                )
            },
            [
                ("tmp_profile_evidence/structures.py", 3),
                ("tmp_profile_evidence/structures.py", 8),
                ("tmp_profile_evidence/structures.py", 9),
                ("tmp_profile_evidence/structures.py", 10),
                ("tmp_profile_evidence/structures.py", 11),
            ],
            True,
            [],
        ),
        (
            "cjk-real-path",
            {"tmp_profile_evidence/証拠.py": "def cjk_evidence():\n    return True\n"},
            [("tmp_profile_evidence/証拠.py", 1)],
            True,
            [],
        ),
        (
            "cjk-forged-path",
            {"tmp_profile_evidence/証拠.py": "def cjk_evidence():\n    return True\n"},
            [("tmp_profile_evidence/不存在.py", 1)],
            False,
            ["file is not changed in diff"],
        ),
    ]

    failures: list[str] = []
    for name, files, refs, should_pass, expected_messages in probes:
        errors = run_real_git_probe(name, files, refs)
        passed = not errors
        joined = "\n".join(errors)
        if passed != should_pass:
            failures.append(f"{name}: expected {'PASS' if should_pass else 'REJECT'}, got {'PASS' if passed else 'REJECT'}: {errors}")
            continue
        for expected in expected_messages:
            if expected not in joined:
                failures.append(f"{name}: missing expected error substring {expected!r}: {errors}")
    return failures


def run_self_test() -> int:
    print(
        "Boundary: verifies provenance and structure only; semantic satisfaction is delegated to adversarial Linon review."
    )
    failures: list[str] = []
    parser_ast_errors = run_parser_ast_self_test()
    if parser_ast_errors:
        failures.extend(parser_ast_errors)
        print("parser-ast-real-git: REJECT", file=sys.stderr)
    else:
        print("parser-ast-real-git: PASS")
    range_errors = run_range_binding_self_test()
    if range_errors:
        failures.extend(range_errors)
        print("range-binding: REJECT", file=sys.stderr)
    else:
        print("range-binding: PASS")
    fixture_parser_cases = {"angle8-misattrib", "overlapping-hunk"}
    for case_name, (should_pass, expected_messages) in SELF_TESTS.items():
        proposal_path = rel_fixture(case_name, "proposal.json")
        if case_name in fixture_parser_cases:
            errors = validate_paths(
                rel_fixture(case_name, "objective.json"),
                rel_fixture(case_name, "contract.json"),
                rel_fixture(case_name, "evidence.json"),
                rel_fixture(case_name, "diff.patch"),
                proposal_path if proposal_path.is_file() else None,
                allow_fixture_diff=True,
            )
        else:
            errors = run_fixture_through_git(case_name, proposal_path if proposal_path.is_file() else None)
        passed = not errors
        joined = "\n".join(errors)
        if passed != should_pass:
            failures.append(f"{case_name}: expected {'PASS' if should_pass else 'REJECT'}, got {'PASS' if passed else 'REJECT'}: {errors}")
            continue
        for expected in expected_messages:
            if expected not in joined:
                failures.append(f"{case_name}: missing expected error substring {expected!r}: {errors}")
        print(f"{case_name}: {'PASS' if passed else 'REJECT'}")

    if failures:
        for failure in failures:
            print(failure, file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    boundary = (
        "This gate verifies provenance and structure only (authorized git-tracked profile, "
        "git-bound diff, every obligation backed by a distinct evidence_ref resolving to a real "
        "added code line, required-evidence kinds present). It does NOT judge whether the cited "
        "code semantically satisfies the obligation -- that is delegated to adversarial Linon review."
    )
    parser = argparse.ArgumentParser(description=boundary)
    parser.add_argument("--objective")
    parser.add_argument("--contract")
    parser.add_argument("--evidence")
    parser.add_argument("--diff")
    parser.add_argument("--base")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--proposal")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)

    if args.self_test:
        return run_self_test()

    required = [args.objective, args.contract, args.evidence]
    if any(value is None for value in required):
        parser.error("--objective, --contract, and --evidence are required unless --self-test is used")

    errors = validate_paths(
        Path(args.objective),
        Path(args.contract),
        Path(args.evidence),
        Path(args.diff) if args.diff else None,
        Path(args.proposal) if args.proposal else None,
        args.base,
        args.head,
    )
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print(f"profile evidence check passed. {boundary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
