#!/usr/bin/env python3
"""Tests for the Python-cored implementer host."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import controller_goal          # noqa: E402
import controller_run           # noqa: E402
import controller_scope         # noqa: E402
import implement_host           # noqa: E402

ORG_ROOT = Path(__file__).resolve().parent.parent


def _git(repo: Path, *args: str) -> None:
    cp = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if cp.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {cp.stderr}")


def make_repo(tmp: Path) -> Path:
    repo = tmp / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "alpha.py").write_text("def alpha_feature():\n    return 'old'\n", encoding="utf-8")
    (repo / "src" / "missed.py").write_text("VALUE = 'old'\n", encoding="utf-8")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_alpha.py").write_text(
        "from src.alpha import alpha_feature\n\n"
        "def test_alpha_feature():\n    assert alpha_feature() == 'old'\n",
        encoding="utf-8",
    )
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


def impl_contract(**overrides):
    base = {
        "role_id": "aufheben-designer",
        "contract_id": "c1",
        "objective": "update alpha feature",
        "acceptance_criteria": ["alpha returns new value"],
        "files_allowed_to_change": ["src/*.py"],
        "files_not_allowed_to_change": ["docs/**"],
        "required_checks": ["python -m pytest"],
        "deliverable_kind": "library",
        "conformance": {"library": {"entrypoint": "src.alpha:alpha_feature", "examples": []}},
    }
    base.update(overrides)
    return base


def raw_prompt(contract=None, objective="update alpha feature"):
    return json.dumps({
        "role": "implementer",
        "objective": objective,
        "inputs": {"aufheben-designer": contract or impl_contract()},
    }, sort_keys=True)


class StubCarrier:
    def __init__(self, edit=None):
        self.prompts = []
        self.edit = edit

    def __call__(self, rp, prompt, sandbox, *, timeout=600, retries=1, out_dir=None, resume_session=None):
        self.prompts.append(prompt)
        if self.edit:
            self.edit(Path(rp))
        return {"ok": True, "attempts": [{"attempt": 0, "ok": True}], "session_id": "s-impl"}


class BuildMapTest(unittest.TestCase):
    def test_build_map_folds_localization_and_contract_guards(self):
        with tempfile.TemporaryDirectory() as d:
            repo = make_repo(Path(d))
            bm = implement_host.build_map_for(
                repo,
                raw_prompt(),
                write_scope=["src/*.py"],
                goal_context={"structured_goal": {"negative_control": "old alpha still accepted",
                                                  "success_condition": "new alpha passes"}},
            )
            localized = [x["path"] for x in bm["localization"]["in_scope_prelocalized"]]
            self.assertIn("src/alpha.py", localized)
            missed = [x["path"] for x in bm["localization"]["in_scope_not_prelocalized"]]
            self.assertIn("src/missed.py", missed)
            self.assertEqual(bm["contract_guards"]["deliverable_kind"], "library")
            self.assertIn("alpha returns new value", bm["contract_guards"]["acceptance_criteria"])
            self.assertEqual(bm["scope"]["files_allowed_to_change"], ["src/*.py"])

    def test_why_present_and_absent_prompt_markers(self):
        with tempfile.TemporaryDirectory() as d:
            repo = make_repo(Path(d))
            present = implement_host.build_map_for(
                repo,
                raw_prompt(),
                write_scope=["src/*.py"],
                goal_context={"structured_goal": {"negative_control": "malformed record must fail",
                                                  "success_condition": "valid record succeeds"}},
            )
            prompt = implement_host.format_build_section(present)
            self.assertIn("malformed record must fail", prompt)
            self.assertIn("WHY:present", prompt)

            absent = implement_host.build_map_for(repo, raw_prompt(), write_scope=["src/*.py"])
            absent_prompt = implement_host.format_build_section(absent)
            self.assertIn("WHY:absent", absent_prompt)
            self.assertNotIn("malformed record must fail", absent_prompt)


class ForwardScopePressureTest(unittest.TestCase):
    """R3: scope pressure is explicit and self-checked before the violation (controller_scope stays the gate)."""

    def _section(self, repo, contract):
        bm = implement_host.build_map_for(
            repo, raw_prompt(contract=contract), write_scope=contract.get("files_allowed_to_change"),
        )
        return implement_host.format_build_section(bm)

    def test_tight_allow_and_deny_render_donottouch_and_self_check(self):
        with tempfile.TemporaryDirectory() as d:
            repo = make_repo(Path(d))
            contract = impl_contract(
                files_allowed_to_change=["src/alpha.py"],
                files_not_allowed_to_change=["tests/**", "docs/**"],
            )
            section = self._section(repo, contract)
            # (a) explicit DO-NOT-TOUCH list naming the denied paths + the pre-finish self-check.
            self.assertIn("DO-NOT-TOUCH", section)
            self.assertIn("- tests/**", section)
            self.assertIn("- docs/**", section)
            self.assertIn("- src/alpha.py", section)  # allow-list surfaced explicitly too
            self.assertIn("PRE-FINISH SELF-CHECK", section)
            self.assertIn("STOP and report that aufheben must widen", section)

    def test_no_deny_entries_emit_no_donottouch_noise(self):
        with tempfile.TemporaryDirectory() as d:
            repo = make_repo(Path(d))
            contract = impl_contract(
                files_allowed_to_change=["src/*.py"], files_not_allowed_to_change=[],
            )
            section = self._section(repo, contract)
            # (b) no spurious/empty DO-NOT-TOUCH block when nothing is denied.
            self.assertNotIn("DO-NOT-TOUCH", section)
            self.assertIn("ALLOWED to change", section)  # allow-list still rendered
            self.assertIn("PRE-FINISH SELF-CHECK", section)  # self-check is allow-list-based, not deny-only

    def test_why_line_and_build_map_json_unchanged_in_shape(self):
        with tempfile.TemporaryDirectory() as d:
            repo = make_repo(Path(d))
            contract = impl_contract(files_not_allowed_to_change=["docs/**"])
            bm = implement_host.build_map_for(
                repo, raw_prompt(contract=contract), write_scope=contract.get("files_allowed_to_change"),
                goal_context={"structured_goal": {"negative_control": "old alpha rejected",
                                                  "success_condition": "new alpha passes"}},
            )
            section = implement_host.format_build_section(bm)
            # (c) WHY line and the build_map JSON dump remain present and unchanged in shape.
            self.assertIn("WHY:present", section)
            self.assertIn("```json\n" + json.dumps(bm, indent=2, ensure_ascii=False) + "\n```", section)


class AdvisoryScopeTest(unittest.TestCase):
    def test_prelocalized_set_does_not_narrow_write_scope(self):
        with tempfile.TemporaryDirectory() as d:
            repo = make_repo(Path(d))
            snapshot = controller_scope.baseline_snapshot(repo)

            def edit_missed(rp: Path) -> None:
                (rp / "src" / "missed.py").write_text("VALUE = 'new'\n", encoding="utf-8")

            carrier = StubCarrier(edit=edit_missed)
            runner = implement_host.make_implement_carrier_runner(
                repo,
                objective=raw_prompt(objective="update alpha feature"),
                contract_inputs={"aufheben-designer": impl_contract()},
                write_scope=["src/*.py"],
                carrier=carrier,
            )
            cr = runner(repo, "role prompt", "workspace-write", out_dir=repo / ".agent-runs" / "impl")
            self.assertTrue(cr["ok"])
            self.assertIn("src/missed.py", carrier.prompts[0])
            scope = controller_scope.enforce(repo, ["src/*.py"], baseline_snapshot=snapshot)
            self.assertTrue(scope.scope_ok, scope.to_dict())
            self.assertEqual(scope.changed, ["src/missed.py"])


class DefectLocusReLocalizationTest(unittest.TestCase):
    """R4: a repair re-seeds pre-localization from the blocking finding's defect_locus. The locus only
    RE-RANKS the advisory in_scope_prelocalized set; it never widens files_allowed_to_change (ADR-0006)."""

    def _localized(self, bm):
        return [x["path"] for x in bm["localization"]["in_scope_prelocalized"]]

    def test_locus_promotes_named_file_to_top(self):
        # (a) on a repair with a defect_locus naming X, in_scope_prelocalized ranks X first.
        with tempfile.TemporaryDirectory() as d:
            repo = make_repo(Path(d))
            base = implement_host.build_map_for(repo, raw_prompt(), write_scope=["src/*.py"])
            self.assertNotEqual(self._localized(base)[0], "src/missed.py")   # not naturally top
            repaired = implement_host.build_map_for(
                repo, raw_prompt(), write_scope=["src/*.py"],
                defect_locus={"file": "src/missed.py", "line_range": [1, 1]},
            )
            self.assertEqual(self._localized(repaired)[0], "src/missed.py")
            self.assertEqual(repaired["localization"]["defect_locus"]["file"], "src/missed.py")

    def test_low_ranked_in_scope_file_is_promoted(self):
        # (b) a finding pointing at a currently NOT-surfaced in-scope file (src/missed.py lands in
        # in_scope_not_prelocalized today) promotes it INTO the pre-localized set.
        with tempfile.TemporaryDirectory() as d:
            repo = make_repo(Path(d))
            base = implement_host.build_map_for(repo, raw_prompt(), write_scope=["src/*.py"])
            self.assertIn("src/missed.py",
                          [x["path"] for x in base["localization"]["in_scope_not_prelocalized"]])
            repaired = implement_host.build_map_for(
                repo, raw_prompt(), write_scope=["src/*.py"],
                defect_locus={"file": "src/missed.py"},
            )
            self.assertIn("src/missed.py", self._localized(repaired))

    def test_no_locus_build_map_byte_for_byte_identical(self):
        # (c) no locus -> the build_map is unchanged from today (the localization carries no defect_locus key).
        with tempfile.TemporaryDirectory() as d:
            repo = make_repo(Path(d))
            a = implement_host.build_map_for(repo, raw_prompt(), write_scope=["src/*.py"])
            b = implement_host.build_map_for(repo, raw_prompt(), write_scope=["src/*.py"], defect_locus=None)
            c = implement_host.build_map_for(repo, raw_prompt(), write_scope=["src/*.py"], defect_locus={})
            self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True))
            self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(c, sort_keys=True))
            self.assertNotIn("defect_locus", a["localization"])

    def test_locus_is_advisory_does_not_widen_write_scope(self):
        # (d) a locus naming a file OUTSIDE files_allowed_to_change does NOT make it writable: the scope
        # is unchanged and the file stays in prelocalized_out_of_scope, never in_scope_prelocalized.
        with tempfile.TemporaryDirectory() as d:
            repo = make_repo(Path(d))   # tests/test_alpha.py exists but is outside the src/*.py allow-list
            bm = implement_host.build_map_for(
                repo, raw_prompt(), write_scope=["src/*.py"],
                defect_locus={"file": "tests/test_alpha.py", "line_range": [1, 1]},
            )
            self.assertEqual(bm["scope"]["files_allowed_to_change"], ["src/*.py"])   # boundary untouched
            self.assertNotIn("tests/test_alpha.py", self._localized(bm))
            out_of_scope = [x["path"] for x in bm["localization"]["prelocalized_out_of_scope"]]
            self.assertIn("tests/test_alpha.py", out_of_scope)

    def test_runner_threads_locus_into_build_map(self):
        # the carrier runner forwards defect_locus end-to-end: the prompt the implementer sees ranks the
        # locus file first.
        with tempfile.TemporaryDirectory() as d:
            repo = make_repo(Path(d))
            carrier = StubCarrier()
            runner = implement_host.make_implement_carrier_runner(
                repo,
                objective=raw_prompt(objective="update alpha feature"),
                contract_inputs={"aufheben-designer": impl_contract()},
                write_scope=["src/*.py"],
                carrier=carrier,
                defect_locus={"file": "src/missed.py"},
            )
            cr = runner(repo, "role prompt", "workspace-write", out_dir=repo / ".agent-runs" / "impl")
            self.assertTrue(cr["ok"])
            build_map = json.loads((repo / ".agent-runs" / "impl" / implement_host.BUILD_MAP_FILE).read_text())
            self.assertEqual(
                build_map["localization"]["in_scope_prelocalized"][0]["path"], "src/missed.py")


class WhyThreadTest(unittest.TestCase):
    def test_run_goal_threads_structured_why_to_leaf_pipeline(self):
        with tempfile.TemporaryDirectory() as d:
            repo = make_repo(Path(d))
            seen = []

            def run_pipeline(wt, objective, run_id, *, goal_context=None):
                seen.append(goal_context)
                return {"converged": True, "sessions": {}}

            def run_leaf(r, task, *, resume_diff=None, goal_context=None):
                return controller_goal.default_run_leaf(
                    r, task, run_pipeline=run_pipeline, resume_diff=resume_diff, goal_context=goal_context
                )

            structured = {
                "outcome": "O",
                "success_condition": "S",
                "negative_control": "N-from-memory-only",
                "owner": "W",
            }
            split = lambda goal, ctx, carrier: [{
                "id": "leaf1", "objective": "update alpha feature", "scope": ["src/*.py"], "depends_on": []
            }]
            refine = lambda goal, ctx, carrier: {"sufficient": True, "missing": [], "structured": structured}
            controller_goal.run_goal(repo, "goal text", split=split, refine=refine, run_leaf=run_leaf)
            self.assertTrue(seen)
            self.assertEqual(seen[0]["structured_goal"]["negative_control"], "N-from-memory-only")

    def test_controller_run_installs_host_and_passes_why_to_prompt(self):
        with tempfile.TemporaryDirectory() as d:
            repo = make_repo(Path(d))
            old_org_root = os.environ.get("AI_ORG_ROOT")
            os.environ["AI_ORG_ROOT"] = str(ORG_ROOT)
            old_default = implement_host._default_carrier
            carrier = StubCarrier()
            implement_host._default_carrier = carrier
            try:
                report = controller_run.run(
                    repo,
                    {
                        "role": "implementer",
                        "prompt": raw_prompt(),
                        "sandbox": "workspace-write",
                        "files_allowed_to_change": ["src/*.py"],
                    },
                    "impl-host-test",
                    goal_context={"structured_goal": {"negative_control": "only in memory",
                                                      "success_condition": "memory path works"}},
                )
            finally:
                implement_host._default_carrier = old_default
                if old_org_root is None:
                    os.environ.pop("AI_ORG_ROOT", None)
                else:
                    os.environ["AI_ORG_ROOT"] = old_org_root
            self.assertTrue(report.ok, report.to_dict())
            self.assertTrue(carrier.prompts)
            self.assertIn("only in memory", carrier.prompts[0])
            self.assertFalse((repo / ".agent-runs" / "goals").exists(), "WHY must not be read from a goal record")


def http_contract(**overrides):
    base = impl_contract(
        objective="wire the orders http endpoint",
        deliverable_kind="http_service",
        files_allowed_to_change=["src/*.py"],
        conformance={"http_service": {"start_cmd": "python -m src.app", "health": "/healthz", "port": 8080}},
    )
    base.update(overrides)
    return base


class CassetteCatalogTest(unittest.TestCase):
    def test_catalog_exposes_name_and_description_not_body(self):
        # (a) cassette_catalog() returns name+description for integration, NOT the body.
        catalog = implement_host.cassette_catalog()
        names = [c["name"] for c in catalog]
        self.assertIn("integration", names)
        entry = next(c for c in catalog if c["name"] == "integration")
        self.assertEqual(set(entry.keys()), {"name", "description"})
        self.assertIn("production boundary", entry["description"])
        self.assertNotIn("NEVER stub or mock", json.dumps(catalog))   # the body is never disclosed here


class SelectCassettesTest(unittest.TestCase):
    def test_select_for_http_integration_objective(self):
        # (b) select_cassettes -> ["integration"] for an http_service/integration objective.
        picks = implement_host.select_cassettes(
            "wire the orders http endpoint", http_contract(), ["src/app.py"])
        self.assertEqual(picks, ["integration"])

    def test_select_none_for_cli_library(self):
        # (b) select_cassettes -> [] for cli/library.
        picks = implement_host.select_cassettes(
            "build a cli that prints reports",
            impl_contract(objective="build a cli that prints reports", deliverable_kind="library"),
            ["src/cli.py"],
        )
        self.assertEqual(picks, [])


class CassetteBuildSectionTest(unittest.TestCase):
    def test_body_appended_when_selected_absent_otherwise(self):
        # (c) the integration body text is appended to the build-map prompt when selected, absent otherwise.
        with tempfile.TemporaryDirectory() as d:
            repo = make_repo(Path(d))
            selected = implement_host.build_map_for(
                repo, raw_prompt(contract=http_contract(), objective="wire the orders http endpoint"),
                write_scope=["src/*.py"],
            )
            section = implement_host.format_build_section(selected)
            self.assertEqual([c["name"] for c in selected["cassettes"]], ["integration"])
            self.assertIn("NEVER stub or mock the production boundary", section)
            self.assertIn("PRIMING CASSETTES", section)

            none = implement_host.build_map_for(repo, raw_prompt(), write_scope=["src/*.py"])  # library contract
            none_section = implement_host.format_build_section(none)
            self.assertEqual(none["cassettes"], [])
            self.assertNotIn("NEVER stub or mock the production boundary", none_section)
            self.assertNotIn("PRIMING CASSETTES", none_section)


class CassetteShadowTest(unittest.TestCase):
    """(d) the shadow query streams a cassette_shadow event with both picks and does NOT block/fail the
    launch (stub that sleeps / raises)."""

    def _read_shadow_events(self, log: Path, run_id: str):
        # Filter on run_id: STREAM_LOG is process-global and resolved at write-time, so a fire-and-forget
        # daemon thread from another test could tee into this log. Each test asserts only its own event.
        if not log.exists():
            return []
        events = [json.loads(line) for line in log.read_text().splitlines() if line.strip()]
        return [e for e in events if e.get("type") == "cassette_shadow" and e.get("run_id") == run_id]

    def test_shadow_streams_both_picks_and_does_not_block(self):
        with tempfile.TemporaryDirectory() as d:
            repo = make_repo(Path(d))
            log = repo / "stream.jsonl"
            os.environ["STREAM_LOG"] = str(log)
            try:
                def slow_query(catalog, none_option):
                    time.sleep(0.5)
                    self.assertNotIn("body", json.dumps(catalog))   # aufheben sees no bodies
                    return [{"name": "integration"}]

                t0 = time.monotonic()
                thread = implement_host.fire_cassette_shadow(
                    repo, "wire the orders http endpoint", http_contract(), ["src/app.py"],
                    run_id="r-shadow", aufheben_query=slow_query)
                elapsed = time.monotonic() - t0
                self.assertLess(elapsed, 0.4, "fire_cassette_shadow must not block on the query")
                self.assertEqual(self._read_shadow_events(log, "r-shadow"), [])   # nothing streamed yet (still sleeping)
                thread.join(5)
                events = self._read_shadow_events(log, "r-shadow")
                self.assertEqual(len(events), 1)
                self.assertEqual(events[0]["deterministic_pick"], ["integration"])
                self.assertEqual(events[0]["aufheben_pick"], ["integration"])
                self.assertEqual(events[0]["run_id"], "r-shadow")
            finally:
                os.environ.pop("STREAM_LOG", None)

    def test_shadow_swallows_a_raising_query(self):
        with tempfile.TemporaryDirectory() as d:
            repo = make_repo(Path(d))
            log = repo / "stream.jsonl"
            os.environ["STREAM_LOG"] = str(log)
            try:
                def boom(catalog, none_option):
                    raise RuntimeError("aufheben query exploded")

                thread = implement_host.fire_cassette_shadow(
                    repo, "wire the orders http endpoint", http_contract(), ["src/app.py"],
                    run_id="r-raise", aufheben_query=boom)
                thread.join(5)
                events = self._read_shadow_events(log, "r-raise")
                self.assertEqual(len(events), 1)
                self.assertEqual(events[0]["deterministic_pick"], ["integration"])   # LIVE pick survives
                self.assertEqual(events[0]["aufheben_pick"], [])                     # failed query -> empty
                self.assertIn("aufheben query exploded", events[0]["error"])
            finally:
                os.environ.pop("STREAM_LOG", None)

    def test_runner_launch_not_blocked_or_failed_by_shadow(self):
        # The implementer launch completes promptly and successfully even when the shadow query sleeps
        # AND raises — the carrier (LIVE lane) is never gated on the shadow.
        with tempfile.TemporaryDirectory() as d:
            repo = make_repo(Path(d))
            log = repo / "stream.jsonl"
            os.environ["STREAM_LOG"] = str(log)
            try:
                def slow_then_raise(catalog, none_option):
                    time.sleep(0.5)
                    raise RuntimeError("late failure")

                carrier = StubCarrier()
                runner = implement_host.make_implement_carrier_runner(
                    repo,
                    objective=raw_prompt(contract=http_contract(), objective="wire the orders http endpoint"),
                    contract_inputs={"aufheben-designer": http_contract()},
                    write_scope=["src/*.py"],
                    carrier=carrier,
                    aufheben_query=slow_then_raise,
                    run_id="r-runner",
                )
                t0 = time.monotonic()
                cr = runner(repo, "role prompt", "workspace-write", out_dir=repo / ".agent-runs" / "impl")
                elapsed = time.monotonic() - t0
                self.assertTrue(cr["ok"])                       # launch succeeded
                self.assertLess(elapsed, 0.4, "the launch must not wait on the shadow query")
                self.assertIn("NEVER stub or mock the production boundary", carrier.prompts[0])  # LIVE primed
            finally:
                os.environ.pop("STREAM_LOG", None)


if __name__ == "__main__":
    unittest.main(verbosity=2)
