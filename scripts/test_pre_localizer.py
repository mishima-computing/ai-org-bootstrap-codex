"""Tests for the deterministic PreLocalizer (PLAN A, ADR-0014).

The load-bearing case: an objective with NO literal path ("add a live chat view to the seller dashboard")
must still surface cockpit/clay/index.html via reference-graph propagation, so GuardScan can reach the
structural guard that pins it.
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pre_localizer  # noqa: E402


def make_clay_repo(tmp: Path) -> Path:
    clay = tmp / "cockpit" / "clay"
    clay.mkdir(parents=True)
    (clay / "index.html").write_text(
        "<html><body>\n<script src=\"seller-dashboard.js\"></script>\n"
        "<script src=\"clay-live.js\"></script>\n</body></html>\n", encoding="utf-8")
    (clay / "seller-dashboard.js").write_text(
        "(function(g){ window.SellerDashboard = {};\n"
        "  function renderDashboardInto(el){ return el; }\n"
        "  if (typeof module==='object') module.exports = { renderDashboardInto };\n})(window);\n",
        encoding="utf-8")
    (clay / "clay-live.js").write_text("window.TOWN = {};\nfunction buildLayout(c){ return c; }\n", encoding="utf-8")
    (clay / "seller-dashboard.test.js").write_text(
        "const indexHtml = fs.readFileSync(path.join(clayDir, 'index.html'), 'utf8');\n"
        "const sellerScriptIndex = indexHtml.indexOf('<script src=\"seller-dashboard.js\"></script>');\n"
        "assert.ok(clayLiveIndex > sellerScriptIndex, 'seller dashboard must load before clay-live');\n",
        encoding="utf-8")
    (tmp / "unrelated").mkdir()
    (tmp / "unrelated" / "billing.py").write_text("def charge(): pass\n", encoding="utf-8")
    return tmp


class PreLocalizerTest(unittest.TestCase):
    def test_no_literal_path_surfaces_index_via_propagation(self):
        with tempfile.TemporaryDirectory() as d:
            repo = make_clay_repo(Path(d))
            cands = pre_localizer.PreLocalizer(repo).candidates("add a live chat view to the seller dashboard")
            paths = [c.path for c in cands]
            self.assertIn("cockpit/clay/seller-dashboard.js", paths)   # filename token + symbol
            self.assertIn("cockpit/clay/index.html", paths,            # reference-graph propagation
                          f"index.html must surface via propagation; got {paths}")
            self.assertNotIn("unrelated/billing.py", paths)            # no spurious match
            idx = next(c for c in cands if c.path == "cockpit/clay/index.html")
            self.assertTrue(any("references" in r or "used-by" in r for r in idx.reasons), idx.reasons)

    def test_literal_path_objective(self):
        with tempfile.TemporaryDirectory() as d:
            repo = make_clay_repo(Path(d))
            cands = pre_localizer.PreLocalizer(repo).candidates("edit cockpit/clay/clay-live.js")
            top = cands[0]
            self.assertEqual(top.path, "cockpit/clay/clay-live.js")
            self.assertTrue(any("literal" in r for r in top.reasons))

    def test_resolve_ref_directory_index(self):
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d)
            (repo / "src" / "foo").mkdir(parents=True)
            (repo / "src" / "app.js").write_text("import './foo';\n", encoding="utf-8")
            (repo / "src" / "foo" / "index.tsx").write_text("export const Foo = 1;\n", encoding="utf-8")
            idx = pre_localizer.RepoIndex.build(repo)
            self.assertEqual(idx.refs_out.get("src/app.js"), {"src/foo/index.tsx"})  # dir import -> index.*

    def test_deterministic(self):
        with tempfile.TemporaryDirectory() as d:
            repo = make_clay_repo(Path(d))
            a = [(c.path, c.score) for c in pre_localizer.PreLocalizer(repo).candidates("seller dashboard chat")]
            b = [(c.path, c.score) for c in pre_localizer.PreLocalizer(repo).candidates("seller dashboard chat")]
            self.assertEqual(a, b)


class DefectLocusReLocalizationTest(unittest.TestCase):
    """R4: on a repair the failing region (defect_locus) re-ranks the advisory candidates toward it."""

    OBJ = "add a live chat view to the seller dashboard"

    def test_locus_file_ranks_top(self):
        # (a) a defect_locus naming file X promotes X to the TOP candidate, even when X is NOT the
        # objective's natural top hit (clay-live.js loses to seller-dashboard.js without a locus).
        with tempfile.TemporaryDirectory() as d:
            repo = make_clay_repo(Path(d))
            pl = pre_localizer.PreLocalizer(repo)
            no_locus = [c.path for c in pl.candidates(self.OBJ)]
            self.assertNotEqual(no_locus[0], "cockpit/clay/clay-live.js")   # not naturally top
            with_locus = pl.candidates(self.OBJ, defect_locus={"file": "cockpit/clay/clay-live.js"})
            self.assertEqual(with_locus[0].path, "cockpit/clay/clay-live.js")
            self.assertTrue(any("defect locus" in r for r in with_locus[0].reasons), with_locus[0].reasons)

    def test_low_ranked_file_materially_promoted(self):
        # (b) a locus pointing at a file the objective did NOT surface at all (billing.py, score 0)
        # promotes it into — and to the top of — the candidate set.
        with tempfile.TemporaryDirectory() as d:
            repo = make_clay_repo(Path(d))
            pl = pre_localizer.PreLocalizer(repo)
            self.assertNotIn("unrelated/billing.py", [c.path for c in pl.candidates(self.OBJ)])
            promoted = pl.candidates(self.OBJ, defect_locus={"file": "unrelated/billing.py"})
            self.assertEqual(promoted[0].path, "unrelated/billing.py")

    def test_no_locus_is_byte_for_byte_identical(self):
        # (c) no locus (first attempt) -> candidates identical to the no-argument call; the new code path
        # is fully gated behind a non-None defect_locus.
        with tempfile.TemporaryDirectory() as d:
            repo = make_clay_repo(Path(d))
            pl = pre_localizer.PreLocalizer(repo)
            base = [(c.path, c.score, tuple(c.reasons)) for c in pl.candidates(self.OBJ)]
            none_locus = [(c.path, c.score, tuple(c.reasons))
                          for c in pl.candidates(self.OBJ, defect_locus=None)]
            empty_locus = [(c.path, c.score, tuple(c.reasons))
                           for c in pl.candidates(self.OBJ, defect_locus={})]
            self.assertEqual(base, none_locus)
            self.assertEqual(base, empty_locus)

    def test_locus_neighbors_and_symbols_promoted_via_reference_graph(self):
        # the locus reuses the reference graph (neighbours of X) and symbol_to_paths (named symbols).
        with tempfile.TemporaryDirectory() as d:
            repo = make_clay_repo(Path(d))
            pl = pre_localizer.PreLocalizer(repo)
            cands = pl.candidates(self.OBJ, defect_locus={
                "file": "cockpit/clay/index.html",                 # references both clay JS files
                "symbols": ["buildLayout"],                        # declared in clay-live.js
            })
            by_path = {c.path: c for c in cands}
            self.assertIn("cockpit/clay/index.html", by_path)
            # index.html references seller-dashboard.js + clay-live.js -> both get the neighbour bonus.
            self.assertTrue(any("near defect locus" in r
                                for r in by_path["cockpit/clay/seller-dashboard.js"].reasons),
                            by_path["cockpit/clay/seller-dashboard.js"].reasons)
            self.assertTrue(any("defect locus symbol" in r
                                for r in by_path["cockpit/clay/clay-live.js"].reasons),
                            by_path["cockpit/clay/clay-live.js"].reasons)

    def test_unknown_locus_file_is_advisory_noop(self):
        # a locus naming a file not in the index has no effect (advisory) — equal to no locus.
        with tempfile.TemporaryDirectory() as d:
            repo = make_clay_repo(Path(d))
            pl = pre_localizer.PreLocalizer(repo)
            base = [(c.path, c.score) for c in pl.candidates(self.OBJ)]
            ghost = [(c.path, c.score)
                     for c in pl.candidates(self.OBJ, defect_locus={"file": "does/not/exist.js"})]
            self.assertEqual(base, ghost)


if __name__ == "__main__":
    unittest.main(verbosity=2)
