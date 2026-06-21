"""Tests for the Python-cored design-role host (PLAN A, ADR-0014).

Proves: GuardScan surfaces the structural guard with per-target pins; guard-map injection keeps the packet
SCHEMA-VALID for both the genius packet (repo_evidence items) and the design-proposal packet (string arrays);
the carrier_runner folds the guard-map into the prompt BEFORE the carrier runs, writes guard-map.json,
injects guard evidence into repo/result.json, and re-prompts deterministically on a schema-gate failure.
No Codex required — an injected stub carrier substitutes.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import design_host          # noqa: E402
import controller_output    # noqa: E402

REPO = Path(__file__).resolve().parent.parent
GENIUS_SCHEMA = REPO / "schemas" / "genius-packet.schema.json"
DESIGN_SCHEMA = REPO / "schemas" / "design-proposal.schema.json"


def make_clay_repo(tmp: Path) -> Path:
    clay = tmp / "cockpit" / "clay"
    clay.mkdir(parents=True)
    (clay / "index.html").write_text(
        "<html><body>\n<script src=\"seller-dashboard.js\"></script>\n"
        "<script src=\"clay-live.js\"></script>\n</body></html>\n", encoding="utf-8")
    (clay / "seller-dashboard.js").write_text(
        "window.SellerDashboard = {};\nfunction renderDashboardInto(el){ return el; }\n"
        "module.exports = { renderDashboardInto };\n", encoding="utf-8")
    (clay / "clay-live.js").write_text("window.TOWN = {};\n", encoding="utf-8")
    (clay / "seller-dashboard.test.js").write_text(
        "const indexHtml = fs.readFileSync(path.join(clayDir, 'index.html'), 'utf8');\n"
        "const sellerScriptIndex = indexHtml.indexOf('<script src=\"seller-dashboard.js\"></script>');\n"
        "assert.ok(clayLiveIndex > sellerScriptIndex, 'seller dashboard must load before clay-live');\n",
        encoding="utf-8")
    return tmp


def valid_genius():
    return {"role_id": "genius", "objective": "o", "substrate_inputs": [], "official_spec_evidence": [],
            "repo_evidence": [], "kept_hypotheses": [], "refuted_hypotheses": [], "unverified_hypotheses": [],
            "what_not_to_copy": [], "handoff_to_aufheben": "h"}


def valid_design(role="aggressive-designer"):
    return {"role_id": role, "objective": "o", "proposal_summary": "s", "recommended_direction": "d",
            "expected_benefits": [], "risks": [], "assumptions": [], "constraints": [], "things_to_avoid": [],
            "handoff_notes": "h", "confidence": {"overall_posture": "grounded", "grounded_claims": [],
                                                 "speculative_claims": []}}


class StubCarrier:
    def __init__(self, packets):
        self._packets = list(packets)   # each: dict (write+ok) | "TRANSPORT_FAIL"
        self.prompts = []
        self.calls = 0

    def __call__(self, rp, prompt, sandbox, *, timeout=600, retries=1, out_dir=None,
                 output_file=None, resume_session=None):
        self.prompts.append(prompt)
        self.calls += 1
        item = self._packets.pop(0) if self._packets else None
        if item == "TRANSPORT_FAIL" or item is None:
            return {"ok": False, "attempts": [{"attempt": 0, "ok": False}], "session_id": "s"}
        Path(output_file).write_text(json.dumps(item), encoding="utf-8")
        return {"ok": True, "attempts": [{"attempt": 0, "ok": True}], "session_id": "s1"}


class GuardScanTest(unittest.TestCase):
    def test_finds_guard_with_pins(self):
        with tempfile.TemporaryDirectory() as d:
            repo = make_clay_repo(Path(d))
            gm = design_host.GuardScan(repo, ["cockpit/clay/index.html"]).build()
            t = [x for x in gm["guarding_tests"] if x["test"].endswith("seller-dashboard.test.js")]
            self.assertTrue(t, f"expected the guarding test; got {gm['guarding_tests']}")
            pins = t[0]["pins"]
            self.assertTrue(any("indexOf" in p or "must load before" in p for p in pins),
                            f"expected the script-order pin via alias tracking; got {pins}")


class CarriageTest(unittest.TestCase):
    def test_genius_injection_stays_schema_valid(self):
        with tempfile.TemporaryDirectory() as d:
            repo = make_clay_repo(Path(d))
            gm = design_host.GuardScan(repo, ["cockpit/clay/index.html"]).build()
            pkt = design_host.inject_guard_evidence(valid_genius(), gm, ".agent-runs/x/guard-map.json", "genius")
            self.assertTrue(any(e.get("ref_type") == "run_artifact" for e in pkt["repo_evidence"]))
            v = controller_output.gate_output(json.dumps(pkt), str(GENIUS_SCHEMA))
            self.assertTrue(v["output_ok"], v.get("errors"))

    def test_designer_injection_stays_schema_valid(self):
        with tempfile.TemporaryDirectory() as d:
            repo = make_clay_repo(Path(d))
            gm = design_host.GuardScan(repo, ["cockpit/clay/index.html"]).build()
            pkt = design_host.inject_guard_evidence(valid_design(), gm, ".agent-runs/x/guard-map.json",
                                                    "aggressive-designer")
            self.assertTrue(len(pkt["things_to_avoid"]) >= 1)
            v = controller_output.gate_output(json.dumps(pkt), str(DESIGN_SCHEMA))
            self.assertTrue(v["output_ok"], v.get("errors"))


class OperabilityCarriageTest(unittest.TestCase):
    def test_injection_clamps_long_strings_and_stays_schema_valid(self):
        # a deep/vendored repo path would overflow continuity maxLength:200 — the injector must clamp it,
        # else determinism produces a packet the schema gate rejects every retry (the BLOCKER fix).
        op_map = {"summary": "s", "missing_safety_checks": [],
                  "continuity_prefill": {"version_constraints": ["x" * 300],
                                         "ecosystem_facts_used": ["inferred deliverable_kind=cli"],
                                         "forbidden_expansions": ["do not clobber " + "y" * 300],
                                         "missing_safety_checks": ["z" * 300]}}
        pkt = design_host.inject_operability_evidence(valid_design("conservative-designer"),
                                                      op_map, ".agent-runs/op.json")
        for f in design_host._CONTINUITY_FACTUAL:
            for item in pkt["continuity"][f]:
                self.assertLessEqual(len(item), 200)
        self.assertEqual(set(pkt["continuity"]),
                         set(design_host._CONTINUITY_FACTUAL) | set(design_host._CONTINUITY_JUDGMENT))
        self.assertTrue(controller_output.gate_output(json.dumps(pkt), str(DESIGN_SCHEMA))["output_ok"])


class RunnerTest(unittest.TestCase):
    def _run(self, repo, packets, objective="add a live chat view to the seller dashboard"):
        carrier = StubCarrier(packets)
        runner = design_host.make_design_carrier_runner(repo, "genius", str(GENIUS_SCHEMA), objective,
                                                        carrier=carrier)
        out_dir = Path(repo) / ".agent-runs" / "run1"
        cr = runner(repo, "## Contract\nthe objective", "read-only", timeout=5, retries=0, out_dir=out_dir)
        return carrier, cr, out_dir

    def test_endtoend_guard_folded_and_injected(self):
        with tempfile.TemporaryDirectory() as d:
            repo = make_clay_repo(Path(d))
            carrier, cr, out_dir = self._run(repo, [valid_genius()])
            self.assertTrue(cr["ok"])
            self.assertIn(design_host.GUARD_MAP_HEADER, carrier.prompts[0])      # guard folded BEFORE carrier
            self.assertIn("seller-dashboard.test.js", carrier.prompts[0])
            self.assertTrue((out_dir / "guard-map.json").is_file())             # full map on disk
            packet = json.loads((Path(repo) / "result.json").read_text())
            locs = [e.get("locator") for e in packet["repo_evidence"]]
            self.assertTrue(any("guard-map.json" in (l or "") for l in locs))   # injected, schema-valid
            self.assertTrue(controller_output.gate_output(json.dumps(packet), str(GENIUS_SCHEMA))["output_ok"])

    def test_schema_retry_then_valid(self):
        with tempfile.TemporaryDirectory() as d:
            repo = make_clay_repo(Path(d))
            bad = valid_genius(); del bad["handoff_to_aufheben"]               # schema-invalid
            carrier, cr, _ = self._run(repo, [bad, valid_genius()])
            self.assertTrue(cr["ok"])
            self.assertEqual(carrier.calls, 2)
            self.assertIn("REJECTED", carrier.prompts[1])                       # deterministic repair note

    def test_transport_fail_retries_then_not_ok(self):
        with tempfile.TemporaryDirectory() as d:
            repo = make_clay_repo(Path(d))
            carrier, cr, _ = self._run(repo, ["TRANSPORT_FAIL"])   # never recovers
            self.assertFalse(cr["ok"])                              # final: not ok (so it is never cached as success)
            self.assertEqual(carrier.calls, 3)                      # retried across max_attempts (absorbs transient empty)

    def test_empty_output_is_retried(self):
        with tempfile.TemporaryDirectory() as d:
            repo = make_clay_repo(Path(d))
            # first launch "succeeds" transport but writes no parseable packet, second is valid
            class EmptyThenValid(StubCarrier):
                def __call__(self, rp, prompt, sandbox, **kw):
                    self.prompts.append(prompt); self.calls += 1
                    if self.calls == 1:
                        Path(kw["output_file"]).write_text("not json", encoding="utf-8")
                        return {"ok": True, "attempts": [{"attempt": 0}], "session_id": "s"}
                    Path(kw["output_file"]).write_text(json.dumps(valid_genius()), encoding="utf-8")
                    return {"ok": True, "attempts": [{"attempt": 0}], "session_id": "s"}
            carrier = EmptyThenValid([])
            runner = design_host.make_design_carrier_runner(repo, "genius", str(GENIUS_SCHEMA),
                                                            "edit cockpit/clay/index.html", carrier=carrier)
            cr = runner(repo, "## Contract\no", "read-only", out_dir=Path(repo) / ".agent-runs" / "r")
            self.assertTrue(cr["ok"])
            self.assertEqual(carrier.calls, 2)


class ConservativeOperabilityTest(unittest.TestCase):
    def test_conservative_gets_operability_substrate_schema_valid(self):
        with tempfile.TemporaryDirectory() as d:
            repo = make_clay_repo(Path(d))
            carrier = StubCarrier([valid_design("conservative-designer")])
            runner = design_host.make_design_carrier_runner(repo, "conservative-designer", str(DESIGN_SCHEMA),
                                                            "make the cockpit deployable and operable", carrier=carrier)
            out_dir = Path(repo) / ".agent-runs" / "r"
            cr = runner(repo, "## Contract\no", "read-only", out_dir=out_dir)
            self.assertTrue(cr["ok"])
            # operability folded into the prompt BEFORE the carrier, and the full map written to disk
            self.assertIn(design_host.OPERABILITY_MAP_HEADER, carrier.prompts[0])
            self.assertTrue((out_dir / "operability-map.json").is_file())
            # the continuity block is present, has all 8 fields, factual ones deterministically filled
            packet = json.loads((Path(repo) / "result.json").read_text())
            cont = packet["continuity"]
            self.assertEqual(set(cont) >= set(design_host._CONTINUITY_FACTUAL) | set(design_host._CONTINUITY_JUDGMENT), True)
            self.assertTrue(cont["version_constraints"])             # deterministically filled
            self.assertTrue(any("deliverable_kind=" in f for f in cont["ecosystem_facts_used"]))
            # still schema-valid against the real design-proposal schema
            self.assertTrue(controller_output.gate_output(json.dumps(packet), str(DESIGN_SCHEMA))["output_ok"])

    def test_genius_and_aggressive_get_no_operability(self):
        with tempfile.TemporaryDirectory() as d:
            repo = make_clay_repo(Path(d))
            carrier = StubCarrier([valid_genius()])
            runner = design_host.make_design_carrier_runner(repo, "genius", str(GENIUS_SCHEMA), "x", carrier=carrier)
            runner(repo, "## Contract\no", "read-only", out_dir=Path(repo) / ".agent-runs" / "g")
            self.assertNotIn(design_host.OPERABILITY_MAP_HEADER, carrier.prompts[0])  # guard-only, unchanged


if __name__ == "__main__":
    unittest.main(verbosity=2)
