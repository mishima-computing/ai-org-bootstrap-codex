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
            _success_criterion(
                action="Read the normalized problem before candidate generation.",
                expected_state="The problem object contains nested measurable success criteria.",
                evidence="The success_criteria array contains actor, capability, verifiable_outcome, and verification tags.",
                check="Assert every criterion has all nested tags and sub-tags populated.",
            ),
            _success_criterion(
                action="Compare non-goals with later approach outputs.",
                expected_state="Later approach steps can identify work that is out of scope.",
                evidence="The non_goals array contains explicit boundaries before candidate generation.",
                check="Assert non_goals is a list of non-empty English strings.",
            ),
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
    _assert_codex_valid_object_schema(schema)

    criterion_schema = schema["properties"]["success_criteria"]["items"]
    assert sorted(criterion_schema["properties"]) == [
        "actor",
        "capability",
        "verifiable_outcome",
        "verification",
    ]
    assert sorted(criterion_schema["properties"]["capability"]["properties"]) == ["action", "preconditions"]
    assert sorted(criterion_schema["properties"]["verifiable_outcome"]["properties"]) == [
        "evidence",
        "expected_state",
    ]
    assert sorted(criterion_schema["properties"]["verification"]["properties"]) == ["check", "method"]
    assert criterion_schema["properties"]["verification"]["properties"]["method"]["enum"] == [
        "automated_test",
        "manual_check",
        "metric",
    ]


def test_normalize_problem_rejects_lint_failure_and_regenerates(monkeypatch, tmp_path):
    first = {
        "problem": "RFC formation lacks a normalized problem statement before approach design.",
        "affected": "RFC reviewers and downstream implementation agents.",
        "current_inadequacy": "The current RFC view lacks measurable step-one boundaries.",
        "success_criteria": [
            _success_criterion(
                action="complete",
                expected_state="The normalized problem has measurable criteria.",
                evidence="",
                check="Verify criteria fields.",
            )
        ],
        "non_goals": ["Do not generate candidate approaches in this step."],
    }
    second = {
        **first,
        "success_criteria": [
            _success_criterion(
                action="Read the normalized problem before candidate generation.",
                expected_state="The criteria object identifies a checkable end state for step 1.",
                evidence="The JSON contains populated nested fields for actor, capability, outcome, and verification.",
                check="Assert nested success criteria slots are non-empty and measurable.",
            )
        ],
    }
    calls = []

    def fake_run_json(repo: Path, **kwargs):
        calls.append(kwargs)
        return {"ok": True, "raw": json.dumps(first if len(calls) == 1 else second)}

    monkeypatch.setattr(receive_module.codex_exec, "run_json", fake_run_json)

    result = receive_module._normalize_problem(_rfc_view(), {"repo": tmp_path})

    assert result == second
    assert len(calls) == 2
    assert "Previous output failed deterministic validation" in calls[1]["prompt"]
    assert "success_criteria[0].capability.action uses a vague standalone value" in calls[1]["prompt"]
    assert "success_criteria[0].verifiable_outcome.evidence is empty" in calls[1]["prompt"]


def test_normalize_problem_fails_closed_when_still_unmeasurable(monkeypatch, tmp_path):
    unmeasurable = {
        "problem": "RFC formation lacks a normalized problem statement before approach design.",
        "affected": "RFC reviewers and downstream implementation agents.",
        "current_inadequacy": "The current RFC view lacks measurable step-one boundaries.",
        "success_criteria": [
            _success_criterion(
                action="Read the normalized problem before candidate generation.",
                expected_state="complete",
                evidence="The JSON contains populated nested fields.",
                check="Verify criteria fields.",
            )
        ],
        "non_goals": ["Do not generate candidate approaches in this step."],
    }
    calls = []

    def fake_run_json(repo: Path, **kwargs):
        calls.append(kwargs)
        return {"ok": True, "raw": json.dumps(unmeasurable)}

    monkeypatch.setattr(receive_module.codex_exec, "run_json", fake_run_json)

    result = receive_module._normalize_problem(_rfc_view(), {"repo": tmp_path})

    assert result["ok"] is False
    assert "remained unmeasurable after 3 attempts" in result["error"]
    assert "success_criteria[0].verifiable_outcome.expected_state uses a vague standalone value" in result["error"]
    assert len(calls) == 3


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
                "source": "success_criteria",
                "why": "The measurable success criteria require codex-valid schemas.",
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

    normalized_problem = _normalized_problem()
    result = receive_module._extract_constraints(
        rfc_view,
        tmp_path,
        {"normalized": "problem"},
        normalized_problem,
    )

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
    assert "Step 1 normalized problem" in kwargs["prompt"]
    assert normalized_problem["problem"] in kwargs["prompt"]
    assert normalized_problem["success_criteria"][0]["verifiable_outcome"]["expected_state"] in kwargs["prompt"]
    assert "verifiable_outcome and verification" in kwargs["prompt"]
    assert "success_criteria" in kwargs["prompt"]
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
    assert hard_item["properties"]["source"]["enum"] == ["repo", "rfc", "domain", "success_criteria"]
    assert soft_item["properties"]["source"]["enum"] == ["repo", "rfc", "domain", "success_criteria"]


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


def test_form_technical_approach_runs_full_generation_and_composes_section(monkeypatch, tmp_path):
    calls = []

    def fake_normalize(rfc_view, context=None):
        calls.append("normalize_problem")
        assert rfc_view == _rfc_view()
        assert context["repo"] == tmp_path.resolve()
        return _normalized_problem()

    def fake_constraints(rfc_view, repo, context=None, normalized_problem=None):
        calls.append("extract_constraints")
        assert rfc_view == _rfc_view()
        assert repo == tmp_path.resolve()
        assert normalized_problem == _normalized_problem()
        return _constraints()

    def fake_prior_art(rfc_view, repo, context=None, normalized_problem=None):
        calls.append("build_prior_art_map")
        assert normalized_problem == _normalized_problem()
        return _prior_art_map()

    def fake_candidates(normalized_problem, constraints, prior_art_map, context=None):
        calls.append("generate_candidates")
        assert normalized_problem == _normalized_problem()
        assert constraints == _constraints()
        assert prior_art_map == _prior_art_map()
        return _candidates()

    def fake_evaluations(candidates, normalized_problem, constraints, context=None):
        calls.append("evaluate_candidates")
        assert candidates == _candidates()
        return _evaluations()

    def fake_selection(candidates, evaluations, constraints, context=None):
        calls.append("select_approach")
        assert candidates == _candidates()
        assert evaluations == _evaluations()
        return _chosen()

    def fake_implementation(chosen, prior_art_map, constraints, rfc_view, repo, context=None):
        calls.append("implementation_strategy")
        assert chosen == _chosen()
        assert prior_art_map == _prior_art_map()
        assert repo == tmp_path.resolve()
        return _implementation_strategy()

    def fake_patch_plan(chosen, implementation_strategy, constraints, context=None):
        calls.append("right_size_patch_plan")
        assert chosen == _chosen()
        assert implementation_strategy == _implementation_strategy()
        return _patch_plan()

    def fake_risks(chosen, implementation_strategy, patch_plan, constraints, context=None):
        calls.append("surface_risks")
        assert chosen == _chosen()
        assert patch_plan == _patch_plan()
        return _risks()

    monkeypatch.setattr(receive_module, "_normalize_problem", fake_normalize)
    monkeypatch.setattr(receive_module, "_extract_constraints", fake_constraints)
    monkeypatch.setattr(receive_module, "_build_prior_art_map", fake_prior_art)
    monkeypatch.setattr(receive_module, "_generate_candidates", fake_candidates)
    monkeypatch.setattr(receive_module, "_evaluate_candidates", fake_evaluations)
    monkeypatch.setattr(receive_module, "_select_approach", fake_selection)
    monkeypatch.setattr(receive_module, "_implementation_strategy", fake_implementation)
    monkeypatch.setattr(receive_module, "_right_size_patch_plan", fake_patch_plan)
    monkeypatch.setattr(receive_module, "_surface_risks", fake_risks)

    result = receive_module.form_technical_approach(_rfc_view(), tmp_path)

    assert result["ok"] is True
    assert calls == [
        "normalize_problem",
        "extract_constraints",
        "build_prior_art_map",
        "generate_candidates",
        "evaluate_candidates",
        "select_approach",
        "implementation_strategy",
        "right_size_patch_plan",
        "surface_risks",
    ]
    assert result["steps"] == {
        "normalize_problem": _normalized_problem(),
        "extract_constraints": _constraints(),
        "build_prior_art_map": _prior_art_map(),
        "generate_candidates": _candidates(),
        "evaluate_candidates": _evaluations(),
        "select_approach": _chosen(),
        "implementation_strategy": _implementation_strategy(),
        "right_size_patch_plan": _patch_plan(),
        "surface_risks": _risks(),
    }

    technical_approach = result["technical_approach"]
    assert technical_approach["source"] == "generated"
    assert technical_approach["chosen_approach"] == _chosen()
    assert technical_approach["alternatives_with_why_not"] == [
        {
            "candidate_name": "Minimal",
            "why_not": "The matrix shows weaker problem fit.",
            "candidate": _candidate("Minimal", "minimal_local"),
        }
    ]
    assert technical_approach["prior_art_rationale"] == _prior_art_map()
    assert technical_approach["trade_off_analysis"] == {
        "evaluations": _evaluations()["evaluations"],
        "accepted_tradeoffs": _chosen()["accepted_tradeoffs"],
        "decision": _chosen()["decision"],
    }
    assert technical_approach["implementation_plan"] == {
        "main_changes": _implementation_strategy()["main_changes"],
        "affected_modules": _implementation_strategy()["affected_modules"],
        "data_api_config_changes": _implementation_strategy()["data_api_config_changes"],
        "observability": _implementation_strategy()["observability"],
    }
    assert technical_approach["compat_migration"] == _implementation_strategy()["migration_compat"]
    assert technical_approach["testing_plan"] == _implementation_strategy()["testing_plan"]
    assert technical_approach["scoped_patch_plan"] == _patch_plan()
    assert technical_approach["risks_open_questions"] == _risks()


def test_form_technical_approach_uses_provided_approach_as_boundary_basis(monkeypatch, tmp_path):
    provided_approach = {
        "chosen_approach": "Requester wants the repo-native helper path.",
        "alternatives": ["Generate a fresh approach from candidates."],
    }
    calls = []

    monkeypatch.setattr(
        receive_module,
        "_normalize_problem",
        lambda rfc_view, context=None: calls.append("normalize_problem") or _normalized_problem(),
    )
    monkeypatch.setattr(
        receive_module,
        "_extract_constraints",
        lambda rfc_view, repo, context=None, normalized_problem=None: (
            calls.append(("extract_constraints", normalized_problem)) or _constraints()
        ),
    )
    monkeypatch.setattr(
        receive_module,
        "_build_prior_art_map",
        lambda rfc_view, repo, context=None, normalized_problem=None: calls.append("build_prior_art_map")
        or _prior_art_map(),
    )
    monkeypatch.setattr(
        receive_module,
        "_generate_candidates",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not full-generate")),
    )
    monkeypatch.setattr(
        receive_module,
        "_evaluate_candidates",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not evaluate generated candidates")),
    )
    monkeypatch.setattr(
        receive_module,
        "_select_approach",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not discard requester approach")),
    )

    def fake_implementation(chosen, prior_art_map, constraints, rfc_view, repo, context=None):
        calls.append("implementation_strategy")
        assert chosen["chosen"] == "Requester-provided Technical Approach"
        assert chosen["requester_approach"] == provided_approach
        return _implementation_strategy()

    def fake_patch_plan(chosen, implementation_strategy, constraints, context=None):
        calls.append("right_size_patch_plan")
        assert chosen["requester_approach"] == provided_approach
        return _patch_plan()

    def fake_risks(chosen, implementation_strategy, patch_plan, constraints, context=None):
        calls.append("surface_risks")
        assert chosen["requester_approach"] == provided_approach
        return _risks()

    monkeypatch.setattr(receive_module, "_implementation_strategy", fake_implementation)
    monkeypatch.setattr(receive_module, "_right_size_patch_plan", fake_patch_plan)
    monkeypatch.setattr(receive_module, "_surface_risks", fake_risks)

    result = receive_module.form_technical_approach(
        _rfc_view(),
        tmp_path,
        provided_approach=provided_approach,
    )

    assert result["ok"] is True
    assert calls == [
        "normalize_problem",
        ("extract_constraints", _normalized_problem()),
        "build_prior_art_map",
        "implementation_strategy",
        "right_size_patch_plan",
        "surface_risks",
    ]
    assert "generate_candidates" not in result["steps"]
    assert "evaluate_candidates" not in result["steps"]
    assert result["steps"]["provided_approach"] == provided_approach
    assert result["technical_approach"]["source"] == "requester_provided_refined"
    assert result["technical_approach"]["chosen_approach"]["requester_approach"] == provided_approach
    assert result["technical_approach"]["alternatives_with_why_not"] == [
        {
            "candidate_name": "Generate a fresh approach from candidates.",
            "why_not": "Not selected by the requester-provided Technical Approach basis.",
        }
    ]


def test_form_technical_approach_fails_closed_when_a_step_errors(monkeypatch, tmp_path):
    monkeypatch.setattr(
        receive_module,
        "_normalize_problem",
        lambda rfc_view, context=None: _normalized_problem(),
    )
    monkeypatch.setattr(
        receive_module,
        "_extract_constraints",
        lambda rfc_view, repo, context=None, normalized_problem=None: {
            "ok": False,
            "error": "constraint extraction failed",
        },
    )
    monkeypatch.setattr(
        receive_module,
        "_build_prior_art_map",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should stop on first failed step")),
    )

    result = receive_module.form_technical_approach(_rfc_view(), tmp_path)

    assert result == {
        "ok": False,
        "error": "constraint extraction failed",
        "failed_step": "extract_constraints",
    }


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


def _success_criterion(
    *,
    action: str = "Inspect generated candidate approaches.",
    preconditions: list[str] | None = None,
    expected_state: str = "The candidate list contains distinct approach objects for later evaluation.",
    evidence: str = "The candidates array has separately named minimal_local and repo_native entries.",
    method: str = "automated_test",
    check: str = "Assert candidates include distinct names, kinds, summaries, decisions, and prior-art links.",
) -> dict[str, object]:
    return {
        "actor": "an RFC reviewer",
        "capability": {
            "action": action,
            "preconditions": preconditions or ["The normalized problem has been produced."],
        },
        "verifiable_outcome": {
            "expected_state": expected_state,
            "evidence": evidence,
        },
        "verification": {
            "method": method,
            "check": check,
        },
    }


def _normalized_problem() -> dict[str, object]:
    return {
        "problem": "RFC formation needs candidate approaches.",
        "affected": "RFC reviewers and implementation agents.",
        "current_inadequacy": "Prior-art evidence exists but no approach options are generated.",
        "success_criteria": [_success_criterion()],
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


def _risks() -> dict[str, object]:
    return {
        "assumptions": ["The existing RFC receive helper pattern remains the intended extension point."],
        "risks": [{"risk": "Prompt ambiguity.", "mitigation": "Constrain the step boundary."}],
        "open_questions": ["Should empty risk lists be allowed for trivial RFCs?"],
        "spikes": ["Mock parser path."],
        "reviewer_questions": ["Are these the right reviewer questions?"],
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
