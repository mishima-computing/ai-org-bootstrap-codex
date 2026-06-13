from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "profile-evidence-check.py"


def load_script():
    spec = importlib.util.spec_from_file_location("profile_evidence_check", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ProfileEvidenceCheckTests(unittest.TestCase):
    def test_valid_triplet_passes(self) -> None:
        module = load_script()
        conservative, contract, implementation_result = module.valid_triplet()
        self.assertEqual(
            module.validate_profile_evidence(
                declared_profiles=["ui-retro-gamer", "ui-gacha-genre"],
                selector_profiles=[],
                conservative=conservative,
                contract=contract,
                implementation_result=implementation_result,
            ),
            [],
        )

    def test_missing_evidence_fails(self) -> None:
        module = load_script()
        conservative, contract, implementation_result = module.valid_triplet()
        implementation_result["implementation_evidence"] = []
        errors = module.validate_profile_evidence(
            declared_profiles=["ui-retro-gamer", "ui-gacha-genre"],
            selector_profiles=[],
            conservative=conservative,
            contract=contract,
            implementation_result=implementation_result,
        )
        self.assertTrue(any("missing evidence" in error for error in errors))

    def test_unknown_selected_profile_fails(self) -> None:
        module = load_script()
        conservative, contract, implementation_result = module.valid_triplet()
        conservative["continuity"]["selected_profiles"].append("not-forwarded-profile")
        errors = module.validate_profile_evidence(
            declared_profiles=["ui-retro-gamer", "ui-gacha-genre"],
            selector_profiles=[],
            conservative=conservative,
            contract=contract,
            implementation_result=implementation_result,
        )
        self.assertTrue(any("was not declared or selected" in error for error in errors))

    def test_selected_selector_profile_requires_contract_application(self) -> None:
        module = load_script()
        conservative, contract, implementation_result = module.valid_triplet()
        conservative["continuity"]["selected_profiles"] = ["ui-retro-gamer"]
        contract["profile_applications"] = []
        implementation_result["implementation_evidence"] = []
        errors = module.validate_profile_evidence(
            declared_profiles=[],
            selector_profiles=["ui-retro-gamer"],
            conservative=conservative,
            contract=contract,
            implementation_result=implementation_result,
        )
        self.assertTrue(any("missing selected profile 'ui-retro-gamer'" in error for error in errors))

    def test_contract_id_mismatch_fails(self) -> None:
        module = load_script()
        conservative, contract, implementation_result = module.valid_triplet()
        implementation_result["implementation_contract_id"] = "DIFFERENT-CONTRACT"
        errors = module.validate_profile_evidence(
            declared_profiles=["ui-retro-gamer", "ui-gacha-genre"],
            selector_profiles=[],
            conservative=conservative,
            contract=contract,
            implementation_result=implementation_result,
        )
        self.assertTrue(any("does not match contract.contract_id" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
