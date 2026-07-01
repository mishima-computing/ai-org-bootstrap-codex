# receive.py — the INTAKE GATE: judges whether an incoming REQUEST may become an RFC.
# This is NOT a dumb loader/translator. A request is discussed and can be SENT BACK (send-back):
#     request --[receive gate]--> promote to RFC | send back for revision | reject.
# Only requests that PASS this gate become grounded RFCs. The RFC phase has two parts:
#     1) receive : intake — can this REQUEST become an RFC at all? (this file)
#     2) review  : debate the direction of an already-formed RFC. (review.py)
# The RFC phase starts by taking a raw request and forming a grounded RFC view. That promotion is
# real work, not a load. It mirrors the Linux early-stage process (kernel.org process/3.Early-stage):
#     1) Specify the problem   — what must be solved, who is affected, where the system falls short
#     2) Early discussion      — surface objections / alternatives BEFORE implementation
#     3) Who do you talk to    — route to the right reviewers/maintainers (the right subsystem)
#     4) When to post          — the problem + intended approach are stated well enough to act on
#     5) Get buy-in            — go / no-go approval to proceed
# Receive owns the grounding and send-back gate. Review owns only the later direction debate.
#
# DECISION: these 5 processes happen INSIDE the RFC formation — one codex-driven stage, like review's internal
# 5-reviewer + Aufheben loop — NOT as 5 separate git stages/branches/commits. Git stores ONLY the result:
# the promoted, contributor-takeable RFC (ai-org/rfc/<id>: rfc.json) or a send-back/reject marker. Doing
# the 5 processes inside the RFC (not in git) keeps the git state from exploding.
#
# Input/output field contract:
#   entrance REQUEST (rough) carries the common-8 through-line fields:
#       title, problem/motivation, proposal, alternatives, intended_users, affected_area, impact, context/links
#   grounded RFC view = the common-8 after research, correction, and repository-context enrichment.
#
# Shape (to match the other stages): validate the request -> codex grounds it -> git-write the
# promoted RFC (ai-org/rfc/<id>: rfc.json), or send the request back with a proposed interpretation.
#
# REFERENCE UPDATE (TODO wiring — memo only for now): when an RFC is received/promoted here, that event
# FIRES a Reference update. The RFC's meaningful implementation terms feed ai_org.reference.build_from_rfc,
# which looks up each term in the org-level knowledge store and, on a miss, expands it (language-agnostic
# repo search -> extract -> strict delta-over-baseline -> distill), appending (never deleting) what it finds.
# This is the Reference super-module's "step 1 = build at RFC-receive time"; leaves later READ that Reference,
# and top up on a miss. The store is off-git and grows with use. The actual call is not wired in yet.
#
# TECHNICAL APPROACH — where the design is FORMED (TODO build; grounded in the Linux/RFC review research):
# "propose the approach" and "review the approach" are DISTINCT. Review CRITIQUES a submitted design; it must
# NOT be the first place the approach is created (Rust RFCs / PEPs / IETF I-Ds all post a design that review
# reshapes). So the RFC must arrive at review already carrying a Technical Approach. That approach is formed
# HERE, at receive, and it is the BOUNDARY between the requester and the AI Org:
#     Technical Approach present in the request?
#       YES -> use it as the basis (ground/refine with the Reference + repo context; do NOT discard the
#              requester's approach). Partial -> fill only the gaps.
#       NO  -> the AI Org GENERATES it (Reference-driven: use the Reference's implementation knowledge to
#              propose HOW to build it, labelled as PROPOSAL, not fact).
# GOAL: usable by a layperson (an amateur will not supply a Technical Approach -> the AI Org generates it).
# But even for an amateur, receive does NOT silently decide everything: when it generates the approach it
# also ASKS QUESTIONS BACK — surface the pivotal decisions/assumptions and return them for confirm/correct
# (this is the existing needs_confirmation "propose a guess, ask 'is this right?'" path, EXTENDED to the
# technical-approach decisions). The amateur steers by ANSWERING (intent/preferences), not by AUTHORING.
# The formed Technical Approach carries: problem/impact, reference-derived prior-art, implementation strategy,
# alternatives-with-why-not, compatibility/migration, testing plan, scope/patch plan, open questions.
#
# TECHNICAL APPROACH — formation procedure (grounded in RFC/PEP + ADR + ATAM + senior-dev practice; TODO, build
# ONE STEP AT A TIME). Codex STRUCTURES the reasoning and exposes evidence/trade-offs; it must NOT emit a one-shot
# design claim. Weighting stays judgment-heavy (final priorities / risk tolerance / architectural taste are human):
#   1. Normalize the problem: problem, affected users/systems, current inadequacy, success criteria, non-goals.
#   2. Extract constraints: hard constraints + soft preferences (repo architecture, compatibility, data/API
#      contracts, performance/security/reliability, test constraints, delivery scope).
#   3. Build a prior-art map from the REFERENCE (design + implementation facets) + repo context: 3-6 patterns,
#      each {pattern, where-seen, when-applies, tradeoffs, adopt|adapt|reject}. This is where e.g. an engine like
#      Godot lands as a CANDIDATE — put on the table and judged on merit/fit, NOT on how often it appeared.
#   4. Generate 2-3 candidate approaches: always a minimal/local one, a repo-native/reference-aligned one, and a
#      more-general/architectural one when plausible; optionally do-nothing/defer when requirements are weak.
#   5. Evaluate candidates on a compact matrix: problem fit, repo fit, complexity, quality attributes,
#      compat/migration, testability, operability, reversibility, risk, evidence.
#   6. Select with rationale: "Choose X because ... under constraints ..., accepting tradeoff F. Reject Y/Z because."
#   7. Implementation strategy: main code changes, affected modules, data/API/config changes, migration/compat,
#      testing plan, observability/operability where relevant.
#   8. Right-size the patch plan: first safe slice, follow-up slices, explicitly deferred work + why safe to defer
#      (YAGNI, unless the deferred decision is hard to reverse or affects major quality attributes).
#   9. Surface risks & open questions: assumptions, risks, unresolved questions, spikes/prototypes, reviewer Qs.
#  10. Emit the Technical Approach section: chosen approach, alternatives-with-why-not, prior-art rationale,
#      trade-off analysis, implementation plan, compat/migration, testing plan, scoped patch plan, risks/open Qs.
# Question-back to the requester (needs_confirmation extended to approach decisions) is deferred — build it LAST.
"""RFC receive — validate and ground an entrance request into an RFC."""
from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Any, Mapping

import ai_org.reference as reference
import ai_org.rfc.codex_exec as codex_exec


COMMON_8_FIELDS = (
    "title",
    "problem",
    "proposal",
    "alternatives",
    "intended_users",
    "affected_area",
    "impact",
    "context",
)

REQUIRED_FIELDS = ("title", "problem")
OPTIONAL_FIELDS = ("proposal", "alternatives", "intended_users", "affected_area", "impact", "context")
OPTIONAL_STRING_FIELDS = ("proposal", "intended_users", "affected_area", "impact", "context")
OPTIONAL_LIST_FIELDS = ("alternatives",)

REQUEST_SCHEMA: dict[str, Any] = {
    "recognized_fields": list(COMMON_8_FIELDS),
    "required": list(REQUIRED_FIELDS),
    "optional": list(OPTIONAL_FIELDS),
    "additional_properties": True,
}

RFC_VIEW_FIELDS = COMMON_8_FIELDS

GROUNDING_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": ["confident", "proposed_rfc", "assumptions", "questions", "grounding_notes"],
    "properties": {
        "confident": {"type": "boolean"},
        "proposed_rfc": {
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
        "assumptions": {"type": "array", "items": {"type": "string"}},
        "questions": {"type": "array", "items": {"type": "string"}},
        "grounding_notes": {"type": "string", "maxLength": 2000},
    },
}

GROUNDING_VERDICT_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": ["faithful_specific", "full_scope", "non_legal", "latest_default", "reasons"],
    "properties": {
        "faithful_specific": {"type": "boolean"},
        "full_scope": {"type": "boolean"},
        "non_legal": {"type": "boolean"},
        "latest_default": {"type": "boolean"},
        "reasons": {"type": "array", "items": {"type": "string"}},
    },
}

NORMALIZED_PROBLEM_FIELDS = (
    "problem",
    "affected",
    "current_inadequacy",
    "success_criteria",
    "non_goals",
)

NORMALIZE_PROBLEM_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": list(NORMALIZED_PROBLEM_FIELDS),
    "properties": {
        "problem": {"type": "string"},
        "affected": {"type": "string"},
        "current_inadequacy": {"type": "string"},
        "success_criteria": {"type": "array", "items": {"type": "string"}},
        "non_goals": {"type": "array", "items": {"type": "string"}},
    },
}

CONSTRAINT_SOURCE_VALUES = ("repo", "rfc", "domain")
CONSTRAINT_ITEM_FIELDS = ("constraint", "source", "why")
PREFERENCE_ITEM_FIELDS = ("preference", "source", "why")
EXTRACT_CONSTRAINTS_FIELDS = ("hard_constraints", "soft_preferences")

EXTRACT_CONSTRAINTS_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": list(EXTRACT_CONSTRAINTS_FIELDS),
    "properties": {
        "hard_constraints": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": list(CONSTRAINT_ITEM_FIELDS),
                "properties": {
                    "constraint": {"type": "string"},
                    "source": {"type": "string", "enum": list(CONSTRAINT_SOURCE_VALUES)},
                    "why": {"type": "string"},
                },
            },
        },
        "soft_preferences": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": list(PREFERENCE_ITEM_FIELDS),
                "properties": {
                    "preference": {"type": "string"},
                    "source": {"type": "string", "enum": list(CONSTRAINT_SOURCE_VALUES)},
                    "why": {"type": "string"},
                },
            },
        },
    },
}

PRIOR_ART_PATTERN_FIELDS = (
    "pattern",
    "where_seen",
    "when_applies",
    "tradeoffs",
    "disposition",
    "rationale",
)
PRIOR_ART_DISPOSITIONS = ("adopt", "adapt", "reject")
PRIOR_ART_MAP_FIELDS = ("patterns",)

PRIOR_ART_MAP_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": list(PRIOR_ART_MAP_FIELDS),
    "properties": {
        "patterns": {
            "type": "array",
            "minItems": 3,
            "maxItems": 6,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": list(PRIOR_ART_PATTERN_FIELDS),
                "properties": {
                    "pattern": {"type": "string"},
                    "where_seen": {"type": "string"},
                    "when_applies": {"type": "string"},
                    "tradeoffs": {"type": "string"},
                    "disposition": {"type": "string", "enum": list(PRIOR_ART_DISPOSITIONS)},
                    "rationale": {"type": "string"},
                },
            },
        },
    },
}
_PRIOR_ART_STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "that",
    "this",
    "from",
    "into",
    "before",
    "after",
    "only",
    "must",
    "should",
    "would",
    "could",
    "needs",
    "need",
    "use",
    "uses",
    "using",
    "rfc",
    "view",
    "step",
    "common",
    "problem",
    "proposal",
    "context",
    "impact",
    "users",
    "area",
    "approach",
    "technical",
    "formation",
}

MAX_REGROUNDS = 2

GENERALIZER_MARKERS = (
    "-style",
    " style",
    "-like",
    "inspired by",
    "generic",
    "broad category",
    "genre entry",
    "safer-sounding substitute",
    "\u98a8",
    "\u6c4e\u7528",
)

SCOPE_HEDGE_MARKERS = (
    "vertical slice",
    "mvp",
    "prototype",
    "short demo",
    "one town",
    "10-minute",
    "minimal",
    "first iteration",
    "small deliverable",
    "\u30b9\u30e9\u30a4\u30b9",
    "\u6700\u5c0f",
    "\u77ed\u7de8",
    "\u30d7\u30ed\u30c8\u30bf\u30a4\u30d7",
)

LEGAL_KEYWORDS = (
    "trademark",
    "copyright",
    " ip ",
    "intellectual property",
    "legal",
    "licensing",
    "license",
    "material usage",
    "rights holder",
    "\u5546\u6a19",
    "\u8457\u4f5c\u6a29",
    "\u6cd5\u52d9",
    "\u30e9\u30a4\u30bb\u30f3\u30b9",
)

RETRO_MARKERS = (
    "1986",
    "famicom",
    "windows 95",
    "windows-95",
    "retro",
    "classic",
    "old-school",
    "old school",
    "vintage",
    "\u30d5\u30a1\u30df\u30b3\u30f3",
    "\u30ec\u30c8\u30ed",
)

RETRO_REQUEST_MARKERS = RETRO_MARKERS + (
    "older version",
    "old version",
    "past version",
    "specific past version",
    "specific year",
)


@dataclass
class GroundingResult:
    rfc_view: dict[str, Any]
    grounding_notes: str = ""
    confident: bool = True
    assumptions: list[str] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)


def receive(source: str | Path | Mapping[str, Any]) -> dict[str, Any]:
    """Load and validate a raw request from a dict or a JSON file path."""
    if isinstance(source, Mapping):
        data = dict(source)
    elif isinstance(source, (str, Path)):
        path = Path(source)
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise ValueError(f"Could not read request JSON file {path}: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise ValueError(f"Request JSON file {path} is invalid JSON: {exc}") from exc
        if not isinstance(loaded, dict):
            raise ValueError(f"Request JSON file {path} must contain a JSON object.")
        data = loaded
    else:
        raise TypeError("receive(source) expects a dict or a path to a JSON file.")

    for field in REQUIRED_FIELDS:
        _required_string_field(data, field)

    for field in OPTIONAL_STRING_FIELDS:
        _optional_string_field(data, field)
    for field in OPTIONAL_LIST_FIELDS:
        _optional_list_field(data, field)

    return data


def intake(source: str | Path | Mapping[str, Any], repo: str | Path, rfc_path: str = "rfc.json") -> dict[str, Any]:
    """Validate and ground a raw request, then promote it only when grounding is confident."""
    try:
        request = receive(source)
        return produce_rfc(request, repo, rfc_path)
    except ValueError as exc:
        return {"status": "rejected", "error": str(exc)}


def produce_rfc(validated_request: Mapping[str, Any], repo: str | Path, rfc_path: str = "rfc.json") -> dict[str, Any]:
    """Ground and write a validated COMMON-8 request to git as ai-org/rfc/<id>:rfc.json."""
    raw_rfc = _common_8(validated_request)
    repo_path = Path(repo).resolve()
    grounding = _ground_with_contract(repo_path, raw_rfc)
    if not grounding.confident:
        result = {
            "status": "needs_confirmation",
            "proposed_rfc": grounding.rfc_view,
            "assumptions": grounding.assumptions,
            "questions": grounding.questions,
            "grounding_notes": grounding.grounding_notes,
        }
        if grounding.violations:
            result["violations"] = grounding.violations
        return result

    rfc = grounding.rfc_view
    rfc_id = _slug(rfc["title"])
    branch = f"ai-org/rfc/{rfc_id}"
    base = _default_branch(repo_path)
    written = _write_rfc_branch(
        repo_path,
        branch,
        base,
        rfc,
        rfc_path=rfc_path,
        commit_message=f"rfc: receive {rfc['title']}",
    )
    return {
        "ok": True,
        "status": "promoted",
        "id": rfc_id,
        "branch": branch,
        "commit": written["commit"],
        "grounding_notes": grounding.grounding_notes,
    }


def _normalize_problem(rfc_view: dict[str, Any], context: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Form step 1 of the documented 10-step Technical Approach procedure."""
    if not _is_rfc_view(rfc_view):
        return _normalized_problem_error("normalize_problem requires a grounded common-8 RFC view")

    repo = _repo_from_context(context)
    run = codex_exec.run_json(
        repo,
        schema=NORMALIZE_PROBLEM_SCHEMA,
        prompt=_normalize_problem_prompt(rfc_view, context),
        schema_filename="rfc-normalize-problem.schema.json",
        output_filename="rfc-normalized-problem.json",
        failure_label="Codex problem normalization",
    )
    if not run["ok"]:
        return _normalized_problem_error(run["error"])
    return _parse_normalized_problem(run["raw"])


def _extract_constraints(
    rfc_view: dict[str, Any],
    repo: str | Path,
    context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Form step 2 of the documented 10-step Technical Approach procedure."""
    # Step 2 of 10: extract hard constraints and soft preferences before approach generation.
    if not _is_rfc_view(rfc_view):
        return _constraints_error("extract_constraints requires a grounded common-8 RFC view")

    repo_path = Path(repo).resolve()
    run = codex_exec.run_json(
        repo_path,
        schema=EXTRACT_CONSTRAINTS_SCHEMA,
        prompt=_extract_constraints_prompt(rfc_view, repo_path, context),
        schema_filename="rfc-extract-constraints.schema.json",
        output_filename="rfc-extracted-constraints.json",
        failure_label="Codex constraint extraction",
    )
    if not run["ok"]:
        return _constraints_error(run["error"])
    return _parse_constraints(run["raw"])


def _build_prior_art_map(
    rfc_view: dict[str, Any],
    repo: str | Path,
    context: Mapping[str, Any] | None = None,
    normalized_problem: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Form step 3 of the documented 10-step Technical Approach procedure."""
    # Step 3 of 10: read Reference facets and repo context to map prior art before candidate generation.
    if not _is_rfc_view(rfc_view):
        return _prior_art_error("build_prior_art_map requires a grounded common-8 RFC view")

    repo_path = Path(repo).resolve()
    concepts = _prior_art_key_concepts(rfc_view, context, normalized_problem)
    try:
        reference_facets = _read_prior_art_reference_facets(concepts, context)
    except Exception as exc:
        return _prior_art_error(f"Reference prior-art read failed: {exc}")
    run = codex_exec.run_json(
        repo_path,
        schema=PRIOR_ART_MAP_SCHEMA,
        prompt=_prior_art_map_prompt(rfc_view, repo_path, concepts, reference_facets, context, normalized_problem),
        schema_filename="rfc-prior-art-map.schema.json",
        output_filename="rfc-prior-art-map.json",
        failure_label="Codex prior-art mapping",
    )
    if not run["ok"]:
        return _prior_art_error(run["error"])
    return _parse_prior_art_map(run["raw"])


def _ground_with_contract(repo: str | Path, rfc_view: dict[str, Any]) -> GroundingResult:
    best_grounding: GroundingResult | None = None
    previous_violations: list[str] = []
    for attempt in range(MAX_REGROUNDS + 1):
        grounding = _ground_request(repo, rfc_view, previous_violations if attempt else None)
        best_grounding = grounding
        verification = _verify_grounding(rfc_view, grounding.rfc_view, grounding)
        violations = list(verification["violations"])
        if not violations:
            return grounding
        previous_violations = violations

    assert best_grounding is not None
    return GroundingResult(
        best_grounding.rfc_view,
        _with_unresolved_violations(best_grounding.grounding_notes, previous_violations),
        False,
        best_grounding.assumptions
        + ["Grounding contract violations remain unresolved after verification."],
        best_grounding.questions + ["Can you confirm or correct the proposed RFC interpretation?"],
        previous_violations,
    )


def _ground_request(
    repo: str | Path,
    rfc_view: dict[str, Any],
    previous_violations: list[str] | None = None,
) -> GroundingResult:
    """Research and correct a rough request before it becomes an RFC branch."""
    prompt = _grounding_prompt(rfc_view, previous_violations)
    temp_dir = Path(tempfile.mkdtemp(prefix="ai-org-rfc-grounding-"))
    schema_file = temp_dir / "rfc-grounding.schema.json"
    out_file = temp_dir / "grounded-rfc.json"
    try:
        schema_file.write_text(json.dumps(GROUNDING_SCHEMA, indent=2), encoding="utf-8")
        cmd = [
            "codex",
            "exec",
            "--sandbox",
            "read-only",
            "-C",
            str(repo),
            "-o",
            str(out_file),
            "--enable",
            "web_search",
            "--output-schema",
            str(schema_file),
            prompt,
        ]
        try:
            completed = subprocess.run(
                cmd,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
            )
        except OSError as exc:
            return _grounding_fail_closed(rfc_view, f"Grounding failed: {exc}")

        if completed.returncode != 0:
            detail = completed.stderr.strip() or (
                "no output file" if not out_file.exists() else "Codex grounding did not complete successfully."
            )
            return _grounding_fail_closed(rfc_view, f"Grounding failed: {detail}")
        if not out_file.exists():
            return _grounding_fail_closed(rfc_view, "Grounding failed: no output file")
        return _parse_grounding_result(out_file.read_text(encoding="utf-8"), rfc_view)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _grounding_prompt(rfc_view: dict[str, Any], previous_violations: list[str] | None = None) -> str:
    # Grounding faithfully renders the request: the specific named thing at full scope.
    # It never generalizes to the category, never shrinks scope, and is not the legal
    # department: no IP, trademark, or copyright analysis.
    reground_instruction = ""
    if previous_violations:
        reground_instruction = (
            "Your previous grounding violated the executable grounding contract. Fix these violations and "
            "re-render the RFC without repeating them:\n"
            + "\n".join(f"- {violation}" for violation in previous_violations)
            + "\n\n"
        )

    return (
        "You are the RFC intake grounding step for AI Org.\n"
        "Your job is to turn a rough, vague, or even wrong request into the right well-grounded RFC view "
        "before an RFC branch is created.\n\n"
        + reground_instruction
        + "Use web search when the request names or implies a real product, game, genre, paper, standard, "
        "company, library, tool, or other external reference. Determine what the reference actually is, "
        "including its concrete defining signatures, core mechanics, interaction rhythms, structure, tone, "
        "progression cadence, characteristic look, conventions, and prior art. Correct misconceptions in the "
        "request. Also inspect the target repository read-only to identify existing code, patterns, "
        "constraints, and affected areas. Do not modify files.\n\n"
        "Faithfully render the request's specific identity. When the request names a specific thing, ground "
        "down to that named thing and preserve its full identity in proposed_rfc, including the title; never "
        "generalize up to a broad category, genre, archetype, or safer-sounding substitute. proposed_rfc must "
        "read as 'faithfully reproduce <the specific named thing>', and a reviewer should be able to tell it "
        "apart from a generic genre entry. Do not turn 'make an elephant' into 'make a mammal'. Do not rename "
        "a named thing as 'an original X-style' work.\n\n"
        "Preserve the request's full scope. Do not reduce the request to a vertical slice, short demo, one "
        "area, 10-minute experience, prototype, MVP, first iteration, or other smaller deliverable. Commit "
        "the proposed_rfc to the complete requested deliverable at the fidelity implied by the named thing. "
        "Downstream phases may decompose and iterate toward that full scope; intake must not hand down a "
        "smaller goal. Do not put start-small, minimal, slice, demo, or MVP hedging in proposed_rfc or "
        "assumptions.\n\n"
        "Grounding is not legal review. Do not perform IP, trademark, copyright, or licensing risk analysis; "
        "do not add legal disclaimers; do not spend proposed_rfc, assumptions, or grounding_notes on legal "
        "concerns. Do not avoid perceived IP risk by renaming, generalizing, or shrinking the named thing. "
        "Your job here is only to understand and faithfully render what to build.\n\n"
        "Default to the latest or current version, conventions, and best practices of the named thing unless "
        "the request explicitly asks for a retro, classic, old, vintage, specific past version, or specific "
        "past year target. This applies across domains: games should target the current experience, modern "
        "graphics, scope, and conventions; security should target current standards and patches; SaaS should "
        "use current stacks and practices; libraries should use current APIs. Do not gratuitously target an "
        "outdated incarnation when the latest sensible target is available.\n\n"
        "Always do the research and commit to the most-likely interpretation as proposed_rfc, even when the "
        "request is ambiguous or under-specified. Do not send back blank open questions instead of grounding. "
        "If you are confident, set confident=true and make proposed_rfc the grounded common-8 RFC view that "
        "should be promoted. If you are not fully confident, set confident=false and still return the best-guess "
        "grounded common-8 proposed_rfc for requester confirmation: this is what I think you mean, right? "
        "List the specific inferences you made in assumptions, each phrased so the requester can confirm or "
        "correct it, such as 'I assumed X because <research>'. Reserve questions only for gaps that research "
        "genuinely cannot resolve after you have inferred the most likely interpretation; questions is usually "
        "empty. grounding_notes must briefly state what you researched, what you corrected, and cite web "
        "references when used.\n\n"
        + _format_rfc("Current request common-8", _rfc_to_view(rfc_view))
        + "\nReturn only JSON matching the provided schema."
    )


def _normalize_problem_prompt(rfc_view: dict[str, Any], context: Mapping[str, Any] | None = None) -> str:
    context_text = json.dumps(context or {}, indent=2, sort_keys=True, default=str)
    return (
        "You are forming step 1 of AI Org's 10-step Technical Approach procedure: normalize the problem.\n"
        "Use the grounded common-8 RFC view and repository context only to restate the problem clearly. "
        "Do not propose an implementation approach, alternatives, patch plan, reviewer decision, or later "
        "Technical Approach steps.\n\n"
        "Return these fields:\n"
        "- problem: the core problem, restated crisply.\n"
        "- affected: affected users, operators, contributors, systems, modules, or workflows.\n"
        "- current_inadequacy: what is missing or where the current state falls short.\n"
        "- success_criteria: checkable statements for what solved looks like.\n"
        "- non_goals: explicit boundaries that should remain out of scope for this RFC.\n\n"
        + _format_rfc("Grounded RFC common-8", _rfc_to_view(rfc_view))
        + f"\nContext:\n{context_text}\n"
        + "\nReturn only JSON matching the provided schema."
    )


def _extract_constraints_prompt(
    rfc_view: dict[str, Any],
    repo: Path,
    context: Mapping[str, Any] | None = None,
) -> str:
    context_text = json.dumps(context or {}, indent=2, sort_keys=True, default=str)
    return (
        "You are forming step 2 of AI Org's 10-step Technical Approach procedure: extract constraints.\n"
        "Inspect the repository read-only as needed from the configured repo root. Use the grounded common-8 "
        "RFC view, repository architecture, and supplied context to identify constraints only. Do not propose "
        "candidate approaches, select an approach, create a patch plan, or perform later Technical Approach "
        "steps.\n\n"
        "Extract two lists:\n"
        "- hard_constraints: must-satisfy constraints that later approaches cannot violate.\n"
        "- soft_preferences: nice-to-have preferences that should influence trade-offs but may be outweighed.\n\n"
        "Cover these areas when evidence exists:\n"
        "- repository architecture and module boundaries.\n"
        "- backward compatibility and public/interface compatibility.\n"
        "- data, API, schema, protocol, and configuration contracts.\n"
        "- performance, security, reliability, operability, and migration requirements.\n"
        "- test constraints, existing coverage style, and verification expectations.\n"
        "- delivery scope, non-goals, rollout boundaries, and documentation expectations.\n\n"
        "For each item, set source to exactly one of: repo, rfc, domain. Use repo for constraints observed in "
        "code, tests, docs, or project structure; rfc for constraints stated or implied by the grounded RFC; "
        "domain for generally applicable engineering, security, reliability, or compatibility constraints. "
        "The why field must briefly explain the evidence or reason. Return an empty list when no defensible "
        "items exist for a category; do not invent unsupported constraints.\n\n"
        + _format_rfc("Grounded RFC common-8", _rfc_to_view(rfc_view))
        + f"\nRepository root:\n{repo}\n"
        + f"\nContext:\n{context_text}\n"
        + "\nReturn only JSON matching the provided schema."
    )


def _prior_art_map_prompt(
    rfc_view: dict[str, Any],
    repo: Path,
    concepts: list[str],
    reference_facets: list[dict[str, Any]],
    context: Mapping[str, Any] | None = None,
    normalized_problem: Mapping[str, Any] | None = None,
) -> str:
    context_text = json.dumps(context or {}, indent=2, sort_keys=True, default=str)
    normalized_problem_text = json.dumps(normalized_problem or {}, indent=2, sort_keys=True, default=str)
    concepts_text = json.dumps(concepts, indent=2, ensure_ascii=True)
    reference_text = json.dumps(reference_facets, indent=2, sort_keys=True, ensure_ascii=True, default=str)
    return (
        "You are forming step 3 of AI Org's 10-step Technical Approach procedure: build a prior-art map.\n"
        "Inspect the repository read-only as needed from the configured repo root. Use the grounded common-8 "
        "RFC view, normalized problem if provided, Reference design facets, Reference implementation facets, "
        "and repository context to synthesize 3 to 6 prior-art patterns. Do not generate candidate approaches, "
        "select an approach, create a patch plan, or perform later Technical Approach steps.\n\n"
        "Each pattern must identify a real design or implementation pattern visible in the Reference facets, "
        "the repository, or both. Treat frameworks, engines, and libraries as candidates judged on fit; do not "
        "favor them merely because they appeared often. A candidate such as Godot belongs here only when the "
        "evidence makes it relevant, and its disposition must be adopt, adapt, or reject on merit.\n\n"
        "For each pattern:\n"
        "- pattern: concise name of the prior-art pattern or candidate.\n"
        "- where_seen: Reference source, repo location, or both; say when evidence is absent or weak.\n"
        "- when_applies: conditions that make the pattern appropriate.\n"
        "- tradeoffs: concrete benefits, costs, and failure modes.\n"
        "- disposition: exactly adopt, adapt, or reject for this RFC.\n"
        "- rationale: why that disposition follows from the RFC, Reference, and repo context.\n\n"
        + _format_rfc("Grounded RFC common-8", _rfc_to_view(rfc_view))
        + f"\nRepository root:\n{repo}\n"
        + f"\nNormalized problem:\n{normalized_problem_text}\n"
        + f"\nContext:\n{context_text}\n"
        + f"\nReference key concepts queried:\n{concepts_text}\n"
        + f"\nReference facets read before this call:\n{reference_text}\n"
        + "\nReturn only JSON matching the provided schema."
    )


def _prior_art_key_concepts(
    rfc_view: dict[str, Any],
    context: Mapping[str, Any] | None = None,
    normalized_problem: Mapping[str, Any] | None = None,
) -> list[str]:
    concepts: list[str] = []
    context = context or {}
    for field in ("reference_terms", "key_concepts", "concepts", "terms"):
        _extend_concepts(concepts, context.get(field))

    _add_concept(concepts, rfc_view.get("title", ""))
    _add_concept(concepts, rfc_view.get("affected_area", ""))
    _extend_concepts(concepts, _explicit_terms(_rfc_text(rfc_view)))
    if normalized_problem:
        _extend_concepts(concepts, _explicit_terms(json.dumps(normalized_problem, sort_keys=True, default=str)))

    if len(concepts) < 6:
        _extend_concepts(concepts, _important_phrases(_rfc_text(rfc_view)))
    return concepts[:12]


def _extend_concepts(concepts: list[str], values: Any) -> None:
    if isinstance(values, str):
        _add_concept(concepts, values)
    elif isinstance(values, list | tuple | set):
        for value in values:
            _add_concept(concepts, value)


def _add_concept(concepts: list[str], value: Any) -> None:
    concept = re.sub(r"\s+", " ", str(value or "").strip())
    if not concept or len(concept) > 120:
        return
    lowered = concept.lower()
    if lowered not in {existing.lower() for existing in concepts}:
        concepts.append(concept)


def _explicit_terms(text: str) -> list[str]:
    terms = re.findall(r"`([^`]{2,80})`", text)
    terms.extend(re.findall(r'"([^"]{2,80})"', text))
    terms.extend(re.findall(r"'([^']{2,80})'", text))
    return terms


def _important_phrases(text: str) -> list[str]:
    words = [
        word.lower()
        for word in re.findall(r"[A-Za-z][A-Za-z0-9_.-]*", text)
        if len(word) > 2 and word.lower() not in _PRIOR_ART_STOPWORDS
    ]
    phrases: list[str] = []
    for size in (3, 2):
        for index in range(0, max(0, len(words) - size + 1)):
            phrase = " ".join(words[index : index + size])
            if not any(part in _PRIOR_ART_STOPWORDS for part in phrase.split()):
                phrases.append(phrase)
    phrases.extend(words)
    return phrases


def _read_prior_art_reference_facets(
    concepts: list[str],
    context: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    reference_context = dict(context or {})
    facets: list[dict[str, Any]] = []
    for term in concepts:
        term_facets: dict[str, Any] = {"term": term, "design": [], "implementation": []}
        for kind in ("design", "implementation"):
            lookup = reference.lookup(term, reference_context, kind=kind)
            candidates = lookup.get("candidates", []) if isinstance(lookup, dict) else []
            if not candidates:
                candidates = reference.query({"term": term, "kind": kind})
            term_facets[kind] = _trim_reference_candidates(candidates)
        if term_facets["design"] or term_facets["implementation"]:
            facets.append(term_facets)
    return facets


def _trim_reference_candidates(candidates: Any) -> list[dict[str, str]]:
    if not isinstance(candidates, list):
        return []
    trimmed: list[dict[str, str]] = []
    for candidate in candidates[:4]:
        if isinstance(candidate, Mapping):
            trimmed.append(
                {
                    str(key): str(value)
                    for key, value in candidate.items()
                    if key
                    in {
                        "kind",
                        "term",
                        "summary",
                        "snippet",
                        "pitfalls",
                        "structure",
                        "rationale",
                        "when_to_use",
                        "when_not_to_use",
                        "tradeoffs",
                        "alternatives",
                        "implementation_hooks",
                        "quality_attributes",
                        "evidence",
                        "delta_claim",
                        "lang_env_version",
                        "author_level",
                        "source_url",
                        "found_via",
                    }
                }
            )
    return trimmed


def _rfc_text(rfc_view: dict[str, Any]) -> str:
    parts: list[str] = []
    for field in RFC_VIEW_FIELDS:
        value = rfc_view.get(field)
        if isinstance(value, list):
            parts.extend(str(item) for item in value)
        else:
            parts.append(str(value or ""))
    return "\n".join(parts)


def _parse_normalized_problem(raw: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        return _normalized_problem_error(f"Codex problem normalization returned invalid JSON: {exc}")

    if not isinstance(parsed, dict):
        return _normalized_problem_error("Codex problem normalization returned non-object JSON")
    if set(parsed) != set(NORMALIZED_PROBLEM_FIELDS):
        return _normalized_problem_error("Codex problem normalization returned invalid fields")
    if not all(isinstance(parsed[field], str) for field in ("problem", "affected", "current_inadequacy")):
        return _normalized_problem_error("Codex problem normalization returned invalid string fields")
    for field in ("success_criteria", "non_goals"):
        value = parsed.get(field)
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            return _normalized_problem_error(f"Codex problem normalization returned invalid {field}")
    return {
        "problem": parsed["problem"],
        "affected": parsed["affected"],
        "current_inadequacy": parsed["current_inadequacy"],
        "success_criteria": list(parsed["success_criteria"]),
        "non_goals": list(parsed["non_goals"]),
    }


def _normalized_problem_error(reason: str) -> dict[str, Any]:
    return {"ok": False, "error": reason}


def _parse_constraints(raw: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        return _constraints_error(f"Codex constraint extraction returned invalid JSON: {exc}")

    if not isinstance(parsed, dict):
        return _constraints_error("Codex constraint extraction returned non-object JSON")
    if set(parsed) != set(EXTRACT_CONSTRAINTS_FIELDS):
        return _constraints_error("Codex constraint extraction returned invalid fields")

    hard_constraints = _parse_constraint_items(
        parsed.get("hard_constraints"),
        field="hard_constraints",
        item_fields=CONSTRAINT_ITEM_FIELDS,
        text_field="constraint",
    )
    if isinstance(hard_constraints, dict) and hard_constraints.get("ok") is False:
        return hard_constraints

    soft_preferences = _parse_constraint_items(
        parsed.get("soft_preferences"),
        field="soft_preferences",
        item_fields=PREFERENCE_ITEM_FIELDS,
        text_field="preference",
    )
    if isinstance(soft_preferences, dict) and soft_preferences.get("ok") is False:
        return soft_preferences

    return {
        "hard_constraints": hard_constraints,
        "soft_preferences": soft_preferences,
    }


def _parse_constraint_items(
    value: Any,
    *,
    field: str,
    item_fields: tuple[str, ...],
    text_field: str,
) -> list[dict[str, str]] | dict[str, Any]:
    if not isinstance(value, list):
        return _constraints_error(f"Codex constraint extraction returned invalid {field}")

    items: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            return _constraints_error(f"Codex constraint extraction returned invalid {field} item")
        if set(item) != set(item_fields):
            return _constraints_error(f"Codex constraint extraction returned invalid {field} item fields")
        if not all(isinstance(item[prop], str) for prop in item_fields):
            return _constraints_error(f"Codex constraint extraction returned invalid {field} item values")
        if item["source"] not in CONSTRAINT_SOURCE_VALUES:
            return _constraints_error(f"Codex constraint extraction returned invalid {field} source")
        items.append(
            {
                text_field: item[text_field],
                "source": item["source"],
                "why": item["why"],
            }
        )
    return items


def _constraints_error(reason: str) -> dict[str, Any]:
    return {"ok": False, "error": reason}


def _parse_prior_art_map(raw: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        return _prior_art_error(f"Codex prior-art mapping returned invalid JSON: {exc}")

    if not isinstance(parsed, dict):
        return _prior_art_error("Codex prior-art mapping returned non-object JSON")
    if set(parsed) != set(PRIOR_ART_MAP_FIELDS):
        return _prior_art_error("Codex prior-art mapping returned invalid fields")

    patterns = parsed.get("patterns")
    if not isinstance(patterns, list) or not 3 <= len(patterns) <= 6:
        return _prior_art_error("Codex prior-art mapping returned invalid patterns")

    parsed_patterns: list[dict[str, str]] = []
    for pattern in patterns:
        if not isinstance(pattern, dict):
            return _prior_art_error("Codex prior-art mapping returned invalid pattern item")
        if set(pattern) != set(PRIOR_ART_PATTERN_FIELDS):
            return _prior_art_error("Codex prior-art mapping returned invalid pattern fields")
        if not all(isinstance(pattern[field], str) for field in PRIOR_ART_PATTERN_FIELDS):
            return _prior_art_error("Codex prior-art mapping returned invalid pattern values")
        if pattern["disposition"] not in PRIOR_ART_DISPOSITIONS:
            return _prior_art_error("Codex prior-art mapping returned invalid disposition")
        parsed_patterns.append({field: pattern[field] for field in PRIOR_ART_PATTERN_FIELDS})

    return {"patterns": parsed_patterns}


def _prior_art_error(reason: str) -> dict[str, Any]:
    return {"ok": False, "error": reason}


def _repo_from_context(context: Mapping[str, Any] | None = None) -> Path:
    if isinstance(context, Mapping):
        repo = context.get("repo_root") or context.get("repo")
        if repo:
            return Path(str(repo)).resolve()
    return Path.cwd().resolve()


def _parse_grounding_result(raw: str, original_rfc_view: dict[str, Any]) -> GroundingResult:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return _grounding_fail_closed(original_rfc_view, f"Grounding returned invalid JSON: {raw}")

    if not isinstance(parsed, dict):
        return _grounding_fail_closed(original_rfc_view, f"Grounding returned non-object JSON: {raw}")

    confident = parsed.get("confident")
    proposed_rfc = parsed.get("proposed_rfc")
    assumptions = parsed.get("assumptions")
    grounding_notes = parsed.get("grounding_notes")
    questions = parsed.get("questions")
    if not isinstance(confident, bool):
        return _grounding_fail_closed(original_rfc_view, f"Grounding returned invalid confident: {raw}")
    if not _is_rfc_view(proposed_rfc):
        return _grounding_fail_closed(original_rfc_view, f"Grounding returned invalid proposed_rfc: {raw}")
    if not isinstance(assumptions, list) or not all(isinstance(assumption, str) for assumption in assumptions):
        return _grounding_fail_closed(original_rfc_view, f"Grounding returned invalid assumptions: {raw}")
    if not isinstance(grounding_notes, str) or len(grounding_notes) > 2000:
        return _grounding_fail_closed(original_rfc_view, f"Grounding returned invalid grounding_notes: {raw}")
    if not isinstance(questions, list) or not all(isinstance(question, str) for question in questions):
        return _grounding_fail_closed(original_rfc_view, f"Grounding returned invalid questions: {raw}")
    return GroundingResult(_rfc_to_view(proposed_rfc), grounding_notes, confident, assumptions, questions)


def _verify_grounding(
    request: dict[str, Any],
    rfc_view: dict[str, Any],
    grounding_result: GroundingResult,
) -> dict[str, Any]:
    violations = _lint_grounding(request, rfc_view, grounding_result)
    verdict = _run_grounding_verifier(request, rfc_view, grounding_result)

    if not verdict.get("ok", False):
        violations.append(str(verdict.get("violation", "C0 verifier: verifier failed closed")))
    else:
        semantic = verdict["verdict"]
        semantic_checks = (
            ("faithful_specific", "C1 faithfulness/specificity"),
            ("full_scope", "C2 full scope"),
            ("non_legal", "C3 non-legal"),
            ("latest_default", "C4 latest-default"),
        )
        reasons = "; ".join(semantic.get("reasons", []))
        for field, label in semantic_checks:
            if semantic.get(field) is not True:
                detail = f": {reasons}" if reasons else ""
                violations.append(f"{label}: verifier marked {field}=false{detail}")

    violations = _dedupe(violations)
    return {"ok": not violations, "violations": violations}


def _lint_grounding(
    request: dict[str, Any],
    rfc_view: dict[str, Any],
    grounding_result: GroundingResult,
) -> list[str]:
    text = _grounding_text(rfc_view, grounding_result)
    violations: list[str] = []

    generalizers = _matching_markers(text, GENERALIZER_MARKERS)
    if generalizers:
        violations.append(
            "C1 faithfulness/specificity lint: grounded RFC uses generalizer markers "
            + ", ".join(generalizers)
        )

    scope_hedges = _matching_markers(text, SCOPE_HEDGE_MARKERS)
    if scope_hedges:
        violations.append("C2 full scope lint: grounded RFC uses scope-shrinking markers " + ", ".join(scope_hedges))

    legal_hits = _keyword_hit_count(text, LEGAL_KEYWORDS)
    word_count = max(1, len(re.findall(r"\w+", text)))
    if legal_hits >= 3 and legal_hits / word_count >= 0.015:
        violations.append("C3 non-legal lint: legal/IP/trademark/copyright language dominates grounding output")

    if not _request_explicitly_retro(request):
        retro_markers = _matching_markers(text, RETRO_MARKERS)
        if retro_markers:
            violations.append(
                "C4 latest-default lint: grounded RFC targets dated/retro markers without a retro request "
                + ", ".join(retro_markers)
            )

    return violations


def _run_grounding_verifier(
    request: dict[str, Any],
    rfc_view: dict[str, Any],
    grounding_result: GroundingResult,
) -> dict[str, Any]:
    prompt = (
        "You are an adversarial read-only verifier for an RFC grounding contract.\n"
        "Given the original request and the grounded RFC view, judge these four checks:\n"
        "C1 faithful_specific: the grounded identity stays the specific named thing, not generalized to its category.\n"
        "C2 full_scope: the RFC does not shrink the request to a slice, demo, MVP, prototype, one area, or first iteration.\n"
        "C3 non_legal: IP, trademark, copyright, licensing, or legal risk content does not dominate the output.\n"
        "C4 latest_default: unless the request explicitly asked for retro, old, classic, vintage, or a specific past "
        "version/year, the RFC targets the latest/current version, conventions, and best practices of the named thing.\n"
        "Be strict. Return false for any check that is semantically violated, even if wording tries to hide it.\n\n"
        + _format_rfc("Original request common-8", _rfc_to_view(request))
        + "\n"
        + _format_rfc("Grounded RFC common-8", _rfc_to_view(rfc_view))
        + f"\ngrounding_notes: {grounding_result.grounding_notes}\n"
        + "\nReturn only JSON matching the provided schema."
    )
    temp_dir = Path(tempfile.mkdtemp(prefix="ai-org-rfc-grounding-verify-"))
    schema_file = temp_dir / "rfc-grounding-verdict.schema.json"
    out_file = temp_dir / "grounding-verdict.json"
    try:
        schema_file.write_text(json.dumps(GROUNDING_VERDICT_SCHEMA, indent=2), encoding="utf-8")
        cmd = [
            "codex",
            "exec",
            "--sandbox",
            "read-only",
            "-o",
            str(out_file),
            "--output-schema",
            str(schema_file),
            prompt,
        ]
        try:
            completed = subprocess.run(
                cmd,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
            )
        except OSError as exc:
            return {"ok": False, "violation": f"C0 verifier: grounding verifier failed: {exc}"}

        if completed.returncode != 0:
            detail = completed.stderr.strip() or (
                "no output file" if not out_file.exists() else "Codex verifier did not complete successfully."
            )
            return {"ok": False, "violation": f"C0 verifier: grounding verifier failed: {detail}"}
        if not out_file.exists():
            return {"ok": False, "violation": "C0 verifier: grounding verifier failed: no output file"}

        return _parse_grounding_verdict(out_file.read_text(encoding="utf-8"))
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _parse_grounding_verdict(raw: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {"ok": False, "violation": f"C0 verifier: grounding verifier returned invalid JSON: {raw}"}

    if not isinstance(parsed, dict):
        return {"ok": False, "violation": f"C0 verifier: grounding verifier returned non-object JSON: {raw}"}

    expected = ("faithful_specific", "full_scope", "non_legal", "latest_default")
    if not all(isinstance(parsed.get(field), bool) for field in expected):
        return {"ok": False, "violation": f"C0 verifier: grounding verifier returned invalid booleans: {raw}"}
    reasons = parsed.get("reasons")
    if not isinstance(reasons, list) or not all(isinstance(reason, str) for reason in reasons):
        return {"ok": False, "violation": f"C0 verifier: grounding verifier returned invalid reasons: {raw}"}
    return {"ok": True, "verdict": parsed}


def _grounding_text(rfc_view: dict[str, Any], grounding_result: GroundingResult) -> str:
    parts: list[str] = []
    for field in RFC_VIEW_FIELDS:
        value = rfc_view[field]
        if isinstance(value, list):
            parts.extend(value)
        else:
            parts.append(value)
    parts.append(grounding_result.grounding_notes)
    return f" {' '.join(parts).lower()} "


def _matching_markers(text: str, markers: tuple[str, ...]) -> list[str]:
    return [marker for marker in markers if marker.lower() in text]


def _keyword_hit_count(text: str, keywords: tuple[str, ...]) -> int:
    return sum(text.count(keyword.lower()) for keyword in keywords)


def _request_explicitly_retro(request: dict[str, Any]) -> bool:
    text = _grounding_text(_rfc_to_view(request), GroundingResult(_rfc_to_view(request)))
    if _matching_markers(text, RETRO_REQUEST_MARKERS):
        return True
    years = [int(year) for year in re.findall(r"\b(19\d{2}|20[0-2]\d)\b", text)]
    return any(year < 2026 for year in years)


def _with_unresolved_violations(notes: str, violations: list[str]) -> str:
    suffix = " Unresolved grounding contract violations: " + "; ".join(violations)
    return (notes + suffix)[:2000]


def _dedupe(values: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value not in seen:
            seen.add(value)
            deduped.append(value)
    return deduped


def _grounding_fail_closed(rfc_view: dict[str, Any], reason: str) -> GroundingResult:
    assumption = "I assumed the current common-8 request is the closest available interpretation because grounding failed before it could produce a researched proposal."
    question = "Can you confirm or correct the proposed RFC interpretation?"
    return GroundingResult(_rfc_to_view(rfc_view), reason[:2000], False, [assumption], [question])


def _required_string_field(data: Mapping[str, Any], field: str) -> None:
    value = data.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Request field {field!r} is required and must be a non-empty string.")


def _optional_string_field(data: dict[str, Any], field: str) -> None:
    value = data.setdefault(field, "")
    if not isinstance(value, str):
        raise ValueError(f"Request field {field!r} must be a string when provided.")


def _optional_list_field(data: dict[str, Any], field: str) -> None:
    value = data.setdefault(field, [])
    if not isinstance(value, list):
        raise ValueError(f"Request field {field!r} must be a list when provided.")
    if not all(isinstance(item, str) for item in value):
        raise ValueError(f"Request field {field!r} must contain only strings.")


def _common_8(data: Mapping[str, Any]) -> dict[str, Any]:
    rfc = {field: data[field] for field in COMMON_8_FIELDS}
    for field in REQUIRED_FIELDS + OPTIONAL_STRING_FIELDS:
        if not isinstance(rfc[field], str):
            raise ValueError(f"Request field {field!r} must be a string.")
    alternatives = rfc["alternatives"]
    if not isinstance(alternatives, list) or not all(isinstance(item, str) for item in alternatives):
        raise ValueError("Request field 'alternatives' must be a list of strings.")
    return rfc


def _is_rfc_view(value: object) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == set(RFC_VIEW_FIELDS)
        and all(isinstance(value[field], str) for field in RFC_VIEW_FIELDS if field != "alternatives")
        and isinstance(value["alternatives"], list)
        and all(isinstance(item, str) for item in value["alternatives"])
    )


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


def _format_alternatives(value: Any) -> str:
    if isinstance(value, list):
        return "\n".join(f"- {item}" for item in value)
    return str(value)


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return slug[:80] or "rfc"


def _write_rfc_branch(
    repo: Path,
    branch: str,
    base: str,
    rfc: Mapping[str, Any],
    *,
    rfc_path: str = "rfc.json",
    extra_files: Mapping[str, Any] | None = None,
    commit_message: str | None = None,
) -> dict[str, str]:
    rfc_view = _common_8(rfc)
    original = _current_branch(repo)
    try:
        _git(repo, "checkout", "-B", branch, base)
        path = repo / rfc_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(rfc_view, indent=2) + "\n", encoding="utf-8")
        add_paths = [rfc_path]
        for rel_path, payload in (extra_files or {}).items():
            extra_path = repo / rel_path
            extra_path.parent.mkdir(parents=True, exist_ok=True)
            extra_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            add_paths.append(rel_path)
        _git(repo, "add", *add_paths)
        _git(repo, "commit", "--allow-empty", "-m", commit_message or f"rfc: write {rfc_view['title']}")
        commit = _git(repo, "rev-parse", "HEAD").strip()
        return {"branch": branch, "commit": commit}
    finally:
        if original:
            _git(repo, "checkout", original)


def _default_branch(repo: Path) -> str:
    origin_head = _git_run(repo, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    if origin_head.returncode == 0:
        ref = origin_head.stdout.strip()
        if ref.startswith("origin/"):
            return ref

    current = _git_run(repo, "symbolic-ref", "--short", "HEAD")
    if current.returncode == 0 and current.stdout.strip():
        return current.stdout.strip()

    raise RuntimeError("could not determine repository default branch")


def _current_branch(repo: Path) -> str:
    current = _git_run(repo, "symbolic-ref", "--short", "HEAD")
    if current.returncode == 0:
        return current.stdout.strip()
    return ""


def _git(repo: Path, *args: str) -> str:
    result = _git_run(repo, *args)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "git command failed")
    return result.stdout


def _git_run(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
