"""Structural guard against codex --output-schema incompatibilities.

Codex `exec --output-schema` forwards the schema to OpenAI Structured Outputs, which
accepts only a narrow JSON Schema subset. Two classes of violation have shipped and
silently broken grounding at runtime while monkeypatched unit tests stayed green:

  1. Forbidden keywords (allOf/anyOf/oneOf/not/if/then/else/const/minLength/maxLength/
     pattern/format) -> HTTP 400 "'<key>' is not permitted".
  2. A non-string `description` (the field registry once embedded a dict of
     role/belongs/must_not/owner/required_at) -> HTTP 400
     "{...} is not of type 'string'".

This test discovers EVERY module-level codex output schema across ai_org and asserts
neither violation exists at any depth, so this bug class cannot silently return. It
does not shell out to codex (that is covered by an opt-in smoke); it enforces the
subset the real codex proved it requires.
"""
from __future__ import annotations

import importlib
import pkgutil
from typing import Any, Iterator

import ai_org

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
    """Yield (qualified_name, schema) for every module-level codex output schema.

    A codex output schema is a module-level dict named *SCHEMA or *VERDICT that is a
    real JSON Schema (has a top-level "type"). Documentation dicts such as
    REQUEST_SCHEMA (no "type"; passed inside a prompt, not via --output-schema) are
    correctly excluded.
    """
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
            yield f"{module_info.name}.{name}", value


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
    # Guard the guard: if discovery silently finds nothing (e.g. a refactor renames the
    # schemas), the assertions below would vacuously pass. Anchor on known schemas.
    names = {name for name, _ in _iter_output_schemas()}
    assert "ai_org.rfc.receive.GROUNDING_SCHEMA" in names
    assert "ai_org.rfc.decompose.SPLIT_SCHEMA" in names
    assert "ai_org.rfc.review.OBJECTION_SCHEMA" in names
    assert len(names) >= 20


def test_no_codex_output_schema_contains_a_forbidden_construct():
    failures: dict[str, list[str]] = {}
    for name, schema in _iter_output_schemas():
        problems = _violations(schema, name)
        if problems:
            failures[name] = problems
    assert not failures, "codex --output-schema violations found:\n" + "\n".join(
        f"  {name}: {', '.join(problems)}" for name, problems in failures.items()
    )
