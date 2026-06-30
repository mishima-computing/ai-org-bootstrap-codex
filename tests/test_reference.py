from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from ai_org import reference


def test_store_write_read_round_trip(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(tmp_path / "reference-store"))
    monkeypatch.setattr(reference, "_codex_baseline", lambda term, context: "basic dict cache")
    monkeypatch.setattr(reference, "_codex_delta_inclusion", lambda term, context, baseline, candidate: {"keep": True, "reason": "delta"})
    monkeypatch.setattr(reference, "_codex_author_level", lambda term, context, candidate: {"author_level": "high", "reason": "rigorous"})
    monkeypatch.setattr(
        reference,
        "fetch_candidates",
        lambda term, context: [
            {
                "snippet": "cache[key] = value if value is not _MISS else recompute()",
                "summary": "Sentinel-aware cache write.",
                "source_url": "https://github.com/example/repo/blob/main/cache.py",
                "lang_env_version": "Python 3.12",
                "pitfalls": "Do not conflate cached None with a miss.",
            }
        ],
    )

    entry = reference.expand("sentinel cache", {"language": "Python", "version": "3.12"})
    read = reference.lookup("sentinel cache", {"language": "Python", "version": "3.12"})

    assert entry["term"] == "sentinel cache"
    assert read is not None
    assert read["term"] == "sentinel cache"
    assert read["candidates"][0]["snippet"] == "cache[key] = value if value is not _MISS else recompute()"
    assert read["candidates"][0]["applicability"]["matches_context"] is True
    assert reference._entry_path("sentinel cache").is_relative_to(tmp_path)
    assert not reference._entry_path("sentinel cache").is_relative_to(reference.REPO_ROOT)


def test_expand_keeps_delta_candidates_and_drops_baseline_equivalent(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(tmp_path / "store"))
    monkeypatch.setattr(reference, "_codex_baseline", lambda term, context: "fetch(url).json()")
    monkeypatch.setattr(
        reference,
        "fetch_candidates",
        lambda term, context: [
            {
                "snippet": "fetch(url).json()",
                "summary": "Baseline fetch.",
                "source_url": "https://github.com/example/simple/blob/main/api.js",
                "lang_env_version": "React 18 hooks",
                "pitfalls": "",
            },
            {
                "snippet": "const ctrl = new AbortController(); useEffect(() => () => ctrl.abort(), []);",
                "summary": "Abort in-flight request on unmount.",
                "source_url": "https://github.com/example/rigorous/blob/main/api.jsx",
                "lang_env_version": "React 18 hooks",
                "pitfalls": "Do not reuse an aborted controller.",
            },
        ],
    )

    def delta(_term, _context, _baseline, candidate):
        return {"keep": "AbortController" in candidate["snippet"], "reason": "compared"}

    monkeypatch.setattr(reference, "_codex_delta_inclusion", delta)
    monkeypatch.setattr(reference, "_codex_author_level", lambda term, context, candidate: {"author_level": "expert", "reason": "cleanup"})

    entry = reference.expand("abortable fetch hook", {"language": "JavaScript", "environment": "React", "version": "18"})

    assert len(entry["candidates"]) == 1
    assert "AbortController" in entry["candidates"][0]["snippet"]
    assert entry["candidates"][0]["source_url"] == "https://github.com/example/rigorous/blob/main/api.jsx"


def test_low_level_author_delta_is_stored_with_honesty_note(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(tmp_path / "store"))
    monkeypatch.setattr(reference, "_codex_baseline", lambda term, context: "basic loop")
    monkeypatch.setattr(reference, "_codex_delta_inclusion", lambda term, context, baseline, candidate: {"keep": True, "reason": "real edge case"})
    monkeypatch.setattr(reference, "_codex_author_level", lambda term, context, candidate: {"author_level": "low", "reason": "rough style"})
    monkeypatch.setattr(
        reference,
        "fetch_candidates",
        lambda term, context: [
            {
                "snippet": "if node is root: return 0  # root-depth edge case",
                "summary": "Handles a root-depth edge case.",
                "source_url": "https://github.com/example/learner/blob/main/tree.py",
                "lang_env_version": "Python 3.12",
                "pitfalls": "Narrow but real delta.",
            }
        ],
    )

    entry = reference.expand("tree depth root edge case", {"language": "Python", "version": "3.12"})

    assert len(entry["candidates"]) == 1
    assert entry["candidates"][0]["author_level"] == "low"
    assert "Only low-level-author candidates were found" in entry["notes"]


def test_lookup_marks_applicability_from_lang_env_version(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(tmp_path / "store"))
    reference._write_entry(
        {
            "term": "state update",
            "candidates": [
                {
                    "snippet": "setState(prev => prev + 1)",
                    "summary": "Functional update.",
                    "source_url": "https://github.com/example/react/blob/main/state.jsx",
                    "lang_env_version": "React 18 hooks",
                    "author_level": "medium",
                    "pitfalls": "Avoid stale closures.",
                },
                {
                    "snippet": "self.value += 1",
                    "summary": "Plain Python mutation.",
                    "source_url": "https://github.com/example/python/blob/main/state.py",
                    "lang_env_version": "Python 3.12",
                    "author_level": "medium",
                    "pitfalls": "Not a React pattern.",
                },
            ],
            "notes": "",
        }
    )

    entry = reference.lookup("state update", {"language": "React", "environment": "hooks", "version": "18"})

    assert entry is not None
    assert entry["candidates"][0]["applicability"]["matches_context"] is True
    assert entry["candidates"][1]["applicability"]["matches_context"] is False


def test_build_from_rfc_drops_generic_terms_and_expands_only_non_generic(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(tmp_path / "store"))
    expanded = []

    def generic(term, context):
        return {"generic": term != "OAuth PKCE", "reason": "test classifier"}

    def expand(term, context):
        expanded.append(term)
        return {"term": term, "candidates": [], "notes": "expanded"}

    monkeypatch.setattr(reference, "_codex_generic_term", generic)
    monkeypatch.setattr(reference, "expand", expand)

    result = reference.build_from_rfc(
        {
            "title": "Add OAuth PKCE",
            "proposal": "Implement OAuth PKCE verifier flow.",
            "context": "This feature improves login.",
        },
        {"language": "TypeScript", "environment": "browser", "version": "ES2022"},
    )

    assert expanded == ["OAuth PKCE"]
    assert result["expanded"] == ["OAuth PKCE"]
    assert set(result["terms"]) == {"OAuth PKCE"}
    assert "Add OAuth" in result["dropped_generic"]


def test_reference_store_rejects_work_repo_paths(monkeypatch):
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(reference.REPO_ROOT / ".ai-org-reference"))

    with pytest.raises(ValueError, match="outside the work repo"):
        reference.lookup("anything", {})


def test_reference_schemas_are_codex_valid():
    for schema in [
        reference.BASELINE_SCHEMA,
        reference.DELTA_SCHEMA,
        reference.AUTHOR_LEVEL_SCHEMA,
        reference.GENERIC_TERM_SCHEMA,
    ]:
        serialized = json.dumps(schema)
        assert "allOf" not in serialized
        assert "anyOf" not in serialized
        assert "oneOf" not in serialized
        _assert_required_is_all_properties(schema)


def test_reference_imports_no_pipeline_or_archive_modules():
    tree = ast.parse(Path(reference.__file__).read_text(encoding="utf-8"))
    forbidden = {"ai_org.rfc", "ai_org.patch", "ai_org.merge", "archive"}
    imports = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)

    assert not {name for name in imports if name in forbidden or any(name.startswith(f"{item}.") for item in forbidden)}


def _assert_required_is_all_properties(schema):
    if isinstance(schema, dict):
        if schema.get("type") == "object":
            assert schema.get("additionalProperties") is False
            assert sorted(schema.get("required", [])) == sorted(schema.get("properties", {}))
        for value in schema.values():
            _assert_required_is_all_properties(value)
    elif isinstance(schema, list):
        for value in schema:
            _assert_required_is_all_properties(value)
