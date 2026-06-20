#!/usr/bin/env python3
"""Schema-output gate for producing / verifying carriers (token-reduction lever).

The 6 read-only carriers (designers, genius, aufheben, linon, stefan) write no files — their
deliverable is schema-valid JSON (design-proposal, genius-packet, implementation-contract,
linon-review, aesthetic-review). carrier_harness's scope/diff gates do nothing for them; what they
need is OUTPUT validation, which validates DETERMINISTICALLY in Python:

  * zero LLM tokens for the validation itself, and
  * fail-closed EARLY — a malformed proposal is rejected before the LLM controller reads it AND
    before it can trigger a wasted downstream carrier (an implementer run on a broken contract is
    thousands of tokens spent on garbage).

Dependency-free: a minimal draft-07 subset validator (type / required / enum / properties /
additionalProperties / items / minimum / maximum / minLength). Enough to fail-closed on malformed
carrier output; not a full JSON Schema implementation.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

_TYPES = {
    "object": dict, "array": list, "string": str, "boolean": bool,
    "integer": int, "number": (int, float), "null": type(None),
}


def _type_ok(value, t: str) -> bool:
    if t == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if t == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if t == "boolean":
        return isinstance(value, bool)
    py = _TYPES.get(t)
    return isinstance(value, py) if py else True


def validate(instance, schema, path: str = "$") -> list[str]:
    """Return a list of error strings; empty = valid (subset of draft-07, fail-closed)."""
    errors: list[str] = []
    if not isinstance(schema, dict):
        return errors

    t = schema.get("type")
    if t:
        types = t if isinstance(t, list) else [t]
        if not any(_type_ok(instance, tt) for tt in types):
            errors.append(f"{path}: expected type {t}, got {type(instance).__name__}")
            return errors  # type wrong → don't cascade

    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path}: {instance!r} not in enum {schema['enum']}")
    if "const" in schema and instance != schema["const"]:
        errors.append(f"{path}: {instance!r} != const {schema['const']!r}")

    if isinstance(instance, dict):
        for req in schema.get("required", []):
            if req not in instance:
                errors.append(f"{path}: missing required '{req}'")
        props = schema.get("properties", {})
        addl = schema.get("additionalProperties", True)
        extra = set(instance) - set(props)
        if addl is False and extra:
            errors.append(f"{path}: additionalProperties not allowed: {sorted(extra)}")
        for k, v in instance.items():
            if k in props:
                errors += validate(v, props[k], f"{path}.{k}")
            elif isinstance(addl, dict):  # additionalProperties as a subschema
                errors += validate(v, addl, f"{path}.{k}")
    elif isinstance(instance, list):
        items = schema.get("items")
        if isinstance(items, dict):
            for i, item in enumerate(instance):
                errors += validate(item, items, f"{path}[{i}]")
        if "minItems" in schema and len(instance) < schema["minItems"]:
            errors.append(f"{path}: fewer than minItems {schema['minItems']}")
    elif isinstance(instance, str):
        if "minLength" in schema and len(instance) < schema["minLength"]:
            errors.append(f"{path}: shorter than minLength {schema['minLength']}")
        if "pattern" in schema and not re.search(schema["pattern"], instance):
            errors.append(f"{path}: does not match pattern {schema['pattern']}")
    elif isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append(f"{path}: below minimum {schema['minimum']}")
        if "maximum" in schema and instance > schema["maximum"]:
            errors.append(f"{path}: above maximum {schema['maximum']}")
    return errors


# Keywords the minimal validator does NOT implement. If a schema uses any of them, the minimal
# validator could pass invalid data (e.g. linon-review uses allOf/if/then for conditional required),
# so we FAIL CLOSED unless the full `jsonschema` library is available (NN4 — never silently ignore).
_UNSUPPORTED = {"allOf", "anyOf", "oneOf", "not", "if", "then", "else", "$ref",
                "patternProperties", "dependencies", "dependentSchemas", "dependentRequired",
                "propertyNames", "contains"}


def _unsupported_keywords(schema) -> set[str]:
    found = set()
    if isinstance(schema, dict):
        found |= _UNSUPPORTED & set(schema)
        for v in schema.values():
            found |= _unsupported_keywords(v)
    elif isinstance(schema, list):
        for v in schema:
            found |= _unsupported_keywords(v)
    return found


def gate_output(output_text: str, schema_path) -> dict:
    """Validate a carrier's JSON output text against a schema file. Fail-closed: unparseable JSON,
    any validation error, or a schema the minimal validator can't fully check → output_ok False."""
    schema_path = Path(schema_path)
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"output_ok": False, "errors": [f"schema unreadable: {exc}"]}
    try:
        instance = json.loads(output_text)
    except json.JSONDecodeError as exc:
        return {"output_ok": False, "errors": [f"output is not valid JSON: {exc}"]}

    # Prefer the full validator; fall back to the minimal one, fail-closed on unsupported constructs.
    try:
        import jsonschema  # type: ignore
        # Select the validator from the schema's own $schema (ADR-0009 P0): the implementation contract
        # declares Draft 2020-12 (if/then, $defs), so a fixed Draft7Validator would mis-validate it.
        validator_cls = jsonschema.validators.validator_for(schema)
        validator_cls.check_schema(schema)
        errs = [f"{list(e.absolute_path) or '$'}: {e.message}"
                for e in validator_cls(schema).iter_errors(instance)]
        return {"output_ok": not errs, "errors": errs[:20], "validator": validator_cls.__name__}
    except ImportError:
        unsupported = _unsupported_keywords(schema)
        if unsupported:
            return {"output_ok": False, "validator": "minimal",
                    "errors": [f"schema uses {sorted(unsupported)}; minimal validator cannot verify "
                               f"it — install jsonschema for fail-closed conditional schemas"]}
        errors = validate(instance, schema)
        return {"output_ok": not errors, "errors": errors[:20], "validator": "minimal"}


def self_test() -> int:
    schema = {"type": "object", "required": ["a", "d"], "additionalProperties": False,
              "properties": {"a": {"type": "string"}, "d": {"enum": ["x", "y"]},
                             "n": {"type": "integer", "minimum": 0}}}
    fails = []
    if validate({"a": "hi", "d": "x"}, schema):
        fails.append("valid object should pass")
    if not validate({"a": "hi"}, schema):
        fails.append("missing required must fail")
    if not validate({"a": 1, "d": "x"}, schema):
        fails.append("wrong type must fail")
    if not validate({"a": "hi", "d": "z"}, schema):
        fails.append("bad enum must fail")
    if not validate({"a": "hi", "d": "x", "extra": 1}, schema):
        fails.append("additionalProperties must fail")
    if not validate({"a": "hi", "d": "x", "n": -1}, schema):
        fails.append("minimum must fail")
    if gate_output("not json", "/nonexistent")["output_ok"]:
        fails.append("unparseable output must fail closed")
    if fails:
        import sys
        for f in fails:
            print("FAIL " + f, file=sys.stderr)
        return 1
    print("controller_output self-test passed (required/type/enum/additionalProperties/minimum, fail-closed).")
    return 0


if __name__ == "__main__":
    import argparse
    import sys
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--self-test", action="store_true")
    p.add_argument("--output"); p.add_argument("--schema")
    a = p.parse_args()
    if a.self_test:
        raise SystemExit(self_test())
    if a.output and a.schema:
        res = gate_output(Path(a.output).read_text(encoding="utf-8"), a.schema)
        print(json.dumps(res, indent=2, ensure_ascii=False))
        raise SystemExit(0 if res["output_ok"] else 1)
    p.print_help()
    raise SystemExit(2)
