from __future__ import annotations

import ast
import base64
import json
import subprocess
from pathlib import Path

import pytest

from ai_org import reference


def test_fetch_candidates_uses_derived_repo_search_not_literal_term(monkeypatch):
    calls = []
    literal = "hit points implementation"

    monkeypatch.setattr(
        reference,
        "_codex_search_keywords",
        lambda term, context: ["turn-based combat system", "rpg battle system"],
    )
    monkeypatch.setattr(
        reference,
        "_codex_extract_pattern",
        lambda term, context, repo, path, content: {
            "relevant": True,
            "snippet": "hp = max(0, min(max_hp, hp - damage)); if hp == 0: enter_ko_state()",
            "summary": "Clamp damage into a valid health range and transition at zero HP.",
            "lang_env_version": "Python 3.12",
            "pitfalls": "Preserve KO transitions when healing or applying overkill damage.",
        },
    )

    def fake_run_gh(cmd):
        calls.append(cmd)
        if cmd[:3] == ["gh", "search", "repos"]:
            assert cmd[3] != literal
            assert "code" not in cmd
            stdout = json.dumps([{"fullName": "studio/rpg", "url": "https://github.com/studio/rpg"}])
            return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")
        if cmd[:2] == ["gh", "api"] and "git/trees" in cmd[2]:
            stdout = json.dumps({"tree": [{"type": "blob", "path": "src/combat/health.py"}]})
            return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")
        if cmd[:2] == ["gh", "api"] and "contents" in cmd[2]:
            encoded = base64.b64encode(b"class Combatant:\n    def take_damage(self, damage): ...").decode()
            return subprocess.CompletedProcess(cmd, 0, stdout=encoded, stderr="")
        raise AssertionError(f"unexpected gh command: {cmd}")

    monkeypatch.setattr(reference, "_run_gh", fake_run_gh)

    candidates = reference.fetch_candidates(literal, {"language": "Python", "version": "3.12"})

    search_calls = [cmd for cmd in calls if cmd[:3] == ["gh", "search", "repos"]]
    assert search_calls
    assert all(cmd[3] in {"turn-based combat system", "rpg battle system"} for cmd in search_calls)
    assert all(literal not in " ".join(cmd) for cmd in calls)
    assert candidates[0]["source_url"] == "https://github.com/studio/rpg/blob/HEAD/src/combat/health.py"
    assert "Clamp damage" in candidates[0]["summary"]


def test_search_repositories_does_not_hard_filter_context_language(monkeypatch):
    calls = []

    def fake_run_gh(cmd):
        calls.append(cmd)
        assert "--language" not in cmd
        stdout = json.dumps(
            [
                {
                    "fullName": "spongehammer/UnityTurnBasedCombatSystem",
                    "url": "https://github.com/spongehammer/UnityTurnBasedCombatSystem",
                    "description": "Unity turn based combat system",
                    "primaryLanguage": {"name": "C#"},
                    "stargazersCount": 33,
                },
                {
                    "fullName": "example/idiom-learning-program",
                    "url": "https://github.com/example/idiom-learning-program",
                    "description": "Unrelated language exercise",
                    "primaryLanguage": {"name": "JavaScript"},
                    "stargazersCount": 0,
                },
            ]
        )
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(reference, "_run_gh", fake_run_gh)

    repos = reference._search_repositories(["turn based combat system"], {"language": "JavaScript"})

    assert calls
    assert repos[0]["fullName"] == "spongehammer/UnityTurnBasedCombatSystem"
    assert repos[0]["primaryLanguage"] == "C#"
    assert repos[0]["stargazersCount"] == 33


def test_search_repositories_retries_gh_language_field_when_primary_language_is_unsupported(monkeypatch):
    calls = []

    def fake_run_gh(cmd):
        calls.append(cmd)
        json_fields = cmd[cmd.index("--json") + 1]
        if "primaryLanguage" in json_fields:
            return subprocess.CompletedProcess(
                cmd,
                1,
                stdout="",
                stderr='Unknown JSON field: "primaryLanguage"',
            )
        stdout = json.dumps(
            [
                {
                    "fullName": "bitbrain/godot4_turn_based_combat_system",
                    "url": "https://github.com/bitbrain/godot4_turn_based_combat_system",
                    "description": "Godot 4 turn based combat system",
                    "language": "GDScript",
                    "stargazersCount": "21",
                }
            ]
        )
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(reference, "_run_gh", fake_run_gh)

    repos = reference._search_repositories(["turn based combat system"], {"language": "JavaScript"})

    assert len(calls) == 2
    assert "primaryLanguage" in calls[0][calls[0].index("--json") + 1]
    assert calls[1][calls[1].index("--json") + 1] == "fullName,url,description,language,stargazersCount"
    assert "--language" not in calls[1]
    assert repos == [
        {
            "fullName": "bitbrain/godot4_turn_based_combat_system",
            "url": "https://github.com/bitbrain/godot4_turn_based_combat_system",
            "description": "Godot 4 turn based combat system",
            "language": "GDScript",
            "stargazersCount": 21,
            "primaryLanguage": "GDScript",
        }
    ]


def test_search_repositories_continues_after_keyword_query_error(monkeypatch):
    calls = []

    def fake_run_gh(cmd):
        calls.append(cmd)
        if cmd[3] == "broken query":
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="api error")
        stdout = json.dumps([{"fullName": "quality/Turn-Based-Combat", "url": "https://github.com/quality/Turn-Based-Combat"}])
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(reference, "_run_gh", fake_run_gh)

    repos = reference._search_repositories(["broken query", "turn based combat system"], {"language": "JavaScript"})

    assert [cmd[3] for cmd in calls] == ["broken query", "turn based combat system"]
    assert repos[0]["fullName"] == "quality/Turn-Based-Combat"


def test_fetch_candidates_reads_cross_language_files_and_records_actual_language(monkeypatch):
    monkeypatch.setattr(reference, "_codex_search_keywords", lambda term, context: ["turn based combat system"])
    monkeypatch.setattr(
        reference,
        "_codex_extract_pattern",
        lambda term, context, repo, path, content: {
            "relevant": True,
            "snippet": "TurnManager advances activeUnit, resolves actions, then checks victory conditions.",
            "summary": "Separates turn order from combat action resolution.",
            "lang_env_version": "",
            "pitfalls": "Keep state transitions explicit when porting.",
        },
    )

    def fake_run_gh(cmd):
        if cmd[:3] == ["gh", "search", "repos"]:
            assert "--language" not in cmd
            stdout = json.dumps(
                [
                    {
                        "fullName": "spongehammer/UnityTurnBasedCombatSystem",
                        "url": "https://github.com/spongehammer/UnityTurnBasedCombatSystem",
                        "primaryLanguage": {"name": "C#"},
                        "stargazersCount": 33,
                    }
                ]
            )
            return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")
        if cmd[:2] == ["gh", "api"] and "git/trees" in cmd[2]:
            stdout = json.dumps({"tree": [{"type": "blob", "path": "Assets/Scripts/TurnBasedCombat.cs"}]})
            return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")
        if cmd[:2] == ["gh", "api"] and "contents" in cmd[2]:
            encoded = base64.b64encode(b"public sealed class TurnManager { }").decode()
            return subprocess.CompletedProcess(cmd, 0, stdout=encoded, stderr="")
        raise AssertionError(f"unexpected gh command: {cmd}")

    monkeypatch.setattr(reference, "_run_gh", fake_run_gh)

    candidates = reference.fetch_candidates("turn manager", {"language": "JavaScript", "version": "ES2022"})

    assert candidates[0]["source_url"].endswith("/blob/HEAD/Assets/Scripts/TurnBasedCombat.cs")
    assert candidates[0]["lang_env_version"] == "C#"


def test_expand_drops_baseline_equivalent_candidates_with_honest_note(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(tmp_path / "store"))
    monkeypatch.setattr(reference, "_codex_baseline", lambda term, context: "hp -= damage; if hp <= 0: dead = True")
    monkeypatch.setattr(reference, "_codex_search_keywords", lambda term, context: ["turn based combat system"])
    monkeypatch.setattr(
        reference,
        "fetch_candidates",
        lambda term, context: [
            {
                "snippet": "health = health - damage; if health <= 0: alive = False",
                "summary": "Subtracts damage and marks death.",
                "source_url": "https://github.com/example/simple/blob/HEAD/combat.py",
                "lang_env_version": "Python 3.12",
                "pitfalls": "No special edge cases.",
            }
        ],
    )
    monkeypatch.setattr(
        reference,
        "_codex_delta_inclusion",
        lambda term, context, baseline, candidate: {"keep": False, "reason": "baseline-equivalent"},
    )

    entry = reference.expand("hit points implementation", {"language": "Python", "version": "3.12"})

    assert entry["candidates"] == []
    assert entry["search_keywords"] == ["turn based combat system"]
    assert entry["examined"] == [
        {
            "repo": "example/simple",
            "language": "Python 3.12",
            "outcome": "rejected-baseline-equivalent",
        }
    ]
    assert entry["notes"] == "baseline-sufficient-nothing-added: baseline already sufficient; nothing valuable to add."


def test_expand_keeps_only_genuine_delta_and_distills_real_fields(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(tmp_path / "store"))
    monkeypatch.setattr(reference, "_codex_baseline", lambda term, context: "hp -= damage; if hp <= 0: dead = True")
    monkeypatch.setattr(reference, "_codex_search_keywords", lambda term, context: ["turn based combat system", "rpg battle system"])
    monkeypatch.setattr(
        reference,
        "fetch_candidates",
        lambda term, context: [
            {
                "snippet": "class Health: apply raw and elemental damage through shields before clamping hp",
                "summary": "Extracted health component.",
                "source_url": "https://github.com/example/rigorous/blob/HEAD/health.py",
                "lang_env_version": "Python 3.12 turn-based RPG",
                "pitfalls": "Shield ordering matters.",
            }
        ],
    )
    monkeypatch.setattr(
        reference,
        "_codex_delta_inclusion",
        lambda term, context, baseline, candidate: {
            "keep": True,
            "reason": "handles shields, overkill clamping, and KO transition ordering missing from baseline",
        },
    )
    monkeypatch.setattr(
        reference,
        "_codex_distill_candidate",
        lambda term, context, baseline, candidate, delta_reason: {
            "snippet": "remaining = shields.absorb(damage); hp = clamp(hp - remaining, 0, max_hp); if hp == 0: set_state('ko')",
            "summary": "Applies mitigation before HP clamping and preserves a single KO transition, beating the baseline's raw subtraction.",
            "lang_env_version": "Python 3.12 turn-based RPG",
            "pitfalls": "Do not trigger KO before shields and overkill clamping have resolved.",
        },
    )
    monkeypatch.setattr(
        reference,
        "_codex_author_level",
        lambda term, context, candidate: {"author_level": "expert", "reason": "cohesive state transition handling"},
    )

    entry = reference.expand("hit points implementation", {"language": "Python", "version": "3.12"})
    read = reference.lookup("hit points implementation", {"language": "Python", "version": "3.12"})

    assert len(entry["candidates"]) == 1
    assert entry["search_keywords"] == ["turn based combat system", "rpg battle system"]
    assert entry["examined"] == [{"repo": "example/rigorous", "language": "Python 3.12 turn-based RPG", "outcome": "kept"}]
    candidate = entry["candidates"][0]
    assert "beating the baseline" in candidate["summary"]
    assert "Do not trigger KO" in candidate["pitfalls"]
    assert "clamp" in candidate["snippet"]
    assert candidate["author_level"] == "expert"
    assert read is not None
    assert read["candidates"][0]["applicability"]["matches_context"] is True
    assert reference._entry_path("hit points implementation").is_relative_to(tmp_path)
    assert not reference._entry_path("hit points implementation").is_relative_to(reference.REPO_ROOT)


def test_low_level_author_delta_is_stored_with_honest_note(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(tmp_path / "store"))
    monkeypatch.setattr(reference, "_codex_baseline", lambda term, context: "basic loop")
    monkeypatch.setattr(reference, "_codex_search_keywords", lambda term, context: ["tree depth root"])
    monkeypatch.setattr(reference, "_codex_delta_inclusion", lambda term, context, baseline, candidate: {"keep": True, "reason": "real edge case"})
    monkeypatch.setattr(
        reference,
        "_codex_distill_candidate",
        lambda term, context, baseline, candidate, delta_reason: {
            "snippet": "if node is root: return 0",
            "summary": "Handles root depth explicitly, beating the baseline that starts counting from children.",
            "lang_env_version": "Python 3.12",
            "pitfalls": "Only applies when root depth is defined as zero.",
        },
    )
    monkeypatch.setattr(reference, "_codex_author_level", lambda term, context, candidate: {"author_level": "low", "reason": "rough style"})
    monkeypatch.setattr(
        reference,
        "fetch_candidates",
        lambda term, context: [
            {
                "snippet": "if node is root: return 0",
                "summary": "Handles a root-depth edge case.",
                "source_url": "https://github.com/example/learner/blob/HEAD/tree.py",
                "lang_env_version": "Python 3.12",
                "pitfalls": "Narrow but real delta.",
            }
        ],
    )

    entry = reference.expand("tree depth root edge case", {"language": "Python", "version": "3.12"})

    assert len(entry["candidates"]) == 1
    assert entry["candidates"][0]["author_level"] == "low"
    assert entry["search_keywords"] == ["tree depth root"]
    assert entry["notes"].startswith("low-level-only:")


def test_expand_records_empty_search_keywords_when_nothing_fetched(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(tmp_path / "store"))
    monkeypatch.setattr(reference, "_codex_baseline", lambda term, context: "use a basic verifier")
    monkeypatch.setattr(reference, "_codex_search_keywords", lambda term, context: ["oauth pkce verifier"])
    monkeypatch.setattr(reference, "_search_repositories", lambda keywords, context: [])

    entry = reference.expand("PKCE verifier rotation", {"language": "TypeScript"})
    read = reference.lookup("PKCE verifier rotation", {"language": "TypeScript"})

    assert entry["candidates"] == []
    assert entry["search_keywords"] == ["oauth pkce verifier"]
    assert entry["examined"] == []
    assert entry["notes"] == "nothing-fetched: no public repository candidates were fetched."
    assert read is not None
    assert read["search_keywords"] == ["oauth pkce verifier"]


def test_expand_records_examined_repos_from_fetch_audit(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(tmp_path / "store"))
    monkeypatch.setattr(reference, "_codex_baseline", lambda term, context: "hp -= damage")
    monkeypatch.setattr(reference, "_codex_search_keywords", lambda term, context: ["turn based combat system"])
    monkeypatch.setattr(
        reference,
        "_codex_extract_pattern",
        lambda term, context, repo, path, content: {
            "relevant": True,
            "snippet": "health = health - damage",
            "summary": "Subtracts damage from health.",
            "lang_env_version": "Python 3.12",
            "pitfalls": "No special edge cases.",
        },
    )
    monkeypatch.setattr(
        reference,
        "_codex_delta_inclusion",
        lambda term, context, baseline, candidate: {"keep": False, "reason": "baseline-equivalent"},
    )

    def fake_run_gh(cmd):
        if cmd[:3] == ["gh", "search", "repos"]:
            stdout = json.dumps(
                [
                    {
                        "fullName": "example/simple",
                        "url": "https://github.com/example/simple",
                        "primaryLanguage": {"name": "Python"},
                    }
                ]
            )
            return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")
        if cmd[:2] == ["gh", "api"] and "git/trees" in cmd[2]:
            stdout = json.dumps({"tree": [{"type": "blob", "path": "combat.py"}]})
            return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")
        if cmd[:2] == ["gh", "api"] and "contents" in cmd[2]:
            encoded = base64.b64encode(b"def apply_damage(health, damage): return health - damage").decode()
            return subprocess.CompletedProcess(cmd, 0, stdout=encoded, stderr="")
        raise AssertionError(f"unexpected gh command: {cmd}")

    monkeypatch.setattr(reference, "_run_gh", fake_run_gh)

    entry = reference.expand("hit points implementation", {"language": "Python", "version": "3.12"})

    assert entry["search_keywords"] == ["turn based combat system"]
    assert entry["examined"] == [
        {
            "repo": "example/simple",
            "language": "Python",
            "outcome": "rejected-baseline-equivalent",
        }
    ]
    assert entry["notes"].startswith("baseline-sufficient")


def test_lookup_marks_applicability_from_lang_env_version_not_timestamps(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(tmp_path / "store"))
    reference._write_entry(
        {
            "term": "state update",
            "search_keywords": ["react state update"],
            "examined": [
                {"repo": "example/react", "language": "JavaScript", "outcome": "kept"},
                {"repo": "example/python", "language": "Python", "outcome": "kept"},
            ],
            "candidates": [
                {
                    "snippet": "setState(prev => prev + 1)",
                    "summary": "Functional update.",
                    "source_url": "https://github.com/example/react/blob/HEAD/state.jsx",
                    "lang_env_version": "React 18 hooks",
                    "author_level": "medium",
                    "pitfalls": "Avoid stale closures.",
                },
                {
                    "snippet": "self.value += 1",
                    "summary": "Plain Python mutation.",
                    "source_url": "https://github.com/example/python/blob/HEAD/state.py",
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
        reference.SEARCH_KEYWORDS_SCHEMA,
        reference.EXTRACT_SCHEMA,
        reference.DELTA_SCHEMA,
        reference.DISTILL_SCHEMA,
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


def test_reference_module_contains_no_japanese_text():
    text = Path(reference.__file__).read_text(encoding="utf-8")
    assert not any("\u3040" <= char <= "\u30ff" or "\u4e00" <= char <= "\u9fff" for char in text)


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
