"""Chain links 2-3: version -> run -> carrier call.

Run log anatomy (verified against live runs, 2026-07-11):
    <vessel>/.ai-org/log/runs/<YYYY-MM-DD>/run-<stamp>/
        supervisor.jsonl            # event-sourced supervisor stream
        artifacts/payloads/         # spill files for oversized payloads
        artifacts/subprocess/       # captured stdout/stderr of subprocesses

Facts the resolver depends on (Memento tattoo -- verified, not assumed):
  * Event payloads larger than the inline cap are recorded as
    {"truncated": "<prefix>"}; the FULL payload is spilled to
    artifacts/payloads/<event_id>.json. Every truncated event observed in
    live runs has its spill file.
  * subprocess.completed payloads carry argv (whose last element is the
    prompt for codex calls), stdout_ref/stderr_ref with paths relative to
    the run dir, and exit_code.
  * approach runs emit approach.step.started/completed windows carrying the
    step name; author-revision (reform) runs do not, but every codex argv
    carries "-o .../patch_series-<step-shaped-name>.json", so the step
    identity is recoverable from the output basename.

READ-ONLY: this module never writes into a run dir.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterator, Optional

from .model import CarrierCall, RunLink, Target

SUPERVISOR = "supervisor.jsonl"
PAYLOADS_DIR = os.path.join("artifacts", "payloads")

# Tolerance when checking "run span covers the commit time": commit and the
# final run event can land on the same second with sub-second skew.
SPAN_TOLERANCE_SECONDS = 10.0

_STEP_OUTPUT_RE = re.compile(r"patch_series-(?P<name>[a-z0-9_-]+)\.json$")

# Memento: the authoring worker owns the patch_author.code_worker namespace for all Codex calls (ai_org/patch_author/code_worker.py: .codex L861, .contingency L1051, .contingency_pivot L1426, .feedback.codex L1849); precedent (engineering_precedent_store.*), reference, and review lack this prefix.
AUTHORING_STAGE_PREFIX = "patch_author.code_worker"


def parse_ts(value: str) -> Optional[datetime]:
    """ISO timestamp (with Z or offset) -> aware UTC datetime."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return dt.astimezone(timezone.utc)


@dataclass
class RunEvent:
    line_no: int
    event_type: str
    event_id: str
    occurred_at: str
    payload: dict
    truncated: bool  # payload arrived truncated in the stream
    resolved: bool  # full payload recovered from the spill file
    # Span-correlation fields (top-level in the JSONL, never truncated). Added
    # for log_perf_projection_tool, which assembles the span tree; judge itself
    # does not read these. Defaults keep every existing call site backward
    # compatible. span_id pairs named spans (<name>.started/.completed share it);
    # subprocess.* events inherit the PARENT span's span_id, so subprocess
    # start/end are paired by causation_event_id instead.
    span_id: str = ""
    parent_span_id: str = ""
    causation_event_id: str = ""
    stage: str = ""


def iter_events(run_dir: str) -> Iterator[RunEvent]:
    """Parse supervisor.jsonl, resolving truncated payloads via spill files."""
    sup = os.path.join(run_dir, SUPERVISOR)
    try:
        f = open(sup, "r", encoding="utf-8", errors="replace")
    except OSError:
        return
    with f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict):
                continue
            payload = obj.get("payload")
            payload = payload if isinstance(payload, dict) else {}
            truncated = set(payload.keys()) == {"truncated"}
            resolved = False
            event_id = str(obj.get("event_id") or "")
            if truncated and event_id:
                spill = os.path.join(run_dir, PAYLOADS_DIR, event_id + ".json")
                try:
                    with open(spill, "r", encoding="utf-8",
                              errors="replace") as sf:
                        full = json.load(sf)
                    if isinstance(full, dict):
                        payload = full
                        resolved = True
                except (OSError, json.JSONDecodeError):
                    pass
            yield RunEvent(
                line_no=line_no,
                event_type=str(obj.get("event_type") or ""),
                event_id=event_id,
                occurred_at=str(obj.get("occurred_at") or ""),
                payload=payload,
                truncated=truncated,
                resolved=resolved,
                span_id=str(obj.get("span_id") or ""),
                parent_span_id=str(obj.get("parent_span_id") or ""),
                causation_event_id=str(obj.get("causation_event_id") or ""),
                stage=str(obj.get("stage") or ""),
            )


@dataclass
class StepWindow:
    step: str
    started_at: str = ""
    completed_at: str = ""


@dataclass
class Run:
    """Parsed view of one run dir: span, step windows, carrier calls."""

    run_dir: str
    span_start: str = ""
    span_end: str = ""
    step_windows: list = field(default_factory=list)
    carrier_calls: list = field(default_factory=list)  # list[CarrierCall]
    unresolved_truncated: int = 0

    def mentions(self, needle: str) -> bool:
        """Raw containment scan of the supervisor stream (cheap, exact)."""
        sup = os.path.join(self.run_dir, SUPERVISOR)
        try:
            with open(sup, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    if needle in line:
                        return True
        except OSError:
            return False
        return False


def _step_from_output_name(argv: list) -> str:
    """Derive a step-shaped name from the '-o <path>' element of argv."""
    for i, item in enumerate(argv):
        if item == "-o" and i + 1 < len(argv):
            m = _STEP_OUTPUT_RE.search(os.path.basename(argv[i + 1]))
            if m:
                return m.group("name").replace("-", "_")
            return os.path.basename(argv[i + 1])
    return ""


def _argv_key(argv: list) -> str:
    return "\x00".join(str(a) for a in argv)


def load_run(run_dir: str) -> Run:
    """Build the Run view: span, step windows, and paired carrier calls."""
    run = Run(run_dir=os.path.abspath(run_dir))
    starts: dict = {}  # argv key -> list of (occurred_at, line_no)
    calls: list[CarrierCall] = []
    current_step: Optional[StepWindow] = None

    for ev in iter_events(run_dir):
        if ev.occurred_at:
            if not run.span_start:
                run.span_start = ev.occurred_at
            run.span_end = ev.occurred_at
        if ev.truncated and not ev.resolved:
            run.unresolved_truncated += 1

        if ev.event_type == "approach.step.started":
            step = ev.payload.get("step")
            if isinstance(step, str):
                current_step = StepWindow(step=step, started_at=ev.occurred_at)
                run.step_windows.append(current_step)
        elif ev.event_type == "approach.step.completed":
            step = ev.payload.get("step")
            if current_step is not None and (
                    not isinstance(step, str) or step == current_step.step):
                current_step.completed_at = ev.occurred_at
                current_step = None
            elif isinstance(step, str):
                run.step_windows.append(StepWindow(
                    step=step, completed_at=ev.occurred_at))
        elif ev.event_type == "subprocess.started":
            argv = ev.payload.get("argv")
            if isinstance(argv, list) and argv and argv[0] == "codex":
                starts.setdefault(_argv_key(argv), []).append(ev.occurred_at)
        elif ev.event_type == "subprocess.completed":
            argv = ev.payload.get("argv")
            if not (isinstance(argv, list) and argv and argv[0] == "codex"):
                continue
            key = _argv_key(argv)
            started_at = ""
            if starts.get(key):
                started_at = starts[key].pop(0)
            prompt = argv[-1] if argv else ""
            stdout_ref = ev.payload.get("stdout_ref") or {}
            stderr_ref = ev.payload.get("stderr_ref") or {}
            if ev.resolved:
                payload_ref = os.path.join(PAYLOADS_DIR, ev.event_id + ".json")
            else:
                payload_ref = f"{SUPERVISOR}:{ev.line_no}"
            calls.append(CarrierCall(
                index=len(calls),
                started_at=started_at,
                completed_at=ev.occurred_at,
                exit_code=ev.payload.get("exit_code"),
                argv0=argv[0],
                output_name=_step_from_output_name(argv),
                prompt_head=prompt[:200],
                payload_ref=payload_ref,
                stdout_path=_rel_artifact(stdout_ref),
                stderr_path=_rel_artifact(stderr_ref),
                stage=ev.stage,
            ))

    _attribute_steps(run, calls)
    run.carrier_calls = calls
    return run


def _rel_artifact(ref: dict) -> str:
    path = ref.get("path") if isinstance(ref, dict) else None
    return path if isinstance(path, str) else ""


def _attribute_steps(run: Run, calls: list) -> None:
    """Step identity: approach windows first, output basename as fallback."""
    windows = []
    for w in run.step_windows:
        t0 = parse_ts(w.started_at)
        t1 = parse_ts(w.completed_at)
        if t1 is not None:
            windows.append((w.step, t0, t1))
    for call in calls:
        tc = parse_ts(call.completed_at)
        attributed = False
        if tc is not None:
            for step, t0, t1 in windows:
                if (t0 is None or t0 <= tc) and tc <= t1:
                    call.step = step
                    call.step_source = "approach-window"
                    attributed = True
                    break
        if not attributed and call.output_name:
            call.step = call.output_name
            call.step_source = "output-name"


# ---------------------------------------------------------------------------
# Run discovery / selection
# ---------------------------------------------------------------------------

def find_run_dirs(vessel: str) -> list[str]:
    """All run dirs under <vessel>/.ai-org/log/runs, sorted by name."""
    root = os.path.join(vessel, ".ai-org", "log", "runs")
    out: list[str] = []
    if not os.path.isdir(root):
        return out
    for day in sorted(os.listdir(root)):
        day_dir = os.path.join(root, day)
        if not os.path.isdir(day_dir):
            continue
        for name in sorted(os.listdir(day_dir)):
            if name.startswith("run-"):
                run_dir = os.path.join(day_dir, name)
                if os.path.isfile(os.path.join(run_dir, SUPERVISOR)):
                    out.append(run_dir)
    return out


def run_span(run_dir: str) -> tuple:
    """(first, last) occurred_at of the supervisor stream (ISO strings)."""
    first = last = ""
    for ev in iter_events(run_dir):
        if ev.occurred_at:
            if not first:
                first = ev.occurred_at
            last = ev.occurred_at
    return first, last


def _covers(span: tuple, when: datetime) -> bool:
    t0 = parse_ts(span[0])
    t1 = parse_ts(span[1])
    if t0 is None or t1 is None:
        return False
    lo = t0.timestamp() - SPAN_TOLERANCE_SECONDS
    hi = t1.timestamp() + SPAN_TOLERANCE_SECONDS
    return lo <= when.timestamp() <= hi


def series_id_of_branch(branch: str) -> str:
    return branch.rstrip("/").rsplit("/", 1)[-1]


def stdout_contains(run_dir: str, call: CarrierCall, needles: list) -> bool:
    """Whether the call's captured stdout contains any needle.

    Matches both the raw form and the JSON-escaped form, because carrier
    stdout is itself JSON and the body scalar may appear escaped inside it.
    """
    if not call.stdout_path:
        return False
    path = os.path.join(run_dir, call.stdout_path)
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return False
    return contains_any_form(text, needles)


def contains_any_form(haystack: str, needles: list) -> bool:
    for needle in needles:
        if not needle:
            continue
        for form in escape_forms(needle):
            if form in haystack:
                return True
    return False


def escape_forms(needle: str) -> list:
    """Raw + JSON-escaped variants of a probe string (deterministic)."""
    forms = [needle]
    escaped = json.dumps(needle, ensure_ascii=False)[1:-1]
    if escaped != needle:
        forms.append(escaped)
    ascii_escaped = json.dumps(needle, ensure_ascii=True)[1:-1]
    if ascii_escaped not in forms:
        forms.append(ascii_escaped)
    return forms


def select_run(vessel: str, branch: str, commit_time_iso: str,
               probe_texts: list) -> tuple:
    """Pick the producing run for a commit: (RunLink, Run|None).

    Criteria, all mechanical:
      1. supervisor span covers the commit time (with tolerance);
      2. the supervisor stream mentions the series id of the branch;
      3. among survivors, prefer a run owning a carrier call whose stdout
         contains the target text.
    """
    link = RunLink()
    when = parse_ts(commit_time_iso)
    if when is None:
        link.reason = f"unparseable commit time {commit_time_iso!r}"
        return link, None
    run_dirs = find_run_dirs(vessel)
    if not run_dirs:
        link.reason = f"no runs found under {vessel}/.ai-org/log/runs"
        return link, None

    series_id = series_id_of_branch(branch)
    covering = [d for d in run_dirs if _covers(run_span(d), when)]
    if not covering:
        link.reason = (
            f"no run's supervisor span covers commit time {commit_time_iso}")
        return link, None
    link.candidates = list(covering)

    loaded = [load_run(d) for d in covering]
    mentioning = [r for r in loaded if r.mentions(series_id)]
    pool = mentioning or loaded

    chosen = None
    for run in pool:
        for call in run.carrier_calls:
            if stdout_contains(run.run_dir, call, probe_texts):
                call.content_hit = True
                chosen = run
                break
        if chosen is not None:
            break
    if chosen is None:
        if len(pool) == 1:
            chosen = pool[0]
            link.reason = (
                "selected by span+series-mention only; no carrier stdout "
                "contains the target text")
        else:
            link.reason = (
                f"{len(pool)} runs cover the commit time and mention the "
                "series, but none has a carrier stdout containing the "
                "target text; cannot adjudicate between them")
            return link, None

    link.ok = True
    link.run_dir = chosen.run_dir
    link.span_start = chosen.span_start
    link.span_end = chosen.span_end
    link.series_mentioned = chosen in mentioning
    link.steps = [w.step for w in chosen.step_windows] or sorted(
        {c.step for c in chosen.carrier_calls if c.step})
    return link, chosen


def select_call(run: Run, probe_texts: list) -> tuple:
    """The earliest authoring carrier call whose stdout contains the target.

    Returns (call | None, all_matching_calls).
    """
    matches = []
    for call in run.carrier_calls:
        if stdout_contains(run.run_dir, call, probe_texts):
            call.content_hit = True
            matches.append(call)
    producers = [
        call for call in matches
        if call.stage.startswith(AUTHORING_STAGE_PREFIX)
    ]
    return (producers[0] if producers else None), producers
