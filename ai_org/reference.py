"""Off-git implementation knowledge store for AI Org.

The reference store is org-level state, not work-repo state. Entries are
sourced from public repositories and the web, then persisted outside this repo
in a queryable SQLite database keyed by term.
"""
from __future__ import annotations

import copy
import concurrent.futures
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import tempfile
import threading
import time
from typing import Any, Mapping


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STORE_PATH = Path("~/.ai-org/reference/reference.sqlite3")

ENTRY_FIELDS = ("term", "search_keywords", "examined", "candidates", "notes")
CANDIDATE_FIELDS = (
    "snippet",
    "summary",
    "source_url",
    "lang_env_version",
    "author_level",
    "pitfalls",
    "found_via",
)
EXAMINED_FIELDS = ("repo", "language", "outcome", "found_via")
EXAMINED_OUTCOMES = {
    "kept",
    "rejected-baseline-equivalent",
    "rejected-low-value",
    "unreadable",
}
EXAMINED_OUTCOME_RANK = {
    "unreadable": 0,
    "rejected-low-value": 1,
    "rejected-baseline-equivalent": 2,
    "kept": 3,
}
SOURCE_EXTENSIONS = {
    "python": (".py",),
    "javascript": (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"),
    "typescript": (".ts", ".tsx", ".js", ".jsx"),
    "react": (".tsx", ".jsx", ".ts", ".js"),
    "java": (".java",),
    "go": (".go",),
    "rust": (".rs",),
    "ruby": (".rb",),
    "php": (".php",),
    "c#": (".cs",),
    "c++": (".cpp", ".cc", ".cxx", ".hpp", ".h"),
    "c": (".c", ".h"),
    "gdscript": (".gd",),
}
DEFAULT_SOURCE_EXTENSIONS = (
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".go",
    ".rs",
    ".java",
    ".rb",
    ".php",
    ".cs",
    ".cpp",
    ".c",
    ".h",
    ".gd",
)
EXTENSION_LANGUAGES = {
    ".py": "Python",
    ".js": "JavaScript",
    ".jsx": "JavaScript/React",
    ".ts": "TypeScript",
    ".tsx": "TypeScript/React",
    ".mjs": "JavaScript",
    ".cjs": "JavaScript",
    ".go": "Go",
    ".rs": "Rust",
    ".java": "Java",
    ".rb": "Ruby",
    ".php": "PHP",
    ".cs": "C#",
    ".cpp": "C++",
    ".cc": "C++",
    ".cxx": "C++",
    ".hpp": "C++",
    ".c": "C",
    ".h": "C/C++",
    ".gd": "GDScript",
}
MAX_REPOS = 8
MAX_FILES_PER_REPO = 3
MAX_FILE_CHARS = 24000

# Proven Codex --output-schema constraints used by the other modules: no
# allOf/anyOf/oneOf, additionalProperties false, and required lists every prop.
BASELINE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["implementation"],
    "properties": {
        "implementation": {"type": "string"},
    },
}

SEARCH_KEYWORDS_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["keywords"],
    "properties": {
        "keywords": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
}

EXTRACT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["relevant", "snippet", "summary", "lang_env_version", "pitfalls"],
    "properties": {
        "relevant": {"type": "boolean"},
        "snippet": {"type": "string"},
        "summary": {"type": "string"},
        "lang_env_version": {"type": "string"},
        "pitfalls": {"type": "string"},
    },
}

DELTA_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["keep", "reason"],
    "properties": {
        "keep": {"type": "boolean"},
        "reason": {"type": "string"},
    },
}

DISTILL_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["snippet", "summary", "lang_env_version", "pitfalls"],
    "properties": {
        "snippet": {"type": "string"},
        "summary": {"type": "string"},
        "lang_env_version": {"type": "string"},
        "pitfalls": {"type": "string"},
    },
}

AUTHOR_LEVEL_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["author_level", "reason"],
    "properties": {
        "author_level": {"type": "string"},
        "reason": {"type": "string"},
    },
}

REFERENCE_TERMS_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["terms"],
    "properties": {
        "terms": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
}

MAX_REFERENCE_TERMS = 30
REFERENCE_MAX_PARALLEL = 6
REFERENCE_SQLITE_BUSY_TIMEOUT_MS = 30000
GH_SEARCH_WINDOW_SECONDS = 60.0


def _env_int(name: str, default: int) -> int:
    try:
        requested = int(os.environ.get(name, ""))
    except ValueError:
        return default
    return requested if requested > 0 else default


GH_SEARCH_PER_MIN = _env_int("AI_ORG_GH_SEARCH_PER_MIN", 28)
GH_SEARCH_RETRY_BACKOFF_SECONDS = 2.0

_REFERENCE_DB_WRITE_LOCK = threading.Lock()
_GH_SEARCH_LOCK = threading.Lock()
_GH_SEARCH_TIMESTAMPS: list[float] = []


def lookup(term: str, context: Mapping[str, Any] | None = None) -> dict[str, Any] | None:
    """Read consumption fields for stored candidates matching the consuming stack."""
    entry = audit(term)
    if entry is None:
        return None

    return {
        "term": entry["term"],
        "candidates": [
            _consumption_candidate(candidate)
            for candidate in entry["candidates"]
            if _candidate_matches_context(candidate, context or {})
        ],
    }


def audit(term: str) -> dict[str, Any] | None:
    """Read the full maintenance record, including audit and provenance fields."""
    return _read_entry(term)


def query(filters: Mapping[str, Any] | None = None) -> list[dict[str, str]]:
    """Search stored candidates by term, applicability, author level, or provenance keyword."""
    filters = dict(filters or {})
    path = _database_path()
    if not path.exists():
        return []

    where = []
    params: list[str] = []
    term = str(filters.get("term") or "").strip()
    if term:
        where.append("lower(c.term) = lower(?)")
        params.append(term)

    author_level = str(filters.get("author_level") or "").strip()
    if author_level:
        where.append("lower(c.author_level) = lower(?)")
        params.append(author_level)

    search_values = [
        str(filters.get(field) or "").strip()
        for field in ("found_via", "keyword")
        if str(filters.get(field) or "").strip()
    ]
    if search_values:
        where.append(
            "("
            + " OR ".join(
                "(lower(c.found_via) LIKE lower(?) OR lower(r.search_keywords) LIKE lower(?))"
                for _value in search_values
            )
            + ")"
        )
        for search_value in search_values:
            like_value = f"%{search_value}%"
            params.extend([like_value, like_value])

    sql = (
        "SELECT c.term, c.snippet, c.summary, c.pitfalls, c.lang_env_version, "
        "c.author_level, c.source_url, c.found_via "
        "FROM candidates c JOIN research r ON r.term = c.term"
    )
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY lower(c.term), c.id"

    try:
        with _connect_existing_database(path) as connection:
            rows = connection.execute(sql, params).fetchall()
    except sqlite3.Error:
        return []

    lang_env_version = str(filters.get("lang_env_version") or "").strip()
    candidates = [_candidate_from_row(row, include_term=True) for row in rows]
    if lang_env_version:
        requested_tokens = _version_tokens(lang_env_version)
        candidates = [
            candidate
            for candidate in candidates
            if _lang_env_matches_filter(candidate.get("lang_env_version", ""), requested_tokens)
        ]
    return candidates


def expand(term: str, context: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Fetch, filter, annotate, and persist implementation knowledge for term."""
    context = dict(context or {})
    baseline = _codex_baseline(term, context)
    search_keywords = _clean_search_keywords(_codex_search_keywords(term, context))
    examined: list[dict[str, str]] = []
    fetched = fetch_candidates(
        term,
        {
            **context,
            "_reference_search_keywords": search_keywords,
            "_reference_examined": examined,
        },
    )

    kept: list[dict[str, Any]] = []
    low_level_count = 0
    for raw_candidate in fetched:
        audit_repo = _candidate_audit_repo(raw_candidate)
        audit_language = _candidate_audit_language(raw_candidate)
        audit_found_via = _candidate_audit_found_via(raw_candidate, search_keywords)
        if audit_repo:
            _record_examined(examined, audit_repo, audit_language, "unreadable", audit_found_via)

        normalized = _normalize_raw_candidate(raw_candidate, search_keywords)
        if normalized is None:
            if audit_repo:
                _record_examined(examined, audit_repo, audit_language, "rejected-low-value", audit_found_via)
            continue

        delta = _codex_delta_inclusion(term, context, baseline, normalized)
        if not delta.get("keep"):
            if audit_repo:
                _record_examined(
                    examined,
                    audit_repo,
                    audit_language,
                    _delta_rejection_outcome(str(delta.get("reason") or "")),
                    audit_found_via,
                )
            continue

        distilled = _codex_distill_candidate(term, context, baseline, normalized, str(delta.get("reason") or ""))
        normalized = _normalize_raw_candidate({**normalized, **distilled}, search_keywords)
        if normalized is None or not _real_distillation(normalized):
            if audit_repo:
                _record_examined(examined, audit_repo, audit_language, "rejected-low-value", audit_found_via)
            continue

        author = _codex_author_level(term, context, normalized)
        author_level = _clean_author_level(author.get("author_level", "unknown"))
        normalized["author_level"] = author_level
        if _is_low_level_author(author_level):
            low_level_count += 1
        kept.append(_stored_candidate(normalized))
        if audit_repo:
            _record_examined(examined, audit_repo, audit_language, "kept", audit_found_via)

    notes = ""
    if kept and low_level_count == len(kept):
        notes = "low-level-only: only low-level-author candidates were found; stored because each surviving candidate is a real delta."
    elif not kept and fetched:
        notes = "baseline-sufficient-nothing-added: baseline already sufficient; nothing valuable to add."
    elif not fetched:
        notes = "nothing-fetched: no public repository candidates were fetched."

    entry = {
        "term": str(term),
        "search_keywords": search_keywords,
        "examined": _clean_examined(examined),
        "candidates": kept,
        "notes": notes,
    }
    _write_entry(entry)
    return copy.deepcopy(entry)


def build_from_rfc(rfc_view: Mapping[str, Any], context: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Build reference entries for implementation-bearing terms found in an RFC-shaped view."""
    context = dict(context or {})
    text = _rfc_text(rfc_view)
    built: dict[str, Any] = {}
    expanded: list[str] = []
    hits: list[str] = []
    failed: dict[str, str] = {}

    terms = _extract_reference_terms(text, context)
    outcomes: dict[str, dict[str, Any]] = {}
    parallelism = _reference_parallelism(len(terms))
    if parallelism <= 1:
        for term in terms:
            outcomes[term] = _build_rfc_term(term, context)
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=parallelism) as executor:
            futures = {executor.submit(_build_rfc_term, term, context): term for term in terms}
            for future in concurrent.futures.as_completed(futures):
                term = futures[future]
                try:
                    outcomes[term] = future.result()
                except Exception as exc:
                    outcomes[term] = {"status": "failed", "error": _format_term_error(exc)}

    for term in terms:
        outcome = outcomes.get(term, {"status": "failed", "error": "term worker did not return"})
        if outcome["status"] == "expanded":
            built[term] = outcome["entry"]
            expanded.append(term)
        elif outcome["status"] == "hit":
            built[term] = outcome["entry"]
            hits.append(term)
        else:
            failed[term] = str(outcome.get("error") or "unknown error")

    return {
        "terms": built,
        "expanded": expanded,
        "hits": hits,
        "failed": failed,
        "dropped_generic": [],
    }


def _build_rfc_term(term: str, context: Mapping[str, Any]) -> dict[str, Any]:
    try:
        existing = lookup(term, context)
        if existing is not None:
            return {"status": "hit", "entry": existing}
        return {"status": "expanded", "entry": expand(term, context)}
    except Exception as exc:
        return {"status": "failed", "error": _format_term_error(exc)}


def _reference_parallelism(term_count: int) -> int:
    if term_count <= 1:
        return 1
    raw = os.environ.get("AI_ORG_REFERENCE_PARALLEL")
    if raw is None:
        requested = REFERENCE_MAX_PARALLEL
    else:
        try:
            requested = int(raw)
        except ValueError:
            requested = REFERENCE_MAX_PARALLEL
    return max(1, min(term_count, requested))


def _format_term_error(exc: BaseException) -> str:
    message = str(exc).strip()
    if message:
        return f"{type(exc).__name__}: {message}"
    return type(exc).__name__


def fetch_candidates(term: str, context: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    """Fetch public implementation candidates.

    This helper intentionally stays small and monkeypatchable. It derives
    broader search concepts for the target term, searches repositories, then
    reads likely implementation files and extracts the target pattern.
    """
    context = dict(context or {})
    search_keywords = context.pop("_reference_search_keywords", None)
    examined = context.pop("_reference_examined", None)
    if not isinstance(search_keywords, list):
        search_keywords = _codex_search_keywords(term, context)
    if not isinstance(examined, list):
        examined = None

    cleaned_keywords = _clean_search_keywords(search_keywords)
    candidates: list[dict[str, Any]] = []
    for repo in _search_repositories(cleaned_keywords, context):
        full_name = str(repo.get("fullName") or repo.get("nameWithOwner") or "").strip()
        if not full_name:
            continue
        primary_language = _repo_primary_language(repo)
        found_via = _clean_found_via(repo.get("found_via"), cleaned_keywords)
        if examined is not None:
            _record_examined(examined, full_name, primary_language, "unreadable", found_via)
        source_url = str(repo.get("url") or f"https://github.com/{full_name}").strip()
        read_any_file = False
        extracted_any_candidate = False
        for path in _candidate_paths_for_repo(full_name, term, context):
            content = _read_github_file(full_name, path)
            if not content:
                continue
            read_any_file = True
            extracted = _codex_extract_pattern(term, context, full_name, path, content)
            if not extracted.get("relevant"):
                continue
            snippet = str(extracted.get("snippet") or "").strip()
            if not snippet:
                continue
            extracted_any_candidate = True
            candidates.append(
                {
                    "snippet": snippet,
                    "summary": str(extracted.get("summary") or "").strip(),
                    "source_url": f"{source_url}/blob/HEAD/{path}",
                    "lang_env_version": _candidate_lang_env_version(
                        repo,
                        path,
                        str(extracted.get("lang_env_version") or "").strip(),
                        context,
                    ),
                    "pitfalls": str(extracted.get("pitfalls") or "").strip(),
                    "found_via": found_via,
                    "_reference_repo": full_name,
                    "_reference_language": primary_language,
                    "_reference_found_via": found_via,
                }
            )
            if len(candidates) >= MAX_REPOS * MAX_FILES_PER_REPO:
                return candidates
        if examined is not None and read_any_file and not extracted_any_candidate:
            _record_examined(examined, full_name, primary_language, "rejected-low-value", found_via)
    return candidates


def _codex_search_keywords(term: str, context: Mapping[str, Any]) -> list[str]:
    result = _codex_json(
        _search_keywords_prompt(term, context),
        SEARCH_KEYWORDS_SCHEMA,
        "search-keywords.json",
    )
    raw_keywords = result.get("keywords")
    if not isinstance(raw_keywords, list):
        return []
    term_key = " ".join(str(term).lower().split())
    keywords = []
    for value in raw_keywords:
        keyword = " ".join(str(value).strip().split())
        if not keyword:
            continue
        if keyword.lower() == term_key:
            continue
        keywords.append(keyword)
    return _unique(keywords)[:8]


def _search_repositories(keywords: list[str], context: Mapping[str, Any]) -> list[dict[str, Any]]:
    repos: list[dict[str, Any]] = []
    seen: set[str] = set()
    for keyword in keywords:
        cmd = [
            "gh",
            "search",
            "repos",
            keyword,
            "--limit",
            "5",
            "--json",
            "fullName,url,description,primaryLanguage,stargazersCount",
        ]
        raw_repos = _gh_search_json(cmd)
        if not isinstance(raw_repos, list):
            continue
        for repo in raw_repos:
            if not isinstance(repo, Mapping):
                continue
            normalized = _normalize_search_repo(repo)
            full_name = str(normalized.get("fullName") or "").strip()
            if not full_name or full_name.lower() in seen:
                continue
            seen.add(full_name.lower())
            normalized["found_via"] = keyword
            repos.append(normalized)
            if len(repos) >= MAX_REPOS:
                return repos
    return repos


def _normalize_search_repo(repo: Mapping[str, Any]) -> dict[str, Any]:
    normalized = dict(repo)
    full_name = str(repo.get("fullName") or repo.get("nameWithOwner") or "").strip()
    if full_name:
        normalized["fullName"] = full_name

    language = _repo_primary_language(repo)
    if language:
        normalized["primaryLanguage"] = language

    stars = _repo_stargazers_count(repo)
    if stars is not None:
        normalized["stargazersCount"] = stars
    return normalized


def _repo_primary_language(repo: Mapping[str, Any]) -> str:
    for field in ("primaryLanguage", "language"):
        value = repo.get(field)
        if isinstance(value, Mapping):
            value = value.get("name")
        language = str(value or "").strip()
        if language:
            return language
    return ""


def _repo_stargazers_count(repo: Mapping[str, Any]) -> int | None:
    for field in ("stargazersCount", "stars"):
        value = repo.get(field)
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return value
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            continue
    return None


def _candidate_paths_for_repo(repo: str, term: str, context: Mapping[str, Any]) -> list[str]:
    tree = _gh_json(["gh", "api", f"repos/{repo}/git/trees/HEAD?recursive=1"])
    items = tree.get("tree") if isinstance(tree, Mapping) else None
    if not isinstance(items, list):
        return []

    extensions = _source_extensions(context)
    scored: list[tuple[int, str]] = []
    term_tokens = _version_tokens(term)
    for item in items:
        if not isinstance(item, Mapping) or item.get("type") != "blob":
            continue
        path = str(item.get("path") or "")
        if not path or not path.lower().endswith(extensions):
            continue
        lowered = path.lower()
        score = sum(4 for token in term_tokens if token in lowered)
        score += sum(2 for word in ("combat", "battle", "health", "damage", "stats", "state", "system") if word in lowered)
        score -= sum(8 for word in ("testdata", "fixture", "vendor", "node_modules", "dist", "build") if word in lowered)
        scored.append((score, path))

    return [path for _score, path in sorted(scored, key=lambda item: (-item[0], len(item[1]), item[1]))[:MAX_FILES_PER_REPO]]


def _read_github_file(repo: str, path: str) -> str:
    completed = _run_gh(["gh", "api", f"repos/{repo}/contents/{path}", "--jq", ".content"])
    if completed is None or completed.returncode != 0:
        return ""
    encoded = "".join((completed.stdout or "").split())
    if not encoded:
        return ""
    try:
        import base64
        import binascii

        decoded = base64.b64decode(encoded, validate=False).decode("utf-8", errors="replace")
    except (ValueError, OSError, binascii.Error):
        return ""
    return decoded[:MAX_FILE_CHARS]


def _candidate_lang_env_version(
    repo: Mapping[str, Any],
    path: str,
    extracted_lang_env_version: str,
    context: Mapping[str, Any],
) -> str:
    actual_language = _repo_primary_language(repo) or _language_from_path(path)
    extracted = str(extracted_lang_env_version or "").strip()
    if actual_language and extracted:
        if actual_language.lower() in extracted.lower():
            return extracted
        return f"{actual_language} source; extracted context: {extracted}"
    if actual_language:
        return actual_language
    if extracted:
        return extracted
    return _context_lang_env_version(context)


def _language_from_path(path: str) -> str:
    suffix = Path(str(path or "")).suffix.lower()
    return EXTENSION_LANGUAGES.get(suffix, "")


def _source_extensions(context: Mapping[str, Any]) -> tuple[str, ...]:
    keys = [
        str(context.get("language") or "").strip().lower(),
        str(context.get("environment") or "").strip().lower(),
    ]
    extensions: list[str] = []
    for key in keys:
        if key in SOURCE_EXTENSIONS:
            extensions.extend(SOURCE_EXTENSIONS[key])
    extensions.extend(DEFAULT_SOURCE_EXTENSIONS)
    return tuple(_unique(extensions))


def _gh_json(cmd: list[str]) -> Any:
    result, _retryable = _gh_json_once(cmd, rate_limit_search=False)
    return result


def _gh_search_json(cmd: list[str]) -> Any:
    for attempt in range(2):
        result, retryable = _gh_json_once(cmd, rate_limit_search=True)
        if retryable and attempt == 0:
            time.sleep(GH_SEARCH_RETRY_BACKOFF_SECONDS)
            continue
        return result
    return []


def _gh_json_once(cmd: list[str], *, rate_limit_search: bool) -> tuple[Any, bool]:
    completed = _run_gh_limited(cmd, rate_limit_search=rate_limit_search)
    if completed is None:
        return [], False
    if completed.returncode != 0:
        if _is_gh_search_rate_limit(completed):
            return [], True
        fallback_cmd = _gh_json_field_fallback(cmd, completed.stderr or "")
        if fallback_cmd is None:
            return [], False
        completed = _run_gh_limited(fallback_cmd, rate_limit_search=rate_limit_search)
        if completed is None or completed.returncode != 0:
            retryable = completed is not None and _is_gh_search_rate_limit(completed)
            return [], retryable
    try:
        return json.loads(completed.stdout or "[]"), False
    except json.JSONDecodeError:
        return [], False


def _run_gh_limited(cmd: list[str], *, rate_limit_search: bool) -> subprocess.CompletedProcess[str] | None:
    if rate_limit_search and _is_gh_search_command(cmd):
        _acquire_gh_search_slot()
    return _run_gh(cmd)


def _is_gh_search_command(cmd: list[str]) -> bool:
    return len(cmd) >= 3 and cmd[0] == "gh" and cmd[1] == "search" and cmd[2] in {"repos", "code"}


def _acquire_gh_search_slot() -> None:
    while True:
        with _GH_SEARCH_LOCK:
            now = time.monotonic()
            cutoff = now - GH_SEARCH_WINDOW_SECONDS
            while _GH_SEARCH_TIMESTAMPS and _GH_SEARCH_TIMESTAMPS[0] <= cutoff:
                _GH_SEARCH_TIMESTAMPS.pop(0)

            limit = _env_int("AI_ORG_GH_SEARCH_PER_MIN", GH_SEARCH_PER_MIN)
            if len(_GH_SEARCH_TIMESTAMPS) < limit:
                _GH_SEARCH_TIMESTAMPS.append(now)
                return

            sleep_for = _GH_SEARCH_TIMESTAMPS[0] + GH_SEARCH_WINDOW_SECONDS - now

        time.sleep(max(sleep_for, 0.001))


def _is_gh_search_rate_limit(completed: subprocess.CompletedProcess[str]) -> bool:
    args = completed.args if isinstance(completed.args, list) else []
    if not _is_gh_search_command(args):
        return False
    text = f"{completed.stderr or ''}\n{completed.stdout or ''}".lower()
    return any(
        marker in text
        for marker in (
            "rate limit",
            "secondary rate",
            "too many requests",
            "http 403",
            "http 429",
            "abuse detection",
            "api rate limit exceeded",
        )
    )


def _gh_json_field_fallback(cmd: list[str], stderr: str) -> list[str] | None:
    if "Unknown JSON field" not in stderr or "--json" not in cmd:
        return None
    json_index = cmd.index("--json") + 1
    if json_index >= len(cmd):
        return None
    fields = [field.strip() for field in cmd[json_index].split(",") if field.strip()]
    replacements = {
        "primaryLanguage": "language",
        "stars": "stargazersCount",
    }
    fallback_fields = [replacements.get(field, field) for field in fields]
    if fallback_fields == fields:
        return None
    fallback_cmd = list(cmd)
    fallback_cmd[json_index] = ",".join(_unique(fallback_fields))
    return fallback_cmd


def _run_gh(cmd: list[str]) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            cmd,
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except OSError:
        return None


def _codex_baseline(term: str, context: Mapping[str, Any]) -> str:
    result = _codex_json(
        _baseline_prompt(term, context),
        BASELINE_SCHEMA,
        "baseline.json",
    )
    implementation = result.get("implementation")
    return implementation if isinstance(implementation, str) else ""


def _codex_delta_inclusion(
    term: str,
    context: Mapping[str, Any],
    baseline: str,
    candidate: Mapping[str, Any],
) -> dict[str, Any]:
    result = _codex_json(
        _delta_prompt(term, context, baseline, candidate),
        DELTA_SCHEMA,
        "delta.json",
    )
    if isinstance(result.get("keep"), bool) and isinstance(result.get("reason"), str):
        return result
    return {"keep": False, "reason": "invalid delta judgment"}


def _codex_extract_pattern(
    term: str,
    context: Mapping[str, Any],
    repo: str,
    path: str,
    content: str,
) -> dict[str, Any]:
    result = _codex_json(
        _extract_prompt(term, context, repo, path, content),
        EXTRACT_SCHEMA,
        "extract.json",
    )
    if isinstance(result.get("relevant"), bool):
        return result
    return {"relevant": False, "snippet": "", "summary": "", "lang_env_version": "", "pitfalls": ""}


def _codex_distill_candidate(
    term: str,
    context: Mapping[str, Any],
    baseline: str,
    candidate: Mapping[str, Any],
    delta_reason: str,
) -> dict[str, Any]:
    result = _codex_json(
        _distill_prompt(term, context, baseline, candidate, delta_reason),
        DISTILL_SCHEMA,
        "distill.json",
    )
    if all(isinstance(result.get(field), str) for field in DISTILL_SCHEMA["properties"]):
        return result
    return {"snippet": "", "summary": "", "lang_env_version": "", "pitfalls": ""}


def _codex_author_level(term: str, context: Mapping[str, Any], candidate: Mapping[str, Any]) -> dict[str, Any]:
    result = _codex_json(
        _author_prompt(term, context, candidate),
        AUTHOR_LEVEL_SCHEMA,
        "author-level.json",
    )
    if isinstance(result.get("author_level"), str) and isinstance(result.get("reason"), str):
        return result
    return {"author_level": "unknown", "reason": "invalid author-level judgment"}


def _extract_reference_terms(text: str, context: Mapping[str, Any]) -> list[str]:
    result = _codex_json(
        _reference_terms_prompt(text, context),
        REFERENCE_TERMS_SCHEMA,
        "reference-terms.json",
    )
    return _clean_reference_terms(result.get("terms"))


def _codex_json(prompt: str, schema: Mapping[str, Any], output_name: str) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="ai-org-reference-codex-") as tmp:
        temp_dir = Path(tmp)
        schema_file = temp_dir / "schema.json"
        out_file = temp_dir / output_name
        schema_file.write_text(json.dumps(schema), encoding="utf-8")
        cmd = [
            "codex",
            "exec",
            "--sandbox",
            "read-only",
            "--skip-git-repo-check",
            "-C",
            str(REPO_ROOT),
            "-o",
            str(out_file),
            "--output-schema",
            str(schema_file),
            prompt,
        ]
        try:
            completed = subprocess.run(
                cmd,
                check=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        except OSError:
            return {}
        if completed.returncode != 0 or not out_file.exists():
            return {}
        try:
            parsed = json.loads(out_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}


def _baseline_prompt(term: str, context: Mapping[str, Any]) -> str:
    return (
        "Describe how you would implement this term unaided, without using external references.\n"
        f"Term: {term}\n"
        f"Consuming stack context: {_json_for_prompt(context)}\n"
        "Return only JSON matching the schema. The implementation field should contain concrete code "
        "or a concrete implementation outline that reflects your baseline knowledge."
    )


def _search_keywords_prompt(term: str, context: Mapping[str, Any]) -> str:
    return (
        "Derive effective GitHub repository search keywords for finding high-quality implementations "
        "that contain this target term's implementation pattern. Do not return the literal term. Prefer "
        "related systems, mechanics, APIs, or containing features where the implementation actually lives.\n"
        f"Reference target term: {term}\n"
        f"Consuming stack context: {_json_for_prompt(context)}\n"
        "Examples: for 'hit points implementation', return concepts such as turn-based combat system, "
        "rpg battle system, character stats, damage system, health bar. Return only schema JSON."
    )


def _extract_prompt(term: str, context: Mapping[str, Any], repo: str, path: str, content: str) -> str:
    return (
        "Inspect this repository file and extract the implementation pattern for the target term. "
        "Do not return a grep fragment. Return the smallest coherent pattern that shows the relevant "
        "state, update rules, edge cases, and integration points. If the file does not implement the "
        "target term, return relevant=false.\n"
        f"Reference target term: {term}\n"
        f"Consuming stack context: {_json_for_prompt(context)}\n"
        f"Repository: {repo}\n"
        f"Path: {path}\n"
        f"File content:\n{content}\n"
        "The summary must say what the pattern does. Pitfalls must be concrete. Return only schema JSON."
    )


def _delta_prompt(term: str, context: Mapping[str, Any], baseline: str, candidate: Mapping[str, Any]) -> str:
    return (
        "Strictly judge whether the candidate contains implementation knowledge that genuinely beats "
        "the baseline. Keep only real deltas: it handles cases the baseline misses, uses a better or "
        "non-obvious technique, or is materially more robust. Different but baseline-equivalent code "
        "must be rejected.\n"
        f"Term: {term}\n"
        f"Consuming stack context: {_json_for_prompt(context)}\n"
        f"Baseline:\n{baseline}\n\n"
        f"Candidate:\n{_json_for_prompt(candidate)}\n"
        "Return keep=false for baseline-equivalent prose, trivial variations, or merely different names. "
        "Return only schema JSON."
    )


def _distill_prompt(
    term: str,
    context: Mapping[str, Any],
    baseline: str,
    candidate: Mapping[str, Any],
    delta_reason: str,
) -> str:
    return (
        "Distill this kept candidate into a stored implementation reference. The summary must be real: "
        "state what the pattern does and why it beats the baseline. The snippet must be the extracted "
        "implementation pattern, not a placeholder. Pitfalls must be concrete, including when not to use "
        "the pattern or edge cases to preserve.\n"
        f"Term: {term}\n"
        f"Consuming stack context: {_json_for_prompt(context)}\n"
        f"Baseline:\n{baseline}\n\n"
        f"Delta reason:\n{delta_reason}\n\n"
        f"Candidate:\n{_json_for_prompt(candidate)}\n"
        "Return only schema JSON."
    )


def _author_prompt(term: str, context: Mapping[str, Any], candidate: Mapping[str, Any]) -> str:
    return (
        "Estimate the source author's competence level from the candidate code itself: sophistication, "
        "rigor, edge-case handling, API boundaries, tests, and failure behavior. Do not use stars, "
        "popularity, commit activity, or social proof.\n"
        f"Term: {term}\n"
        f"Consuming stack context: {_json_for_prompt(context)}\n"
        f"Candidate:\n{_json_for_prompt(candidate)}\n"
        "Use a short author_level such as low, medium, high, expert, or unknown. Return only schema JSON."
    )


def _reference_terms_prompt(text: str, context: Mapping[str, Any]) -> str:
    return (
        "Extract the implementation-bearing reference terms from this RFC text for an implementation "
        "knowledge lookup. Return only meaningful terms worth researching: concrete implementation "
        "concepts, mechanics, systems, algorithms, protocols, APIs, data formats, framework concepts, "
        "library names, and named domain entities. Exclude generic/common words, filler phrases, "
        "non-implementation prose, feature-benefit language, and accidental adjacent-word n-grams. "
        "Prefer precise multi-word terms such as command-based turn battle system, EXP and gold leveling, "
        "save/load system, random encounter system, shop and inn economy, tilemap overworld traversal, "
        "or boss gate progression. Return at most 30 terms, ordered by implementation importance.\n"
        f"Consuming stack context: {_json_for_prompt(context)}\n"
        f"RFC text:\n{text}\n"
        "Return only schema JSON."
    )


def _write_entry(entry: Mapping[str, Any]) -> None:
    if not _valid_entry(entry):
        raise ValueError("invalid reference entry")
    path = _database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _REFERENCE_DB_WRITE_LOCK:
        with _connect_database(path) as connection:
            _ensure_schema(connection)
            with connection:
                connection.execute(
                    """
                    INSERT INTO research(term, notes, search_keywords, examined)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(term) DO UPDATE SET
                        notes = excluded.notes,
                        search_keywords = excluded.search_keywords,
                        examined = excluded.examined
                    """,
                    (
                        str(entry["term"]),
                        str(entry["notes"]),
                        json.dumps(entry["search_keywords"], sort_keys=True),
                        json.dumps(entry["examined"], sort_keys=True),
                    ),
                )
                connection.execute("DELETE FROM candidates WHERE term = ?", (str(entry["term"]),))
                connection.executemany(
                    """
                    INSERT INTO candidates(
                        term,
                        snippet,
                        summary,
                        pitfalls,
                        lang_env_version,
                        author_level,
                        source_url,
                        found_via
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            str(entry["term"]),
                            candidate["snippet"],
                            candidate["summary"],
                            candidate["pitfalls"],
                            candidate["lang_env_version"],
                            candidate["author_level"],
                            candidate["source_url"],
                            candidate["found_via"],
                        )
                        for candidate in entry["candidates"]
                    ],
                )


def _read_entry(term: str) -> dict[str, Any] | None:
    path = _database_path()
    if not path.exists():
        return None
    try:
        with _connect_existing_database(path) as connection:
            research = connection.execute(
                "SELECT term, notes, search_keywords, examined FROM research WHERE lower(term) = lower(?)",
                (str(term),),
            ).fetchone()
            if research is None:
                return None
            candidate_rows = connection.execute(
                """
                SELECT term, snippet, summary, pitfalls, lang_env_version, author_level, source_url, found_via
                FROM candidates
                WHERE lower(term) = lower(?)
                ORDER BY id
                """,
                (str(term),),
            ).fetchall()
    except (sqlite3.Error, json.JSONDecodeError):
        return None

    try:
        entry = {
            "term": str(research["term"]),
            "search_keywords": json.loads(str(research["search_keywords"] or "[]")),
            "examined": json.loads(str(research["examined"] or "[]")),
            "candidates": [_candidate_from_row(row) for row in candidate_rows],
            "notes": str(research["notes"] or ""),
        }
    except (TypeError, json.JSONDecodeError):
        return None
    if not _valid_entry(entry):
        return None
    return entry


def _connect_database(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path, timeout=REFERENCE_SQLITE_BUSY_TIMEOUT_MS / 1000)
    connection.row_factory = sqlite3.Row
    connection.execute(f"PRAGMA busy_timeout = {REFERENCE_SQLITE_BUSY_TIMEOUT_MS}")
    connection.execute("PRAGMA journal_mode = WAL")
    return connection


def _connect_existing_database(path: Path) -> sqlite3.Connection:
    connection = _connect_database(path)
    _ensure_schema(connection)
    return connection


def _ensure_schema(connection: sqlite3.Connection) -> None:
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS research (
            term TEXT PRIMARY KEY,
            notes TEXT NOT NULL,
            search_keywords TEXT NOT NULL,
            examined TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            term TEXT NOT NULL,
            snippet TEXT NOT NULL,
            summary TEXT NOT NULL,
            pitfalls TEXT NOT NULL,
            lang_env_version TEXT NOT NULL,
            author_level TEXT NOT NULL,
            source_url TEXT NOT NULL,
            found_via TEXT NOT NULL,
            FOREIGN KEY(term) REFERENCES research(term) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_reference_candidates_term ON candidates(term);
        CREATE INDEX IF NOT EXISTS idx_reference_candidates_lang_env_version ON candidates(lang_env_version);
        CREATE INDEX IF NOT EXISTS idx_reference_candidates_author_level ON candidates(author_level);
        CREATE INDEX IF NOT EXISTS idx_reference_candidates_found_via ON candidates(found_via);
        """
    )


def _database_path() -> Path:
    raw = os.environ.get("AI_ORG_REFERENCE_STORE")
    store = Path(raw).expanduser() if raw else DEFAULT_STORE_PATH.expanduser()
    resolved = store.resolve()
    if _path_is_inside(resolved, REPO_ROOT) or _path_is_inside(resolved, REPO_ROOT / ".git"):
        raise ValueError("AI Org reference store must be outside the work repo and its .git directory")
    return resolved


def _path_is_inside(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _candidate_from_row(row: Mapping[str, Any], *, include_term: bool = False) -> dict[str, str]:
    candidate = {
        "snippet": str(row["snippet"] or ""),
        "summary": str(row["summary"] or ""),
        "source_url": str(row["source_url"] or ""),
        "lang_env_version": str(row["lang_env_version"] or ""),
        "author_level": str(row["author_level"] or ""),
        "pitfalls": str(row["pitfalls"] or ""),
        "found_via": str(row["found_via"] or ""),
    }
    if include_term:
        return {"term": str(row["term"] or ""), **candidate}
    return candidate


def _consumption_candidate(candidate: Mapping[str, Any]) -> dict[str, str]:
    return {
        "snippet": str(candidate.get("snippet") or ""),
        "summary": str(candidate.get("summary") or ""),
        "pitfalls": str(candidate.get("pitfalls") or ""),
        "lang_env_version": str(candidate.get("lang_env_version") or ""),
        "author_level": str(candidate.get("author_level") or ""),
        "source_url": str(candidate.get("source_url") or ""),
    }


def _candidate_matches_context(candidate: Mapping[str, Any], context: Mapping[str, Any]) -> bool:
    if not _version_tokens(_context_lang_env_version(context)):
        return True
    return bool(_applicability(str(candidate.get("lang_env_version") or ""), context).get("matches_context"))


def _lang_env_matches_filter(lang_env_version: str, requested_tokens: set[str]) -> bool:
    if not requested_tokens:
        return True
    candidate_tokens = _version_tokens(lang_env_version)
    return requested_tokens.issubset(candidate_tokens) or bool(requested_tokens & candidate_tokens)


def _valid_entry(value: Any) -> bool:
    if not isinstance(value, Mapping) or set(value) != set(ENTRY_FIELDS):
        return False
    if not isinstance(value["term"], str):
        return False
    if not isinstance(value["notes"], str):
        return False
    if not isinstance(value["search_keywords"], list) or not all(isinstance(item, str) for item in value["search_keywords"]):
        return False
    search_keywords = _clean_search_keywords(value["search_keywords"])
    if search_keywords != value["search_keywords"]:
        return False
    if not isinstance(value["examined"], list) or not all(_valid_examined(item) for item in value["examined"]):
        return False
    keyword_set = {keyword.lower() for keyword in search_keywords}
    if any(item["found_via"].lower() not in keyword_set for item in value["examined"]):
        return False
    candidates = value["candidates"]
    return (
        isinstance(candidates, list)
        and all(_valid_candidate(candidate) for candidate in candidates)
        and all(candidate["found_via"].lower() in keyword_set for candidate in candidates)
    )


def _valid_candidate(value: Any) -> bool:
    if not isinstance(value, Mapping) or set(value) != set(CANDIDATE_FIELDS):
        return False
    return all(isinstance(value[field], str) for field in CANDIDATE_FIELDS) and bool(value["found_via"].strip())


def _valid_examined(value: Any) -> bool:
    if not isinstance(value, Mapping) or set(value) != set(EXAMINED_FIELDS):
        return False
    return (
        isinstance(value["repo"], str)
        and isinstance(value["language"], str)
        and isinstance(value["outcome"], str)
        and isinstance(value["found_via"], str)
        and value["outcome"] in EXAMINED_OUTCOMES
        and bool(value["found_via"].strip())
    )


def _normalize_raw_candidate(value: Any, search_keywords: list[str] | None = None) -> dict[str, str] | None:
    if not isinstance(value, Mapping):
        return None
    snippet = str(value.get("snippet") or "").strip()
    source_url = str(value.get("source_url") or "").strip()
    found_via = _candidate_audit_found_via(value, search_keywords or [])
    if not snippet or not source_url or not found_via:
        return None
    return {
        "snippet": snippet,
        "summary": str(value.get("summary") or "").strip(),
        "source_url": source_url,
        "lang_env_version": str(value.get("lang_env_version") or "").strip(),
        "author_level": str(value.get("author_level") or "").strip(),
        "pitfalls": str(value.get("pitfalls") or "").strip(),
        "found_via": found_via,
    }


def _real_distillation(candidate: Mapping[str, str]) -> bool:
    summary = str(candidate.get("summary") or "").strip()
    snippet = str(candidate.get("snippet") or "").strip()
    pitfalls = str(candidate.get("pitfalls") or "").strip()
    lang_env_version = str(candidate.get("lang_env_version") or "").strip()
    if not summary or not snippet or not pitfalls or not lang_env_version:
        return False
    placeholder_markers = (
        "github public code search candidate",
        "placeholder",
        "todo",
        "n/a",
        "none",
    )
    return not any(marker == summary.lower() or marker == pitfalls.lower() for marker in placeholder_markers)


def _stored_candidate(candidate: Mapping[str, str]) -> dict[str, str]:
    lang_env_version = candidate.get("lang_env_version") or ""
    return {
        "snippet": candidate.get("snippet", ""),
        "summary": candidate.get("summary", ""),
        "source_url": candidate.get("source_url", ""),
        "lang_env_version": lang_env_version,
        "author_level": candidate.get("author_level", "") or "unknown",
        "pitfalls": candidate.get("pitfalls", ""),
        "found_via": candidate.get("found_via", ""),
    }


def _clean_search_keywords(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    keywords = []
    for value in values:
        keyword = " ".join(str(value).strip().split())
        if keyword:
            keywords.append(keyword)
    return _unique(keywords)


def _clean_found_via(value: Any, search_keywords: list[str]) -> str:
    found_via = " ".join(str(value or "").strip().split())
    if not found_via:
        return search_keywords[0] if len(search_keywords) == 1 else ""
    allowed = {keyword.lower(): keyword for keyword in search_keywords}
    if allowed:
        return allowed.get(found_via.lower(), "")
    return found_via


def _record_examined(examined: list[dict[str, str]], repo: str, language: str, outcome: str, found_via: str) -> None:
    repo = str(repo or "").strip()
    if not repo or outcome not in EXAMINED_OUTCOMES:
        return
    language = str(language or "").strip()
    found_via = " ".join(str(found_via or "").strip().split())
    if not found_via:
        return
    for item in examined:
        if item.get("repo", "").lower() != repo.lower():
            continue
        if EXAMINED_OUTCOME_RANK[outcome] > EXAMINED_OUTCOME_RANK.get(item.get("outcome", ""), -1):
            item["outcome"] = outcome
        if language and not item.get("language"):
            item["language"] = language
        if not item.get("found_via"):
            item["found_via"] = found_via
        return
    examined.append({"repo": repo, "language": language, "outcome": outcome, "found_via": found_via})


def _clean_examined(values: Any) -> list[dict[str, str]]:
    if not isinstance(values, list):
        return []
    cleaned: list[dict[str, str]] = []
    for value in values:
        if not isinstance(value, Mapping):
            continue
        repo = str(value.get("repo") or "").strip()
        outcome = str(value.get("outcome") or "").strip()
        if not repo or outcome not in EXAMINED_OUTCOMES:
            continue
        found_via = " ".join(str(value.get("found_via") or "").strip().split())
        _record_examined(cleaned, repo, str(value.get("language") or "").strip(), outcome, found_via)
    return cleaned


def _candidate_audit_repo(candidate: Any) -> str:
    if not isinstance(candidate, Mapping):
        return ""
    repo = str(candidate.get("_reference_repo") or "").strip()
    if repo:
        return repo
    source_url = str(candidate.get("source_url") or "").strip()
    match = re.match(r"https://github\.com/([^/]+/[^/]+)/", source_url)
    return match.group(1) if match else ""


def _candidate_audit_language(candidate: Any) -> str:
    if not isinstance(candidate, Mapping):
        return ""
    language = str(candidate.get("_reference_language") or "").strip()
    if language:
        return language
    return str(candidate.get("lang_env_version") or "").strip()


def _candidate_audit_found_via(candidate: Any, search_keywords: list[str]) -> str:
    if not isinstance(candidate, Mapping):
        return _clean_found_via("", search_keywords)
    return _clean_found_via(candidate.get("_reference_found_via") or candidate.get("found_via"), search_keywords)


def _delta_rejection_outcome(reason: str) -> str:
    lowered = str(reason or "").lower()
    if "baseline" in lowered or "equivalent" in lowered:
        return "rejected-baseline-equivalent"
    return "rejected-low-value"


def _clean_author_level(value: str) -> str:
    normalized = str(value or "").strip().lower()
    return normalized or "unknown"


def _is_low_level_author(author_level: str) -> bool:
    normalized = author_level.strip().lower()
    return normalized in {"low", "low-level", "beginner", "novice", "weak"}


def _applicability(lang_env_version: str, context: Mapping[str, Any]) -> dict[str, Any]:
    candidate_tokens = _version_tokens(lang_env_version)
    context_value = _context_lang_env_version(context)
    context_tokens = _version_tokens(context_value)
    if not candidate_tokens or not context_tokens:
        return {
            "matches_context": False,
            "reason": "missing lang/env/version context",
        }

    shared = candidate_tokens & context_tokens
    language = str(context.get("language") or "").lower()
    version = str(context.get("version") or "").lower()
    language_ok = not language or language in candidate_tokens
    version_ok = not version or version in candidate_tokens or any(token.startswith(version) for token in candidate_tokens)
    matches = bool(shared) and language_ok and version_ok
    reason = "lang/env/version matches consuming context" if matches else "lang/env/version differs from consuming context"
    return {
        "matches_context": matches,
        "reason": reason,
    }


def _context_lang_env_version(context: Mapping[str, Any]) -> str:
    parts = [
        str(context.get("language") or "").strip(),
        str(context.get("environment") or "").strip(),
        str(context.get("version") or "").strip(),
    ]
    return " ".join(part for part in parts if part)


def _version_tokens(value: str) -> set[str]:
    return {token.lower() for token in re.findall(r"[A-Za-z0-9.#+_-]+", str(value)) if token}


def _rfc_text(value: Any) -> str:
    if isinstance(value, Mapping):
        return "\n".join(_rfc_text(item) for item in value.values())
    if isinstance(value, list):
        return "\n".join(_rfc_text(item) for item in value)
    return str(value or "")


def _clean_reference_terms(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    terms = []
    for value in values:
        if not isinstance(value, str):
            continue
        term = re.sub(r"\s+", " ", value).strip(" \t\r\n\"'`.,:;()[]{}")
        if not term or not term.isascii() or not re.search(r"[A-Za-z]", term):
            continue
        terms.append(term)
    return _unique(terms)[:MAX_REFERENCE_TERMS]


def _snippet_from_gh_item(item: Mapping[str, Any]) -> str:
    matches = item.get("textMatches")
    if isinstance(matches, list):
        fragments = []
        for match in matches:
            if isinstance(match, Mapping) and isinstance(match.get("fragment"), str):
                fragments.append(match["fragment"])
        if fragments:
            return "\n".join(fragments)
    return str(item.get("path") or "").strip()


def _json_for_prompt(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True)


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result = []
    for value in values:
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result
