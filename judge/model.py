"""Plain dataclasses for every link of the provenance chain.

The report is the deliverable of the judge: one object per chain link,
each carrying `ok` + `reason` so a broken link names itself instead of
being papered over. `to_dict()` on every dataclass yields JSON-safe dicts.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any, Optional


def _to_jsonable(value: Any) -> Any:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {k: _to_jsonable(v) for k, v in dataclasses.asdict(value).items()}
    if isinstance(value, dict):
        return {k: _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(v) for v in value]
    return value


class _Jsonable:
    def to_dict(self) -> dict:
        return _to_jsonable(self)  # type: ignore[arg-type]


@dataclass
class Target(_Jsonable):
    """What is being adjudicated: a spot inside the committed body."""

    body_path: str
    term: Optional[str] = None
    node_arg: Optional[str] = None
    # Resolved against the tip blob:
    json_path: Optional[list] = None  # path of the matched scalar
    node_id: Optional[str] = None  # nearest enclosing node carrying an "id"
    node_path: Optional[list] = None
    scalar_text: Optional[str] = None  # full scalar containing the term
    ok: bool = False
    reason: str = ""


@dataclass
class RoundRecordHit(_Jsonable):
    """A committed review-round record that names the target node."""

    record_path: str
    review_round: Optional[int]
    author_version: Optional[int]
    listed_as: str  # "added" | "changed" | "affected"


@dataclass
class VersionLink(_Jsonable):
    """field -> version: the commit that introduced/last changed the target."""

    ok: bool = False
    reason: str = ""
    commit: str = ""
    commit_subject: str = ""
    committed_at: str = ""  # ISO committer date of the change commit
    parent_commit: str = ""  # previous commit touching the body (or "")
    window_start: str = ""  # parent committer date (or "")
    window_end: str = ""  # change commit committer date
    change_kind: str = ""  # "introduced" | "changed"
    version_label: str = ""  # e.g. "v2" (from commit subject), "" if unknown
    round_records: list = field(default_factory=list)  # list[RoundRecordHit]


@dataclass
class CarrierCall(_Jsonable):
    """One carrier (codex exec) subprocess recorded in a run log."""

    index: int = 0
    started_at: str = ""
    completed_at: str = ""
    exit_code: Optional[int] = None
    argv0: str = ""
    output_name: str = ""  # basename of the -o target (step-shaped name)
    prompt_head: str = ""
    payload_ref: str = ""  # spill file under artifacts/payloads, or
    #                        "supervisor.jsonl:<line>" when the payload
    #                        was inline (not truncated)
    stdout_path: str = ""
    stderr_path: str = ""
    stage: str = ""  # top-level supervisor stage for this call
    step: str = ""  # step name attributed to this call
    step_source: str = ""  # "approach-window" | "output-name" | ""
    session_uuid: str = ""
    rollout_path: str = ""
    confidence: str = "none"  # session-id+content | session-id | content
    #                           | cwd+time | none
    content_hit: bool = False  # target/probe text found in the call stdout


@dataclass
class RunLink(_Jsonable):
    """version -> run: the producing engine run."""

    ok: bool = False
    reason: str = ""
    run_dir: str = ""
    span_start: str = ""
    span_end: str = ""
    series_mentioned: bool = False
    candidates: list = field(default_factory=list)  # other run dirs in window
    steps: list = field(default_factory=list)  # approach/reform step names


@dataclass
class SessionLink(_Jsonable):
    """call -> session: the Codex rollout behind the carrier call."""

    ok: bool = False
    reason: str = ""
    uuid: str = ""
    rollout_path: str = ""
    cwd: str = ""
    confidence: str = "none"


@dataclass
class ThinkingItem(_Jsonable):
    """One verbatim recorded item from the rollout, with its line number."""

    line_no: int
    kind: str  # reasoning | agent_message | assistant_message | file_read
    #            | user_prompt
    text: str  # verbatim excerpt (clipped, never rewritten)
    encrypted: bool = False  # reasoning stored as encrypted_content only
    author: str = ""  # for inter-agent messages


@dataclass
class ThinkingLink(_Jsonable):
    """session -> thinking: mechanically extracted recorded deliberation."""

    ok: bool = False
    reason: str = ""
    anchor_lines: list = field(default_factory=list)
    matched_on: str = ""  # "scalar" | "term"
    items: list = field(default_factory=list)  # list[ThinkingItem]
    encrypted_reasoning_count: int = 0


@dataclass
class ProvenanceReport(_Jsonable):
    """The full adjudication: every chain link, resolved or broken."""

    vessel: str
    branch: str
    body_path: str
    # Origin mode: full sha the body/target were resolved AS OF ("" = tip).
    at_commit: str = ""
    target: Target = field(default_factory=lambda: Target(body_path=""))
    version: VersionLink = field(default_factory=VersionLink)
    run: RunLink = field(default_factory=RunLink)
    call: Optional[CarrierCall] = None
    session: SessionLink = field(default_factory=SessionLink)
    thinking: ThinkingLink = field(default_factory=ThinkingLink)

    @property
    def broken_link(self) -> str:
        """Name of the first unresolved link, or '' when fully resolved."""
        for name in ("target", "version", "run", "session", "thinking"):
            part = getattr(self, name)
            if part is None or not getattr(part, "ok", False):
                return name
        if self.call is None:
            return "call"
        return ""
