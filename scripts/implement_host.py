"""Python-cored implementer host.

The host owns deterministic grounding before the carrier runs: pre-localized real files, the goal WHY
threaded in memory, the implementation contract guards, and the existing guard scan. It never owns the
post-carrier gates and never narrows the implementer's write boundary; scope/conformance remain downstream.
"""
from __future__ import annotations

import fnmatch
import json
from pathlib import Path

import pre_localizer
from design_host import GuardScan

BUILD_MAP_HEADER = "## BUILD-MAP (deterministic implementer grounding)"
BUILD_MAP_FILE = "build-map.json"


def _default_carrier(repo, prompt, sandbox, *, timeout, retries, out_dir, resume_session):
    import carrier_harness
    return carrier_harness.run_carrier(repo, prompt, sandbox, timeout=timeout, retries=retries,
                                       out_dir=out_dir, resume_session=resume_session)


def prompt_payload(raw_prompt: str) -> dict:
    try:
        payload = json.loads(raw_prompt or "")
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def objective_from_prompt(raw_prompt: str) -> str:
    payload = prompt_payload(raw_prompt)
    obj = payload.get("objective")
    return obj if isinstance(obj, str) and obj.strip() else (raw_prompt or "")


def inputs_from_prompt(raw_prompt: str) -> dict:
    inputs = prompt_payload(raw_prompt).get("inputs")
    return inputs if isinstance(inputs, dict) else {}


def _matches_any(path: str, globs: list[str]) -> bool:
    return any(fnmatch.fnmatch(path, pattern) for pattern in globs)


def _contract_inputs(inputs: dict) -> dict:
    direct = inputs.get("aufheben-designer")
    if isinstance(direct, dict):
        return direct
    for value in inputs.values():
        if isinstance(value, dict) and (
            "files_allowed_to_change" in value or "acceptance_criteria" in value or "deliverable_kind" in value
        ):
            return value
    return {}


def _why_from_context(goal_context: dict | None) -> dict:
    sg = (goal_context or {}).get("structured_goal") if isinstance(goal_context, dict) else None
    if isinstance(sg, dict) and any(str(sg.get(k) or "").strip() for k in ("negative_control", "success_condition")):
        return {
            "status": "present",
            "source": "run_goal.context.structured_goal",
            "outcome": sg.get("outcome") or "",
            "success_condition": sg.get("success_condition") or "",
            "negative_control": sg.get("negative_control") or "",
            "owner": sg.get("owner") or "",
        }
    return {
        "status": "absent",
        "marker": "WHY:absent",
        "source": "run_goal.context.structured_goal",
        "note": "No structured negative_control/success_condition was supplied in memory; none was fabricated.",
    }


def build_map_for(repo, raw_prompt: str, *, contract_inputs: dict | None = None,
                  write_scope: list[str] | None = None, goal_context: dict | None = None) -> dict:
    rp = Path(repo).resolve()
    objective = objective_from_prompt(raw_prompt)
    inputs = contract_inputs if isinstance(contract_inputs, dict) else inputs_from_prompt(raw_prompt)
    impl_contract = _contract_inputs(inputs)
    allowed_globs = list(write_scope or impl_contract.get("files_allowed_to_change") or [])
    forbidden_globs = list(impl_contract.get("files_not_allowed_to_change") or [])

    index = pre_localizer.RepoIndex.cached(rp)
    candidates = pre_localizer.PreLocalizer(rp, index=index).candidates(objective)
    candidate_paths = {c.path for c in candidates}
    in_scope_candidates = [c for c in candidates if _matches_any(c.path, allowed_globs)]
    in_scope_real = sorted(p for p in index.paths if _matches_any(p, allowed_globs))

    guard_map = GuardScan(rp, [c.path for c in candidates]).build()
    guard_map["candidates"] = [{"path": c.path, "score": c.score, "reasons": c.reasons} for c in candidates]

    return {
        "objective": objective,
        "why": _why_from_context(goal_context),
        "scope": {
            "files_allowed_to_change": allowed_globs,
            "files_not_allowed_to_change": forbidden_globs,
            "note": "Pre-localization is advisory only. The write boundary remains files_allowed_to_change.",
        },
        "localization": {
            "in_scope_prelocalized": [
                {"path": c.path, "score": c.score, "reasons": c.reasons} for c in in_scope_candidates
            ],
            "in_scope_not_prelocalized": [
                {"path": p, "reason": "scope says writable; PreLocalizer did not surface it"}
                for p in in_scope_real if p not in candidate_paths
            ],
            "prelocalized_out_of_scope": [
                {"path": c.path, "score": c.score, "reasons": c.reasons}
                for c in candidates if not _matches_any(c.path, allowed_globs)
            ],
        },
        "contract_guards": {
            "deliverable_kind": impl_contract.get("deliverable_kind"),
            "acceptance_criteria": impl_contract.get("acceptance_criteria") or [],
            "required_checks": impl_contract.get("required_checks") or [],
            "conformance": impl_contract.get("conformance") or {},
        },
        "guard_scan": guard_map,
    }


def format_build_section(build_map: dict) -> str:
    why = build_map.get("why") or {}
    if why.get("status") == "present":
        why_line = (
            "WHY:present. Build toward the success_condition and keep the negative_control refutable. "
            f"negative_control: {why.get('negative_control') or ''}"
        )
    else:
        why_line = "WHY:absent. No structured negative_control was supplied; do not invent one."
    return "\n\n".join([
        BUILD_MAP_HEADER,
        why_line,
        "This grounding is ADDITIVE under ADR-0006. Prefer the in-scope pre-localized files when they fit, "
        "but every file matching files_allowed_to_change remains writable. If the needed file is outside "
        "files_allowed_to_change, stop and report that aufheben must widen scope; do not silently expand it.",
        "```json\n" + json.dumps(build_map, indent=2, ensure_ascii=False) + "\n```",
    ])


def make_implement_carrier_runner(repo, *, objective, contract_inputs=None, write_scope=None,
                                  goal_context=None, carrier=None, max_attempts=3):
    """Return a controller_workflow carrier_runner for the write-role implementer."""
    carrier = carrier or _default_carrier

    def runner(rp, prompt, sandbox, *, timeout=600, retries=1, out_dir=None, resume_session=None):
        rp = Path(rp).resolve()
        out_dir = Path(out_dir) if out_dir else (rp / ".agent-runs" / "carrier")
        out_dir.mkdir(parents=True, exist_ok=True)

        build_map = build_map_for(rp, objective, contract_inputs=contract_inputs,
                                  write_scope=write_scope, goal_context=goal_context)
        build_file = out_dir / BUILD_MAP_FILE
        build_file.write_text(json.dumps(build_map, indent=2, ensure_ascii=False), encoding="utf-8")

        base_prompt = format_build_section(build_map) + "\n\n---\n\n" + prompt
        session = resume_session
        all_attempts: list = []
        cr: dict = {"ok": False}
        for attempt in range(max_attempts):
            send = base_prompt if attempt == 0 else base_prompt + _transport_retry_note()
            cr = carrier(rp, send, sandbox, timeout=timeout, retries=0,
                         out_dir=out_dir / f"attempt{attempt}", resume_session=session)
            session = cr.get("session_id") or session
            for item in cr.get("attempts") or []:
                all_attempts.append({**item, "host_attempt": attempt} if isinstance(item, dict) else item)
            if cr.get("ok"):
                return {**cr, "ok": True, "attempts": all_attempts, "session_id": session}
        return {**cr, "ok": False, "attempts": all_attempts, "session_id": session}

    return runner


def _transport_retry_note() -> str:
    return ("\n\n## PRIOR IMPLEMENTER LAUNCH DID NOT COMPLETE\n"
            "Continue from the same BUILD-MAP. Make only in-scope implementation edits and let the existing "
            "scope/conformance gates judge the result.")
