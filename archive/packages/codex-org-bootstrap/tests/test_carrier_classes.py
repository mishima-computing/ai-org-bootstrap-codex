"""Token-reduction levers: schema-output gate (producing carriers) + content-addressed cache."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))

import controller_output as output  # noqa: E402
import controller_cache as cache  # noqa: E402
import controller_workflow as workflow  # noqa: E402


def git(repo, *a):
    subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)


def _repo(tmp):
    r = Path(tmp)
    git(r, "init"); git(r, "config", "user.email", "t@t"); git(r, "config", "user.name", "t")
    (r / "seed.txt").write_text("s"); git(r, "add", "-A"); git(r, "commit", "-m", "s")
    return r


SCHEMA = {"type": "object", "required": ["verdict"], "additionalProperties": False,
          "properties": {"verdict": {"enum": ["pass", "fail"]}, "note": {"type": "string"}}}


class SchemaGateTests(unittest.TestCase):
    def test_self_test(self):
        self.assertEqual(output.self_test(), 0)

    def test_validate_basics(self):
        self.assertEqual(output.validate({"verdict": "pass"}, SCHEMA), [])
        self.assertTrue(output.validate({}, SCHEMA))                       # missing required
        self.assertTrue(output.validate({"verdict": "maybe"}, SCHEMA))    # bad enum
        self.assertTrue(output.validate({"verdict": "pass", "x": 1}, SCHEMA))  # additionalProperties
        self.assertTrue(output.validate({"verdict": 1}, SCHEMA))          # wrong type

    def test_gate_output_fail_closed_on_garbage(self):
        with tempfile.TemporaryDirectory() as d:
            sp = Path(d) / "s.json"; sp.write_text(json.dumps(SCHEMA))
            self.assertFalse(output.gate_output("not json at all", sp)["output_ok"])
            self.assertTrue(output.gate_output('{"verdict":"pass"}', sp)["output_ok"])
            self.assertFalse(output.gate_output('{"verdict":"nope"}', sp)["output_ok"])

    def test_workflow_blocks_on_invalid_output(self):
        with tempfile.TemporaryDirectory() as d:
            r = _repo(d)
            sp = r / "schema.json"; sp.write_text(json.dumps(SCHEMA))

            def stub(repo, prompt, sandbox, *, timeout, retries, out_dir, **_):
                (Path(repo) / "result.json").write_text('{"verdict": "WRONG"}')  # invalid enum
                return {"ok": True, "attempts": [{"attempt": 0, "exit": 0}]}
            contract = {"role": "linon", "prompt": "p", "sandbox": "read-only",
                        "timeout": 30, "retries": 0}
            rep = workflow.run_contract(r, contract, "og", carrier_runner=stub,
                                        include_builtin_gates=False, clock=lambda: 1,
                                        output_schema=str(sp), output_path="result.json")
            self.assertFalse(rep.ok)
            self.assertTrue(any("schema-output" in u for u in rep.unresolved_failures))


class CacheTests(unittest.TestCase):
    def test_cache_hit_skips_the_carrier(self):
        with tempfile.TemporaryDirectory() as d:
            r = _repo(d)
            contract = {"role": "implementer", "prompt": "p", "sandbox": "workspace-write",
                        "timeout": 30, "retries": 0, "files_allowed_to_change": ["out.txt"]}
            calls = {"n": 0}

            def stub(repo, prompt, sandbox, *, timeout, retries, out_dir, **_):
                calls["n"] += 1
                (Path(repo) / "out.txt").write_text("carrier output v1")
                return {"ok": True, "attempts": [{"attempt": 0, "exit": 0}]}

            # run 1: real carrier, result cached
            rep1 = workflow.run_contract(r, contract, "c1", carrier_runner=stub,
                                         include_builtin_gates=False, cache_enabled=True, clock=lambda: 1)
            self.assertTrue(rep1.ok)
            self.assertEqual(calls["n"], 1)

            # reset to the pre-run state (keep .agent-runs/ — the cache lives there; in real repos
            # it is gitignored so clean leaves it alone)
            git(r, "clean", "-fdq", "-e", ".agent-runs")
            self.assertFalse((r / "out.txt").exists())

            # run 2: cache hit → carrier MUST NOT be called; result replayed
            def boom(repo, prompt, sandbox, *, timeout, retries, out_dir, **_):
                raise AssertionError("carrier should not run on a cache hit")
            rep2 = workflow.run_contract(r, contract, "c2", carrier_runner=boom,
                                         include_builtin_gates=False, cache_enabled=True, clock=lambda: 1)
            self.assertTrue(rep2.ok)
            self.assertEqual(calls["n"], 1)                 # carrier not re-run
            self.assertEqual((r / "out.txt").read_text(encoding="utf-8"), "carrier output v1")  # replayed
            self.assertIn("out.txt", rep2.changed_files)

    def test_cache_miss_on_different_state(self):
        with tempfile.TemporaryDirectory() as d:
            r = _repo(d)
            contract = {"role": "implementer", "prompt": "p", "sandbox": "workspace-write",
                        "timeout": 30, "retries": 0, "files_allowed_to_change": ["out.txt"]}

            def stub(repo, prompt, sandbox, *, timeout, retries, out_dir, **_):
                (Path(repo) / "out.txt").write_text("v1")
                return {"ok": True, "attempts": [{"attempt": 0, "exit": 0}]}
            workflow.run_contract(r, contract, "m1", carrier_runner=stub,
                                  include_builtin_gates=False, cache_enabled=True, clock=lambda: 1)
            # change repo state (commit something) → state hash differs → cache must miss
            (r / "other.txt").write_text("z"); git(r, "add", "-A"); git(r, "commit", "-m", "x")
            git(r, "clean", "-fdq")
            calls = {"n": 0}

            def stub2(repo, prompt, sandbox, *, timeout, retries, out_dir, **_):
                calls["n"] += 1
                (Path(repo) / "out.txt").write_text("v2")
                return {"ok": True, "attempts": [{"attempt": 0, "exit": 0}]}
            workflow.run_contract(r, contract, "m2", carrier_runner=stub2,
                                  include_builtin_gates=False, cache_enabled=True, clock=lambda: 1)
            self.assertEqual(calls["n"], 1)  # different state → carrier ran (no false reuse)


class LinonFixTests(unittest.TestCase):
    def test_schema_unsupported_keyword_fails_closed(self):
        # allOf/if/then are used by real schemas (linon-review). With the full `jsonschema` library
        # present they are checked properly; WITHOUT it the minimal validator can't, and must fail closed
        # (not silently pass). Assert the right behavior for whichever is installed.
        try:
            import jsonschema  # noqa: F401
            has_full = True
        except ImportError:
            has_full = False
        with tempfile.TemporaryDirectory() as d:
            sp = Path(d) / "s.json"
            sp.write_text(json.dumps({"allOf": [{"type": "object"}]}))
            res = output.gate_output('{"x": 1}', sp)
            if has_full:
                self.assertTrue(res["output_ok"])      # jsonschema validates the conditional schema
                # the validator field now names the SELECTED draft class (validator_for the schema's $schema),
                # not the generic "jsonschema"; assert a real jsonschema validator was used (not "minimal").
                self.assertIn("Validator", res.get("validator", ""))
                self.assertNotEqual(res.get("validator"), "minimal")
            else:
                self.assertFalse(res["output_ok"])     # minimal validator fails closed on allOf

    def test_additional_properties_as_schema(self):
        sch = {"type": "object", "additionalProperties": {"type": "string"}}
        self.assertEqual(output.validate({"a": "ok"}, sch), [])
        self.assertTrue(output.validate({"a": 1}, sch))

    def test_cache_safe_rel(self):
        with tempfile.TemporaryDirectory() as d:
            r = Path(d)
            self.assertTrue(cache._safe_rel(r, "a/b.txt"))
            self.assertFalse(cache._safe_rel(r, "../escape.txt"))
            self.assertFalse(cache._safe_rel(r, "/etc/passwd"))

    def test_lookup_rejects_tampered_bundle(self):
        with tempfile.TemporaryDirectory() as d:
            r = _repo(d)
            (r / "out.txt").write_text("v1")
            snap = {}
            st = cache.state_hash(r, snap)
            key = cache.contract_key({"role": "implementer"}, st)
            cache.store(r, key, st, snap, {"ok": True, "attempts": []})
            bpath = r / ".agent-runs" / "controller" / "cache" / f"{key}.json"
            b = json.loads(bpath.read_text(encoding="utf-8"))
            b["changed"] = b["changed"] + ["sneaky.txt"]   # tamper without fixing manifest
            bpath.write_text(json.dumps(b))
            self.assertIsNone(cache.lookup(r, key, st))     # integrity check rejects it

    def test_replay_rejects_content_mismatch_and_rolls_back(self):
        with tempfile.TemporaryDirectory() as d:
            r = _repo(d)
            bundle = {"key": "k", "state_hash": "s", "changed": ["x.txt"],
                      "tracked_diff": "", "files": {"x.txt": "REAL CONTENT"},
                      "content_hashes": {"x.txt": "deadbeef"},  # wrong hash → poisoned
                      "carrier_result": {"ok": True, "attempts": []}}
            self.assertFalse(cache.replay(r, bundle, {}))    # content mismatch → miss
            self.assertFalse((r / "x.txt").exists())          # rolled back (no residue)

    def test_workflow_blocks_output_path_traversal(self):
        with tempfile.TemporaryDirectory() as d:
            r = _repo(d)
            sp = r / "schema.json"; sp.write_text(json.dumps(SCHEMA))

            def stub(repo, prompt, sandbox, *, timeout, retries, out_dir, **_):
                return {"ok": True, "attempts": [{"attempt": 0, "exit": 0}]}
            rep = workflow.run_contract(r, {"role": "linon", "prompt": "p", "sandbox": "read-only",
                                            "timeout": 30, "retries": 0},
                                        "tr", carrier_runner=stub, include_builtin_gates=False,
                                        clock=lambda: 1, output_schema=str(sp),
                                        output_path="../../../etc/hosts")
            self.assertFalse(rep.ok)
            self.assertTrue(any("escapes repo" in u for u in rep.unresolved_failures))


if __name__ == "__main__":
    unittest.main()
