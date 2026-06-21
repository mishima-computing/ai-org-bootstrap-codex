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


if __name__ == "__main__":
    unittest.main(verbosity=2)
