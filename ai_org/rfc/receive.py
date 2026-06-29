# receive.py — the INTAKE GATE: judges whether an incoming REQUEST may become an RFC.
# This is NOT a dumb loader/translator. A request is discussed and can be SENT BACK (差し戻し):
#     request --[receive gate]--> promote to RFC | send back for revision | reject.
# Only requests that PASS this gate go on to be MATURED into an RFC. The RFC phase has two parts:
#     1) receive : intake — can this REQUEST become an RFC at all? (this file)
#     2) review  : the FORMATION / MATURATION engine — mature the request into a contributor-takeable
#                  RFC (refine the common-8, craft the exit-only). NOT a separate "direction review";
#                  it IS the formation (the 5 過程). (review.py)
# THE RFC PHASE'S JOB = take a raw REQUEST and make it CONTRIBUTOR-TAKEABLE. That promotion is real
# work, not a load. It mirrors the Linux early-stage process (kernel.org process/3.Early-stage) — the
# "5 過程" that turn a request into a proposable RFC:
#     1) Specify the problem   — what must be solved, who is affected, where the system falls short
#     2) Early discussion      — surface objections / alternatives BEFORE implementation
#     3) Who do you talk to    — route to the right reviewers/maintainers (the right subsystem)
#     4) When to post          — the problem + intended approach are stated well enough to act on
#     5) Get buy-in            — go / no-go approval to proceed
# Only after these is the RFC ready for a Contributor to TAKE and implement. (This whole front-end was
# being IGNORED — receive was treated as a loader. It is not: it does the promotion work + the gate.)
#
# DECISION: these 5 過程 happen INSIDE the RFC formation — one codex-driven stage, like review's internal
# 5-reviewer + Aufheben loop — NOT as 5 separate git stages/branches/commits. Git stores ONLY the result:
# the promoted, contributor-takeable RFC (ai-org/rfc/<id>: rfc.json) or a send-back/reject marker. Doing
# the 5 過程 inside the RFC (not in git) keeps the git state from exploding.
#
# INPUT/OUTPUT field contract (grounded in REAL templates — Rust RFC 0000-template, PEP 12, Fuchsia RFC,
# Google design doc, GitLab feature proposal, kernel submitting-patches; not abstraction):
#   入り口 REQUEST (rough) carries the COMMON-8 through-line fields:
#       title, problem/motivation, proposal, alternatives, intended_users, affected_area, impact, context/links
#   出口 RFC (contributor-takeable) = the COMMON-8 (now REFINED) + the EXIT-ONLY fields the formation crafts:
#       goals & non-goals, reference-level design/spec, API/interface, backwards-compat, security, privacy,
#       testing, drawbacks, open-questions, future-possibilities, + meta (status, reviewers, resolution)
#   Formation's job = refine the common-8 AND craft the exit-only -> a contributor-takeable RFC (or send-back).
#   (A good request template is already a mini-RFC; the RFC adds the design/decision/meta the request lacks.)
#
# Shape (to match the other stages): git-read the request -> codex judges (gate) -> git-write either
# the promoted RFC (ai-org/rfc/<id>: rfc.json) or a send-back/reject marker.
#
# STATUS (be honest): the code BELOW is still only the manual loader/translator — the GATE (codex
# judgment + send-back, git read/write) is NOT built yet. TODO: build the gate in this form.
"""RFC receive — step 1 of the RFC phase: validate the entrance request."""
from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
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


def produce_rfc(validated_request: Mapping[str, Any], repo: str | Path, rfc_path: str = "rfc.json") -> dict[str, Any]:
    """Write a validated COMMON-8 request to git as ai-org/rfc/<id>:rfc.json."""
    rfc = _common_8(validated_request)
    repo_path = Path(repo).resolve()
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
    return {"ok": True, "id": rfc_id, "branch": branch, "commit": commit}


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
