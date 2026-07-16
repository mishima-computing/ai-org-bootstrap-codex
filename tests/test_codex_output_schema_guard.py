"""Structural guard against codex --output-schema incompatibilities.

Codex `exec --output-schema` forwards the schema to OpenAI Structured Outputs, which
accepts only a narrow JSON Schema subset. Two classes of violation have shipped and
silently broken grounding at runtime while monkeypatched unit tests stayed green:

  1. Forbidden keywords (allOf/anyOf/oneOf/not/if/then/else/const/minLength/maxLength/
     pattern/format) -> HTTP 400 "'<key>' is not permitted".
  2. A non-string `description` (the field registry once embedded a dict of
     role/belongs/must_not/owner/required_at) -> HTTP 400
     "{...} is not of type 'string'".

This test discovers EVERY codex output schema builder across ai_org and asserts
neither violation exists at any depth, so this bug class cannot silently return. It
does not shell out to codex (that is covered by an opt-in smoke); it enforces the
subset the real codex proved it requires.
"""
from __future__ import annotations

import importlib
import json
from pathlib import Path
import pkgutil
from typing import Any, Iterator

import ai_org
from ai_org.schema_lifecycle import iter_schema_builders

# Keywords codex/OpenAI Structured Outputs reject in an --output-schema.
FORBIDDEN_KEYWORDS = frozenset(
    {
        "allOf",
        "anyOf",
        "oneOf",
        "not",
        "if",
        "then",
        "else",
        "const",
        "minLength",
        "maxLength",
        "pattern",
        "format",
    }
)


def _iter_output_schemas() -> Iterator[tuple[str, dict[str, Any]]]:
    """Yield (qualified_name, schema) for every codex output schema builder."""
    for name, builder in iter_schema_builders(ai_org):
        yield name, builder()


def _iter_legacy_module_schema_globals() -> Iterator[str]:
    """Yield old-style module globals that would escape the builder lifecycle."""
    seen: set[int] = set()
    for module_info in pkgutil.walk_packages(ai_org.__path__, ai_org.__name__ + "."):
        try:
            module = importlib.import_module(module_info.name)
        except Exception:  # noqa: BLE001 - a broken optional module must not hide schemas
            continue
        for name, value in vars(module).items():
            if not isinstance(value, dict):
                continue
            if not (name.endswith("SCHEMA") or name.endswith("VERDICT")):
                continue
            if "type" not in value:
                continue
            if id(value) in seen:
                continue
            seen.add(id(value))
            yield f"{module_info.name}.{name}"


def _violations(value: Any, path: str) -> list[str]:
    problems: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if key in FORBIDDEN_KEYWORDS:
                problems.append(f"forbidden keyword at {child_path}")
            if key == "description" and not isinstance(child, str):
                problems.append(
                    f"non-string description at {child_path} (type {type(child).__name__})"
                )
            problems.extend(_violations(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            problems.extend(_violations(child, f"{path}[{index}]"))
    return problems


def test_discovers_the_known_codex_output_schemas():
    # Guard the guard: if discovery silently finds nothing, the assertions below
    # would vacuously pass. Anchor on known builder outputs.
    names = {name for name, _ in _iter_output_schemas()}
    assert "ai_org.patchwork_queue.receive.GROUNDING_SCHEMA" in names
    assert "ai_org.patchwork_queue.receive.RIGHT_SIZE_PATCH_PLAN_SCHEMA" in names
    assert "ai_org.patchwork_queue.review.OBJECTION_SCHEMA" in names
    assert len(names) >= 18


def test_no_module_level_codex_output_schema_ledgers_remain():
    assert list(_iter_legacy_module_schema_globals()) == []


def test_codex_output_schema_builders_match_pre_refactor_snapshot():
    snapshot_path = Path(__file__).parent / "fixtures" / "codex_output_schema_snapshot.json"
    expected = json.loads(snapshot_path.read_text(encoding="utf-8"))
    actual = {name: schema for name, schema in _iter_output_schemas()}
    # These schemas are the confirmed splitter redesign surface. The lineage
    # carrier is no longer Codex output at all, and patch-plan authoring now
    # intentionally differs from the pre-refactor positional snapshot.
    changed = {
        "ai_org.patchwork_queue.patch_series_gate.LINEAGE_SPLIT_SCHEMA",
        "ai_org.patchwork_queue.receive.RIGHT_SIZE_PATCH_PLAN_SCHEMA",
    }
    actual = {name: schema for name, schema in actual.items() if name not in changed}
    expected = {name: schema for name, schema in expected.items() if name not in changed}
    assert actual == expected


def test_no_codex_output_schema_contains_a_forbidden_construct():
    failures: dict[str, list[str]] = {}
    for name, schema in _iter_output_schemas():
        problems = _violations(schema, name)
        if problems:
            failures[name] = problems
    assert not failures, "codex --output-schema violations found:\n" + "\n".join(
        f"  {name}: {', '.join(problems)}" for name, problems in failures.items()
    )


def test_canonical_root_preview_schema_contains_no_work_or_worker_taxonomy():
    schema = dict(_iter_output_schemas())["ai_org.patchwork_queue.receive.CANONICAL_ROOT_PREVIEW_SCHEMA"]
    forbidden = {
        "work_type",
        "worker_role",
        "producer_kind",
        "child_kind",
        "node_kind",
    }

    def keys(value: Any) -> set[str]:
        if isinstance(value, dict):
            return set(value) | set().union(*(keys(child) for child in value.values()))
        if isinstance(value, list):
            return set().union(*(keys(child) for child in value))
        return set()

    assert keys(schema).isdisjoint(forbidden)
