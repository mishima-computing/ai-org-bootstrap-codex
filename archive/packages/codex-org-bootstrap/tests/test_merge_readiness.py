from __future__ import annotations

import argparse
import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
CHECKER_PATH = ROOT / "scripts" / "verify-merge-readiness.py"
FIXTURE_DIR = ROOT / "fixtures" / "merge-gate"

_SPEC = importlib.util.spec_from_file_location("verify_merge_readiness", CHECKER_PATH)
assert _SPEC is not None and _SPEC.loader is not None
CHECKER = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(CHECKER)


def fixture_args(case_name: str) -> argparse.Namespace:
    case_dir = FIXTURE_DIR / case_name
    return argparse.Namespace(
        record=case_dir / "readiness.json",
        diff=case_dir / "diff.patch",
        base=None,
        head=None,
    )


class MergeReadinessTests(unittest.TestCase):
    def test_real_readiness_passes(self) -> None:
        args = fixture_args("real-pr")
        result = CHECKER.verify_readiness(args.record, args)
        self.assertEqual(result["status"], "pass")
        self.assertIn("profile evidence skipped", result["profile_evidence"])

    def test_facade_readiness_blocks(self) -> None:
        args = fixture_args("facade-pr")
        with self.assertRaises(CHECKER.ReadinessError) as raised:
            CHECKER.verify_readiness(args.record, args)
        self.assertIn("diff_sha256 mismatch", str(raised.exception))

    def test_self_test_passes(self) -> None:
        code, payload = CHECKER.run_self_test()
        self.assertEqual(code, 0, payload)


if __name__ == "__main__":
    unittest.main()
