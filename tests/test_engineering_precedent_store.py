from __future__ import annotations

import ast
import base64
import concurrent.futures
import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
import unicodedata
from pathlib import Path

import pytest

from ai_org import engineering_precedent_store


ORIGINAL_FETCH_DESIGN_CANDIDATES = engineering_precedent_store.fetch_design_candidates


@pytest.fixture(autouse=True)
def default_no_design_lane(monkeypatch, tmp_path):
    monkeypatch.delenv("AI_ORG_REFERENCE_TTL_SECONDS", raising=False)
    monkeypatch.delenv("AI_ORG_REFERENCE_FORCE", raising=False)
    monkeypatch.delenv("AI_ORG_PRECEDENT_READ_DISABLED", raising=False)
    monkeypatch.setenv("AI_ORG_GH_SEARCH_CACHE_TTL_SECONDS", "0")
    monkeypatch.setattr(
        engineering_precedent_store,
        "DEFAULT_STORE_PATH",
        tmp_path / "aiorg_assets" / "engineering_precedent_store" / "org.sqlite3",
    )
    monkeypatch.setattr(engineering_precedent_store, "fetch_design_candidates", lambda term, context: [])


def _design_candidate(
    source_url="https://example.com/design",
    delta_claim="Use leases only if crash recovery matters.",
    found_via="job queue architecture",
    evidence_class="unclassified",
):
    return {
        "kind": "design",
        "structure": "Components, responsibilities, boundaries, and flow are explicit.",
        "rationale": "The boundary keeps operational failure handling out of domain logic.",
        "when_to_use": "Use when the constraint appears in production.",
        "when_not_to_use": "Do not use for trivial local-only flows.",
        "tradeoffs": "Improves reliability while adding operational complexity.",
        "alternatives": "A simpler design was rejected because failure recovery was ambiguous.",
        "implementation_hooks": "Read implementation candidates for leases, retries, and idempotency.",
        "quality_attributes": "Reliability, operability, and testability.",
        "evidence": "Accepted ADR with production adoption evidence.",
        "delta_claim": delta_claim,
        "author_level": "unknown",
        "source_url": source_url,
        "found_via": found_via,
        "lang_env_version": "general",
        "evidence_class": evidence_class,
    }


def test_fetch_candidates_uses_derived_repo_search_not_literal_term(monkeypatch):
    calls = []
    literal = "hit points implementation"

    monkeypatch.setattr(
        engineering_precedent_store,
        "_codex_search_keywords",
        lambda term, context: ["turn-based combat system", "rpg battle system"],
    )
    monkeypatch.setattr(
        engineering_precedent_store,
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

    monkeypatch.setattr(engineering_precedent_store, "_run_gh", fake_run_gh)

    candidates = engineering_precedent_store.fetch_candidates(literal, {"language": "Python", "version": "3.12"})

    search_calls = [cmd for cmd in calls if cmd[:3] == ["gh", "search", "repos"]]
    assert search_calls
    assert all(cmd[3] in {"turn-based combat system", "rpg battle system"} for cmd in search_calls)
    assert all(literal not in " ".join(cmd) for cmd in calls)
    assert candidates[0]["source_url"] == "https://github.com/studio/rpg/blob/HEAD/src/combat/health.py"
    assert "Clamp damage" in candidates[0]["summary"]
    assert candidates[0]["found_via"] in {"turn-based combat system", "rpg battle system"}


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

    monkeypatch.setattr(engineering_precedent_store, "_run_gh", fake_run_gh)

    repos = engineering_precedent_store._search_repositories(["turn based combat system"], {"language": "JavaScript"})

    assert calls
    assert repos[0]["fullName"] == "spongehammer/UnityTurnBasedCombatSystem"
    assert repos[0]["primaryLanguage"] == "C#"
    assert repos[0]["stargazersCount"] == 33


def test_search_repositories_uses_gh_language_field_first(monkeypatch):
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

    monkeypatch.setattr(engineering_precedent_store, "_run_gh", fake_run_gh)

    repos = engineering_precedent_store._search_repositories(["turn based combat system"], {"language": "JavaScript"})

    assert len(calls) == 1
    assert calls[0][calls[0].index("--json") + 1] == "fullName,url,description,language,stargazersCount"
    assert "--language" not in calls[0]
    assert repos == [
        {
            "fullName": "bitbrain/godot4_turn_based_combat_system",
            "url": "https://github.com/bitbrain/godot4_turn_based_combat_system",
            "description": "Godot 4 turn based combat system",
            "language": "GDScript",
            "stargazersCount": 21,
            "primaryLanguage": "GDScript",
            "found_via": "turn based combat system",
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

    monkeypatch.setattr(engineering_precedent_store, "_run_gh", fake_run_gh)

    repos = engineering_precedent_store._search_repositories(["broken query", "turn based combat system"], {"language": "JavaScript"})

    assert [cmd[3] for cmd in calls] == ["broken query", "turn based combat system"]
    assert repos[0]["fullName"] == "quality/Turn-Based-Combat"


def test_github_repo_search_query_builder_splits_at_boolean_operator_cap():
    representative_keywords = [
        "turn based combat system",
        "job queue architecture",
        "alpha OR beta OR gamma OR delta OR epsilon OR zeta OR eta",
    ]

    queries = engineering_precedent_store._github_repo_search_queries(
        representative_keywords,
        refinements=engineering_precedent_store.DESIGN_REPO_SEARCH_TERMS,
    )

    assert queries
    assert all(
        engineering_precedent_store._github_search_boolean_operator_count(query) <= engineering_precedent_store.GITHUB_SEARCH_BOOLEAN_OPERATOR_LIMIT
        for query, _found_via in queries
    )
    for keyword in representative_keywords[:2]:
        emitted = [query for query, found_via in queries if found_via == keyword]
        assert emitted
        assert all(term in " ".join(emitted) for term in engineering_precedent_store.DESIGN_REPO_SEARCH_TERMS)
    split_keyword_queries = [
        query
        for query, found_via in engineering_precedent_store._github_repo_search_queries([representative_keywords[2]])
        if found_via == representative_keywords[2]
    ]
    assert len(split_keyword_queries) == 2
    assert all(
        engineering_precedent_store._github_search_boolean_operator_count(query) <= engineering_precedent_store.GITHUB_SEARCH_BOOLEAN_OPERATOR_LIMIT
        for query in split_keyword_queries
    )


def test_codex_search_keywords_broadens_overqualified_model_output(monkeypatch):
    prompts = []

    def codex_json(prompt, schema, output_name):
        prompts.append(prompt)
        assert schema == engineering_precedent_store.SEARCH_KEYWORDS_SCHEMA
        assert output_name == "search-keywords.json"
        return {
            "keywords": [
                "browser RPG persistent game state",
                "javascript RPG state serialization",
                "party data persistence",
                "save/load system",
                "save load manager",
                "rpg character roster javascript",
                "turn based combat actors javascript",
                "inventory system",
            ]
        }

    monkeypatch.setattr(engineering_precedent_store, "_codex_json", codex_json)

    keywords = engineering_precedent_store._codex_search_keywords(
        "save/load system",
        {"language": "JavaScript", "environment": "browser", "domain": "RPG"},
    )

    assert keywords == [
        "persistent game state",
        "state serialization",
        "party data persistence",
        "save load manager",
        "character roster",
        "turn based combat actors",
        "inventory system",
    ]
    _assert_general_search_keywords(keywords)
    assert "language-agnostic" in prompts[0]
    assert "do not include programming languages" in prompts[0]


def test_search_keyword_overqualification_helper_flags_bad_queries():
    bad_keywords = [
        "browser RPG persistent game state",
        "javascript RPG state serialization",
        "rpg character roster javascript",
        "turn based combat actors javascript",
        "one two three four five",
    ]
    good_keywords = [
        "save system",
        "game state serialization",
        "save load manager",
        "inventory system",
        "character roster",
        "progression gating",
        "unlock system",
        "level gate",
    ]

    assert all(engineering_precedent_store._search_keyword_is_overqualified(keyword) for keyword in bad_keywords)
    assert not any(engineering_precedent_store._search_keyword_is_overqualified(keyword) for keyword in good_keywords)
    _assert_general_search_keywords(good_keywords)


def test_normalize_term_folds_only_safe_suffix_variants():
    key = engineering_precedent_store._normalize_term

    assert key("Dungeon Exploration System") == key("dungeon exploration")
    assert key("Spells and MP Systems") == "spells and mp"
    assert key("  DUNGEON   EXPLORATION!!!  ") == key("dungeon exploration")
    assert key("save/load system") == "save/load"
    assert key("save/load system mechanics") == "save/load"
    assert key("party recruitment system") != key("party growth system")


def test_github_search_rate_limiter_enforces_shared_rolling_window_across_processes(tmp_path):
    start_file = tmp_path / "start"
    child_code = """
import json
import os
import time
from pathlib import Path

from ai_org import engineering_precedent_store

engineering_precedent_store.GH_SEARCH_WINDOW_SECONDS = float(os.environ["TEST_GH_WINDOW_SECONDS"])
start = Path(os.environ["TEST_START_FILE"])
while not start.exists():
    time.sleep(0.005)
before = time.time()
engineering_precedent_store._acquire_gh_search_slot()
after = time.time()
print(json.dumps({"before": before, "after": after, "elapsed": after - before}))
"""
    env = {
        **os.environ,
        "HOME": str(tmp_path),
        "PYTHONPATH": str(engineering_precedent_store.REPO_ROOT),
        "TEST_START_FILE": str(start_file),
        "TEST_GH_WINDOW_SECONDS": "0.35",
        "AI_ORG_GH_SEARCH_PER_MIN": "1",
        "AI_ORG_GH_SEARCH_FILE_LOCK_TIMEOUT_SECONDS": "1",
    }
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", child_code],
            cwd=engineering_precedent_store.REPO_ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(2)
    ]
    start_file.touch()

    results = []
    for process in processes:
        stdout, stderr = process.communicate(timeout=5)
        assert process.returncode == 0, stderr
        results.append(json.loads(stdout))

    elapsed = sorted(result["elapsed"] for result in results)
    assert elapsed[0] < 0.2
    assert elapsed[1] >= 0.25

    ledger = (
        tmp_path
        / "aiorg_assets"
        / "engineering_precedent_store"
        / "gh-search-rate"
        / "github-search-timestamps-ledger.jsonl"
    )
    assert ledger.exists()
    assert len([line for line in ledger.read_text(encoding="utf-8").splitlines() if line.strip()]) == 1


def test_search_repositories_backs_off_and_retries_rate_limited_search_once(monkeypatch):
    calls = []
    sleeps = []

    def fake_run_gh(cmd):
        calls.append(cmd)
        if cmd == ["gh", "api", "rate_limit"]:
            stdout = json.dumps({"resources": {"search": {"reset": 112}}})
            return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")
        search_calls = [call for call in calls if call[:3] == ["gh", "search", "repos"]]
        if len(search_calls) == 1:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="HTTP 403: rate limit exceeded")
        stdout = json.dumps([{"fullName": "quality/Turn-Based-Combat", "url": "https://github.com/quality/Turn-Based-Combat"}])
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(engineering_precedent_store.time, "time", lambda: 100.0)
    monkeypatch.setattr(engineering_precedent_store.time, "sleep", lambda seconds: sleeps.append(seconds))
    monkeypatch.setattr(engineering_precedent_store, "_gh_search_reset_jitter_seconds", lambda: 0.5)
    monkeypatch.setattr(engineering_precedent_store, "_run_gh", fake_run_gh)

    repos = engineering_precedent_store._search_repositories(["turn based combat system"], {"language": "JavaScript"})

    assert [call[:3] for call in calls].count(["gh", "search", "repos"]) == 2
    assert ["gh", "api", "rate_limit"] in calls
    assert sleeps == [12.5]
    assert repos[0]["fullName"] == "quality/Turn-Based-Combat"


def test_gh_search_memoization_hit_skips_run_gh(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(tmp_path / "engineering_precedent_store.sqlite3"))
    monkeypatch.setenv("AI_ORG_GH_SEARCH_CACHE_TTL_SECONDS", "86400")
    calls = []
    cmd = ["gh", "search", "repos", "turn based combat system", "--limit", "1", "--json", "fullName"]

    def fake_run_gh(cmd_arg):
        calls.append(cmd_arg)
        stdout = json.dumps([{"fullName": "quality/combat"}])
        return subprocess.CompletedProcess(cmd_arg, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(engineering_precedent_store, "_run_gh", fake_run_gh)

    first = engineering_precedent_store._gh_search_json(cmd)

    monkeypatch.setattr(
        engineering_precedent_store,
        "_run_gh",
        lambda cmd_arg: (_ for _ in ()).throw(AssertionError(f"unexpected gh call: {cmd_arg}")),
    )
    second = engineering_precedent_store._gh_search_json(cmd)

    assert first == [{"fullName": "quality/combat"}]
    assert second == first
    assert calls == [cmd]


def test_gh_search_memoization_ttl_expiry_refetches(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(tmp_path / "engineering_precedent_store.sqlite3"))
    monkeypatch.setenv("AI_ORG_GH_SEARCH_CACHE_TTL_SECONDS", "10")
    now = 100.0
    calls = []
    cmd = ["gh", "search", "repos", "turn based combat system", "--limit", "1", "--json", "fullName"]

    def fake_run_gh(cmd_arg):
        calls.append(cmd_arg)
        stdout = json.dumps([{"fullName": f"quality/combat-{len(calls)}"}])
        return subprocess.CompletedProcess(cmd_arg, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(engineering_precedent_store.time, "time", lambda: now)
    monkeypatch.setattr(engineering_precedent_store, "_run_gh", fake_run_gh)

    first = engineering_precedent_store._gh_search_json(cmd)
    now = 111.0
    second = engineering_precedent_store._gh_search_json(cmd)

    assert first == [{"fullName": "quality/combat-1"}]
    assert second == [{"fullName": "quality/combat-2"}]
    assert calls == [cmd, cmd]


def test_plain_gh_api_reads_are_not_capped_by_search_rate_limiter(monkeypatch):
    calls = []

    def fake_run_gh(cmd):
        calls.append(cmd)
        stdout = json.dumps({"tree": [{"type": "blob", "path": "src/combat/health.py"}]})
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(
        engineering_precedent_store.time,
        "sleep",
        lambda seconds: (_ for _ in ()).throw(AssertionError("plain gh api read was throttled")),
    )
    monkeypatch.setattr(engineering_precedent_store, "_run_gh", fake_run_gh)

    paths = engineering_precedent_store._candidate_paths_for_repo("example/repo", "health", {"language": "Python"})

    assert paths == ["src/combat/health.py"]
    assert calls == [["gh", "api", "repos/example/repo/git/trees/HEAD?recursive=1"]]


def test_fetch_candidates_reads_cross_language_files_and_records_actual_language(monkeypatch):
    monkeypatch.setattr(engineering_precedent_store, "_codex_search_keywords", lambda term, context: ["turn based combat system"])
    monkeypatch.setattr(
        engineering_precedent_store,
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

    monkeypatch.setattr(engineering_precedent_store, "_run_gh", fake_run_gh)

    candidates = engineering_precedent_store.fetch_candidates("turn manager", {"language": "JavaScript", "version": "ES2022"})

    assert candidates[0]["source_url"].endswith("/blob/HEAD/Assets/Scripts/TurnBasedCombat.cs")
    assert candidates[0]["lang_env_version"] == "C#"
    assert candidates[0]["found_via"] == "turn based combat system"


def test_expand_drops_baseline_equivalent_candidates_with_honest_note(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(tmp_path / "engineering_precedent_store.sqlite3"))
    monkeypatch.setattr(engineering_precedent_store, "_codex_baseline", lambda term, context: "hp -= damage; if hp <= 0: dead = True")
    monkeypatch.setattr(engineering_precedent_store, "_codex_search_keywords", lambda term, context: ["turn based combat system"])
    monkeypatch.setattr(
        engineering_precedent_store,
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
        engineering_precedent_store,
        "_codex_delta_inclusion",
        lambda term, context, baseline, candidate: {"keep": False, "reason": "baseline-equivalent"},
    )

    entry = engineering_precedent_store.expand("hit points implementation", {"language": "Python", "version": "3.12"})

    assert entry["candidates"] == []
    assert entry["search_keywords"] == ["turn based combat system"]
    assert entry["examined"] == [
        {
            "repo": "example/simple",
            "language": "Python 3.12",
            "outcome": "rejected-baseline-equivalent",
            "found_via": "turn based combat system",
        }
    ]
    assert entry["notes"] == "baseline-sufficient-nothing-added: baseline already sufficient; nothing valuable to add."


def test_expand_keeps_only_genuine_delta_and_distills_real_fields(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(tmp_path / "engineering_precedent_store.sqlite3"))
    monkeypatch.setattr(engineering_precedent_store, "_codex_baseline", lambda term, context: "hp -= damage; if hp <= 0: dead = True")
    monkeypatch.setattr(engineering_precedent_store, "_codex_search_keywords", lambda term, context: ["turn based combat system", "rpg battle system"])
    monkeypatch.setattr(
        engineering_precedent_store,
        "fetch_candidates",
        lambda term, context: [
            {
                "snippet": "class Health: apply raw and elemental damage through shields before clamping hp",
                "summary": "Extracted health component.",
                "source_url": "https://github.com/example/rigorous/blob/HEAD/health.py",
                "lang_env_version": "Python 3.12 turn-based RPG",
                "pitfalls": "Shield ordering matters.",
                "found_via": "rpg battle system",
            }
        ],
    )
    monkeypatch.setattr(
        engineering_precedent_store,
        "_codex_delta_inclusion",
        lambda term, context, baseline, candidate: {
            "keep": True,
            "reason": "handles shields, overkill clamping, and KO transition ordering missing from baseline",
        },
    )
    monkeypatch.setattr(
        engineering_precedent_store,
        "_codex_distill_candidate",
        lambda term, context, baseline, candidate, delta_reason: {
            "snippet": "remaining = shields.absorb(damage); hp = clamp(hp - remaining, 0, max_hp); if hp == 0: set_state('ko')",
            "summary": "Applies mitigation before HP clamping and preserves a single KO transition, beating the baseline's raw subtraction.",
            "lang_env_version": "Python 3.12 turn-based RPG",
            "pitfalls": "Do not trigger KO before shields and overkill clamping have resolved.",
        },
    )
    monkeypatch.setattr(
        engineering_precedent_store,
        "_codex_author_level",
        lambda term, context, candidate: {"author_level": "expert", "reason": "cohesive state transition handling"},
    )

    entry = engineering_precedent_store.expand("hit points implementation", {"language": "Python", "version": "3.12"})
    read = engineering_precedent_store.lookup("hit points implementation", {"language": "Python", "version": "3.12"})

    assert len(entry["candidates"]) == 1
    assert entry["search_keywords"] == ["turn based combat system", "rpg battle system"]
    assert entry["examined"] == [
        {
            "repo": "example/rigorous",
            "language": "Python 3.12 turn-based RPG",
            "outcome": "kept",
            "found_via": "rpg battle system",
        }
    ]
    candidate = entry["candidates"][0]
    assert "beating the baseline" in candidate["summary"]
    assert "Do not trigger KO" in candidate["pitfalls"]
    assert "clamp" in candidate["snippet"]
    assert candidate["author_level"] == "expert"
    assert candidate["found_via"] == "rpg battle system"
    assert candidate["found_via"] in entry["search_keywords"]
    assert read is not None
    assert set(read) == {"term", "candidates"}
    assert set(read["candidates"][0]) == {
        "kind",
        "snippet",
        "summary",
        "pitfalls",
        "lang_env_version",
        "author_level",
        "source_url",
    }
    assert "found_via" not in read["candidates"][0]
    assert engineering_precedent_store._database_path().is_relative_to(tmp_path)
    assert not engineering_precedent_store._database_path().is_relative_to(engineering_precedent_store.REPO_ROOT)


def test_expand_produces_implementation_and_design_candidates_from_separate_lanes(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(tmp_path / "engineering_precedent_store.sqlite3"))
    monkeypatch.setattr(engineering_precedent_store, "fetch_design_candidates", ORIGINAL_FETCH_DESIGN_CANDIDATES)
    lane_calls = []

    monkeypatch.setattr(engineering_precedent_store, "_codex_baseline", lambda term, context: "basic queue worker")
    monkeypatch.setattr(engineering_precedent_store, "_codex_search_keywords", lambda term, context: ["job queue worker"])
    monkeypatch.setattr(engineering_precedent_store, "_codex_design_search_keywords", lambda term, context: ["job queue architecture"])
    monkeypatch.setattr(
        engineering_precedent_store,
        "_search_repositories",
        lambda keywords, context: lane_calls.append(("implementation-search", keywords))
        or [{"fullName": "example/worker", "url": "https://github.com/example/worker", "primaryLanguage": {"name": "Python"}, "found_via": keywords[0]}],
    )
    monkeypatch.setattr(
        engineering_precedent_store,
        "_search_design_repositories",
        lambda keywords, context: lane_calls.append(("design-search", keywords))
        or [{"fullName": "example/adr", "url": "https://github.com/example/adr", "primaryLanguage": {"name": "Markdown"}, "found_via": keywords[0]}],
    )
    monkeypatch.setattr(engineering_precedent_store, "_candidate_paths_for_repo", lambda repo, term, context: ["worker.py"])
    monkeypatch.setattr(engineering_precedent_store, "_design_paths_for_repo", lambda repo, term, context: ["docs/adr/001-queue.md"])
    monkeypatch.setattr(engineering_precedent_store, "_read_github_file", lambda repo, path: "source content")
    monkeypatch.setattr(
        engineering_precedent_store,
        "_codex_extract_pattern",
        lambda term, context, repo, path, content: {
            "relevant": True,
            "snippet": "lease = claim_due_job(now); run_idempotently(lease)",
            "summary": "Claims due jobs with a lease before running.",
            "lang_env_version": "Python 3.12",
            "pitfalls": "Renew leases or release them on failure.",
        },
    )
    monkeypatch.setattr(
        engineering_precedent_store,
        "_codex_extract_design",
        lambda term, context, source_url, content: {
            "relevant": True,
            "structure": "Queue, worker, lease owner, and retry scheduler have separate responsibilities and boundaries.",
            "rationale": "A lease boundary prevents concurrent workers from committing the same job.",
            "when_to_use": "Use when workers can crash after claiming work.",
            "when_not_to_use": "Do not use when jobs are single-process and in-memory only.",
            "tradeoffs": "Adds clock and lease-renewal complexity to gain crash recovery.",
            "alternatives": "A simple pop was rejected because crashes lose in-flight work.",
            "implementation_hooks": "Read implementation candidates for lease renewal and idempotency keys.",
            "quality_attributes": "Reliability, operability, and bounded duplicate execution.",
            "evidence": "Accepted ADR with production worker migration notes.",
            "delta_claim": "Use leases only if crash recovery matters; otherwise a simple queue is cheaper.",
            "lang_env_version": "general",
        },
    )
    monkeypatch.setattr(engineering_precedent_store, "_codex_design_web_sources", lambda keywords, context: [])
    monkeypatch.setattr(engineering_precedent_store, "_codex_delta_inclusion", lambda term, context, baseline, candidate: {"keep": True, "reason": "lease claim is a real delta"})
    monkeypatch.setattr(engineering_precedent_store, "_codex_design_delta_inclusion", lambda term, context, candidate: {"keep": True, "reason": candidate["delta_claim"]})
    monkeypatch.setattr(
        engineering_precedent_store,
        "_codex_distill_candidate",
        lambda term, context, baseline, candidate, delta_reason: {
            "snippet": candidate["snippet"],
            "summary": candidate["summary"],
            "lang_env_version": candidate["lang_env_version"],
            "pitfalls": candidate["pitfalls"],
        },
    )
    monkeypatch.setattr(engineering_precedent_store, "_codex_author_level", lambda term, context, candidate: {"author_level": "high", "reason": "clear leases"})
    monkeypatch.setattr(engineering_precedent_store, "_codex_design_competence", lambda term, context, candidate: {"keep": True, "author_level": "expert", "reason": "accepted ADR with migration evidence"})

    entry = engineering_precedent_store.expand("job queue worker", {"language": "Python", "version": "3.12"})

    assert [call[0] for call in lane_calls] == ["implementation-search", "design-search"]
    assert {candidate["kind"] for candidate in entry["candidates"]} == {"implementation", "design"}
    implementation = [candidate for candidate in entry["candidates"] if candidate["kind"] == "implementation"][0]
    design = [candidate for candidate in entry["candidates"] if candidate["kind"] == "design"][0]
    assert "lease =" in implementation["snippet"]
    assert "structure" in design
    assert "snippet" not in design
    assert design["delta_claim"].startswith("Use leases only if")
    assert entry["search_keywords"] == ["job queue worker", "job queue architecture"]


def test_design_delta_filter_keeps_only_non_obvious_design_lessons(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(tmp_path / "engineering_precedent_store.sqlite3"))
    monkeypatch.setattr(engineering_precedent_store, "_codex_baseline", lambda term, context: "basic design")
    monkeypatch.setattr(engineering_precedent_store, "_codex_search_keywords", lambda term, context: [])
    monkeypatch.setattr(engineering_precedent_store, "fetch_candidates", lambda term, context: [])
    monkeypatch.setattr(
        engineering_precedent_store,
        "fetch_design_candidates",
        lambda term, context: [
            _design_candidate(
                source_url="https://example.com/basic-pattern",
                delta_claim="Observer decouples senders and receivers.",
            ),
            _design_candidate(
                source_url="https://example.com/rollout-adr",
                delta_claim="Use the event log only if replay latency is bounded; otherwise keep a compact state table.",
            ),
        ],
    )
    monkeypatch.setattr(
        engineering_precedent_store,
        "_codex_design_delta_inclusion",
        lambda term, context, candidate: {
            "keep": "only if" in candidate["delta_claim"],
            "reason": candidate["delta_claim"] if "only if" in candidate["delta_claim"] else "GoF-basic",
        },
    )
    monkeypatch.setattr(engineering_precedent_store, "_codex_design_competence", lambda term, context, candidate: {"keep": True, "author_level": "expert", "reason": "specific production evidence"})

    entry = engineering_precedent_store.expand("event subscription design", {})

    assert [candidate["source_url"] for candidate in entry["candidates"]] == ["https://example.com/rollout-adr"]
    assert entry["candidates"][0]["kind"] == "design"
    assert "only if replay latency" in entry["candidates"][0]["delta_claim"]


def test_workaround_thread_folds_into_strongest_sibling(monkeypatch):
    events = []
    monkeypatch.setattr(
        engineering_precedent_store.org_log,
        "emit",
        lambda event_type, payload=None, **kwargs: events.append((event_type, payload)) or {"event_id": event_type},
    )
    ctx = engineering_precedent_store.org_log.RunContext(repo=engineering_precedent_store.REPO_ROOT)
    official = _design_candidate(
        source_url="https://docs.example.com/supervision",
        delta_claim="Use supervised process groups when child lifetime must outlive the parent shell.",
        evidence_class="official_doc",
    )
    practitioner = _design_candidate(
        source_url="https://example.com/process-blog",
        delta_claim="Use sleeps only during local diagnostics.",
        evidence_class="practitioner_report",
    )
    workaround = _design_candidate(
        source_url="https://stackoverflow.example.com/sleep-after-spawn",
        delta_claim="Sleep after spawn so the child has time to detach.",
        evidence_class="workaround_thread",
    )

    folded = engineering_precedent_store._fold_workaround_candidates(
        "child process lifetime",
        [practitioner, workaround, official],
        ctx,
    )

    assert [candidate["source_url"] for candidate in folded] == [
        "https://example.com/process-blog",
        "https://docs.example.com/supervision",
    ]
    target = [candidate for candidate in folded if candidate["source_url"] == "https://docs.example.com/supervision"][0]
    assert "sleep-after-spawn" in target["alternatives"]
    assert events == [
        (
            "engineering_precedent_store.workaround_candidate.folded",
            {
                "term": "child process lifetime",
                "candidate_kind": "design",
                "candidate_source_url": "https://stackoverflow.example.com/sleep-after-spawn",
                "candidate_found_via": "job queue architecture",
                "candidate_evidence_class": "workaround_thread",
                "target_kind": "design",
                "target_source_url": "https://docs.example.com/supervision",
                "target_evidence_class": "official_doc",
            },
        )
    ]


def test_workaround_thread_dropped_with_event_when_alone(monkeypatch):
    events = []
    monkeypatch.setattr(
        engineering_precedent_store.org_log,
        "emit",
        lambda event_type, payload=None, **kwargs: events.append((event_type, payload)) or {"event_id": event_type},
    )
    ctx = engineering_precedent_store.org_log.RunContext(repo=engineering_precedent_store.REPO_ROOT)
    workaround = _design_candidate(
        source_url="https://github.com/example/project/issues/123#issuecomment-1",
        delta_claim="Sleep after spawn to avoid a race in this one environment.",
        evidence_class="workaround_thread",
    )

    folded = engineering_precedent_store._fold_workaround_candidates("child process lifetime", [workaround], ctx)

    assert folded == []
    assert events == [
        (
            "engineering_precedent_store.workaround_candidate.dropped",
            {
                "term": "child process lifetime",
                "candidate_kind": "design",
                "candidate_source_url": "https://github.com/example/project/issues/123#issuecomment-1",
                "candidate_found_via": "job queue architecture",
                "candidate_evidence_class": "workaround_thread",
            },
        )
    ]


def test_missing_evidence_class_is_tolerated_on_old_rows(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(tmp_path / "engineering_precedent_store.sqlite3"))
    engineering_precedent_store._write_entry(
        {
            "term": "state update",
            "search_keywords": ["react state update"],
            "examined": [
                {"repo": "example/react", "language": "JavaScript", "outcome": "kept", "found_via": "react state update"},
            ],
            "candidates": [
                {
                    "snippet": "setState(prev => prev + 1)",
                    "summary": "Functional update.",
                    "source_url": "https://github.com/example/react/blob/HEAD/state.jsx",
                    "lang_env_version": "React 18 hooks",
                    "author_level": "medium",
                    "pitfalls": "Avoid stale closures.",
                    "found_via": "react state update",
                },
            ],
            "notes": "",
        }
    )

    audit = engineering_precedent_store.audit("state update")
    lookup = engineering_precedent_store.lookup("state update", {"language": "React", "environment": "hooks", "version": "18"})

    assert audit["candidates"][0]["evidence_class"] == "unclassified"
    assert "evidence_class" not in lookup["candidates"][0]


def test_practitioner_report_is_not_demoted_by_workaround_like_content(monkeypatch):
    events = []
    monkeypatch.setattr(
        engineering_precedent_store.org_log,
        "emit",
        lambda event_type, payload=None, **kwargs: events.append((event_type, payload)) or {"event_id": event_type},
    )
    ctx = engineering_precedent_store.org_log.RunContext(repo=engineering_precedent_store.REPO_ROOT)
    practitioner = _design_candidate(
        source_url="https://engineering.example.com/schema-tolerance",
        delta_claim="Use schema tolerance instead of discriminated unions when the output-schema subset forbids oneOf.",
        evidence_class="practitioner_report",
    )

    folded = engineering_precedent_store._fold_workaround_candidates("schema tolerance versus sum types", [practitioner], ctx)

    assert folded == [practitioner]
    assert events == []


def test_design_github_search_uses_shared_gh_rate_limited_queue(monkeypatch):
    now = 1.0
    sleeps = []
    calls = []

    def sleep(seconds):
        nonlocal now
        sleeps.append(seconds)
        now += seconds

    def fake_run_gh(cmd):
        calls.append((now, cmd))
        stdout = json.dumps(
            [
                {"fullName": f"example/design-{index}", "url": f"https://github.com/example/design-{index}"}
                for index in range(engineering_precedent_store.MAX_REPOS)
            ]
        )
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(engineering_precedent_store, "_WEB_SEARCH_TIMESTAMPS", [])
    monkeypatch.setenv("AI_ORG_GH_SEARCH_PER_MIN", "1")
    rate_dir = engineering_precedent_store._gh_search_rate_directory()
    rate_dir.mkdir(parents=True, exist_ok=True)
    (rate_dir / "github-search-timestamps-ledger.jsonl").write_text("1.000000\n", encoding="utf-8")
    monkeypatch.setattr(engineering_precedent_store.time, "time", lambda: now)
    monkeypatch.setattr(engineering_precedent_store.time, "sleep", sleep)
    monkeypatch.setattr(engineering_precedent_store, "_run_gh", fake_run_gh)

    engineering_precedent_store._search_design_repositories(["job queue architecture"], {})

    assert sleeps == [engineering_precedent_store.GH_SEARCH_WINDOW_SECONDS]
    assert calls[0][0] >= engineering_precedent_store.GH_SEARCH_WINDOW_SECONDS
    assert calls[0][1][:3] == ["gh", "search", "repos"]
    assert engineering_precedent_store._WEB_SEARCH_TIMESTAMPS == []


def test_design_web_search_uses_separate_limiter_not_gh_queue(monkeypatch):
    now = 0.0
    sleeps = []

    def monotonic():
        return now

    def sleep(seconds):
        nonlocal now
        sleeps.append(seconds)
        now += seconds

    def codex_json(prompt, schema, output_name):
        assert schema == engineering_precedent_store.DESIGN_SOURCE_SCHEMA
        return {
            "sources": [
                {
                    "title": "Queue ADR",
                    "url": "https://example.com/queue-adr",
                    "content": "Accepted ADR with migration evidence.",
                    "status": "accepted",
                }
            ]
        }

    rate_dir = engineering_precedent_store._gh_search_rate_directory()
    rate_dir.mkdir(parents=True, exist_ok=True)
    ledger = rate_dir / "github-search-timestamps-ledger.jsonl"
    ledger.write_text("1.000000\n", encoding="utf-8")
    monkeypatch.setattr(engineering_precedent_store, "_WEB_SEARCH_TIMESTAMPS", [0.0] * engineering_precedent_store.WEB_SEARCH_PER_MIN)
    monkeypatch.setattr(engineering_precedent_store.time, "monotonic", monotonic)
    monkeypatch.setattr(engineering_precedent_store.time, "sleep", sleep)
    monkeypatch.setattr(engineering_precedent_store, "_codex_json", codex_json)

    sources = engineering_precedent_store._codex_design_web_sources(["job queue architecture"], {})

    assert sleeps == [engineering_precedent_store.WEB_SEARCH_WINDOW_SECONDS]
    assert sources[0]["url"] == "https://example.com/queue-adr"
    assert ledger.read_text(encoding="utf-8") == "1.000000\n"


def test_low_level_author_delta_is_stored_with_honest_note(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(tmp_path / "engineering_precedent_store.sqlite3"))
    monkeypatch.setattr(engineering_precedent_store, "_codex_baseline", lambda term, context: "basic loop")
    monkeypatch.setattr(engineering_precedent_store, "_codex_search_keywords", lambda term, context: ["tree depth root"])
    monkeypatch.setattr(engineering_precedent_store, "_codex_delta_inclusion", lambda term, context, baseline, candidate: {"keep": True, "reason": "real edge case"})
    monkeypatch.setattr(
        engineering_precedent_store,
        "_codex_distill_candidate",
        lambda term, context, baseline, candidate, delta_reason: {
            "snippet": "if node is root: return 0",
            "summary": "Handles root depth explicitly, beating the baseline that starts counting from children.",
            "lang_env_version": "Python 3.12",
            "pitfalls": "Only applies when root depth is defined as zero.",
        },
    )
    monkeypatch.setattr(engineering_precedent_store, "_codex_author_level", lambda term, context, candidate: {"author_level": "low", "reason": "rough style"})
    monkeypatch.setattr(
        engineering_precedent_store,
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

    entry = engineering_precedent_store.expand("tree depth root edge case", {"language": "Python", "version": "3.12"})

    assert len(entry["candidates"]) == 1
    assert entry["candidates"][0]["author_level"] == "low"
    assert entry["candidates"][0]["found_via"] == "tree depth root"
    assert entry["search_keywords"] == ["tree depth root"]
    assert entry["notes"].startswith("low-level-only:")


def test_expand_records_empty_search_keywords_when_nothing_fetched(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(tmp_path / "engineering_precedent_store.sqlite3"))
    monkeypatch.setattr(engineering_precedent_store, "_codex_baseline", lambda term, context: "use a basic verifier")
    monkeypatch.setattr(engineering_precedent_store, "_codex_search_keywords", lambda term, context: ["oauth pkce verifier"])
    monkeypatch.setattr(engineering_precedent_store, "_search_repositories", lambda keywords, context: [])

    entry = engineering_precedent_store.expand("PKCE verifier rotation", {"language": "TypeScript"})
    read = engineering_precedent_store.lookup("PKCE verifier rotation", {"language": "TypeScript"})

    assert entry["candidates"] == []
    assert entry["search_keywords"] == ["oauth pkce verifier"]
    assert entry["examined"] == []
    assert entry["notes"] == "nothing-fetched: no public repository candidates were fetched."
    assert read is not None
    assert "search_keywords" not in read
    assert engineering_precedent_store.audit("PKCE verifier rotation")["search_keywords"] == ["oauth pkce verifier"]


def test_expand_skips_recent_research_and_returns_stored_candidates(monkeypatch, tmp_path):
    now = {"value": 1000.0}
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(tmp_path / "engineering_precedent_store.sqlite3"))
    monkeypatch.setattr(engineering_precedent_store.time, "time", lambda: now["value"])
    engineering_precedent_store._write_entry(
        {
            "term": "state update",
            "search_keywords": ["react state update"],
            "examined": [
                {"repo": "example/react", "language": "JavaScript", "outcome": "kept", "found_via": "react state update"},
            ],
            "candidates": [
                {
                    "snippet": "setState(prev => prev + 1)",
                    "summary": "Functional update.",
                    "source_url": "https://github.com/example/react/blob/HEAD/state.jsx",
                    "lang_env_version": "React 18 hooks",
                    "author_level": "medium",
                    "pitfalls": "Avoid stale closures.",
                    "found_via": "react state update",
                },
            ],
            "notes": "stored result",
        }
    )

    now["value"] = 1100.0
    monkeypatch.setattr(engineering_precedent_store, "_codex_baseline", lambda term, context: pytest.fail("baseline should be skipped"))
    monkeypatch.setattr(engineering_precedent_store, "_codex_search_keywords", lambda term, context: pytest.fail("keywords should be skipped"))
    monkeypatch.setattr(engineering_precedent_store, "fetch_candidates", lambda term, context: pytest.fail("implementation fetch should be skipped"))
    monkeypatch.setattr(engineering_precedent_store, "fetch_design_candidates", lambda term, context: pytest.fail("design fetch should be skipped"))

    entry = engineering_precedent_store.expand("state update", {"language": "React", "environment": "hooks", "version": "18"})

    assert entry["notes"] == "stored result"
    assert entry["candidates"][0]["snippet"] == "setState(prev => prev + 1)"
    assert [attempt["last_searched_at"] for attempt in entry["research"]] == [1000.0]
    assert [attempt["attempt"] for attempt in entry["research"]] == [1]


def test_expand_skips_recent_research_across_term_variants(monkeypatch, tmp_path):
    now = {"value": 1200.0}
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(tmp_path / "engineering_precedent_store.sqlite3"))
    monkeypatch.setattr(engineering_precedent_store.time, "time", lambda: now["value"])
    engineering_precedent_store._write_entry(
        {
            "term": "dungeon exploration",
            "search_keywords": ["dungeon exploration"],
            "examined": [],
            "candidates": [],
            "notes": "nothing-fetched: no public repository candidates were fetched.",
        }
    )

    now["value"] = 1210.0
    monkeypatch.setattr(engineering_precedent_store, "_codex_baseline", lambda term, context: pytest.fail("baseline should be skipped"))
    monkeypatch.setattr(engineering_precedent_store, "_codex_search_keywords", lambda term, context: pytest.fail("keywords should be skipped"))
    monkeypatch.setattr(engineering_precedent_store, "fetch_candidates", lambda term, context: pytest.fail("implementation fetch should be skipped"))
    monkeypatch.setattr(engineering_precedent_store, "fetch_design_candidates", lambda term, context: pytest.fail("design fetch should be skipped"))

    entry = engineering_precedent_store.expand("Dungeon Exploration System", {"language": "Python"})

    assert entry["term"] == "dungeon exploration"
    assert entry["candidates"] == []
    assert [attempt["last_searched_at"] for attempt in entry["research"]] == [1200.0]


def test_expand_skips_recent_empty_research_without_searching(monkeypatch, tmp_path):
    now = {"value": 2000.0}
    calls = []
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(tmp_path / "engineering_precedent_store.sqlite3"))
    monkeypatch.setattr(engineering_precedent_store.time, "time", lambda: now["value"])
    monkeypatch.setattr(engineering_precedent_store, "_codex_baseline", lambda term, context: calls.append("baseline") or "use a basic verifier")
    monkeypatch.setattr(engineering_precedent_store, "_codex_search_keywords", lambda term, context: calls.append("keywords") or ["oauth pkce verifier"])
    monkeypatch.setattr(engineering_precedent_store, "fetch_candidates", lambda term, context: calls.append("implementation-fetch") or [])
    monkeypatch.setattr(engineering_precedent_store, "fetch_design_candidates", lambda term, context: calls.append("design-fetch") or [])

    first = engineering_precedent_store.expand("PKCE verifier rotation", {"language": "TypeScript"})
    now["value"] = 2010.0
    second = engineering_precedent_store.expand("PKCE verifier rotation", {"language": "TypeScript"})

    assert first["candidates"] == []
    assert second["candidates"] == []
    assert second["notes"] == "nothing-fetched: no public repository candidates were fetched."
    assert calls == ["baseline", "keywords", "implementation-fetch", "design-fetch"]
    assert [attempt["last_searched_at"] for attempt in second["research"]] == [2000.0]


def test_expand_force_bypasses_recent_research_and_runs_search_lanes(monkeypatch, tmp_path):
    now = {"value": 2500.0}
    calls = []
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(tmp_path / "engineering_precedent_store.sqlite3"))
    monkeypatch.setattr(engineering_precedent_store.time, "time", lambda: now["value"])
    monkeypatch.setattr(engineering_precedent_store, "_codex_baseline", lambda term, context: calls.append("baseline") or "use a basic verifier")
    monkeypatch.setattr(engineering_precedent_store, "_codex_search_keywords", lambda term, context: calls.append("keywords") or ["oauth pkce verifier"])
    monkeypatch.setattr(engineering_precedent_store, "fetch_candidates", lambda term, context: calls.append("implementation-fetch") or [])
    monkeypatch.setattr(engineering_precedent_store, "fetch_design_candidates", lambda term, context: calls.append("design-fetch") or [])

    engineering_precedent_store.expand("PKCE verifier rotation", {"language": "TypeScript"})
    now["value"] = 2510.0
    entry = engineering_precedent_store.expand("PKCE verifier rotation", {"language": "TypeScript"}, force=True)

    assert entry["candidates"] == []
    assert calls == [
        "baseline",
        "keywords",
        "implementation-fetch",
        "design-fetch",
        "baseline",
        "keywords",
        "implementation-fetch",
        "design-fetch",
    ]
    assert [attempt["attempt"] for attempt in engineering_precedent_store.audit("PKCE verifier rotation")["research"]] == [1, 2]
    assert [attempt["last_searched_at"] for attempt in engineering_precedent_store.audit("PKCE verifier rotation")["research"]] == [
        2500.0,
        2510.0,
    ]


def test_expand_env_force_bypasses_recent_research(monkeypatch, tmp_path):
    now = {"value": 2600.0}
    calls = []
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(tmp_path / "engineering_precedent_store.sqlite3"))
    monkeypatch.setattr(engineering_precedent_store.time, "time", lambda: now["value"])
    monkeypatch.setattr(engineering_precedent_store, "_codex_baseline", lambda term, context: calls.append("baseline") or "use a basic verifier")
    monkeypatch.setattr(engineering_precedent_store, "_codex_search_keywords", lambda term, context: calls.append("keywords") or ["oauth pkce verifier"])
    monkeypatch.setattr(engineering_precedent_store, "fetch_candidates", lambda term, context: calls.append("implementation-fetch") or [])
    monkeypatch.setattr(engineering_precedent_store, "fetch_design_candidates", lambda term, context: calls.append("design-fetch") or [])

    engineering_precedent_store.expand("PKCE verifier rotation", {"language": "TypeScript"})
    now["value"] = 2610.0
    monkeypatch.setenv("AI_ORG_REFERENCE_FORCE", "1")
    engineering_precedent_store.expand("PKCE verifier rotation", {"language": "TypeScript"})
    entry = engineering_precedent_store.audit("PKCE verifier rotation")

    assert entry is not None
    assert calls == [
        "baseline",
        "keywords",
        "implementation-fetch",
        "design-fetch",
        "baseline",
        "keywords",
        "implementation-fetch",
        "design-fetch",
    ]
    assert [attempt["last_searched_at"] for attempt in entry["research"]] == [2600.0, 2610.0]


def test_expand_runs_when_research_is_older_than_ttl_or_never_searched(monkeypatch, tmp_path):
    now = {"value": 3000.0}
    calls = []
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(tmp_path / "engineering_precedent_store.sqlite3"))
    monkeypatch.setattr(engineering_precedent_store.time, "time", lambda: now["value"])
    monkeypatch.setattr(engineering_precedent_store, "_codex_baseline", lambda term, context: calls.append("baseline") or "use a basic verifier")
    monkeypatch.setattr(engineering_precedent_store, "_codex_search_keywords", lambda term, context: calls.append("keywords") or ["oauth pkce verifier"])
    monkeypatch.setattr(engineering_precedent_store, "fetch_candidates", lambda term, context: calls.append("implementation-fetch") or [])
    monkeypatch.setattr(engineering_precedent_store, "fetch_design_candidates", lambda term, context: calls.append("design-fetch") or [])

    engineering_precedent_store.expand("PKCE verifier rotation", {"language": "TypeScript"})
    now["value"] = 3000.0 + engineering_precedent_store.REFERENCE_RESEARCH_TTL_SECONDS + 1
    engineering_precedent_store.expand("PKCE verifier rotation", {"language": "TypeScript"})
    entry = engineering_precedent_store.audit("PKCE verifier rotation")

    assert entry is not None
    assert calls == [
        "baseline",
        "keywords",
        "implementation-fetch",
        "design-fetch",
        "baseline",
        "keywords",
        "implementation-fetch",
        "design-fetch",
    ]
    assert [attempt["attempt"] for attempt in entry["research"]] == [1, 2]
    assert [attempt["last_searched_at"] for attempt in entry["research"]] == [
        3000.0,
        3000.0 + engineering_precedent_store.REFERENCE_RESEARCH_TTL_SECONDS + 1,
    ]


def test_precedent_research_ttl_is_env_configurable(monkeypatch, tmp_path):
    now = {"value": 4000.0}
    calls = []
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(tmp_path / "engineering_precedent_store.sqlite3"))
    monkeypatch.setenv("AI_ORG_REFERENCE_TTL_SECONDS", "5")
    monkeypatch.setattr(engineering_precedent_store.time, "time", lambda: now["value"])
    monkeypatch.setattr(engineering_precedent_store, "_codex_baseline", lambda term, context: calls.append("baseline") or "use a basic verifier")
    monkeypatch.setattr(engineering_precedent_store, "_codex_search_keywords", lambda term, context: calls.append("keywords") or ["oauth pkce verifier"])
    monkeypatch.setattr(engineering_precedent_store, "fetch_candidates", lambda term, context: calls.append("implementation-fetch") or [])
    monkeypatch.setattr(engineering_precedent_store, "fetch_design_candidates", lambda term, context: calls.append("design-fetch") or [])

    engineering_precedent_store.expand("PKCE verifier rotation", {"language": "TypeScript"})
    now["value"] = 4004.0
    engineering_precedent_store.expand("PKCE verifier rotation", {"language": "TypeScript"})
    now["value"] = 4006.0
    engineering_precedent_store.expand("PKCE verifier rotation", {"language": "TypeScript"})
    entry = engineering_precedent_store.audit("PKCE verifier rotation")

    assert entry is not None
    assert engineering_precedent_store._precedent_research_ttl_seconds() == 5.0
    assert calls == [
        "baseline",
        "keywords",
        "implementation-fetch",
        "design-fetch",
        "baseline",
        "keywords",
        "implementation-fetch",
        "design-fetch",
    ]
    assert [attempt["last_searched_at"] for attempt in entry["research"]] == [4000.0, 4006.0]


def test_reexpand_appends_new_candidates_dedups_identical_and_preserves_on_empty_fetch(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(tmp_path / "engineering_precedent_store.sqlite3"))
    monkeypatch.setenv("AI_ORG_REFERENCE_TTL_SECONDS", "0")
    monkeypatch.setattr(engineering_precedent_store, "_codex_baseline", lambda term, context: "basic implementation")
    monkeypatch.setattr(
        engineering_precedent_store,
        "_codex_delta_inclusion",
        lambda term, context, baseline, candidate: {"keep": True, "reason": "real implementation detail"},
    )
    monkeypatch.setattr(
        engineering_precedent_store,
        "_codex_distill_candidate",
        lambda term, context, baseline, candidate, delta_reason: {
            "snippet": candidate["snippet"],
            "summary": candidate["summary"],
            "lang_env_version": candidate["lang_env_version"],
            "pitfalls": candidate["pitfalls"],
        },
    )
    monkeypatch.setattr(
        engineering_precedent_store,
        "_codex_author_level",
        lambda term, context, candidate: {"author_level": "expert", "reason": "clear implementation"},
    )

    calls = {"count": 0}

    def search_keywords(term, context):
        if calls["count"] == 0:
            return ["first durable pattern"]
        if calls["count"] == 1:
            return ["first durable pattern", "second durable pattern"]
        return ["source disappeared pattern"]

    def fetch_candidates(term, context):
        calls["count"] += 1
        if calls["count"] == 1:
            return [
                {
                    "snippet": "first durable snippet",
                    "summary": "First durable summary.",
                    "source_url": "https://github.com/example/first/blob/HEAD/main.py",
                    "lang_env_version": "Python 3.12",
                    "pitfalls": "First durable pitfall.",
                    "found_via": "first durable pattern",
                }
            ]
        if calls["count"] == 2:
            return [
                {
                    "snippet": "first durable snippet",
                    "summary": "Changed summary that must not overwrite the stored candidate.",
                    "source_url": "https://github.com/example/first/blob/HEAD/main.py",
                    "lang_env_version": "Python 3.12",
                    "pitfalls": "Changed pitfall.",
                    "found_via": "first durable pattern",
                },
                {
                    "snippet": "second durable snippet",
                    "summary": "Second durable summary.",
                    "source_url": "https://github.com/example/second/blob/HEAD/main.py",
                    "lang_env_version": "Python 3.12",
                    "pitfalls": "Second durable pitfall.",
                    "found_via": "second durable pattern",
                },
            ]
        return []

    monkeypatch.setattr(engineering_precedent_store, "_codex_search_keywords", search_keywords)
    monkeypatch.setattr(engineering_precedent_store, "fetch_candidates", fetch_candidates)

    engineering_precedent_store.expand("durable engineering_precedent_store", {"language": "Python", "version": "3.12"})
    engineering_precedent_store.expand("durable engineering_precedent_store", {"language": "Python", "version": "3.12"})
    entry = engineering_precedent_store.audit("durable engineering_precedent_store")

    assert entry is not None
    assert [candidate["snippet"] for candidate in entry["candidates"]] == [
        "first durable snippet",
        "second durable snippet",
    ]
    assert entry["candidates"][0]["summary"] == "First durable summary."
    assert [attempt["attempt"] for attempt in entry["research"]] == [1, 2]
    assert [attempt["search_keywords"] for attempt in entry["research"]] == [
        ["first durable pattern"],
        ["first durable pattern", "second durable pattern"],
    ]

    engineering_precedent_store.expand("durable engineering_precedent_store", {"language": "Python", "version": "3.12"})
    preserved = engineering_precedent_store.audit("durable engineering_precedent_store")

    assert preserved is not None
    assert [candidate["snippet"] for candidate in preserved["candidates"]] == [
        "first durable snippet",
        "second durable snippet",
    ]
    assert [attempt["attempt"] for attempt in preserved["research"]] == [1, 2, 3]
    assert preserved["research"][2]["notes"] == "nothing-fetched: no public repository candidates were fetched."


def test_candidate_dedup_uses_term_key_source_url_and_snippet(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(tmp_path / "engineering_precedent_store.sqlite3"))
    first = {
        "term": "dungeon exploration",
        "search_keywords": ["dungeon exploration"],
        "examined": [],
        "candidates": [
            {
                "snippet": "enter_room(); resolve_encounter()",
                "summary": "Original summary.",
                "source_url": "https://github.com/example/dungeon/blob/HEAD/explore.py",
                "lang_env_version": "Python 3.12",
                "author_level": "high",
                "pitfalls": "Resolve encounters before rewards.",
                "found_via": "dungeon exploration",
            },
        ],
        "notes": "",
    }
    second = {
        **first,
        "term": "dungeon exploration system",
        "candidates": [
            {
                **first["candidates"][0],
                "summary": "Changed summary that must not overwrite the stored candidate.",
            },
        ],
    }

    engineering_precedent_store._write_entry(first)
    engineering_precedent_store._write_entry(second)

    entry = engineering_precedent_store.audit("Dungeon Exploration Systems")
    assert entry is not None
    assert [attempt["term"] for attempt in entry["research"]] == ["dungeon exploration", "dungeon exploration system"]
    assert [candidate["summary"] for candidate in entry["candidates"]] == ["Original summary."]


def test_expand_records_examined_repos_from_fetch_audit(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(tmp_path / "engineering_precedent_store.sqlite3"))
    monkeypatch.setattr(engineering_precedent_store, "_codex_baseline", lambda term, context: "hp -= damage")
    monkeypatch.setattr(engineering_precedent_store, "_codex_search_keywords", lambda term, context: ["turn based combat system"])
    monkeypatch.setattr(
        engineering_precedent_store,
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
        engineering_precedent_store,
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

    monkeypatch.setattr(engineering_precedent_store, "_run_gh", fake_run_gh)

    entry = engineering_precedent_store.expand("hit points implementation", {"language": "Python", "version": "3.12"})

    assert entry["search_keywords"] == ["turn based combat system"]
    assert entry["examined"] == [
        {
            "repo": "example/simple",
            "language": "Python",
            "outcome": "rejected-baseline-equivalent",
            "found_via": "turn based combat system",
        }
    ]
    assert entry["notes"].startswith("baseline-sufficient")


def test_lookup_filters_by_applicability_and_returns_only_consumption_fields(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(tmp_path / "engineering_precedent_store.sqlite3"))
    engineering_precedent_store._write_entry(
        {
            "term": "state update",
            "search_keywords": ["react state update"],
            "examined": [
                {"repo": "example/react", "language": "JavaScript", "outcome": "kept", "found_via": "react state update"},
                {"repo": "example/python", "language": "Python", "outcome": "kept", "found_via": "react state update"},
            ],
            "candidates": [
                {
                    "snippet": "setState(prev => prev + 1)",
                    "summary": "Functional update.",
                    "source_url": "https://github.com/example/react/blob/HEAD/state.jsx",
                    "lang_env_version": "React 18 hooks",
                    "author_level": "medium",
                    "pitfalls": "Avoid stale closures.",
                    "found_via": "react state update",
                },
                {
                    "snippet": "self.value += 1",
                    "summary": "Plain Python mutation.",
                    "source_url": "https://github.com/example/python/blob/HEAD/state.py",
                    "lang_env_version": "Python 3.12",
                    "author_level": "medium",
                    "pitfalls": "Not a React pattern.",
                    "found_via": "react state update",
                },
            ],
            "notes": "",
        }
    )

    entry = engineering_precedent_store.lookup("state update", {"language": "React", "environment": "hooks", "version": "18"})

    assert entry is not None
    assert len(entry["candidates"]) == 1
    assert entry["candidates"][0]["snippet"] == "setState(prev => prev + 1)"
    assert set(entry["candidates"][0]) == {
        "kind",
        "snippet",
        "summary",
        "pitfalls",
        "lang_env_version",
        "author_level",
        "source_url",
    }
    assert "search_keywords" not in entry
    assert "examined" not in entry
    assert "notes" not in entry
    assert "found_via" not in entry["candidates"][0]


def test_lookup_hits_across_term_phrasing_variants(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(tmp_path / "engineering_precedent_store.sqlite3"))
    engineering_precedent_store._write_entry(
        {
            "term": "dungeon exploration",
            "search_keywords": ["dungeon exploration"],
            "examined": [
                {"repo": "example/dungeon", "language": "Python", "outcome": "kept", "found_via": "dungeon exploration"},
            ],
            "candidates": [
                {
                    "snippet": "enter_room(); resolve_encounter(); reveal_exits()",
                    "summary": "Rooms resolve encounters before exits are revealed.",
                    "source_url": "https://github.com/example/dungeon/blob/HEAD/explore.py",
                    "lang_env_version": "Python 3.12",
                    "author_level": "high",
                    "pitfalls": "Do not reveal locked exits before resolving the encounter.",
                    "found_via": "dungeon exploration",
                },
            ],
            "notes": "stored result",
        }
    )

    lookup = engineering_precedent_store.lookup("Dungeon Exploration System!!!", {"language": "Python", "version": "3.12"})
    audit = engineering_precedent_store.audit("dungeon exploration system")
    query_results = engineering_precedent_store.query({"term": "Dungeon Exploration System"})

    assert lookup is not None
    assert lookup["term"] == "dungeon exploration"
    assert lookup["candidates"][0]["snippet"] == "enter_room(); resolve_encounter(); reveal_exits()"
    assert audit is not None
    assert audit["term"] == "dungeon exploration"
    assert [candidate["term"] for candidate in query_results] == ["dungeon exploration"]


def test_audit_returns_management_fields(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(tmp_path / "engineering_precedent_store.sqlite3"))
    engineering_precedent_store._write_entry(
        {
            "term": "state update",
            "search_keywords": ["react state update"],
            "examined": [
                {"repo": "example/react", "language": "JavaScript", "outcome": "kept", "found_via": "react state update"},
            ],
            "candidates": [
                {
                    "snippet": "setState(prev => prev + 1)",
                    "summary": "Functional update.",
                    "source_url": "https://github.com/example/react/blob/HEAD/state.jsx",
                    "lang_env_version": "React 18 hooks",
                    "author_level": "medium",
                    "pitfalls": "Avoid stale closures.",
                    "found_via": "react state update",
                },
            ],
            "notes": "maintenance note",
        }
    )

    entry = engineering_precedent_store.audit("state update")

    assert entry is not None
    assert entry["search_keywords"] == ["react state update"]
    assert entry["examined"][0]["outcome"] == "kept"
    assert entry["candidates"][0]["found_via"] == "react state update"
    assert entry["notes"] == "maintenance note"


def test_legacy_cascade_schema_migrates_to_candidate_independent_history(monkeypatch, tmp_path):
    db_path = tmp_path / "engineering_precedent_store.sqlite3"
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(db_path))
    with sqlite3.connect(db_path) as connection:
        connection.executescript(
            """
            PRAGMA foreign_keys = ON;
            CREATE TABLE research (
                term TEXT PRIMARY KEY,
                notes TEXT NOT NULL,
                search_keywords TEXT NOT NULL,
                examined TEXT NOT NULL
            );
            CREATE TABLE candidates (
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
            """
        )
        connection.execute(
            "INSERT INTO research(term, notes, search_keywords, examined) VALUES (?, ?, ?, ?)",
            ("state update", "legacy note", json.dumps(["react state update"]), json.dumps([])),
        )
        connection.execute(
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
            (
                "state update",
                "setState(prev => prev + 1)",
                "Functional update.",
                "Avoid stale closures.",
                "React 18 hooks",
                "medium",
                "https://github.com/example/react/blob/HEAD/state.jsx",
                "react state update",
            ),
        )

    migrated = engineering_precedent_store.audit("state update")

    assert migrated is not None
    assert [attempt["attempt"] for attempt in migrated["research"]] == [1]
    assert migrated["candidates"][0]["snippet"] == "setState(prev => prev + 1)"
    with sqlite3.connect(db_path) as connection:
        assert connection.execute("PRAGMA foreign_key_list(candidates)").fetchall() == []

    engineering_precedent_store._write_entry(
        {
            "term": "state update",
            "search_keywords": ["source disappeared pattern"],
            "examined": [],
            "candidates": [],
            "notes": "nothing-fetched: no public repository candidates were fetched.",
        }
    )
    preserved = engineering_precedent_store.audit("state update")

    assert preserved is not None
    assert [attempt["attempt"] for attempt in preserved["research"]] == [1, 2]
    assert [candidate["snippet"] for candidate in preserved["candidates"]] == ["setState(prev => prev + 1)"]


def test_existing_store_without_term_key_columns_is_backfilled(monkeypatch, tmp_path):
    db_path = tmp_path / "engineering_precedent_store.sqlite3"
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(db_path))
    with sqlite3.connect(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE research (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                term TEXT NOT NULL,
                attempt INTEGER NOT NULL,
                captured_at REAL NOT NULL,
                last_searched_at REAL NOT NULL,
                notes TEXT NOT NULL,
                search_keywords TEXT NOT NULL,
                examined TEXT NOT NULL
            );
            CREATE TABLE candidates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                term TEXT NOT NULL,
                kind TEXT NOT NULL DEFAULT 'implementation',
                snippet TEXT NOT NULL,
                summary TEXT NOT NULL,
                pitfalls TEXT NOT NULL,
                structure TEXT NOT NULL DEFAULT '',
                rationale TEXT NOT NULL DEFAULT '',
                when_to_use TEXT NOT NULL DEFAULT '',
                when_not_to_use TEXT NOT NULL DEFAULT '',
                tradeoffs TEXT NOT NULL DEFAULT '',
                alternatives TEXT NOT NULL DEFAULT '',
                implementation_hooks TEXT NOT NULL DEFAULT '',
                quality_attributes TEXT NOT NULL DEFAULT '',
                evidence TEXT NOT NULL DEFAULT '',
                delta_claim TEXT NOT NULL DEFAULT '',
                lang_env_version TEXT NOT NULL,
                author_level TEXT NOT NULL,
                source_url TEXT NOT NULL,
                found_via TEXT NOT NULL
            );
            """
        )
        connection.execute(
            """
            INSERT INTO research(term, attempt, captured_at, last_searched_at, notes, search_keywords, examined)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "Dungeon Exploration System",
                1,
                100.0,
                100.0,
                "",
                json.dumps(["dungeon exploration"]),
                json.dumps([]),
            ),
        )
        connection.execute(
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
            (
                "Dungeon Exploration System",
                "enter_room(); resolve_encounter()",
                "Resolve the room loop before moving deeper.",
                "Keep exit reveal after encounter resolution.",
                "Python 3.12",
                "high",
                "https://github.com/example/dungeon/blob/HEAD/explore.py",
                "dungeon exploration",
            ),
        )

    lookup = engineering_precedent_store.lookup("dungeon exploration", {"language": "Python", "version": "3.12"})
    second_lookup = engineering_precedent_store.lookup("dungeon exploration system", {"language": "Python", "version": "3.12"})
    audit = engineering_precedent_store.audit("dungeon exploration")

    assert lookup is not None
    assert second_lookup is not None
    assert lookup["candidates"][0]["snippet"] == "enter_room(); resolve_encounter()"
    assert audit["candidates"][0]["evidence_class"] == "unclassified"
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        research_keys = connection.execute("SELECT term_key FROM research").fetchall()
        candidate_keys = connection.execute("SELECT term_key FROM candidates").fetchall()
        research_columns = [row["name"] for row in connection.execute("PRAGMA table_info(research)").fetchall()]
        candidate_columns = [row["name"] for row in connection.execute("PRAGMA table_info(candidates)").fetchall()]

    assert [row["term_key"] for row in research_keys] == ["dungeon exploration"]
    assert [row["term_key"] for row in candidate_keys] == ["dungeon exploration"]
    assert research_columns.count("term_key") == 1
    assert candidate_columns.count("term_key") == 1
    assert candidate_columns.count("evidence_class") == 1


def test_existing_store_history_and_deprecation_migration_is_idempotent(monkeypatch, tmp_path):
    db_path = tmp_path / "engineering_precedent_store.sqlite3"
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(db_path))
    with sqlite3.connect(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE research (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                term TEXT NOT NULL,
                term_key TEXT NOT NULL DEFAULT '',
                attempt INTEGER NOT NULL,
                captured_at REAL NOT NULL,
                last_searched_at REAL NOT NULL,
                notes TEXT NOT NULL,
                search_keywords TEXT NOT NULL,
                examined TEXT NOT NULL
            );
            CREATE TABLE candidates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                term TEXT NOT NULL,
                term_key TEXT NOT NULL DEFAULT '',
                kind TEXT NOT NULL DEFAULT 'implementation',
                snippet TEXT NOT NULL,
                summary TEXT NOT NULL,
                pitfalls TEXT NOT NULL,
                structure TEXT NOT NULL DEFAULT '',
                rationale TEXT NOT NULL DEFAULT '',
                when_to_use TEXT NOT NULL DEFAULT '',
                when_not_to_use TEXT NOT NULL DEFAULT '',
                tradeoffs TEXT NOT NULL DEFAULT '',
                alternatives TEXT NOT NULL DEFAULT '',
                implementation_hooks TEXT NOT NULL DEFAULT '',
                quality_attributes TEXT NOT NULL DEFAULT '',
                evidence TEXT NOT NULL DEFAULT '',
                delta_claim TEXT NOT NULL DEFAULT '',
                lang_env_version TEXT NOT NULL,
                author_level TEXT NOT NULL,
                source_url TEXT NOT NULL,
                found_via TEXT NOT NULL,
                evidence_class TEXT NOT NULL DEFAULT ''
            );
            """
        )
        connection.execute(
            """
            INSERT INTO research(term, term_key, attempt, captured_at, last_searched_at, notes, search_keywords, examined)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "state update",
                "state update",
                1,
                100.0,
                100.0,
                "",
                json.dumps(["react state update"]),
                json.dumps([]),
            ),
        )
        connection.execute(
            """
            INSERT INTO candidates(
                term,
                term_key,
                snippet,
                summary,
                pitfalls,
                lang_env_version,
                author_level,
                source_url,
                found_via
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "state update",
                "state update",
                "setState(prev => prev + 1)",
                "Functional update.",
                "Avoid stale closures.",
                "React 18 hooks",
                "medium",
                "https://github.com/example/react/blob/HEAD/state.jsx",
                "react state update",
            ),
        )

    first = engineering_precedent_store.audit("state update")
    second = engineering_precedent_store.audit("state update")

    assert first["candidates"][0]["snippet"] == "setState(prev => prev + 1)"
    assert second["candidates"][0]["snippet"] == "setState(prev => prev + 1)"
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        research_columns = [row["name"] for row in connection.execute("PRAGMA table_info(research)").fetchall()]
        candidate_columns = [row["name"] for row in connection.execute("PRAGMA table_info(candidates)").fetchall()]
        history_columns = [row["name"] for row in connection.execute("PRAGMA table_info(reference_history)").fetchall()]
        cache_columns = [row["name"] for row in connection.execute("PRAGMA table_info(gh_search_response_cache)").fetchall()]
        trigger_names = [
            row["name"]
            for row in connection.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'trigger' AND name LIKE 'reference_history_after_%'
                ORDER BY name
                """
            ).fetchall()
        ]

    assert research_columns.count("deprecated_at") == 1
    assert research_columns.count("deprecated_reason") == 1
    assert candidate_columns.count("deprecated_at") == 1
    assert candidate_columns.count("deprecated_reason") == 1
    assert history_columns == ["id", "ts", "op", "term", "detail"]
    assert cache_columns == ["command_key", "search_kind", "query", "captured_at", "response_json"]
    assert trigger_names == [
        "reference_history_after_candidate_insert",
        "reference_history_after_research_insert",
    ]


def test_query_filters_candidates_by_term_lang_author_and_keyword(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(tmp_path / "engineering_precedent_store.sqlite3"))
    engineering_precedent_store._write_entry(
        {
            "term": "state update",
            "search_keywords": ["react state update"],
            "examined": [
                {"repo": "example/react", "language": "JavaScript", "outcome": "kept", "found_via": "react state update"},
            ],
            "candidates": [
                {
                    "snippet": "setState(prev => prev + 1)",
                    "summary": "Functional update.",
                    "source_url": "https://github.com/example/react/blob/HEAD/state.jsx",
                    "lang_env_version": "React 18 hooks",
                    "author_level": "medium",
                    "pitfalls": "Avoid stale closures.",
                    "found_via": "react state update",
                },
            ],
            "notes": "",
        }
    )
    engineering_precedent_store._write_entry(
        {
            "term": "state update",
            "search_keywords": ["python state update"],
            "examined": [
                {"repo": "example/python", "language": "Python", "outcome": "kept", "found_via": "python state update"},
            ],
            "candidates": [
                {
                    "snippet": "self.value += 1",
                    "summary": "Plain Python mutation.",
                    "source_url": "https://github.com/example/python/blob/HEAD/state.py",
                    "lang_env_version": "Python 3.12",
                    "author_level": "low",
                    "pitfalls": "Not a React pattern.",
                    "found_via": "python state update",
                },
            ],
            "notes": "",
        }
    )

    results = engineering_precedent_store.query(
        {
            "term": "state update",
            "lang_env_version": "Python",
            "author_level": "low",
            "keyword": "python state",
        }
    )

    assert len(results) == 1
    assert results[0]["term"] == "state update"
    assert results[0]["snippet"] == "self.value += 1"
    assert results[0]["found_via"] == "python state update"


def test_lookup_and_query_filter_by_kind_and_design_append_only(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(tmp_path / "engineering_precedent_store.sqlite3"))
    engineering_precedent_store._write_entry(
        {
            "term": "state update",
            "search_keywords": ["react state update", "state update architecture"],
            "examined": [
                {"repo": "example/react", "language": "JavaScript", "outcome": "kept", "found_via": "react state update"},
                {"repo": "web/example.com", "language": "general", "outcome": "kept", "found_via": "state update architecture"},
            ],
            "candidates": [
                {
                    "kind": "implementation",
                    "snippet": "setState(prev => prev + 1)",
                    "summary": "Functional update.",
                    "source_url": "https://github.com/example/react/blob/HEAD/state.jsx",
                    "lang_env_version": "React 18 hooks",
                    "author_level": "medium",
                    "pitfalls": "Avoid stale closures.",
                    "found_via": "react state update",
                },
                _design_candidate(
                    source_url="https://example.com/state-architecture",
                    delta_claim="Use local reducer state only if replay is unnecessary; otherwise persist events.",
                )
                | {"found_via": "state update architecture"},
            ],
            "notes": "",
        }
    )
    engineering_precedent_store._write_entry(
        {
            "term": "state update",
            "search_keywords": ["state update architecture"],
            "examined": [],
            "candidates": [
                _design_candidate(
                    source_url="https://example.com/state-architecture",
                    delta_claim="Changed text that must not overwrite the original design candidate.",
                )
                | {"found_via": "state update architecture", "rationale": "Changed rationale."},
            ],
            "notes": "",
        }
    )
    engineering_precedent_store._write_entry(
        {
            "term": "state update",
            "search_keywords": ["state disappeared"],
            "examined": [],
            "candidates": [],
            "notes": "nothing-fetched: no public repository candidates were fetched.",
        }
    )

    design_lookup = engineering_precedent_store.lookup("state update", kind="design")
    implementation_lookup = engineering_precedent_store.lookup("state update", {"language": "React", "environment": "hooks", "version": "18"}, kind="implementation")
    design_query = engineering_precedent_store.query({"term": "state update", "kind": "design", "keyword": "state update architecture"})
    audit = engineering_precedent_store.audit("state update")

    assert design_lookup is not None
    assert [candidate["kind"] for candidate in design_lookup["candidates"]] == ["design"]
    assert "snippet" not in design_lookup["candidates"][0]
    assert implementation_lookup is not None
    assert [candidate["kind"] for candidate in implementation_lookup["candidates"]] == ["implementation"]
    assert implementation_lookup["candidates"][0]["snippet"] == "setState(prev => prev + 1)"
    assert len(design_query) == 1
    assert design_query[0]["kind"] == "design"
    assert "Changed text" not in design_query[0]["delta_claim"]
    assert audit is not None
    assert len(audit["candidates"]) == 2
    assert [attempt["attempt"] for attempt in audit["research"]] == [1, 2, 3]


def test_add_preheld_writes_dedups_and_lookup_returns_consumption_fields(monkeypatch, tmp_path):
    db_path = tmp_path / "engineering_precedent_store.sqlite3"
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(db_path))
    facet = {
        "structure": "Keep structured output schemas inside the Codex safe subset.",
        "rationale": "Live runs showed invalid_json_schema failures for unsupported keywords.",
        "when_to_use": "Use for every Codex output schema.",
        "when_not_to_use": "Do not use broad JSON Schema constraints with Codex output schemas.",
        "tradeoffs": "Move value constraints into prompts and deterministic post-validation.",
        "alternatives": "Full JSON Schema was rejected by live Codex runs.",
        "implementation_hooks": "Guard schemas in tests and post-validate parsed values.",
        "quality_attributes": "Reliability and deterministic validation.",
        "evidence": "ai-org-bootstrap-codex@345bc17; tests/test_codex_output_schema_guard.py.",
        "delta_claim": "The Codex schema subset is smaller than ordinary JSON Schema.",
        "source_url": "ai-org-bootstrap-codex@345bc17; tests/test_codex_output_schema_guard.py",
    }

    first = engineering_precedent_store.add_preheld("codex output schema safe subset", "design", facet)
    changed = {**facet, "structure": "Changed text that must not overwrite the original candidate."}
    second = engineering_precedent_store.add_preheld("codex output schema safe subset", "design", changed)

    lookup = engineering_precedent_store.lookup("codex output schema safe subset", kind="design")
    audit = engineering_precedent_store.audit("codex output schema safe subset")
    with sqlite3.connect(db_path) as connection:
        research_count = connection.execute("SELECT count(*) FROM research").fetchone()[0]
        candidate_count = connection.execute("SELECT count(*) FROM candidates").fetchone()[0]

    assert first["search_keywords"] == ["org-preheld"]
    assert second["search_keywords"] == ["org-preheld"]
    assert audit is not None
    assert lookup is not None
    assert research_count == 2
    assert candidate_count == 1
    assert [attempt["search_keywords"] for attempt in audit["research"]] == [["org-preheld"], ["org-preheld"]]
    assert audit["research"][0]["examined"] == [
        {
            "repo": "ai-org-bootstrap-codex@345bc17; tests/test_codex_output_schema_guard.py",
            "language": "general",
            "outcome": "kept",
            "found_via": "org-preheld",
        }
    ]
    assert audit["candidates"][0]["structure"] == facet["structure"]
    assert "Changed text" not in audit["candidates"][0]["structure"]
    assert lookup["candidates"][0]["author_level"] == "org-experience (primary; learned from live runs)"
    assert lookup["candidates"][0]["source_url"] == "ai-org-bootstrap-codex@345bc17; tests/test_codex_output_schema_guard.py"
    assert lookup["candidates"][0]["kind"] == "design"
    assert "found_via" not in lookup["candidates"][0]


def test_seed_preheld_org_lessons_is_idempotent_and_append_only(monkeypatch, tmp_path):
    db_path = tmp_path / "engineering_precedent_store.sqlite3"
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(db_path))

    first = engineering_precedent_store.seed_preheld_org_lessons()
    with sqlite3.connect(db_path) as connection:
        first_research_count = connection.execute("SELECT count(*) FROM research").fetchone()[0]
        first_candidate_count = connection.execute("SELECT count(*) FROM candidates").fetchone()[0]
        first_candidate_ids = [row[0] for row in connection.execute("SELECT id FROM candidates ORDER BY id").fetchall()]
    second = engineering_precedent_store.seed_preheld_org_lessons()
    with sqlite3.connect(db_path) as connection:
        second_research_count = connection.execute("SELECT count(*) FROM research").fetchone()[0]
        second_candidate_count = connection.execute("SELECT count(*) FROM candidates").fetchone()[0]
        second_candidate_ids = [row[0] for row in connection.execute("SELECT id FROM candidates ORDER BY id").fetchall()]

    assert first["stored"] == 10
    assert first["skipped"] == 0
    assert second["stored"] == 0
    assert second["skipped"] == 10
    assert first_research_count == 10
    assert first_candidate_count == 10
    assert second_research_count == first_research_count
    assert second_candidate_count == first_candidate_count
    assert second_candidate_ids == first_candidate_ids

    lookup = engineering_precedent_store.lookup("required all props payload artifact tolerance", kind="design")
    assert lookup is not None
    assert len(lookup["candidates"]) == 1
    candidate = lookup["candidates"][0]
    assert candidate["author_level"] == "org-experience (primary; learned from live runs)"
    assert "mode-irrelevant payload" in candidate["structure"]
    assert candidate["source_url"] == "ai-org-bootstrap-codex@871c99a; ai-org-bootstrap-codex@2f5f13b"

    lookup = engineering_precedent_store.lookup("group tree merges implementations not doc nodes", kind="design")
    assert lookup is not None
    assert len(lookup["candidates"]) == 1
    candidate = lookup["candidates"][0]
    assert "Child patch series branches are doc nodes" in candidate["structure"]
    assert candidate["source_url"] == "ai-org-bootstrap-codex@this-commit"

    lookup = engineering_precedent_store.lookup("vector figure base construction", kind="design")
    assert lookup is not None
    assert len(lookup["candidates"]) == 1
    candidate = lookup["candidates"][0]
    assert "reusable base primitives" in candidate["structure"]
    assert candidate["source_url"] == "ai-org-bootstrap-codex@pro-corpus-2026-07-03#open-peeps-humaaans"


def test_empty_research_rows_are_stored_and_reexpand_appends_history(monkeypatch, tmp_path):
    db_path = tmp_path / "engineering_precedent_store.sqlite3"
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(db_path))
    monkeypatch.setenv("AI_ORG_REFERENCE_TTL_SECONDS", "0")
    monkeypatch.setattr(engineering_precedent_store, "_codex_baseline", lambda term, context: "use a basic verifier")
    monkeypatch.setattr(engineering_precedent_store, "_codex_search_keywords", lambda term, context: ["oauth pkce verifier"])
    monkeypatch.setattr(engineering_precedent_store, "_search_repositories", lambda keywords, context: [])

    engineering_precedent_store.expand("PKCE verifier rotation", {"language": "TypeScript"})
    engineering_precedent_store.expand("PKCE verifier rotation", {"language": "TypeScript"})

    entry = engineering_precedent_store.audit("PKCE verifier rotation")
    assert entry is not None
    assert entry["search_keywords"] == ["oauth pkce verifier"]
    assert entry["candidates"] == []
    assert entry["notes"] == "nothing-fetched: no public repository candidates were fetched."
    assert [attempt["attempt"] for attempt in entry["research"]] == [1, 2]
    assert [attempt["search_keywords"] for attempt in entry["research"]] == [
        ["oauth pkce verifier"],
        ["oauth pkce verifier"],
    ]

    with sqlite3.connect(db_path) as connection:
        research_count = connection.execute("SELECT count(*) FROM research").fetchone()[0]
        candidate_count = connection.execute("SELECT count(*) FROM candidates").fetchone()[0]

    assert research_count == 2
    assert candidate_count == 0
    assert engineering_precedent_store.query({"term": "PKCE verifier rotation", "keyword": "oauth pkce verifier"}) == []


def test_deprecate_candidates_hides_lookup_and_query_but_audit_keeps_reason(monkeypatch, tmp_path):
    db_path = tmp_path / "engineering_precedent_store.sqlite3"
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(db_path))
    engineering_precedent_store._write_entry(
        {
            "term": "state update",
            "search_keywords": ["react state update"],
            "examined": [
                {"repo": "example/react", "language": "JavaScript", "outcome": "kept", "found_via": "react state update"},
            ],
            "candidates": [
                {
                    "snippet": "setState(prev => prev + 1)",
                    "summary": "Functional update.",
                    "source_url": "https://github.com/example/react/blob/HEAD/state.jsx",
                    "lang_env_version": "React 18 hooks",
                    "author_level": "medium",
                    "pitfalls": "Avoid stale closures.",
                    "found_via": "react state update",
                },
            ],
            "notes": "maintenance note",
        }
    )

    deprecated = engineering_precedent_store.deprecate_candidates("state update", "poisoned facet from raw SQL incident")
    lookup = engineering_precedent_store.lookup("state update", {"language": "React", "version": "18"})
    query_results = engineering_precedent_store.query({"term": "state update", "keyword": "react state update"})
    audit = engineering_precedent_store.audit("state update")
    with sqlite3.connect(db_path) as connection:
        history = connection.execute(
            "SELECT op, term, detail FROM reference_history ORDER BY id"
        ).fetchall()

    assert deprecated["candidates"] == 1
    assert deprecated["research"] == 0
    assert lookup == {"term": "state update", "candidates": []}
    assert query_results == []
    assert audit["candidates"][0]["deprecated_reason"] == "poisoned facet from raw SQL incident"
    assert audit["candidates"][0]["deprecated_at"]
    assert [row[0] for row in history] == ["insert_research", "insert_candidate", "deprecate_candidates"]
    assert json.loads(history[-1][2])["reason"] == "poisoned facet from raw SQL incident"


def test_deprecate_term_hides_research_and_records_reason_in_audit(monkeypatch, tmp_path):
    db_path = tmp_path / "engineering_precedent_store.sqlite3"
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(db_path))
    engineering_precedent_store._write_entry(
        {
            "term": "obsolete canon",
            "search_keywords": [],
            "examined": [],
            "candidates": [],
            "notes": "old term-level research",
        }
    )

    deprecated = engineering_precedent_store.deprecate_term("obsolete canon", "term superseded by new canon")
    lookup = engineering_precedent_store.lookup("obsolete canon", kind="design")
    audit = engineering_precedent_store.audit("obsolete canon")

    assert deprecated["research"] == 1
    assert deprecated["candidates"] == 0
    assert lookup == {"term": "obsolete canon", "candidates": []}
    assert audit["research"][0]["deprecated_reason"] == "term superseded by new canon"
    assert audit["research"][0]["deprecated_at"]


def test_lookup_unknown_term_in_valid_store_returns_empty_candidates(monkeypatch, tmp_path):
    db_path = tmp_path / "engineering_precedent_store.sqlite3"
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(db_path))
    engineering_precedent_store._write_entry(
        {
            "term": "known canon",
            "search_keywords": [],
            "examined": [],
            "candidates": [],
            "notes": "",
        }
    )

    result = engineering_precedent_store.lookup("unknown canon", kind="design")

    assert result == {"term": "unknown canon", "candidates": []}


def test_default_store_path_is_used_without_env(monkeypatch, tmp_path):
    monkeypatch.delenv("AI_ORG_REFERENCE_STORE", raising=False)
    db_path = tmp_path / "aiorg_assets" / "engineering_precedent_store" / "org.sqlite3"
    monkeypatch.setattr(engineering_precedent_store, "DEFAULT_STORE_PATH", db_path)
    engineering_precedent_store._write_entry(
        {
            "term": "known canon",
            "search_keywords": [],
            "examined": [],
            "candidates": [],
            "notes": "",
        }
    )

    result = engineering_precedent_store.lookup("known canon", kind="design")

    assert engineering_precedent_store._database_path() == db_path.resolve()
    assert db_path.exists()
    assert result == {"term": "known canon", "candidates": []}


def test_env_store_override_still_wins(monkeypatch, tmp_path):
    default_path = tmp_path / "default" / "org.sqlite3"
    override_path = tmp_path / "override" / "engineering_precedent_store.sqlite3"
    monkeypatch.setattr(engineering_precedent_store, "DEFAULT_STORE_PATH", default_path)
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(override_path))
    engineering_precedent_store._write_entry(
        {
            "term": "override canon",
            "search_keywords": [],
            "examined": [],
            "candidates": [],
            "notes": "",
        }
    )

    assert engineering_precedent_store._database_path() == override_path.resolve()
    assert override_path.exists()
    assert not default_path.exists()


def test_lookup_with_missing_default_store_raises_store_unavailable(monkeypatch, tmp_path):
    missing = tmp_path / "missing-default.sqlite3"
    monkeypatch.delenv("AI_ORG_REFERENCE_STORE", raising=False)
    monkeypatch.setattr(engineering_precedent_store, "DEFAULT_STORE_PATH", missing)

    with pytest.raises(engineering_precedent_store.StoreUnavailable) as exc_info:
        engineering_precedent_store.lookup("anything", {})

    assert exc_info.value.reason == "missing"


def test_lookup_with_missing_store_raises_store_unavailable(monkeypatch, tmp_path):
    missing = tmp_path / "missing.sqlite3"
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(missing))

    with pytest.raises(engineering_precedent_store.StoreUnavailable) as exc_info:
        engineering_precedent_store.lookup("anything", {})

    assert exc_info.value.reason == "missing"


def test_lookup_with_unreadable_store_raises_store_unavailable(monkeypatch, tmp_path):
    db_path = tmp_path / "engineering_precedent_store.sqlite3"
    db_path.write_bytes(b"")
    db_path.chmod(0)
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(db_path))

    try:
        with pytest.raises(engineering_precedent_store.StoreUnavailable) as exc_info:
            engineering_precedent_store.lookup("anything", {})
    finally:
        db_path.chmod(0o600)

    assert exc_info.value.reason == "unreadable"


def test_build_from_patch_series_extracts_terms_once_and_expands_only_returned_terms(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(tmp_path / "engineering_precedent_store.sqlite3"))
    monkeypatch.setenv("AI_ORG_REFERENCE_PARALLEL", "1")
    codex_calls = []
    expanded = []

    def codex_json(prompt, schema, output_name):
        codex_calls.append((prompt, schema, output_name))
        return {
            "terms": [
                "OAuth PKCE verifier flow",
                "browser token exchange",
                "OAuth PKCE verifier flow system",
                "browser token exchange mechanics",
            ]
        }

    def expand(term, context, force=False, **kwargs):
        expanded.append(term)
        return {"term": term, "candidates": [], "notes": "expanded"}

    monkeypatch.setattr(engineering_precedent_store, "_codex_json", codex_json)
    monkeypatch.setattr(engineering_precedent_store, "expand", expand)

    result = engineering_precedent_store.build_from_patch_series(
        {
            "title": "Add OAuth PKCE",
            "proposal": "Implement OAuth PKCE verifier flow.",
            "context": "This feature improves login. Avoid expanding generic words like members towns castles.",
        },
        {"language": "TypeScript", "environment": "browser", "version": "ES2022"},
    )

    assert len(codex_calls) == 1
    assert codex_calls[0][1] == engineering_precedent_store.REFERENCE_TERMS_SCHEMA
    assert codex_calls[0][2] == "precedent-terms.json"
    assert expanded == ["OAuth PKCE verifier flow", "browser token exchange"]
    assert result["expanded"] == ["OAuth PKCE verifier flow", "browser token exchange"]
    assert set(result["terms"]) == {"OAuth PKCE verifier flow", "browser token exchange"}
    assert "feature" not in expanded
    assert "members towns castles" not in expanded


def test_expand_kinds_restricts_research_lanes(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(tmp_path / "engineering_precedent_store.sqlite3"))
    calls = []

    monkeypatch.setattr(engineering_precedent_store, "_codex_baseline", lambda term, context: calls.append("baseline") or "basic")
    monkeypatch.setattr(engineering_precedent_store, "_codex_search_keywords", lambda term, context: calls.append("keywords") or [])
    monkeypatch.setattr(engineering_precedent_store, "fetch_candidates", lambda term, context: calls.append("implementation") or [])
    monkeypatch.setattr(engineering_precedent_store, "fetch_design_candidates", lambda term, context: calls.append("design") or [])

    engineering_precedent_store.expand("lane split design", {}, kinds=("design",))
    assert calls == ["design"]

    calls.clear()
    engineering_precedent_store.expand("lane split implementation", {}, kinds=("implementation",))
    assert calls == ["baseline", "keywords", "implementation"]


def test_build_from_patch_series_passes_kinds_to_expand(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(tmp_path / "engineering_precedent_store.sqlite3"))
    monkeypatch.setenv("AI_ORG_REFERENCE_PARALLEL", "1")
    monkeypatch.setattr(engineering_precedent_store, "_extract_precedent_terms", lambda text, context: ["design term", "implementation term"])
    monkeypatch.setattr(engineering_precedent_store, "lookup", lambda term, context, kind=None: None)
    calls = []

    def expand(term, context, force=False, kinds=None):
        calls.append((term, kinds))
        return {"term": term, "candidates": [], "notes": "expanded"}

    monkeypatch.setattr(engineering_precedent_store, "expand", expand)

    result = engineering_precedent_store.build_from_patch_series({"proposal": "Build split lanes."}, {}, kinds=("design",))

    assert calls == [("design term", ("design",)), ("implementation term", ("design",))]
    assert result["processed_terms"] == ["design term", "implementation term"]


def test_build_from_patch_series_propagates_force_to_expand(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(tmp_path / "engineering_precedent_store.sqlite3"))
    calls = []
    monkeypatch.setattr(engineering_precedent_store, "_extract_precedent_terms", lambda text, context: ["oauth verifier"])
    monkeypatch.setattr(engineering_precedent_store, "lookup", lambda term, context: None)

    def expand(term, context, force=False, **kwargs):
        calls.append((term, force))
        return {"term": term, "candidates": [], "notes": "expanded"}

    monkeypatch.setattr(engineering_precedent_store, "expand", expand)

    result = engineering_precedent_store.build_from_patch_series({"proposal": "Implement OAuth verifier."}, {"language": "TypeScript"}, force=True)

    assert calls == [("oauth verifier", True)]
    assert result["expanded"] == ["oauth verifier"]


def test_build_from_patch_series_uses_bounded_pool_and_isolates_term_failures(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(tmp_path / "engineering_precedent_store.sqlite3"))
    monkeypatch.setenv("AI_ORG_REFERENCE_PARALLEL", "3")
    monkeypatch.setattr(engineering_precedent_store, "_extract_precedent_terms", lambda text, context: ["alpha term", "bad term", "gamma term"])
    monkeypatch.setattr(engineering_precedent_store, "lookup", lambda term, context: None)

    max_workers = []
    real_executor = concurrent.futures.ThreadPoolExecutor

    class RecordingExecutor(real_executor):
        def __init__(self, *args, **kwargs):
            max_workers.append(kwargs.get("max_workers"))
            super().__init__(*args, **kwargs)

    active = 0
    max_active = 0
    active_lock = threading.Lock()

    def expand(term, context, force=False, **kwargs):
        nonlocal active, max_active
        with active_lock:
            active += 1
            max_active = max(max_active, active)
        try:
            time.sleep(0.05)
            if term == "bad term":
                raise RuntimeError("network failed")
            return {"term": term, "candidates": [], "notes": "expanded"}
        finally:
            with active_lock:
                active -= 1

    monkeypatch.setattr(engineering_precedent_store.concurrent.futures, "ThreadPoolExecutor", RecordingExecutor)
    monkeypatch.setattr(engineering_precedent_store, "expand", expand)

    result = engineering_precedent_store.build_from_patch_series({"proposal": "alpha beta gamma"}, {"language": "Python"})

    assert max_workers == [3]
    assert max_active > 1
    assert result["expanded"] == ["alpha term", "gamma term"]
    assert result["hits"] == []
    assert set(result["terms"]) == {"alpha term", "gamma term"}
    assert result["failed"] == {"bad term": "RuntimeError: network failed"}

    monkeypatch.setenv("AI_ORG_REFERENCE_PARALLEL", "8")
    assert engineering_precedent_store._precedent_parallelism(10) == 8


def test_background_build_failure_is_captured_in_future(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(tmp_path / "engineering_precedent_store.sqlite3"))

    def fail_build(*args, **kwargs):
        raise RuntimeError("background failed")

    monkeypatch.setattr(engineering_precedent_store, "build_from_patch_series", fail_build)

    future = engineering_precedent_store.start_background_build({"proposal": "Build async."}, {}, kinds=("implementation",))
    engineering_precedent_store.await_background_builds(timeout=5)

    assert future.done()
    assert future.exception() is None
    assert future.result()["ok"] is False
    assert "RuntimeError: background failed" in future.result()["error"]


def test_build_from_patch_series_parallel_expand_writes_all_sqlite_rows(monkeypatch, tmp_path):
    db_path = tmp_path / "engineering_precedent_store.sqlite3"
    terms = ["alpha cache", "beta queue", "gamma lock", "delta retry"]

    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(db_path))
    monkeypatch.setenv("AI_ORG_REFERENCE_PARALLEL", "4")
    monkeypatch.setattr(engineering_precedent_store, "_extract_precedent_terms", lambda text, context: terms)
    monkeypatch.setattr(engineering_precedent_store, "_codex_baseline", lambda term, context: f"basic {term}")
    monkeypatch.setattr(engineering_precedent_store, "_codex_search_keywords", lambda term, context: [f"{term} implementation"])
    monkeypatch.setattr(engineering_precedent_store, "_codex_delta_inclusion", lambda term, context, baseline, candidate: {"keep": True, "reason": "real delta"})
    monkeypatch.setattr(
        engineering_precedent_store,
        "_codex_distill_candidate",
        lambda term, context, baseline, candidate, reason: {
            "snippet": f"def {term.replace(' ', '_')}(): pass",
            "summary": f"Useful implementation detail for {term}.",
            "lang_env_version": "Python 3.12",
            "pitfalls": f"Handle the {term} edge case.",
        },
    )
    monkeypatch.setattr(engineering_precedent_store, "_codex_author_level", lambda term, context, candidate: {"author_level": "high", "reason": "clear"})

    def fetch_candidates(term, context):
        time.sleep(0.02)
        return [
            {
                "snippet": f"raw {term}",
                "summary": f"Raw summary for {term}.",
                "source_url": f"https://github.com/example/{term.replace(' ', '-')}/blob/HEAD/main.py",
                "lang_env_version": "Python 3.12",
                "author_level": "unknown",
                "pitfalls": f"Raw pitfall for {term}.",
                "found_via": f"{term} implementation",
                "_precedent_repo": f"example/{term.replace(' ', '-')}",
                "_precedent_language": "Python",
                "_precedent_found_via": f"{term} implementation",
            }
        ]

    monkeypatch.setattr(engineering_precedent_store, "fetch_candidates", fetch_candidates)

    result = engineering_precedent_store.build_from_patch_series({"proposal": "Build several implementation details."}, {"language": "Python"})

    assert result["expanded"] == terms
    assert result["failed"] == {}
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
        research_rows = connection.execute("SELECT term, search_keywords FROM research ORDER BY term").fetchall()
        candidate_rows = connection.execute("SELECT term, snippet, found_via FROM candidates ORDER BY term").fetchall()

    assert journal_mode == "wal"
    assert [row["term"] for row in research_rows] == sorted(terms)
    assert [row["term"] for row in candidate_rows] == sorted(terms)
    assert all(json.loads(row["search_keywords"]) == [f"{row['term']} implementation"] for row in research_rows)
    assert all(row["snippet"].startswith("def ") for row in candidate_rows)


def test_background_implementation_write_and_foreground_design_build_share_wal_store(monkeypatch, tmp_path):
    db_path = tmp_path / "engineering_precedent_store.sqlite3"
    impl_started = threading.Event()
    release_impl = threading.Event()

    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(db_path))
    monkeypatch.setenv("AI_ORG_REFERENCE_PARALLEL", "1")
    monkeypatch.setattr(engineering_precedent_store, "_extract_precedent_terms", lambda text, context: ["concurrent term"])
    monkeypatch.setattr(engineering_precedent_store, "_codex_baseline", lambda term, context: "basic concurrent term")
    monkeypatch.setattr(engineering_precedent_store, "_codex_search_keywords", lambda term, context: ["concurrent term implementation"])
    monkeypatch.setattr(engineering_precedent_store, "_codex_delta_inclusion", lambda term, context, baseline, candidate: {"keep": True, "reason": "real delta"})
    monkeypatch.setattr(
        engineering_precedent_store,
        "_codex_distill_candidate",
        lambda term, context, baseline, candidate, reason: {
            "snippet": "def concurrent_term(): pass",
            "summary": "Useful implementation detail for concurrent term.",
            "lang_env_version": "Python 3.12",
            "pitfalls": "Keep the write short.",
        },
    )
    monkeypatch.setattr(engineering_precedent_store, "_codex_author_level", lambda term, context, candidate: {"author_level": "high", "reason": "clear"})
    monkeypatch.setattr(engineering_precedent_store, "_codex_design_delta_inclusion", lambda term, context, candidate: {"keep": True, "reason": "real design"})
    monkeypatch.setattr(engineering_precedent_store, "_codex_design_competence", lambda term, context, candidate: {"keep": True, "author_level": "high", "reason": "clear"})

    def fetch_candidates(term, context):
        impl_started.set()
        assert release_impl.wait(5)
        return [
            {
                "kind": "implementation",
                "snippet": "raw concurrent term",
                "summary": "Raw implementation summary.",
                "source_url": "https://github.com/example/concurrent/blob/HEAD/main.py",
                "lang_env_version": "Python 3.12",
                "author_level": "unknown",
                "pitfalls": "Raw pitfall.",
                "found_via": "concurrent term implementation",
            }
        ]

    def fetch_design_candidates(term, context):
        assert impl_started.wait(5)
        return [_design_candidate("https://example.com/concurrent-design")]

    monkeypatch.setattr(engineering_precedent_store, "fetch_candidates", fetch_candidates)
    monkeypatch.setattr(engineering_precedent_store, "fetch_design_candidates", fetch_design_candidates)

    future = engineering_precedent_store.start_background_build({"proposal": "Build concurrent term."}, {}, kinds=("implementation",))
    design_result = engineering_precedent_store.build_from_patch_series({"proposal": "Build concurrent term."}, {}, kinds=("design",))
    release_impl.set()
    engineering_precedent_store.await_background_builds(timeout=5)

    assert future.done()
    assert future.exception() is None
    assert design_result["expanded"] == ["concurrent term"]
    assert future.result()["expanded"] == ["concurrent term"]
    with sqlite3.connect(db_path) as connection:
        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
        kinds = [row[0] for row in connection.execute("SELECT kind FROM candidates ORDER BY kind").fetchall()]

    assert journal_mode == "wal"
    assert kinds == ["design", "implementation"]


def test_precedent_store_rejects_work_repo_paths(monkeypatch):
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(engineering_precedent_store.REPO_ROOT / ".ai-org-engineering_precedent_store"))

    with pytest.raises(ValueError, match="outside the work repo"):
        engineering_precedent_store.lookup("anything", {})


def test_precedent_schemas_are_codex_valid():
    for schema in [
        engineering_precedent_store.BASELINE_SCHEMA,
        engineering_precedent_store.SEARCH_KEYWORDS_SCHEMA,
        engineering_precedent_store.DESIGN_SEARCH_KEYWORDS_SCHEMA,
        engineering_precedent_store.EXTRACT_SCHEMA,
        engineering_precedent_store.DESIGN_SOURCE_SCHEMA,
        engineering_precedent_store.DESIGN_EXTRACT_SCHEMA,
        engineering_precedent_store.DELTA_SCHEMA,
        engineering_precedent_store.DESIGN_COMPETENCE_SCHEMA,
        engineering_precedent_store.DISTILL_SCHEMA,
        engineering_precedent_store.AUTHOR_LEVEL_SCHEMA,
        engineering_precedent_store.REFERENCE_TERMS_SCHEMA,
    ]:
        serialized = json.dumps(schema)
        assert "allOf" not in serialized
        assert "anyOf" not in serialized
        assert "oneOf" not in serialized
        _assert_required_is_all_properties(schema)
    for schema in [engineering_precedent_store.EXTRACT_SCHEMA, engineering_precedent_store.DESIGN_EXTRACT_SCHEMA]:
        evidence_class = schema["properties"]["evidence_class"]
        assert "evidence_class" in schema["required"]
        assert evidence_class == {"type": "string", "enum": list(engineering_precedent_store.EVIDENCE_CLASSES)}


def test_precedent_imports_no_pipeline_or_archive_modules():
    tree = ast.parse(Path(engineering_precedent_store.__file__).read_text(encoding="utf-8"))
    forbidden = {"ai_org.patchwork_queue", "ai_org.maintainer_merge", "archive"}
    imports = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)

    assert not {name for name in imports if name in forbidden or any(name.startswith(f"{item}.") for item in forbidden)}


def test_precedent_module_contains_no_japanese_text():
    text = Path(engineering_precedent_store.__file__).read_text(encoding="utf-8")
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


def _assert_general_search_keywords(keywords):
    forbidden = engineering_precedent_store.SEARCH_KEYWORD_QUALIFIER_TOKENS
    for keyword in keywords:
        tokens = engineering_precedent_store._search_keyword_tokens(keyword)
        assert engineering_precedent_store.SEARCH_KEYWORD_MIN_TOKENS <= len(tokens) <= engineering_precedent_store.SEARCH_KEYWORD_MAX_TOKENS
        assert not (set(tokens) & forbidden)


# --- write-exact / paraphrase-tolerant reads / keys-never-destroyed (ratified 2026-07-04) ---


def _empty_entry(term, notes=""):
    return {
        "term": term,
        "search_keywords": [],
        "examined": [],
        "candidates": [],
        "notes": notes,
    }


def _snippet_entry(term, snippet, notes=""):
    return {
        "term": term,
        "search_keywords": ["shared search keyword"],
        "examined": [],
        "candidates": [
            {
                "snippet": snippet,
                "summary": "Stored candidate.",
                "source_url": f"https://github.com/example/{term.replace(' ', '-')}",
                "lang_env_version": "Python 3.12",
                "author_level": "medium",
                "pitfalls": "None recorded.",
                "found_via": "shared search keyword",
            }
        ],
        "notes": notes,
    }


def _dump_knowledge_rows(db_path):
    connection = sqlite3.connect(db_path)
    try:
        research = connection.execute(
            "SELECT term, term_key, attempt, notes, search_keywords, examined FROM research ORDER BY id"
        ).fetchall()
        candidates = connection.execute(
            "SELECT term, term_key, kind, snippet, source_url FROM candidates ORDER BY id"
        ).fetchall()
    finally:
        connection.close()
    return research, candidates


def test_write_round_trip_preserves_keys_byte_for_byte_including_japanese(monkeypatch, tmp_path):
    db_path = tmp_path / "engineering_precedent_store.sqlite3"
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(db_path))
    # Decomposed dakuten (ha + combining voiced mark) and an ideographic space:
    # NFC/NFKC would rewrite both, so any normalization on the write path shows.
    term = "ターン制バトル　コマンド・メニュー"

    # Guard: the fixture must stay genuinely decomposed. If an editor ever
    # NFC-normalizes this file, the round-trip test silently weakens.
    assert term != unicodedata.normalize("NFC", term)

    engineering_precedent_store._write_entry(_empty_entry(term, notes="first"))
    engineering_precedent_store._write_entry(_empty_entry(term, notes="second"))

    connection = sqlite3.connect(db_path)
    try:
        stored_terms = [row[0] for row in connection.execute("SELECT term FROM research ORDER BY id").fetchall()]
    finally:
        connection.close()
    assert stored_terms == [term, term]
    assert all(stored.encode("utf-8") == term.encode("utf-8") for stored in stored_terms)

    audited = engineering_precedent_store.audit(term)
    assert audited["term"].encode("utf-8") == term.encode("utf-8")


def test_paraphrase_read_unifies_nfc_variants_without_touching_stored_key(monkeypatch, tmp_path):
    db_path = tmp_path / "engineering_precedent_store.sqlite3"
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(db_path))
    stored_term = "ターン制バトル　コマンド・メニュー"
    engineering_precedent_store._write_entry(_empty_entry(stored_term, notes="japanese canon"))

    # Composed batoru, plain spaces, reordered tokens: exact key misses, the
    # frozen W1 tokenizer (NFC + casefold + punctuation split) must still hit.
    hit = engineering_precedent_store.lookup("メニュー コマンド ターン制バトル")

    assert hit["term"].encode("utf-8") == stored_term.encode("utf-8")


def test_lookup_paraphrase_hits_reworded_and_reordered_queries(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(tmp_path / "engineering_precedent_store.sqlite3"))
    engineering_precedent_store._write_entry(_snippet_entry("battle command menu", "open_menu()"))

    reworded = engineering_precedent_store.lookup("turn based battle command menu")
    reordered = engineering_precedent_store.lookup("Menu, Command... BATTLE")

    assert reworded["term"] == "battle command menu"
    assert reworded["candidates"][0]["snippet"] == "open_menu()"
    assert reordered["term"] == "battle command menu"


def test_lookup_paraphrase_recent_research_skip_prevents_duplicate_search(monkeypatch, tmp_path):
    now = {"value": 5000.0}
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(tmp_path / "engineering_precedent_store.sqlite3"))
    monkeypatch.setattr(engineering_precedent_store.time, "time", lambda: now["value"])
    engineering_precedent_store._write_entry(_empty_entry("battle command menu", notes="researched"))

    now["value"] = 5100.0
    monkeypatch.setattr(engineering_precedent_store, "_codex_baseline", lambda term, context: pytest.fail("baseline should be skipped"))
    monkeypatch.setattr(engineering_precedent_store, "_codex_search_keywords", lambda term, context: pytest.fail("keywords should be skipped"))
    monkeypatch.setattr(engineering_precedent_store, "fetch_candidates", lambda term, context: pytest.fail("fetch should be skipped"))

    entry = engineering_precedent_store.expand("turn based battle command menu", {"language": "Python"})

    assert entry["term"] == "battle command menu"
    assert [attempt["last_searched_at"] for attempt in entry["research"]] == [5000.0]


def test_lookup_exact_match_short_circuits_paraphrase(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(tmp_path / "engineering_precedent_store.sqlite3"))
    engineering_precedent_store._write_entry(_snippet_entry("battle command menu", "exact()"))
    engineering_precedent_store._write_entry(_snippet_entry("battle command menu layout", "fuzzy()"))

    hit = engineering_precedent_store.lookup("battle command menu")

    assert hit["term"] == "battle command menu"
    assert [candidate["snippet"] for candidate in hit["candidates"]] == ["exact()"]


def test_paraphrase_ranking_prefers_higher_fraction_then_key_bytes(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(tmp_path / "engineering_precedent_store.sqlite3"))
    engineering_precedent_store._write_entry(_snippet_entry("delta gamma beta", "full_overlap()"))
    engineering_precedent_store._write_entry(_snippet_entry("beta gamma", "partial_overlap()"))

    full = engineering_precedent_store.lookup("beta gamma delta")

    # 3/3 query tokens beat 2/3 regardless of insertion order.
    assert full["term"] == "delta gamma beta"

    engineering_precedent_store._write_entry(_snippet_entry("epsilon gamma", "tied_b()"))
    tied = engineering_precedent_store.lookup("beta epsilon gamma")

    # "beta gamma" and "epsilon gamma" both match 2/3 of the query tokens and
    # 2/2 of their own tokens; the deterministic tiebreak is key byte order.
    assert tied["term"] == "beta gamma"


def test_paraphrase_below_overlap_floor_reports_miss(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(tmp_path / "engineering_precedent_store.sqlite3"))
    engineering_precedent_store._write_entry(_snippet_entry("battle command menu", "open_menu()"))

    miss = engineering_precedent_store.lookup("inventory sorting algorithm")
    single_shared_token = engineering_precedent_store.lookup("battle animation")

    assert miss == {"term": "inventory sorting algorithm", "candidates": []}
    assert single_shared_token == {"term": "battle animation", "candidates": []}


def test_paraphrase_directional_floor_longer_stored_key_hits(monkeypatch, tmp_path):
    # Live probe 2026-07-05: the 4-token query matched ALL of its tokens
    # against the 7-token canon key, but the symmetric stored-side floor
    # rejected the hit at 4/7=0.57. The stored floor is directional now:
    # it only fires when the stored key is SHORTER than the query.
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(tmp_path / "engineering_precedent_store.sqlite3"))
    engineering_precedent_store._write_entry(
        _snippet_entry("defer design question to implementation routing criteria", "route_to_patch()")
    )

    hit = engineering_precedent_store.lookup("defer implementation question routing")

    assert hit["term"] == "defer design question to implementation routing criteria"
    assert [candidate["snippet"] for candidate in hit["candidates"]] == ["route_to_patch()"]


def test_paraphrase_directional_floor_short_stored_key_still_cannot_hijack(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(tmp_path / "engineering_precedent_store.sqlite3"))
    engineering_precedent_store._write_entry(_snippet_entry("gamma", "hijack_bait()"))
    engineering_precedent_store._write_entry(_snippet_entry("known canon", "snowball_bait()"))

    # Short stored key vs long query: one shared token out of five stays a miss.
    long_query = engineering_precedent_store.lookup("alpha beta gamma delta epsilon")
    # The original snowball case (equal length, one shared generic token)
    # stays blocked by the query-side floor: 1/2 = 0.5 < 0.6.
    snowball = engineering_precedent_store.lookup("unknown canon")

    assert long_query == {"term": "alpha beta gamma delta epsilon", "candidates": []}
    assert snowball == {"term": "unknown canon", "candidates": []}


def test_paraphrase_tokenizer_is_frozen_no_stemming_no_synonyms():
    tokens = engineering_precedent_store._paraphrase_tokens
    assert tokens("Battle-Command  MENU!!") == ("battle", "command", "menu")
    # No stemming IN THE TOKENIZER: morphological variants stay distinct
    # tokens here; families live one layer up (_paraphrase_family_token,
    # additive index rows only).
    assert tokens("menus") != tokens("menu")
    assert tokens("running") == ("running",)
    # NFC + casefold is the only normalization.
    assert tokens("バトル") == tokens("バトル")
    assert tokens("Straße") == tokens("strasse")


def test_paraphrase_family_rules_are_deterministic_and_pinned():
    family = engineering_precedent_store._paraphrase_family_token
    # The rule list is FROZEN: first suffix whose stem passes the guard wins,
    # at most one suffix stripped, ASCII-alpha only. These mappings are the
    # pinned behavior; changing them requires changing the rules, which
    # changes the version stamp below and forces an index rebuild.
    assert family("cleanup") == "clean"
    assert family("lookup") == "look"
    assert family("routing") == "rout"
    assert family("restoration") == "restor"
    assert family("implementation") == "implement"
    assert family("menus") == "menu"
    # Minimum-stem guard against over-stripping (a too-short stem does not
    # consume the token; later suffixes still get their turn).
    assert family("this") == ""
    assert family("group") == ""
    assert family("sing") == ""
    # ASCII-alpha tokens only: no language detection, no alphanumerics.
    assert family("バトル") == ""
    assert family("v2") == ""
    # The version stamp is DERIVED from the rules, so a rule edit cannot
    # forget to bump it. This literal is the pin.
    assert (
        engineering_precedent_store.PARAPHRASE_FAMILY_RULES_VERSION
        == "family-v1:ation+tion+ment+ing+es+ed+up+s:min-stem-4"
    )


def test_paraphrase_family_cleanup_probe_hits(monkeypatch, tmp_path):
    # Live probe 2026-07-05: 'terminal prompt cleanup on exit' missed the
    # banked 'clean terminal prompt restoration' because clean != cleanup as
    # raw tokens. The additive family token (cleanup -> clean) lifts the
    # match to 3/5 query tokens = the floor, and 3/4 stored originals.
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(tmp_path / "engineering_precedent_store.sqlite3"))
    engineering_precedent_store._write_entry(_snippet_entry("clean terminal prompt restoration", "restore_prompt()"))

    hit = engineering_precedent_store.lookup("terminal prompt cleanup on exit")

    assert hit["term"] == "clean terminal prompt restoration"
    assert [candidate["snippet"] for candidate in hit["candidates"]] == ["restore_prompt()"]


def test_paraphrase_correct_absence_researched_term_with_zero_candidates(monkeypatch, tmp_path):
    # Live probe 2026-07-05: 'pomodoro state machine lifecycle' returning
    # zero candidates is CORRECT - the term was researched and nothing was
    # banked. Absence must stay absence: no fuzzy jump into another term's
    # candidates, exactly an empty candidate list on the researched term.
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(tmp_path / "engineering_precedent_store.sqlite3"))
    engineering_precedent_store._write_entry(_empty_entry("pomodoro state machine lifecycle", notes="researched, nothing banked"))
    engineering_precedent_store._write_entry(_snippet_entry("clean terminal prompt restoration", "restore_prompt()"))
    engineering_precedent_store._write_entry(
        _snippet_entry("defer design question to implementation routing criteria", "route_to_patch()")
    )

    exact = engineering_precedent_store.lookup("pomodoro state machine lifecycle")
    paraphrased = engineering_precedent_store.lookup("pomodoro lifecycle state machine")

    assert exact["term"] == "pomodoro state machine lifecycle"
    assert exact["candidates"] == []
    assert paraphrased["term"] == "pomodoro state machine lifecycle"
    assert paraphrased["candidates"] == []


def test_paraphrase_ranking_exact_match_outranks_family_only_match(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(tmp_path / "engineering_precedent_store.sqlite3"))
    # 'prompt clean extras' is byte-smaller, so the old term_key tiebreak
    # would pick it; the exact-match preference must pick the key whose
    # tokens match the query exactly instead of through a family token.
    engineering_precedent_store._write_entry(_snippet_entry("prompt clean extras", "family_level()"))
    engineering_precedent_store._write_entry(_snippet_entry("prompt cleanup extras", "exact_level()"))

    hit = engineering_precedent_store.lookup("prompt cleanup")

    assert hit["term"] == "prompt cleanup extras"
    assert [candidate["snippet"] for candidate in hit["candidates"]] == ["exact_level()"]


def test_paraphrase_family_version_stamp_mismatch_rebuilds_derived_index(monkeypatch, tmp_path):
    db_path = tmp_path / "engineering_precedent_store.sqlite3"
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(db_path))
    engineering_precedent_store._write_entry(_snippet_entry("clean terminal prompt restoration", "restore_prompt()"))
    source_before = _dump_knowledge_rows(db_path)

    connection = sqlite3.connect(db_path)
    try:
        stamp = connection.execute(
            "SELECT value FROM knowledge_index_meta WHERE key = 'paraphrase_family_rules_version'"
        ).fetchone()
        assert stamp == (engineering_precedent_store.PARAPHRASE_FAMILY_RULES_VERSION,)
        # Simulate a store indexed under an older rule list: stale stamp plus
        # stale derived rows that a merely-additive backfill would keep.
        connection.execute(
            "UPDATE knowledge_index_meta SET value = 'family-v0:legacy-rules' "
            "WHERE key = 'paraphrase_family_rules_version'"
        )
        connection.execute(
            "INSERT INTO knowledge_term_tokens(token, kind, source_token, term_key) "
            "VALUES ('stalefamily', 'family', 'restoration', 'clean terminal prompt restoration')"
        )
        connection.commit()
    finally:
        connection.close()

    # Any store open re-checks the stamp and rebuilds the WHOLE derived table.
    hit = engineering_precedent_store.lookup("terminal prompt cleanup on exit")
    assert hit["term"] == "clean terminal prompt restoration"

    connection = sqlite3.connect(db_path)
    try:
        stale = connection.execute(
            "SELECT COUNT(*) FROM knowledge_term_tokens WHERE token = 'stalefamily'"
        ).fetchone()[0]
        stamp = connection.execute(
            "SELECT value FROM knowledge_index_meta WHERE key = 'paraphrase_family_rules_version'"
        ).fetchone()
        family_rows = connection.execute(
            "SELECT COUNT(*) FROM knowledge_term_tokens WHERE kind = 'family'"
        ).fetchone()[0]
    finally:
        connection.close()
    assert stale == 0
    assert stamp == (engineering_precedent_store.PARAPHRASE_FAMILY_RULES_VERSION,)
    assert family_rows > 0
    # Source rows are provenance: the rebuild touched only the derived layer.
    assert _dump_knowledge_rows(db_path) == source_before


def test_paraphrase_legacy_two_column_index_migrates_with_keys_byte_identical(monkeypatch, tmp_path):
    db_path = tmp_path / "engineering_precedent_store.sqlite3"
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(db_path))
    # NFD-decomposed dakuten plus an ideographic space: NFC/NFKC would rewrite
    # both, so any key normalization during the migration shows byte-wise.
    japanese_term = unicodedata.normalize("NFD", "ターン制バトル") + "　コマンド・メニュー"
    assert japanese_term != unicodedata.normalize("NFC", japanese_term)
    engineering_precedent_store._write_entry(_snippet_entry("clean terminal prompt restoration", "restore_prompt()"))
    engineering_precedent_store._write_entry(_empty_entry(japanese_term, notes="japanese canon"))
    source_before = _dump_knowledge_rows(db_path)

    # Simulate the pre-family live store: two-column index, no version stamp.
    connection = sqlite3.connect(db_path)
    try:
        connection.execute("DROP TABLE knowledge_term_tokens")
        connection.execute(
            "CREATE TABLE knowledge_term_tokens (token TEXT NOT NULL, term_key TEXT NOT NULL, PRIMARY KEY (token, term_key))"
        )
        connection.execute("DELETE FROM knowledge_index_meta WHERE key = 'paraphrase_family_rules_version'")
        connection.commit()
    finally:
        connection.close()

    # First open detects the legacy shape and rebuilds; family matching works.
    hit = engineering_precedent_store.lookup("terminal prompt cleanup on exit")
    assert hit["term"] == "clean terminal prompt restoration"
    japanese_hit = engineering_precedent_store.lookup("メニュー コマンド ターン制バトル")
    assert japanese_hit["term"].encode("utf-8") == japanese_term.encode("utf-8")

    connection = sqlite3.connect(db_path)
    try:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(knowledge_term_tokens)").fetchall()
        }
    finally:
        connection.close()
    assert columns == {"token", "kind", "source_token", "term_key"}
    # Keys byte-identical through the migration, decomposed Japanese included.
    source_after = _dump_knowledge_rows(db_path)
    assert source_after == source_before
    assert any(str(row[0]).encode("utf-8") == japanese_term.encode("utf-8") for row in source_after[0])


def test_paraphrase_reads_never_mutate_stored_rows(monkeypatch, tmp_path):
    db_path = tmp_path / "engineering_precedent_store.sqlite3"
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(db_path))
    engineering_precedent_store._write_entry(_snippet_entry("battle command menu", "open_menu()"))
    engineering_precedent_store._write_entry(_empty_entry("bestiary"))
    before = _dump_knowledge_rows(db_path)

    engineering_precedent_store.lookup("turn based battle command menu")
    engineering_precedent_store.lookup("monster log bestiary")
    engineering_precedent_store.lookup("completely unrelated novel term")
    engineering_precedent_store.audit("battle command menu")
    engineering_precedent_store.query({"term": "battle command menu"})

    assert _dump_knowledge_rows(db_path) == before


def test_token_index_migration_is_idempotent_and_additive_on_fixture_store(monkeypatch, tmp_path):
    db_path = tmp_path / "engineering_precedent_store.sqlite3"
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(db_path))
    engineering_precedent_store._write_entry(_snippet_entry("battle command menu", "open_menu()"))
    engineering_precedent_store._write_entry(_empty_entry("bestiary or monster log"))
    before = _dump_knowledge_rows(db_path)

    # Simulate a pre-paraphrase-index fixture store: the derived index is absent.
    connection = sqlite3.connect(db_path)
    try:
        connection.execute("DROP TABLE knowledge_term_tokens")
        connection.commit()
    finally:
        connection.close()

    # First open rebuilds the derived index from existing keys.
    hit = engineering_precedent_store.lookup("turn based battle command menu")
    assert hit["term"] == "battle command menu"

    def token_rows():
        conn = sqlite3.connect(db_path)
        try:
            return conn.execute(
                "SELECT token, term_key FROM knowledge_term_tokens ORDER BY token, term_key"
            ).fetchall()
        finally:
            conn.close()

    first_rows = token_rows()
    assert first_rows

    # Second and third opens are no-ops: same index rows, source rows untouched.
    engineering_precedent_store.lookup("battle command menu")
    engineering_precedent_store.audit("bestiary or monster log")
    assert token_rows() == first_rows
    assert _dump_knowledge_rows(db_path) == before


def test_read_disabled_knowledge_reads_report_miss(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(tmp_path / "engineering_precedent_store.sqlite3"))
    engineering_precedent_store._write_entry(_snippet_entry("battle command menu", "open_menu()"))

    monkeypatch.setenv("AI_ORG_PRECEDENT_READ_DISABLED", "1")
    disabled_lookup = engineering_precedent_store.lookup("battle command menu")
    disabled_fuzzy = engineering_precedent_store.lookup("turn based battle command menu")
    disabled_query = engineering_precedent_store.query({"term": "battle command menu"})
    disabled_audit = engineering_precedent_store.audit("battle command menu")

    monkeypatch.delenv("AI_ORG_PRECEDENT_READ_DISABLED")
    enabled_lookup = engineering_precedent_store.lookup("battle command menu")

    assert disabled_lookup == {"term": "battle command menu", "candidates": []}
    assert disabled_fuzzy == {"term": "turn based battle command menu", "candidates": []}
    assert disabled_query == []
    assert disabled_audit["research"] == []
    assert enabled_lookup["candidates"][0]["snippet"] == "open_menu()"


def test_read_disabled_expand_researches_fresh_and_banks_the_write(monkeypatch, tmp_path):
    now = {"value": 9000.0}
    calls = []
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(tmp_path / "engineering_precedent_store.sqlite3"))
    monkeypatch.setattr(engineering_precedent_store.time, "time", lambda: now["value"])
    engineering_precedent_store._write_entry(_empty_entry("state update", notes="stored result"))

    now["value"] = 9010.0
    monkeypatch.setenv("AI_ORG_PRECEDENT_READ_DISABLED", "1")
    monkeypatch.setattr(engineering_precedent_store, "_codex_baseline", lambda term, context: calls.append("baseline") or "baseline")
    monkeypatch.setattr(engineering_precedent_store, "_codex_search_keywords", lambda term, context: calls.append("keywords") or ["react state update"])
    monkeypatch.setattr(engineering_precedent_store, "fetch_candidates", lambda term, context: calls.append("implementation-fetch") or [])

    entry = engineering_precedent_store.expand("state update", {"language": "Python"})

    # Reads reported miss: the recent-research skip did not fire.
    assert calls == ["baseline", "keywords", "implementation-fetch"]
    assert entry["term"] == "state update"

    # Writes persisted while reads were disabled: the fresh attempt is banked.
    monkeypatch.delenv("AI_ORG_PRECEDENT_READ_DISABLED")
    audited = engineering_precedent_store.audit("state update")
    assert [attempt["attempt"] for attempt in audited["research"]] == [1, 2]
    assert [attempt["last_searched_at"] for attempt in audited["research"]] == [9000.0, 9010.0]


def test_read_disabled_bypasses_memo_cache_reads_but_keeps_writes_and_rate_limiter(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(tmp_path / "engineering_precedent_store.sqlite3"))
    monkeypatch.setenv("AI_ORG_GH_SEARCH_CACHE_TTL_SECONDS", "86400")
    gh_calls = []
    slot_calls = []
    cmd = ["gh", "search", "repos", "turn based combat system", "--limit", "1", "--json", "fullName"]

    def fake_run_gh(cmd_arg):
        gh_calls.append(cmd_arg)
        return subprocess.CompletedProcess(cmd_arg, 0, stdout=json.dumps([{"fullName": "quality/combat"}]), stderr="")

    monkeypatch.setattr(engineering_precedent_store, "_run_gh", fake_run_gh)
    monkeypatch.setattr(engineering_precedent_store, "_acquire_gh_search_slot", lambda: slot_calls.append("slot"))

    monkeypatch.setenv("AI_ORG_PRECEDENT_READ_DISABLED", "1")
    first = engineering_precedent_store._gh_search_json(cmd)
    second = engineering_precedent_store._gh_search_json(cmd)

    # Cache reads bypassed: gh ran twice; rate limiter engaged both times.
    assert first == second == [{"fullName": "quality/combat"}]
    assert len(gh_calls) == 2
    assert len(slot_calls) == 2

    # Cache WRITES stayed on while reads were disabled: re-enabling reads
    # serves the banked response without another gh call.
    monkeypatch.delenv("AI_ORG_PRECEDENT_READ_DISABLED")
    third = engineering_precedent_store._gh_search_json(cmd)
    assert third == first
    assert len(gh_calls) == 2


def test_gh_memo_cache_stays_exact_for_similar_queries(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(tmp_path / "engineering_precedent_store.sqlite3"))
    monkeypatch.setenv("AI_ORG_GH_SEARCH_CACHE_TTL_SECONDS", "86400")
    gh_calls = []

    def fake_run_gh(cmd_arg):
        gh_calls.append(cmd_arg)
        return subprocess.CompletedProcess(cmd_arg, 0, stdout=json.dumps([{"fullName": "quality/combat"}]), stderr="")

    monkeypatch.setattr(engineering_precedent_store, "_run_gh", fake_run_gh)
    monkeypatch.setattr(engineering_precedent_store, "_acquire_gh_search_slot", lambda: None)

    original = ["gh", "search", "repos", "turn based combat system", "--limit", "1", "--json", "fullName"]
    reworded = ["gh", "search", "repos", "combat system turn based", "--limit", "1", "--json", "fullName"]

    engineering_precedent_store._gh_search_json(original)
    engineering_precedent_store._gh_search_json(reworded)

    # The memo cache is a rate-limit shield keyed on the exact command; a
    # reworded query is a different gh search and must not be served the
    # other query's cached response. Paraphrase matching lives one layer up,
    # in the knowledge store, where it prevents the duplicate search from
    # being requested at all.
    assert gh_calls == [original, reworded]
