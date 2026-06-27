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
  - the AUFHEBEN (reused from ../archive/.../aufheben-designer) consolidates the five into one
    revised view, ONCE per round,
  - the five reviewers then re-critique that consolidation,
  - repeat until ALL FIVE have NO unresolved objection (CONVERGED), up to CAP rounds.

Outcomes:
  - DIRECTION-OK : converged within CAP (no unresolved objection) -> proceed to a real patch series.
  - NAK (reject) : did NOT converge within CAP -> rejected; the result returns which dimensions
                   resolved and which objections remain unresolved.
CAP is tentatively 5 — kept low on purpose to OBSERVE the loop's behavior and each LLM's behavior
before tuning or removing it. There is no separate "revise" outcome: revision IS the loop (the
aufheben revises, the five re-critique); only convergence (OK) and non-convergence (NAK) are terminal.

STUB: the loop/orchestration below is real; the reviewer and aufheben calls go through the
carrier seam in ``carrier.py`` (not wired yet), so running this raises until a carrier is connected.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .. import carrier
from .receive import RFC


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


@dataclass
class Objection:
    dimension: str
    has_objection: bool
    detail: str = ""


@dataclass
class ReviewResult:
    status: str                 # "direction-ok" (converged) | "nak" (not converged within CAP)
    rounds: int                 # rounds actually run
    final_view: str             # the latest consolidated RFC view (from the aufheben)
    resolved: list = field(default_factory=list)     # dimension keys with NO objection at the end
    unresolved: list = field(default_factory=list)   # Objection list still open at the end (NAK)
    history: list = field(default_factory=list)       # per-round objections, for the record


def _review_one(dim: Dimension, rfc: RFC, current_view: str | None) -> Objection:
    """One reviewer critiques the RFC (or the latest aufheben consolidation) on ONE dimension.

    STUB: builds the prompt and calls the carrier; raises until a carrier is wired. When wired,
    it returns an Objection (has_objection + detail) parsed from the reviewer's output.
    """
    prompt = (
        f"You review an RFC on ONE concern only: {dim.key} — {dim.blurb}\n"
        f"RFC title: {rfc.title}\nproblem: {rfc.problem}\nproposed_change: {rfc.proposed_change}\n"
        f"interface_sketch: {rfc.interface_sketch}\n"
        + (f"\nLatest consolidated view to re-critique:\n{current_view}\n" if current_view else "")
        + "\nState any objection on YOUR concern only, or that you have none."
    )
    resp = carrier.invoke(carrier.CarrierRequest(role=f"rfc-review:{dim.key}", prompt=prompt))
    # TODO(parse): map resp.text -> Objection(has_objection, detail)
    raise NotImplementedError("rfc reviewer parse not wired (stub)")  # pragma: no cover


def _aufheben_consolidate(rfc: RFC, objections: list[Objection], current_view: str | None) -> str:
    """The archived aufheben merges the five reviewers' objections into ONE revised view.

    Reused (conceptually/by prompt) from ../archive/.../roles/aufheben-designer. Runs ONCE per
    round. STUB: builds the prompt and calls the carrier; raises until wired.
    """
    joined = "\n".join(f"- [{o.dimension}] {o.detail}" for o in objections if o.has_objection)
    prompt = (
        "You are the aufheben. Consolidate the reviewers' objections into ONE revised, coherent "
        "view of the RFC's direction that addresses them without losing intent.\n"
        f"RFC: {rfc.title}\n"
        + (f"Current view:\n{current_view}\n" if current_view else "")
        + f"Objections this round:\n{joined}\n"
    )
    resp = carrier.invoke(carrier.CarrierRequest(role="aufheben", prompt=prompt))
    return resp.text


def run_rfc_review(rfc: RFC) -> ReviewResult:
    """Loop up to CAP rounds: 5 reviewers -> (if objections) aufheben consolidates -> 5 re-critique.

    Converged within CAP (no unresolved objection) -> "direction-ok".
    Not converged within CAP -> "nak", returning which dimensions resolved and which objections
    remain unresolved. Orchestration is real; the role calls are stubs.
    """
    current_view: str | None = None
    history: list = []
    for rounds in range(1, CAP + 1):
        objections = [_review_one(dim, rfc, current_view) for dim in DIMENSIONS]
        history.append(objections)
        unresolved = [o for o in objections if o.has_objection]
        if not unresolved:                                   # converged -> direction OK
            resolved = [o.dimension for o in objections]
            return ReviewResult("direction-ok", rounds, current_view or "",
                                resolved=resolved, unresolved=[], history=history)
        if rounds == CAP:                                    # cap reached, still open -> NAK
            resolved = [o.dimension for o in objections if not o.has_objection]
            return ReviewResult("nak", rounds, current_view or "",
                                resolved=resolved, unresolved=unresolved, history=history)
        current_view = _aufheben_consolidate(rfc, objections, current_view)  # revise, then re-critique


# Entry (manual for now):
#   from ai_org.rfc import RFC
#   from ai_org.rfc_review import run_rfc_review
#   result = run_rfc_review(RFC(title=..., problem=..., proposed_change=..., interface_sketch=...))
