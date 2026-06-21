"""Tests for the deterministic operability scan (ADR-0014 operability extension)."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import operability_scan as ops  # noqa: E402
import deliverable_kind as dk  # noqa: E402
import pre_localizer  # noqa: E402


def write(repo: Path, rel: str, text: str):
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def http_repo(tmp: Path) -> Path:
    write(tmp, "Dockerfile", "FROM python:3.12\nEXPOSE 8080\nCMD [\"uvicorn\", \"app:app\", \"--port\", \"8080\"]\n")
    write(tmp, "app.py", "from fastapi import FastAPI\napp = FastAPI()\n@app.get('/healthz')\ndef h(): return 'ok'\n")
    write(tmp, "requirements.txt", "fastapi==0.111.0\nuvicorn==0.30.0\n")
    return tmp


def cli_repo(tmp: Path) -> Path:
    write(tmp, "pyproject.toml", "[project]\nname='mytool'\n[project.scripts]\nmytool='mytool.cli:main'\n")
    write(tmp, "mytool/cli.py", "import argparse\ndef main():\n    argparse.ArgumentParser().parse_args()\n")
    write(tmp, "poetry.lock", "# locked\n")
    return tmp


class OperabilityScanTest(unittest.TestCase):
    def setUp(self):
        pre_localizer._INDEX_CACHE.clear()

    def test_http_service_surface_and_kind(self):
        with tempfile.TemporaryDirectory() as d:
            repo = http_repo(Path(d))
            m = ops.OperabilityScan(repo).build()
            self.assertEqual(m["kind_verdict"]["kind"], "http_service")
            self.assertTrue(m["surface"].get("health_routes"))
            # health is REQUIRED for a service and IS present -> not in missing
            self.assertFalse(any("absent: health" in x for x in m["missing_safety_checks"]))
            vc = [v for v in m["continuity_prefill"]["version_constraints"] if "no lockfile" not in v]
            self.assertTrue(any("pinned" in v for v in vc), vc)   # requirements.txt pins detected

    def test_cli_does_not_demand_health(self):
        with tempfile.TemporaryDirectory() as d:
            repo = cli_repo(Path(d))
            m = ops.OperabilityScan(repo).build()
            self.assertEqual(m["kind_verdict"]["kind"], "cli")
            # the false-positive killer: a CLI is NEVER faulted for lacking a health endpoint
            self.assertFalse(any("health" in x for x in m["missing_safety_checks"]),
                             f"a CLI must not demand health; got {m['missing_safety_checks']}")

    def test_continuity_prefill_shape_excludes_selected_profiles(self):
        with tempfile.TemporaryDirectory() as d:
            repo = http_repo(Path(d))
            pre = ops.OperabilityScan(repo).build()["continuity_prefill"]
            self.assertEqual(set(pre), {"version_constraints", "ecosystem_facts_used",
                                        "forbidden_expansions", "missing_safety_checks"})
            self.assertNotIn("selected_profiles", pre)   # Codex correction: profile ids are authorization-validated
            # the inferred kind rides in ecosystem_facts_used, not selected_profiles
            self.assertTrue(any("deliverable_kind=" in f for f in pre["ecosystem_facts_used"]))

    def test_report_text_does_not_make_a_library_a_service(self):
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d)
            write(repo, "lib.py", "# TRANSPORT SUPPORT REPORT IMPORTANT\ndef helper():\n    return 1\n")
            m = ops.OperabilityScan(repo).build()
            # the bare-PORT regex bug used to mark this library as a service; the fix keeps it a library
            self.assertEqual(m["kind_verdict"]["kind"], "library")
            self.assertNotEqual(m["kind_verdict"]["kind"], dk.UNKNOWN_SERVICE)
            self.assertFalse(any("health" in x for x in m["missing_safety_checks"]))

    def test_forbidden_expansions_from_guard_map(self):
        with tempfile.TemporaryDirectory() as d:
            repo = cli_repo(Path(d))
            gm = {"protected_exports": [{"file": "mytool/cli.py", "symbols": ["1: def main"]}],
                  "governing_adrs": [{"doc": "docs/ADR-1.md", "governs": ["mytool"]}]}
            m = ops.OperabilityScan(repo, guard_map=gm).build()
            self.assertTrue(any("mytool/cli.py" in f for f in m["forbidden_expansions"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
