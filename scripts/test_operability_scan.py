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
            self.assertEqual(m["existing_repo_surface_kind"], m["kind_verdict"])
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
            # the existing repo surface kind rides in ecosystem_facts_used, not selected_profiles
            self.assertTrue(any("existing_repo_surface_kind=" in f for f in pre["ecosystem_facts_used"]))

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

    def test_change_intent_no_surface_refactor_in_service_repo_advises_none(self):
        with tempfile.TemporaryDirectory() as d:
            repo = http_repo(Path(d))
            candidates = pre_localizer.PreLocalizer(repo).candidates("rename/refactor internal app plumbing, no behavior change")
            m = ops.ChangeIntentScan(repo, "rename/refactor internal app plumbing, no behavior change",
                                     candidates).build()
            self.assertEqual(m["interface_delta"], "no_surface_change")
            self.assertTrue(m["advisory_only"])
            self.assertEqual(m["existing_repo_surface_kind"]["kind"], "http_service")
            self.assertEqual(m["deliverable_kind_advice"], "none")
            self.assertTrue(any("regression_suite" in x for x in m["contract_design_advice"]))

    def test_change_intent_add_endpoint_preserves_existing_surface_kind(self):
        with tempfile.TemporaryDirectory() as d:
            repo = http_repo(Path(d))
            m = ops.ChangeIntentScan(repo, "add /metrics endpoint").build()
            self.assertEqual(m["interface_delta"], "adds_new_interface")
            self.assertEqual(m["existing_repo_surface_kind"]["kind"], "http_service")
            self.assertEqual(m["deliverable_kind_advice"], "http_service")

    def test_change_intent_weak_or_conflicting_falls_back_unknown(self):
        with tempfile.TemporaryDirectory() as d:
            repo = http_repo(Path(d))
            weak = ops.ChangeIntentScan(repo, "improve support").build()
            self.assertEqual(weak["interface_delta"], "unknown")
            self.assertIsNone(weak["deliverable_kind_advice"])
            conflicting = ops.ChangeIntentScan(repo, "add and remove endpoint support").build()
            self.assertEqual(conflicting["interface_delta"], "unknown")
            self.assertIsNone(conflicting["deliverable_kind_advice"])

    def test_change_intent_removes_and_modifies_interfaces(self):
        with tempfile.TemporaryDirectory() as d:
            repo = http_repo(Path(d))
            removes = ops.ChangeIntentScan(repo, "remove /healthz endpoint").build()
            self.assertEqual(removes["interface_delta"], "removes_interface")
            self.assertEqual(removes["deliverable_kind_advice"], "http_service")
            modifies = ops.ChangeIntentScan(repo, "change existing /healthz endpoint response").build()
            self.assertEqual(modifies["interface_delta"], "modifies_existing_interface")
            self.assertEqual(modifies["deliverable_kind_advice"], "http_service")

    def test_transform_kind_routes_unambiguous_rename_only_when_tool_accepts(self):
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d)
            write(repo, "pkg/scaffold.py", "def scaffold_runner():\n    return 'scaffold'\n")
            routed = ops.TransformKindScan(repo, "rename scaffold to demo_org").build()
            self.assertEqual(routed["route"], "tool")
            self.assertEqual(routed["tool_id"], "rename-codemod")
            self.assertEqual(routed["transform_kind"], "rename")

            ambiguous = ops.TransformKindScan(repo, "rename scaffold to demo_org and rename alpha to beta").build()
            self.assertEqual(ambiguous["route"], "llm")
            self.assertEqual(ambiguous["transform_kind"], "novel")

            unsupported = ops.TransformKindScan(repo, "rename missing_symbol to other_symbol").build()
            self.assertEqual(unsupported["route"], "llm")

    def test_transform_kind_routes_new_kinds_in_shadow_only_when_tools_accept(self):
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d)
            write(repo, "pkg/old_mod.py", "VALUE = 1\n")
            write(repo, "consumer.py", "from pkg.old_mod import VALUE\n")
            write(repo, "messy.py", "import sys\nx = 1   \n")
            write(repo, "sig.py", "def fetch(url, timeout):\n    return url\n")

            move = ops.TransformKindScan(repo, "move pkg/old_mod.py to pkg/new_mod.py").build()
            self.assertEqual(move["route"], "tool")
            self.assertEqual(move["tool_id"], "move-relocate")
            self.assertEqual(move["mode"], "shadow")

            imports = ops.TransformKindScan(repo, "clean imports in messy.py").build()
            self.assertEqual(imports["route"], "tool")
            self.assertEqual(imports["tool_id"], "import-hygiene")
            self.assertEqual(imports["mode"], "shadow")

            fmt = ops.TransformKindScan(repo, "format messy.py with the repo formatter").build()
            self.assertEqual(fmt["route"], "tool")
            self.assertEqual(fmt["tool_id"], "format-lint-fix")
            self.assertEqual(fmt["mode"], "shadow")

            sig = ops.TransformKindScan(repo, "change signature of fetch to fetch(endpoint, timeout=10) in sig.py").build()
            self.assertEqual(sig["route"], "tool")
            self.assertEqual(sig["tool_id"], "signature-change")
            self.assertEqual(sig["mode"], "shadow")

            ambiguous = ops.TransformKindScan(repo, "move pkg/old_mod.py to pkg/new_mod.py and format messy.py").build()
            self.assertEqual(ambiguous["route"], "llm")
        print("ok  transform-kind classifier routes move/import/format/signature shadow and rejects ambiguity")

    def test_transform_kind_extracts_mixed_leaf_suboperation_without_routing_whole_leaf(self):
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d)
            write(repo, "pkg/scaffold.py", "def scaffold_runner():\n    return 1\n")
            scan = ops.TransformKindScan(repo, "add feature flag support and rename scaffold to demo_org")
            whole = scan.build()
            subops = scan.deterministic_subops()
            self.assertEqual(whole["route"], "llm")
            self.assertEqual(len(subops), 1)
            self.assertEqual(subops[0]["tool_id"], "rename-codemod")
        print("ok  mixed objective is LLM as a whole but exposes deterministic sub-op for extraction")


if __name__ == "__main__":
    unittest.main(verbosity=2)
