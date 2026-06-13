#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from typing import Any
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "packages" / "codex-org-bootstrap" / "src"
sys.path.insert(0, str(PKG))

from ai_org_bootstrap.pack import find_repo_root
from ai_org_bootstrap.registry import load_runtime_registry
from ai_org_bootstrap.scripts.validate_pack import validate as validate_pack

ROOT = Path(__file__).resolve().parents[1]


def _schema_by_agent() -> dict[str, str]:
    return {entry.agent_id: entry.schema for entry in load_runtime_registry(ROOT / "registry/runtime-registry.yaml")}


SCHEMA_BY_AGENT = _schema_by_agent()


def validate_schema_instance(schema: dict[str, Any], instance: object) -> list[str]:
    errors: list[str] = []
    _validate_value(schema, instance, "$", errors)
    return errors


def check_role_conditionals(schema_path: Path, instance: object) -> list[str]:
    if schema_path.name != "linon-review.schema.json" or not isinstance(instance, dict):
        return []

    findings = instance.get("findings")
    if not isinstance(findings, list):
        return []

    errors: list[str] = []
    for index, finding in enumerate(findings):
        if not isinstance(finding, dict):
            continue

        line_range = finding.get("line_range")
        if isinstance(line_range, dict):
            start = line_range.get("start")
            end = line_range.get("end")
            if (
                isinstance(start, int)
                and not isinstance(start, bool)
                and isinstance(end, int)
                and not isinstance(end, bool)
                and end < start
            ):
                errors.append(f"$.findings[{index}].line_range.end < start")

        if finding.get("severity") == "critical":
            for key in ("principle_id", "defect_locus"):
                if key not in finding:
                    errors.append(f"$.findings[{index}].{key}: missing required field for critical severity")

    return errors


def validate_linon_review_fixtures(root: Path) -> list[str]:
    fixture_dir = root / "fixtures" / "linon-review"
    schema_path = root / "schemas" / "linon-review.schema.json"
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return [f"{schema_path.relative_to(root)}: schema_parse_error: {exc}"]
    if not isinstance(schema, dict):
        return [f"{schema_path.relative_to(root)}: schema must be object"]

    expectations = {
        "valid-minimal.json": True,
        "invalid-critical-missing-principle.json": False,
        "invalid-line-range.json": False,
        "invalid-missing-evidence.json": False,
    }
    errors: list[str] = []
    for name, should_accept in expectations.items():
        fixture_path = fixture_dir / name
        try:
            instance = json.loads(fixture_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{fixture_path.relative_to(root)}: fixture_parse_error: {exc}")
            continue

        fixture_errors = validate_schema_instance(schema, instance)
        fixture_errors.extend(check_role_conditionals(schema_path, instance))
        rel = fixture_path.relative_to(root)
        if should_accept and fixture_errors:
            errors.append(f"{rel}: expected ACCEPTED, got REJECTED: {fixture_errors}")
        if not should_accept and not fixture_errors:
            errors.append(f"{rel}: expected REJECTED, got ACCEPTED")

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


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=None)
    args = parser.parse_args(argv)
    root = find_repo_root(args.root)

    errors = validate_pack(root)
    errors.extend(validate_linon_review_fixtures(root))
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print("Codex-only AI Org Bootstrap pack validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
