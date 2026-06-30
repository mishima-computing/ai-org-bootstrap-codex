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
    original = _current_branch(repo_path)

    try:
        _git(repo_path, "checkout", "-B", branch, base)
        path = repo_path / rfc_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(rfc, indent=2) + "\n", encoding="utf-8")
        _git(repo_path, "add", rfc_path)
        _git(repo_path, "commit", "--allow-empty", "-m", f"rfc: receive {rfc['title']}")
        commit = _git(repo_path, "rev-parse", "HEAD").strip()
    finally:
        if original:
            _git(repo_path, "checkout", original)
    return {
        "ok": True,
        "status": "promoted",
        "id": rfc_id,
        "branch": branch,
        "commit": commit,
        "grounding_notes": grounding.grounding_notes,
    }


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
