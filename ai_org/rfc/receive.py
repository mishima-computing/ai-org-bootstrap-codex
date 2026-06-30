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
# promoted RFC (ai-org/rfc/<id>: rfc.json), or send the request back with specific questions.
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
    "required": ["sufficient", "grounded_rfc", "grounding_notes", "questions"],
    "properties": {
        "sufficient": {"type": "boolean"},
        "grounded_rfc": {
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
        "grounding_notes": {"type": "string", "maxLength": 2000},
        "questions": {"type": "array", "items": {"type": "string"}},
    },
}


@dataclass
class GroundingResult:
    rfc_view: dict[str, Any]
    grounding_notes: str = ""
    sufficient: bool = True
    questions: list[str] = field(default_factory=list)


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
    """Validate and ground a raw request, then promote it only when grounding is sufficient."""
    try:
        request = receive(source)
        return produce_rfc(request, repo, rfc_path)
    except ValueError as exc:
        return {"status": "rejected", "error": str(exc)}


def produce_rfc(validated_request: Mapping[str, Any], repo: str | Path, rfc_path: str = "rfc.json") -> dict[str, Any]:
    """Ground and write a validated COMMON-8 request to git as ai-org/rfc/<id>:rfc.json."""
    raw_rfc = _common_8(validated_request)
    repo_path = Path(repo).resolve()
    grounding = _ground_request(repo_path, raw_rfc)
    if not grounding.sufficient:
        return {
            "status": "needs_clarification",
            "questions": grounding.questions,
            "grounding_notes": grounding.grounding_notes,
        }

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


def _ground_request(repo: str | Path, rfc_view: dict[str, Any]) -> GroundingResult:
    """Research and correct a rough request before it becomes an RFC branch."""
    prompt = (
        "You are the RFC intake grounding step for AI Org.\n"
        "Your job is to turn a rough, vague, or even wrong request into the right well-grounded RFC view "
        "before an RFC branch is created.\n\n"
        "Use web search when the request names or implies a real product, game, genre, paper, standard, "
        "company, library, tool, or other external reference. Determine what the reference actually is, "
        "including genre, core mechanics, conventions, and prior art. Correct misconceptions in the request. "
        "Also inspect the target repository read-only to identify existing code, patterns, constraints, "
        "and affected areas. Do not modify files.\n\n"
        "If the request is too under-specified to ground confidently, set sufficient=false, keep grounded_rfc "
        "as the best common-8 view of the current request, and return specific requester questions. Do not "
        "invent missing product references, scope, or acceptance criteria.\n\n"
        "If sufficient=true, return a grounded common-8 RFC view that preserves useful user intent while "
        "correcting false assumptions, wrong genre/scope, and missing context. grounding_notes must briefly "
        "state what you found, what you corrected, and cite web references when used. questions must be an "
        "empty list when sufficient=true.\n\n"
        + _format_rfc("Current request common-8", _rfc_to_view(rfc_view))
        + "\nReturn only JSON matching the provided schema."
    )
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


def _parse_grounding_result(raw: str, original_rfc_view: dict[str, Any]) -> GroundingResult:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return _grounding_fail_closed(original_rfc_view, f"Grounding returned invalid JSON: {raw}")

    if not isinstance(parsed, dict):
        return _grounding_fail_closed(original_rfc_view, f"Grounding returned non-object JSON: {raw}")

    sufficient = parsed.get("sufficient")
    grounded_rfc = parsed.get("grounded_rfc")
    grounding_notes = parsed.get("grounding_notes")
    questions = parsed.get("questions")
    if not isinstance(sufficient, bool):
        return _grounding_fail_closed(original_rfc_view, f"Grounding returned invalid sufficient: {raw}")
    if not _is_rfc_view(grounded_rfc):
        return _grounding_fail_closed(original_rfc_view, f"Grounding returned invalid grounded_rfc: {raw}")
    if not isinstance(grounding_notes, str) or len(grounding_notes) > 2000:
        return _grounding_fail_closed(original_rfc_view, f"Grounding returned invalid grounding_notes: {raw}")
    if not isinstance(questions, list) or not all(isinstance(question, str) for question in questions):
        return _grounding_fail_closed(original_rfc_view, f"Grounding returned invalid questions: {raw}")
    if not sufficient and not [question for question in questions if question.strip()]:
        return _grounding_fail_closed(original_rfc_view, "Grounding marked insufficient without questions")
    return GroundingResult(_rfc_to_view(grounded_rfc), grounding_notes, sufficient, questions)


def _grounding_fail_closed(rfc_view: dict[str, Any], reason: str) -> GroundingResult:
    question = "Please clarify the request so intake can ground it confidently."
    return GroundingResult(_rfc_to_view(rfc_view), reason[:2000], False, [question])


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
