"""Off-git implementation knowledge store for AI Org.

The reference store is org-level state, not work-repo state. Entries are
sourced from public repositories and the web, then persisted outside this repo
as JSON files keyed by term.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Any, Mapping


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STORE_DIR = Path("~/.ai-org/reference/")

ENTRY_FIELDS = ("term", "search_keywords", "examined", "candidates", "notes")
CANDIDATE_FIELDS = (
    "snippet",
    "summary",
    "source_url",
    "lang_env_version",
    "author_level",
    "pitfalls",
)
EXAMINED_FIELDS = ("repo", "language", "outcome")
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

GENERIC_TERM_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["generic", "reason"],
    "properties": {
        "generic": {"type": "boolean"},
        "reason": {"type": "string"},
    },
}


def lookup(term: str, context: Mapping[str, Any] | None = None) -> dict[str, Any] | None:
    """Read a stored entry and mark candidate applicability for the consuming stack."""
    path = _entry_path(term)
    if not path.exists():
        return None

    try:
        entry = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    if not _valid_entry(entry):
        return None

    result = copy.deepcopy(entry)
    for candidate in result["candidates"]:
        candidate["applicability"] = _applicability(candidate.get("lang_env_version", ""), context or {})
    return result


def expand(term: str, context: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Fetch, filter, annotate, and persist implementation knowledge for term."""
    context = dict(context or {})
    baseline = _codex_baseline(term, context)
    search_keywords = _codex_search_keywords(term, context)
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
        if audit_repo:
            _record_examined(examined, audit_repo, audit_language, "unreadable")

        normalized = _normalize_raw_candidate(raw_candidate)
        if normalized is None:
            if audit_repo:
                _record_examined(examined, audit_repo, audit_language, "rejected-low-value")
            continue

        delta = _codex_delta_inclusion(term, context, baseline, normalized)
        if not delta.get("keep"):
            if audit_repo:
                _record_examined(
                    examined,
                    audit_repo,
                    audit_language,
                    _delta_rejection_outcome(str(delta.get("reason") or "")),
                )
            continue

        distilled = _codex_distill_candidate(term, context, baseline, normalized, str(delta.get("reason") or ""))
        normalized = _normalize_raw_candidate({**normalized, **distilled})
        if normalized is None or not _real_distillation(normalized):
            if audit_repo:
                _record_examined(examined, audit_repo, audit_language, "rejected-low-value")
            continue

        author = _codex_author_level(term, context, normalized)
        author_level = _clean_author_level(author.get("author_level", "unknown"))
        normalized["author_level"] = author_level
        if _is_low_level_author(author_level):
            low_level_count += 1
        kept.append(_stored_candidate(normalized))
        if audit_repo:
            _record_examined(examined, audit_repo, audit_language, "kept")

    notes = ""
    if kept and low_level_count == len(kept):
        notes = "low-level-only: only low-level-author candidates were found; stored because each surviving candidate is a real delta."
    elif not kept and fetched:
        notes = "baseline-sufficient-nothing-added: baseline already sufficient; nothing valuable to add."
    elif not fetched:
        notes = "nothing-fetched: no public repository candidates were fetched."

    entry = {
        "term": str(term),
        "search_keywords": _clean_search_keywords(search_keywords),
        "examined": _clean_examined(examined),
        "candidates": kept,
        "notes": notes,
    }
    _write_entry(entry)
    return copy.deepcopy(entry)


def build_from_rfc(rfc_view: Mapping[str, Any], context: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Build reference entries for non-generic terms found in an RFC-shaped view."""
    context = dict(context or {})
    text = _rfc_text(rfc_view)
    built: dict[str, Any] = {}
    expanded: list[str] = []
    hits: list[str] = []
    dropped: list[str] = []

    for term in _non_overlapping_terms(text, context):
        existing = lookup(term, context)
        if existing is None:
            built[term] = expand(term, context)
            expanded.append(term)
        else:
            built[term] = existing
            hits.append(term)

    for term in _candidate_terms(text):
        if term not in built:
            dropped.append(term)

    return {
        "terms": built,
        "expanded": expanded,
        "hits": hits,
        "dropped_generic": _unique(dropped),
    }


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

    candidates: list[dict[str, Any]] = []
    for repo in _search_repositories(_clean_search_keywords(search_keywords), context):
        full_name = str(repo.get("fullName") or repo.get("nameWithOwner") or "").strip()
        if not full_name:
            continue
        primary_language = _repo_primary_language(repo)
        if examined is not None:
            _record_examined(examined, full_name, primary_language, "unreadable")
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
                    "_reference_repo": full_name,
                    "_reference_language": primary_language,
                }
            )
            if len(candidates) >= MAX_REPOS * MAX_FILES_PER_REPO:
                return candidates
        if examined is not None and read_any_file and not extracted_any_candidate:
            _record_examined(examined, full_name, primary_language, "rejected-low-value")
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
        raw_repos = _gh_json(cmd)
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
    completed = _run_gh(cmd)
    if completed is None:
        return []
    if completed.returncode != 0:
        fallback_cmd = _gh_json_field_fallback(cmd, completed.stderr or "")
        if fallback_cmd is None:
            return []
        completed = _run_gh(fallback_cmd)
        if completed is None or completed.returncode != 0:
            return []
    try:
        return json.loads(completed.stdout or "[]")
    except json.JSONDecodeError:
        return []


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


def _codex_generic_term(term: str, context: Mapping[str, Any]) -> dict[str, Any]:
    result = _codex_json(
        _generic_prompt(term, context),
        GENERIC_TERM_SCHEMA,
        "generic-term.json",
    )
    if isinstance(result.get("generic"), bool) and isinstance(result.get("reason"), str):
        return result
    return {"generic": True, "reason": "invalid generic-term judgment"}


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


def _generic_prompt(term: str, context: Mapping[str, Any]) -> str:
    return (
        "Classify this RFC word or phrase. Is it a generic/common word that should be dropped from an "
        "implementation reference search, or is it a domain term, mechanism, API, protocol, proper noun, "
        "library name, algorithm, file format, framework concept, or other useful implementation term?\n"
        f"Term: {term}\n"
        f"Consuming stack context: {_json_for_prompt(context)}\n"
        "Return generic=true for common verbs, filler, ordinary product words, and broad words without "
        "implementation-specific meaning. Return only schema JSON."
    )


def _write_entry(entry: Mapping[str, Any]) -> None:
    if not _valid_entry(entry):
        raise ValueError("invalid reference entry")
    path = _entry_path(str(entry["term"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entry, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _entry_path(term: str) -> Path:
    return _store_dir() / f"{_slug(term)}.json"


def _store_dir() -> Path:
    raw = os.environ.get("AI_ORG_REFERENCE_STORE")
    store = Path(raw).expanduser() if raw else DEFAULT_STORE_DIR.expanduser()
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


def _slug(term: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", str(term).lower()).strip("-")
    digest = hashlib.sha1(str(term).encode("utf-8")).hexdigest()[:8]
    if not base:
        base = "term"
    return f"{base[:80].rstrip('-')}-{digest}"


def _valid_entry(value: Any) -> bool:
    if not isinstance(value, Mapping) or set(value) != set(ENTRY_FIELDS):
        return False
    if not isinstance(value["term"], str):
        return False
    if not isinstance(value["notes"], str):
        return False
    if not isinstance(value["search_keywords"], list) or not all(isinstance(item, str) for item in value["search_keywords"]):
        return False
    if not isinstance(value["examined"], list) or not all(_valid_examined(item) for item in value["examined"]):
        return False
    candidates = value["candidates"]
    return isinstance(candidates, list) and all(_valid_candidate(candidate) for candidate in candidates)


def _valid_candidate(value: Any) -> bool:
    if not isinstance(value, Mapping) or set(value) != set(CANDIDATE_FIELDS):
        return False
    return all(isinstance(value[field], str) for field in CANDIDATE_FIELDS)


def _valid_examined(value: Any) -> bool:
    if not isinstance(value, Mapping) or set(value) != set(EXAMINED_FIELDS):
        return False
    return (
        isinstance(value["repo"], str)
        and isinstance(value["language"], str)
        and isinstance(value["outcome"], str)
        and value["outcome"] in EXAMINED_OUTCOMES
    )


def _normalize_raw_candidate(value: Any) -> dict[str, str] | None:
    if not isinstance(value, Mapping):
        return None
    snippet = str(value.get("snippet") or "").strip()
    source_url = str(value.get("source_url") or "").strip()
    if not snippet or not source_url:
        return None
    return {
        "snippet": snippet,
        "summary": str(value.get("summary") or "").strip(),
        "source_url": source_url,
        "lang_env_version": str(value.get("lang_env_version") or "").strip(),
        "author_level": str(value.get("author_level") or "").strip(),
        "pitfalls": str(value.get("pitfalls") or "").strip(),
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


def _record_examined(examined: list[dict[str, str]], repo: str, language: str, outcome: str) -> None:
    repo = str(repo or "").strip()
    if not repo or outcome not in EXAMINED_OUTCOMES:
        return
    language = str(language or "").strip()
    for item in examined:
        if item.get("repo", "").lower() != repo.lower():
            continue
        if EXAMINED_OUTCOME_RANK[outcome] > EXAMINED_OUTCOME_RANK.get(item.get("outcome", ""), -1):
            item["outcome"] = outcome
        if language and not item.get("language"):
            item["language"] = language
        return
    examined.append({"repo": repo, "language": language, "outcome": outcome})


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
        _record_examined(cleaned, repo, str(value.get("language") or "").strip(), outcome)
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


def _non_overlapping_terms(text: str, context: Mapping[str, Any]) -> list[str]:
    tokens = _token_spans(text)
    accepted: list[tuple[int, int, str]] = []
    for start, end, term in _candidate_term_spans(tokens):
        if any(not (end <= used_start or start >= used_end) for used_start, used_end, _used in accepted):
            continue
        judgment = _codex_generic_term(term, context)
        if judgment.get("generic") is False:
            accepted.append((start, end, term))
    return [term for _start, _end, term in sorted(accepted)]


def _candidate_terms(text: str) -> list[str]:
    return [term for _start, _end, term in _candidate_term_spans(_token_spans(text))]


def _token_spans(text: str) -> list[tuple[int, int, str]]:
    return [(match.start(), match.end(), match.group(0)) for match in re.finditer(r"[A-Za-z][A-Za-z0-9_.+#-]*", text)]


def _candidate_term_spans(tokens: list[tuple[int, int, str]]) -> list[tuple[int, int, str]]:
    candidates: list[tuple[int, int, str]] = []
    seen: set[str] = set()
    for width in (3, 2, 1):
        for index in range(0, len(tokens) - width + 1):
            selected = tokens[index : index + width]
            term = " ".join(token for _start, _end, token in selected).strip()
            key = term.lower()
            if key in seen:
                continue
            seen.add(key)
            candidates.append((selected[0][0], selected[-1][1], term))
    return candidates


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
