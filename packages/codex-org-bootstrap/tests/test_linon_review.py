from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
VALIDATOR_PATH = ROOT / "scripts" / "validate-bootstrap-pack.py"
SCHEMA_PATH = ROOT / "schemas" / "linon-review.schema.json"
FIXTURE_DIR = ROOT / "fixtures" / "linon-review"

_SPEC = importlib.util.spec_from_file_location("validate_bootstrap_pack", VALIDATOR_PATH)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"cannot import validator from {VALIDATOR_PATH}")
_VALIDATOR = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_VALIDATOR)


def load_fixture(name: str) -> object:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def validation_errors(name: str) -> list[str]:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    instance = load_fixture(name)
    errors = _VALIDATOR.validate_schema_instance(schema, instance)
    errors.extend(_VALIDATOR.check_role_conditionals(SCHEMA_PATH, instance))
    return errors


class LinonReviewTests(unittest.TestCase):
    def test_valid_minimal_has_no_conditional_errors(self) -> None:
        errors = _VALIDATOR.check_role_conditionals(SCHEMA_PATH, load_fixture("valid-minimal.json"))
        self.assertEqual(errors, [])

    def test_invalid_line_range_is_rejected(self) -> None:
        errors = _VALIDATOR.check_role_conditionals(SCHEMA_PATH, load_fixture("invalid-line-range.json"))
        self.assertTrue(any("$.findings[0].line_range.end < start" in error for error in errors))

    def test_invalid_critical_missing_principle_is_rejected(self) -> None:
        errors = _VALIDATOR.check_role_conditionals(
            SCHEMA_PATH,
            load_fixture("invalid-critical-missing-principle.json"),
        )
        self.assertTrue(any("$.findings[0].principle_id" in error for error in errors))

    def test_linon_review_fixture_expectations(self) -> None:
        self.assertEqual(validation_errors("valid-minimal.json"), [])
        for name in (
            "invalid-critical-missing-principle.json",
            "invalid-line-range.json",
            "invalid-missing-evidence.json",
        ):
            with self.subTest(name=name):
                self.assertNotEqual(validation_errors(name), [])


if __name__ == "__main__":
    unittest.main()
