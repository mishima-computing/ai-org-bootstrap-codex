#!/usr/bin/env python3
"""Run the registry-declared org DAG through controller_run.

The registry owns the graph. This module only derives a deterministic execution order from
``output_to`` and passes each role a compact JSON prompt containing the objective and upstream
results.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, str(Path(HERE).parent / "packages" / "codex-org-bootstrap" / "src"))

import controller_run  # noqa: E402
from controller_evidence import RunJournal, sha256_file  # noqa: E402
from ai_org_bootstrap.registry import RegistryEntry, load_runtime_registry  # noqa: E402

RESULT_FILE = "result.json"
PROVENANCE_FILE = "provenance-manifest.json"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso8601_utc(value: datetime | float | int | None = None) -> str:
    if value is None:
        value = _utc_now()
    if isinstance(value, (float, int)):
        value = datetime.fromtimestamp(value, timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _entries(repo: Path) -> dict[str, RegistryEntry]:
    entries = load_runtime_registry(repo / "registry" / "runtime-registry.yaml")
    return {entry.agent_id: entry for entry in entries}


def _predecessors(entries: dict[str, RegistryEntry]) -> dict[str, list[str]]:
    predecessors = {role: [] for role in entries}
    for role, entry in entries.items():
        if entry.output_to:
            if entry.output_to not in entries:
                raise ValueError(f"role {role} output_to unknown role {entry.output_to}")
            predecessors[entry.output_to].append(role)
    return {role: sorted(upstream) for role, upstream in predecessors.items()}


def _verifier_roles(entries: dict[str, RegistryEntry]) -> list[str]:
    return sorted(role for role, entry in entries.items() if entry.output_to is None and not entry.write_scope)


def _topological_roles(entries: dict[str, RegistryEntry], verifiers: set[str]) -> list[str]:
    roles = sorted(role for role in entries if role not in verifiers)
    indegree = {role: 0 for role in roles}
    outgoing = {role: [] for role in roles}
    for role in roles:
        target = entries[role].output_to
        if target and target in indegree:
            outgoing[role].append(target)
            indegree[target] += 1

    ready = sorted(role for role, degree in indegree.items() if degree == 0)
    ordered: list[str] = []
    while ready:
        role = ready.pop(0)
        ordered.append(role)
        for target in sorted(outgoing[role]):
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)
                ready.sort()
    if len(ordered) != len(roles):
        blocked = sorted(role for role in roles if role not in ordered)
        raise ValueError(f"registry output_to graph has a cycle involving {blocked}")
    return ordered


def _prompt(role: str, objective: str, inputs: dict[str, dict]) -> str:
    payload = {"role": role, "objective": objective, "inputs": inputs}
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _allowed_files(entry: RegistryEntry, inputs: dict[str, dict]) -> list[str]:
    upstream_allowed: list[str] = []
    for upstream in inputs.values():
        allowed = upstream.get("files_allowed_to_change")
        if isinstance(allowed, list) and all(isinstance(item, str) for item in allowed):
            upstream_allowed.extend(allowed)
    if upstream_allowed:
        return sorted(dict.fromkeys(upstream_allowed))
    return list(entry.write_scope)


def _forbidden_files(inputs: dict[str, dict]) -> list[str]:
    forbidden: list[str] = []
    for upstream in inputs.values():
        disallowed = upstream.get("files_not_allowed_to_change")
        if isinstance(disallowed, list) and all(isinstance(item, str) for item in disallowed):
            forbidden.extend(disallowed)
    return sorted(dict.fromkeys(forbidden))


def _contract(entry: RegistryEntry, objective: str, inputs: dict[str, dict]) -> dict:
    write_scope = _allowed_files(entry, inputs)
    contract = {
        "role": entry.agent_id,
        "prompt": _prompt(entry.agent_id, objective, inputs),
        "sandbox": "workspace-write" if write_scope else "read-only",
    }
    if write_scope:
        contract["files_allowed_to_change"] = write_scope
    forbidden = _forbidden_files(inputs)
    if forbidden:
        contract["forbidden_paths"] = forbidden
    return contract


def _read_result(path: Path) -> dict | None:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _controller_journal_root(repo: Path) -> Path:
    return repo / ".agent-runs" / "controller"


def _validate_run_id(repo: Path, run_id: str) -> str:
    if not isinstance(run_id, str) or not run_id or run_id in {".", ".."}:
        raise ValueError("run_id must be a non-empty safe single path segment")
    if "/" in run_id or "\\" in run_id:
        raise ValueError("run_id must be a safe single path segment")
    root = _controller_journal_root(repo).resolve()
    candidate = (root / run_id).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("run_id escapes .agent-runs/controller") from exc
    if candidate.parent != root:
        raise ValueError("run_id must be a safe single path segment")
    return run_id


def _stage_journal_dir(repo: Path, run_id: str) -> Path:
    return _controller_journal_root(repo) / run_id


def _manifest_path(repo: Path, run_id: str) -> Path:
    return RunJournal(repo, run_id).dir / PROVENANCE_FILE


def _conversation_log_path(report_dict: dict) -> str | None:
    attempts = report_dict.get("attempts")
    if not isinstance(attempts, list) or not attempts:
        return None
    for attempt in reversed(attempts):
        if isinstance(attempt, dict) and isinstance(attempt.get("log"), str):
            return attempt["log"]
    return None


def _stage_output_for_write_role(role: str, report_dict: dict) -> dict:
    return {
        "role_id": role,
        "diff_artifact": report_dict.get("diff_artifact"),
    }


def _artifact_record(entry: RegistryEntry, result: dict | None, result_path: Path | None,
                     result_sha256: str | None, report_dict: dict) -> dict:
    if entry.write_scope:
        return {
            "result_path": None,
            "result_sha256": None,
            "result": None,
            "diff_artifact": report_dict.get("diff_artifact"),
        }
    return {
        "result_path": str(result_path) if result_path is not None else None,
        "result_sha256": result_sha256,
        "result": result,
        "diff_artifact": None,
    }


def _freeze_events(report_dict: dict) -> list[dict]:
    events: list[dict] = []
    attempts = report_dict.get("attempts")
    if not isinstance(attempts, list):
        return events
    for attempt in attempts:
        if not isinstance(attempt, dict) or not attempt.get("frozen"):
            continue
        event = {
            "type": "carrier_freeze_killed",
            "attempt": attempt.get("attempt"),
            "timestamp": attempt.get("timestamp") or _iso8601_utc(),
            "retryable": bool(attempt.get("retryable")),
        }
        if "no_output_timeout" in attempt:
            event["no_output_timeout"] = attempt["no_output_timeout"]
        events.append(event)
    return events


def _stage_record(repo: Path, role: str, entry: RegistryEntry, contract: dict, result: dict | None,
                  result_path: Path | None, result_sha256: str | None, report_dict: dict,
                  stage_run_id: str, stage_ok: bool, started_at: datetime, finished_at: datetime) -> dict:
    journal_dir = _stage_journal_dir(repo, stage_run_id)
    return {
        "role": role,
        "run_id": stage_run_id,
        "contract_sent": contract,
        "artifact": _artifact_record(entry, result, result_path, result_sha256, report_dict),
        "conversation_log_path": _conversation_log_path(report_dict),
        "journal_dir": str(journal_dir),
        "journal_path": str(journal_dir / "journal.jsonl"),
        "report_ok": bool(report_dict.get("ok")),
        "stage_ok": stage_ok,
        "events": _freeze_events(report_dict),
        "timing": {
            "started_at": _iso8601_utc(started_at),
            "finished_at": _iso8601_utc(finished_at),
            "duration_seconds": max(0.0, (finished_at - started_at).total_seconds()),
        },
    }


def _write_manifest(repo: Path, run_id: str, manifest: dict) -> Path:
    path = _manifest_path(repo, run_id)
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
                    encoding="utf-8")
    return path


def _provenance_manifest(started_at: datetime, finished_at: datetime, stages: list[dict]) -> dict:
    return {
        "started_at": _iso8601_utc(started_at),
        "finished_at": _iso8601_utc(finished_at),
        "stages": stages,
    }


def _preserve_result(repo: Path, stage_run_id: str) -> tuple[dict | None, Path | None, str | None]:
    result_path = repo / RESULT_FILE
    result = _read_result(result_path)
    if result is None:
        return None, None, None
    preserved_path = _stage_journal_dir(repo, stage_run_id) / RESULT_FILE
    preserved_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(result_path, preserved_path)
    return result, preserved_path, sha256_file(preserved_path)


def _run_stage(repo: Path, entry: RegistryEntry, contract: dict, run_id: str,
               cache: bool) -> tuple[bool, dict | None, Path | None, str | None, object]:
    result_path = repo / RESULT_FILE
    if result_path.exists():
        result_path.unlink()
    report = controller_run.run(repo, contract, run_id, cache=cache)
    if entry.write_scope:
        stage_ok = bool(report.ok)
        return stage_ok, None, None, None, report
    result, preserved_path, result_sha256 = _preserve_result(repo, run_id)
    stage_ok = bool(report.ok) and result is not None
    return stage_ok, result, preserved_path, result_sha256, report


def run_pipeline(repo, objective: str, run_id: str, *, cache: bool = True) -> dict:
    repo = Path(repo).resolve()
    run_id = _validate_run_id(repo, run_id)
    entries = _entries(repo)
    predecessors = _predecessors(entries)
    verifiers = set(_verifier_roles(entries))
    ordered = _topological_roles(entries, verifiers)

    results: dict[str, dict] = {}
    reports: dict[str, dict] = {}
    summary: dict[str, bool] = {}
    required_ok: dict[str, bool] = {}
    terminal_write_roles: list[str] = []
    stages: list[dict] = []
    run_started_at = _utc_now()

    for role in ordered:
        entry = entries[role]
        inputs = {upstream: results[upstream] for upstream in predecessors[role] if upstream in results}
        stage_run_id = f"{run_id}-{role}"
        contract = _contract(entry, objective, inputs)
        started_at = _utc_now()
        stage_ok, result, result_path, result_sha256, report = _run_stage(repo, entry, contract, stage_run_id, cache)
        finished_at = _utc_now()
        report_dict = report.to_dict()
        summary[role] = bool(report.ok)
        required_ok[role] = stage_ok
        reports[role] = report_dict
        stages.append(_stage_record(repo, role, entry, contract, result, result_path, result_sha256,
                                    report_dict, stage_run_id, stage_ok, started_at, finished_at))
        if result is not None:
            results[role] = result
        elif entry.write_scope:
            results[role] = _stage_output_for_write_role(role, report_dict)
        if entry.output_to is None and entry.write_scope:
            terminal_write_roles.append(role)
        if not stage_ok:
            manifest = _provenance_manifest(run_started_at, _utc_now(), stages)
            manifest_path = _write_manifest(repo, run_id, manifest)
            return {
                "summary": summary,
                "required_ok": required_ok,
                "order": list(summary),
                "reports": reports,
                "results": results,
                "manifest": manifest,
                "manifest_path": str(manifest_path),
            }

    verifier_inputs = {role: results[role] for role in sorted(terminal_write_roles) if role in results}
    for role in sorted(verifiers):
        entry = entries[role]
        stage_run_id = f"{run_id}-{role}"
        contract = _contract(entry, objective, verifier_inputs)
        started_at = _utc_now()
        stage_ok, result, result_path, result_sha256, report = _run_stage(repo, entry, contract, stage_run_id, cache)
        finished_at = _utc_now()
        report_dict = report.to_dict()
        summary[role] = bool(report.ok)
        required_ok[role] = stage_ok
        reports[role] = report_dict
        stages.append(_stage_record(repo, role, entry, contract, result, result_path, result_sha256,
                                    report_dict, stage_run_id, stage_ok, started_at, finished_at))
        if result is not None:
            results[role] = result
        if not stage_ok:
            break

    manifest = _provenance_manifest(run_started_at, _utc_now(), stages)
    manifest_path = _write_manifest(repo, run_id, manifest)
    return {"summary": summary, "required_ok": required_ok, "order": list(summary),
            "reports": reports, "results": results, "manifest": manifest,
            "manifest_path": str(manifest_path)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--objective", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--no-cache", action="store_true")
    args = parser.parse_args(argv)

    result = run_pipeline(args.repo, args.objective, args.run_id, cache=not args.no_cache)
    print(json.dumps(result["summary"], indent=2, ensure_ascii=False))
    print(f"provenance_manifest: {result['manifest_path']}")
    return 0 if all(result["required_ok"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
