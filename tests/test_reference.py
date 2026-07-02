from __future__ import annotations

import ast
import base64
import concurrent.futures
import json
import sqlite3
import subprocess
import threading
import time
from pathlib import Path

import pytest

from ai_org import reference


ORIGINAL_FETCH_DESIGN_CANDIDATES = reference.fetch_design_candidates


@pytest.fixture(autouse=True)
def default_no_design_lane(monkeypatch):
    monkeypatch.delenv("AI_ORG_REFERENCE_TTL_SECONDS", raising=False)
    monkeypatch.delenv("AI_ORG_REFERENCE_FORCE", raising=False)
    monkeypatch.setattr(reference, "fetch_design_candidates", lambda term, context: [])


def _design_candidate(source_url="https://example.com/design", delta_claim="Use leases only if crash recovery matters."):
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
        "found_via": "job queue architecture",
        "lang_env_version": "general",
    }


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

    monkeypatch.setattr(reference, "_run_gh", fake_run_gh)

    repos = reference._search_repositories(["broken query", "turn based combat system"], {"language": "JavaScript"})

    assert [cmd[3] for cmd in calls] == ["broken query", "turn based combat system"]
    assert repos[0]["fullName"] == "quality/Turn-Based-Combat"


def test_codex_search_keywords_broadens_overqualified_model_output(monkeypatch):
    prompts = []

    def codex_json(prompt, schema, output_name):
        prompts.append(prompt)
        assert schema == reference.SEARCH_KEYWORDS_SCHEMA
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

    monkeypatch.setattr(reference, "_codex_json", codex_json)

    keywords = reference._codex_search_keywords(
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

    assert all(reference._search_keyword_is_overqualified(keyword) for keyword in bad_keywords)
    assert not any(reference._search_keyword_is_overqualified(keyword) for keyword in good_keywords)
    _assert_general_search_keywords(good_keywords)


def test_normalize_term_folds_only_safe_suffix_variants():
    key = reference._normalize_term

    assert key("Dungeon Exploration System") == key("dungeon exploration")
    assert key("Spells and MP Systems") == "spells and mp"
    assert key("  DUNGEON   EXPLORATION!!!  ") == key("dungeon exploration")
    assert key("save/load system") == "save/load"
    assert key("save/load system mechanics") == "save/load"
    assert key("party recruitment system") != key("party growth system")


def test_github_search_rate_limiter_enforces_shared_rolling_window(monkeypatch):
    now = 0.0
    calls = []
    sleeps = []

    def monotonic():
        return now

    def sleep(seconds):
        nonlocal now
        sleeps.append(seconds)
        now += seconds

    def fake_run_gh(cmd):
        calls.append((now, cmd))
        stdout = json.dumps([{"fullName": f"example/repo-{len(calls)}"}])
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

    monkeypatch.delenv("AI_ORG_GH_SEARCH_PER_MIN", raising=False)
    monkeypatch.setattr(reference, "_GH_SEARCH_TIMESTAMPS", [])
    monkeypatch.setattr(reference.time, "monotonic", monotonic)
    monkeypatch.setattr(reference.time, "sleep", sleep)
    monkeypatch.setattr(reference, "_run_gh", fake_run_gh)

    for index in range(reference.GH_SEARCH_PER_MIN + 1):
        reference._gh_search_json(["gh", "search", "repos", f"query-{index}", "--limit", "1", "--json", "fullName"])

    call_times = [called_at for called_at, _cmd in calls]
    assert len(calls) == reference.GH_SEARCH_PER_MIN + 1
    assert call_times[: reference.GH_SEARCH_PER_MIN] == [0.0] * reference.GH_SEARCH_PER_MIN
    assert call_times[reference.GH_SEARCH_PER_MIN] >= reference.GH_SEARCH_WINDOW_SECONDS
    assert sleeps == [reference.GH_SEARCH_WINDOW_SECONDS]
    for window_start in call_times:
        in_window = [
            called_at
            for called_at in call_times
            if window_start <= called_at < window_start + reference.GH_SEARCH_WINDOW_SECONDS
        ]
        assert len(in_window) <= reference.GH_SEARCH_PER_MIN


def test_search_repositories_backs_off_and_retries_rate_limited_search_once(monkeypatch):
    calls = []
    sleeps = []

    def fake_run_gh(cmd):
        calls.append(cmd)
        if len(calls) == 1:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="HTTP 403: rate limit exceeded")
        stdout = json.dumps([{"fullName": "quality/Turn-Based-Combat", "url": "https://github.com/quality/Turn-Based-Combat"}])
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(reference, "_GH_SEARCH_TIMESTAMPS", [])
    monkeypatch.setattr(reference.time, "sleep", lambda seconds: sleeps.append(seconds))
    monkeypatch.setattr(reference, "_run_gh", fake_run_gh)

    repos = reference._search_repositories(["turn based combat system"], {"language": "JavaScript"})

    assert len(calls) == 2
    assert sleeps == [reference.GH_SEARCH_RETRY_BACKOFF_SECONDS]
    assert repos[0]["fullName"] == "quality/Turn-Based-Combat"


def test_plain_gh_api_reads_are_not_capped_by_search_rate_limiter(monkeypatch):
    calls = []

    def fake_run_gh(cmd):
        calls.append(cmd)
        stdout = json.dumps({"tree": [{"type": "blob", "path": "src/combat/health.py"}]})
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(reference, "_GH_SEARCH_TIMESTAMPS", [0.0] * reference.GH_SEARCH_PER_MIN)
    monkeypatch.setattr(reference.time, "monotonic", lambda: 0.0)
    monkeypatch.setattr(
        reference.time,
        "sleep",
        lambda seconds: (_ for _ in ()).throw(AssertionError("plain gh api read was throttled")),
    )
    monkeypatch.setattr(reference, "_run_gh", fake_run_gh)

    paths = reference._candidate_paths_for_repo("example/repo", "health", {"language": "Python"})

    assert paths == ["src/combat/health.py"]
    assert calls == [["gh", "api", "repos/example/repo/git/trees/HEAD?recursive=1"]]


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
    assert candidates[0]["found_via"] == "turn based combat system"


def test_expand_drops_baseline_equivalent_candidates_with_honest_note(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(tmp_path / "reference.sqlite3"))
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
            "found_via": "turn based combat system",
        }
    ]
    assert entry["notes"] == "baseline-sufficient-nothing-added: baseline already sufficient; nothing valuable to add."


def test_expand_keeps_only_genuine_delta_and_distills_real_fields(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(tmp_path / "reference.sqlite3"))
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
                "found_via": "rpg battle system",
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
    assert reference._database_path().is_relative_to(tmp_path)
    assert not reference._database_path().is_relative_to(reference.REPO_ROOT)


def test_expand_produces_implementation_and_design_candidates_from_separate_lanes(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(tmp_path / "reference.sqlite3"))
    monkeypatch.setattr(reference, "fetch_design_candidates", ORIGINAL_FETCH_DESIGN_CANDIDATES)
    lane_calls = []

    monkeypatch.setattr(reference, "_codex_baseline", lambda term, context: "basic queue worker")
    monkeypatch.setattr(reference, "_codex_search_keywords", lambda term, context: ["job queue worker"])
    monkeypatch.setattr(reference, "_codex_design_search_keywords", lambda term, context: ["job queue architecture"])
    monkeypatch.setattr(
        reference,
        "_search_repositories",
        lambda keywords, context: lane_calls.append(("implementation-search", keywords))
        or [{"fullName": "example/worker", "url": "https://github.com/example/worker", "primaryLanguage": {"name": "Python"}, "found_via": keywords[0]}],
    )
    monkeypatch.setattr(
        reference,
        "_search_design_repositories",
        lambda keywords, context: lane_calls.append(("design-search", keywords))
        or [{"fullName": "example/adr", "url": "https://github.com/example/adr", "primaryLanguage": {"name": "Markdown"}, "found_via": keywords[0]}],
    )
    monkeypatch.setattr(reference, "_candidate_paths_for_repo", lambda repo, term, context: ["worker.py"])
    monkeypatch.setattr(reference, "_design_paths_for_repo", lambda repo, term, context: ["docs/adr/001-queue.md"])
    monkeypatch.setattr(reference, "_read_github_file", lambda repo, path: "source content")
    monkeypatch.setattr(
        reference,
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
        reference,
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
    monkeypatch.setattr(reference, "_codex_design_web_sources", lambda keywords, context: [])
    monkeypatch.setattr(reference, "_codex_delta_inclusion", lambda term, context, baseline, candidate: {"keep": True, "reason": "lease claim is a real delta"})
    monkeypatch.setattr(reference, "_codex_design_delta_inclusion", lambda term, context, candidate: {"keep": True, "reason": candidate["delta_claim"]})
    monkeypatch.setattr(
        reference,
        "_codex_distill_candidate",
        lambda term, context, baseline, candidate, delta_reason: {
            "snippet": candidate["snippet"],
            "summary": candidate["summary"],
            "lang_env_version": candidate["lang_env_version"],
            "pitfalls": candidate["pitfalls"],
        },
    )
    monkeypatch.setattr(reference, "_codex_author_level", lambda term, context, candidate: {"author_level": "high", "reason": "clear leases"})
    monkeypatch.setattr(reference, "_codex_design_competence", lambda term, context, candidate: {"keep": True, "author_level": "expert", "reason": "accepted ADR with migration evidence"})

    entry = reference.expand("job queue worker", {"language": "Python", "version": "3.12"})

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
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(tmp_path / "reference.sqlite3"))
    monkeypatch.setattr(reference, "_codex_baseline", lambda term, context: "basic design")
    monkeypatch.setattr(reference, "_codex_search_keywords", lambda term, context: [])
    monkeypatch.setattr(reference, "fetch_candidates", lambda term, context: [])
    monkeypatch.setattr(
        reference,
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
        reference,
        "_codex_design_delta_inclusion",
        lambda term, context, candidate: {
            "keep": "only if" in candidate["delta_claim"],
            "reason": candidate["delta_claim"] if "only if" in candidate["delta_claim"] else "GoF-basic",
        },
    )
    monkeypatch.setattr(reference, "_codex_design_competence", lambda term, context, candidate: {"keep": True, "author_level": "expert", "reason": "specific production evidence"})

    entry = reference.expand("event subscription design", {})

    assert [candidate["source_url"] for candidate in entry["candidates"]] == ["https://example.com/rollout-adr"]
    assert entry["candidates"][0]["kind"] == "design"
    assert "only if replay latency" in entry["candidates"][0]["delta_claim"]


def test_design_github_search_uses_shared_gh_rate_limited_queue(monkeypatch):
    now = 0.0
    sleeps = []
    calls = []

    def monotonic():
        return now

    def sleep(seconds):
        nonlocal now
        sleeps.append(seconds)
        now += seconds

    def fake_run_gh(cmd):
        calls.append((now, cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="[]", stderr="")

    monkeypatch.setattr(reference, "_GH_SEARCH_TIMESTAMPS", [0.0] * reference.GH_SEARCH_PER_MIN)
    monkeypatch.setattr(reference, "_WEB_SEARCH_TIMESTAMPS", [])
    monkeypatch.setattr(reference.time, "monotonic", monotonic)
    monkeypatch.setattr(reference.time, "sleep", sleep)
    monkeypatch.setattr(reference, "_run_gh", fake_run_gh)

    reference._search_design_repositories(["job queue architecture"], {})

    assert sleeps == [reference.GH_SEARCH_WINDOW_SECONDS]
    assert calls[0][0] >= reference.GH_SEARCH_WINDOW_SECONDS
    assert calls[0][1][:3] == ["gh", "search", "repos"]
    assert reference._WEB_SEARCH_TIMESTAMPS == []


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
        assert schema == reference.DESIGN_SOURCE_SCHEMA
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

    monkeypatch.setattr(reference, "_GH_SEARCH_TIMESTAMPS", [0.0] * reference.GH_SEARCH_PER_MIN)
    monkeypatch.setattr(reference, "_WEB_SEARCH_TIMESTAMPS", [0.0] * reference.WEB_SEARCH_PER_MIN)
    monkeypatch.setattr(reference.time, "monotonic", monotonic)
    monkeypatch.setattr(reference.time, "sleep", sleep)
    monkeypatch.setattr(reference, "_codex_json", codex_json)

    sources = reference._codex_design_web_sources(["job queue architecture"], {})

    assert sleeps == [reference.WEB_SEARCH_WINDOW_SECONDS]
    assert sources[0]["url"] == "https://example.com/queue-adr"
    assert len(reference._GH_SEARCH_TIMESTAMPS) == reference.GH_SEARCH_PER_MIN


def test_low_level_author_delta_is_stored_with_honest_note(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(tmp_path / "reference.sqlite3"))
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
    assert entry["candidates"][0]["found_via"] == "tree depth root"
    assert entry["search_keywords"] == ["tree depth root"]
    assert entry["notes"].startswith("low-level-only:")


def test_expand_records_empty_search_keywords_when_nothing_fetched(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(tmp_path / "reference.sqlite3"))
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
    assert "search_keywords" not in read
    assert reference.audit("PKCE verifier rotation")["search_keywords"] == ["oauth pkce verifier"]


def test_expand_skips_recent_research_and_returns_stored_candidates(monkeypatch, tmp_path):
    now = {"value": 1000.0}
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(tmp_path / "reference.sqlite3"))
    monkeypatch.setattr(reference.time, "time", lambda: now["value"])
    reference._write_entry(
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
    monkeypatch.setattr(reference, "_codex_baseline", lambda term, context: pytest.fail("baseline should be skipped"))
    monkeypatch.setattr(reference, "_codex_search_keywords", lambda term, context: pytest.fail("keywords should be skipped"))
    monkeypatch.setattr(reference, "fetch_candidates", lambda term, context: pytest.fail("implementation fetch should be skipped"))
    monkeypatch.setattr(reference, "fetch_design_candidates", lambda term, context: pytest.fail("design fetch should be skipped"))

    entry = reference.expand("state update", {"language": "React", "environment": "hooks", "version": "18"})

    assert entry["notes"] == "stored result"
    assert entry["candidates"][0]["snippet"] == "setState(prev => prev + 1)"
    assert [attempt["last_searched_at"] for attempt in entry["research"]] == [1000.0]
    assert [attempt["attempt"] for attempt in entry["research"]] == [1]


def test_expand_skips_recent_research_across_term_variants(monkeypatch, tmp_path):
    now = {"value": 1200.0}
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(tmp_path / "reference.sqlite3"))
    monkeypatch.setattr(reference.time, "time", lambda: now["value"])
    reference._write_entry(
        {
            "term": "dungeon exploration",
            "search_keywords": ["dungeon exploration"],
            "examined": [],
            "candidates": [],
            "notes": "nothing-fetched: no public repository candidates were fetched.",
        }
    )

    now["value"] = 1210.0
    monkeypatch.setattr(reference, "_codex_baseline", lambda term, context: pytest.fail("baseline should be skipped"))
    monkeypatch.setattr(reference, "_codex_search_keywords", lambda term, context: pytest.fail("keywords should be skipped"))
    monkeypatch.setattr(reference, "fetch_candidates", lambda term, context: pytest.fail("implementation fetch should be skipped"))
    monkeypatch.setattr(reference, "fetch_design_candidates", lambda term, context: pytest.fail("design fetch should be skipped"))

    entry = reference.expand("Dungeon Exploration System", {"language": "Python"})

    assert entry["term"] == "dungeon exploration"
    assert entry["candidates"] == []
    assert [attempt["last_searched_at"] for attempt in entry["research"]] == [1200.0]


def test_expand_skips_recent_empty_research_without_searching(monkeypatch, tmp_path):
    now = {"value": 2000.0}
    calls = []
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(tmp_path / "reference.sqlite3"))
    monkeypatch.setattr(reference.time, "time", lambda: now["value"])
    monkeypatch.setattr(reference, "_codex_baseline", lambda term, context: calls.append("baseline") or "use a basic verifier")
    monkeypatch.setattr(reference, "_codex_search_keywords", lambda term, context: calls.append("keywords") or ["oauth pkce verifier"])
    monkeypatch.setattr(reference, "fetch_candidates", lambda term, context: calls.append("implementation-fetch") or [])
    monkeypatch.setattr(reference, "fetch_design_candidates", lambda term, context: calls.append("design-fetch") or [])

    first = reference.expand("PKCE verifier rotation", {"language": "TypeScript"})
    now["value"] = 2010.0
    second = reference.expand("PKCE verifier rotation", {"language": "TypeScript"})

    assert first["candidates"] == []
    assert second["candidates"] == []
    assert second["notes"] == "nothing-fetched: no public repository candidates were fetched."
    assert calls == ["baseline", "keywords", "implementation-fetch", "design-fetch"]
    assert [attempt["last_searched_at"] for attempt in second["research"]] == [2000.0]


def test_expand_force_bypasses_recent_research_and_runs_search_lanes(monkeypatch, tmp_path):
    now = {"value": 2500.0}
    calls = []
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(tmp_path / "reference.sqlite3"))
    monkeypatch.setattr(reference.time, "time", lambda: now["value"])
    monkeypatch.setattr(reference, "_codex_baseline", lambda term, context: calls.append("baseline") or "use a basic verifier")
    monkeypatch.setattr(reference, "_codex_search_keywords", lambda term, context: calls.append("keywords") or ["oauth pkce verifier"])
    monkeypatch.setattr(reference, "fetch_candidates", lambda term, context: calls.append("implementation-fetch") or [])
    monkeypatch.setattr(reference, "fetch_design_candidates", lambda term, context: calls.append("design-fetch") or [])

    reference.expand("PKCE verifier rotation", {"language": "TypeScript"})
    now["value"] = 2510.0
    entry = reference.expand("PKCE verifier rotation", {"language": "TypeScript"}, force=True)

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
    assert [attempt["attempt"] for attempt in reference.audit("PKCE verifier rotation")["research"]] == [1, 2]
    assert [attempt["last_searched_at"] for attempt in reference.audit("PKCE verifier rotation")["research"]] == [
        2500.0,
        2510.0,
    ]


def test_expand_env_force_bypasses_recent_research(monkeypatch, tmp_path):
    now = {"value": 2600.0}
    calls = []
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(tmp_path / "reference.sqlite3"))
    monkeypatch.setattr(reference.time, "time", lambda: now["value"])
    monkeypatch.setattr(reference, "_codex_baseline", lambda term, context: calls.append("baseline") or "use a basic verifier")
    monkeypatch.setattr(reference, "_codex_search_keywords", lambda term, context: calls.append("keywords") or ["oauth pkce verifier"])
    monkeypatch.setattr(reference, "fetch_candidates", lambda term, context: calls.append("implementation-fetch") or [])
    monkeypatch.setattr(reference, "fetch_design_candidates", lambda term, context: calls.append("design-fetch") or [])

    reference.expand("PKCE verifier rotation", {"language": "TypeScript"})
    now["value"] = 2610.0
    monkeypatch.setenv("AI_ORG_REFERENCE_FORCE", "1")
    reference.expand("PKCE verifier rotation", {"language": "TypeScript"})
    entry = reference.audit("PKCE verifier rotation")

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
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(tmp_path / "reference.sqlite3"))
    monkeypatch.setattr(reference.time, "time", lambda: now["value"])
    monkeypatch.setattr(reference, "_codex_baseline", lambda term, context: calls.append("baseline") or "use a basic verifier")
    monkeypatch.setattr(reference, "_codex_search_keywords", lambda term, context: calls.append("keywords") or ["oauth pkce verifier"])
    monkeypatch.setattr(reference, "fetch_candidates", lambda term, context: calls.append("implementation-fetch") or [])
    monkeypatch.setattr(reference, "fetch_design_candidates", lambda term, context: calls.append("design-fetch") or [])

    reference.expand("PKCE verifier rotation", {"language": "TypeScript"})
    now["value"] = 3000.0 + reference.REFERENCE_RESEARCH_TTL_SECONDS + 1
    reference.expand("PKCE verifier rotation", {"language": "TypeScript"})
    entry = reference.audit("PKCE verifier rotation")

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
        3000.0 + reference.REFERENCE_RESEARCH_TTL_SECONDS + 1,
    ]


def test_reference_research_ttl_is_env_configurable(monkeypatch, tmp_path):
    now = {"value": 4000.0}
    calls = []
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(tmp_path / "reference.sqlite3"))
    monkeypatch.setenv("AI_ORG_REFERENCE_TTL_SECONDS", "5")
    monkeypatch.setattr(reference.time, "time", lambda: now["value"])
    monkeypatch.setattr(reference, "_codex_baseline", lambda term, context: calls.append("baseline") or "use a basic verifier")
    monkeypatch.setattr(reference, "_codex_search_keywords", lambda term, context: calls.append("keywords") or ["oauth pkce verifier"])
    monkeypatch.setattr(reference, "fetch_candidates", lambda term, context: calls.append("implementation-fetch") or [])
    monkeypatch.setattr(reference, "fetch_design_candidates", lambda term, context: calls.append("design-fetch") or [])

    reference.expand("PKCE verifier rotation", {"language": "TypeScript"})
    now["value"] = 4004.0
    reference.expand("PKCE verifier rotation", {"language": "TypeScript"})
    now["value"] = 4006.0
    reference.expand("PKCE verifier rotation", {"language": "TypeScript"})
    entry = reference.audit("PKCE verifier rotation")

    assert entry is not None
    assert reference._reference_research_ttl_seconds() == 5.0
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
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(tmp_path / "reference.sqlite3"))
    monkeypatch.setenv("AI_ORG_REFERENCE_TTL_SECONDS", "0")
    monkeypatch.setattr(reference, "_codex_baseline", lambda term, context: "basic implementation")
    monkeypatch.setattr(
        reference,
        "_codex_delta_inclusion",
        lambda term, context, baseline, candidate: {"keep": True, "reason": "real implementation detail"},
    )
    monkeypatch.setattr(
        reference,
        "_codex_distill_candidate",
        lambda term, context, baseline, candidate, delta_reason: {
            "snippet": candidate["snippet"],
            "summary": candidate["summary"],
            "lang_env_version": candidate["lang_env_version"],
            "pitfalls": candidate["pitfalls"],
        },
    )
    monkeypatch.setattr(
        reference,
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

    monkeypatch.setattr(reference, "_codex_search_keywords", search_keywords)
    monkeypatch.setattr(reference, "fetch_candidates", fetch_candidates)

    reference.expand("durable reference", {"language": "Python", "version": "3.12"})
    reference.expand("durable reference", {"language": "Python", "version": "3.12"})
    entry = reference.audit("durable reference")

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

    reference.expand("durable reference", {"language": "Python", "version": "3.12"})
    preserved = reference.audit("durable reference")

    assert preserved is not None
    assert [candidate["snippet"] for candidate in preserved["candidates"]] == [
        "first durable snippet",
        "second durable snippet",
    ]
    assert [attempt["attempt"] for attempt in preserved["research"]] == [1, 2, 3]
    assert preserved["research"][2]["notes"] == "nothing-fetched: no public repository candidates were fetched."


def test_candidate_dedup_uses_term_key_source_url_and_snippet(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(tmp_path / "reference.sqlite3"))
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

    reference._write_entry(first)
    reference._write_entry(second)

    entry = reference.audit("Dungeon Exploration Systems")
    assert entry is not None
    assert [attempt["term"] for attempt in entry["research"]] == ["dungeon exploration", "dungeon exploration system"]
    assert [candidate["summary"] for candidate in entry["candidates"]] == ["Original summary."]


def test_expand_records_examined_repos_from_fetch_audit(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(tmp_path / "reference.sqlite3"))
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
            "found_via": "turn based combat system",
        }
    ]
    assert entry["notes"].startswith("baseline-sufficient")


def test_lookup_filters_by_applicability_and_returns_only_consumption_fields(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(tmp_path / "reference.sqlite3"))
    reference._write_entry(
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

    entry = reference.lookup("state update", {"language": "React", "environment": "hooks", "version": "18"})

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
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(tmp_path / "reference.sqlite3"))
    reference._write_entry(
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

    lookup = reference.lookup("Dungeon Exploration System!!!", {"language": "Python", "version": "3.12"})
    audit = reference.audit("dungeon exploration system")
    query_results = reference.query({"term": "Dungeon Exploration System"})

    assert lookup is not None
    assert lookup["term"] == "dungeon exploration"
    assert lookup["candidates"][0]["snippet"] == "enter_room(); resolve_encounter(); reveal_exits()"
    assert audit is not None
    assert audit["term"] == "dungeon exploration"
    assert [candidate["term"] for candidate in query_results] == ["dungeon exploration"]


def test_audit_returns_management_fields(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(tmp_path / "reference.sqlite3"))
    reference._write_entry(
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

    entry = reference.audit("state update")

    assert entry is not None
    assert entry["search_keywords"] == ["react state update"]
    assert entry["examined"][0]["outcome"] == "kept"
    assert entry["candidates"][0]["found_via"] == "react state update"
    assert entry["notes"] == "maintenance note"


def test_legacy_cascade_schema_migrates_to_candidate_independent_history(monkeypatch, tmp_path):
    db_path = tmp_path / "reference.sqlite3"
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

    migrated = reference.audit("state update")

    assert migrated is not None
    assert [attempt["attempt"] for attempt in migrated["research"]] == [1]
    assert migrated["candidates"][0]["snippet"] == "setState(prev => prev + 1)"
    with sqlite3.connect(db_path) as connection:
        assert connection.execute("PRAGMA foreign_key_list(candidates)").fetchall() == []

    reference._write_entry(
        {
            "term": "state update",
            "search_keywords": ["source disappeared pattern"],
            "examined": [],
            "candidates": [],
            "notes": "nothing-fetched: no public repository candidates were fetched.",
        }
    )
    preserved = reference.audit("state update")

    assert preserved is not None
    assert [attempt["attempt"] for attempt in preserved["research"]] == [1, 2]
    assert [candidate["snippet"] for candidate in preserved["candidates"]] == ["setState(prev => prev + 1)"]


def test_existing_store_without_term_key_columns_is_backfilled(monkeypatch, tmp_path):
    db_path = tmp_path / "reference.sqlite3"
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

    lookup = reference.lookup("dungeon exploration", {"language": "Python", "version": "3.12"})
    second_lookup = reference.lookup("dungeon exploration system", {"language": "Python", "version": "3.12"})

    assert lookup is not None
    assert second_lookup is not None
    assert lookup["candidates"][0]["snippet"] == "enter_room(); resolve_encounter()"
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


def test_query_filters_candidates_by_term_lang_author_and_keyword(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(tmp_path / "reference.sqlite3"))
    reference._write_entry(
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
    reference._write_entry(
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

    results = reference.query(
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
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(tmp_path / "reference.sqlite3"))
    reference._write_entry(
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
    reference._write_entry(
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
    reference._write_entry(
        {
            "term": "state update",
            "search_keywords": ["state disappeared"],
            "examined": [],
            "candidates": [],
            "notes": "nothing-fetched: no public repository candidates were fetched.",
        }
    )

    design_lookup = reference.lookup("state update", kind="design")
    implementation_lookup = reference.lookup("state update", {"language": "React", "environment": "hooks", "version": "18"}, kind="implementation")
    design_query = reference.query({"term": "state update", "kind": "design", "keyword": "state update architecture"})
    audit = reference.audit("state update")

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
    db_path = tmp_path / "reference.sqlite3"
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

    first = reference.add_preheld("codex output schema safe subset", "design", facet)
    changed = {**facet, "structure": "Changed text that must not overwrite the original candidate."}
    second = reference.add_preheld("codex output schema safe subset", "design", changed)

    lookup = reference.lookup("codex output schema safe subset", kind="design")
    audit = reference.audit("codex output schema safe subset")
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
    db_path = tmp_path / "reference.sqlite3"
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(db_path))

    first = reference.seed_preheld_org_lessons()
    with sqlite3.connect(db_path) as connection:
        first_research_count = connection.execute("SELECT count(*) FROM research").fetchone()[0]
        first_candidate_count = connection.execute("SELECT count(*) FROM candidates").fetchone()[0]
        first_candidate_ids = [row[0] for row in connection.execute("SELECT id FROM candidates ORDER BY id").fetchall()]
    second = reference.seed_preheld_org_lessons()
    with sqlite3.connect(db_path) as connection:
        second_research_count = connection.execute("SELECT count(*) FROM research").fetchone()[0]
        second_candidate_count = connection.execute("SELECT count(*) FROM candidates").fetchone()[0]
        second_candidate_ids = [row[0] for row in connection.execute("SELECT id FROM candidates ORDER BY id").fetchall()]

    assert first["stored"] == 6
    assert first["skipped"] == 0
    assert second["stored"] == 0
    assert second["skipped"] == 6
    assert first_research_count == 6
    assert first_candidate_count == 6
    assert second_research_count == first_research_count
    assert second_candidate_count == first_candidate_count
    assert second_candidate_ids == first_candidate_ids

    lookup = reference.lookup("required all props payload artifact tolerance", kind="design")
    assert lookup is not None
    assert len(lookup["candidates"]) == 1
    candidate = lookup["candidates"][0]
    assert candidate["author_level"] == "org-experience (primary; learned from live runs)"
    assert "mode-irrelevant payload" in candidate["structure"]
    assert candidate["source_url"] == "ai-org-bootstrap-codex@871c99a; ai-org-bootstrap-codex@2f5f13b"

    lookup = reference.lookup("group tree merges implementations not doc nodes", kind="design")
    assert lookup is not None
    assert len(lookup["candidates"]) == 1
    candidate = lookup["candidates"][0]
    assert "Child RFC branches are doc nodes" in candidate["structure"]
    assert candidate["source_url"] == "ai-org-bootstrap-codex@this-commit"


def test_empty_research_rows_are_stored_and_reexpand_appends_history(monkeypatch, tmp_path):
    db_path = tmp_path / "reference.sqlite3"
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(db_path))
    monkeypatch.setenv("AI_ORG_REFERENCE_TTL_SECONDS", "0")
    monkeypatch.setattr(reference, "_codex_baseline", lambda term, context: "use a basic verifier")
    monkeypatch.setattr(reference, "_codex_search_keywords", lambda term, context: ["oauth pkce verifier"])
    monkeypatch.setattr(reference, "_search_repositories", lambda keywords, context: [])

    reference.expand("PKCE verifier rotation", {"language": "TypeScript"})
    reference.expand("PKCE verifier rotation", {"language": "TypeScript"})

    entry = reference.audit("PKCE verifier rotation")
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
    assert reference.query({"term": "PKCE verifier rotation", "keyword": "oauth pkce verifier"}) == []


def test_build_from_rfc_extracts_terms_once_and_expands_only_returned_terms(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(tmp_path / "reference.sqlite3"))
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

    monkeypatch.setattr(reference, "_codex_json", codex_json)
    monkeypatch.setattr(reference, "expand", expand)

    result = reference.build_from_rfc(
        {
            "title": "Add OAuth PKCE",
            "proposal": "Implement OAuth PKCE verifier flow.",
            "context": "This feature improves login. Avoid expanding generic words like members towns castles.",
        },
        {"language": "TypeScript", "environment": "browser", "version": "ES2022"},
    )

    assert len(codex_calls) == 1
    assert codex_calls[0][1] == reference.REFERENCE_TERMS_SCHEMA
    assert codex_calls[0][2] == "reference-terms.json"
    assert expanded == ["OAuth PKCE verifier flow", "browser token exchange"]
    assert result["expanded"] == ["OAuth PKCE verifier flow", "browser token exchange"]
    assert set(result["terms"]) == {"OAuth PKCE verifier flow", "browser token exchange"}
    assert "feature" not in expanded
    assert "members towns castles" not in expanded


def test_expand_kinds_restricts_research_lanes(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(tmp_path / "reference.sqlite3"))
    calls = []

    monkeypatch.setattr(reference, "_codex_baseline", lambda term, context: calls.append("baseline") or "basic")
    monkeypatch.setattr(reference, "_codex_search_keywords", lambda term, context: calls.append("keywords") or [])
    monkeypatch.setattr(reference, "fetch_candidates", lambda term, context: calls.append("implementation") or [])
    monkeypatch.setattr(reference, "fetch_design_candidates", lambda term, context: calls.append("design") or [])

    reference.expand("lane split design", {}, kinds=("design",))
    assert calls == ["design"]

    calls.clear()
    reference.expand("lane split implementation", {}, kinds=("implementation",))
    assert calls == ["baseline", "keywords", "implementation"]


def test_build_from_rfc_passes_kinds_to_expand(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(tmp_path / "reference.sqlite3"))
    monkeypatch.setenv("AI_ORG_REFERENCE_PARALLEL", "1")
    monkeypatch.setattr(reference, "_extract_reference_terms", lambda text, context: ["design term", "implementation term"])
    monkeypatch.setattr(reference, "lookup", lambda term, context, kind=None: None)
    calls = []

    def expand(term, context, force=False, kinds=None):
        calls.append((term, kinds))
        return {"term": term, "candidates": [], "notes": "expanded"}

    monkeypatch.setattr(reference, "expand", expand)

    result = reference.build_from_rfc({"proposal": "Build split lanes."}, {}, kinds=("design",))

    assert calls == [("design term", ("design",)), ("implementation term", ("design",))]
    assert result["processed_terms"] == ["design term", "implementation term"]


def test_build_from_rfc_propagates_force_to_expand(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(tmp_path / "reference.sqlite3"))
    calls = []
    monkeypatch.setattr(reference, "_extract_reference_terms", lambda text, context: ["oauth verifier"])
    monkeypatch.setattr(reference, "lookup", lambda term, context: None)

    def expand(term, context, force=False, **kwargs):
        calls.append((term, force))
        return {"term": term, "candidates": [], "notes": "expanded"}

    monkeypatch.setattr(reference, "expand", expand)

    result = reference.build_from_rfc({"proposal": "Implement OAuth verifier."}, {"language": "TypeScript"}, force=True)

    assert calls == [("oauth verifier", True)]
    assert result["expanded"] == ["oauth verifier"]


def test_build_from_rfc_uses_bounded_pool_and_isolates_term_failures(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(tmp_path / "reference.sqlite3"))
    monkeypatch.setenv("AI_ORG_REFERENCE_PARALLEL", "3")
    monkeypatch.setattr(reference, "_extract_reference_terms", lambda text, context: ["alpha term", "bad term", "gamma term"])
    monkeypatch.setattr(reference, "lookup", lambda term, context: None)

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

    monkeypatch.setattr(reference.concurrent.futures, "ThreadPoolExecutor", RecordingExecutor)
    monkeypatch.setattr(reference, "expand", expand)

    result = reference.build_from_rfc({"proposal": "alpha beta gamma"}, {"language": "Python"})

    assert max_workers == [3]
    assert max_active > 1
    assert result["expanded"] == ["alpha term", "gamma term"]
    assert result["hits"] == []
    assert set(result["terms"]) == {"alpha term", "gamma term"}
    assert result["failed"] == {"bad term": "RuntimeError: network failed"}

    monkeypatch.setenv("AI_ORG_REFERENCE_PARALLEL", "8")
    assert reference._reference_parallelism(10) == 8


def test_background_build_failure_is_captured_in_future(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(tmp_path / "reference.sqlite3"))

    def fail_build(*args, **kwargs):
        raise RuntimeError("background failed")

    monkeypatch.setattr(reference, "build_from_rfc", fail_build)

    future = reference.start_background_build({"proposal": "Build async."}, {}, kinds=("implementation",))
    reference.await_background_builds(timeout=5)

    assert future.done()
    assert future.exception() is None
    assert future.result()["ok"] is False
    assert "RuntimeError: background failed" in future.result()["error"]


def test_build_from_rfc_parallel_expand_writes_all_sqlite_rows(monkeypatch, tmp_path):
    db_path = tmp_path / "reference.sqlite3"
    terms = ["alpha cache", "beta queue", "gamma lock", "delta retry"]

    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(db_path))
    monkeypatch.setenv("AI_ORG_REFERENCE_PARALLEL", "4")
    monkeypatch.setattr(reference, "_extract_reference_terms", lambda text, context: terms)
    monkeypatch.setattr(reference, "_codex_baseline", lambda term, context: f"basic {term}")
    monkeypatch.setattr(reference, "_codex_search_keywords", lambda term, context: [f"{term} implementation"])
    monkeypatch.setattr(reference, "_codex_delta_inclusion", lambda term, context, baseline, candidate: {"keep": True, "reason": "real delta"})
    monkeypatch.setattr(
        reference,
        "_codex_distill_candidate",
        lambda term, context, baseline, candidate, reason: {
            "snippet": f"def {term.replace(' ', '_')}(): pass",
            "summary": f"Useful implementation detail for {term}.",
            "lang_env_version": "Python 3.12",
            "pitfalls": f"Handle the {term} edge case.",
        },
    )
    monkeypatch.setattr(reference, "_codex_author_level", lambda term, context, candidate: {"author_level": "high", "reason": "clear"})

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
                "_reference_repo": f"example/{term.replace(' ', '-')}",
                "_reference_language": "Python",
                "_reference_found_via": f"{term} implementation",
            }
        ]

    monkeypatch.setattr(reference, "fetch_candidates", fetch_candidates)

    result = reference.build_from_rfc({"proposal": "Build several implementation details."}, {"language": "Python"})

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
    db_path = tmp_path / "reference.sqlite3"
    impl_started = threading.Event()
    release_impl = threading.Event()

    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(db_path))
    monkeypatch.setenv("AI_ORG_REFERENCE_PARALLEL", "1")
    monkeypatch.setattr(reference, "_extract_reference_terms", lambda text, context: ["concurrent term"])
    monkeypatch.setattr(reference, "_codex_baseline", lambda term, context: "basic concurrent term")
    monkeypatch.setattr(reference, "_codex_search_keywords", lambda term, context: ["concurrent term implementation"])
    monkeypatch.setattr(reference, "_codex_delta_inclusion", lambda term, context, baseline, candidate: {"keep": True, "reason": "real delta"})
    monkeypatch.setattr(
        reference,
        "_codex_distill_candidate",
        lambda term, context, baseline, candidate, reason: {
            "snippet": "def concurrent_term(): pass",
            "summary": "Useful implementation detail for concurrent term.",
            "lang_env_version": "Python 3.12",
            "pitfalls": "Keep the write short.",
        },
    )
    monkeypatch.setattr(reference, "_codex_author_level", lambda term, context, candidate: {"author_level": "high", "reason": "clear"})
    monkeypatch.setattr(reference, "_codex_design_delta_inclusion", lambda term, context, candidate: {"keep": True, "reason": "real design"})
    monkeypatch.setattr(reference, "_codex_design_competence", lambda term, context, candidate: {"keep": True, "author_level": "high", "reason": "clear"})

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

    monkeypatch.setattr(reference, "fetch_candidates", fetch_candidates)
    monkeypatch.setattr(reference, "fetch_design_candidates", fetch_design_candidates)

    future = reference.start_background_build({"proposal": "Build concurrent term."}, {}, kinds=("implementation",))
    design_result = reference.build_from_rfc({"proposal": "Build concurrent term."}, {}, kinds=("design",))
    release_impl.set()
    reference.await_background_builds(timeout=5)

    assert future.done()
    assert future.exception() is None
    assert design_result["expanded"] == ["concurrent term"]
    assert future.result()["expanded"] == ["concurrent term"]
    with sqlite3.connect(db_path) as connection:
        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
        kinds = [row[0] for row in connection.execute("SELECT kind FROM candidates ORDER BY kind").fetchall()]

    assert journal_mode == "wal"
    assert kinds == ["design", "implementation"]


def test_reference_store_rejects_work_repo_paths(monkeypatch):
    monkeypatch.setenv("AI_ORG_REFERENCE_STORE", str(reference.REPO_ROOT / ".ai-org-reference"))

    with pytest.raises(ValueError, match="outside the work repo"):
        reference.lookup("anything", {})


def test_reference_schemas_are_codex_valid():
    for schema in [
        reference.BASELINE_SCHEMA,
        reference.SEARCH_KEYWORDS_SCHEMA,
        reference.DESIGN_SEARCH_KEYWORDS_SCHEMA,
        reference.EXTRACT_SCHEMA,
        reference.DESIGN_SOURCE_SCHEMA,
        reference.DESIGN_EXTRACT_SCHEMA,
        reference.DELTA_SCHEMA,
        reference.DESIGN_COMPETENCE_SCHEMA,
        reference.DISTILL_SCHEMA,
        reference.AUTHOR_LEVEL_SCHEMA,
        reference.REFERENCE_TERMS_SCHEMA,
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


def _assert_general_search_keywords(keywords):
    forbidden = reference.SEARCH_KEYWORD_QUALIFIER_TOKENS
    for keyword in keywords:
        tokens = reference._search_keyword_tokens(keyword)
        assert reference.SEARCH_KEYWORD_MIN_TOKENS <= len(tokens) <= reference.SEARCH_KEYWORD_MAX_TOKENS
        assert not (set(tokens) & forbidden)
