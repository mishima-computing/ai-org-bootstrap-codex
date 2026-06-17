#!/usr/bin/env python3
"""Run the registry-declared org DAG through controller_run.

The registry owns the graph. This module only derives a deterministic execution order from
``output_to`` and passes each role a compact JSON prompt containing the objective and upstream
results.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import shutil
import subprocess
import sys
import tempfile
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

# codex exec exits non-zero when the "task submission" fails (OpenAI Codex docs: exec exits non-zero
# on submission failure) — a transport-level hiccup, NOT a verdict on the work. The write stages are
# the long, expensive carriers where this bit us: implementer runs ending report_ok=False with no
# frozen/killed/timeout, i.e. a clean turn that nonetheless exited non-zero. Give write roles one extra
# attempt so a transient submission failure is absorbed instead of failing the stage.
WRITE_ROLE_RETRIES = 2


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
    # the registry is part of the ORG install, not the workspace — read it from org_root so the org can
    # operate on an EXTERNAL --repo (cross-repo). Self-hosted (AI_ORG_ROOT unset): org_root == repo.
    entries = load_runtime_registry(controller_run.org_root(repo) / "registry" / "runtime-registry.yaml")
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
        contract["retries"] = WRITE_ROLE_RETRIES   # absorb codex's transient non-zero submission exits
    forbidden = _forbidden_files(inputs)
    if forbidden:
        contract["forbidden_paths"] = forbidden
    return contract


def _read_result(path: Path, errors: list[str] | None = None) -> dict | None:
    if not path.is_file():
        return None
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        if errors is not None:
            errors.append(f"{RESULT_FILE}: invalid JSON: {exc}")
        return None
    except (OSError, UnicodeDecodeError) as exc:
        if errors is not None:
            errors.append(f"{RESULT_FILE}: unreadable: {exc}")
        return None
    if not isinstance(result, dict):
        if errors is not None:
            errors.append(f"{RESULT_FILE}: expected JSON object, got {type(result).__name__}")
        return None
    return result


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
    stage_errors = report_dict.get("stage_errors")
    if not isinstance(stage_errors, list):
        stage_errors = []
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
        "stage_errors": stage_errors,
        "events": _freeze_events(report_dict),
        "timing": {
            "started_at": _iso8601_utc(started_at),
            "finished_at": _iso8601_utc(finished_at),
            "duration_seconds": max(0.0, (finished_at - started_at).total_seconds()),
        },
    }


def _execute_stage(repo: Path, role: str, entry: RegistryEntry, objective: str, inputs: dict[str, dict],
                   stage_run_id: str, cache: bool) -> tuple[bool, dict | None, dict, dict]:
    contract = _contract(entry, objective, inputs)
    started_at = _utc_now()
    stage_ok, result, result_path, result_sha256, report, stage_errors = _run_stage(
        repo, entry, contract, stage_run_id, cache
    )
    finished_at = _utc_now()
    report_dict = report.to_dict()
    _record_stage_errors(report_dict, stage_errors)
    stage = _stage_record(repo, role, entry, contract, result, result_path, result_sha256,
                          report_dict, stage_run_id, stage_ok, started_at, finished_at)
    return stage_ok, result, report_dict, stage


def _store_stage_output(role: str, entry: RegistryEntry, result: dict | None,
                        report_dict: dict, results: dict[str, dict]) -> None:
    if result is not None:
        results[role] = result
    elif entry.write_scope:
        results[role] = _stage_output_for_write_role(role, report_dict)


def _write_manifest(repo: Path, run_id: str, manifest: dict) -> Path:
    path = _manifest_path(repo, run_id)
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
                    encoding="utf-8")
    return path


def _linon_findings(result: dict | str | None) -> list[dict]:
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except json.JSONDecodeError:
            return []
    if not isinstance(result, dict):
        return []
    findings = result.get("findings")
    if not isinstance(findings, list):
        return []
    return [finding for finding in findings if isinstance(finding, dict)]


def _iteration_record(kind: str, iteration: int, started_at: datetime, finished_at: datetime,
                      stages: list[dict], linon_findings_count: int) -> dict:
    return {
        "kind": kind,
        "iteration": iteration,
        "started_at": _iso8601_utc(started_at),
        "finished_at": _iso8601_utc(finished_at),
        "duration_seconds": max(0.0, (finished_at - started_at).total_seconds()),
        "linon_findings_count": linon_findings_count,
        "stages": stages,
    }


def _provenance_manifest(started_at: datetime, finished_at: datetime, stages: list[dict],
                         iterations: list[dict] | None = None) -> dict:
    manifest = {
        "started_at": _iso8601_utc(started_at),
        "finished_at": _iso8601_utc(finished_at),
        "stages": stages,
    }
    if iterations is not None:
        manifest["iterations"] = iterations
    return manifest


def _preserve_result(repo: Path, stage_run_id: str,
                     errors: list[str] | None = None) -> tuple[dict | None, Path | None, str | None]:
    result_path = repo / RESULT_FILE
    result = _read_result(result_path, errors)
    if result is None:
        return None, None, None
    preserved_path = _stage_journal_dir(repo, stage_run_id) / RESULT_FILE
    preserved_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(result_path, preserved_path)
    return result, preserved_path, sha256_file(preserved_path)


def _run_stage(repo: Path, entry: RegistryEntry, contract: dict, run_id: str,
               cache: bool) -> tuple[bool, dict | None, Path | None, str | None, object, list[str]]:
    result_path = repo / RESULT_FILE
    if result_path.exists():
        result_path.unlink()
    report = controller_run.run(repo, contract, run_id, cache=cache)
    if entry.write_scope:
        stage_ok = bool(report.ok)
        return stage_ok, None, None, None, report, []
    stage_errors: list[str] = []
    result, preserved_path, result_sha256 = _preserve_result(repo, run_id, stage_errors)
    stage_ok = bool(report.ok) and result is not None
    return stage_ok, result, preserved_path, result_sha256, report, stage_errors


def _record_stage_errors(report_dict: dict, stage_errors: list[str]) -> None:
    if not stage_errors:
        return
    report_dict["stage_errors"] = list(stage_errors)
    unresolved = report_dict.get("unresolved_failures")
    if isinstance(unresolved, list):
        unresolved.extend(stage_errors)
    else:
        report_dict["unresolved_failures"] = list(stage_errors)


def _waves(producer_roles: list[str], predecessors: dict[str, list[str]]) -> list[list[str]]:
    """Group producer roles into dependency LEVELS: a role joins the earliest wave after all its
    producer-predecessors. Roles in the same wave are independent — they can run concurrently (the three
    designers all feed aufheben with nothing between them => one wave)."""
    pset = set(producer_roles)
    waves, placed, remaining = [], set(), list(producer_roles)
    while remaining:
        ready = [r for r in remaining if all(p in placed or p not in pset for p in predecessors.get(r, []))]
        if not ready:                                  # a cycle would stall us — fail safe to one serial wave
            ready = list(remaining)
        waves.append(sorted(ready))
        placed.update(ready)
        remaining = [r for r in remaining if r not in placed]
    return waves


def _run_wave_parallel(repo: Path, roles: list[str], entries, objective: str,
                       predecessors, results: dict, run_id: str, cache: bool, max_workers: int) -> dict:
    """Run independent READ-ONLY producers concurrently, each in its own git worktree so their result.json
    and scope checks cannot collide; bring each stage's journal back into the main repo for provenance.
    Returns {role: (stage_ok, result, report_dict, stage)}."""
    prepared = []                                      # create the worktrees serially (git worktree add)
    for role in roles:
        wt = Path(tempfile.mkdtemp(prefix=f"pl-par-{role}-"))
        add = subprocess.run(["git", "-C", str(repo), "worktree", "add", "--detach", str(wt), "HEAD"],
                             capture_output=True, text=True)
        prepared.append((role, wt, add.returncode == 0, add.stderr))

    def _one(role, wt, ok, err):
        stage_run_id = f"{run_id}-{role}"
        if not ok:
            raise RuntimeError(f"worktree add failed for {role}: {err[-200:]}")
        inputs = {u: results[u] for u in predecessors.get(role, []) if u in results}
        stage_ok, result, report_dict, stage = _execute_stage(wt, role, entries[role], objective,
                                                              inputs, stage_run_id, cache)
        src = wt / ".agent-runs" / "controller"        # copy the stage journal back so provenance finds it
        if src.is_dir():
            dst = repo / ".agent-runs" / "controller"
            dst.mkdir(parents=True, exist_ok=True)
            for d in src.glob(stage_run_id + "*"):
                shutil.copytree(d, dst / d.name, dirs_exist_ok=True)
        stage = json.loads(json.dumps(stage).replace(str(wt), str(repo)))   # rewrite worktree paths -> repo
        return role, (stage_ok, result, report_dict, stage)

    out = {}
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = [ex.submit(_one, role, wt, ok, err) for (role, wt, ok, err) in prepared]
            for fut in concurrent.futures.as_completed(futures):
                role, oc = fut.result()
                out[role] = oc
    finally:
        for (_, wt, _, _) in prepared:
            subprocess.run(["git", "-C", str(repo), "worktree", "remove", "--force", str(wt)],
                           capture_output=True)
            shutil.rmtree(wt, ignore_errors=True)
    return out


def run_pipeline(repo, objective: str, run_id: str, *, cache: bool = True,
                 max_repair_iterations: int = 3, max_parallel: int = 1) -> dict:
    if not isinstance(max_repair_iterations, int) or max_repair_iterations < 0:
        raise ValueError("max_repair_iterations must be a non-negative integer")

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
    iterations: list[dict] = []
    run_started_at = _utc_now()
    pipeline_failed = False

    repo_is_git = (repo / ".git").exists()
    for wave in _waves(ordered, predecessors):
        # independent READ-ONLY producers of this wave run concurrently in isolated worktrees (the three
        # designers => one parallel wave); write roles and singletons run serially in the repo.
        par = [r for r in wave if not entries[r].write_scope] if (max_parallel > 1 and repo_is_git) else []
        outcomes: dict[str, tuple] = {}
        if len(par) > 1:
            outcomes.update(_run_wave_parallel(repo, par, entries, objective, predecessors,
                                               results, run_id, cache, max_parallel))
        for role in wave:
            if role in outcomes:
                continue
            inputs = {u: results[u] for u in predecessors[role] if u in results}
            outcomes[role] = _execute_stage(repo, role, entries[role], objective, inputs,
                                            f"{run_id}-{role}", cache)
        for role in wave:                              # record deterministically in wave order
            entry = entries[role]
            stage_ok, result, report_dict, stage = outcomes[role]
            summary[role] = bool(report_dict.get("ok"))
            required_ok[role] = stage_ok
            reports[role] = report_dict
            stages.append(stage)
            _store_stage_output(role, entry, result, report_dict, results)
            if entry.output_to is None and entry.write_scope:
                terminal_write_roles.append(role)
            if not stage_ok:
                pipeline_failed = True
        if pipeline_failed:
            break

    if not pipeline_failed:
        verifier_inputs = {role: results[role] for role in sorted(terminal_write_roles) if role in results}
        for role in sorted(verifiers):
            entry = entries[role]
            stage_run_id = f"{run_id}-{role}"
            stage_ok, result, report_dict, stage = _execute_stage(repo, role, entry, objective,
                                                                  verifier_inputs, stage_run_id, cache)
            summary[role] = bool(report_dict.get("ok"))
            required_ok[role] = stage_ok
            reports[role] = report_dict
            stages.append(stage)
            _store_stage_output(role, entry, result, report_dict, results)
            if not stage_ok:
                pipeline_failed = True
                break

    initial_finished_at = _utc_now()
    findings = _linon_findings(results.get("linon"))
    iterations.append(_iteration_record("initial", 0, run_started_at, initial_finished_at,
                                        list(stages), len(findings)))

    repair_iterations = 0
    producer_roles = {
        role for role, entry in entries.items()
        if entry.output_to == "aufheben-designer" and not entry.write_scope
    }
    repair_forward_roles = [
        role for role in ordered
        if role in producer_roles or role in {"aufheben-designer", "implementer"}
    ]

    while findings and not pipeline_failed and repair_iterations < max_repair_iterations:
        repair_iterations += 1
        repair_started_at = _utc_now()
        repair_stages: list[dict] = []
        linon_context = dict(results.get("linon") or {})
        linon_context["repair_iteration"] = repair_iterations

        for role in repair_forward_roles:
            entry = entries[role]
            if role in producer_roles:
                inputs = {"linon": linon_context}
            else:
                inputs = {upstream: results[upstream]
                          for upstream in predecessors[role] if upstream in results}
            stage_run_id = f"{run_id}-repair{repair_iterations}-{role}"
            stage_ok, result, report_dict, stage = _execute_stage(repo, role, entry, objective,
                                                                  inputs, stage_run_id, cache)
            summary[role] = bool(report_dict.get("ok"))
            required_ok[role] = stage_ok
            reports[role] = report_dict
            stages.append(stage)
            repair_stages.append(stage)
            _store_stage_output(role, entry, result, report_dict, results)
            if not stage_ok:
                pipeline_failed = True
                break

        if not pipeline_failed and "linon" in entries:
            verifier_inputs = {role: results[role] for role in sorted(terminal_write_roles) if role in results}
            entry = entries["linon"]
            stage_run_id = f"{run_id}-repair{repair_iterations}-linon"
            stage_ok, result, report_dict, stage = _execute_stage(repo, "linon", entry, objective,
                                                                  verifier_inputs, stage_run_id, cache)
            summary["linon"] = bool(report_dict.get("ok"))
            required_ok["linon"] = stage_ok
            reports["linon"] = report_dict
            stages.append(stage)
            repair_stages.append(stage)
            _store_stage_output("linon", entry, result, report_dict, results)
            if not stage_ok:
                pipeline_failed = True

        findings = _linon_findings(results.get("linon"))
        repair_finished_at = _utc_now()
        iterations.append(_iteration_record("repair", repair_iterations, repair_started_at, repair_finished_at,
                                            repair_stages, len(findings)))

    converged = bool(required_ok.get("linon")) and not findings
    manifest = _provenance_manifest(run_started_at, _utc_now(), stages, iterations)
    manifest_path = _write_manifest(repo, run_id, manifest)
    return {"summary": summary, "required_ok": required_ok, "order": list(summary),
            "reports": reports, "results": results, "manifest": manifest,
            "manifest_path": str(manifest_path), "converged": converged,
            "repair_iterations": repair_iterations,
            "max_repair_iterations": max_repair_iterations,
            "linon_findings_count": len(findings)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--objective", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--max-repair-iterations", type=int, default=3)
    parser.add_argument("--max-parallel", type=int, default=4,
                        help="run independent read-only producers (the designers) concurrently, in "
                             "isolated worktrees; 1 = serial")
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--org-root", default=None,
                        help="the ORG install (registry/schemas/bootstrap); defaults to --repo "
                             "(self-hosted). Set it to operate the org on an EXTERNAL --repo (cross-repo).")
    args = parser.parse_args(argv)
    if args.org_root:
        os.environ["AI_ORG_ROOT"] = str(Path(args.org_root).expanduser().resolve())

    result = run_pipeline(args.repo, args.objective, args.run_id, cache=not args.no_cache,
                          max_repair_iterations=args.max_repair_iterations, max_parallel=args.max_parallel)
    print(json.dumps(result["summary"], indent=2, ensure_ascii=False))
    print(f"provenance_manifest: {result['manifest_path']}")
    return 0 if all(result["required_ok"].values()) and result["converged"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
