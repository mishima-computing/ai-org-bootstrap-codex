"""Small patch series-local helper for running Codex with a JSON output schema."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Any

import ai_org.log as org_log
from ai_org.codex_reset import (
    TRANSIENT_RETRIES_EXHAUSTED,
    codex_resume_command,
    run_with_reset_retries,
    transient_retries_exhausted,
)


_PATH_RE = re.compile(
    r"\b([A-Za-z_][A-Za-z0-9_]*(?:\[[^\]]+\])?(?:\.[A-Za-z_][A-Za-z0-9_]*(?:\[[^\]]+\])?)*)\b"
)
_NON_PATH_WORDS = {
    "Codex",
    "Previous",
    "Reviewer",
    "Return",
    "Regenerate",
    "Correct",
    "Fix",
    "JSON",
}


@dataclass(frozen=True)
class ValidationRejectionAtom:
    validator_id: str
    rule_id: str
    error_class: str
    json_pointer: str
    subject_atom: str
    requirement: str
    fingerprint: str


@dataclass(frozen=True)
class ValidationRejection:
    validator_id: str
    error_class: str
    path: str
    requirement: str
    fingerprint: str
    atoms: tuple[ValidationRejectionAtom, ...] = ()

    @property
    def atom_fingerprints(self) -> tuple[str, ...]:
        return tuple(atom.fingerprint for atom in self.atoms) or ((self.fingerprint,) if self.fingerprint else ())


@dataclass(frozen=True)
class RejectionDecision:
    classification: str
    rejection: ValidationRejection
    permanent: bool = False
    repeated_fingerprints: tuple[str, ...] = ()


class RejectionRetryState:
    """Classify deterministic validation failures across attempts."""

    def __init__(self) -> None:
        self.previous_fingerprints: set[str] = set()

    def classify(
        self,
        rejection: ValidationRejection,
        *,
        attempt: int,
        ctx: org_log.RunContext | None = None,
    ) -> RejectionDecision:
        current_fingerprints = set(rejection.atom_fingerprints)
        repeated = tuple(sorted(current_fingerprints & self.previous_fingerprints))
        permanent = bool(repeated)
        classification = "permanent" if permanent else "stochastic-recoverable"
        self.previous_fingerprints = current_fingerprints
        decision = RejectionDecision(
            classification=classification,
            rejection=rejection,
            permanent=permanent,
            repeated_fingerprints=repeated,
        )
        emit_validation_rejection(decision, attempt=attempt, ctx=ctx)
        return decision


def validation_rejection(
    validator_id: str,
    requirement: str | list[str] | tuple[str, ...],
    *,
    error_class: str | None = None,
    path: str | None = None,
    rule_id: str | None = None,
    check_id: str | None = None,
    json_pointer: str | None = None,
    subject_atom: str | None = None,
) -> ValidationRejection:
    requirements = [requirement] if isinstance(requirement, str) else list(requirement)
    atoms = tuple(
        _rejection_atom(
            str(validator_id),
            str(item),
            error_class=error_class,
            path=path,
            rule_id=rule_id or check_id,
            json_pointer=json_pointer,
            subject_atom=subject_atom,
        )
        for item in requirements
    )
    clean_requirement = "; ".join(atom.requirement for atom in atoms)
    first = atoms[0]
    return ValidationRejection(
        validator_id=str(validator_id),
        error_class=first.error_class,
        path=first.json_pointer,
        requirement=clean_requirement,
        fingerprint=first.fingerprint,
        atoms=atoms,
    )


def rejection_fingerprint(
    validator_id: str,
    error_class: str,
    json_pointer: str,
    rule_id: str = "validation",
    subject_atom: str = "",
) -> str:
    identity = json.dumps(
        {
            "validator_id": validator_id,
            "rule_id": _normalize_atom(rule_id) or "validation",
            "error_class": error_class,
            "json_pointer": _to_json_pointer(json_pointer),
            "subject_atom": _normalize_atom(subject_atom),
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]


def _rejection_atom(
    validator_id: str,
    requirement: str,
    *,
    error_class: str | None,
    path: str | None,
    rule_id: str | None,
    json_pointer: str | None,
    subject_atom: str | None,
) -> ValidationRejectionAtom:
    clean_requirement = " ".join(str(requirement).split())
    clean_path = json_pointer or path or _extract_path(clean_requirement)
    clean_pointer = _to_json_pointer(clean_path)
    clean_class = error_class or _classify_error(clean_requirement)
    clean_rule = _normalize_atom(rule_id or _extract_rule_id(clean_requirement) or clean_class) or "validation"
    clean_subject = _normalize_atom(subject_atom or _extract_subject_atom(clean_requirement, clean_path))
    return ValidationRejectionAtom(
        validator_id=validator_id,
        rule_id=clean_rule,
        error_class=clean_class,
        json_pointer=clean_pointer,
        subject_atom=clean_subject,
        requirement=clean_requirement,
        fingerprint=rejection_fingerprint(validator_id, clean_class, clean_pointer, clean_rule, clean_subject),
    )


def format_rejection_feedback(rejection: ValidationRejection) -> str:
    lines = [
        "Previous deterministic validator rejection:",
        f"validator_id: {rejection.validator_id}",
    ]
    for atom in rejection.atoms:
        lines.extend(
            [
                f"rule_id: {atom.rule_id}",
                f"path: {atom.json_pointer}",
                f"json_pointer: {atom.json_pointer}",
                f"error_class: {atom.error_class}",
                f"subject_atom: {atom.subject_atom}",
                f"fingerprint: {atom.fingerprint}",
                f"requirement: {atom.requirement}",
            ]
        )
    return "\n".join(lines)


def permanent_rejection_error(decision: RejectionDecision) -> str:
    repeated = ", ".join(decision.repeated_fingerprints) or decision.rejection.fingerprint
    return (
        "Permanent deterministic validation rejection remained invalid: repeated atom fingerprint "
        f"{repeated} for {decision.rejection.validator_id}: "
        f"{decision.rejection.requirement}"
    )


def permanent_rejection_payload(decision: RejectionDecision) -> dict[str, Any]:
    return {
        "failure_mode": "permanent_rejection",
        "rejection_fingerprint": decision.repeated_fingerprints[0] if decision.repeated_fingerprints else decision.rejection.fingerprint,
        "rejection_fingerprints": list(decision.rejection.atom_fingerprints),
        "repeated_rejection_fingerprints": list(decision.repeated_fingerprints),
        "validator_id": decision.rejection.validator_id,
        "error_class": decision.rejection.error_class,
        "path": decision.rejection.path,
        "json_pointer": decision.rejection.path,
    }


def emit_validation_rejection(
    decision: RejectionDecision,
    *,
    attempt: int,
    ctx: org_log.RunContext | None,
) -> None:
    if ctx is None:
        return
    org_log.emit(
        "codex.validation_rejection.classified",
        {
            "attempt": attempt,
            "status": "failed",
            "classification": decision.classification,
            "failure_mode": "permanent_rejection" if decision.permanent else None,
            "fingerprint": decision.rejection.fingerprint,
            "atom_fingerprints": list(decision.rejection.atom_fingerprints),
            "repeated_fingerprints": list(decision.repeated_fingerprints),
            "defect_atoms": [
                {
                    "validator_id": atom.validator_id,
                    "rule_id": atom.rule_id,
                    "error_class": atom.error_class,
                    "json_pointer": atom.json_pointer,
                    "subject_atom": atom.subject_atom,
                    "fingerprint": atom.fingerprint,
                    "requirement": atom.requirement,
                }
                for atom in decision.rejection.atoms
            ],
            "validator_id": decision.rejection.validator_id,
            "error_class": decision.rejection.error_class,
            "path": decision.rejection.path,
            "json_pointer": decision.rejection.path,
            "requirement": decision.rejection.requirement,
        },
        ctx=ctx,
        severity="error" if decision.permanent else "warning",
    )


def _extract_rule_id(error: str) -> str:
    c_match = re.search(r"\b(C\d+(?:[A-Za-z0-9_.-]+)?)\b", error)
    if c_match:
        return c_match.group(1)
    prefix = error.split(":", 1)[0].strip()
    if prefix and len(prefix) <= 80 and any(char.isalpha() for char in prefix):
        return prefix
    return ""


def _extract_subject_atom(error: str, path: str) -> str:
    quoted = re.findall(r"['\"]([^'\"]{1,80})['\"]", error)
    if quoted:
        return quoted[0]
    lowered = error.lower()
    for marker in ("-style", "generic", "minimal", "mvp", "vertical slice", "prototype", "demo"):
        if marker in lowered:
            return marker
    cleaned = error.replace(path, " ")
    words = re.findall(r"[A-Za-z0-9_-]+", cleaned.lower())
    return " ".join(words[:10])


def _normalize_atom(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9_./-]+", str(value).lower()))[:160]


def _to_json_pointer(path: str) -> str:
    path = str(path or "$").strip()
    if not path or path == "$":
        return ""
    if path.startswith("/"):
        return path
    if path.startswith("$."):
        path = path[2:]
    elif path.startswith("$"):
        path = path[1:]
    pieces: list[str] = []
    for part in path.split("."):
        if not part:
            continue
        while "[" in part:
            head, tail = part.split("[", 1)
            if head:
                pieces.append(head)
            index, _, rest = tail.partition("]")
            if index:
                pieces.append(index.strip("'\""))
            part = rest
        if part:
            pieces.append(part)
    return "/" + "/".join(piece.replace("~", "~0").replace("/", "~1") for piece in pieces)


def _emit_codex_attempt(
    *,
    ctx: org_log.RunContext | None,
    attempt: int | None,
    status: str,
    classification: str,
    error: str | None = None,
) -> None:
    if ctx is None:
        return
    payload: dict[str, Any] = {
        "attempt": attempt if attempt is not None else (ctx.attempt if ctx.attempt is not None else 1),
        "status": status,
        "classification": classification,
    }
    if error:
        payload["error"] = error
    org_log.emit(
        "codex.attempt.completed",
        payload,
        ctx=ctx,
        severity="warning" if status == "failed" else "info",
    )


def _extract_path(error: str) -> str:
    for match in _PATH_RE.finditer(error):
        candidate = match.group(1)
        if candidate in _NON_PATH_WORDS:
            continue
        if "." in candidate or "[" in candidate:
            return candidate
    for phrase in (
        "invalid JSON",
        "non-object JSON",
        "invalid fields",
        "output fields do not match schema",
    ):
        if phrase in error:
            return "$"
    return "$"


def _classify_error(error: str) -> str:
    lowered = error.lower()
    if "invalid json" in lowered or "non-object json" in lowered:
        return "invalid_json"
    if "empty" in lowered:
        return "empty_slot"
    if "mismatch" in lowered or "wrong" in lowered:
        return "mismatch"
    if "duplicate" in lowered:
        return "duplicate"
    if "invalid field" in lowered or "fields do not match schema" in lowered:
        return "invalid_fields"
    return "validation"


def run_json(
    repo: Path,
    *,
    schema: dict[str, Any],
    prompt: str,
    schema_filename: str,
    output_filename: str,
    failure_label: str,
    ctx: org_log.RunContext | None = None,
    reasoning_effort: str | None = None,
) -> dict[str, Any]:
    """Run codex exec and return the raw JSON output text or a closed failure.

    reasoning_effort=None inherits the configured codex default (unchanged
    behavior for existing callers); a value overrides model_reasoning_effort
    for this single exec (e.g. "low" for bounded, skeleton-guided repair work).
    """
    # Memento: runs 11 and 12b spent 75+ minutes retrying the same deterministic
    # validator rejection. A retry must change the odds or stop: transient
    # subprocess errors keep backoff behavior, while validator rejections are
    # fingerprinted as the smallest actionable invariant: validator_id + rule_id
    # or check_id + error_class + json_pointer + normalized subject atom. Permanent
    # means the same atom repeats after targeted feedback for that atom. Never
    # fingerprint the whole error paragraph nor just the validator root. Example:
    # attempt 1 atoms {generic, minimal} followed by attempt 2 atom {-style} is
    # fresh/recoverable; attempt 1 {generic} followed by attempt 2 {generic at
    # the same rule/path/subject} is permanent.
    # Reference facets shaping this design (canonical store, AI_ORG_REFERENCE_STORE):
    # "retry error classification and fingerprinting" (transient / stochastic-recoverable /
    # permanent split; fingerprint on atom identity) and "deterministic validator
    # around stochastic generator" (law 5: a retry must change the odds or stop; law 10:
    # every deterministic rejection needs a compact stable fingerprint). Both facets are
    # grounded in Google SRE retry guidance via harness_lessons_research 2026-07-03.
    temp_dir = Path(tempfile.mkdtemp(prefix="ai-org-patch_series-codex-"))
    schema_file = temp_dir / schema_filename
    out_file = temp_dir / output_filename
    try:
        schema_file.write_text(json.dumps(schema, indent=2), encoding="utf-8")
        cmd = [
            "codex",
            "exec",
            "--sandbox",
            "read-only",
            "-C",
            str(repo),
            "-o",
            str(out_file),
            "--output-schema",
            str(schema_file),
        ]
        if reasoning_effort is not None:
            cmd.extend(["-c", f'model_reasoning_effort="{reasoning_effort}"'])
        cmd.append("--json")
        cmd.append(prompt)
        if ctx is None:
            # Memento: adapter boundary for legacy direct calls only. Pipeline
            # callers must pass their RunContext so one pull remains one run.
            log_ctx = org_log.RunContext(repo=repo, stage="codex_exec")
            org_log.emit(
                "log.context.adapter_boundary",
                {"helper": "ai_org.patchwork_queue.codex_exec.run_json", "reason": "missing caller RunContext"},
                ctx=log_ctx,
                severity="warning",
            )
        else:
            log_ctx = ctx
        try:
            def invoke(command: list[str]) -> subprocess.CompletedProcess[str]:
                return org_log.logged_subprocess(
                    command,
                    ctx=log_ctx,
                    capture_policy="head_tail",
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                )

            completed = run_with_reset_retries(
                lambda: invoke(cmd),
                ctx=log_ctx,
                event_name="patchwork_queue.codex_reset_wait",
                resume_once=lambda session_id: invoke(codex_resume_command(cmd, session_id)),
            )
        except OSError as exc:
            error = f"{failure_label} failed: {exc}"
            _emit_codex_attempt(ctx=log_ctx, attempt=log_ctx.attempt, status="failed", classification="transient", error=error)
            return {"ok": False, "error": error}
        if completed.returncode != 0:
            detail = completed.stderr.strip() or (
                "no output file" if not out_file.exists() else f"{failure_label} did not complete successfully."
            )
            error = f"{failure_label} failed: {detail}"
            if transient_retries_exhausted(completed):
                error = f"{error} [{TRANSIENT_RETRIES_EXHAUSTED}]"
                _emit_codex_attempt(
                    ctx=log_ctx,
                    attempt=log_ctx.attempt,
                    status="failed",
                    classification=TRANSIENT_RETRIES_EXHAUSTED,
                    error=error,
                )
                return {"ok": False, "error": error, "failure_mode": TRANSIENT_RETRIES_EXHAUSTED}
            _emit_codex_attempt(ctx=log_ctx, attempt=log_ctx.attempt, status="failed", classification="transient", error=error)
            return {"ok": False, "error": error}
        if not out_file.exists():
            error = f"{failure_label} failed: no output file"
            _emit_codex_attempt(ctx=log_ctx, attempt=log_ctx.attempt, status="failed", classification="transient", error=error)
            return {"ok": False, "error": error}
        _emit_codex_attempt(ctx=log_ctx, attempt=log_ctx.attempt, status="completed", classification="completed")
        return {"ok": True, "raw": out_file.read_text(encoding="utf-8")}
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
