#!/usr/bin/env python3
"""Replay one implementer leaf in an isolated scratch repo.

The probe answers a narrow question: if a fresh implementer carrier receives the same aufheben contract
and deterministic gate evidence the engine would feed to an implementer leaf, does the conformance gate
clear, improve, or show zero progress?
"""
from __future__ import annotations

import argparse
import contextlib
import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable, Iterable

HERE = Path(__file__).resolve().parent
ORG_ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ORG_ROOT / "packages" / "codex-org-bootstrap" / "src"))

import conformance  # noqa: E402
import controller_pipeline  # noqa: E402
import controller_run  # noqa: E402
import runner_factory  # noqa: E402

AUFHEBEN_ROLE = controller_pipeline.AUFHEBEN_ROLE
DETERMINISTIC_SOURCES = controller_pipeline._DETERMINISTIC_IMPL_SOURCES


class ProbeError(RuntimeError):
    pass


@contextlib.contextmanager
def _probe_env(scratch_repo: Path):
    old = {name: os.environ.get(name) for name in ("AI_ORG_ROOT", "STREAM_LOG")}
    os.environ.setdefault("AI_ORG_ROOT", str(ORG_ROOT))
    os.environ["STREAM_LOG"] = str(scratch_repo / ".agent-runs" / "stream.jsonl")
    try:
        yield
    finally:
        for name, value in old.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


@contextlib.contextmanager
def _patched_carrier(carrier: Callable | None):
    if carrier is None:
        yield
        return
    import implement_host

    old = implement_host._default_carrier
    implement_host._default_carrier = carrier
    try:
        yield
    finally:
        implement_host._default_carrier = old


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    cp = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if check and cp.returncode != 0:
        raise ProbeError(f"git {' '.join(args)} failed: {cp.stderr.strip()}")
    return cp


def _copy_repo_state(repo: Path) -> Path:
    if not repo.is_dir():
        raise ProbeError(f"repo does not exist or is not a directory: {repo}")
    root = Path(tempfile.mkdtemp(prefix="implementer-probe-"))
    scratch = root / "repo"

    def ignore(_dir: str, names: list[str]) -> set[str]:
        ignored = {".git", ".agent-runs", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
        return ignored.intersection(names)

    shutil.copytree(repo, scratch, ignore=ignore)
    _git(scratch, "init", "-q")
    _git(scratch, "config", "user.email", "implementer-probe@example.invalid")
    _git(scratch, "config", "user.name", "Implementer Probe")
    _git(scratch, "add", "-A")
    _git(scratch, "commit", "--allow-empty", "-q", "-m", "probe baseline")
    return scratch


def _load_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProbeError(f"could not read JSON from {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ProbeError(f"{path} must contain a JSON object")
    return data


def _looks_like_aufheben_contract(value: dict) -> bool:
    return (
        value.get("role_id") == AUFHEBEN_ROLE
        or (
            isinstance(value.get("files_allowed_to_change"), list)
            and isinstance(value.get("acceptance_criteria"), list)
            and isinstance(value.get("deliverable_kind"), str)
        )
    )


def _event_matches_leaf(event: dict, leaf: str) -> bool:
    if not leaf:
        return True
    run_id = str(event.get("run_id") or event.get("leaf") or event.get("leaf_id") or "")
    return leaf == run_id or leaf in run_id


def _iter_dicts(value) -> Iterable[dict]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _iter_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_dicts(child)


def _read_records(path: Path) -> tuple[list[dict], dict | None]:
    text = path.read_text(encoding="utf-8")
    try:
        loaded = json.loads(text)
        return [], loaded if isinstance(loaded, dict) else None
    except json.JSONDecodeError:
        pass

    records: list[dict] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ProbeError(f"{path}:{lineno}: invalid JSONL event: {exc}") from exc
        if isinstance(event, dict):
            records.append(event)
    return records, None


def _prompt_inputs(carrier_contract: dict | None) -> dict:
    if not isinstance(carrier_contract, dict):
        return {}
    try:
        payload = json.loads(carrier_contract.get("prompt") or "")
    except json.JSONDecodeError:
        return {}
    inputs = payload.get("inputs")
    return inputs if isinstance(inputs, dict) else {}


def _is_repair_continuation(carrier_contract: dict | None) -> bool:
    if not isinstance(carrier_contract, dict):
        return False
    try:
        payload = json.loads(carrier_contract.get("prompt") or "")
    except json.JSONDecodeError:
        return False
    return payload.get("mode") == "repair-continuation"


def _find_aufheben_contract(events: list[dict], blob: dict | None, leaf: str) -> tuple[dict | None, str]:
    candidates: list[tuple[str, dict]] = []
    if blob:
        results = blob.get("results") if isinstance(blob.get("results"), dict) else {}
        if isinstance(results.get(AUFHEBEN_ROLE), dict):
            candidates.append(("results.aufheben-designer", results[AUFHEBEN_ROLE]))
        for d in _iter_dicts(blob):
            if _looks_like_aufheben_contract(d):
                candidates.append(("json-record", d))
    for event in events:
        if not _event_matches_leaf(event, leaf):
            continue
        speech = event.get("speech")
        if isinstance(speech, dict) and _looks_like_aufheben_contract(speech):
            candidates.append((f"stream:{event.get('run_id')}:speech", speech))
        for d in _iter_dicts(event):
            if _looks_like_aufheben_contract(d):
                candidates.append((f"stream:{event.get('run_id')}", d))
    if candidates:
        source, contract = candidates[-1]
        return copy.deepcopy(contract), source
    return None, ""


def _find_implementer_contract(events: list[dict], blob: dict | None, leaf: str) -> tuple[dict | None, str]:
    candidates: list[tuple[str, dict]] = []
    dict_sources = []
    if blob:
        dict_sources.append(("json-record", blob))
    dict_sources.extend((f"stream:{e.get('run_id')}", e) for e in events if _event_matches_leaf(e, leaf))
    for source, root in dict_sources:
        for d in _iter_dicts(root):
            if d.get("role") == "implementer" and isinstance(d.get("contract_sent"), dict):
                candidates.append((source, d["contract_sent"]))
            elif d.get("role") == "implementer" and isinstance(d.get("prompt"), str):
                candidates.append((source, d))
    if not candidates:
        return None, ""

    def score(item: tuple[str, dict]) -> int:
        inputs = _prompt_inputs(item[1])
        return 1 if "gate_findings" in inputs else 0

    source, contract = sorted(candidates, key=score)[-1]
    return copy.deepcopy(contract), source


def _failed_deterministic(findings: list) -> list[dict]:
    out = []
    for finding in findings or []:
        if not isinstance(finding, dict) or finding.get("passed"):
            continue
        if finding.get("source") in DETERMINISTIC_SOURCES or finding.get("check") == "forbidden_pattern":
            out.append(copy.deepcopy(finding))
    return out


def _find_gate_findings(events: list[dict], blob: dict | None, leaf: str) -> tuple[list[dict], str]:
    groups: list[tuple[str, list[dict]]] = []
    if blob:
        for d in _iter_dicts(blob):
            failed = _failed_deterministic(d.get("findings") if isinstance(d.get("findings"), list) else [])
            if failed:
                groups.append(("json-record", failed))
    for event in events:
        if not _event_matches_leaf(event, leaf):
            continue
        failed = _failed_deterministic(event.get("findings") if isinstance(event.get("findings"), list) else [])
        if failed:
            groups.append((f"stream:{event.get('run_id')}:{event.get('source')}", failed))
    if not groups:
        return [], ""
    source, findings = groups[-1]
    return findings, source


def extract_from_run(path: Path, leaf: str) -> dict:
    events, blob = _read_records(path)
    full_contract, contract_source = _find_aufheben_contract(events, blob, leaf)
    carrier_contract, carrier_source = _find_implementer_contract(events, blob, leaf)
    findings, findings_source = _find_gate_findings(events, blob, leaf)
    if full_contract is None and carrier_contract is not None:
        visible = _prompt_inputs(carrier_contract).get(AUFHEBEN_ROLE)
        if isinstance(visible, dict):
            full_contract = copy.deepcopy(visible)
            contract_source = carrier_source + ":prompt-inputs"
    if full_contract is None:
        raise ProbeError(f"could not find an aufheben implementation contract for leaf {leaf!r} in {path}")
    return {
        "contract": full_contract,
        "carrier_contract": carrier_contract,
        "findings": findings,
        "sources": {
            "contract": contract_source,
            "carrier_contract": carrier_source,
            "findings": findings_source,
        },
    }


def infer_repo_from_run_path(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    parts = resolved.parts
    if ".agent-runs" in parts:
        idx = parts.index(".agent-runs")
        if idx > 0:
            return Path(*parts[:idx])
    return Path.cwd()


def _synthesize_implementer_contract(repo: Path, contract: dict, findings: list[dict]) -> dict:
    inputs = {AUFHEBEN_ROLE: contract}
    evidence = controller_pipeline._deterministic_repair_evidence(findings, {AUFHEBEN_ROLE: contract})
    if evidence:
        inputs["gate_findings"] = evidence
    visible_inputs = controller_pipeline._withhold_acceptance_bundle("implementer", inputs)
    entries = controller_pipeline._entries(repo)
    return controller_pipeline._contract(
        entries["implementer"],
        str(contract.get("objective") or "implement the aufheben contract"),
        visible_inputs,
        resume_session=None,
    )


def _count_by_pattern(report: dict) -> dict[str, int]:
    """Per-pattern BLOCKING occurrence count, matching what the gate actually enforces.

    Only the gate's blocking `findings` are counted. `advisory_findings` (e.g. forbidden tokens that
    survive OUTSIDE files_allowed_to_change, which `run_forbidden_patterns` reports as `out_of_scope=True`
    and `passed=True`) are deliberately excluded: counting them inflated `after_count`, so a carrier that
    cleaned the in-scope tree was scored "STUCK (zero progress)" even though a direct
    run_forbidden_patterns/run_conformance on the carrier's output passes the forbidden check. The probe's
    progress/verdict must equal the gate's blocking determination, not the advisory tally.
    """
    counts: dict[str, int] = {}
    for finding in report.get("findings") or []:
        if not isinstance(finding, dict) or not finding.get("pattern"):
            continue
        if finding.get("passed") or finding.get("out_of_scope"):
            continue  # passing / out-of-scope (advisory) hits are not blocking forbidden occurrences
        pattern = str(finding["pattern"])
        counts[pattern] = max(counts.get(pattern, 0), int(finding.get("count") or 0))
    return counts


def _forbidden_progress(contract: dict, before: dict, after: dict) -> list[dict]:
    patterns = [p for p in contract.get("forbidden_patterns") or [] if isinstance(p, dict) and p.get("pattern")]
    before_counts = _count_by_pattern(before)
    after_counts = _count_by_pattern(after)
    rows = []
    for spec in patterns:
        pattern = str(spec["pattern"])
        b = before_counts.get(pattern, 0)
        a = after_counts.get(pattern, 0)
        rows.append({
            "pattern": pattern,
            "scope": spec.get("scope", "leaf"),
            "max_occurrences": spec.get("max_occurrences", 0),
            "before_count": b,
            "after_count": a,
            "delta": a - b,
        })
    return rows


def _diff_summary(repo: Path, controller_report: dict) -> dict:
    stat = _git(repo, "diff", "--stat", check=False).stdout.strip()
    status = _git(repo, "status", "--short", check=False).stdout.splitlines()
    return {
        "changed_files": controller_report.get("changed_files") or [],
        "git_status": status,
        "stat": stat,
    }


def _verdict(controller_report: dict, conformance_report: dict, progress: list[dict]) -> str:
    remaining = [f for f in conformance_report.get("findings") or [] if isinstance(f, dict) and not f.get("passed")]
    if bool(controller_report.get("ok")) and bool(conformance_report.get("passed")):
        return "carrier COMPLETED"
    if remaining and progress:
        failing = [p for p in progress if p["after_count"] > p["max_occurrences"]]
        if failing and all(p["after_count"] == p["before_count"] for p in failing):
            return "carrier STUCK (zero progress)"
    if remaining and not controller_report.get("changed_files") and not controller_report.get("ok"):
        return "carrier STUCK (zero progress)"
    return "carrier PARTIAL"


def run_probe(repo: Path, contract: dict, *, findings: list[dict] | None = None,
              carrier_contract: dict | None = None, keep_scratch: bool = False,
              carrier: Callable | None = None, sources: dict | None = None) -> dict:
    scratch = _copy_repo_state(Path(repo).resolve())
    scratch_parent = scratch.parent
    try:
        with _probe_env(scratch):
            impl_contract = carrier_contract or _synthesize_implementer_contract(scratch, contract, findings or [])
            defect_locus = controller_pipeline._finding_defect_locus(findings or [])
            runner = runner_factory.get_conformance_runner()
            before_forbidden = conformance.run_forbidden_patterns(contract, cwd=str(scratch))
            with _patched_carrier(carrier):
                # Core fidelity point: this is the same controller entrypoint the pipeline uses for an
                # implementer leaf; controller_run injects role instructions and implement_host builds the
                # implementer prompt/build-map before calling carrier_harness.
                controller_report = controller_run.run(
                    scratch, impl_contract, "implementer-probe", cache=False,
                    resume_session=None, defect_locus=defect_locus,
                ).to_dict()
            after_conformance = conformance.run_conformance(contract, runner, cwd=str(scratch))
            progress = _forbidden_progress(contract, before_forbidden, after_conformance)
            report = {
                "verdict": _verdict(controller_report, after_conformance, progress),
                "converged": bool(controller_report.get("ok")) and bool(after_conformance.get("passed")),
                "scratch_repo": str(scratch) if keep_scratch else None,
                "sources": sources or {},
                "controller_report": controller_report,
                "conformance": after_conformance,
                "forbidden_pattern_progress": progress,
                "remaining_findings": [
                    f for f in after_conformance.get("findings") or []
                    if isinstance(f, dict) and not f.get("passed")
                ],
                "diff_summary": _diff_summary(scratch, controller_report),
            }
            return report
    finally:
        if not keep_scratch:
            shutil.rmtree(scratch_parent, ignore_errors=True)


def render_report(report: dict) -> str:
    lines = [
        "IMPLEMENTER PROBE REPORT",
        report["verdict"],
        f"converged: {str(report.get('converged')).lower()}",
    ]
    if report.get("scratch_repo"):
        lines.append(f"scratch_repo: {report['scratch_repo']}")
    if report.get("sources"):
        lines.append("sources:")
        for key, value in sorted(report["sources"].items()):
            if value:
                lines.append(f"  {key}: {value}")
    lines.append("forbidden_pattern_progress:")
    if report.get("forbidden_pattern_progress"):
        for row in report["forbidden_pattern_progress"]:
            lines.append(
                "  - "
                f"{row['pattern']} scope={row['scope']} "
                f"before={row['before_count']} after={row['after_count']} "
                f"max={row['max_occurrences']} delta={row['delta']}"
            )
    else:
        lines.append("  (none declared)")
    lines.append("remaining_findings:")
    if report.get("remaining_findings"):
        for finding in report["remaining_findings"]:
            detail = str(finding.get("detail") or "").replace("\n", " ")
            lines.append(
                "  - "
                f"{finding.get('source')}:{finding.get('check')} "
                f"severity={finding.get('severity')} detail={detail}"
            )
            if finding.get("fix_hint"):
                lines.append(f"    fix_hint: {finding['fix_hint']}")
    else:
        lines.append("  (none)")
    lines.append("diff_summary:")
    changed = report.get("diff_summary", {}).get("changed_files") or []
    lines.append(f"  changed_files: {', '.join(changed) if changed else '(none)'}")
    stat = report.get("diff_summary", {}).get("stat")
    if stat:
        lines.append("  stat:")
        lines.extend(f"    {line}" for line in stat.splitlines())
    unresolved = report.get("controller_report", {}).get("unresolved_failures") or []
    if unresolved:
        lines.append("controller_unresolved:")
        lines.extend(f"  - {item}" for item in unresolved)
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--contract", help="path to an aufheben implementation contract JSON")
    source.add_argument("--from-run", help="stream.jsonl or controller result JSON to extract from")
    parser.add_argument("--repo", help="repo to copy and probe; required with --contract")
    parser.add_argument("--leaf", help="leaf id/run-id substring to extract with --from-run")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    parser.add_argument("--keep-scratch", action="store_true", help="keep the scratch repo for inspection")
    args = parser.parse_args(argv)

    try:
        if args.contract:
            if not args.repo:
                raise ProbeError("--repo is required with --contract")
            contract = _load_json(Path(args.contract))
            payload = {
                "contract": contract,
                "findings": [],
                "carrier_contract": None,
                "sources": {"contract": args.contract},
            }
            repo = Path(args.repo)
        else:
            if not args.leaf:
                raise ProbeError("--leaf is required with --from-run")
            payload = extract_from_run(Path(args.from_run), args.leaf)
            repo = Path(args.repo) if args.repo else infer_repo_from_run_path(Path(args.from_run))
            if payload.get("findings") or _is_repair_continuation(payload.get("carrier_contract")):
                source_note = "synthesized fresh replay from extracted contract/findings"
                payload["carrier_contract"] = None
                payload.setdefault("sources", {})["carrier_contract_used"] = source_note
        report = run_probe(
            repo,
            payload["contract"],
            findings=payload.get("findings") or [],
            carrier_contract=payload.get("carrier_contract"),
            keep_scratch=args.keep_scratch,
            sources=payload.get("sources") or {},
        )
    except Exception as exc:  # noqa: BLE001 - CLI should make probe errors explicit and nonzero.
        print(f"implementer_probe.py: error: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True))
    else:
        print(render_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
