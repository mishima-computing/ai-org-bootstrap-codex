"""controller_run dogfood entrypoint: routes a carrier to the gates its role CLASS needs."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "packages" / "codex-org-bootstrap" / "src"))

import controller_run as cr  # noqa: E402
import controller_workflow as workflow  # noqa: E402


class ControllerRunRoutingTests(unittest.TestCase):
    def setUp(self):
        self._orig = workflow.run_contract
        self.captured = {}

        class _Rep:
            ok = True
            def to_dict(self):  # noqa: D401
                return {"ok": True}

        def _fake(repo, contract, run_id, **kwargs):
            self.captured = kwargs
            return _Rep()
        workflow.run_contract = _fake

    def tearDown(self):
        workflow.run_contract = self._orig

    def test_implementer_gets_quality_gate_not_schema(self):
        cr.run(ROOT, {"role": "implementer", "prompt": "p", "sandbox": "workspace-write",
                      "files_allowed_to_change": ["x"]}, "r")
        self.assertTrue(self.captured["quality_gate_enabled"])
        self.assertNotIn("output_schema", self.captured)
        self.assertTrue(self.captured["cache_enabled"])

    def test_producing_carrier_gets_schema_gate_not_quality(self):
        for role, schema in [("linon", "linon-review"), ("stefan", "aesthetic-review"),
                             ("aggressive-designer", "design-proposal")]:
            cr.run(ROOT, {"role": role, "prompt": "p", "sandbox": "read-only"}, "r")
            self.assertFalse(self.captured["quality_gate_enabled"])
            self.assertIn(schema, self.captured["output_schema"])
            self.assertEqual(self.captured["output_path"], cr.OUTPUT_FILE)

    def test_unknown_role_rejected(self):
        with self.assertRaises(SystemExit):
            cr.run(ROOT, {"role": "nobody", "prompt": "p", "sandbox": "read-only"}, "r")

    def test_no_cache_flag(self):
        cr.run(ROOT, {"role": "implementer", "prompt": "p", "sandbox": "workspace-write",
                      "files_allowed_to_change": ["x"]}, "r", cache=False)
        self.assertFalse(self.captured["cache_enabled"])


if __name__ == "__main__":
    unittest.main()
