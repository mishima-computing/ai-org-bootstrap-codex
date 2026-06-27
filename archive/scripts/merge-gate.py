#!/usr/bin/env python3
"""Poll PR head checks, merge only on a non-empty all-pass check set, and emit evidence.

Exit codes:
0 gate passed and the merge action completed, or check-only/dry-run/fixture passed.
1 gate block from completed failing checks or statuses.
2 usage, environment, gh, API, or merge command failure.
3 indeterminate registration or pending timeout.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
FIXTURES_DIR = ROOT / "fixtures/merge-gate"
READINESS_VERIFIER = ROOT / "scripts" / "verify-merge-readiness.py"
PASSING_CHECK_CONCLUSIONS = {"success", "neutral", "skipped"}
FAILING_CHECK_CONCLUSIONS = {
    "action_required",
    "cancelled",
    "failure",
    "stale",
    "startup_failure",
    "timed_out",
}
PENDING_CHECK_STATUSES = {"queued", "requested", "waiting", "pending", "in_progress"}
PASSING_STATUS_STATES = {"success"}
FAILING_STATUS_STATES = {"error", "failure"}
PENDING_STATUS_STATES = {"expected", "pending"}


class GateUsageError(Exception):
    pass


class GhError(Exception):
    def __init__(self, message: str, returncode: int | None = None):
        self.message = message
        self.returncode = returncode
        super().__init__(message)


def run_gh(args: list[str], timeout: float = 30.0) -> str:
    if shutil.which("gh") is None:
        raise GhError("gh CLI is not available")
    try:
        result = subprocess.run(
            ["gh", *args],
            check=False,
            capture_output=True,
            text=True, encoding="utf-8", errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise GhError(f"gh command timed out: gh {' '.join(args)}") from exc
    except OSError as exc:
        raise GhError(f"gh command failed to start: {exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        first_line = detail[0] if detail else "no gh error output"
        raise GhError(f"gh {' '.join(args)} failed: {first_line}", result.returncode)
    return result.stdout


def gh_json_value(args: list[str], timeout: float = 30.0) -> Any:
    output = run_gh(args, timeout=timeout)
    try:
        return json.loads(output)
    except json.JSONDecodeError as exc:
        raise GhError(f"gh {' '.join(args)} returned non-JSON output: {exc}") from exc


def gh_json(args: list[str], timeout: float = 30.0) -> dict[str, Any]:
    parsed = gh_json_value(args, timeout=timeout)
    if not isinstance(parsed, dict):
        raise GhError(f"gh {' '.join(args)} returned JSON {type(parsed).__name__}, expected object")
    return parsed


def infer_repo() -> str:
    parsed = gh_json(["repo", "view", "--json", "nameWithOwner"])
    value = parsed.get("nameWithOwner")
    if not isinstance(value, str) or "/" not in value:
        raise GhError("gh repo view did not return nameWithOwner")
    return value


def pr_head(repo: str, pr: str) -> dict[str, Any]:
    parsed = gh_json(["pr", "view", pr, "--repo", repo, "--json", "number,baseRefOid,headRefOid,url"])
    head_sha = parsed.get("headRefOid")
    base_sha = parsed.get("baseRefOid")
    number = parsed.get("number")
    if not isinstance(head_sha, str) or not head_sha:
        raise GhError("gh pr view did not return headRefOid")
    if not isinstance(base_sha, str) or not base_sha:
        raise GhError("gh pr view did not return baseRefOid")
    if not isinstance(number, int):
        raise GhError("gh pr view did not return numeric PR number")
    return {
        "number": number,
        "base_sha": base_sha,
        "head_sha": head_sha,
        "url": parsed.get("url") if isinstance(parsed.get("url"), str) else None,
    }


def fetch_check_set(repo: str, sha: str) -> tuple[dict[str, Any], dict[str, Any]]:
    check_run_pages = gh_json_value([
        "api",
        f"repos/{repo}/commits/{sha}/check-runs",
        "--paginate",
        "--slurp",
    ])
    if isinstance(check_run_pages, dict):
        check_runs = check_run_pages
    elif isinstance(check_run_pages, list):
        merged_runs: list[Any] = []
        total_count = 0
        for page in check_run_pages:
            if not isinstance(page, dict):
                continue
            raw_runs = page.get("check_runs", [])
            if isinstance(raw_runs, list):
                merged_runs.extend(raw_runs)
            raw_total = page.get("total_count")
            if isinstance(raw_total, int):
                total_count = max(total_count, raw_total)
        check_runs = {"total_count": total_count, "check_runs": merged_runs}
    else:
        raise GhError("check-runs API returned unexpected JSON shape")
    combined_status = gh_json(["api", f"repos/{repo}/commits/{sha}/status"])
    return check_runs, combined_status


def normalized_check_runs(response: dict[str, Any]) -> list[dict[str, Any]]:
    raw_runs = response.get("check_runs", [])
    if not isinstance(raw_runs, list):
        return [{"name": "check-runs-response", "status": "unknown", "conclusion": "malformed"}]
    runs: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_runs):
        item = raw if isinstance(raw, dict) else {}
        name = item.get("name") if isinstance(item.get("name"), str) else f"check-run-{index}"
        status = item.get("status") if isinstance(item.get("status"), str) else "unknown"
        conclusion = item.get("conclusion")
        runs.append({
            "name": name,
            "status": status,
            "conclusion": conclusion if isinstance(conclusion, str) or conclusion is None else "unknown",
        })
    return runs


def normalized_statuses(response: dict[str, Any]) -> list[dict[str, Any]]:
    raw_statuses = response.get("statuses", [])
    if not isinstance(raw_statuses, list):
        return [{"context": "combined-status-response", "state": "malformed"}]
    statuses: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_statuses):
        item = raw if isinstance(raw, dict) else {}
        context = item.get("context") if isinstance(item.get("context"), str) else f"legacy-status-{index}"
        state = item.get("state") if isinstance(item.get("state"), str) else "unknown"
        statuses.append({"context": context, "state": state})
    return statuses


def classify_check_run(run: dict[str, Any]) -> str:
    status = str(run.get("status", "unknown"))
    conclusion = run.get("conclusion")
    if status != "completed":
        return "pending" if status in PENDING_CHECK_STATUSES else "block"
    if conclusion in PASSING_CHECK_CONCLUSIONS:
        return "pass"
    if conclusion in FAILING_CHECK_CONCLUSIONS:
        return "block"
    return "block"


def classify_status(status: dict[str, Any]) -> str:
    state = str(status.get("state", "unknown"))
    if state in PASSING_STATUS_STATES:
        return "pass"
    if state in FAILING_STATUS_STATES:
        return "block"
    if state in PENDING_STATUS_STATES:
        return "pending"
    return "block"


def classify_gate(check_runs_response: dict[str, Any], combined_status_response: dict[str, Any]) -> dict[str, Any]:
    check_runs = normalized_check_runs(check_runs_response)
    statuses = normalized_statuses(combined_status_response)
    check_items = [
        {**item, "class": classify_check_run(item)}
        for item in check_runs
    ]
    status_items = [
        {**item, "class": classify_status(item)}
        for item in statuses
    ]
    all_items = check_items + status_items
    non_empty = bool(all_items)
    classes = {str(item["class"]) for item in all_items}

    if not non_empty:
        gate_class = "unregistered"
        exit_code = 3
        status = "indeterminate"
    elif "block" in classes:
        gate_class = "blocked"
        exit_code = 1
        status = "blocked"
    elif "pending" in classes:
        gate_class = "pending"
        exit_code = 3
        status = "indeterminate"
    elif classes == {"pass"}:
        gate_class = "passed"
        exit_code = 0
        status = "pass"
    else:
        gate_class = "blocked"
        exit_code = 1
        status = "blocked"

    return {
        "status": status,
        "exit_code": exit_code,
        "gate_class": gate_class,
        "check_runs": check_items,
        "legacy_statuses": status_items,
        "checked_count": len(all_items),
    }


def poll_gate(repo: str, sha: str, timeout_seconds: float, interval_seconds: float) -> tuple[int, dict[str, Any]]:
    start = time.monotonic()
    deadline = start + timeout_seconds
    attempts: list[dict[str, Any]] = []
    sleep_seconds = max(0.1, interval_seconds)

    while True:
        check_runs, combined_status = fetch_check_set(repo, sha)
        classification = classify_gate(check_runs, combined_status)
        attempts.append({
            "attempt": len(attempts) + 1,
            "elapsed_seconds": round(time.monotonic() - start, 3),
            "gate_class": classification["gate_class"],
            "exit_code": classification["exit_code"],
            "checked_count": classification["checked_count"],
        })
        if classification["exit_code"] in {0, 1}:
            evidence = dict(classification)
            evidence["poll_attempts"] = attempts
            return int(classification["exit_code"]), evidence

        now = time.monotonic()
        if now >= deadline:
            evidence = dict(classification)
            evidence["status"] = "indeterminate"
            evidence["exit_code"] = 3
            evidence["poll_attempts"] = attempts
            evidence["timeout_seconds"] = timeout_seconds
            evidence["timeout_reason"] = classification["gate_class"]
            return 3, evidence
        delay = min(sleep_seconds, max(0.0, deadline - now))
        time.sleep(delay)
        sleep_seconds = min(sleep_seconds * 1.5, 30.0)


def merge_pr(repo: str, pr: str, method: str) -> dict[str, Any]:
    command = ["pr", "merge", pr, "--repo", repo, f"--{method}"]
    run_gh(command, timeout=120.0)
    return {
        "command": "gh " + " ".join(command),
        "method": method,
        "completed": True,
    }


def write_evidence(path: Path | None, payload: dict[str, Any]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def run_readiness_gate(
    record: Path | None,
    *,
    diff: Path | None = None,
    base: str | None = None,
    head: str | None = None,
) -> tuple[int, dict[str, Any]]:
    if record is None:
        return 1, {
            "status": "blocked",
            "exit_code": 1,
            "error": "readiness record is required",
        }
    command = [sys.executable, str(READINESS_VERIFIER), "--record", str(record)]
    if diff is not None:
        command.extend(["--diff", str(diff)])
    if base:
        command.extend(["--base", base])
    if head:
        command.extend(["--head", head])
    proc = subprocess.run(command, cwd=ROOT, text=True, encoding="utf-8", errors="replace", capture_output=True, check=False)
    raw = proc.stdout if proc.returncode == 0 else proc.stderr or proc.stdout
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = {
            "status": "pass" if proc.returncode == 0 else "blocked",
            "exit_code": proc.returncode,
            "raw_output": raw.strip(),
        }
    if isinstance(parsed, dict):
        parsed["command"] = " ".join(command)
        parsed["exit_code"] = proc.returncode
        return proc.returncode, parsed
    return proc.returncode, {
        "status": "pass" if proc.returncode == 0 else "blocked",
        "exit_code": proc.returncode,
        "command": " ".join(command),
        "raw_output": raw.strip(),
    }


def fixture_payload(path: Path) -> tuple[dict[str, Any], dict[str, Any], int | None, str]:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise GateUsageError(f"cannot read fixture {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise GateUsageError(f"fixture {path} is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise GateUsageError(f"fixture {path} must be a JSON object")
    check_runs = parsed.get("check_runs_response")
    combined_status = parsed.get("combined_status_response")
    if not isinstance(check_runs, dict) or not isinstance(combined_status, dict):
        raise GateUsageError(f"fixture {path} must include object check_runs_response and combined_status_response")
    expected_exit = parsed.get("expected_exit")
    if expected_exit is not None and not isinstance(expected_exit, int):
        raise GateUsageError(f"fixture {path} expected_exit must be integer when present")
    name = parsed.get("name") if isinstance(parsed.get("name"), str) else path.stem
    return check_runs, combined_status, expected_exit, name


def run_fixture(path: Path) -> tuple[int, dict[str, Any]]:
    check_runs, combined_status, expected_exit, name = fixture_payload(path)
    classification = classify_gate(check_runs, combined_status)
    payload = dict(classification)
    payload.update({
        "fixture": str(path),
        "fixture_name": name,
        "mode": "fixture",
    })
    if expected_exit is not None:
        payload["expected_exit"] = expected_exit
        payload["self_test_passed"] = int(classification["exit_code"]) == expected_exit
    return int(classification["exit_code"]), payload


def run_self_test() -> tuple[int, dict[str, Any]]:
    fixture_paths = [
        FIXTURES_DIR / "empty.json",
        FIXTURES_DIR / "pending.json",
        FIXTURES_DIR / "mixed-fail.json",
        FIXTURES_DIR / "all-pass.json",
        FIXTURES_DIR / "legacy-status-only.json",
        FIXTURES_DIR / "timeout.json",
    ]
    cases: list[dict[str, Any]] = []
    failed = False
    for path in fixture_paths:
        actual_exit, payload = run_fixture(path)
        expected = payload.get("expected_exit")
        passed = actual_exit == expected
        failed = failed or not passed
        cases.append({
            "name": payload["fixture_name"],
            "expected_exit": expected,
            "actual_exit": actual_exit,
            "gate_class": payload["gate_class"],
            "passed": passed,
        })

    code = 1 if failed else 0
    return code, {
        "status": "pass" if code == 0 else "fail",
        "exit_code": code,
        "cases": cases,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Poll GitHub PR head checks and merge only after a non-empty all-pass check set.",
        epilog="Exit 0 pass+merged/check-only pass; 1 gate block; 2 usage/env/API/merge failure; 3 registration or pending timeout.",
    )
    parser.add_argument("pr", nargs="?", help="Pull request number or URL.")
    parser.add_argument("--repo", help="GitHub owner/name. Defaults to gh repo view.")
    parser.add_argument("--method", choices=["merge", "squash", "rebase"], default="merge", help="gh pr merge method. Default: merge.")
    parser.add_argument("--timeout", type=float, default=300.0, help="Hard poll timeout in seconds. Default: 300.")
    parser.add_argument("--interval", type=float, default=5.0, help="Initial poll interval in seconds. Default: 5.")
    parser.add_argument("--check-only", action="store_true", help="Check gate readiness and write evidence without merging.")
    parser.add_argument("--dry-run", action="store_true", help="Check gate readiness and report the merge command without executing it.")
    parser.add_argument("--out", type=Path, help="Write evidence JSON to this path.")
    parser.add_argument("--fixture", type=Path, help="Classify a recorded check-set fixture offline without gh.")
    parser.add_argument("--readiness-record", type=Path, help="Content-bound merge readiness record. Required for real PR runs.")
    parser.add_argument("--readiness-diff", type=Path, help="Fixture/offline PR diff artifact for readiness verification.")
    parser.add_argument("--self-test", action="store_true", help="Run offline classifier fixtures without network or gh.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.timeout < 0:
            raise GateUsageError("--timeout must be non-negative")
        if args.interval <= 0:
            raise GateUsageError("--interval must be positive")

        if args.self_test:
            code, payload = run_self_test()
            print(json.dumps(payload, indent=2, sort_keys=True))
            return code

        if args.fixture:
            readiness_code: int | None = None
            readiness_payload: dict[str, Any] | None = None
            if args.readiness_record is not None:
                readiness_code, readiness_payload = run_readiness_gate(
                    args.readiness_record,
                    diff=args.readiness_diff,
                )
                if readiness_code != 0:
                    payload = {
                        "status": "blocked",
                        "exit_code": 1,
                        "fixture": str(args.fixture),
                        "mode": "fixture",
                        "readiness": readiness_payload,
                        "ci_checks": "not evaluated; readiness blocked first",
                        "merged": False,
                    }
                    write_evidence(args.out, payload)
                    print(json.dumps(payload, indent=2, sort_keys=True))
                    return 1
            code, payload = run_fixture(args.fixture)
            if readiness_payload is not None:
                payload["readiness"] = readiness_payload
            write_evidence(args.out, payload)
            print(json.dumps(payload, indent=2, sort_keys=True))
            return code

        if not args.pr:
            raise GateUsageError("PR number or URL is required outside --self-test/--fixture")

        repo = args.repo or infer_repo()
        pr_info = pr_head(repo, args.pr)
        readiness_code, readiness = run_readiness_gate(
            args.readiness_record,
            base=pr_info["base_sha"],
            head=pr_info["head_sha"],
        )
        if readiness_code != 0:
            evidence = {
                "mode": "check-only" if args.check_only else "dry-run" if args.dry_run else "merge",
                "repo": repo,
                "pr": pr_info["number"],
                "pr_url": pr_info["url"],
                "base_sha": pr_info["base_sha"],
                "head_sha": pr_info["head_sha"],
                "merge_method": args.method,
                "status": "blocked",
                "exit_code": 1,
                "gate_class": "readiness-blocked",
                "readiness": readiness,
                "merged": False,
            }
            write_evidence(args.out, evidence)
            print(json.dumps(evidence, indent=2, sort_keys=True))
            return 1
        code, evidence = poll_gate(repo, pr_info["head_sha"], args.timeout, args.interval)
        evidence.update({
            "mode": "check-only" if args.check_only else "dry-run" if args.dry_run else "merge",
            "repo": repo,
            "pr": pr_info["number"],
            "pr_url": pr_info["url"],
            "base_sha": pr_info["base_sha"],
            "head_sha": pr_info["head_sha"],
            "merge_method": args.method,
            "readiness": readiness,
            "merged": False,
        })

        if code == 0 and args.dry_run:
            evidence["merge_command"] = f"gh pr merge {args.pr} --repo {repo} --{args.method}"
        elif code == 0 and not args.check_only:
            evidence["merge"] = merge_pr(repo, args.pr, args.method)
            evidence["merged"] = True

        write_evidence(args.out, evidence)
        print(json.dumps(evidence, indent=2, sort_keys=True))
        return code
    except GateUsageError as exc:
        payload = {"status": "error", "exit_code": 2, "error": str(exc)}
        write_evidence(args.out, payload)
        print(json.dumps(payload, indent=2, sort_keys=True), file=sys.stderr)
        return 2
    except GhError as exc:
        payload = {
            "status": "error",
            "exit_code": 2,
            "error": exc.message,
        }
        if exc.returncode is not None:
            payload["gh_exit_code"] = exc.returncode
        write_evidence(args.out, payload)
        print(json.dumps(payload, indent=2, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
