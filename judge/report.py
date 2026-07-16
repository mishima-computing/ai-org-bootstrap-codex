"""Human-readable rendering of a ProvenanceReport.

Pure formatting: quotes the record verbatim, adds nothing. The CLI calls
this; libraries should consume the dataclasses / to_dict() instead.
"""

from __future__ import annotations

from .model import CarrierCall, ProvenanceReport

_RULE = "-" * 72


def _section(title: str) -> str:
    return f"{_RULE}\n{title}\n{_RULE}"


def render(report: ProvenanceReport) -> str:
    out = []
    out.append(_section("JUDGE PROVENANCE TRACE"))
    out.append(f"vessel : {report.vessel}")
    out.append(f"branch : {report.branch}")
    out.append(f"body   : {report.body_path}")
    if report.at_commit:
        out.append(f"as of  : {report.at_commit} (origin mode: target "
                   "resolved at this commit, not the tip)")

    t = report.target
    out.append(_section("target"))
    if t.term:
        out.append(f"term      : {t.term!r}")
    if t.node_arg:
        out.append(f"node arg  : {t.node_arg}")
    if t.ok:
        out.append(f"json path : /{'/'.join(str(s) for s in (t.json_path or []))}")
        out.append(f"node id   : {t.node_id or '(no id-bearing ancestor)'}")
        if t.scalar_text:
            out.append(f"scalar    : {_clip(t.scalar_text, 220)!r}")
    else:
        out.append(f"BROKEN: {t.reason}")
        return "\n".join(out)

    v = report.version
    out.append(_section("field -> version"))
    if v.ok:
        out.append(f"commit    : {v.commit}")
        out.append(f"subject   : {v.commit_subject}")
        out.append(f"version   : {v.version_label or '(no version label in subject)'}")
        out.append(f"kind      : {v.change_kind}")
        out.append(f"window    : {v.window_start or '(first commit)'} .. {v.window_end}")
        for hit in v.round_records:
            out.append(
                f"round rec : {hit.record_path} "
                f"(round {hit.review_round}, author v{hit.author_version}, "
                f"listed as {hit.listed_as})")
        if not v.round_records:
            out.append("round rec : none names this node")
    else:
        out.append(f"BROKEN: {v.reason}")
        return "\n".join(out)

    r = report.run
    out.append(_section("version -> run"))
    if r.ok:
        out.append(f"run dir   : {r.run_dir}")
        out.append(f"span      : {r.span_start} .. {r.span_end}")
        out.append(f"series    : {'mentioned in supervisor stream' if r.series_mentioned else 'NOT mentioned (span-only match)'}")
        if r.steps:
            out.append(f"steps     : {', '.join(r.steps)}")
        if r.reason:
            out.append(f"note      : {r.reason}")
    else:
        out.append(f"BROKEN: {r.reason}")
        if r.candidates:
            out.append("candidates considered:")
            out.extend(f"  {c}" for c in r.candidates)
        return "\n".join(out)

    out.append(_section("run -> carrier call"))
    c = report.call
    if c is not None:
        out.append(f"call #    : {c.index}")
        out.append(f"step      : {c.step or '(unattributed)'} [{c.step_source}]")
        out.append(f"window    : {c.started_at or '?'} .. {c.completed_at}")
        out.append(f"exit code : {c.exit_code}")
        out.append(f"payload   : {c.payload_ref}")
        out.append(f"stdout    : {c.stdout_path}")
        out.append(f"stderr    : {c.stderr_path}")
        out.append(f"prompt    : {_clip(c.prompt_head, 160)!r}")
    else:
        out.append(f"BROKEN: {report.session.reason}")
        return "\n".join(out)

    s = report.session
    out.append(_section("call -> session"))
    if s.ok:
        out.append(f"session   : {s.uuid}")
        out.append(f"rollout   : {s.rollout_path}")
        out.append(f"cwd       : {s.cwd}")
        out.append(f"confidence: {s.confidence}")
    else:
        out.append(f"BROKEN: {s.reason}")
        return "\n".join(out)

    th = report.thinking
    out.append(_section("recorded thinking"))
    if th.ok:
        out.append(
            f"anchors   : rollout lines {', '.join(str(a) for a in th.anchor_lines)}"
            f" (matched on {th.matched_on})")
        if th.reason:
            out.append(f"note      : {th.reason}")
        for item in th.items:
            label = item.kind + (f" by {item.author}" if item.author else "")
            if item.encrypted:
                out.append(
                    f"  L{item.line_no:>5} [{label}] <encrypted_content;"
                    " plaintext not recorded>")
            else:
                out.append(f"  L{item.line_no:>5} [{label}]")
                for ln in item.text.splitlines() or [""]:
                    out.append(f"        | {ln}")
    else:
        out.append(f"BROKEN: {th.reason}")

    broken = report.broken_link
    out.append(_RULE)
    out.append("chain     : "
               + ("fully resolved" if not broken else f"broken at link {broken!r}"))
    return "\n".join(out)


def render_calls_table(calls: list) -> str:
    """Fixed-width table of CarrierCall rows."""
    header = (f"{'#':>4}  {'step':<28} {'completed_at':<28} "
              f"{'session':<36} {'conf':<18} stdout")
    lines = [header, "-" * len(header)]
    for c in calls:
        step = _clip(c.step or c.output_name or "?", 28)
        lines.append(
            f"{c.index:>4}  {step:<28} {c.completed_at:<28} "
            f"{c.session_uuid or '-':<36} {c.confidence:<18} "
            f"{c.stdout_path or '-'}")
    return "\n".join(lines)


def _clip(text: str, n: int) -> str:
    text = text.replace("\n", " ")
    return text if len(text) <= n else text[: n - 3] + "..."
