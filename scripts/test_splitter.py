#!/usr/bin/env python3
"""Direct tests for the Splitter child-DAG adapter."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from splitter import HOUSE_RULES, split  # noqa: E402


def test_valid_dag_from_stub_carrier_is_returned():
    expected = [
        {
            "id": "design",
            "objective": "Design the API",
            "scope": ["src/splitter.py"],
            "depends_on": [],
        },
        {
            "id": "test",
            "objective": "Test the API",
            "scope": ["src/test_splitter.py"],
            "depends_on": ["design"],
        },
    ]

    def carrier(_prompt):
        return json.dumps(expected)

    assert split("Create splitter", "Existing Frontier validates DAGs.", carrier=carrier) == expected


def test_malformed_carrier_output_returns_empty_list():
    def carrier(_prompt):
        return "{not json"

    assert split("Create splitter", "Existing Frontier validates DAGs.", carrier=carrier) == []


def test_invalid_task_shapes_and_frontier_errors_return_empty_list():
    valid_task = {
        "id": "valid",
        "objective": "Valid task",
        "scope": ["src/splitter.py"],
        "depends_on": [],
    }
    invalid_outputs = [
        {},
        ["not a task"],
        [{**valid_task, "id": 1}],
        [{**valid_task, "scope": "src/splitter.py"}],
        [{**valid_task, "scope": [1]}],
        [{"id": "missing", "objective": "Missing fields", "scope": []}],
        [{**valid_task, "status": "pending"}],
        [{**valid_task, "depends_on": ["missing"]}],
    ]

    for output in invalid_outputs:
        def carrier(_prompt, output=output):
            return json.dumps(output)

        assert split("Create splitter", "Existing Frontier validates DAGs.", carrier=carrier) == []

    def raising_carrier(_prompt):
        raise RuntimeError("carrier failed")

    assert split("Create splitter", "Existing Frontier validates DAGs.", carrier=raising_carrier) == []


def test_prompt_contains_goal_context_and_house_rules():
    prompts = []

    def carrier(prompt):
        prompts.append(prompt)
        return "[]"

    assert split("Split this goal", "Use this codebase context.", carrier=carrier) == []
    assert len(prompts) == 1
    assert "Split this goal" in prompts[0]
    assert "Use this codebase context." in prompts[0]
    assert HOUSE_RULES in prompts[0]


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for test in tests:
        test()
    print(f"{len(tests)} passed")


if __name__ == "__main__":
    main()
