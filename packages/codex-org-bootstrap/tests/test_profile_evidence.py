from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
FIXTURE_DIR = ROOT / "fixtures" / "profile-evidence"
CHECKER_PATH = ROOT / "scripts" / "profile-evidence-check.py"

_SPEC = importlib.util.spec_from_file_location("profile_evidence_check", CHECKER_PATH)
assert _SPEC is not None and _SPEC.loader is not None
CHECKER = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(CHECKER)


def run_case(case_name: str) -> list[str]:
    case_dir = FIXTURE_DIR / case_name
    proposal = case_dir / "proposal.json"
    return CHECKER.validate_paths(
        case_dir / "objective.json",
        case_dir / "contract.json",
        case_dir / "evidence.json",
        case_dir / "diff.patch",
        proposal if proposal.is_file() else None,
        allow_fixture_diff=True,
    )


class ProfileEvidenceTests(unittest.TestCase):
    def assert_case_rejected(self, case_name: str, *expected_messages: str) -> None:
        errors = run_case(case_name)
        self.assertTrue(errors, f"{case_name} unexpectedly passed")
        joined = "\n".join(errors)
        for expected in expected_messages:
            self.assertIn(expected, joined)

    def test_honest_rate_limiter_case_passes(self) -> None:
        self.assertEqual(run_case("honest-rate-limiter"), [])

    def test_angle9_polarity_case_passes(self) -> None:
        self.assertEqual(run_case("angle9-polarity"), [])

    def test_forged_objective_uncarded_profile_rejected(self) -> None:
        self.assert_case_rejected("forged-objective", "unauthorized uncarded profile")

    def test_forged_diff_rejected(self) -> None:
        self.assert_case_rejected("forged-diff", "fixture patch is not git apply --check clean")

    def test_content_irrelevant_semantics_are_delegated_to_linon(self) -> None:
        self.assertEqual(run_case("content-irrelevant"), [])

    def test_token_gaming_semantics_are_delegated_to_linon(self) -> None:
        self.assertEqual(run_case("token-gaming"), [])

    def test_vacuous_filler_semantics_are_delegated_to_linon(self) -> None:
        self.assertEqual(run_case("vacuous-filler"), [])

    def test_comment_evidence_rejected_by_structural_floor(self) -> None:
        self.assert_case_rejected("tokenstuff-comment", "not an added code-structure line")

    def test_bare_string_evidence_rejected_by_structural_floor(self) -> None:
        self.assert_case_rejected("angle6-degenerate", "not an added code-structure line")

    def test_misattributed_hunk_rejected(self) -> None:
        self.assert_case_rejected("angle8-misattrib", "lacks matching +++")

    def test_overlapping_hunk_rejected(self) -> None:
        self.assert_case_rejected("overlapping-hunk", "non-monotonic overlapping hunk")

    def test_partial_coverage_rejected(self) -> None:
        self.assert_case_rejected("partial-coverage", "has no backing evidence")

    def test_required_evidence_unmet_rejected(self) -> None:
        self.assert_case_rejected("required-evidence-unmet", "required_evidence 'security_review_report' not satisfied")

    def test_required_evidence_kind_cannot_reuse_line(self) -> None:
        self.assert_case_rejected("forged-security-kind", "cannot satisfy distinct required_evidence kinds")


if __name__ == "__main__":
    unittest.main()
