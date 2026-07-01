from __future__ import annotations

import json
from pathlib import Path

from ai_org.rfc import receive as receive_module


def test_form_technical_approach_builds_derivation_tree(monkeypatch, tmp_path):
    calls = []

    monkeypatch.setattr(
        receive_module,
        "_normalize_problem",
        lambda rfc_view, context=None: calls.append("normalize_problem") or _normalized_problem(),
    )

    def fake_constraints(rfc_view, repo, context=None, approach=None):
        calls.append(("extract_constraints", approach))
        assert "problem" in approach
        assert "normalized_problem" not in approach
        return _constraints()

    def fake_prior_art(rfc_view, repo, context=None, approach=None):
        calls.append(("build_prior_art_map", approach))
        assert approach["problem"]["constraints"]["hard"][0]["id"] == "constraint:hard:1"
        return _prior_art_map()

    monkeypatch.setattr(receive_module, "_extract_constraints", fake_constraints)
    monkeypatch.setattr(receive_module, "_build_prior_art_map", fake_prior_art)
    monkeypatch.setattr(
        receive_module,
        "_generate_candidates",
        lambda normalized, constraints, prior_art, context=None: calls.append("generate_candidates") or _candidates(),
    )
    monkeypatch.setattr(
        receive_module,
        "_evaluate_candidates",
        lambda candidates, normalized, constraints, context=None: calls.append("evaluate_candidates") or _evaluations(),
    )
    monkeypatch.setattr(
        receive_module,
        "_select_approach",
        lambda candidates, evaluations, constraints, context=None: calls.append("select_approach") or _decision(),
    )
    monkeypatch.setattr(
        receive_module,
        "_implementation_strategy",
        lambda chosen, prior_art, constraints, rfc_view, repo, context=None: calls.append("implementation_strategy")
        or _implementation(),
    )
    monkeypatch.setattr(
        receive_module,
        "_right_size_patch_plan",
        lambda chosen, implementation, constraints, context=None: calls.append("right_size_patch_plan") or _patch_plan(),
    )
    monkeypatch.setattr(
        receive_module,
        "_surface_risks",
        lambda chosen, implementation, patch_plan, constraints, context=None: calls.append("surface_risks") or _risks(),
    )

    result = receive_module.form_technical_approach(_rfc_view(), tmp_path)

    assert result["ok"] is True
    assert "approach" not in result
    assert calls[0] == "normalize_problem"
    assert calls[-1] == "surface_risks"

    tree = result["technical_approach"]
    assert set(tree) == {"problem", "cross_links"}
    problem = tree["problem"]
    assert problem["id"] == "problem"
    assert problem["goals"][0]["id"] == "goal:1"
    assert problem["constraints"]["hard"][0]["id"] == "constraint:hard:1"
    assert problem["constraints"]["soft"][0]["id"] == "constraint:soft:1"
    assert problem["prior_art"][0]["id"] == "prior_art:reference-first-prior-art-synthesis"

    question = problem["question"]
    assert question["id"] == "question:approach"
    assert [candidate["id"] for candidate in question["candidates"]] == ["minimal", "repo_native"]
    repo_candidate = question["candidates"][1]
    assert repo_candidate["evaluation"]["id"] == "evaluation:repo_native"
    assert repo_candidate["evaluation"]["scores"]["problem_fit"]["rating"] == "high"
    assert repo_candidate["evaluation"]["arguments"][0]["role"] == "support"
    assert repo_candidate["risks"][0]["id"] == "risk:candidate"

    decision = question["decision"]
    assert decision["id"] == "decision:repo_native"
    assert decision["selected_candidate_id"] == "repo_native"
    assert decision["rejected"] == [{"candidate_id": "minimal", "objection": "Lower problem fit."}]
    assert decision["implementation"]["id"] == "implementation:repo_native"
    assert decision["implementation"]["patch_plan"]["id"] == "patch_plan:repo_native"
    assert decision["implementation"]["risks"][0]["id"] == "risk:implementation"

    link_types = {link["type"] for link in tree["cross_links"]}
    assert link_types <= set(receive_module.CROSS_LINK_TYPES)
    assert {"from": "repo_native", "to": "question:approach", "type": "depends_on"} in tree["cross_links"]
    assert {"from": "implementation:repo_native", "to": "decision:repo_native", "type": "implements"} in tree[
        "cross_links"
    ]
    assert {"from": "risk:implementation", "to": "implementation:repo_native", "type": "mitigates"} in tree[
        "cross_links"
    ]


def test_empty_slot_regenerates_then_accepts(monkeypatch, tmp_path):
    attempts = [
        {
            "systems": [
                {
                    "system_name": "Battle loop",
                    "behavior_in_game": "",
                    "named_content": {"entities": ["Slime"], "content_items": ["Spark spell"]},
                    "key_modules": ["game.battle"],
                }
            ],
            "persistence": {"saved_fields": ["battle_state"]},
        },
        _implementation(),
    ]
    prompts = []

    def fake_run_json(repo: Path, **kwargs):
        prompts.append(kwargs["prompt"])
        return {"ok": True, "raw": json.dumps(attempts.pop(0))}

    monkeypatch.setattr(receive_module.codex_exec, "run_json", fake_run_json)

    result = receive_module._implementation_strategy(
        _decision(),
        _prior_art_map(),
        _constraints(),
        _rfc_view(),
        tmp_path,
        {"repo": tmp_path},
    )

    assert result == _implementation()
    assert len(prompts) == 2
    assert "behavior_in_game is empty" in prompts[1]


def test_empty_slot_fails_closed_after_bounded_regeneration(monkeypatch, tmp_path):
    invalid = {
        "systems": [
            {
                "system_name": "Battle loop",
                "behavior_in_game": "",
                "named_content": {"entities": ["Slime"], "content_items": ["Spark spell"]},
                "key_modules": ["game.battle"],
            }
        ],
        "persistence": {"saved_fields": ["battle_state"]},
    }

    monkeypatch.setattr(
        receive_module.codex_exec,
        "run_json",
        lambda repo, **kwargs: {"ok": True, "raw": json.dumps(invalid)},
    )

    result = receive_module._implementation_strategy(
        _decision(),
        _prior_art_map(),
        _constraints(),
        _rfc_view(),
        tmp_path,
        {"repo": tmp_path},
    )

    assert result["ok"] is False
    assert "remained invalid" in result["error"]
    assert "behavior_in_game is empty" in result["error"]


def test_risk_targets_fail_closed_when_parent_is_unknown(monkeypatch, tmp_path):
    monkeypatch.setattr(receive_module, "_normalize_problem", lambda rfc_view, context=None: _normalized_problem())
    monkeypatch.setattr(receive_module, "_extract_constraints", lambda *args, **kwargs: _constraints())
    monkeypatch.setattr(receive_module, "_build_prior_art_map", lambda *args, **kwargs: _prior_art_map())
    monkeypatch.setattr(receive_module, "_generate_candidates", lambda *args, **kwargs: _candidates())
    monkeypatch.setattr(receive_module, "_evaluate_candidates", lambda *args, **kwargs: _evaluations())
    monkeypatch.setattr(receive_module, "_select_approach", lambda *args, **kwargs: _decision())
    monkeypatch.setattr(receive_module, "_implementation_strategy", lambda *args, **kwargs: _implementation())
    monkeypatch.setattr(receive_module, "_right_size_patch_plan", lambda *args, **kwargs: _patch_plan())
    monkeypatch.setattr(
        receive_module,
        "_surface_risks",
        lambda *args, **kwargs: {
            "risks": [
                {
                    "id": "risk:lost",
                    "risk": "Detached risk.",
                    "mitigation": "Attach to a known parent.",
                    "attaches_to": "implementation",
                    "target_id": "implementation:unknown",
                }
            ]
        },
    )

    result = receive_module.form_technical_approach(_rfc_view(), tmp_path)

    assert result["ok"] is False
    assert result["failed_step"] == "surface_risks"
    assert "targets unknown implementation node" in result["error"]


def test_technical_approach_schemas_are_codex_valid():
    for schema in (
        receive_module.NORMALIZE_PROBLEM_SCHEMA,
        receive_module.EXTRACT_CONSTRAINTS_SCHEMA,
        receive_module.PRIOR_ART_MAP_SCHEMA,
        receive_module.CANDIDATE_APPROACH_SCHEMA,
        receive_module.GENERATE_CANDIDATES_SCHEMA,
        receive_module.EVALUATE_CANDIDATE_SCHEMA,
        receive_module.EVALUATE_CANDIDATES_SCHEMA,
        receive_module.SELECT_APPROACH_SCHEMA,
        receive_module.IMPLEMENTATION_STRATEGY_SCHEMA,
        receive_module.RIGHT_SIZE_PATCH_PLAN_SCHEMA,
        receive_module.SURFACE_RISKS_SCHEMA,
    ):
        _assert_codex_valid_object_schema(schema)


def test_receive_imports_reference_and_codex_exec_without_later_phases():
    source = Path(receive_module.__file__).read_text(encoding="utf-8")

    assert "import ai_org.reference as reference" in source
    assert "import ai_org.rfc.codex_exec as codex_exec" in source
    assert "ai_org.patch" not in source
    assert "ai_org.merge" not in source
    assert not any("\u3040" <= character <= "\u9fff" for character in source)


def _assert_codex_valid_object_schema(schema: dict[str, object]) -> None:
    assert "allOf" not in schema
    assert "anyOf" not in schema
    assert "oneOf" not in schema
    if schema.get("type") == "object":
        assert schema["additionalProperties"] is False
        assert sorted(schema["required"]) == sorted(schema["properties"])
        for subschema in schema["properties"].values():
            _assert_codex_valid_object_schema(subschema)
    if schema.get("type") == "array":
        _assert_codex_valid_object_schema(schema["items"])


def _rfc_view() -> dict[str, object]:
    return {
        "title": "Playable Battle Slice",
        "problem": "Players need a first battle loop.",
        "proposal": "Add a small battle loop with a named enemy and spell.",
        "alternatives": ["Defer combat."],
        "intended_users": "Players and game contributors.",
        "affected_area": "gameplay",
        "impact": "The first playable slice can be verified.",
        "context": "Technical Approach formation.",
    }


def _normalized_problem() -> dict[str, object]:
    return {
        "problem": "The game lacks a verifiable first battle loop.",
        "affected": "Players and game contributors.",
        "current_inadequacy": "There is no named enemy, action, or progress condition for combat.",
        "success_criteria": [
            {
                "actor": "player",
                "capability": {
                    "action": "Cast Spark at a Slime in the meadow encounter.",
                    "preconditions": ["The player is in Green Meadow with Spark learned."],
                },
                "verifiable_outcome": {
                    "expected_state": "The Slime is defeated and the meadow gate opens.",
                    "evidence": "Battle log records Spark damage and gate state changes to open.",
                },
                "verification": {
                    "method": "automated_test",
                    "check": "Assert Spark defeats Slime and sets meadow_gate_open true.",
                },
            }
        ],
        "non_goals": ["Do not add a full campaign."],
    }


def _constraints() -> dict[str, object]:
    return {
        "hard_constraints": [
            {
                "statement": "Keep RFC receive isolated from patch and merge modules.",
                "derivation": {"from": "repo", "trace": "ai_org.rfc.receive imports only RFC helpers."},
                "implication": {
                    "must": "Reuse ai_org.rfc.codex_exec for Codex-backed approach nodes.",
                    "must_not": "Import patch or merge phase modules.",
                },
            }
        ],
        "soft_preferences": [
            {
                "statement": "Prefer repo-native gameplay modules.",
                "derivation": {"from": "repo", "trace": "Existing game modules own battle behavior."},
                "rationale": "Local ownership keeps the first slice easy to test.",
            }
        ],
    }


def _prior_art_map() -> dict[str, object]:
    return {
        "patterns": [
            {
                "name": "Reference-first prior-art synthesis",
                "source": {"reference_concept": "battle loop", "facet_kind": "design", "where": "Reference."},
                "when_applies": "When a first playable loop needs named content.",
                "tradeoffs": {"pros": ["Grounded design."], "cons": ["Requires explicit content slots."]},
                "disposition": {"choice": "adopt", "why": "It gives the candidate concrete content."},
                "traces_to": ["normalized_problem.success_criteria[0]"],
            }
        ]
    }


def _candidate(candidate_id: str, kind: str, name: str) -> dict[str, object]:
    return {
        "id": candidate_id,
        "name": name,
        "kind": kind,
        "summary": f"{name} builds the battle slice.",
        "first_playable_moment": {
            "player_actions": ["Cast Spark at Slime."],
            "named_content": {
                "locations": ["Green Meadow"],
                "enemies": ["Slime"],
                "items_or_spells": ["Spark"],
            },
            "win_or_progress_condition": "Slime defeated and meadow gate opens.",
        },
        "core_systems": ["battle resolution"],
        "draws_on": ["Reference-first prior-art synthesis"],
    }


def _candidates() -> dict[str, object]:
    return {
        "candidates": [
            _candidate("minimal", "minimal_local", "Minimal"),
            _candidate("repo_native", "repo_native", "Repo Native"),
        ]
    }


def _evaluations() -> dict[str, object]:
    return {
        "evaluations": [
            {"candidate_id": "minimal", "scores": _scores(problem_fit=("medium", "Narrow battle coverage."))},
            {"candidate_id": "repo_native", "scores": _scores(problem_fit=("high", "Covers the named loop."))},
        ]
    }


def _scores(**overrides: tuple[str, str]) -> dict[str, object]:
    defaults = {
        "problem_fit": ("high", "Matches the success criterion."),
        "repo_fit": ("high", "Fits module boundaries."),
        "complexity": ("medium", "Adds focused behavior."),
        "quality_attributes": ("high", "Preserves deterministic resolution."),
        "compat_migration": ("high", "No migration required."),
        "testability": ("high", "Can be verified with an automated test."),
        "operability": ("medium", "Battle log gives visibility."),
        "reversibility": ("high", "Local behavior can be replaced."),
        "risk": ("medium", "Balance may need tuning."),
    }
    defaults.update(overrides)
    return {field: {"rating": rating, "reason": reason} for field, (rating, reason) in defaults.items()}


def _decision() -> dict[str, object]:
    return {
        "selected_candidate_id": "repo_native",
        "arguments": [
            {
                "role": "support",
                "about_candidate_id": "repo_native",
                "claim": "Repo Native best implements the named battle loop.",
                "grounds": "Its evaluation has high problem and repo fit.",
                "warrant": "The selected approach should satisfy the goal while fitting existing modules.",
                "backing": "The prior-art map favors named content and repo inspection.",
                "rebuttal": "It costs more than the minimal local patch.",
            },
            {
                "role": "objection",
                "about_candidate_id": "minimal",
                "claim": "Minimal leaves less room for repo-native battle ownership.",
                "grounds": "Its problem fit is only medium.",
                "warrant": "A first playable loop should establish the module path.",
                "backing": "The constraint prefers repo-native gameplay modules.",
                "rebuttal": "Minimal would be cheaper.",
            },
        ],
        "rationale": {
            "because": ["It covers the named Slime and Spark loop."],
            "under_constraints": ["It keeps receive isolated and reuses codex_exec."],
            "accepting_tradeoffs": ["It adds more implementation detail than the minimal patch."],
        },
        "rejected": [{"candidate_id": "minimal", "objection": "Lower problem fit."}],
    }


def _implementation() -> dict[str, object]:
    return {
        "systems": [
            {
                "system_name": "Battle loop",
                "behavior_in_game": "Player casts Spark, Slime takes damage, and the meadow gate opens on victory.",
                "named_content": {"entities": ["Player", "Slime"], "content_items": ["Spark", "Meadow gate"]},
                "key_modules": ["game.battle", "game.state"],
            }
        ],
        "persistence": {"saved_fields": ["player_spells", "slime_defeated", "meadow_gate_open"]},
    }


def _patch_plan() -> dict[str, object]:
    return {
        "first_playable": {
            "player_can": ["Enter Green Meadow.", "Cast Spark at Slime."],
            "named_content": {
                "locations": ["Green Meadow"],
                "enemies": ["Slime"],
                "items_or_spells": ["Spark"],
            },
            "win_or_progress_condition": "Slime defeated and meadow gate opens.",
            "how_verified": "Run the battle-loop test and assert meadow_gate_open.",
        },
        "follow_ups": [
            {
                "adds": "Add a second meadow enemy.",
                "named_content": {"locations": ["Green Meadow"], "enemies": ["Bat"], "items_or_spells": ["Spark"]},
            }
        ],
        "deferred": [{"item": "Full campaign map.", "why_safe_to_defer": "The first battle loop is independent."}],
    }


def _risks() -> dict[str, object]:
    return {
        "risks": [
            {
                "id": "risk:candidate",
                "risk": "The repo-native path may touch more files.",
                "mitigation": "Keep the first playable patch limited to battle and state modules.",
                "attaches_to": "candidate",
                "target_id": "repo_native",
            },
            {
                "id": "risk:implementation",
                "risk": "Persisted battle state could drift from runtime state.",
                "mitigation": "Save and reload player_spells, slime_defeated, and meadow_gate_open in tests.",
                "attaches_to": "implementation",
                "target_id": "implementation:repo_native",
            },
        ]
    }
