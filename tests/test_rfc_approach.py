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
