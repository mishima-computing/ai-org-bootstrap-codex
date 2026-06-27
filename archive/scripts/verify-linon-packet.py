#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import fnmatch
import importlib.util
import io
import json
import re
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
HASH_ARTIFACTS_PATH = ROOT / "scripts" / "hash-artifacts.py"
SCHEMA_PATH = ROOT / "schemas" / "linon-packet.schema.json"
FIXTURE_DIR = ROOT / "fixtures" / "linon-review" / "packet"

_HASH_SPEC = importlib.util.spec_from_file_location("hash_artifacts", HASH_ARTIFACTS_PATH)
if _HASH_SPEC is None or _HASH_SPEC.loader is None:
    raise RuntimeError(f"cannot import {HASH_ARTIFACTS_PATH}")
hash_artifacts = importlib.util.module_from_spec(_HASH_SPEC)
_HASH_SPEC.loader.exec_module(hash_artifacts)


def _repo_path(raw: str, root: Path) -> Path:
    path = Path(raw)
    if path.is_absolute():
        return path
    return root / path


def _load_json(path: Path) -> tuple[object | None, list[str]]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), []
    except Exception as exc:  # noqa: BLE001
        return None, [f"{path}: JSON parse error: {exc}"]


def validate_schema_instance(schema: dict[str, Any], instance: object) -> list[str]:
    errors: list[str] = []
    _validate_value(schema, instance, "$", errors)
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
            errors.append(f"{path}: at least {min_items} items required")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                _validate_value(item_schema, item, f"{path}[{index}]", errors)
        return

    if expected_type == "string":
        if not isinstance(value, str):
            errors.append(f"{path}: expected string")
            return
        min_length = schema.get("minLength")
        if isinstance(min_length, int) and len(value) < min_length:
            errors.append(f"{path}: must be non-empty")
        pattern = schema.get("pattern")
        if isinstance(pattern, str) and not re.fullmatch(pattern, value):
            errors.append(f"{path}: value does not match pattern {pattern!r}")
        const = schema.get("const")
        if isinstance(const, str) and value != const:
            errors.append(f"{path}: value must be {const!r}")


def _artifact_hash_errors(packet: dict[str, Any], root: Path) -> list[str]:
    errors: list[str] = []
    diff = packet["diff_artifact"]
    contract = packet["implementation_contract"]
    pair = packet["sha256_pair"]
    checks = (
        ("diff_artifact.sha256", _repo_path(diff["path"], root), diff["sha256"]),
        ("sha256_pair.diff_sha256", _repo_path(diff["path"], root), pair["diff_sha256"]),
        ("implementation_contract.sha256", _repo_path(contract["path"], root), contract["sha256"]),
        ("sha256_pair.contract_sha256", _repo_path(contract["path"], root), pair["contract_sha256"]),
    )
    for label, path, recorded in checks:
        try:
            actual = hash_artifacts.sha256(path)
        except OSError as exc:
            errors.append(f"{label}: cannot read {path}: {exc}")
            continue
        if actual != recorded:
            errors.append(f"{label}: sha256 mismatch for {path}: recorded {recorded}, actual {actual}")
    return errors


def _embedded_contract_errors(packet: dict[str, Any], root: Path) -> list[str]:
    contract = packet["implementation_contract"]
    contract_path = _repo_path(contract["path"], root)
    try:
        composed = contract_path.read_bytes()
    except OSError as exc:
        return [f"implementation_contract.path: cannot read {contract_path}: {exc}"]
    embedded = contract["embedded_contract"]
    embedded_bytes = json.dumps(embedded, indent=2, ensure_ascii=False).encode("utf-8") + b"\n"
    with tempfile.NamedTemporaryFile() as source:
        source.write(embedded_bytes)
        source.flush()
        with contextlib.redirect_stdout(io.StringIO()):
            embed_exit = hash_artifacts.verify_embed(
                ["--verify-embed", "--composed", str(contract_path), "--source", source.name]
            )
    if embed_exit != 0:
        return [f"implementation_contract.embedded_contract: bytes do not match {contract_path}"]
    return []


def _touched_paths(diff_text: str) -> list[str]:
    touched: list[str] = []
    for line in diff_text.splitlines():
        if line.startswith("+++ b/") or line.startswith("--- a/"):
            path = line[6:]
        elif line.startswith("+++ ") or line.startswith("--- "):
            path = line[4:]
        else:
            continue
        if path == "/dev/null":
            continue
        if path.startswith(("a/", "b/")):
            path = path[2:]
        if path and path not in touched:
            touched.append(path)
    return touched


def _scope_errors(packet: dict[str, Any], root: Path) -> list[str]:
    diff_path = _repo_path(packet["diff_artifact"]["path"], root)
    try:
        diff_text = diff_path.read_text(encoding="utf-8")
    except OSError as exc:
        return [f"diff_artifact.path: cannot read {diff_path}: {exc}"]
    allowed = packet["implementation_contract"]["embedded_contract"]["files_allowed_to_change"]
    out_of_scope = [
        path
        for path in _touched_paths(diff_text)
        if not any(fnmatch.fnmatchcase(path, pattern) for pattern in allowed)
    ]
    return [f"diff_artifact.path: out-of-scope path {path!r} not allowed by embedded contract" for path in out_of_scope]


def verify_packet(packet_path: Path, root: Path = ROOT) -> list[str]:
    schema_obj, schema_errors = _load_json(SCHEMA_PATH)
    if schema_errors:
        return schema_errors
    packet_obj, packet_errors = _load_json(packet_path)
    if packet_errors:
        return packet_errors
    if not isinstance(schema_obj, dict):
        return [f"{SCHEMA_PATH}: schema must be object"]

    errors = validate_schema_instance(schema_obj, packet_obj)
    if errors:
        return errors
    if not isinstance(packet_obj, dict):
        return ["$: expected object"]

    status = packet_obj["implementation_contract"].get("ratification_status")
    if not isinstance(status, str) or not status.strip():
        errors.append("$.implementation_contract.ratification_status: missing or empty")
    errors.extend(_artifact_hash_errors(packet_obj, root))
    errors.extend(_embedded_contract_errors(packet_obj, root))
    errors.extend(_scope_errors(packet_obj, root))
    return errors


def run_self_test(root: Path = ROOT) -> list[str]:
    expectations = {
        "example-packet.json": True,
        "forged-packet.json": False,
        "scope-violation-packet.json": False,
    }
    errors: list[str] = []
    for name, should_pass in expectations.items():
        fixture_errors = verify_packet(FIXTURE_DIR / name, root)
        if should_pass and fixture_errors:
            errors.append(f"{name}: expected ACCEPTED, got REJECTED: {fixture_errors}")
        if not should_pass and not fixture_errors:
            errors.append(f"{name}: expected REJECTED, got ACCEPTED")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify a Linon packet before review execution.")
    parser.add_argument("packet", nargs="?")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)

    if args.self_test:
        errors = run_self_test()
    elif args.packet:
        errors = verify_packet(_repo_path(args.packet, ROOT))
    else:
        parser.error("packet path or --self-test is required")

    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print("Linon packet verification passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
