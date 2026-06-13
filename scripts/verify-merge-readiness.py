#!/usr/bin/env python3
"""Content-bound merge readiness gate.

The readiness record carries the non-deterministic Linon verdict. This gate binds
that verdict to the actual PR diff and re-runs deterministic provenance gates.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
FIXTURES_DIR = ROOT / "fixtures" / "merge-gate"
PACKET_VERIFIER = ROOT / "scripts" / "verify-linon-packet.py"
PROFILE_CHECKER = ROOT / "scripts" / "profile-evidence-check.py"
LINON_REVIEW_SCHEMA = ROOT / "schemas" / "linon-review.schema.json"


class ReadinessError(Exception):
    pass


def repo_path(raw: str, root: Path = ROOT) -> Path:
    path = Path(raw)
    return path if path.is_absolute() else root / path


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ReadinessError(f"cannot read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ReadinessError(f"{path}: JSON parse error: {exc}") from exc


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def git_bytes(args: list[str], root: Path = ROOT) -> bytes:
    proc = subprocess.run(
        ["git", *args],
        cwd=root,
        check=False,
        capture_output=True,
    )
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", "replace").strip()
        raise ReadinessError(f"git {' '.join(args)} failed: {detail}")
    return proc.stdout


def git_text(args: list[str], root: Path = ROOT) -> str:
    return git_bytes(args, root).decode("utf-8", "replace").strip()


def actual_diff(args: argparse.Namespace, root: Path = ROOT) -> tuple[bytes, str | None]:
    if args.diff:
        path = repo_path(args.diff, root)
        try:
            return path.read_bytes(), str(path)
        except OSError as exc:
            raise ReadinessError(f"cannot read diff artifact {path}: {exc}") from exc
    if not args.base or not args.head:
        raise ReadinessError("--base and --head are required when --diff is not provided")
    diff_range = f"{args.base}..{args.head}"
    return git_bytes(["-c", "core.quotepath=false", "diff", diff_range], root), None


def validate_value(schema: dict[str, Any], value: object, path: str, errors: list[str]) -> None:
    expected_type = schema.get("type")
    if expected_type == "object":
        if not isinstance(value, dict):
            errors.append(f"{path}: expected object")
            return
        properties = schema.get("properties", {})
        if not isinstance(properties, dict):
            properties = {}
        required = schema.get("required", [])
        if isinstance(required, list):
            for key in required:
                if isinstance(key, str) and key not in value:
                    errors.append(f"{path}.{key}: missing required field")
        if schema.get("additionalProperties") is False:
            for key in value:
                if key not in properties:
                    errors.append(f"{path}.{key}: additional property is not allowed")
        for key, child in properties.items():
            if key in value and isinstance(child, dict):
                validate_value(child, value[key], f"{path}.{key}", errors)
        return

    if expected_type == "array":
        if not isinstance(value, list):
            errors.append(f"{path}: expected array")
            return
        max_items = schema.get("maxItems")
        if isinstance(max_items, int) and len(value) > max_items:
            errors.append(f"{path}: at most {max_items} items allowed")
        items = schema.get("items")
        if isinstance(items, dict):
            for index, item in enumerate(value):
                validate_value(items, item, f"{path}[{index}]", errors)
        return

    if expected_type == "string":
        if not isinstance(value, str):
            errors.append(f"{path}: expected string")
            return
        min_length = schema.get("minLength")
        if isinstance(min_length, int) and len(value) < min_length:
            errors.append(f"{path}: must be non-empty")
        max_length = schema.get("maxLength")
        if isinstance(max_length, int) and len(value) > max_length:
            errors.append(f"{path}: at most {max_length} characters allowed")
        enum = schema.get("enum")
        if isinstance(enum, list) and value not in enum:
            errors.append(f"{path}: value must be one of {enum}")
        const = schema.get("const")
        if isinstance(const, str) and value != const:
            errors.append(f"{path}: value must be {const!r}")
        return

    if expected_type == "integer":
        if not isinstance(value, int) or isinstance(value, bool):
            errors.append(f"{path}: expected integer")
            return
        minimum = schema.get("minimum")
        if isinstance(minimum, int) and value < minimum:
            errors.append(f"{path}: minimum {minimum}")
        return


def validate_linon_review(review: object) -> list[str]:
    schema = load_json(LINON_REVIEW_SCHEMA)
    if not isinstance(schema, dict):
        return [f"{LINON_REVIEW_SCHEMA}: schema must be object"]
    errors: list[str] = []
    validate_value(schema, review, "$", errors)
    if not isinstance(review, dict):
        return errors

    findings = review.get("findings")
    if isinstance(findings, list):
        for index, finding in enumerate(findings):
            if not isinstance(finding, dict):
                continue
            line_range = finding.get("line_range")
            if isinstance(line_range, dict):
                start = line_range.get("start")
                end = line_range.get("end")
                if isinstance(start, int) and isinstance(end, int) and end < start:
                    errors.append(f"$.findings[{index}].line_range.end < start")
            if finding.get("severity") == "critical":
                for key in ("principle_id", "defect_locus"):
                    if key not in finding:
                        errors.append(f"$.findings[{index}].{key}: missing required field for critical severity")
            if finding.get("lens") == "other" and "class_note" not in finding:
                errors.append(f"$.findings[{index}].class_note: missing required field for other lens")
    return errors


def blocking_count(review: dict[str, Any]) -> int:
    findings = review.get("findings")
    criticals = sum(1 for item in findings if isinstance(item, dict) and item.get("severity") == "critical") if isinstance(findings, list) else 0
    verdicts = review.get("criterion_verdicts")
    refuted = sum(1 for item in verdicts if isinstance(item, dict) and item.get("verdict") == "refuted") if isinstance(verdicts, list) else 0
    return criticals + refuted


def subprocess_gate(command: list[str]) -> tuple[bool, str]:
    proc = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
    output = "\n".join(part.strip() for part in (proc.stdout, proc.stderr) if part.strip())
    return proc.returncode == 0, output


def load_packet_contract(packet_path: Path) -> dict[str, Any]:
    packet = load_json(packet_path)
    if not isinstance(packet, dict):
        raise ReadinessError("packet_path: packet must be a JSON object")
    contract = packet.get("implementation_contract")
    if not isinstance(contract, dict):
        raise ReadinessError("packet_path: missing implementation_contract object")
    embedded = contract.get("embedded_contract")
    if not isinstance(embedded, dict):
        raise ReadinessError("packet_path: missing implementation_contract.embedded_contract object")
    return embedded


def contract_declares_profiles(contract: dict[str, Any]) -> bool:
    applications = contract.get("profile_applications")
    return isinstance(applications, list) and bool(applications)


def run_profile_gate(record: dict[str, Any], args: argparse.Namespace, contract: dict[str, Any]) -> str:
    if not contract_declares_profiles(contract):
        return "profile evidence skipped: contract declares no profile_applications"
    profile = record.get("profile_evidence")
    if not isinstance(profile, dict):
        raise ReadinessError("profile_evidence is required when the contract declares profile_applications")
    required = ("objective_path", "contract_path", "evidence_path")
    missing = [key for key in required if not isinstance(profile.get(key), str) or not profile.get(key)]
    if missing:
        raise ReadinessError(f"profile_evidence missing required paths: {', '.join(missing)}")
    command = [
        sys.executable,
        str(PROFILE_CHECKER),
        "--objective",
        str(repo_path(profile["objective_path"])),
        "--contract",
        str(repo_path(profile["contract_path"])),
        "--evidence",
        str(repo_path(profile["evidence_path"])),
    ]
    if args.base:
        command.extend(["--base", args.base])
    if args.head:
        command.extend(["--head", args.head])
    ok, output = subprocess_gate(command)
    if not ok:
        raise ReadinessError(f"profile evidence gate failed: {output}")
    return output or "profile evidence check passed"


def verify_readiness(record_path: Path, args: argparse.Namespace, root: Path = ROOT) -> dict[str, Any]:
    record = load_json(record_path)
    if not isinstance(record, dict):
        raise ReadinessError("readiness record must be a JSON object")
    pr = record.get("pr")
    if not isinstance(pr, dict) or not isinstance(pr.get("id"), str) or not isinstance(pr.get("ref"), str):
        raise ReadinessError("pr.id and pr.ref are required")
    if not isinstance(record.get("head_sha"), str) or not record["head_sha"]:
        raise ReadinessError("head_sha is required")

    diff_bytes, diff_source = actual_diff(args, root)
    diff_sha = sha256_bytes(diff_bytes)
    recorded_sha = record.get("diff_sha256")
    if not isinstance(recorded_sha, str) or len(recorded_sha) != 64:
        raise ReadinessError("diff_sha256 must be a sha256 hex string")
    if recorded_sha != diff_sha:
        raise ReadinessError(f"diff_sha256 mismatch: recorded {recorded_sha}, actual {diff_sha}")

    if args.head and not args.diff:
        resolved_head = git_text(["rev-parse", "--verify", f"{args.head}^{{commit}}"], root)
        if record["head_sha"] != resolved_head:
            raise ReadinessError(f"head_sha mismatch: recorded {record['head_sha']}, actual {resolved_head}")

    packet_raw = record.get("packet_path")
    if not isinstance(packet_raw, str) or not packet_raw:
        raise ReadinessError("packet_path is required")
    packet_path = repo_path(packet_raw, root)
    packet = load_json(packet_path)
    if isinstance(packet, dict):
        pair = packet.get("sha256_pair")
        if isinstance(pair, dict) and pair.get("diff_sha256") != diff_sha:
            raise ReadinessError(f"packet diff_sha256 mismatch: recorded {pair.get('diff_sha256')}, actual {diff_sha}")
    ok, packet_output = subprocess_gate([sys.executable, str(PACKET_VERIFIER), str(packet_path)])
    if not ok:
        raise ReadinessError(f"Linon packet verification failed: {packet_output}")

    linon = record.get("linon")
    if not isinstance(linon, dict):
        raise ReadinessError("linon object is required")
    review_raw = linon.get("review_path")
    if not isinstance(review_raw, str) or not review_raw:
        raise ReadinessError("linon.review_path is required")
    review = load_json(repo_path(review_raw, root))
    review_errors = validate_linon_review(review)
    if review_errors:
        raise ReadinessError(f"linon review schema validation failed: {review_errors[0]}")
    if not isinstance(review, dict):
        raise ReadinessError("linon review must be a JSON object")
    actual_blocking = blocking_count(review)
    recorded_blocking = linon.get("blocking_findings")
    if recorded_blocking != actual_blocking:
        raise ReadinessError(f"linon.blocking_findings mismatch: recorded {recorded_blocking}, review has {actual_blocking}")
    verdict = linon.get("verdict")
    if not isinstance(verdict, str) or verdict.lower() in {"blocked", "blocking", "fail", "failed", "critical", "refuted"}:
        raise ReadinessError(f"blocking Linon verdict: {verdict!r}")
    if actual_blocking != 0:
        raise ReadinessError(f"blocking Linon findings: {actual_blocking}")

    contract = load_packet_contract(packet_path)
    profile_output = run_profile_gate(record, args, contract)
    return {
        "status": "pass",
        "exit_code": 0,
        "record": str(record_path),
        "diff_source": diff_source or "git",
        "diff_sha256": diff_sha,
        "packet_verification": packet_output or "Linon packet verification passed.",
        "profile_evidence": profile_output,
        "linon": {
            "verdict": verdict,
            "blocking_findings": actual_blocking,
            "review_path": review_raw,
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Verify content-bound merge readiness.")
    parser.add_argument("--record", type=Path, help="Merge readiness record JSON.")
    parser.add_argument("--diff", type=Path, help="Recorded PR diff artifact for fixture/offline verification.")
    parser.add_argument("--base", help="Base commit/ref for git diff.")
    parser.add_argument("--head", help="Head commit/ref for git diff.")
    parser.add_argument("--self-test", action="store_true", help="Run readiness fixtures.")
    return parser


def self_test_case(name: str, should_pass: bool) -> dict[str, Any]:
    case_dir = FIXTURES_DIR / name
    args = argparse.Namespace(
        record=case_dir / "readiness.json",
        diff=case_dir / "diff.patch",
        base=None,
        head=None,
    )
    try:
        payload = verify_readiness(args.record, args)
        actual_pass = True
        detail = payload["status"]
    except ReadinessError as exc:
        actual_pass = False
        detail = str(exc)
    return {
        "name": name,
        "expected": "pass" if should_pass else "block",
        "actual": "pass" if actual_pass else "block",
        "passed": actual_pass == should_pass,
        "detail": detail,
    }


def run_self_test() -> tuple[int, dict[str, Any]]:
    cases = [
        self_test_case("real-pr", True),
        self_test_case("facade-pr", False),
    ]
    failed = [case for case in cases if not case["passed"]]
    return (1 if failed else 0), {
        "status": "fail" if failed else "pass",
        "exit_code": 1 if failed else 0,
        "cases": cases,
    }


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.self_test:
        code, payload = run_self_test()
        print(json.dumps(payload, indent=2, sort_keys=True))
        return code
    if args.record is None:
        print("readiness record is required", file=sys.stderr)
        return 2
    try:
        payload = verify_readiness(repo_path(str(args.record)), args)
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    except ReadinessError as exc:
        payload = {"status": "blocked", "exit_code": 1, "error": str(exc)}
        print(json.dumps(payload, indent=2, sort_keys=True), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
