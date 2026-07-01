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
