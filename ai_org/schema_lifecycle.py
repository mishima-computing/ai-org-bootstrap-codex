"""Deterministic builders for Codex output schemas.

Memento: schemas are projections: generated per use from neutral primitives,
discarded after use; caching is an optimization, not a lifecycle; module-level
schema ledgers hid the game-vocabulary residue -- do not reintroduce.
"""
from __future__ import annotations

from collections.abc import Callable, Iterator
from copy import deepcopy
import importlib
import pkgutil
from typing import Any


SchemaBuilder = Callable[[], dict[str, Any]]


def string_schema(**extra: Any) -> dict[str, Any]:
    return {"type": "string", **extra}


def boolean_schema(**extra: Any) -> dict[str, Any]:
    return {"type": "boolean", **extra}


def integer_schema(**extra: Any) -> dict[str, Any]:
    return {"type": "integer", **extra}


def array_schema(items: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {"type": "array", "items": items, **extra}


def enum_schema(values: list[str] | tuple[str, ...], **extra: Any) -> dict[str, Any]:
    return {"enum": list(values), **extra}


def object_schema(
    required: list[str] | tuple[str, ...],
    properties: dict[str, Any],
    *,
    schema_uri: str | None = None,
    additional_properties: bool = False,
    **extra: Any,
) -> dict[str, Any]:
    schema: dict[str, Any] = {}
    if schema_uri is not None:
        schema["$schema"] = schema_uri
    schema.update(
        {
            "type": "object",
            "additionalProperties": additional_properties,
            "required": list(required),
            "properties": properties,
        }
    )
    schema.update(extra)
    return schema


def draft_object_schema(required: list[str] | tuple[str, ...], properties: dict[str, Any]) -> dict[str, Any]:
    return object_schema(required, properties, schema_uri="https://json-schema.org/draft/2020-12/schema")


def fresh_schema(schema: dict[str, Any]) -> dict[str, Any]:
    return deepcopy(schema)


def generated_schema_attr(name: str, builders: dict[str, SchemaBuilder]) -> Any:
    try:
        return builders[name]()
    except KeyError as exc:
        raise AttributeError(name) from exc


def iter_schema_builders(package: Any) -> Iterator[tuple[str, SchemaBuilder]]:
    """Yield all builders exposed through `_CODEX_OUTPUT_SCHEMA_BUILDERS`."""
    seen: set[tuple[str, str]] = set()
    for module_info in pkgutil.walk_packages(package.__path__, package.__name__ + "."):
        try:
            module = importlib.import_module(module_info.name)
        except Exception:  # noqa: BLE001 - a broken optional module must not hide schemas
            continue
        builders = getattr(module, "_CODEX_OUTPUT_SCHEMA_BUILDERS", {})
        if not isinstance(builders, dict):
            continue
        for name, builder in builders.items():
            key = (module_info.name, name)
            if key in seen:
                continue
            seen.add(key)
            yield f"{module_info.name}.{name}", builder
