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
AUFHEBEN_ROLE = "aufheben-designer"

# On a REPAIR iteration these four roles RESUME their prior codex session (keeping full memory, emitting
# only a small delta) instead of re-deriving from scratch. aufheben-designer/linon/stefan stay FRESH:
# aufheben must re-synthesize the changed designer outputs, and linon must stay an independent adversary.
SESSION_REUSE_ROLES = ("aggressive-designer", "conservative-designer", "genius", "implementer")


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


def _close_brackets(s: str) -> str:
    """Append the closing brackets a truncated JSON value is missing (the F10 closure repair), ignoring
    brackets inside strings. Recovers a carrier whose output was cut off mid-object."""
    stack, in_str, esc = [], False, False
    for ch in s:
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[":
            stack.append(ch)
        elif ch == "}" and stack and stack[-1] == "{":
            stack.pop()
        elif ch == "]" and stack and stack[-1] == "[":
            stack.pop()
    return s + "".join("}" if c == "{" else "]" for c in reversed(stack))


def _salvage_json(text: str):
    """Best-effort recovery of a JSON value from a carrier's raw output. LLM carriers wrap JSON in
    markdown fences, add prose around it, emit Python-literal syntax (single-quoted keys), or get cut off.
    Try, in order: as-is; fence-stripped; the first {...}/[...] block; the same with missing brackets
    closed; and finally ast.literal_eval (Python literals). Raises json.JSONDecodeError if unrecoverable."""
    s = (text or "").strip()
    candidates = [s]
    if "```" in s:
        for part in s.split("```"):
            part = part.strip()
            if part[:4] == "json":
                part = part[4:].strip()
            if part[:1] in "{[":
                candidates.append(part)
    starts = [i for i in (s.find("{"), s.find("[")) if i >= 0]
    if starts:
        start = min(starts)
        end = max(s.rfind("}"), s.rfind("]"))
        if end > start:
            candidates.append(s[start:end + 1])
    for c in candidates:
        for attempt in (c, _close_brackets(c)):
            try:
                return json.loads(attempt)
            except json.JSONDecodeError:
                pass
        try:
            import ast
            return ast.literal_eval(c)                    # Python-literal output (single-quoted keys, etc.)
        except (ValueError, SyntaxError):
            pass
    raise json.JSONDecodeError("unsalvageable carrier output", s or "", 0)


def _read_result(path: Path, errors: list[str] | None = None) -> dict | None:
    if not path.is_file():
        return None
    try:
        result = _salvage_json(path.read_text(encoding="utf-8"))
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


def _stream_append(repo, event: dict) -> None:
    """Tee one event to the shared stream log (STREAM_LOG env, else <repo>/.agent-runs/stream.jsonl) so a
    consumer sees the dialectic as it happens — who (source) did what, when (ts). STREAM_LOG points at the
    shared log even from an isolated worktree. Fail-soft: a logging error never breaks the pipeline."""
    try:
        log = Path(os.environ.get("STREAM_LOG") or (Path(repo) / ".agent-runs" / "stream.jsonl"))
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": _iso8601_utc(), **dict(event)}, ensure_ascii=False) + "\n")
    except Exception:                                      # noqa: BLE001 - observability never breaks a run
        pass


SPEECH_CAP = 16000   # max serialized chars of a role's speech that ride the stream verbatim


def _bounded_speech(result):
    """A role's actual output (its validated packet — proposal, findings, contract) shaped to ride the
    shared stream. The stream is the only DURABLE record of what each agent said: the per-stage result.json
    is preserved inside the stage's worktree, which for a wave role is an ephemeral sub-worktree removed
    after the wave — so a host (Shagiri) that wants to show "what this agent said" cannot read it back, only
    the stream survives (log-is-the-state-source). Emitting just ok/usage on stage_done was the poor-minimal
    -log time bomb: the speech was computed, then dropped. Oversized packets are previewed (not silently cut)
    so the truncation is legible rather than a hidden data loss."""
    if result is None:
        return None
    try:
        s = json.dumps(result, ensure_ascii=False)
    except Exception:                                          # noqa: BLE001 — a non-serializable packet still streams as text
        s = str(result)
    if len(s) <= SPEECH_CAP:
        return result
    return {"_truncated": True, "_chars": len(s), "_preview": s[:SPEECH_CAP]}


def _stream_speech(repo, role: str, stage_run_id: str, result) -> None:
    """Tee a role's speech onto the shared stream as its own `agent_message` event (distinct from stage_done
    so existing consumers are untouched), making the dialectic's CONTENT — not just its timing — durable and
    host-readable."""
    _stream_append(repo, {"source": role, "type": "agent_message", "run_id": stage_run_id,
                          "speech": _bounded_speech(result)})


def _linon_via_codex_review_enabled() -> bool:
    return os.environ.get("LINON_VIA_CODEX_REVIEW", "") not in ("", "0", "false", "no")


def _execute_linon_via_codex_review(repo: Path, stage_run_id: str) -> tuple[bool, dict | None, dict, dict]:
    """Run the review role through Codex's native `codex review` (diff-anchored: it starts from the leaf's
    uncommitted changes but reads full files + cross-file dependents) instead of a free-reading role
    carrier. Returns the same (stage_ok, result, report_dict, stage) tuple so the `while findings` repair
    loop is unchanged; findings come back in the `{"findings": [...]}` shape `_linon_findings` consumes."""
    import codex_review
    started_at = _utc_now()
    try:
        rv = codex_review.review(str(repo))
    except Exception as exc:  # noqa: BLE001 — a failed review is "could not review", not "clean"
        rv = {"findings": [], "ok": False, "raw": f"{type(exc).__name__}: {exc}"}
    finished_at = _utc_now()
    findings = rv.get("findings") or []
    stage_ok = bool(rv.get("ok"))
    result = {"findings": findings}
    unresolved = [] if stage_ok else ["codex review did not complete"]
    report_dict = {"ok": stage_ok, "unresolved_failures": unresolved, "reviewer": "codex-review"}
    try:                                                # journal the raw review for audit (mirror _run_stage)
        d = Path(repo) / ".agent-runs" / "controller" / stage_run_id
        d.mkdir(parents=True, exist_ok=True)
        (d / "codex-review.log").write_text(rv.get("raw") or "", encoding="utf-8")
    except OSError:
        pass
    stage = {"role": "linon", "run_id": stage_run_id, "ok": stage_ok, "reviewer": "codex-review",
             "started_at": _iso8601_utc(started_at), "finished_at": _iso8601_utc(finished_at),
             "findings_count": len(findings)}
    _stream_speech(repo, "linon", stage_run_id, result)       # the findings themselves (durable on the stream)
    _stream_append(repo, {"source": "linon", "type": "stage_done", "run_id": stage_run_id,
                          "ok": stage_ok, "unresolved": unresolved})
    return stage_ok, result, report_dict, stage


def _execute_stage(repo: Path, role: str, entry: RegistryEntry, objective: str, inputs: dict[str, dict],
                   stage_run_id: str, cache: bool, resume_session=None) -> tuple[bool, dict | None, dict, dict]:
    if role == "linon" and _linon_via_codex_review_enabled():
        return _execute_linon_via_codex_review(repo, stage_run_id)
    contract = _contract(entry, objective, inputs)
    started_at = _utc_now()
    stage_ok, result, result_path, result_sha256, report, stage_errors = _run_stage(
        repo, entry, contract, stage_run_id, cache, resume_session
    )
    finished_at = _utc_now()
    report_dict = report.to_dict()
    _record_stage_errors(report_dict, stage_errors)
    stage = _stage_record(repo, role, entry, contract, result, result_path, result_sha256,
                          report_dict, stage_run_id, stage_ok, started_at, finished_at)
    # the stage's TOKEN + CONTEXT spend (from codex's --json stream, captured per attempt by the harness)
    # rides the stage_done event onto the shared stream, so a host (Shagiri) can show what /status shows.
    # None on a cache hit — a replayed stage spends ~0 tokens, which is the honest number.
    usage = next((a.get("usage") for a in reversed(report_dict.get("attempts") or []) if a.get("usage")), None)
    _stream_speech(repo, role, stage_run_id, result)          # the CONTENT the role produced (durable on the stream)
    _stream_append(repo, {"source": role, "type": "stage_done", "run_id": stage_run_id,
                          "ok": bool(stage_ok), "unresolved": report_dict.get("unresolved_failures") or [],
                          "usage": usage})
    return stage_ok, result, report_dict, stage


def _copy_stage_journal(wt: Path, repo: Path, stage_run_id: str) -> None:
    """Bring a worktree stage's journal back into the main repo so provenance/activity find it."""
    src = wt / ".agent-runs" / "controller"
    if src.is_dir():
        dst = repo / ".agent-runs" / "controller"
        dst.mkdir(parents=True, exist_ok=True)
        for d in src.glob(stage_run_id + "*"):
            shutil.copytree(d, dst / d.name, dirs_exist_ok=True)


def _execute_stage_isolated(repo: Path, role: str, entry: RegistryEntry, objective: str,
                            inputs: dict[str, dict], stage_run_id: str, cache: bool) -> tuple:
    """Run ONE write role in its own git worktree (detached at HEAD) so its scope check evaluates only
    its OWN diff, then merge its file changes back into the main repo. This is the serial-but-isolated
    path (max_parallel=1); the concurrent path is _run_wave_parallel. Falls back to in-repo execution if
    a worktree cannot be created, rather than failing the run."""
    wt = Path(tempfile.mkdtemp(prefix=f"pl-iso-{role}-"))
    add = subprocess.run(["git", "-C", str(repo), "worktree", "add", "--detach", str(wt), "HEAD"],
                         capture_output=True, text=True)
    if add.returncode != 0:
        shutil.rmtree(wt, ignore_errors=True)
        return _execute_stage(repo, role, entry, objective, inputs, stage_run_id, cache)
    try:
        stage_ok, result, report_dict, stage = _execute_stage(wt, role, entry, objective, inputs,
                                                              stage_run_id, cache)
        _copy_stage_journal(wt, repo, stage_run_id)
        stage = json.loads(json.dumps(stage).replace(str(wt), str(repo)))   # worktree paths -> repo
        if stage_ok:
            _apply_worktree_changes(repo, wt, report_dict.get("changed_files") or [])
        return stage_ok, result, report_dict, stage
    finally:
        subprocess.run(["git", "-C", str(repo), "worktree", "remove", "--force", str(wt)],
                       capture_output=True)
        shutil.rmtree(wt, ignore_errors=True)


def _store_stage_output(role: str, entry: RegistryEntry, stage_ok: bool, result: dict | None,
                        report_dict: dict, results: dict[str, dict]) -> None:
    if stage_ok and result is not None:
        results[role] = result
    elif stage_ok and entry.write_scope:
        results[role] = _stage_output_for_write_role(role, report_dict)
    else:
        results.pop(role, None)


def _write_manifest(repo: Path, run_id: str, manifest: dict) -> Path:
    path = _manifest_path(repo, run_id)
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
                    encoding="utf-8")
    return path


# A reviewer's finding must target the human-authored DELIVERABLE. A finding whose file is controller
# scratch (.agent-runs/), a build/cache artifact, a dependency tree, or a generated/lock file is NOT
# reviewable: no change to the deliverable can clear it, so it drives the repair loop (while findings ...)
# forever — observed live as a scaffold leaf failing linon r0..r3 on .agent-runs/.../journal.jsonl. Such
# findings are DROPPED before they gate convergence. This is the hard, deterministic backstop: it holds no
# matter what the reviewer was able to read (gitignore/exclude only soft-reduces what it sees; the reviewer
# can still `find`/`cat` past it). Segment-based so it matches both relative and absolute finding paths.
_NONREVIEWABLE_SEGMENTS = (".agent-runs", ".git", "__pycache__", "node_modules", ".venv", "venv",
                           ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox")
_NONREVIEWABLE_BASENAMES = ("package-lock.json", "poetry.lock", "pnpm-lock.yaml", "yarn.lock", "Cargo.lock")
_NONREVIEWABLE_GLOBS = ("*.pyc", "*.pyo", "*.pyd", "*.egg-info", "*.min.js", "*.min.css", "*.map")


def _is_reviewable_finding_path(path: str) -> bool:
    """False if a finding's file is non-deliverable (scratch / artifact / dependency / generated). A
    finding with no concrete target is kept (conservative — we only drop what we can positively classify)."""
    import fnmatch
    p = (path or "").strip()
    if not p:
        return True
    segs = [s for s in p.replace("\\", "/").split("/") if s and s != "."]
    if any(seg in _NONREVIEWABLE_SEGMENTS for seg in segs):
        return False
    base = segs[-1] if segs else ""
    if base in _NONREVIEWABLE_BASENAMES:
        return False
    return not any(fnmatch.fnmatch(base, g) for g in _NONREVIEWABLE_GLOBS)


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
    dicts = [finding for finding in findings if isinstance(finding, dict)]
    # drop findings about non-deliverable files (scratch/artifact/generated) — they can never converge.
    # The raw result (with every finding) is still journaled in reports["linon"], so nothing is lost from
    # the audit trail; only the convergence gate ignores the un-actionable ones.
    return [f for f in dicts if _is_reviewable_finding_path(f.get("file") or f.get("path") or "")]


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
               cache: bool, resume_session=None) -> tuple[bool, dict | None, Path | None, str | None, object, list[str]]:
    result_path = repo / RESULT_FILE
    if result_path.exists():
        result_path.unlink()
    report = controller_run.run(repo, contract, run_id, cache=cache, resume_session=resume_session)
    if entry.write_scope:
        stage_ok = bool(report.ok)
        return stage_ok, None, None, None, report, []
    stage_errors: list[str] = []
    result, preserved_path, result_sha256 = _preserve_result(repo, run_id, stage_errors)
    if result is None and bool(report.ok):           # carrier ran clean but emitted unsalvageable output:
        reask = dict(contract)                       # ask once more for ONLY valid JSON, bypassing the cache
        reask["prompt"] = contract["prompt"] + (
            "\n\nYOUR PREVIOUS OUTPUT WAS NOT VALID JSON. Return ONLY the JSON value your schema requires "
            "— no prose, no markdown fences, double-quoted keys, no trailing commas.")
        if result_path.exists():
            result_path.unlink()
        report = controller_run.run(repo, reask, run_id + "-reask", cache=False)
        stage_errors = []
        result, preserved_path, result_sha256 = _preserve_result(repo, run_id + "-reask", stage_errors)
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


def _apply_worktree_changes(repo: Path, wt: Path, changed_files: list[str]) -> None:
    """Copy a WRITE role's own changes out of its isolated worktree into the main repo. The role ran off
    HEAD in `wt`, so `changed_files` (its scope_report.changed) are ITS changes only — never a sibling
    role's. Disjoint write scopes mean independent roles (the CI writers + the implementer) merge back
    without conflict; an implementer is never charged for, nor sees, a CI writer's .github edits."""
    for rel in changed_files or []:
        if not rel or rel == ".agent-runs" or rel.startswith(".agent-runs/"):
            continue
        src, dst = wt / rel, repo / rel
        if src.is_file():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        elif not src.exists() and dst.is_file():       # the role deleted it in its worktree
            dst.unlink()


def _run_wave_parallel(repo: Path, roles: list[str], entries, objective: str,
                       predecessors, results: dict, run_id: str, cache: bool, max_workers: int) -> dict:
    """Run wave roles each in its own git worktree (detached at HEAD) so their result.json and — for
    write roles — their working-tree edits and scope checks cannot collide. READ-ONLY producers only
    emit result.json; WRITE roles' file changes are merged back into the main repo after the wave, in a
    deterministic order. Bring each stage's journal back for provenance. max_workers=1 keeps it serial
    but still ISOLATED (the correctness guarantee does not depend on concurrency).
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
        # merge write-role edits into the main repo serially, deterministic order (disjoint scopes apply
        # cleanly). The scope check already ran isolated in each worktree, so this only moves files.
        wt_by_role = {role: wt for (role, wt, _, _) in prepared}
        for role in sorted(out):
            if not entries[role].write_scope:
                continue
            stage_ok, _result, report_dict, _stage = out[role]
            if stage_ok:
                _apply_worktree_changes(repo, wt_by_role[role], report_dict.get("changed_files") or [])
    finally:
        for (_, wt, _, _) in prepared:
            subprocess.run(["git", "-C", str(repo), "worktree", "remove", "--force", str(wt)],
                           capture_output=True)
            shutil.rmtree(wt, ignore_errors=True)
    return out


def _advisory_producer_roles(entries: dict[str, RegistryEntry]) -> set[str]:
    return {
        role for role, entry in entries.items()
        if entry.output_to == AUFHEBEN_ROLE and not entry.write_scope
    }


def _has_valid_producer_for_aufheben(predecessors: dict[str, list[str]], producer_roles: set[str],
                                     results: dict[str, dict]) -> bool:
    return any(role in results for role in predecessors.get(AUFHEBEN_ROLE, []) if role in producer_roles)


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
    sessions: dict[str, str] = {}   # role -> its last codex session id, so a REPAIR re-run can RESUME it
    summary: dict[str, bool] = {}
    required_ok: dict[str, bool] = {}
    fatal_ok: dict[str, bool] = {}
    terminal_write_roles: list[str] = []
    stages: list[dict] = []
    iterations: list[dict] = []
    run_started_at = _utc_now()
    pipeline_failed = False
    producer_roles = _advisory_producer_roles(entries)

    repo_is_git = (repo / ".git").exists()
    for wave in _waves(ordered, predecessors):
        # WRITE roles run in their OWN worktree (off HEAD) so each role's scope check sees only its own
        # diff — an implementer is never charged for, nor sees, a CI writer's .github edits (per-stage
        # isolation; correctness does not depend on max_parallel). When max_parallel>1 the wave's
        # independent roles (producers AND write roles) additionally run CONCURRENTLY; otherwise they run
        # serially IN WAVE ORDER, write roles still isolated.
        outcomes: dict[str, tuple] = {}
        if repo_is_git and max_parallel > 1 and len(wave) > 1:
            outcomes.update(_run_wave_parallel(repo, sorted(wave), entries, objective, predecessors,
                                               results, run_id, cache, max_parallel))
        for role in wave:
            if role in outcomes:
                continue
            if role == AUFHEBEN_ROLE and not _has_valid_producer_for_aufheben(predecessors, producer_roles,
                                                                              results):
                fatal_ok[AUFHEBEN_ROLE] = False
                pipeline_failed = True
                break
            inputs = {u: results[u] for u in predecessors[role] if u in results}
            runner = (_execute_stage_isolated if (repo_is_git and entries[role].write_scope)
                      else _execute_stage)
            outcomes[role] = runner(repo, role, entries[role], objective, inputs,
                                    f"{run_id}-{role}", cache)
        if pipeline_failed:
            break
        for role in wave:                              # record deterministically in wave order
            entry = entries[role]
            stage_ok, result, report_dict, stage = outcomes[role]
            summary[role] = bool(report_dict.get("ok"))
            required_ok[role] = stage_ok
            if role not in producer_roles:
                fatal_ok[role] = stage_ok
            reports[role] = report_dict
            if report_dict.get("session_id"):           # capture the role's session so a REPAIR can RESUME it
                sessions[role] = report_dict["session_id"]
            stages.append(stage)
            _store_stage_output(role, entry, stage_ok, result, report_dict, results)
            if entry.output_to is None and entry.write_scope:
                terminal_write_roles.append(role)
            if not stage_ok and role not in producer_roles:
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
            fatal_ok[role] = stage_ok
            reports[role] = report_dict
            if report_dict.get("session_id"):
                sessions[role] = report_dict["session_id"]
            stages.append(stage)
            _store_stage_output(role, entry, stage_ok, result, report_dict, results)
            if not stage_ok:
                pipeline_failed = True
                break

    initial_finished_at = _utc_now()
    findings = _linon_findings(results.get("linon"))
    iterations.append(_iteration_record("initial", 0, run_started_at, initial_finished_at,
                                        list(stages), len(findings)))

    repair_iterations = 0
    repair_forward_roles = [
        role for role in ordered
        if role in producer_roles or role in {AUFHEBEN_ROLE, "implementer"}
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
                if role == AUFHEBEN_ROLE and not _has_valid_producer_for_aufheben(predecessors, producer_roles,
                                                                                  results):
                    fatal_ok[AUFHEBEN_ROLE] = False
                    pipeline_failed = True
                    break
                inputs = {upstream: results[upstream]
                          for upstream in predecessors[role] if upstream in results}
            stage_run_id = f"{run_id}-repair{repair_iterations}-{role}"
            # RESUME the prior session for the producers/implementer ONLY (full memory, small delta);
            # aufheben/other roles stay fresh. Chained: iteration N+1 resumes iteration N's session.
            resume = sessions.get(role) if role in SESSION_REUSE_ROLES else None
            stage_ok, result, report_dict, stage = _execute_stage(repo, role, entry, objective,
                                                                  inputs, stage_run_id, cache,
                                                                  resume_session=resume)
            if report_dict.get("session_id"):           # chain the next repair onto this iteration's session
                sessions[role] = report_dict["session_id"]
            summary[role] = bool(report_dict.get("ok"))
            required_ok[role] = stage_ok
            reports[role] = report_dict
            stages.append(stage)
            repair_stages.append(stage)
            _store_stage_output(role, entry, stage_ok, result, report_dict, results)
            if role not in producer_roles:
                fatal_ok[role] = stage_ok
            if not stage_ok and role not in producer_roles:
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
            _store_stage_output("linon", entry, stage_ok, result, report_dict, results)
            fatal_ok["linon"] = stage_ok
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
            "fatal_ok": fatal_ok,
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
    return 0 if all(result["fatal_ok"].values()) and result["converged"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
