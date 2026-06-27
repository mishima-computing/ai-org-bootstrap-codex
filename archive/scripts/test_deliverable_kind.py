"""Tests for the deterministic deliverable-kind classifier (ADR-0014 operability extension).

Verifies first-match-under-precedence, evidence carriage, the kind-aware required-operability map (health is
service-only — the false-positive killer), and the refuse-don't-guess outcomes (unknown_service_like /
undetermined). Mirrors the production buildpack-detect shape the web research grounded the design in.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import deliverable_kind as dk  # noqa: E402


class ClassifyTest(unittest.TestCase):
    def test_http_service_from_server_cmd_and_port(self):
        v = dk.classify_kind({"server_cmd": ["uvicorn app:app"], "bound_port": True,
                              "explicit_deploy_config": True})
        self.assertEqual(v["kind"], "http_service")
        self.assertEqual(v["confidence"], "high")
        self.assertTrue(v["advisory_only"])

    def test_http_service_from_framework_and_routes(self):
        v = dk.classify_kind({"http_framework": True, "routes": ["/users"], "health_routes": ["/healthz"]})
        self.assertEqual(v["kind"], "http_service")

    def test_rpc_service_wins_over_http(self):
        v = dk.classify_kind({"proto": ["api.proto"], "grpc": True, "server_cmd": ["x"], "bound_port": True})
        self.assertEqual(v["kind"], "rpc_service")        # precedence: rpc fires first
        self.assertTrue(any(c["kind"] == "http_service" for c in v["candidates"]))  # http kept as candidate

    def test_cli_from_console_scripts(self):
        v = dk.classify_kind({"console_scripts": True})
        self.assertEqual(v["kind"], "cli")

    def test_batch_job_from_scheduler(self):
        v = dk.classify_kind({"scheduler": ["k8s CronJob"]})
        self.assertEqual(v["kind"], "batch_job")

    def test_library_is_the_residual(self):
        v = dk.classify_kind({"importable_only": True})
        self.assertEqual(v["kind"], "library")

    def test_listener_without_http_rpc_is_unknown_service_like(self):
        v = dk.classify_kind({"bound_port": True})
        self.assertEqual(v["kind"], dk.UNKNOWN_SERVICE)   # NOT forced into batch_job

    def test_undetermined_when_executable_but_unclassifiable(self):
        v = dk.classify_kind({"dockerfile_cmd": ["CMD ['./weird']"]})
        self.assertEqual(v["kind"], dk.UNDETERMINED)
        self.assertEqual(v["confidence"], "unknown")

    def test_nothing_detected_is_undetermined(self):
        v = dk.classify_kind({})
        self.assertEqual(v["kind"], dk.UNDETERMINED)


class RequiredOperabilityTest(unittest.TestCase):
    def test_health_is_service_only(self):
        self.assertIn("health", dk.required_operability("http_service"))
        self.assertIn("health", dk.required_operability("rpc_service"))
        self.assertNotIn("health", dk.required_operability("cli"))      # the false-positive killer
        self.assertNotIn("health", dk.required_operability("batch_job"))
        self.assertNotIn("health", dk.required_operability("library"))

    def test_cli_requires_clean_exit_not_health(self):
        req = dk.required_operability("cli")
        self.assertIn("clean_exit", req)
        self.assertIn("dependency_pinning", req)

    def test_every_interface_kind_has_a_required_set(self):
        for k in dk.INTERFACE_KINDS:
            self.assertTrue(dk.required_operability(k), k)


if __name__ == "__main__":
    unittest.main(verbosity=2)
