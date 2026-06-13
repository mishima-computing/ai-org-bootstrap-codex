#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from typing import Any
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "packages" / "codex-org-bootstrap" / "src"
sys.path.insert(0, str(PKG))

from ai_org_bootstrap.registry import load_runtime_registry
from ai_org_bootstrap.scripts.validate_pack import main

ROOT = Path(__file__).resolve().parents[1]


def _schema_by_agent() -> dict[str, str]:
    return {entry.agent_id: entry.schema for entry in load_runtime_registry(ROOT / "registry/runtime-registry.yaml")}


SCHEMA_BY_AGENT = _schema_by_agent()


def validate_schema_instance(schema: dict[str, Any], instance: object) -> list[str]:
    errors: list[str] = []
    _validate_value(schema, instance, "$", errors)
    return errors


def check_role_conditionals(schema_path: Path, instance: object) -> list[str]:
    errors: list[str] = []
    if schema_path.name == "implementation-contract.schema.json" and isinstance(instance, dict):
        acceptance_criteria = instance.get("acceptance_criteria")
        profile_applications = instance.get("profile_applications")
        if isinstance(acceptance_criteria, list) and isinstance(profile_applications, list):
            for app_index, application in enumerate(profile_applications):
                if not isinstance(application, dict):
                    continue
                refs = application.get("acceptance_criteria_refs")
                if not isinstance(refs, list):
                    continue
                for ref_index, ref in enumerate(refs):
                    if isinstance(ref, int) and not isinstance(ref, bool):
                        if ref < 0 or ref >= len(acceptance_criteria):
                            errors.append(
                                f"$.profile_applications[{app_index}].acceptance_criteria_refs[{ref_index}]: "
                                f"criterion index {ref} out of range for acceptance_criteria length {len(acceptance_criteria)}"
                            )
    return errors


def _validate_value(schema: dict[str, Any], value: object, path: str, errors: list[str]) -> None:
    expected_type = schema.get("type")
    if expected_type == "object":
        if not isinstance(value, dict):
            errors.append(f"{path}: expected object")
            return
        properties = schema.get("properties", {})
        if not isinstance(properties, dict):
            properties = {}
        required = schema.get("required", [])
        if isinstance(required, list):
            for key in required:
                if isinstance(key, str) and key not in value:
                    errors.append(f"{path}.{key}: missing required field")
        if schema.get("additionalProperties") is False:
            for key in value:
                if key not in properties:
                    errors.append(f"{path}.{key}: additional property is not allowed")
        for key, child_schema in properties.items():
            if key in value and isinstance(child_schema, dict):
                _validate_value(child_schema, value[key], f"{path}.{key}", errors)
        return

    if expected_type == "array":
        if not isinstance(value, list):
            errors.append(f"{path}: expected array")
            return
        min_items = schema.get("minItems")
        if isinstance(min_items, int) and len(value) < min_items:
            errors.append(f"{path}: at least {min_items} items; actual {len(value)} required {min_items}")
        max_items = schema.get("maxItems")
        if isinstance(max_items, int) and len(value) > max_items:
            errors.append(f"{path}: at most {max_items} items; actual {len(value)} allowed {max_items}")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                _validate_value(item_schema, item, f"{path}[{index}]", errors)
        return

    if expected_type == "string":
        if not isinstance(value, str):
            errors.append(f"{path}: expected string")
            return
        max_length = schema.get("maxLength")
        if isinstance(max_length, int) and len(value) > max_length:
            errors.append(f"{path}: at most {max_length} characters; actual {len(value)} allowed {max_length}")
        enum = schema.get("enum")
        if isinstance(enum, list) and value not in enum:
            errors.append(f"{path}: value must be one of {enum}")
        const = schema.get("const")
        if isinstance(const, str) and value != const:
            errors.append(f"{path}: value must be {const!r}")
        return

    if expected_type in {"number", "integer"}:
        if expected_type == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
            errors.append(f"{path}: expected integer")
            return
        if expected_type == "number" and (not isinstance(value, (int, float)) or isinstance(value, bool)):
            errors.append(f"{path}: expected number")
            return
        return

    if expected_type == "boolean" and not isinstance(value, bool):
        errors.append(f"{path}: expected boolean")


if __name__ == "__main__":
    raise SystemExit(main())
