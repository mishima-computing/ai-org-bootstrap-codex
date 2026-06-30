# review.py — this IS the RFC FORMATION / MATURATION engine. It was mis-framed (and I, via amnesia,
# treated it) as a SEPARATE "direction review" phase. It is NOT separate. The RFC phase = receive
# (intake of a raw REQUEST) + THIS (mature that request into a contributor-takeable RFC). The 5 roles
# + Aufheben loop below ARE the maturation ("the 5 過程" / the vetting): they REFINE the request's
# common-8 fields and CRAFT the exit-only fields, converging on a contributor-takeable RFC (buy-in)
# or sending it back / NAK. (See receive.py top for the input/output field contract.)
#
# OPEN (to resolve when this is rewritten — step 2): the input should be the REQUEST (not an already
# finished rfc.json), the output the crafted RFC; and reconcile the 5 dimensions named here
# (need/approach/compat/scope/maintenance) with the early-stage 5 過程
# (specify problem / early discussion / who to talk to / when to post / get buy-in).
#
# REQUIREMENT — formation must GROUND a rough request, not just polish it (AI Org's 守備範囲):
#   Handling a vague / sloppy / even-WRONG request and still producing the RIGHT RFC is the AI Org's job,
#   not the user's. A request like "make a game like <kumo>" must trigger the formation to RESEARCH what
#   is actually being asked — the real referenced product/genre, prior art, and the repo context — and
#   correct the specification (e.g. "kumo" is an auto-battle party dungeon RPG, NOT a maze arcade). The
#   current loop only REFINES the user's wording, so a wrong/off-genre request passes through as a clean
#   but WRONG RFC (proven: a "spider labyrinth" request yielded a polished maze-arcade RFC for a game that
#   is actually an idle RPG). The formation needs a grounding/research step (specify-the-problem + prior-art
#   from the "過去の蓄積" research) that turns 雑 -> correct. Until that exists, GIGO: garbage request in,
#   garbage (well-formatted) RFC out. This is the core of the deferred RFC-formation 作り込み.
#
# ── REFERENCE: how the Linux community FORMS an RFC (grounded by research; our design may differ/evolve,
#    but this is the model we are abstracting — keep it here so we can revise against it). The number "5"
#    is NOT absolute: it is one doc's enumeration; the real process varies and has no global fixed rule.
#
#    A) Before posting — early-stage formation (kernel.org process/3.Early-stage):
#         specify the problem (what/who/where-it-falls-short) · early discussion (surface objections/
#         alternatives BEFORE code) · who do you talk to (route to the right list/maintainers, MAINTAINERS
#         / get_maintainer.pl) · when to post (problem + approach solid enough to act on) · get buy-in.
#    B) The RFC artifact posted: an [RFC]/[RFC PATCH] cover letter — a CONCRETE object reviewers can argue
#         about: problem/motivation, proposed design + interface, comparison to ALTERNATIVES/prior-art,
#         tradeoffs, TODO/open-questions, deliberate scope decisions, diffstat. (kpatch RFC: lkml 1405.0/00278)
#    C) Discussion / maturation: reviewers bring expertise AND alternatives (kGraft author proposed a better
#         approach), Reviewed-by/Acked-by/Nacked-by, repost as [RFC v2]/[v3]; iterate to consensus or it is
#         sent back / dropped.
#    D) Lifecycle around it (kernel.org process/2.Process, 5+1): Design · Early review · Wider review ·
#         Merging into mainline · Stable release (· long-term maintenance).
#    E) Transition: when consensus + review issues resolved + code ready, the subject changes RFC -> PATCH
#         and it becomes a real, bisectable patch series headed for a subsystem tree -> linux-next -> mainline.
#    Sources: kernel.org process/{2.Process,3.Early-stage,6.Followthrough}; Rust RFC 0000-template; PEP 12;
#    Fuchsia RFC best_practices; Google "Design Docs"; LWN kpatch (597123) + livepatch (634649).
"""RFC review — debate the DIRECTION, not the code.

Mirrors how a Linux subsystem maintainer + community review an RFC on the mailing list: they
argue about whether the change is wanted and whether the approach/interface is right, long
before any patch is reviewed line-by-line.

Five independent reviewers (one LLM-backed role each), one concern apiece:

  1. NEED        — is this change wanted at all? (problem legitimacy; may reject/NAK outright)
  2. APPROACH    — is the design / interface / API right? are there better alternatives?
  3. COMPAT      — does it break existing behavior or violate conventions? ("don't break userspace")
  4. SCOPE       — is the scope right? how should it be split? what is a prerequisite?
  5. MAINTENANCE — who maintains it? is the burden justified?

Resolution loop (decided design):
  - each reviewer emits its objections (指摘) on the current RFC view,
  - the AUFHEBEN consolidates the five into one structured revised RFC view,
    ONCE per round, or escalates a fundamental contradiction,
  - the five reviewers then re-critique that consolidation,
  - repeat until ALL FIVE have NO unresolved objection (CONVERGED), up to CAP rounds.

Outcomes:
  - DIRECTION-OK : converged within CAP (no unresolved objection) -> proceed to a real patch series.
  - NAK (reject) : did NOT converge within CAP, or Aufheben found a fundamental contradiction
                   -> rejected; the result returns which dimensions resolved and which objections
                   remain unresolved.
CAP is tentatively 5 — kept low on purpose to OBSERVE the loop's behavior and each LLM's behavior
before tuning or removing it. There is no separate "revise" outcome: revision IS the loop (the
aufheben revises, the five re-critique); only convergence (OK) and non-convergence (NAK) are terminal.

The loop/orchestration below is real; the reviewer and aufheben calls run Codex directly.
"""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Any
from dataclasses import dataclass, field


# --- the five review dimensions -------------------------------------------------------------
@dataclass
class Dimension:
    key: str
    blurb: str   # what this reviewer is responsible for judging


DIMENSIONS: list[Dimension] = [
    Dimension("need", "Is this change wanted at all? problem legitimacy; may NAK."),
    Dimension("approach", "Is the design/interface/API right? better alternatives?"),
    Dimension("compat", "Does it break existing behavior or conventions? don't break userspace."),
    Dimension("scope", "Is the scope right? how to split? what is a prerequisite?"),
    Dimension("maintenance", "Who maintains it? is the burden justified?"),
]

# Tentative round cap. Low on purpose: observe loop + per-LLM behavior before tuning/removing.
CAP = 5

# codex --output-schema constraints (PROVEN against real codex v0.142.0, 2026-06-29):
#   - NO allOf / anyOf / oneOf / if-then        (HTTP 400: "'allOf' is not permitted")
#   - additionalProperties MUST be false
#   - `required` MUST list EVERY key in `properties` (no optional fields)
#     (HTTP 400: "'required' ... including every key in properties. Missing 'escalation_reason'")
#     To make a field effectively optional: keep it in required and let the model return "" (or use type ["...","null"]).
OBJECTION_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": ["has_objection", "detail"],
    "properties": {
        "has_objection": {"type": "boolean"},
        "detail": {"type": "string"},
    },
}

RFC_VIEW_FIELDS = (
    "title",
    "problem",
    "proposal",
    "alternatives",
    "intended_users",
    "affected_area",
    "impact",
    "context",
)

AUFHEBEN_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": ["verdict", "revised_rfc", "situation_read", "escalation_reason"],
    "properties": {
        "verdict": {"enum": ["proceed", "escalate"]},
        "revised_rfc": {
            "type": "object",
            "additionalProperties": False,
            "required": list(RFC_VIEW_FIELDS),
            "properties": {
                "title": {"type": "string"},
                "problem": {"type": "string"},
                "proposal": {"type": "string"},
                "alternatives": {"type": "array", "items": {"type": "string"}},
                "intended_users": {"type": "string"},
                "affected_area": {"type": "string"},
                "impact": {"type": "string"},
                "context": {"type": "string"},
            },
        },
        "situation_read": {"type": "string", "maxLength": 1000},
        "escalation_reason": {"type": "string"},
    },
}


@dataclass
class Objection:
    dimension: str
    has_objection: bool
    detail: str = ""


@dataclass
class AufhebenDecision:
    verdict: str
    revised_rfc: dict[str, Any]
    situation_read: str
    escalation_reason: str = ""


@dataclass
class ReviewResult:
    status: str                 # "direction-ok" (converged) | "nak" (not converged within CAP)
    rounds: int                 # rounds actually run
    final_view: dict[str, Any] | str  # the latest structured RFC view (or "" if none)
    resolved: list = field(default_factory=list)     # dimension keys with NO objection at the end
    unresolved: list = field(default_factory=list)   # Objection list still open at the end (NAK)
    history: list = field(default_factory=list)       # per-round objections, for the record
    escalation_reason: str = ""


def _review_one(
    dim: Dimension,
    rfc_view: dict[str, Any],
    repo: str | Path,
    current_view: dict[str, Any] | None,
) -> Objection:
    """One reviewer critiques the RFC (or the latest aufheben consolidation) on ONE dimension.

    Fail closed: an unrunnable or malformed review is treated as an unresolved objection.
    """
    prompt = (
        f"You review an RFC on ONE concern only: {dim.key} - {dim.blurb}\n"
        "Inspect the target repository read-only only as needed for this dimension.\n"
        "Do not review any other dimension.\n\n"
        + _format_rfc("Original RFC", _rfc_to_view(rfc_view))
        + (_format_rfc("\nCurrent structured revised RFC to re-critique", current_view) if current_view else "")
        + "\nReturn only JSON matching the provided schema: "
        '{"has_objection": boolean, "detail": "brief dimension-specific explanation"}'
    )
    temp_dir = Path(tempfile.mkdtemp(prefix=f"ai-org-rfc-review-{dim.key}-"))
    schema_file = temp_dir / "rfc-objection.schema.json"
    out_file = temp_dir / f"{dim.key}-objection.json"
    try:
        schema_file.write_text(json.dumps(OBJECTION_SCHEMA, indent=2), encoding="utf-8")
        cmd = ["codex", "exec", "--sandbox", "read-only", "-C", str(repo), "-o", str(out_file)]
        if schema_file is not None:
            cmd += ["--output-schema", str(schema_file)]
        cmd.append(prompt)
        # codex can exit non-zero and write NO -o file (e.g. a rejected schema). A real run crashed here on
        # read_text of a missing file. Check returncode AND out_file existence BEFORE reading; fail closed otherwise.
        completed = subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            detail = completed.stderr.strip() or (
                "no output file" if not out_file.exists() else "Codex reviewer did not complete successfully."
            )
            return Objection(dim.key, True, f"Codex review failed for {dim.key}: {detail}")
        if not out_file.exists():
            return Objection(dim.key, True, f"Codex review failed for {dim.key}: no output file")
        result_text = out_file.read_text(encoding="utf-8")
        return _parse_objection(dim.key, result_text)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _parse_objection(dimension: str, raw: str) -> Objection:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return Objection(dimension, True, f"Codex review for {dimension} returned invalid JSON: {raw}")

    if not isinstance(parsed, dict):
        return Objection(dimension, True, f"Codex review for {dimension} returned non-object JSON: {raw}")

    has_objection = parsed.get("has_objection")
    detail = parsed.get("detail")
    if not isinstance(has_objection, bool) or not isinstance(detail, str):
        return Objection(
            dimension,
            True,
            f"Codex review for {dimension} returned JSON that did not match the objection schema: {raw}",
        )
    return Objection(dimension, has_objection, detail)


def _rfc_to_view(rfc_view: dict[str, Any]) -> dict[str, Any]:
    return {field: rfc_view[field] for field in RFC_VIEW_FIELDS}


def _format_rfc(label: str, view: dict[str, Any]) -> str:
    return (
        f"{label}:\n"
        f"title: {view['title']}\n"
        f"problem: {view['problem']}\n"
        f"proposal: {view['proposal']}\n"
        f"alternatives: {_format_alternatives(view['alternatives'])}\n"
        f"intended_users: {view['intended_users']}\n"
        f"affected_area: {view['affected_area']}\n"
        f"impact: {view['impact']}\n"
        f"context: {view['context']}\n"
    )


def _aufheben_consolidate(
    rfc_view: dict[str, Any],
    objections: list[Objection],
    repo: str | Path,
    current_view: dict[str, Any] | None,
) -> AufhebenDecision:
    """The Aufheben step merges objections into one revised RFC, or escalates.

    Runs once for each non-converged round.
    """
    joined = "\n".join(f"- [{o.dimension}] {o.detail}" for o in objections if o.has_objection)
    prompt = (
        "You are the Aufheben. Synthesize the reviewers' objections into ONE revised, coherent RFC direction "
        "that resolves them without losing intent. If the objections form a FUNDAMENTAL, unresolvable "
        "contradiction, escalate instead.\n\n"
        "Synthesis technique (止揚 / Aufhebung): do NOT collapse a tension onto one side. When two objections "
        "pull against each other, hold them as an ANTONYMOUS COMPOUND — a single paired concept that keeps "
        "both poles — and sublate (preserve both, raise to a higher resolution). The ti-yong tradition names "
        "exactly such tension-holding pairs: 體用 (essence-function) separates inner tests from outer proxies; "
        "文質 (form-substance) holds an audit-vs-theater tension. Use such a pair to resolve a "
        "criteria-vs-proxy / audit-vs-theater tension — only when it changes a reviewable criterion, not as "
        "ornament. (Pointers: Muller ti-yong; SEP 'Aufhebung'.)\n\n"
        + _format_rfc("RFC", _rfc_to_view(rfc_view))
        + (_format_rfc("\nCurrent view", current_view) if current_view else "")
        + f"Objections this round:\n{joined}\n"
        "\nReturn only JSON matching the provided schema. Use verdict=proceed with revised_rfc when the "
        "objections can be synthesized. Use verdict=escalate with escalation_reason when they cannot. "
        "Keep situation_read at or under 1000 characters."
    )
    temp_dir = Path(tempfile.mkdtemp(prefix="ai-org-rfc-aufheben-"))
    schema_file = temp_dir / "rfc-aufheben.schema.json"
    out_file = temp_dir / "aufheben-view.json"
    try:
        schema_file.write_text(json.dumps(AUFHEBEN_SCHEMA, indent=2), encoding="utf-8")
        cmd = ["codex", "exec", "--sandbox", "read-only", "-C", str(repo), "-o", str(out_file)]
        if schema_file is not None:
            cmd += ["--output-schema", str(schema_file)]
        cmd.append(prompt)
        # codex can exit non-zero and write NO -o file (e.g. a rejected schema). A real run crashed here on
        # read_text of a missing file. Check returncode AND out_file existence BEFORE reading; fail closed otherwise.
        completed = subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            detail = completed.stderr.strip() or (
                "no output file" if not out_file.exists() else "Codex Aufheben did not complete successfully."
            )
            return _aufheben_fail_closed(f"Aufheben failed: {detail}")
        if not out_file.exists():
            return _aufheben_fail_closed("Aufheben failed: no output file")
        result_text = out_file.read_text(encoding="utf-8")
        return _parse_aufheben_decision(result_text)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _parse_aufheben_decision(raw: str) -> AufhebenDecision:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return _aufheben_fail_closed(f"Aufheben returned invalid JSON: {raw}")

    if not isinstance(parsed, dict):
        return _aufheben_fail_closed(f"Aufheben returned non-object JSON: {raw}")

    verdict = parsed.get("verdict")
    revised_rfc = parsed.get("revised_rfc")
    situation_read = parsed.get("situation_read")
    escalation_reason = parsed.get("escalation_reason", "")

    if verdict not in {"proceed", "escalate"}:
        return _aufheben_fail_closed(f"Aufheben returned unsupported verdict: {raw}")
    if not isinstance(situation_read, str) or len(situation_read) > 1000:
        return _aufheben_fail_closed(f"Aufheben returned invalid situation_read: {raw}")
    if not _is_rfc_view(revised_rfc):
        return _aufheben_fail_closed(f"Aufheben returned invalid revised_rfc: {raw}")
    if verdict == "escalate" and not isinstance(escalation_reason, str):
        return _aufheben_fail_closed(f"Aufheben returned invalid escalation_reason: {raw}")

    return AufhebenDecision(
        verdict=verdict,
        revised_rfc=revised_rfc,
        situation_read=situation_read,
        escalation_reason=escalation_reason,
    )


def _is_rfc_view(value: object) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == set(RFC_VIEW_FIELDS)
        and all(isinstance(value[field], str) for field in RFC_VIEW_FIELDS if field != "alternatives")
        and isinstance(value["alternatives"], list)
        and all(isinstance(item, str) for item in value["alternatives"])
    )


def _aufheben_fail_closed(reason: str) -> AufhebenDecision:
    return AufhebenDecision(
        verdict="escalate",
        revised_rfc={field: ([] if field == "alternatives" else "") for field in RFC_VIEW_FIELDS},
        situation_read=reason[:1000],
        escalation_reason=reason,
    )


def _format_alternatives(value: Any) -> str:
    if isinstance(value, list):
        return "\n".join(f"- {item}" for item in value)
    return str(value)


def _rfc_branch(rfc_id_or_branch: str) -> str:
    if rfc_id_or_branch.startswith("refs/heads/"):
        return rfc_id_or_branch.removeprefix("refs/heads/")
    if rfc_id_or_branch.startswith("ai-org/rfc/"):
        return rfc_id_or_branch
    return f"ai-org/rfc/{rfc_id_or_branch}"


def _read_rfc_from_git(repo: str | Path, rfc_id_or_branch: str, rfc_path: str) -> tuple[dict[str, Any] | None, str]:
    branch = _rfc_branch(rfc_id_or_branch)
    completed = subprocess.run(
        ["git", "-C", str(repo), "show", f"{branch}:{rfc_path}"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or f"could not read {branch}:{rfc_path}"
        return None, detail
    try:
        parsed = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        return None, f"{branch}:{rfc_path} is invalid JSON: {exc}"
    if not _is_rfc_view(parsed):
        return None, f"{branch}:{rfc_path} must contain exactly the COMMON-8 fields"
    return _rfc_to_view(parsed), ""


def _write_direction_ok(
    repo: str | Path,
    rfc_id_or_branch: str,
    rfc_path: str,
    final_view: dict[str, Any],
    rounds: int,
) -> None:
    branch = _rfc_branch(rfc_id_or_branch)
    _checkout(repo, branch)
    target = Path(repo, rfc_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(final_view, indent=2) + "\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", rfc_path], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "--allow-empty", "-m", f"rfc: direction-ok ({rounds} rounds)"],
        check=True,
    )


def _write_nak(repo: str | Path, rfc_id_or_branch: str, rounds: int, unresolved_dimensions: list[str]) -> None:
    _checkout(repo, _rfc_branch(rfc_id_or_branch))
    dimensions = ", ".join(unresolved_dimensions) if unresolved_dimensions else "none"
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "commit",
            "--allow-empty",
            "-m",
            f"rfc: nak ({rounds} rounds)\n\nunresolved: {dimensions}",
        ],
        check=True,
    )


# PROVEN end-to-end against real codex (2026-06-29): git read (rfc.json@RFC branch) -> 5 reviewers + Aufheben (real
# substantive verdicts) -> git write (commit "rfc: direction-ok|nak" lands in the repo's git log).
def run_rfc_review(repo: str | Path, rfc_id_or_branch: str, rfc_path: str = "rfc.json") -> ReviewResult:
    """Loop up to CAP rounds: 5 reviewers -> (if objections) aufheben consolidates -> 5 re-critique.

    Converged within CAP (no unresolved objection) -> "direction-ok".
    Not converged within CAP -> "nak", returning which dimensions resolved and which objections
    remain unresolved.
    """
    # git is done HERE in python, NOT by codex: codex --sandbox workspace-write can edit the working tree but
    # CANNOT write .git (PROVEN: ".git/index.lock: Operation not permitted"). So python reads/writes git around codex.
    rfc_view, read_error = _read_rfc_from_git(repo, rfc_id_or_branch, rfc_path)
    if rfc_view is None:
        result = ReviewResult(
            "nak",
            0,
            "",
            resolved=[],
            unresolved=[Objection("rfc-read", True, read_error)],
            history=[],
            escalation_reason=read_error,
        )
        try:
            _write_nak(repo, rfc_id_or_branch, 0, ["rfc-read"])
        except subprocess.CalledProcessError as exc:
            result.escalation_reason = f"{read_error}; failed to commit NAK: {exc}"
        return result

    current_view: dict[str, Any] | None = None
    history: list = []
    for rounds in range(1, CAP + 1):
        objections = [_review_one(dim, rfc_view, repo, current_view) for dim in DIMENSIONS]
        round_history: dict[str, Any] = {"round": rounds, "objections": objections}
        history.append(round_history)
        unresolved = [o for o in objections if o.has_objection]
        if not unresolved:                                   # converged -> direction OK
            resolved = [o.dimension for o in objections]
            final_view = current_view or rfc_view
            _write_direction_ok(repo, rfc_id_or_branch, rfc_path, final_view, rounds)
            return ReviewResult("direction-ok", rounds, final_view,
                                resolved=resolved, unresolved=[], history=history)
        decision = _aufheben_consolidate(rfc_view, objections, repo, current_view)  # revise, then re-critique
        round_history["aufheben"] = {
            "verdict": decision.verdict,
            "situation_read": decision.situation_read,
            "escalation_reason": decision.escalation_reason,
        }
        if decision.verdict == "escalate":
            resolved = [o.dimension for o in objections if not o.has_objection]
            _write_nak(repo, rfc_id_or_branch, rounds, [o.dimension for o in unresolved])
            return ReviewResult(
                "nak",
                rounds,
                current_view or "",
                resolved=resolved,
                unresolved=unresolved,
                history=history,
                escalation_reason=decision.escalation_reason,
            )
        current_view = decision.revised_rfc
        if rounds == CAP:                                    # cap reached, still open -> NAK
            resolved = [o.dimension for o in objections if not o.has_objection]
            _write_nak(repo, rfc_id_or_branch, rounds, [o.dimension for o in unresolved])
            return ReviewResult("nak", rounds, current_view or "",
                                resolved=resolved, unresolved=unresolved, history=history)


def _checkout(repo: str | Path, branch: str) -> None:
    subprocess.run(["git", "-C", str(repo), "checkout", branch], check=True)


# Entry (manual for now):
#   from ai_org.rfc_review import run_rfc_review
#   result = run_rfc_review(repo=".")
