#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any


VALIDATOR_PATH = Path(__file__).resolve().with_name("validate-bootstrap-pack.py")
_SPEC = importlib.util.spec_from_file_location("validate_bootstrap_pack", VALIDATOR_PATH)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"cannot import validator from {VALIDATOR_PATH}")
_VALIDATOR = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_VALIDATOR)

validate_schema_instance = _VALIDATOR.validate_schema_instance
SCHEMA_BY_AGENT = _VALIDATOR.SCHEMA_BY_AGENT
check_role_conditionals = _VALIDATOR.check_role_conditionals
ROOT = _VALIDATOR.ROOT

FORMAT_STATUS = "PROVISIONAL submit-result.v1"
SUPERSEDE_TRIGGER = "adoption of the #39 tool-I/O substrate"


def repair_error(field_path: str, actual: object, allowed: object, repair_hint: str) -> dict[str, object]:
    return {
        "field_path": field_path,
        "actual": actual,
        "allowed": allowed,
        "repair_hint": repair_hint,
    }


def validation_error_from_message(message: str, schema_rel: str | None = None) -> dict[str, object]:
    field_path, separator, detail = message.partition(": ")
    if not separator:
        field_path = "$"
        detail = message
    return repair_error(
        field_path=field_path,
        actual=detail,
        allowed=schema_rel or "selected agent schema",
        repair_hint="Return JSON that conforms to the selected schema and drop unsupported keys.",
    )


def parse_payload_error(message: str) -> dict[str, object]:
    return repair_error(
        field_path="$",
        actual=message,
        allowed="valid JSON payload",
        repair_hint="Return pure JSON with no Markdown fences or explanatory text.",
    )


def output_path_error(message: str) -> dict[str, object]:
    return repair_error(
        field_path="expected_output",
        actual=message,
        allowed=".agent-runs/<run>/... path",
        repair_hint="Use an output path contained under the run directory.",
    )


def write_error(message: str) -> dict[str, object]:
    return repair_error(
        field_path="expected_output",
        actual=message,
        allowed="writable run artifact path",
        repair_hint="Retry with a writable path under .agent-runs/ or let the controller write the artifact.",
    )


def load_schema(agent: str) -> tuple[dict[str, Any] | None, str | None, str | None]:
    schema_rel = SCHEMA_BY_AGENT.get(agent)
    if schema_rel is None:
        return None, None, f"unknown agent {agent!r}; expected one of {sorted(SCHEMA_BY_AGENT)}"
    schema_path = ROOT / schema_rel
    try:
        loaded = json.loads(schema_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return None, schema_rel, f"{schema_rel}: schema_parse_error: {exc}"
    if not isinstance(loaded, dict):
        return None, schema_rel, f"{schema_rel}: schema must be object"
    return loaded, schema_rel, None


def parse_payload(raw_payload: str) -> tuple[object | None, str | None]:
    try:
        return json.loads(raw_payload), None
    except Exception as exc:  # noqa: BLE001
        return None, f"payload_parse_error: {exc}"


def validate_payload(agent: str, instance: object) -> tuple[list[str], str | None]:
    schema, schema_rel, schema_error = load_schema(agent)
    if schema_error or schema is None:
        return [schema_error or "schema_load_error"], schema_rel

    errors = validate_schema_instance(schema, instance)
    errors.extend(check_role_conditionals(ROOT / schema_rel, instance))
    return errors, schema_rel


def contained_output_path(raw_output: str, root: Path = ROOT) -> tuple[Path | None, str | None]:
    runs_dir = (root / ".agent-runs").resolve()
    raw_path = Path(raw_output)
    target = raw_path if raw_path.is_absolute() else root / raw_path

    try:
        resolved_target = target.resolve(strict=False)
        resolved_target.relative_to(runs_dir)
    except Exception as exc:  # noqa: BLE001
        if isinstance(exc, ValueError):
            return None, f"{raw_output}: output path must resolve under .agent-runs/"
        return None, f"{raw_output}: output_path_error: {exc}"

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        resolved_parent = target.parent.resolve(strict=True)
        resolved_target = target.resolve(strict=False)
    except Exception as exc:  # noqa: BLE001
        return None, f"{raw_output}: output_path_error: {exc}"

    try:
        resolved_target.relative_to(runs_dir)
        resolved_parent.relative_to(runs_dir)
    except ValueError:
        return None, f"{raw_output}: output path must resolve under .agent-runs/"

    return resolved_target, None


def atomic_write_json(path: Path, instance: object) -> None:
    payload = json.dumps(instance, indent=2, ensure_ascii=False) + "\n"
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def status_payload(status: str, agent: str | None = None, schema: str | None = None, **extra: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "status": status,
        "format": FORMAT_STATUS,
        "supersede_trigger": SUPERSEDE_TRIGGER,
    }
    if agent is not None:
        payload["agent"] = agent
    if schema is not None:
        payload["schema"] = schema
    payload.update(extra)
    return payload


def print_status(payload: dict[str, object]) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def submit(agent: str, expected_output: str, raw_payload: str, root: Path = ROOT) -> int:
    instance, parse_error = parse_payload(raw_payload)
    if parse_error:
        print_status(status_payload("fail", agent=agent, errors=[parse_payload_error(parse_error)]))
        return 1

    errors, schema_rel = validate_payload(agent, instance)
    if errors:
        print_status(status_payload(
            "fail",
            agent=agent,
            schema=schema_rel,
            errors=[validation_error_from_message(item, schema_rel) for item in errors],
        ))
        return 1

    output_path, path_error = contained_output_path(expected_output, root=root)
    if path_error or output_path is None:
        print_status(status_payload(
            "fail",
            agent=agent,
            schema=schema_rel,
            errors=[output_path_error(path_error or "output_path_error")],
        ))
        return 1

    try:
        atomic_write_json(output_path, instance)
    except Exception as exc:  # noqa: BLE001
        print_status(status_payload("fail", agent=agent, schema=schema_rel, errors=[write_error(f"atomic_write_error: {exc}")]))
        return 1

    print_status(status_payload("pass", agent=agent, schema=schema_rel, expected_output=str(output_path)))
    return 0


def aggressive_pass_instance() -> dict[str, Any]:
    return {
        "role_id": "aggressive-designer",
        "objective": "example objective",
        "proposal_summary": "example summary",
        "recommended_direction": "example direction",
        "expected_benefits": [],
        "risks": [],
        "assumptions": [],
        "constraints": [],
        "things_to_avoid": [],
        "handoff_notes": "example handoff",
        "confidence": {
            "overall_posture": "grounded",
            "grounded_claims": [],
            "speculative_claims": [],
        },
    }


def conservative_instance() -> dict[str, Any]:
    instance = aggressive_pass_instance()
    instance["role_id"] = "conservative-designer"
    instance["continuity"] = {
        "selected_profiles": [],
        "version_constraints": [],
        "ecosystem_facts_used": [],
        "forbidden_expansions": [],
        "safe_change_path": "safe",
        "reversibility_plan": "reverse",
        "missing_safety_checks": [],
        "knowledge_gaps": [],
    }
    return instance


def implementation_contract_instance() -> dict[str, Any]:
    return {
        "role_id": "aufheben-designer",
        "contract_id": "IC-self-test",
        "objective": "example objective",
        "selected_direction": "example direction",
        "rejected_parts": [],
        "implementation_summary": "example summary",
        "acceptance_criteria": [],
        "profile_applications": [],
        "files_allowed_to_change": [],
        "files_not_allowed_to_change": [],
        "required_checks": [],
        "security_requirements": [],
        "nonfunctional_requirements": [],
        "non_goals": [],
        "risks": [],
        "fallback_plan": "example fallback",
        "handoff_to_implementer": "example handoff",
    }


def implementation_result_instance() -> dict[str, Any]:
    return {
        "role_id": "implementer",
        "implementation_contract_id": "IC-self-test",
        "summary": "example implementation result",
        "files_changed": [],
        "implementation_evidence": [],
        "commands_run": [],
        "command_results": [],
        "checks_passed": [],
        "checks_failed": [],
        "remaining_failures": [],
        "scope_deviations": [],
        "manual_followup": [],
    }


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _self_test_unknown_key() -> None:
    instance = implementation_contract_instance()
    instance["non_goals_note_removed"] = "drop this key"
    errors, _schema = validate_payload("aufheben-designer", instance)
    _assert(any("non_goals_note_removed" in item for item in errors), "unknown key error must name key")


def _self_test_cap_excess() -> None:
    instance = conservative_instance()
    instance["continuity"]["safe_change_path"] = "x" * 601
    errors, _schema = validate_payload("conservative-designer", instance)
    joined = "\n".join(errors)
    _assert("$.continuity.safe_change_path" in joined, "cap error must include JSON path")
    _assert("at most 600" in joined, "cap error must retain at most N wording")
    _assert("actual 601" in joined and "allowed 600" in joined, "cap error must include actual vs allowed")


def _self_test_missing_required_field() -> None:
    instance = conservative_instance()
    del instance["continuity"]["knowledge_gaps"]
    errors, _schema = validate_payload("conservative-designer", instance)
    _assert(
        any("$.continuity.knowledge_gaps: missing required field" == item for item in errors),
        "missing required field error must include field JSON path",
    )


def _self_test_contract_requires_profile_applications() -> None:
    instance = implementation_contract_instance()
    del instance["profile_applications"]
    errors, _schema = validate_payload("aufheben-designer", instance)
    _assert(
        any("$.profile_applications: missing required field" == item for item in errors),
        "implementation contract must require profile_applications",
    )


def _self_test_result_requires_implementation_evidence() -> None:
    instance = implementation_result_instance()
    del instance["implementation_evidence"]
    errors, _schema = validate_payload("implementer", instance)
    _assert(
        any("$.implementation_evidence: missing required field" == item for item in errors),
        "implementation result must require implementation_evidence",
    )


def _self_test_profile_application_requires_obligation_items() -> None:
    instance = implementation_contract_instance()
    instance["acceptance_criteria"] = ["observable profile obligation is implemented"]
    instance["profile_applications"] = [
        {
            "profile_id": "ui-retro-gamer",
            "source_proposal": "conservative-designer",
            "contract_obligations": [],
            "acceptance_criteria_refs": [0],
            "required_evidence": ["file and check evidence"],
        }
    ]
    errors, _schema = validate_payload("aufheben-designer", instance)
    _assert(
        any("$.profile_applications[0].contract_obligations: at least 1 items" in item for item in errors),
        "profile application obligations must not be empty",
    )


def _self_test_profile_application_refs_acceptance_criteria() -> None:
    instance = implementation_contract_instance()
    instance["acceptance_criteria"] = ["observable profile obligation is implemented"]
    instance["profile_applications"] = [
        {
            "profile_id": "ui-retro-gamer",
            "source_proposal": "conservative-designer",
            "contract_obligations": ["Implement pointerdown feedback as visible state."],
            "acceptance_criteria_refs": [1],
            "required_evidence": ["file and check evidence"],
        }
    ]
    errors, _schema = validate_payload("aufheben-designer", instance)
    _assert(
        any("criterion index 1 out of range" in item for item in errors),
        "profile application refs must point to existing acceptance criteria",
    )


def _self_test_passing_instance_and_atomic_write() -> None:
    import contextlib
    import io
    import tempfile as _tempfile

    instance = aggressive_pass_instance()
    errors, _schema = validate_payload("aggressive-designer", instance)
    _assert(errors == [], f"passing instance should validate: {errors!r}")

    with _tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        out = root / ".agent-runs" / "self-test" / "result.json"
        with contextlib.redirect_stdout(io.StringIO()):
            exit_code = submit("aggressive-designer", str(out), json.dumps(instance), root=root)
        _assert(exit_code == 0, "submit should pass")
        _assert(json.loads(out.read_text(encoding="utf-8")) == instance, "submit should write expected JSON")


def _self_test_path_containment() -> None:
    import tempfile as _tempfile

    with _tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        outside = root / "outside.json"
        target, error = contained_output_path(str(outside), root=root)
        _assert(target is None, "outside path must be rejected")
        _assert(error is not None and ".agent-runs" in error, "outside path error must name .agent-runs")

        external = root / "external"
        external.mkdir()
        symlink = root / ".agent-runs" / "link"
        symlink.parent.mkdir()
        symlink.symlink_to(external, target_is_directory=True)
        target, error = contained_output_path(str(symlink / "result.json"), root=root)
        _assert(target is None, "symlink escape must be rejected")
        _assert(error is not None and ".agent-runs" in error, "symlink escape error must name .agent-runs")


def _self_test_emitted_validation_error_shape() -> None:
    import contextlib
    import io
    import tempfile as _tempfile

    with _tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        out = root / ".agent-runs" / "self-test" / "result.json"
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = submit("aggressive-designer", str(out), "{not-json", root=root)
        _assert(exit_code == 1, "invalid JSON submit should fail")
        payload = json.loads(stdout.getvalue())
        error = payload["errors"][0]
        _assert(
            set(error) == {"field_path", "actual", "allowed", "repair_hint"},
            "validation error must use repair object shape",
        )
        _assert(error["field_path"] == "$", "parse error path must point at payload root")


def run_self_tests() -> int:
    cases = [
        ("unknown_key_tombstone", _self_test_unknown_key),
        ("cap_excess_actual_vs_allowed", _self_test_cap_excess),
        ("missing_required_field_path", _self_test_missing_required_field),
        ("contract_requires_profile_applications", _self_test_contract_requires_profile_applications),
        ("result_requires_implementation_evidence", _self_test_result_requires_implementation_evidence),
        ("profile_application_requires_obligation_items", _self_test_profile_application_requires_obligation_items),
        ("profile_application_refs_acceptance_criteria", _self_test_profile_application_refs_acceptance_criteria),
        ("passing_instance_and_atomic_write", _self_test_passing_instance_and_atomic_write),
        ("path_containment", _self_test_path_containment),
        ("emitted_validation_error_shape", _self_test_emitted_validation_error_shape),
    ]
    results = []
    for name, test in cases:
        try:
            test()
        except AssertionError as exc:
            results.append({"name": name, "status": "fail", "error": str(exc)})
        else:
            results.append({"name": name, "status": "pass"})

    failed = [item for item in results if item["status"] != "pass"]
    print_status(status_payload("pass" if not failed else "fail", cases=results))
    return 0 if not failed else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate and atomically write a role result artifact.")
    parser.add_argument("--agent", help="Agent id; selects schema through SCHEMA_BY_AGENT.")
    parser.add_argument("--expected-output", "--out", dest="expected_output", help="Destination result.json under .agent-runs/.")
    parser.add_argument("--json", dest="json_payload", help="JSON object payload. If omitted, payload is read from stdin.")
    parser.add_argument("--self-test", action="store_true", help="Run submit-result self-tests.")
    args = parser.parse_args(argv)

    if args.self_test:
        return run_self_tests()

    if not args.agent or not args.expected_output:
        parser.error("--agent and --expected-output are required unless --self-test is used")

    raw_payload = args.json_payload
    if raw_payload is None:
        raw_payload = sys.stdin.read()
    if not raw_payload.strip():
        print_status(status_payload(
            "fail",
            agent=args.agent,
            errors=[parse_payload_error("payload_missing: provide --json or stdin JSON")],
        ))
        return 1

    return submit(args.agent, args.expected_output, raw_payload)


if __name__ == "__main__":
    sys.exit(main())
