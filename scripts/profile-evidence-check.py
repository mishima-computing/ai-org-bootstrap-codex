#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path
from typing import Any


def load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def profile_list(value: object, path: str, errors: list[str]) -> list[str]:
    if not isinstance(value, list):
        errors.append(f"{path}: expected array")
        return []
    result: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item:
            errors.append(f"{path}[{index}]: expected non-empty string")
            continue
        result.append(item)
    return result


def selected_profiles(conservative: object, errors: list[str]) -> list[str]:
    if not isinstance(conservative, dict):
        errors.append("conservative: expected object")
        return []
    continuity = conservative.get("continuity")
    if not isinstance(continuity, dict):
        errors.append("conservative.continuity: expected object")
        return []
    return profile_list(continuity.get("selected_profiles"), "conservative.continuity.selected_profiles", errors)


def profile_applications(contract: object, errors: list[str]) -> list[dict[str, Any]]:
    if not isinstance(contract, dict):
        errors.append("contract: expected object")
        return []
    raw = contract.get("profile_applications")
    if not isinstance(raw, list):
        errors.append("contract.profile_applications: expected array")
        return []
    applications: list[dict[str, Any]] = []
    acceptance = contract.get("acceptance_criteria")
    acceptance_len = len(acceptance) if isinstance(acceptance, list) else 0
    for app_index, application in enumerate(raw):
        if not isinstance(application, dict):
            errors.append(f"contract.profile_applications[{app_index}]: expected object")
            continue
        profile_id = application.get("profile_id")
        if not isinstance(profile_id, str) or not profile_id:
            errors.append(f"contract.profile_applications[{app_index}].profile_id: expected non-empty string")
        obligations = application.get("contract_obligations")
        if not isinstance(obligations, list) or not obligations:
            errors.append(f"contract.profile_applications[{app_index}].contract_obligations: expected non-empty array")
        else:
            for obligation_index, obligation in enumerate(obligations):
                if not isinstance(obligation, str) or not obligation:
                    errors.append(
                        f"contract.profile_applications[{app_index}].contract_obligations[{obligation_index}]: "
                        "expected non-empty string"
                    )
        refs = application.get("acceptance_criteria_refs")
        if not isinstance(refs, list) or not refs:
            errors.append(f"contract.profile_applications[{app_index}].acceptance_criteria_refs: expected non-empty array")
        else:
            for ref_index, ref in enumerate(refs):
                if not isinstance(ref, int) or isinstance(ref, bool):
                    errors.append(
                        f"contract.profile_applications[{app_index}].acceptance_criteria_refs[{ref_index}]: "
                        "expected integer"
                    )
                elif ref < 0 or ref >= acceptance_len:
                    errors.append(
                        f"contract.profile_applications[{app_index}].acceptance_criteria_refs[{ref_index}]: "
                        f"criterion index {ref} out of range for acceptance_criteria length {acceptance_len}"
                    )
        applications.append(application)
    return applications


def implementation_evidence(result: object, errors: list[str]) -> list[dict[str, Any]]:
    if not isinstance(result, dict):
        errors.append("implementation_result: expected object")
        return []
    raw = result.get("implementation_evidence")
    if not isinstance(raw, list):
        errors.append("implementation_result.implementation_evidence: expected array")
        return []
    evidence: list[dict[str, Any]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            errors.append(f"implementation_result.implementation_evidence[{index}]: expected object")
            continue
        for key in ["profile_id", "obligation", "evidence_ref", "verification"]:
            value = item.get(key)
            if not isinstance(value, str) or not value:
                errors.append(f"implementation_result.implementation_evidence[{index}].{key}: expected non-empty string")
        evidence.append(item)
    return evidence


def validate_profile_evidence(
    *,
    declared_profiles: list[str],
    selector_profiles: list[str],
    conservative: object,
    contract: object,
    implementation_result: object,
) -> list[str]:
    errors: list[str] = []
    declared = set(declared_profiles)
    allowed = declared | set(selector_profiles)
    selected = selected_profiles(conservative, errors)
    selected_set = set(selected)

    for profile_id in sorted(declared - selected_set):
        errors.append(f"conservative.continuity.selected_profiles: missing declared profile {profile_id!r}")

    for profile_id in sorted(selected_set - allowed):
        errors.append(f"conservative.continuity.selected_profiles: profile {profile_id!r} was not declared or selected")

    if isinstance(contract, dict) and isinstance(implementation_result, dict):
        contract_id = contract.get("contract_id")
        result_contract_id = implementation_result.get("implementation_contract_id")
        if not isinstance(contract_id, str) or not contract_id:
            errors.append("contract.contract_id: expected non-empty string")
        if not isinstance(result_contract_id, str) or not result_contract_id:
            errors.append("implementation_result.implementation_contract_id: expected non-empty string")
        if isinstance(contract_id, str) and isinstance(result_contract_id, str) and contract_id != result_contract_id:
            errors.append(
                "implementation_result.implementation_contract_id: "
                f"{result_contract_id!r} does not match contract.contract_id {contract_id!r}"
            )

    applications = profile_applications(contract, errors)
    app_profiles = {
        application.get("profile_id")
        for application in applications
        if isinstance(application.get("profile_id"), str) and application.get("profile_id")
    }

    for profile_id in sorted(app_profiles - selected_set):
        errors.append(f"contract.profile_applications: profile {profile_id!r} is not in conservative selected_profiles")

    for profile_id in sorted(selected_set - app_profiles):
        errors.append(f"contract.profile_applications: missing selected profile {profile_id!r}")

    required_pairs: set[tuple[str, str]] = set()
    for app_index, application in enumerate(applications):
        profile_id = application.get("profile_id")
        obligations = application.get("contract_obligations")
        if not isinstance(profile_id, str) or not isinstance(obligations, list):
            continue
        for obligation in obligations:
            if isinstance(obligation, str) and obligation:
                required_pairs.add((profile_id, obligation))
            else:
                errors.append(f"contract.profile_applications[{app_index}]: obligation cannot be used as evidence key")

    evidence = implementation_evidence(implementation_result, errors)
    evidence_pairs = {
        (item.get("profile_id"), item.get("obligation"))
        for item in evidence
        if isinstance(item.get("profile_id"), str) and isinstance(item.get("obligation"), str)
    }

    for profile_id, obligation in sorted(required_pairs - evidence_pairs):
        errors.append(f"implementation_result.implementation_evidence: missing evidence for {profile_id!r}: {obligation!r}")

    for profile_id, obligation in sorted(evidence_pairs - required_pairs):
        errors.append(f"implementation_result.implementation_evidence: evidence has no contract obligation {profile_id!r}: {obligation!r}")

    return errors


def run_check(args: argparse.Namespace) -> tuple[int, dict[str, object]]:
    declared = args.declared_profile or []
    selector = args.selector_profile or []
    errors: list[str] = []
    try:
        conservative = load_json(Path(args.conservative))
        contract = load_json(Path(args.contract))
        implementation_result = load_json(Path(args.implementation_result))
    except Exception as exc:  # noqa: BLE001
        return 2, {"status": "error", "errors": [f"input_load_error: {exc}"]}

    errors.extend(validate_profile_evidence(
        declared_profiles=declared,
        selector_profiles=selector,
        conservative=conservative,
        contract=contract,
        implementation_result=implementation_result,
    ))
    status = "pass" if not errors else "fail"
    return (0 if not errors else 1), {
        "status": status,
        "declared_profiles": declared,
        "selector_profiles": selector,
        "errors": errors,
    }


def write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def valid_triplet() -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    conservative = {
        "role_id": "conservative-designer",
        "objective": "Build a gacha screen with retro feel.",
        "proposal_summary": "Use declared UI profiles as concrete implementation obligations.",
        "recommended_direction": "Static web demo with event/state feedback.",
        "expected_benefits": [],
        "risks": [],
        "assumptions": [],
        "constraints": [],
        "things_to_avoid": [],
        "handoff_notes": "Convert profile obligations into contract profile_applications.",
        "confidence": {"overall_posture": "grounded", "grounded_claims": [], "speculative_claims": []},
        "continuity": {
            "selected_profiles": ["ui-retro-gamer", "ui-gacha-genre"],
            "version_constraints": [],
            "ecosystem_facts_used": [],
            "forbidden_expansions": [],
            "safe_change_path": "Use existing static web surface.",
            "reversibility_plan": "Remove demo files.",
            "missing_safety_checks": [],
            "knowledge_gaps": [],
        },
    }
    contract = {
        "role_id": "aufheben-designer",
        "contract_id": "IC-profile-evidence-self-test",
        "objective": "Build a gacha screen with retro feel.",
        "selected_direction": "Implement profile-backed gacha interactions.",
        "rejected_parts": [],
        "implementation_summary": "Fixture contract.",
        "acceptance_criteria": [
            "Pointerdown feedback is visible.",
            "Reveal ceremony separates rarity and item identity.",
        ],
        "profile_applications": [
            {
                "profile_id": "ui-retro-gamer",
                "source_proposal": "conservative-designer",
                "contract_obligations": ["Pointerdown feedback is visible."],
                "acceptance_criteria_refs": [0],
                "required_evidence": ["implementation_evidence entry with file reference"],
            },
            {
                "profile_id": "ui-gacha-genre",
                "source_proposal": "conservative-designer",
                "contract_obligations": ["Reveal ceremony separates rarity and item identity."],
                "acceptance_criteria_refs": [1],
                "required_evidence": ["implementation_evidence entry with file reference"],
            },
        ],
        "files_allowed_to_change": ["index.html", "styles.css", "app.js"],
        "files_not_allowed_to_change": [],
        "required_checks": [],
        "security_requirements": [],
        "nonfunctional_requirements": [],
        "non_goals": [],
        "risks": [],
        "fallback_plan": "Remove demo files.",
        "handoff_to_implementer": "Implement exactly the profiled obligations.",
    }
    implementation_result = {
        "role_id": "implementer",
        "implementation_contract_id": "IC-profile-evidence-self-test",
        "summary": "Fixture result.",
        "files_changed": ["index.html", "styles.css", "app.js"],
        "implementation_evidence": [
            {
                "profile_id": "ui-retro-gamer",
                "obligation": "Pointerdown feedback is visible.",
                "evidence_ref": "app.js:10",
                "verification": "manual DOM interaction",
            },
            {
                "profile_id": "ui-gacha-genre",
                "obligation": "Reveal ceremony separates rarity and item identity.",
                "evidence_ref": "app.js:40",
                "verification": "manual DOM interaction",
            },
        ],
        "commands_run": [],
        "command_results": [],
        "checks_passed": [],
        "checks_failed": [],
        "remaining_failures": [],
        "scope_deviations": [],
        "manual_followup": [],
    }
    return conservative, contract, implementation_result


def self_test_case(name: str, mutate: Any, expected_error: str | None) -> dict[str, object]:
    conservative, contract, implementation_result = valid_triplet()
    mutate(conservative, contract, implementation_result)
    errors = validate_profile_evidence(
        declared_profiles=["ui-retro-gamer", "ui-gacha-genre"],
        selector_profiles=[],
        conservative=conservative,
        contract=contract,
        implementation_result=implementation_result,
    )
    if expected_error is None:
        passed = not errors
    else:
        passed = any(expected_error in error for error in errors)
    return {"name": name, "status": "pass" if passed else "fail", "errors": errors}


def run_self_tests() -> int:
    cases = [
        self_test_case("valid_triplet", lambda *_: None, None),
        self_test_case(
            "missing_declared_selected_profile",
            lambda conservative, _contract, _result: conservative["continuity"]["selected_profiles"].remove("ui-retro-gamer"),
            "missing declared profile 'ui-retro-gamer'",
        ),
        self_test_case(
            "unknown_contract_profile",
            lambda _conservative, contract, _result: contract["profile_applications"][0].update({"profile_id": "not-forwarded-profile"}),
            "is not in conservative selected_profiles",
        ),
        self_test_case(
            "missing_implementation_evidence",
            lambda _conservative, _contract, result: result.update({"implementation_evidence": []}),
            "missing evidence for 'ui-gacha-genre'",
        ),
        self_test_case(
            "extra_implementation_evidence",
            lambda _conservative, _contract, result: result["implementation_evidence"].append({
                "profile_id": "ui-retro-gamer",
                "obligation": "Uncontracted flourish.",
                "evidence_ref": "app.js:99",
                "verification": "manual",
            }),
            "evidence has no contract obligation",
        ),
        self_test_case(
            "contract_id_mismatch",
            lambda _conservative, _contract, result: result.update({"implementation_contract_id": "DIFFERENT-CONTRACT"}),
            "does not match contract.contract_id",
        ),
    ]
    conservative, contract, implementation_result = valid_triplet()
    conservative["continuity"]["selected_profiles"] = ["ui-retro-gamer"]
    contract["profile_applications"] = []
    implementation_result["implementation_evidence"] = []
    errors = validate_profile_evidence(
        declared_profiles=[],
        selector_profiles=[],
        conservative=conservative,
        contract=contract,
        implementation_result=implementation_result,
    )
    cases.append({
        "name": "unauthorized_profile_when_allowed_empty",
        "status": "pass" if any("was not declared or selected" in error for error in errors) else "fail",
        "errors": errors,
    })
    conservative, contract, implementation_result = valid_triplet()
    conservative["continuity"]["selected_profiles"] = ["ui-retro-gamer"]
    contract["profile_applications"] = []
    implementation_result["implementation_evidence"] = []
    errors = validate_profile_evidence(
        declared_profiles=[],
        selector_profiles=["ui-retro-gamer"],
        conservative=conservative,
        contract=contract,
        implementation_result=implementation_result,
    )
    cases.append({
        "name": "selector_profile_requires_contract_application",
        "status": "pass" if any("missing selected profile 'ui-retro-gamer'" in error for error in errors) else "fail",
        "errors": errors,
    })
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        conservative, contract, implementation_result = valid_triplet()
        write_json(root / "conservative.json", conservative)
        write_json(root / "contract.json", contract)
        write_json(root / "implementation-result.json", implementation_result)
        args = argparse.Namespace(
            declared_profile=["ui-retro-gamer", "ui-gacha-genre"],
            selector_profile=[],
            conservative=str(root / "conservative.json"),
            contract=str(root / "contract.json"),
            implementation_result=str(root / "implementation-result.json"),
        )
        exit_code, payload = run_check(args)
        cases.append({
            "name": "cli_valid_triplet",
            "status": "pass" if exit_code == 0 and payload["status"] == "pass" else "fail",
            "errors": payload.get("errors", []),
        })

    failed = [case for case in cases if case["status"] != "pass"]
    print(json.dumps({"status": "pass" if not failed else "fail", "cases": cases}, indent=2, ensure_ascii=False))
    return 0 if not failed else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate UI profile propagation from designer to contract to evidence.")
    parser.add_argument("--declared-profile", action="append", default=[])
    parser.add_argument("--selector-profile", action="append", default=[])
    parser.add_argument("--conservative")
    parser.add_argument("--contract")
    parser.add_argument("--implementation-result")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        return run_self_tests()

    required = [args.conservative, args.contract, args.implementation_result]
    if not all(required):
        parser.error("--conservative, --contract, and --implementation-result are required unless --self-test is used")

    exit_code, payload = run_check(args)
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
