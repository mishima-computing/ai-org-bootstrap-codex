"""Python-cored implementer host.

The host owns deterministic grounding before the carrier runs: pre-localized real files, the goal WHY
threaded in memory, the implementation contract guards, and the existing guard scan. It never owns the
post-carrier gates and never narrows the implementer's write boundary; scope/conformance remain downstream.
"""
from __future__ import annotations

import fnmatch
import json
import threading
from pathlib import Path

import pre_localizer
from design_host import GuardScan

BUILD_MAP_HEADER = "## BUILD-MAP (deterministic implementer grounding)"
BUILD_MAP_FILE = "build-map.json"

# A/B-aufheben cassette library (ADR-0006-additive). The LIVE pick is a deterministic lookup the
# implementer builds on immediately; the SHADOW pick is an aufheben-style query fired in parallel,
# observed-only, never on the launch's critical path. The library is JSON; bodies are disclosed to the
# implementer only when selected (progressive disclosure — aufheben sees only names + descriptions).
CASSETTES_FILE = Path(__file__).resolve().parent.parent / "cassettes" / "cassettes.json"


def _load_cassettes(path: Path | None = None) -> list[dict]:
    """Load the JSON cassette library. Fail-soft: a missing/malformed library yields no cassettes rather
    than breaking the implementer launch — cassettes are additive priming, never a hard dependency."""
    p = Path(path) if path else CASSETTES_FILE
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return [c for c in data if isinstance(c, dict) and c.get("name")] if isinstance(data, list) else []


def cassette_catalog(path: Path | None = None) -> list[dict]:
    """The TRACK LIST shown to aufheben: names + one-line descriptions ONLY, never the bodies
    (progressive disclosure — the prime text is disclosed only to the implementer, and only when picked)."""
    return [{"name": c.get("name"), "description": c.get("description", "")} for c in _load_cassettes(path)]


def _objective_text(objective: str) -> str:
    """The human task text, whether `objective` is a raw string or a JSON prompt payload."""
    return objective_from_prompt(objective) if isinstance(objective, str) else str(objective or "")


def _languages_of(candidate_files: list[str] | None) -> set[str]:
    exts = {Path(p).suffix.lstrip(".").lower() for p in (candidate_files or []) if p}
    alias = {"py": "python", "js": "javascript", "ts": "typescript", "go": "go", "rs": "rust",
             "java": "java", "rb": "ruby"}
    return {alias.get(e, e) for e in exts if e}


def select_cassettes(objective: str, contract: dict | None, candidate_files: list[str] | None = None,
                     *, path: Path | None = None) -> list[str]:
    """LIVE deterministic pick — a pure lookup over each cassette's `when` fields. Returns the cassette
    NAMES that match the objective / contract / candidate languages. `[]` means NONE (no priming). This is
    what the implementer builds on immediately; it never blocks and never calls the model."""
    contract = contract if isinstance(contract, dict) else {}
    text = _objective_text(objective).lower()
    deliverable_kind = contract.get("deliverable_kind")
    languages = _languages_of(candidate_files)
    selected: list[str] = []
    for c in _load_cassettes(path):
        when = c.get("when") if isinstance(c.get("when"), dict) else {}
        kinds = when.get("deliverable_kinds") or []
        task_types = when.get("task_types") or []
        when_langs = when.get("languages") or []
        match = (
            (deliverable_kind and deliverable_kind in kinds)
            or any(t and t.lower() in text for t in task_types)
            or (bool(when_langs) and bool(languages.intersection(l.lower() for l in when_langs)))
        )
        if match and c.get("name") not in selected:
            selected.append(c.get("name"))
    return selected


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
                  write_scope: list[str] | None = None, goal_context: dict | None = None,
                  defect_locus: dict | None = None) -> dict:
    rp = Path(repo).resolve()
    objective = objective_from_prompt(raw_prompt)
    inputs = contract_inputs if isinstance(contract_inputs, dict) else inputs_from_prompt(raw_prompt)
    impl_contract = _contract_inputs(inputs)
    allowed_globs = list(write_scope or impl_contract.get("files_allowed_to_change") or [])
    forbidden_globs = list(impl_contract.get("files_not_allowed_to_change") or [])

    index = pre_localizer.RepoIndex.cached(rp)
    # LIVE cassette pick (ADR-0006-additive): a deterministic lookup over the JSON library's `when` fields.
    # Selected bodies are folded into the build-map prompt below; an empty pick primes nothing. The pick is
    # pure — it never narrows the write boundary and never calls the model.
    cassette_names = set(select_cassettes(objective, impl_contract))
    # R4: on a repair, re-seed pre-localization from the blocking finding's defect_locus so the advisory
    # candidate set zooms to the failing region. No locus (first attempt) -> identical ranking to today. The
    # locus only RE-RANKS advisory candidates; the write boundary below stays files_allowed_to_change (ADR-0006).
    locus = defect_locus if isinstance(defect_locus, dict) and defect_locus else None
    candidates = pre_localizer.PreLocalizer(rp, index=index).candidates(objective, defect_locus=locus)
    candidate_paths = {c.path for c in candidates}
    in_scope_candidates = [c for c in candidates if _matches_any(c.path, allowed_globs)]
    in_scope_real = sorted(p for p in index.paths if _matches_any(p, allowed_globs))

    guard_map = GuardScan(rp, [c.path for c in candidates]).build()
    guard_map["candidates"] = [{"path": c.path, "score": c.score, "reasons": c.reasons} for c in candidates]

    localization = {
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
    }
    if locus:
        # R4: echo the failing region so the implementer sees WHERE (advisory; never widens scope).
        localization["defect_locus"] = {
            "file": locus.get("file") or locus.get("path"),
            "line_range": locus.get("line_range") or locus.get("lines"),
            "symbols": locus.get("symbols") or [],
            "note": "Repair re-localization: candidates re-ranked around this region. Advisory only — the "
                    "write boundary is still files_allowed_to_change.",
        }

    cassettes = [
        {"name": c.get("name"), "body": c.get("body", ""), "paired_gate": c.get("paired_gate", "")}
        for c in _load_cassettes() if c.get("name") in cassette_names
    ]

    return {
        "objective": objective,
        "why": _why_from_context(goal_context),
        "cassettes": cassettes,
        "scope": {
            "files_allowed_to_change": allowed_globs,
            "files_not_allowed_to_change": forbidden_globs,
            "note": "Pre-localization is advisory only. The write boundary remains files_allowed_to_change.",
        },
        "localization": localization,
        "contract_guards": {
            "deliverable_kind": impl_contract.get("deliverable_kind"),
            "acceptance_criteria": impl_contract.get("acceptance_criteria") or [],
            "required_checks": impl_contract.get("required_checks") or [],
            "conformance": impl_contract.get("conformance") or {},
        },
        "guard_scan": guard_map,
    }


def _scope_lists(build_map: dict) -> tuple[list[str], list[str]]:
    """Surface the contract's own allow/deny scope from the build_map (additive — restates, never invents)."""
    scope = build_map.get("scope") or {}
    allowed = [g for g in (scope.get("files_allowed_to_change") or []) if str(g).strip()]
    forbidden = [g for g in (scope.get("files_not_allowed_to_change") or []) if str(g).strip()]
    return allowed, forbidden


def _scope_block(build_map: dict) -> str:
    """Render the scope contract prominently at the top: allow-list, optional DO-NOT-TOUCH, self-check."""
    allowed, forbidden = _scope_lists(build_map)
    lines = ["## SCOPE (read first — controller_scope.enforce is the hard gate; this is your forward self-check)"]
    if allowed:
        lines.append("ALLOWED to change — only files matching these globs:")
        lines.extend(f"- {g}" for g in allowed)
    else:
        lines.append("ALLOWED to change: no allow-list was supplied; do not assume a wider boundary.")
    if forbidden:
        lines.append("DO-NOT-TOUCH — the contract explicitly denies these (editing them fails scope):")
        lines.extend(f"- {g}" for g in forbidden)
    lines.append(
        "PRE-FINISH SELF-CHECK: before finishing, list the files you changed and confirm each is within "
        "files_allowed_to_change. If you need a file outside it, STOP and report that aufheben must widen "
        "scope — do not silently expand scope."
    )
    return "\n".join(lines)


def _cassette_block(build_map: dict) -> str | None:
    """Render the LIVE-selected cassette bodies (the prime text) as a prominent priming section. Returns
    None when no cassette was selected, so the prompt carries no empty/noise section."""
    cassettes = [c for c in (build_map.get("cassettes") or []) if str(c.get("body") or "").strip()]
    if not cassettes:
        return None
    lines = ["## PRIMING CASSETTES (deterministically selected for this task — apply before you build)"]
    for c in cassettes:
        gate = f" [paired gate: {c['paired_gate']}]" if c.get("paired_gate") else ""
        lines.append(f"### {c.get('name')}{gate}")
        lines.append(str(c.get("body") or "").strip())
    return "\n\n".join(lines)


def format_build_section(build_map: dict) -> str:
    why = build_map.get("why") or {}
    if why.get("status") == "present":
        why_line = (
            "WHY:present. Build toward the success_condition and keep the negative_control refutable. "
            f"negative_control: {why.get('negative_control') or ''}"
        )
    else:
        why_line = "WHY:absent. No structured negative_control was supplied; do not invent one."
    sections = [
        BUILD_MAP_HEADER,
        _scope_block(build_map),
        why_line,
        "This grounding is ADDITIVE under ADR-0006. Prefer the in-scope pre-localized files when they fit, "
        "but every file matching files_allowed_to_change remains writable. If the needed file is outside "
        "files_allowed_to_change, stop and report that aufheben must widen scope; do not silently expand it.",
    ]
    cassette_block = _cassette_block(build_map)
    if cassette_block:
        sections.append(cassette_block)
    sections.append("```json\n" + json.dumps(build_map, indent=2, ensure_ascii=False) + "\n```")
    return "\n\n".join(sections)


def _stream_shadow_event(repo, event: dict) -> None:
    """Tee one cassette_shadow event onto the shared stream. Fail-soft — observability never breaks a run."""
    try:
        import controller_pipeline
        controller_pipeline._stream_append(repo, event)
    except Exception:                                  # noqa: BLE001 - the shadow is observed-only
        pass


def fire_cassette_shadow(repo, objective, contract, candidate_files=None, *, run_id=None,
                         aufheben_query=None, deterministic_pick=None) -> threading.Thread | None:
    """SHADOW (the experiment): fire-and-forget an aufheben-style query in PARALLEL with the implementer
    launch, then stream `{"type":"cassette_shadow","deterministic_pick":[..],"aufheben_pick":[..],"run_id":..}`.

    aufheben is shown ONLY the track list (names + descriptions) + an explicit NONE option, and asked to pick
    0-2 names. The launch MUST NOT block on or fail from this: the query runs on a daemon thread and every
    error is swallowed. `aufheben_query(catalog, none_option)` returns the picked names (or anything; it is
    normalized). When no query is supplied, the shadow still records the deterministic pick with an empty
    aufheben pick. Returns the started Thread (so a caller/test can join it) or None if it could not start."""
    if deterministic_pick is None:
        deterministic_pick = select_cassettes(objective, contract, candidate_files)
    catalog = cassette_catalog()
    none_option = {"name": "NONE", "description": "No cassette fits this task — prime nothing."}

    def _normalize_pick(raw) -> list[str]:
        names = {c["name"] for c in catalog}
        picks = []
        for item in (raw or []):
            n = item.get("name") if isinstance(item, dict) else item
            if isinstance(n, str) and n in names and n not in picks:
                picks.append(n)
        return picks[:2]   # aufheben is asked for 0-2 names

    def _worker():
        aufheben_pick: list[str] = []
        error = None
        try:
            if aufheben_query is not None:
                aufheben_pick = _normalize_pick(aufheben_query(catalog, none_option))
        except Exception as exc:                       # noqa: BLE001 - shadow failure must not surface
            error = repr(exc)
        event = {"source": "aufheben", "type": "cassette_shadow", "run_id": run_id,
                 "deterministic_pick": list(deterministic_pick), "aufheben_pick": aufheben_pick}
        if error is not None:
            event["error"] = error
        _stream_shadow_event(repo, event)

    try:
        t = threading.Thread(target=_worker, name="cassette-shadow", daemon=True)
        t.start()
        return t
    except Exception:                                  # noqa: BLE001 - never block/fail the launch
        return None


def default_aufheben_query(repo, objective, *, sandbox="read-only", timeout=120):
    """Build a lightweight, read-only aufheben-style query callable for the SHADOW lane. It shows aufheben
    ONLY the track list (names + descriptions) + an explicit NONE option and asks for 0-2 names — the prime
    bodies are never disclosed (progressive disclosure). Returns a callable(catalog, none_option) -> raw pick.
    Every error is swallowed by fire_cassette_shadow, so this never endangers the implementer launch."""
    def query(catalog, none_option):
        import carrier_harness
        tracks = "\n".join(f"- {c['name']}: {c['description']}" for c in catalog)
        prompt = (
            "You are aufheben. Pick the priming cassettes that best fit this implementation task. You see only "
            "the TRACK LIST (names + one-line descriptions); choose 0-2 names, or NONE if none fit.\n\n"
            f"OBJECTIVE:\n{_objective_text(objective)}\n\nTRACK LIST:\n{tracks}\n- {none_option['name']}: "
            f"{none_option['description']}\n\n"
            'Reply with raw JSON only: {"pick": ["<name>", ...]}  (empty list == NONE).'
        )
        cr = carrier_harness.run_carrier(Path(repo).resolve(), prompt, sandbox, timeout=timeout, retries=0)
        text = (cr or {}).get("final_message") or (cr or {}).get("output") or ""
        try:
            return (json.loads(text) or {}).get("pick") or []
        except (TypeError, json.JSONDecodeError):
            return []
    return query


def make_implement_carrier_runner(repo, *, objective, contract_inputs=None, write_scope=None,
                                  goal_context=None, carrier=None, max_attempts=3, defect_locus=None,
                                  aufheben_query=None, run_id=None):
    """Return a controller_workflow carrier_runner for the write-role implementer.

    `defect_locus` (R4): on a repair, the blocking finding's failing region re-seeds pre-localization so the
    advisory candidate set zooms to it. None on a first attempt -> identical grounding to today."""
    carrier = carrier or _default_carrier

    def runner(rp, prompt, sandbox, *, timeout=600, retries=1, out_dir=None, resume_session=None):
        rp = Path(rp).resolve()
        out_dir = Path(out_dir) if out_dir else (rp / ".agent-runs" / "carrier")
        out_dir.mkdir(parents=True, exist_ok=True)

        build_map = build_map_for(rp, objective, contract_inputs=contract_inputs,
                                  write_scope=write_scope, goal_context=goal_context,
                                  defect_locus=defect_locus)
        build_file = out_dir / BUILD_MAP_FILE
        build_file.write_text(json.dumps(build_map, indent=2, ensure_ascii=False), encoding="utf-8")

        base_prompt = format_build_section(build_map) + "\n\n---\n\n" + prompt

        # SHADOW (the experiment): fire the aufheben-style query in PARALLEL and stream cassette_shadow.
        # Fire-and-forget — the implementer launches on the LIVE deterministic pick immediately below; the
        # shadow must not block or fail it. deterministic_pick mirrors the LIVE pick already in the build-map.
        live_pick = [c.get("name") for c in (build_map.get("cassettes") or []) if c.get("name")]
        try:
            fire_cassette_shadow(rp, objective, _contract_inputs(
                contract_inputs if isinstance(contract_inputs, dict) else inputs_from_prompt(objective)),
                run_id=run_id, aufheben_query=aufheben_query, deterministic_pick=live_pick)
        except Exception:                              # noqa: BLE001 - shadow can never break the launch
            pass

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
