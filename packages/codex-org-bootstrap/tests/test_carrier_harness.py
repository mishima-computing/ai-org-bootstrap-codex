from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location("carrier_harness", ROOT / "scripts" / "carrier_harness.py")
ch = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ch)


class CarrierHarnessTests(unittest.TestCase):
    def test_stdin_is_always_closed(self):
        # The whole point of the harness: codex never waits on stdin.
        import inspect
        self.assertIn("stdin=subprocess.DEVNULL", inspect.getsource(ch.run_carrier))

    def test_argv_pins_flags_and_validates_sandbox(self):
        argv = ch.build_codex_argv(Path("/tmp/x"), "workspace-write", model="m")
        self.assertEqual(argv[:2], ["codex", "exec"])
        self.assertIn("-C", argv)
        self.assertIn("--sandbox", argv)
        self.assertIn("--model", argv)
        with self.assertRaises(ValueError):
            ch.build_codex_argv(Path("/tmp/x"), "yolo")

    def test_discipline_prepended(self):
        composed = ch.compose_prompt("DO X", "GUARD", True)
        self.assertTrue(composed.startswith("GUARD"))
        self.assertIn("DO X", composed)
        self.assertEqual(ch.compose_prompt("DO X", "GUARD", False), "DO X")

    def test_scope_deviations(self):
        dev = ch.scope_deviations(
            ["demos/a.html", "roles/x.md", "scripts/y.py"], ["demos/**", "scripts/*.py"])
        self.assertEqual(dev, ["roles/x.md"])
        self.assertEqual(ch.scope_deviations(["demos/a.html"], []), [])

    def test_self_test_passes(self):
        self.assertEqual(ch.self_test(), 0)


if __name__ == "__main__":
    unittest.main()
