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

ENTRY_FIELDS = ("term", "candidates", "notes")
CANDIDATE_FIELDS = (
    "snippet",
    "summary",
    "source_url",
    "lang_env_version",
    "author_level",
    "pitfalls",
)

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

DELTA_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["keep", "reason"],
    "properties": {
        "keep": {"type": "boolean"},
        "reason": {"type": "string"},
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
    fetched = fetch_candidates(term, context)

    kept: list[dict[str, Any]] = []
    low_level_count = 0
    for raw_candidate in fetched:
        normalized = _normalize_raw_candidate(raw_candidate)
        if normalized is None:
            continue

        delta = _codex_delta_inclusion(term, context, baseline, normalized)
        if not delta.get("keep"):
            continue

        author = _codex_author_level(term, context, normalized)
        author_level = _clean_author_level(author.get("author_level", "unknown"))
        normalized["author_level"] = author_level
        if _is_low_level_author(author_level):
            low_level_count += 1
        kept.append(_stored_candidate(normalized))

    notes = ""
    if kept and low_level_count == len(kept):
        notes = "Only low-level-author candidates were found; stored because each surviving candidate is a real delta."
    elif not kept and fetched:
        notes = "Fetched candidates were baseline-equivalent or invalid; no delta was stored."
    elif not fetched:
        notes = "No public repository or web candidates were fetched."

    entry = {"term": str(term), "candidates": kept, "notes": notes}
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

    This helper intentionally stays small and monkeypatchable. It uses GitHub
    CLI code search when available; tests can replace it with deterministic
    public-repo and web fixtures.
    """
    language = str((context or {}).get("language") or "").strip()
    query = str(term)
    if language:
        query = f"{query} language:{language}"

    try:
        completed = subprocess.run(
            [
                "gh",
                "search",
                "code",
                query,
                "--limit",
                "10",
                "--json",
                "path,repository,url,textMatches",
            ],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except OSError:
        return []

    if completed.returncode != 0:
        return []

    try:
        results = json.loads(completed.stdout or "[]")
    except json.JSONDecodeError:
        return []

    candidates = []
    if not isinstance(results, list):
        return candidates

    for item in results:
        if not isinstance(item, dict):
            continue
        snippet = _snippet_from_gh_item(item)
        if not snippet:
            continue
        repo = item.get("repository")
        source_url = item.get("url") or ""
        if isinstance(repo, dict) and repo.get("url"):
            source_url = str(repo["url"])
        candidates.append(
            {
                "snippet": snippet,
                "summary": "GitHub public code search candidate.",
                "source_url": source_url,
                "lang_env_version": _context_lang_env_version(context or {}),
                "pitfalls": "",
            }
        )
    return candidates


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


def _delta_prompt(term: str, context: Mapping[str, Any], baseline: str, candidate: Mapping[str, Any]) -> str:
    return (
        "Judge whether the candidate contains implementation knowledge that goes beyond the baseline. "
        "Keep only real deltas: concrete pattern, code, edge-case handling, integration technique, or "
        "ecosystem-specific rigor that the baseline would not produce unaided.\n"
        f"Term: {term}\n"
        f"Consuming stack context: {_json_for_prompt(context)}\n"
        f"Baseline:\n{baseline}\n\n"
        f"Candidate:\n{_json_for_prompt(candidate)}\n"
        "Return keep=false for baseline-equivalent prose or trivial code. Return only schema JSON."
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
    candidates = value["candidates"]
    return isinstance(candidates, list) and all(_valid_candidate(candidate) for candidate in candidates)


def _valid_candidate(value: Any) -> bool:
    if not isinstance(value, Mapping) or set(value) != set(CANDIDATE_FIELDS):
        return False
    return all(isinstance(value[field], str) for field in CANDIDATE_FIELDS)


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
