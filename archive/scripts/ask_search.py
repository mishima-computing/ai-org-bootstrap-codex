#!/usr/bin/env python3
"""Search-then-confirm support for underdetermined asks.

The searcher proposes candidates from bounded repo-owned sources. The provenance
kernel below is the trusted part: a candidate is kept only when it cites a
retrieved source ref exactly and quotes text that is actually in that source.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any


class GhError(Exception):
    def __init__(self, message: str, returncode: int | None = None):
        self.message = message
        self.returncode = returncode
        super().__init__(message)


def run_gh(args: list[str], timeout: float = 30.0) -> str:
    if shutil.which("gh") is None:
        raise GhError("gh CLI is not available")
    try:
        result = subprocess.run(
            ["gh", *args],
            check=False,
            capture_output=True,
            text=True, encoding="utf-8", errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise GhError(f"gh command timed out: gh {' '.join(args)}") from exc
    except OSError as exc:
        raise GhError(f"gh command failed to start: {exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        first_line = detail[0] if detail else "no gh error output"
        raise GhError(f"gh {' '.join(args)} failed: {first_line}", result.returncode)
    return result.stdout


def gh_json_value(args: list[str], timeout: float = 30.0) -> Any:
    output = run_gh(args, timeout=timeout)
    try:
        return json.loads(output)
    except json.JSONDecodeError as exc:
        raise GhError(f"gh {' '.join(args)} returned non-JSON output: {exc}") from exc


def gh_json(args: list[str], timeout: float = 30.0) -> dict[str, Any]:
    parsed = gh_json_value(args, timeout=timeout)
    if not isinstance(parsed, dict):
        raise GhError(f"gh {' '.join(args)} returned JSON {type(parsed).__name__}, expected object")
    return parsed


def _emit(emit, event: dict) -> None:
    if callable(emit):
        emit(event)


def _rel(repo: Path, path: Path) -> str:
    try:
        return path.relative_to(repo).as_posix()
    except ValueError:
        return path.as_posix()


def _excerpt(text: str, terms: list[str], max_chars: int = 700) -> str:
    blocks = [b.strip() for b in re.split(r"\n\s*\n", text or "") if b.strip()]
    low_terms = [t.lower() for t in terms if t]
    for block in blocks:
        lower = block.lower()
        if any(t in lower for t in low_terms):
            return block[:max_chars]
    return (blocks[0] if blocks else (text or ""))[:max_chars]


def assemble_tier0_corpus(repo: str, missing: list, objective: str = "") -> list[dict]:
    """Read docs/decisions/*.md plus docs/**/*.md directly; do not rely on path-citation ADR lookup."""
    root = Path(repo)
    docs = root / "docs"
    if not docs.is_dir():
        return []
    paths: list[Path] = []
    paths.extend(sorted((docs / "decisions").glob("*.md")) if (docs / "decisions").is_dir() else [])
    paths.extend(sorted(docs.glob("**/*.md")))
    seen: set[str] = set()
    terms = [str(m) for m in (missing or [])] + re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", objective or "")[:12]
    passages: list[dict] = []
    for path in paths:
        if not path.is_file():
            continue
        ref = _rel(root, path)
        if ref in seen:
            continue
        seen.add(ref)
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        passages.append({
            "kind": "adr" if "/decisions/" in f"/{ref}" else "doc",
            "ref": ref,
            "url": ref,
            "text": text,
            "excerpt": _excerpt(text, terms),
        })
    return passages


def _infer_repo_query(repo: str) -> str | None:
    cp = subprocess.run(["git", "-C", str(repo), "config", "--get", "remote.origin.url"],
                        capture_output=True, text=True)
    remote = (cp.stdout or "").strip()
    m = re.search(r"[:/]([^/:]+/[^/]+?)(?:\.git)?$", remote)
    if m:
        return f"repo:{m.group(1)}"
    return None


def assemble_tier1_corpus(repo: str, missing: list, objective: str = "", emit=None) -> list[dict]:
    if os.environ.get("AOB_ASK_SEARCH_GH", "1").strip().lower() in {"0", "false", "no", "off"}:
        return []
    scope = _infer_repo_query(repo)
    if not scope:
        return []
    query_terms = " ".join(str(m) for m in (missing or []) if str(m).strip()) or "ADR decision"
    query = f"{query_terms} {scope}"
    try:
        out = run_gh(["search", "code", query, "--limit", "10", "--json", "path,repository,textMatches"], timeout=15)
        parsed = json.loads(out)
    except Exception as exc:  # noqa: BLE001 - GitHub is fail-soft
        _emit(emit, {"type": "ask_search_tier_failed", "tier": "gh", "error": str(exc)[:300]})
        return []
    passages: list[dict] = []
    for item in parsed if isinstance(parsed, list) else []:
        repo_name = ((item.get("repository") or {}).get("nameWithOwner")
                     if isinstance(item.get("repository"), dict) else None)
        path = item.get("path")
        matches = item.get("textMatches") if isinstance(item.get("textMatches"), list) else []
        fragments = [m.get("fragment", "") for m in matches if isinstance(m, dict) and m.get("fragment")]
        text = "\n\n".join(fragments).strip()
        if not repo_name or not path or not text:
            continue
        ref = f"github:{repo_name}/{path}"
        passages.append({"kind": "github", "ref": ref,
                         "url": f"https://github.com/{repo_name}/blob/HEAD/{path}",
                         "text": text, "excerpt": _excerpt(text, [str(m) for m in missing or []])})
    return passages


def propose_candidates(missing: list, structured: dict | None, objective: str, passages: list[dict]) -> list[dict]:
    """Deterministic fallback proposer over the bounded corpus.

    It intentionally keeps the value equal to a real excerpt. A later carrier-backed proposer can be added
    without weakening the provenance kernel, because this function's output is still validated below.
    """
    candidates: list[dict] = []
    for field in [str(m) for m in (missing or []) if str(m).strip()]:
        field_l = field.lower()
        for passage in passages:
            text = passage.get("text") or ""
            chunks = [c.strip() for c in re.split(r"\n\s*\n|(?<=[.!?])\s+", text) if c.strip()]
            matching = [c for c in chunks if field_l in c.lower()]
            if not matching:
                continue
            value = matching[0][:500]
            candidates.append({"field": field, "value": value, "source_ref": passage.get("ref"),
                               "url": passage.get("url"), "excerpt": value,
                               "confidence": "high" if passage.get("kind") == "adr" else "low"})
            break
    return candidates


def validate_candidates(raw_candidates: list[dict], passages: list[dict], *, node_id: str, emit=None) -> list[dict]:
    by_ref = {p.get("ref"): p for p in passages if p.get("ref")}
    kept: list[dict] = []
    for raw in raw_candidates or []:
        field = raw.get("field")
        ref = raw.get("source_ref")
        value = str(raw.get("value") or "").strip()
        excerpt = str(raw.get("excerpt") or "").strip()
        passage = by_ref.get(ref)
        reason = None
        if not value:
            reason = "empty_value"
        elif not passage:
            reason = "bad_provenance"
        elif not excerpt or excerpt not in str(passage.get("text") or ""):
            reason = "no_match"
        if reason:
            _emit(emit, {"type": "ask_candidate_rejected", "node_id": node_id,
                         "field": field, "source_ref": ref, "reason": reason})
            continue
        kept.append({"field": field, "value": value, "source_ref": ref,
                     "url": raw.get("url") or passage.get("url"),
                     "excerpt": excerpt, "confidence": raw.get("confidence") or "low"})
        _emit(emit, {"type": "ask_candidate_found", "node_id": node_id, "field": field,
                     "source_ref": ref, "confidence": raw.get("confidence") or "low"})
    return kept


def _classify(kept: list[dict]) -> dict:
    by_field: dict[str, list[dict]] = {}
    for cand in kept:
        by_field.setdefault(str(cand.get("field")), []).append(cand)
    conflicts = []
    clear = []
    for field, cands in by_field.items():
        distinct = []
        seen = set()
        for cand in cands:
            key = (cand.get("value"), cand.get("source_ref"))
            if key not in seen:
                seen.add(key)
                distinct.append(cand)
        values = {c.get("value") for c in distinct}
        if len(values) > 1:
            conflicts.append({"field": field, "candidates": distinct})
        elif distinct:
            clear.append(distinct[0])
    return {"candidates": clear, "conflicts": conflicts}


def search_candidates(repo: str, node_id: str, missing: list, structured: dict | None,
                      objective: str = "", *, emit=None, enabled: bool = True) -> dict:
    if not enabled:
        return {"candidates": [], "conflicts": []}
    _emit(emit, {"type": "ask_search_started", "node_id": node_id, "missing": list(missing or []),
                 "tiers": ["local_docs", "github"]})
    passages = assemble_tier0_corpus(repo, missing, objective)
    raw = propose_candidates(missing, structured, objective, passages)
    kept = validate_candidates(raw, passages, node_id=node_id, emit=emit)
    classified = _classify(kept)
    if classified["candidates"] or classified["conflicts"]:
        return classified
    gh_passages = assemble_tier1_corpus(repo, missing, objective, emit=emit)
    gh_raw = propose_candidates(missing, structured, objective, gh_passages)
    return _classify(validate_candidates(gh_raw, gh_passages, node_id=node_id, emit=emit))
