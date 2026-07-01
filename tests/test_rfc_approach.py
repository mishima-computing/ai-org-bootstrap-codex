from __future__ import annotations

import json
from pathlib import Path

from ai_org.rfc import receive as receive_module


def test_form_technical_approach_builds_derivation_tree(monkeypatch, tmp_path):
    calls = []
    approaches = {}

    monkeypatch.setattr(
        receive_module,
        "_normalize_problem",
        lambda rfc_view, context=None: calls.append("normalize_problem") or _normalized_problem(),
    )

    def fake_constraints(rfc_view, repo, context=None, approach=None):
        calls.append(("extract_constraints", approach))
        approaches["extract_constraints"] = approach
        assert "problem" in approach
        assert "normalized_problem" not in approach
        return _constraints()

    def fake_prior_art(rfc_view, repo, context=None, approach=None):
        calls.append(("build_prior_art_map", approach))
        approaches["build_prior_art_map"] = approach
        assert approach["problem"]["constraints"]["hard"][0]["id"] == "constraint:hard:1"
        return _prior_art_map()

    def fake_generate_candidates(normalized, constraints, prior_art, context=None, accumulated_approach=None):
        calls.append("generate_candidates")
        approaches["generate_candidates"] = accumulated_approach
        return _candidates()

    def fake_evaluate_candidates(candidates, normalized, constraints, context=None, accumulated_approach=None):
        calls.append("evaluate_candidates")
        approaches["evaluate_candidates"] = accumulated_approach
        return _evaluations()

    def fake_select_approach(candidates, evaluations, constraints, context=None, accumulated_approach=None):
        calls.append("select_approach")
        approaches["select_approach"] = accumulated_approach
        return _decision()

    def fake_implementation_strategy(
        chosen,
        prior_art,
        constraints,
        rfc_view,
        repo,
        context=None,
        accumulated_approach=None,
    ):
        calls.append("implementation_strategy")
        approaches["implementation_strategy"] = accumulated_approach
        return _implementation()

    def fake_right_size_patch_plan(
        chosen,
        implementation,
        constraints,
        context=None,
        accumulated_approach=None,
    ):
        calls.append("right_size_patch_plan")
        approaches["right_size_patch_plan"] = accumulated_approach
        return _patch_plan()

    def fake_surface_risks(
        chosen,
        implementation,
        patch_plan,
        constraints,
        context=None,
        accumulated_approach=None,
    ):
        calls.append("surface_risks")
        approaches["surface_risks"] = accumulated_approach
        return _risks()

    monkeypatch.setattr(receive_module, "_extract_constraints", fake_constraints)
    monkeypatch.setattr(receive_module, "_build_prior_art_map", fake_prior_art)
    monkeypatch.setattr(receive_module, "_generate_candidates", fake_generate_candidates)
    monkeypatch.setattr(receive_module, "_evaluate_candidates", fake_evaluate_candidates)
    monkeypatch.setattr(receive_module, "_select_approach", fake_select_approach)
    monkeypatch.setattr(receive_module, "_implementation_strategy", fake_implementation_strategy)
    monkeypatch.setattr(receive_module, "_right_size_patch_plan", fake_right_size_patch_plan)
    monkeypatch.setattr(receive_module, "_surface_risks", fake_surface_risks)

    result = receive_module.form_technical_approach(_rfc_view(), tmp_path)

    assert result["ok"] is True
    assert "approach" not in result
    assert calls[0] == "normalize_problem"
    assert calls[-1] == "surface_risks"
    for step in (
        "extract_constraints",
        "build_prior_art_map",
        "generate_candidates",
        "evaluate_candidates",
        "select_approach",
        "implementation_strategy",
        "right_size_patch_plan",
        "surface_risks",
    ):
        assert approaches[step]["problem"]["goals"][0]["verification"]["check"] == (
            "Assert Spark defeats Slime and sets meadow_gate_open true."
        )
    assert approaches["generate_candidates"]["problem"]["prior_art"][0]["id"] == (
        "prior_art:reference-first-prior-art-synthesis"
    )
    assert approaches["evaluate_candidates"]["problem"]["question"]["candidates"][0]["id"] == "minimal"
    assert approaches["select_approach"]["problem"]["question"]["candidates"][1]["evaluation"]["id"] == (
        "evaluation:repo_native"
    )
    assert approaches["implementation_strategy"]["problem"]["question"]["decision"]["id"] == "decision:repo_native"
    assert approaches["right_size_patch_plan"]["problem"]["question"]["decision"]["implementation"]["systems"][0][
        "system_name"
    ] == "Battle loop"
    assert approaches["surface_risks"]["problem"]["question"]["decision"]["implementation"]["patch_plan"][
        "first_playable"
    ]["how_verified"] == "Run the battle-loop test and assert meadow_gate_open."

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


def test_step_prompts_render_accumulated_goals_and_direct_ancestors(tmp_path):
    accumulated = _accumulated_approach_through_patch_plan()

    prompts = {
        "extract_constraints": receive_module._extract_constraints_prompt(
            _rfc_view(),
            tmp_path,
            {"repo": tmp_path},
            accumulated,
        ),
        "build_prior_art_map": receive_module._prior_art_map_prompt(
            _rfc_view(),
            tmp_path,
            ["battle loop"],
            [{"term": "battle loop", "design": [], "implementation": [], "status": "not_found"}],
            {"repo": tmp_path},
            accumulated,
        ),
        "generate_candidates": receive_module._generate_candidate_prompt(
            _normalized_problem(),
            _constraints(),
            _prior_art_map(),
            "repo_native",
            [],
            {"repo": tmp_path},
            accumulated,
        ),
        "evaluate_candidates": receive_module._evaluate_candidate_prompt(
            _candidate("repo_native", "repo_native", "Repo Native"),
            _candidates(),
            _normalized_problem(),
            _constraints(),
            {"repo": tmp_path},
            accumulated,
        ),
        "select_approach": receive_module._select_approach_prompt(
            _candidates(),
            _evaluations(),
            _constraints(),
            {"repo": tmp_path},
            accumulated,
        ),
        "implementation_strategy": receive_module._implementation_strategy_prompt(
            _decision(),
            _prior_art_map(),
            _constraints(),
            _rfc_view(),
            tmp_path,
            {"repo": tmp_path},
            accumulated,
        ),
        "right_size_patch_plan": receive_module._right_size_patch_plan_prompt(
            _decision(),
            _implementation(),
            _constraints(),
            {"repo": tmp_path},
            accumulated,
        ),
        "surface_risks": receive_module._surface_risks_prompt(
            _decision(),
            _implementation(),
            _patch_plan(),
            _constraints(),
            {"repo": tmp_path},
            accumulated,
        ),
    }

    for prompt in prompts.values():
        assert "Root success_criteria from step 1" in prompt
        assert "Assert Spark defeats Slime and sets meadow_gate_open true." in prompt
        assert "Accumulated approach so far" in prompt

    assert "Prior-art map" in prompts["generate_candidates"]
    assert "Reference-first prior-art synthesis" in prompts["generate_candidates"]
    assert "Candidate to evaluate" in prompts["evaluate_candidates"]
    assert "All candidate approaches" in prompts["evaluate_candidates"]
    assert "Evaluation matrix" in prompts["select_approach"]
    assert "evaluation:repo_native" in prompts["implementation_strategy"]
    assert "decision:repo_native" in prompts["implementation_strategy"]
    assert "Battle loop" in prompts["right_size_patch_plan"]
    assert "Patch plan" in prompts["surface_risks"]
    assert "Run the battle-loop test and assert meadow_gate_open." in prompts["surface_risks"]


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


def test_prior_art_expands_reference_on_miss_and_uses_researched_facets(monkeypatch, tmp_path):
    expand_calls = []
    lookup_contexts = []
    prompts = []
    stack_context = {"language": "Python", "environment": "CLI", "version": "3.12"}
    context = {**stack_context, "repo": tmp_path, "repo_root": tmp_path}

    monkeypatch.setattr(receive_module, "_prior_art_key_concepts", lambda *args, **kwargs: ["battle loop"])

    def fake_lookup(term, reference_context, kind=None):
        lookup_contexts.append(dict(reference_context))
        assert reference_context == stack_context
        assert "repo" not in reference_context
        assert "repo_root" not in reference_context
        return {"term": term, "candidates": []}

    def fake_expand(term, reference_context):
        expand_calls.append((term, dict(reference_context)))
        return {
            "term": term,
            "candidates": [
                _reference_candidate("design", "researched design summary"),
                _reference_candidate("implementation", "researched implementation summary"),
            ],
        }

    def fake_run_json(repo: Path, **kwargs):
        prompt = kwargs["prompt"]
        prompts.append(prompt)
        assert "researched design summary" in prompt
        assert "researched implementation summary" in prompt
        assert '"status": "researched"' in prompt
        facet_kind = "none" if len(prompts) == 1 else "design"
        return {"ok": True, "raw": json.dumps(_prior_art_map_with_facet("battle loop", facet_kind))}

    monkeypatch.setattr(receive_module.reference, "lookup", fake_lookup)
    monkeypatch.setattr(receive_module.reference, "expand", fake_expand)
    monkeypatch.setattr(receive_module.codex_exec, "run_json", fake_run_json)

    result = receive_module._build_prior_art_map(_rfc_view(), tmp_path, context, {"normalized_problem": _normalized_problem()})

    assert result["patterns"][0]["source"]["facet_kind"] == "design"
    assert expand_calls == [("battle loop", stack_context)]
    assert lookup_contexts
    assert len(prompts) == 2


def test_prior_art_reference_hit_does_not_expand(monkeypatch):
    expand_calls = []
    stack_context = {"language": "Python", "environment": "CLI", "version": "3.12"}

    def fake_lookup(term, reference_context, kind=None):
        if kind == "design":
            return {"term": term, "candidates": [_reference_candidate("design", "stored design summary")]}
        return {"term": term, "candidates": []}

    monkeypatch.setattr(receive_module.reference, "lookup", fake_lookup)
    monkeypatch.setattr(receive_module.reference, "expand", lambda *args, **kwargs: expand_calls.append(args))

    facets = receive_module._read_prior_art_reference_facets(["battle loop"], {**stack_context, "repo": Path(".")})

    assert expand_calls == []
    assert facets[0]["status"] == "retrieved"
    assert facets[0]["design"][0]["summary"] == "stored design summary"


def test_prior_art_allows_none_only_when_expand_produces_no_facets(monkeypatch, tmp_path):
    expand_calls = []

    monkeypatch.setattr(receive_module, "_prior_art_key_concepts", lambda *args, **kwargs: ["unknown pattern"])
    monkeypatch.setattr(
        receive_module.reference,
        "lookup",
        lambda term, reference_context, kind=None: {"term": term, "candidates": []},
    )

    def fake_expand(term, reference_context):
        expand_calls.append(term)
        return {"term": term, "candidates": []}

    monkeypatch.setattr(receive_module.reference, "expand", fake_expand)
    monkeypatch.setattr(
        receive_module.codex_exec,
        "run_json",
        lambda repo, **kwargs: {"ok": True, "raw": json.dumps(_prior_art_map_with_facet("unknown pattern", "none"))},
    )

    result = receive_module._build_prior_art_map(_rfc_view(), tmp_path, {}, {"normalized_problem": _normalized_problem()})

    assert expand_calls == ["unknown pattern"]
    assert result["patterns"][0]["source"]["facet_kind"] == "none"


def test_prior_art_reference_expansion_is_capped(monkeypatch):
    concepts = [f"concept {index}" for index in range(receive_module.MAX_PRIOR_ART_REFERENCE_EXPANSIONS + 2)]
    expand_calls = []

    monkeypatch.setattr(
        receive_module.reference,
        "lookup",
        lambda term, reference_context, kind=None: {"term": term, "candidates": []},
    )

    def fake_expand(term, reference_context):
        expand_calls.append(term)
        return {"term": term, "candidates": []}

    monkeypatch.setattr(receive_module.reference, "expand", fake_expand)

    facets = receive_module._read_prior_art_reference_facets(concepts, {})

    assert expand_calls == concepts[: receive_module.MAX_PRIOR_ART_REFERENCE_EXPANSIONS]
    assert [facet["status"] for facet in facets[-2:]] == ["not_researched_cap", "not_researched_cap"]


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


def _prior_art_map_with_facet(reference_concept: str, facet_kind: str) -> dict[str, object]:
    return {
        "patterns": [
            {
                "name": f"Prior-art pattern {index}",
                "source": {
                    "reference_concept": reference_concept,
                    "facet_kind": facet_kind,
                    "where": "Reference." if facet_kind != "none" else "RFC and repository.",
                },
                "when_applies": "When the RFC needs a grounded implementation direction.",
                "tradeoffs": {"pros": ["Grounded direction."], "cons": ["Requires validation."]},
                "disposition": {"choice": "adopt", "why": "It fits the RFC constraints."},
                "traces_to": ["normalized_problem.success_criteria[0]"],
            }
            for index in range(1, 4)
        ]
    }


def _reference_candidate(kind: str, summary: str) -> dict[str, str]:
    return {
        "kind": kind,
        "term": "battle loop",
        "summary": summary,
        "snippet": f"{kind} snippet",
        "pitfalls": f"{kind} pitfalls",
        "structure": f"{kind} structure",
        "rationale": f"{kind} rationale",
        "when_to_use": f"{kind} use",
        "tradeoffs": f"{kind} tradeoffs",
        "implementation_hooks": f"{kind} hooks",
        "quality_attributes": f"{kind} quality",
        "evidence": f"{kind} evidence",
        "delta_claim": f"{kind} delta",
        "lang_env_version": "Python 3.12",
        "author_level": "maintainer",
        "source_url": "https://example.test/reference",
        "found_via": "test",
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


def _accumulated_approach_through_patch_plan() -> dict[str, object]:
    problem = receive_module._problem_root_from_normalized(_normalized_problem())
    problem["constraints"] = receive_module._constraint_tree_nodes(_constraints())
    problem["prior_art"] = receive_module._prior_art_tree_nodes(_prior_art_map())
    problem["question"] = receive_module._partial_question_tree(
        candidates=_candidates(),
        evaluations=_evaluations(),
        selected=_decision(),
        implementation=_implementation(),
        patch_plan=_patch_plan(),
    )
    return {"problem": problem}
