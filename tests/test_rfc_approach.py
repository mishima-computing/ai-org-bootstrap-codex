from __future__ import annotations

import json
from pathlib import Path

from ai_org.rfc import receive as receive_module


def test_normalize_problem_returns_parsed_structure(monkeypatch, tmp_path):
    rfc_view = _rfc_view()
    expected = {
        "problem": "RFC formation lacks a normalized problem statement before approach design.",
        "affected": "RFC reviewers and downstream implementation agents.",
        "current_inadequacy": "The grounded common-8 view is not restated into crisp success boundaries.",
        "success_criteria": [
            "A normalized problem includes checkable success criteria.",
            "Out-of-scope work is explicit before later approach steps run.",
        ],
        "non_goals": ["Do not generate candidate approaches in this step."],
    }
    calls = []

    def fake_run_json(repo: Path, **kwargs):
        calls.append((repo, kwargs))
        return {"ok": True, "raw": json.dumps(expected)}

    monkeypatch.setattr(receive_module.codex_exec, "run_json", fake_run_json)

    result = receive_module._normalize_problem(rfc_view, {"repo": tmp_path})

    assert result == expected
    assert len(calls) == 1
    repo, kwargs = calls[0]
    assert repo == tmp_path.resolve()
    assert kwargs["schema"] == receive_module.NORMALIZE_PROBLEM_SCHEMA
    assert kwargs["schema_filename"] == "rfc-normalize-problem.schema.json"
    assert kwargs["output_filename"] == "rfc-normalized-problem.json"
    assert kwargs["failure_label"] == "Codex problem normalization"
    assert "step 1" in kwargs["prompt"]
    assert "Do not propose an implementation approach" in kwargs["prompt"]
    assert "Return only JSON matching the provided schema." in kwargs["prompt"]


def test_normalize_problem_fails_closed_on_invalid_or_missing_fields(monkeypatch, tmp_path):
    invalid_outputs = [
        {"affected": "Users", "current_inadequacy": "Missing", "success_criteria": [], "non_goals": []},
        {
            "problem": "Problem",
            "affected": "Users",
            "current_inadequacy": "Missing",
            "success_criteria": ["Done"],
            "non_goals": [],
            "extra": "not allowed",
        },
        {
            "problem": "Problem",
            "affected": "Users",
            "current_inadequacy": "Missing",
            "success_criteria": ["Done"],
            "non_goals": [42],
        },
    ]

    for payload in invalid_outputs:
        monkeypatch.setattr(
            receive_module.codex_exec,
            "run_json",
            lambda repo, **kwargs: {"ok": True, "raw": json.dumps(payload)},
        )

        result = receive_module._normalize_problem(_rfc_view(), {"repo": tmp_path})

        assert result["ok"] is False
        assert "Codex problem normalization returned invalid" in result["error"]


def test_normalize_problem_schema_is_codex_valid():
    schema = receive_module.NORMALIZE_PROBLEM_SCHEMA
    serialized = json.dumps(schema)
    assert "allOf" not in serialized
    assert "anyOf" not in serialized
    assert "oneOf" not in serialized
    assert schema["additionalProperties"] is False
    assert sorted(schema["required"]) == sorted(schema["properties"])


def test_extract_constraints_returns_parsed_structure(monkeypatch, tmp_path):
    rfc_view = _rfc_view()
    expected = {
        "hard_constraints": [
            {
                "constraint": "Keep RFC receive isolated from patch and merge modules.",
                "source": "repo",
                "why": "The RFC package owns receive/review/decompose without importing implementation phases.",
            },
            {
                "constraint": "Generated schemas must be accepted by Codex output schema validation.",
                "source": "rfc",
                "why": "The request requires no allOf, anyOf, or oneOf and required must list all properties.",
            },
        ],
        "soft_preferences": [
            {
                "preference": "Match the existing normalize_problem helper pattern.",
                "source": "repo",
                "why": "Step 1 already uses ai_org.rfc.codex_exec.run_json with prompt, schema, and parser helpers.",
            },
            {
                "preference": "Keep delivery scoped to step 2 only.",
                "source": "domain",
                "why": "Incremental approach formation reduces review and regression risk.",
            },
        ],
    }
    calls = []

    def fake_run_json(repo: Path, **kwargs):
        calls.append((repo, kwargs))
        return {"ok": True, "raw": json.dumps(expected)}

    monkeypatch.setattr(receive_module.codex_exec, "run_json", fake_run_json)

    result = receive_module._extract_constraints(rfc_view, tmp_path, {"normalized": "problem"})

    assert result == expected
    assert len(calls) == 1
    repo, kwargs = calls[0]
    assert repo == tmp_path.resolve()
    assert kwargs["schema"] == receive_module.EXTRACT_CONSTRAINTS_SCHEMA
    assert kwargs["schema_filename"] == "rfc-extract-constraints.schema.json"
    assert kwargs["output_filename"] == "rfc-extracted-constraints.json"
    assert kwargs["failure_label"] == "Codex constraint extraction"
    assert "step 2" in kwargs["prompt"]
    assert "extract constraints" in kwargs["prompt"]
    assert "Inspect the repository read-only" in kwargs["prompt"]
    assert "Do not propose candidate approaches" in kwargs["prompt"]
    assert "Return only JSON matching the provided schema." in kwargs["prompt"]


def test_extract_constraints_fails_closed_on_invalid_shape(monkeypatch, tmp_path):
    invalid_outputs = [
        {"hard_constraints": []},
        {"hard_constraints": [], "soft_preferences": [], "extra": "not allowed"},
        {
            "hard_constraints": [{"constraint": "Keep API stable.", "source": "unknown", "why": "Bad source."}],
            "soft_preferences": [],
        },
        {
            "hard_constraints": [{"constraint": "Keep API stable.", "source": "repo"}],
            "soft_preferences": [],
        },
        {
            "hard_constraints": [],
            "soft_preferences": [{"preference": "Small patch.", "source": "rfc", "why": 42}],
        },
    ]

    for payload in invalid_outputs:
        monkeypatch.setattr(
            receive_module.codex_exec,
            "run_json",
            lambda repo, **kwargs: {"ok": True, "raw": json.dumps(payload)},
        )

        result = receive_module._extract_constraints(_rfc_view(), tmp_path)

        assert result["ok"] is False
        assert "Codex constraint extraction returned invalid" in result["error"]


def test_extract_constraints_schema_is_codex_valid():
    schema = receive_module.EXTRACT_CONSTRAINTS_SCHEMA
    serialized = json.dumps(schema)
    assert "allOf" not in serialized
    assert "anyOf" not in serialized
    assert "oneOf" not in serialized
    _assert_codex_valid_object_schema(schema)

    hard_item = schema["properties"]["hard_constraints"]["items"]
    soft_item = schema["properties"]["soft_preferences"]["items"]
    assert hard_item["properties"]["source"]["enum"] == ["repo", "rfc", "domain"]
    assert soft_item["properties"]["source"]["enum"] == ["repo", "rfc", "domain"]


def test_build_prior_art_map_reads_reference_and_returns_patterns(monkeypatch, tmp_path):
    rfc_view = _rfc_view()
    expected = {
        "patterns": [
            {
                "pattern": "Reference-first prior-art synthesis",
                "where_seen": "Reference design facet for prior-art map.",
                "when_applies": "Use when an RFC approach needs evidence before candidate generation.",
                "tradeoffs": "Improves grounding but depends on Reference coverage.",
                "disposition": "adopt",
                "rationale": "The RFC explicitly requires Reference design and implementation facets.",
            },
            {
                "pattern": "Repo-native read-only inspection",
                "where_seen": "Existing RFC steps use read-only Codex calls.",
                "when_applies": "Use when repository context can constrain a design without edits.",
                "tradeoffs": "Keeps phases isolated but cannot verify changes.",
                "disposition": "adapt",
                "rationale": "Step 3 should read context but stop before candidate approaches.",
            },
            {
                "pattern": "Frequency-based framework selection",
                "where_seen": "General engine-selection discussions.",
                "when_applies": "Only when frequency correlates with maintainability evidence.",
                "tradeoffs": "Popularity can hide poor repository fit.",
                "disposition": "reject",
                "rationale": "The RFC says engines are judged on fit, not appearance count.",
            },
        ]
    }
    reference_calls = []
    query_calls = []
    codex_calls = []

    def fake_lookup(term, context=None, kind=None):
        reference_calls.append((term, context, kind))
        if term == "prior-art map" and kind == "design":
            return {
                "term": term,
                "candidates": [
                    {
                        "kind": "design",
                        "structure": "Map patterns before choosing an approach.",
                        "rationale": "Separates evidence from selection.",
                        "when_to_use": "Before candidate generation.",
                        "when_not_to_use": "After a design is already fixed.",
                        "tradeoffs": "More up-front reasoning.",
                        "alternatives": "Jump directly to one approach.",
                        "implementation_hooks": "Feed patterns to candidate generation.",
                        "quality_attributes": "Traceability.",
                        "evidence": "Stored design facet.",
                        "delta_claim": "Non-obvious separation point.",
                        "lang_env_version": "general",
                        "author_level": "high",
                        "source_url": "https://example.test/design",
                    }
                ],
            }
        if term == "prior-art map" and kind == "implementation":
            return {
                "term": term,
                "candidates": [
                    {
                        "kind": "implementation",
                        "snippet": "patterns = synthesize(reference_facets, repo_context)",
                        "summary": "Implementation facet combines Reference and repo context.",
                        "pitfalls": "Do not generate later-step candidate approaches.",
                        "lang_env_version": "Python",
                        "author_level": "medium",
                        "source_url": "https://example.test/impl",
                    }
                ],
            }
        return {"term": term, "candidates": []}

    def fake_query(filters):
        query_calls.append(filters)
        return []

    def fake_run_json(repo: Path, **kwargs):
        codex_calls.append((repo, kwargs))
        return {"ok": True, "raw": json.dumps(expected)}

    monkeypatch.setattr(receive_module.reference, "lookup", fake_lookup)
    monkeypatch.setattr(receive_module.reference, "query", fake_query)
    monkeypatch.setattr(receive_module.codex_exec, "run_json", fake_run_json)

    result = receive_module._build_prior_art_map(
        rfc_view,
        tmp_path,
        {"reference_terms": ["prior-art map"], "language": "Python"},
        {"problem": "Approach formation needs prior art."},
    )

    assert result == expected
    assert ("prior-art map", {"reference_terms": ["prior-art map"], "language": "Python"}, "design") in reference_calls
    assert (
        "prior-art map",
        {"reference_terms": ["prior-art map"], "language": "Python"},
        "implementation",
    ) in reference_calls
    assert all(pattern["disposition"] in {"adopt", "adapt", "reject"} for pattern in result["patterns"])
    assert len(codex_calls) == 1
    repo, kwargs = codex_calls[0]
    assert repo == tmp_path.resolve()
    assert kwargs["schema"] == receive_module.PRIOR_ART_MAP_SCHEMA
    assert kwargs["schema_filename"] == "rfc-prior-art-map.schema.json"
    assert kwargs["output_filename"] == "rfc-prior-art-map.json"
    assert kwargs["failure_label"] == "Codex prior-art mapping"
    assert "step 3" in kwargs["prompt"]
    assert "Reference design facets" in kwargs["prompt"]
    assert "Reference implementation facets" in kwargs["prompt"]
    assert "Inspect the repository read-only" in kwargs["prompt"]
    assert "Do not generate candidate approaches" in kwargs["prompt"]
    assert "Godot" in kwargs["prompt"]
    assert "Return only JSON matching the provided schema." in kwargs["prompt"]
    assert query_calls


def test_build_prior_art_map_fails_closed_on_invalid_shape(monkeypatch, tmp_path):
    invalid_outputs = [
        {"patterns": []},
        {"patterns": [{"pattern": "Too few"}]},
        {
            "patterns": [
                _prior_art_pattern("One", "adopt"),
                _prior_art_pattern("Two", "adapt"),
                _prior_art_pattern("Three", "defer"),
            ]
        },
        {
            "patterns": [
                _prior_art_pattern("One", "adopt"),
                _prior_art_pattern("Two", "adapt"),
                {**_prior_art_pattern("Three", "reject"), "extra": "not allowed"},
            ]
        },
        {
            "patterns": [
                _prior_art_pattern("One", "adopt"),
                _prior_art_pattern("Two", "adapt"),
                {**_prior_art_pattern("Three", "reject"), "tradeoffs": 42},
            ]
        },
    ]

    monkeypatch.setattr(receive_module.reference, "lookup", lambda term, context=None, kind=None: None)
    monkeypatch.setattr(receive_module.reference, "query", lambda filters: [])

    for payload in invalid_outputs:
        monkeypatch.setattr(
            receive_module.codex_exec,
            "run_json",
            lambda repo, **kwargs: {"ok": True, "raw": json.dumps(payload)},
        )

        result = receive_module._build_prior_art_map(
            _rfc_view(),
            tmp_path,
            {"reference_terms": ["prior-art map"]},
        )

        assert result["ok"] is False
        assert "Codex prior-art mapping returned invalid" in result["error"]


def test_prior_art_map_schema_is_codex_valid():
    schema = receive_module.PRIOR_ART_MAP_SCHEMA
    serialized = json.dumps(schema)
    assert "allOf" not in serialized
    assert "anyOf" not in serialized
    assert "oneOf" not in serialized
    _assert_codex_valid_object_schema(schema)

    pattern = schema["properties"]["patterns"]["items"]
    assert pattern["properties"]["disposition"]["enum"] == ["adopt", "adapt", "reject"]
    assert schema["properties"]["patterns"]["minItems"] == 3
    assert schema["properties"]["patterns"]["maxItems"] == 6


def test_generate_candidates_returns_parsed_candidates(monkeypatch, tmp_path):
    expected = {
        "candidates": [
            {
                "name": "Local Parser Guard",
                "kind": "minimal_local",
                "summary": "Add the smallest RFC-local helper and parser needed for this formation step.",
                "key_decisions": [
                    "Reuse the existing codex_exec.run_json path.",
                    "Keep validation private to receive.py.",
                ],
                "draws_on": ["Repo-native read-only inspection"],
            },
            {
                "name": "Reference-Aligned Receive Step",
                "kind": "repo_native",
                "summary": "Mirror the prior receive step helpers with a schema, prompt, parser, and tests.",
                "key_decisions": [
                    "Follow the step 1 through 3 helper layout.",
                    "Use prior-art pattern names in draws_on.",
                ],
                "draws_on": ["Reference-first prior-art synthesis", "Repo-native read-only inspection"],
            },
            {
                "name": "Approach Strategy Layer",
                "kind": "general_architectural",
                "summary": "Introduce a broader strategy boundary only if later approach steps need reuse.",
                "key_decisions": [
                    "Keep the layer conceptual at candidate generation time.",
                    "Defer selection to later steps.",
                ],
                "draws_on": ["Frequency-based framework selection"],
            },
        ]
    }
    calls = []

    def fake_run_json(repo: Path, **kwargs):
        calls.append((repo, kwargs))
        return {"ok": True, "raw": json.dumps(expected)}

    monkeypatch.setattr(receive_module.codex_exec, "run_json", fake_run_json)

    result = receive_module._generate_candidates(
        _normalized_problem(),
        _constraints(),
        _prior_art_map(),
        {"repo": tmp_path},
    )

    assert result == expected
    assert len(calls) == 1
    repo, kwargs = calls[0]
    assert repo == tmp_path.resolve()
    assert kwargs["schema"] == receive_module.GENERATE_CANDIDATES_SCHEMA
    assert kwargs["schema_filename"] == "rfc-generate-candidates.schema.json"
    assert kwargs["output_filename"] == "rfc-candidate-approaches.json"
    assert kwargs["failure_label"] == "Codex candidate generation"
    assert "step 4" in kwargs["prompt"]
    assert "Always include one minimal_local candidate" in kwargs["prompt"]
    assert "Always include one repo_native candidate" in kwargs["prompt"]
    assert "general_architectural" in kwargs["prompt"]
    assert "do_nothing_defer" in kwargs["prompt"]
    assert "does not count toward the 2 to 3 substantive candidates" in kwargs["prompt"]
    assert "Do not select a winner" in kwargs["prompt"]
    assert "Return only JSON matching the provided schema." in kwargs["prompt"]


def test_generate_candidates_fails_closed_on_invalid_shape(monkeypatch, tmp_path):
    valid_minimal = _candidate("Minimal", "minimal_local")
    valid_repo = _candidate("Repo Native", "repo_native")
    valid_general = _candidate("General", "general_architectural")
    valid_defer = _candidate("Defer", "do_nothing_defer")
    invalid_outputs = [
        {"candidates": [valid_minimal]},
        {"candidates": [valid_minimal, valid_repo], "extra": "not allowed"},
        {"candidates": [{**valid_minimal, "extra": "not allowed"}, valid_repo]},
        {"candidates": [{**valid_minimal, "kind": "unknown"}, valid_repo]},
        {"candidates": [{**valid_minimal, "key_decisions": [42]}, valid_repo]},
        {"candidates": [valid_minimal, {**valid_repo, "draws_on": "Reference"}]},
        {"candidates": [valid_minimal, _candidate("Duplicate", "minimal_local")]},
        {"candidates": [valid_minimal, valid_general]},
        {"candidates": [valid_defer, valid_minimal]},
    ]

    for payload in invalid_outputs:
        monkeypatch.setattr(
            receive_module.codex_exec,
            "run_json",
            lambda repo, **kwargs: {"ok": True, "raw": json.dumps(payload)},
        )

        result = receive_module._generate_candidates(
            _normalized_problem(),
            _constraints(),
            _prior_art_map(),
            {"repo": tmp_path},
        )

        assert result["ok"] is False
        assert "Codex candidate generation returned" in result["error"]


def test_generate_candidates_schema_is_codex_valid():
    schema = receive_module.GENERATE_CANDIDATES_SCHEMA
    serialized = json.dumps(schema)
    assert "allOf" not in serialized
    assert "anyOf" not in serialized
    assert "oneOf" not in serialized
    _assert_codex_valid_object_schema(schema)

    candidate = schema["properties"]["candidates"]["items"]
    assert candidate["properties"]["kind"]["enum"] == [
        "minimal_local",
        "repo_native",
        "general_architectural",
        "do_nothing_defer",
    ]
    assert schema["properties"]["candidates"]["minItems"] == 2
    assert schema["properties"]["candidates"]["maxItems"] == 4


def test_evaluate_candidates_returns_parsed_matrix(monkeypatch, tmp_path):
    candidates = _candidates()
    expected = {
        "evaluations": [
            {
                "candidate_name": "Minimal",
                "scores": _evaluation_scores(
                    problem_fit="medium - solves the core problem with narrow scope.",
                    repo_fit="high - follows existing receive.py helper boundaries.",
                ),
                "summary": "Lowest implementation cost, with limited architectural improvement.",
            },
            {
                "candidate_name": "Repo Native",
                "scores": _evaluation_scores(
                    problem_fit="high - covers the normalized problem directly.",
                    repo_fit="high - mirrors the established RFC approach formation pattern.",
                ),
                "summary": "Best repository fit while keeping the step isolated.",
            },
        ]
    }
    calls = []

    def fake_run_json(repo: Path, **kwargs):
        calls.append((repo, kwargs))
        return {"ok": True, "raw": json.dumps(expected)}

    monkeypatch.setattr(receive_module.codex_exec, "run_json", fake_run_json)

    result = receive_module._evaluate_candidates(
        candidates,
        _normalized_problem(),
        _constraints(),
        {"repo": tmp_path},
    )

    assert result == expected
    assert len(result["evaluations"]) == len(candidates["candidates"])
    assert len(calls) == 1
    repo, kwargs = calls[0]
    assert repo == tmp_path.resolve()
    assert kwargs["schema"] == receive_module.EVALUATE_CANDIDATES_SCHEMA
    assert kwargs["schema_filename"] == "rfc-evaluate-candidates.schema.json"
    assert kwargs["output_filename"] == "rfc-candidate-evaluations.json"
    assert kwargs["failure_label"] == "Codex candidate evaluation"
    assert "step 5" in kwargs["prompt"]
    assert "compact matrix" in kwargs["prompt"]
    assert "Do not select a winner" in kwargs["prompt"]
    assert "Return one evaluation per candidate" in kwargs["prompt"]
    assert "Return only JSON matching the provided schema." in kwargs["prompt"]
    for evaluation in result["evaluations"]:
        assert set(evaluation["scores"]) == set(receive_module.CANDIDATE_EVALUATION_SCORE_FIELDS)


def test_evaluate_candidates_fails_closed_on_invalid_shape(monkeypatch, tmp_path):
    valid = {
        "evaluations": [
            {
                "candidate_name": "Minimal",
                "scores": _evaluation_scores(),
                "summary": "Summary.",
            },
            {
                "candidate_name": "Repo Native",
                "scores": _evaluation_scores(),
                "summary": "Summary.",
            },
        ]
    }
    invalid_outputs = [
        {"evaluations": [valid["evaluations"][0]]},
        {"evaluations": valid["evaluations"], "extra": "not allowed"},
        {"evaluations": [{**valid["evaluations"][0], "extra": "not allowed"}, valid["evaluations"][1]]},
        {"evaluations": [{**valid["evaluations"][0], "candidate_name": "Unknown"}, valid["evaluations"][1]]},
        {"evaluations": [valid["evaluations"][0], {**valid["evaluations"][1], "summary": 42}]},
        {"evaluations": [{**valid["evaluations"][0], "scores": "bad"}, valid["evaluations"][1]]},
        {
            "evaluations": [
                {
                    **valid["evaluations"][0],
                    "scores": {**_evaluation_scores(), "extra": "not allowed"},
                },
                valid["evaluations"][1],
            ]
        },
        {
            "evaluations": [
                {
                    **valid["evaluations"][0],
                    "scores": {field: "high - reason." for field in receive_module.CANDIDATE_EVALUATION_SCORE_FIELDS[:-1]},
                },
                valid["evaluations"][1],
            ]
        },
        {
            "evaluations": [
                {
                    **valid["evaluations"][0],
                    "scores": {**_evaluation_scores(), "risk": 42},
                },
                valid["evaluations"][1],
            ]
        },
        {"evaluations": [valid["evaluations"][0], {**valid["evaluations"][1], "candidate_name": "Minimal"}]},
    ]

    for payload in invalid_outputs:
        monkeypatch.setattr(
            receive_module.codex_exec,
            "run_json",
            lambda repo, **kwargs: {"ok": True, "raw": json.dumps(payload)},
        )

        result = receive_module._evaluate_candidates(
            _candidates(),
            _normalized_problem(),
            _constraints(),
            {"repo": tmp_path},
        )

        assert result["ok"] is False
        assert "Codex candidate evaluation returned" in result["error"]


def test_evaluate_candidates_schema_is_codex_valid():
    schema = receive_module.EVALUATE_CANDIDATES_SCHEMA
    serialized = json.dumps(schema)
    assert "allOf" not in serialized
    assert "anyOf" not in serialized
    assert "oneOf" not in serialized
    _assert_codex_valid_object_schema(schema)

    evaluation = schema["properties"]["evaluations"]["items"]
    scores = evaluation["properties"]["scores"]
    assert scores["additionalProperties"] is False
    assert sorted(scores["required"]) == sorted(receive_module.CANDIDATE_EVALUATION_SCORE_FIELDS)
    assert sorted(scores["required"]) == sorted(scores["properties"])


def test_select_approach_returns_parsed_selection(monkeypatch, tmp_path):
    expected = {
        "chosen": "Repo Native",
        "decision": (
            "Choose Repo Native because the evaluation matrix gives it the strongest problem fit and repo fit "
            "under constraints that keep RFC receive isolated, accepting tradeoff of slightly more prompt detail."
        ),
        "accepted_tradeoffs": ["Slightly more prompt and parser surface than the minimal local option."],
        "rejected": [
            {
                "candidate_name": "Minimal",
                "why_not": "The matrix shows weaker problem coverage despite low complexity.",
            }
        ],
    }
    calls = []

    def fake_run_json(repo: Path, **kwargs):
        calls.append((repo, kwargs))
        return {"ok": True, "raw": json.dumps(expected)}

    monkeypatch.setattr(receive_module.codex_exec, "run_json", fake_run_json)

    result = receive_module._select_approach(
        _candidates(),
        _evaluations(),
        _constraints(),
        {"repo": tmp_path},
    )

    assert result == expected
    assert result["chosen"] == "Repo Native"
    assert "evaluation matrix" in result["decision"]
    assert "constraints" in result["decision"]
    assert result["accepted_tradeoffs"] == ["Slightly more prompt and parser surface than the minimal local option."]
    assert result["rejected"] == [
        {
            "candidate_name": "Minimal",
            "why_not": "The matrix shows weaker problem coverage despite low complexity.",
        }
    ]
    assert len(calls) == 1
    repo, kwargs = calls[0]
    assert repo == tmp_path.resolve()
    assert kwargs["schema"] == receive_module.SELECT_APPROACH_SCHEMA
    assert kwargs["schema_filename"] == "rfc-select-approach.schema.json"
    assert kwargs["output_filename"] == "rfc-selected-approach.json"
    assert kwargs["failure_label"] == "Codex approach selection"
    assert "step 6" in kwargs["prompt"]
    assert "select the approach" in kwargs["prompt"]
    assert "evaluation matrix" in kwargs["prompt"]
    assert "constraints" in kwargs["prompt"]
    assert "Do not write an implementation strategy" in kwargs["prompt"]
    assert "Return only JSON matching the provided schema." in kwargs["prompt"]


def test_select_approach_fails_closed_on_invalid_shape(monkeypatch, tmp_path):
    valid = {
        "chosen": "Repo Native",
        "decision": (
            "Choose Repo Native because the evaluation matrix is strongest under constraints that isolate RFC "
            "receive, accepting tradeoff of added prompt detail."
        ),
        "accepted_tradeoffs": ["Added prompt detail."],
        "rejected": [{"candidate_name": "Minimal", "why_not": "The matrix shows weaker problem fit."}],
    }
    invalid_outputs = [
        {key: value for key, value in valid.items() if key != "chosen"},
        {**valid, "extra": "not allowed"},
        {**valid, "chosen": "Unknown"},
        {**valid, "decision": "Choose Repo Native because it is best."},
        {**valid, "accepted_tradeoffs": ["Added prompt detail.", 42]},
        {**valid, "rejected": "Minimal"},
        {**valid, "rejected": [{"candidate_name": "Minimal"}]},
        {**valid, "rejected": [{"candidate_name": "Unknown", "why_not": "Not a candidate."}]},
        {**valid, "rejected": [{"candidate_name": "Repo Native", "why_not": "Rejects chosen."}]},
        {**valid, "rejected": []},
    ]

    for payload in invalid_outputs:
        monkeypatch.setattr(
            receive_module.codex_exec,
            "run_json",
            lambda repo, **kwargs: {"ok": True, "raw": json.dumps(payload)},
        )

        result = receive_module._select_approach(
            _candidates(),
            _evaluations(),
            _constraints(),
            {"repo": tmp_path},
        )

        assert result["ok"] is False
        assert "Codex approach selection returned" in result["error"]


def test_select_approach_schema_is_codex_valid():
    schema = receive_module.SELECT_APPROACH_SCHEMA
    serialized = json.dumps(schema)
    assert "allOf" not in serialized
    assert "anyOf" not in serialized
    assert "oneOf" not in serialized
    _assert_codex_valid_object_schema(schema)

    rejected = schema["properties"]["rejected"]["items"]
    assert rejected["additionalProperties"] is False
    assert sorted(rejected["required"]) == sorted(receive_module.REJECTED_APPROACH_FIELDS)
    assert sorted(rejected["required"]) == sorted(rejected["properties"])


def test_implementation_strategy_returns_parsed_strategy(monkeypatch, tmp_path):
    expected = {
        "main_changes": [
            "Add IMPLEMENTATION_STRATEGY_SCHEMA beside the other Technical Approach step schemas.",
            "Add _implementation_strategy with one codex_exec.run_json call and a strict parser.",
        ],
        "affected_modules": ["ai_org.rfc.receive", "tests.test_rfc_approach"],
        "data_api_config_changes": ["New private RFC formation JSON shape for implementation strategy."],
        "migration_compat": "No runtime migration; this is an additive private helper.",
        "testing_plan": "Extend tests/test_rfc_approach.py and run pytest tests/.",
        "observability": "n/a",
    }
    calls = []

    def fake_run_json(repo: Path, **kwargs):
        calls.append((repo, kwargs))
        return {"ok": True, "raw": json.dumps(expected)}

    monkeypatch.setattr(receive_module.codex_exec, "run_json", fake_run_json)

    result = receive_module._implementation_strategy(
        _chosen(),
        _prior_art_map(),
        _constraints(),
        _rfc_view(),
        tmp_path,
        {"request_id": "rfc-step-7"},
    )

    assert result == expected
    assert result["main_changes"] == expected["main_changes"]
    assert result["affected_modules"] == ["ai_org.rfc.receive", "tests.test_rfc_approach"]
    assert result["data_api_config_changes"] == ["New private RFC formation JSON shape for implementation strategy."]
    assert result["migration_compat"] == "No runtime migration; this is an additive private helper."
    assert result["testing_plan"] == "Extend tests/test_rfc_approach.py and run pytest tests/."
    assert result["observability"] == "n/a"
    assert len(calls) == 1
    repo, kwargs = calls[0]
    assert repo == tmp_path.resolve()
    assert kwargs["schema"] == receive_module.IMPLEMENTATION_STRATEGY_SCHEMA
    assert kwargs["schema_filename"] == "rfc-implementation-strategy.schema.json"
    assert kwargs["output_filename"] == "rfc-implementation-strategy.json"
    assert kwargs["failure_label"] == "Codex implementation strategy"
    assert "step 7" in kwargs["prompt"]
    assert "implementation strategy" in kwargs["prompt"]
    assert "read-only repository inspection" in kwargs["prompt"]
    assert "Do not modify files" in kwargs["prompt"]
    assert "Do not generate or re-evaluate alternatives" in kwargs["prompt"]
    assert "Return only JSON matching the provided schema." in kwargs["prompt"]


def test_implementation_strategy_fails_closed_on_invalid_shape(monkeypatch, tmp_path):
    valid = {
        "main_changes": ["Add helper."],
        "affected_modules": ["ai_org.rfc.receive"],
        "data_api_config_changes": [],
        "migration_compat": "No migration.",
        "testing_plan": "Run pytest tests/.",
        "observability": "n/a",
    }
    invalid_outputs = [
        {key: value for key, value in valid.items() if key != "main_changes"},
        {**valid, "extra": "not allowed"},
        {**valid, "main_changes": "Add helper."},
        {**valid, "affected_modules": [42]},
        {**valid, "data_api_config_changes": ["Config.", 42]},
        {**valid, "migration_compat": ["No migration."]},
        {**valid, "testing_plan": 42},
        {**valid, "observability": None},
    ]

    for payload in invalid_outputs:
        monkeypatch.setattr(
            receive_module.codex_exec,
            "run_json",
            lambda repo, **kwargs: {"ok": True, "raw": json.dumps(payload)},
        )

        result = receive_module._implementation_strategy(
            _chosen(),
            _prior_art_map(),
            _constraints(),
            _rfc_view(),
            tmp_path,
        )

        assert result["ok"] is False
        assert "Codex implementation strategy returned" in result["error"]


def test_implementation_strategy_schema_is_codex_valid():
    schema = receive_module.IMPLEMENTATION_STRATEGY_SCHEMA
    serialized = json.dumps(schema)
    assert "allOf" not in serialized
    assert "anyOf" not in serialized
    assert "oneOf" not in serialized
    _assert_codex_valid_object_schema(schema)

    assert sorted(schema["required"]) == sorted(receive_module.IMPLEMENTATION_STRATEGY_FIELDS)
    for field in ("main_changes", "affected_modules", "data_api_config_changes"):
        assert schema["properties"][field]["type"] == "array"
        assert schema["properties"][field]["items"]["type"] == "string"


def test_right_size_patch_plan_returns_parsed_plan(monkeypatch, tmp_path):
    expected = {
        "first_slice": "Add the private schema, helper, parser, and focused tests as one working walking skeleton.",
        "follow_up_slices": [
            "Wire the patch plan into the final Technical Approach emitter after steps 9 and 10 exist.",
            "Refine slice wording once reviewer question surfacing is available.",
        ],
        "deferred": [
            {
                "item": "Automatic issue creation for deferred work.",
                "why_safe_to_defer": "It is speculative workflow automation and does not affect the RFC formation contract.",
            }
        ],
        "yagni_note": "Do not build orchestration for steps 9 and 10 in this slice because step 8 is additive and private.",
    }
    calls = []

    def fake_run_json(repo: Path, **kwargs):
        calls.append((repo, kwargs))
        return {"ok": True, "raw": json.dumps(expected)}

    monkeypatch.setattr(receive_module.codex_exec, "run_json", fake_run_json)

    result = receive_module._right_size_patch_plan(
        _chosen(),
        _implementation_strategy(),
        _constraints(),
        {"repo": tmp_path, "request_id": "rfc-step-8"},
    )

    assert result == expected
    assert result["first_slice"] == expected["first_slice"]
    assert result["follow_up_slices"] == expected["follow_up_slices"]
    assert result["deferred"] == expected["deferred"]
    assert result["yagni_note"] == expected["yagni_note"]
    assert len(calls) == 1
    repo, kwargs = calls[0]
    assert repo == tmp_path.resolve()
    assert kwargs["schema"] == receive_module.RIGHT_SIZE_PATCH_PLAN_SCHEMA
    assert kwargs["schema_filename"] == "rfc-right-size-patch-plan.schema.json"
    assert kwargs["output_filename"] == "rfc-right-sized-patch-plan.json"
    assert kwargs["failure_label"] == "Codex patch plan right-sizing"
    assert "step 8" in kwargs["prompt"]
    assert "right-size the patch plan" in kwargs["prompt"]
    assert "read-only repository inspection" in kwargs["prompt"]
    assert "Do not modify files" in kwargs["prompt"]
    assert "Every slice must leave the system working" in kwargs["prompt"]
    assert "Apply YAGNI" in kwargs["prompt"]
    assert "hard to reverse" in kwargs["prompt"]
    assert "quality attributes" in kwargs["prompt"]
    assert "Return only JSON matching the provided schema." in kwargs["prompt"]


def test_right_size_patch_plan_fails_closed_on_invalid_shape(monkeypatch, tmp_path):
    valid = {
        "first_slice": "Add the helper and tests as a walking skeleton.",
        "follow_up_slices": ["Wire into final approach emission later."],
        "deferred": [{"item": "Automation.", "why_safe_to_defer": "Speculative."}],
        "yagni_note": "Do not build later steps now.",
    }
    invalid_outputs = [
        {key: value for key, value in valid.items() if key != "first_slice"},
        {**valid, "extra": "not allowed"},
        {**valid, "first_slice": ["Add helper."]},
        {**valid, "follow_up_slices": "Wire later."},
        {**valid, "follow_up_slices": ["Wire later.", 42]},
        {**valid, "deferred": "None."},
        {**valid, "deferred": [{"item": "Automation."}]},
        {**valid, "deferred": [{"item": "Automation.", "why_safe_to_defer": "Speculative.", "extra": "no"}]},
        {**valid, "deferred": [{"item": "Automation.", "why_safe_to_defer": 42}]},
        {**valid, "yagni_note": None},
    ]

    for payload in invalid_outputs:
        monkeypatch.setattr(
            receive_module.codex_exec,
            "run_json",
            lambda repo, **kwargs: {"ok": True, "raw": json.dumps(payload)},
        )

        result = receive_module._right_size_patch_plan(
            _chosen(),
            _implementation_strategy(),
            _constraints(),
            {"repo": tmp_path},
        )

        assert result["ok"] is False
        assert "Codex patch plan right-sizing returned" in result["error"]


def test_right_size_patch_plan_schema_is_codex_valid():
    schema = receive_module.RIGHT_SIZE_PATCH_PLAN_SCHEMA
    serialized = json.dumps(schema)
    assert "allOf" not in serialized
    assert "anyOf" not in serialized
    assert "oneOf" not in serialized
    _assert_codex_valid_object_schema(schema)

    assert sorted(schema["required"]) == sorted(receive_module.RIGHT_SIZE_PATCH_PLAN_FIELDS)
    assert schema["properties"]["follow_up_slices"]["items"]["type"] == "string"
    deferred = schema["properties"]["deferred"]["items"]
    assert deferred["additionalProperties"] is False
    assert sorted(deferred["required"]) == sorted(receive_module.PATCH_PLAN_DEFERRED_FIELDS)
    assert sorted(deferred["required"]) == sorted(deferred["properties"])


def test_surface_risks_returns_parsed_risks(monkeypatch, tmp_path):
    expected = {
        "assumptions": ["The existing RFC receive helper pattern remains the intended extension point."],
        "risks": [
            {
                "risk": "The prompt may duplicate step 10 final-section wording.",
                "mitigation": "Keep step 9 limited to raw risks, questions, spikes, and reviewer prompts.",
            }
        ],
        "open_questions": ["Should empty risk lists be allowed for trivial RFCs?"],
        "spikes": ["Run a mocked Codex response through the strict parser before wiring orchestration."],
        "reviewer_questions": ["Do these risks capture the main uncertainty before final Technical Approach emission?"],
    }
    calls = []

    def fake_run_json(repo: Path, **kwargs):
        calls.append((repo, kwargs))
        return {"ok": True, "raw": json.dumps(expected)}

    monkeypatch.setattr(receive_module.codex_exec, "run_json", fake_run_json)

    result = receive_module._surface_risks(
        _chosen(),
        _implementation_strategy(),
        _patch_plan(),
        _constraints(),
        {"repo": tmp_path, "request_id": "rfc-step-9"},
    )

    assert result == expected
    assert result["assumptions"] == expected["assumptions"]
    assert result["risks"] == expected["risks"]
    assert result["open_questions"] == expected["open_questions"]
    assert result["spikes"] == expected["spikes"]
    assert result["reviewer_questions"] == expected["reviewer_questions"]
    assert len(calls) == 1
    repo, kwargs = calls[0]
    assert repo == tmp_path.resolve()
    assert kwargs["schema"] == receive_module.SURFACE_RISKS_SCHEMA
    assert kwargs["schema_filename"] == "rfc-surface-risks.schema.json"
    assert kwargs["output_filename"] == "rfc-surfaced-risks.json"
    assert kwargs["failure_label"] == "Codex risk surfacing"
    assert "step 9" in kwargs["prompt"]
    assert "surface risks and open questions" in kwargs["prompt"]
    assert "read-only repository inspection" in kwargs["prompt"]
    assert "Do not modify files" in kwargs["prompt"]
    assert "prototypes or spikes" in kwargs["prompt"]
    assert "reviewer or requester" in kwargs["prompt"]
    assert "emit the final Technical Approach section" in kwargs["prompt"]
    assert "Return only JSON matching the provided schema." in kwargs["prompt"]


def test_surface_risks_fails_closed_on_invalid_shape(monkeypatch, tmp_path):
    valid = {
        "assumptions": ["Existing helper pattern remains valid."],
        "risks": [{"risk": "Prompt ambiguity.", "mitigation": "Constrain the step boundary."}],
        "open_questions": ["Is reviewer wording sufficient?"],
        "spikes": ["Mock parser path."],
        "reviewer_questions": ["Are these the right reviewer questions?"],
    }
    invalid_outputs = [
        {key: value for key, value in valid.items() if key != "assumptions"},
        {**valid, "extra": "not allowed"},
        {**valid, "assumptions": "Existing helper pattern remains valid."},
        {**valid, "assumptions": ["Existing helper pattern remains valid.", 42]},
        {**valid, "risks": "Prompt ambiguity."},
        {**valid, "risks": [{"risk": "Prompt ambiguity."}]},
        {**valid, "risks": [{"risk": "Prompt ambiguity.", "mitigation": "Constrain.", "extra": "no"}]},
        {**valid, "risks": [{"risk": "Prompt ambiguity.", "mitigation": 42}]},
        {**valid, "open_questions": [42]},
        {**valid, "spikes": None},
        {**valid, "reviewer_questions": ["Question?", 42]},
    ]

    for payload in invalid_outputs:
        monkeypatch.setattr(
            receive_module.codex_exec,
            "run_json",
            lambda repo, **kwargs: {"ok": True, "raw": json.dumps(payload)},
        )

        result = receive_module._surface_risks(
            _chosen(),
            _implementation_strategy(),
            _patch_plan(),
            _constraints(),
            {"repo": tmp_path},
        )

        assert result["ok"] is False
        assert "Codex risk surfacing returned" in result["error"]


def test_surface_risks_schema_is_codex_valid():
    schema = receive_module.SURFACE_RISKS_SCHEMA
    serialized = json.dumps(schema)
    assert "allOf" not in serialized
    assert "anyOf" not in serialized
    assert "oneOf" not in serialized
    _assert_codex_valid_object_schema(schema)

    assert sorted(schema["required"]) == sorted(receive_module.SURFACE_RISKS_FIELDS)
    for field in ("assumptions", "open_questions", "spikes", "reviewer_questions"):
        assert schema["properties"][field]["items"]["type"] == "string"
    risk = schema["properties"]["risks"]["items"]
    assert risk["additionalProperties"] is False
    assert sorted(risk["required"]) == sorted(receive_module.SURFACED_RISK_FIELDS)
    assert sorted(risk["required"]) == sorted(risk["properties"])


def test_receive_imports_reference_without_importing_later_phases():
    source = Path(receive_module.__file__).read_text(encoding="utf-8")

    assert "import ai_org.reference as reference" in source
    assert "ai_org.patch" not in source
    assert "ai_org.merge" not in source


def _assert_codex_valid_object_schema(schema: dict[str, object]) -> None:
    if schema.get("type") == "object":
        assert schema["additionalProperties"] is False
        assert sorted(schema["required"]) == sorted(schema["properties"])
        for subschema in schema["properties"].values():
            _assert_codex_valid_object_schema(subschema)
    if schema.get("type") == "array":
        _assert_codex_valid_object_schema(schema["items"])


def _rfc_view() -> dict[str, object]:
    return {
        "title": "Normalize RFC Problem",
        "problem": "Approach formation needs a crisp problem statement.",
        "proposal": "Normalize the grounded common-8 RFC before later approach steps.",
        "alternatives": ["Let later approach generation infer the problem repeatedly."],
        "intended_users": "RFC authors and reviewers.",
        "affected_area": "ai_org.rfc",
        "impact": "Technical Approach formation starts from clearer boundaries.",
        "context": "Step 1 of the documented procedure.",
    }


def _prior_art_pattern(name: str, disposition: str) -> dict[str, object]:
    return {
        "pattern": name,
        "where_seen": "Reference.",
        "when_applies": "When applicable.",
        "tradeoffs": "Tradeoffs.",
        "disposition": disposition,
        "rationale": "Rationale.",
    }


def _normalized_problem() -> dict[str, object]:
    return {
        "problem": "RFC formation needs candidate approaches.",
        "affected": "RFC reviewers and implementation agents.",
        "current_inadequacy": "Prior-art evidence exists but no approach options are generated.",
        "success_criteria": ["Generate distinct candidate approaches."],
        "non_goals": ["Do not select a candidate in step 4."],
    }


def _constraints() -> dict[str, object]:
    return {
        "hard_constraints": [
            {
                "constraint": "Keep RFC receive isolated from patch and merge modules.",
                "source": "repo",
                "why": "RFC formation must not import later phases.",
            }
        ],
        "soft_preferences": [
            {
                "preference": "Reuse existing RFC Codex helper patterns.",
                "source": "repo",
                "why": "Steps 1 through 3 already use codex_exec.run_json.",
            }
        ],
    }


def _prior_art_map() -> dict[str, object]:
    return {
        "patterns": [
            _prior_art_pattern("Reference-first prior-art synthesis", "adopt"),
            _prior_art_pattern("Repo-native read-only inspection", "adapt"),
            _prior_art_pattern("Frequency-based framework selection", "reject"),
        ]
    }


def _candidate(name: str, kind: str) -> dict[str, object]:
    return {
        "name": name,
        "kind": kind,
        "summary": "Summary.",
        "key_decisions": ["Decision."],
        "draws_on": ["Reference-first prior-art synthesis"],
    }


def _candidates() -> dict[str, object]:
    return {"candidates": [_candidate("Minimal", "minimal_local"), _candidate("Repo Native", "repo_native")]}


def _evaluations() -> dict[str, object]:
    return {
        "evaluations": [
            {
                "candidate_name": "Minimal",
                "scores": _evaluation_scores(
                    problem_fit="medium - solves the core problem with narrow scope.",
                    repo_fit="high - follows existing receive.py helper boundaries.",
                ),
                "summary": "Lowest implementation cost, with limited architectural improvement.",
            },
            {
                "candidate_name": "Repo Native",
                "scores": _evaluation_scores(
                    problem_fit="high - covers the normalized problem directly.",
                    repo_fit="high - mirrors the established RFC approach formation pattern.",
                ),
                "summary": "Best repository fit while keeping the step isolated.",
            },
        ]
    }


def _chosen() -> dict[str, object]:
    return {
        "chosen": "Repo Native",
        "decision": (
            "Choose Repo Native because the evaluation matrix is strongest under constraints that isolate RFC "
            "receive, accepting tradeoff of added prompt detail."
        ),
        "accepted_tradeoffs": ["Added prompt detail."],
        "rejected": [{"candidate_name": "Minimal", "why_not": "The matrix shows weaker problem fit."}],
    }


def _implementation_strategy() -> dict[str, object]:
    return {
        "main_changes": [
            "Add a private right-size patch plan schema.",
            "Add a read-only Codex helper and strict parser.",
        ],
        "affected_modules": ["ai_org.rfc.receive", "tests.test_rfc_approach"],
        "data_api_config_changes": ["New private RFC formation JSON shape for patch plan sizing."],
        "migration_compat": "No migration; additive private helper.",
        "testing_plan": "Run pytest tests/.",
        "observability": "n/a",
    }


def _patch_plan() -> dict[str, object]:
    return {
        "first_slice": "Add the private helper and focused tests as one working slice.",
        "follow_up_slices": ["Wire step 9 into final approach emission after step 10 exists."],
        "deferred": [{"item": "Automated reviewer routing.", "why_safe_to_defer": "Not needed for step 9."}],
        "yagni_note": "Do not build final section emission in step 9.",
    }


def _evaluation_scores(**overrides: str) -> dict[str, object]:
    scores = {
        "problem_fit": "high - fits the normalized problem.",
        "repo_fit": "high - fits existing module boundaries.",
        "complexity": "low - limited moving parts.",
        "quality_attributes": "medium - preserves relevant quality attributes.",
        "compat_migration": "high - no compatibility migration expected.",
        "testability": "high - directly testable with existing unit tests.",
        "operability": "medium - no operational burden expected.",
        "reversibility": "high - easy to revise before later steps.",
        "risk": "low - main risk is prompt ambiguity.",
        "evidence": "high - supported by prior step outputs.",
    }
    scores.update(overrides)
    return scores
