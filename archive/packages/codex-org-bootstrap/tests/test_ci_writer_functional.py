from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "packages" / "codex-org-bootstrap" / "src"))

import ci_writer_functional as ciw  # noqa: E402


class FunctionalCiWriterKernelTests(unittest.TestCase):
    def test_false_green_static_gate_rejects_or_true(self):
        workflow = """name: bad
on: { pull_request: {} }
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - run: |
          set -euo pipefail
          python3 -m unittest || true
"""
        findings = ciw.workflow_false_green_findings(workflow, ".github/workflows/functional-ci.yml")
        self.assertTrue(any("|| true" in f["detail"] for f in findings), findings)

    def test_negative_control_gate_passes_honest_workflow_and_fails_false_green(self):
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d)
            tests = repo / "tests"
            tests.mkdir()
            (tests / "test_sample.py").write_text(
                "import unittest\n\nclass Sample(unittest.TestCase):\n"
                "    def test_ok(self):\n"
                "        self.assertTrue(True)\n",
                encoding="utf-8",
            )
            checks = [ciw.CheckCommand("python", "python3 -m unittest discover -s tests", "test")]
            honest = """name: functional-ci
on: { pull_request: {} }
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - run: |
          set -euo pipefail
          python3 -m unittest discover -s tests
"""
            proof = ciw.prove_negative_control(repo, honest, checks)
            self.assertTrue(proof.passed, proof)
            self.assertTrue(proof.good_input["passed"], proof.good_input)
            self.assertTrue(proof.negative_control["red_observed"], proof.negative_control)

            false_green = honest.replace("python3 -m unittest discover -s tests",
                                         "python3 -m unittest discover -s tests || true")
            failed = ciw.prove_negative_control(repo, false_green, checks)
            self.assertFalse(failed.passed, failed)
            self.assertTrue(any(e.code == "negative_control_static_failed" for e in failed.escalations),
                            failed.escalations)

    def test_resolver_escalates_stdlib_first_party_and_ambiguous_modules(self):
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d)
            (repo / "localpkg.py").write_text("VALUE = 1\n", encoding="utf-8")
            with self.assertRaises(ciw.ModuleResolutionEscalation) as stdlib:
                ciw.resolve_module_distribution(
                    "json.decoder", repo, package_map={}, stdlib_names={"json"}, first_party_modules=set())
            self.assertEqual(stdlib.exception.code, "stdlib_module")

            with self.assertRaises(ciw.ModuleResolutionEscalation) as first_party:
                ciw.resolve_module_distribution(
                    "localpkg", repo, package_map={}, stdlib_names=set(), first_party_modules={"localpkg"})
            self.assertEqual(first_party.exception.code, "first_party_module")

            with self.assertRaises(ciw.ModuleResolutionEscalation) as ambiguous:
                ciw.resolve_module_distribution(
                    "google.cloud", repo,
                    package_map={"google": ["google-api-core", "google-auth"]},
                    stdlib_names=set(),
                    first_party_modules=set(),
                )
            self.assertEqual(ambiguous.exception.code, "ambiguous_module_distribution")

    def test_resolver_uses_curated_alias_without_guessing(self):
        with tempfile.TemporaryDirectory() as d:
            dist = ciw.resolve_module_distribution(
                "cv2", Path(d), package_map={}, stdlib_names=set(), first_party_modules=set())
            self.assertEqual(dist, "opencv-python")

    def test_emitted_fixpoint_contains_fail_closed_resolution_protocol(self):
        step = ciw._fixpoint_step_script("python3 -m unittest discover")
        self.assertIn("except ModuleNotFoundError as exc", step)
        self.assertIn("return exc.name", step)
        self.assertIn('module.split(".")[0]', step)
        self.assertIn("stdlib_module", step)
        self.assertIn("first_party_module", step)
        self.assertIn("packages_distributions()", step)
        self.assertIn('"cv2": "opencv-python"', step)
        self.assertIn("ambiguous_module_distribution", step)
        self.assertIn("ResolutionImpossible", step)
        self.assertIn("attempted.add(mod)", step)


if __name__ == "__main__":
    unittest.main()
