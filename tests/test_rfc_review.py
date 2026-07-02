from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from ai_org.rfc import review


RFC_ID = "manual-rfc"
RFC_BRANCH = f"ai-org/rfc/{RFC_ID}"


def _rfc_view() -> dict[str, object]:
    return {
        "raw_request": "Run real RFC review.",
        "working_title": "Manual RFC",
        "request_type": "feature",
        "problem_or_motivation": "The workflow needs real RFC review.",
        "intended_users_or_jobs": "Contributors taking RFC work.",
        "desired_outcomes_success": "RFC direction converges before patch work starts.",
        "affected_area_platform": "ai_org.rfc",
        "tech_stack": {
            "build_strategy": "framework_based",
            "engine": "",
            "framework": "repo-native Python modules",
            "language": "Python",
            "platform": "CLI",
            "rationale": "Use the repository's existing Python modules.",
            "provenance": "requester_specified",
        },
        "background_facts": "The target repo is read-only during review.",
        "constraints_assumptions": [],
        "references": [],
        "grounding_provenance": "Test fixture grounding.",
        "open_questions": [],
        "non_goals_out_of_scope": [],
        "proposal_hint": "Run dimension reviewers and consolidate objections.",
        "alternatives_considered": ["Keep loading RFCs directly."],
    }


def _approach_tree() -> dict[str, object]:
    return {
        "problem": {
            "id": "problem",
            "summary": "RFC review needs anchored direction critique.",
            "constraints": {
                "hard": [{"id": "constraint:hard:1", "text": "Review never authors fixes."}],
                "soft": [{"id": "constraint:soft:1", "text": "Keep output schema safe."}],
            },
            "prior_art": [{"id": "prior_art:lkml", "summary": "LKML RFC threads use anchored replies."}],
            "question": {
                "id": "question:approach",
                "candidates": [
                    {"id": "candidate:status-quo", "summary": "Keep the old shell."},
                    {"id": "candidate:anchored-review", "summary": "Use anchored objections."},
                ],
                "decision": {
                    "id": "decision:anchored-review",
                    "selected_candidate_id": "candidate:anchored-review",
                    "implementation": {
                        "id": "implementation:anchored-review",
                        "patch_plan": {"id": "patch_plan:anchored-review", "first": "Review only."},
                        "risks": [{"id": "risk:review-loop", "summary": "Review could author fixes."}],
                    },
                },
            },
        },
        "cross_links": [
            {"from": "decision:anchored-review", "to": "question:approach", "type": "answers"},
        ],
    }


def _evidence(term: str = "Manual RFC") -> list[dict[str, object]]:
    return [{"source_type": "reference", "citation": "Reference lookup consulted.", "consulted_terms": [term]}]


def _objection(
    axis: str = "approach",
    *,
    objection_id: str | None = None,
    anchors: list[str] | None = None,
    objection_type: str = "blocking",
    claim: str = "The selected approach does not preserve the review boundary.",
    evidence: list[dict[str, object]] | None = None,
    impact: str = "Review can mutate the direction before the author reposts.",
    action: str = "revise_subtree",
) -> dict[str, object]:
    return {
        "objection_id": objection_id or f"{axis}:1",
        "anchor_node_ids": ["decision:anchored-review"] if anchors is None else anchors,
        "axis": axis,
        "type": objection_type,
        "claim": claim,
        "evidence": evidence if evidence is not None else _evidence(),
        "impact": impact,
        "requested_author_action": action,
        "status": "open",
    }


def _axis_payload(axis: str, objections: list[dict[str, object]] | None = None) -> str:
    objections = objections or []
    return json.dumps(
        {
            "axis": axis,
            "verdict": "objections_pending" if any(item["type"] == "blocking" for item in objections) else "Direction-reviewed-by",
            "objections": objections,
        }
    )


def _aufheben_payload(
    verdict: str = "direction-ok",
    objections: list[dict[str, object]] | None = None,
    *,
    nak_reason: str = "",
) -> str:
    return json.dumps(
        {
            "verdict": verdict,
            "summary": "Consolidated the RFC direction-review round.",
            "deduplicated_objections": objections or [],
            "contradiction_resolutions": [],
            "nak_reason": nak_reason,
            "evidence": _evidence() if verdict == "nak" else [],
        }
    )


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _init_repo(repo: Path, *, include_approach: bool = True) -> None:
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True, text=True)
    _git(repo, "config", "user.name", "RFC Test")
    _git(repo, "config", "user.email", "rfc-test@example.invalid")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "base")
    _git(repo, "branch", "-M", "main")
    _git(repo, "checkout", "-B", RFC_BRANCH, "main")
    (repo / "rfc.json").write_text(json.dumps(_rfc_view()) + "\n", encoding="utf-8")
    _git(repo, "add", "rfc.json")
    if include_approach:
        (repo / "technical-approach.json").write_text(json.dumps(_approach_tree()) + "\n", encoding="utf-8")
        _git(repo, "add", "technical-approach.json")
    _git(repo, "commit", "-m", "initial rfc")
    _git(repo, "checkout", "main")


def _latest_commit_message(repo: Path) -> str:
    return _git(repo, "log", "-1", "--pretty=%B", RFC_BRANCH).stdout


def _schema_kind(output_schema: str | Path) -> str:
    schema = json.loads(Path(output_schema).read_text(encoding="utf-8"))
    if schema == review.OBJECTION_SCHEMA:
        return "reviewer"
    if schema == review.AUFHEBEN_SCHEMA:
        return "aufheben"
    raise AssertionError(f"unexpected schema: {schema}")


def _install_codex_fake(monkeypatch: pytest.MonkeyPatch, handler):
    real_run = review.subprocess.run

    def fake_run(cmd, *args, **kwargs):
        if cmd and cmd[0] == "codex":
            assert cmd[:4] == ["codex", "exec", "--sandbox", "read-only"]
            out_file = Path(cmd[cmd.index("-o") + 1])
            output_schema = Path(cmd[cmd.index("--output-schema") + 1])
            repo = Path(cmd[cmd.index("-C") + 1])
            prompt = cmd[-1]
            payload, returncode = handler(repo, prompt, output_schema)
            if payload is not None:
                out_file.write_text(payload, encoding="utf-8")
            return subprocess.CompletedProcess(cmd, returncode, stdout="", stderr="")
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(review.subprocess, "run", fake_run)


def _patch_reference(monkeypatch: pytest.MonkeyPatch, calls: list[str] | None = None) -> None:
    def fake_lookup(term, context=None, kind=None):
        if calls is not None:
            calls.append(term)
        return {"term": term, "candidates": [{"summary": f"Stored guidance for {term}", "source": "Reference"}]}

    monkeypatch.setattr(review.reference, "lookup", fake_lookup)


def test_missing_technical_approach_fails_closed_without_codex(tmp_path, monkeypatch):
    _init_repo(tmp_path, include_approach=False)

    result = review.run_rfc_review(tmp_path, RFC_ID)

    assert result.status == "nak"
    assert result.rounds == 0
    assert "technical-approach.json" in result.escalation_reason
    assert "rfc: nak" in _latest_commit_message(tmp_path)
    assert Path(result.round_record_path).exists()


def test_dangling_anchor_ids_feedback_retry(tmp_path, monkeypatch):
    _init_repo(tmp_path)
    _patch_reference(monkeypatch)
    reviewer_calls = 0
    prompts: list[str] = []

    def handler(repo, prompt, output_schema):
        nonlocal reviewer_calls
        kind = _schema_kind(output_schema)
        if kind == "aufheben":
            return _aufheben_payload("direction-ok"), 0
        axis = next(dimension.key for dimension in review.DIMENSIONS if f"axis: {dimension.key}" in prompt)
        reviewer_calls += 1
        prompts.append(prompt)
        if axis == "approach" and "Feedback from prior invalid output" not in prompt:
            return _axis_payload(axis, [_objection(axis, anchors=["missing:node"])]), 0
        return _axis_payload(axis), 0

    _install_codex_fake(monkeypatch, handler)

    result = review.run_rfc_review(tmp_path, RFC_ID)

    assert result.status == "direction-ok"
    assert reviewer_calls == len(review.DIMENSIONS) + 1
    assert any("unknown node ids" in prompt for prompt in prompts)


def test_blocking_objection_requires_impact_and_anchor(tmp_path, monkeypatch):
    _init_repo(tmp_path)
    _patch_reference(monkeypatch)
    reviewer_calls = 0

    def handler(repo, prompt, output_schema):
        nonlocal reviewer_calls
        kind = _schema_kind(output_schema)
        if kind == "aufheben":
            return _aufheben_payload("direction-ok"), 0
        axis = next(dimension.key for dimension in review.DIMENSIONS if f"axis: {dimension.key}" in prompt)
        reviewer_calls += 1
        if axis == "need" and "Feedback from prior invalid output" not in prompt:
            return _axis_payload(axis, [_objection(axis, anchors=[], impact="")]), 0
        return _axis_payload(axis), 0

    _install_codex_fake(monkeypatch, handler)

    result = review.run_rfc_review(tmp_path, RFC_ID)

    assert result.status == "direction-ok"
    record = json.loads(Path(result.round_record_path).read_text(encoding="utf-8"))
    need_review = next(axis for axis in record["axis_reviews"] if axis["axis"] == "need")
    assert need_review["attempts"] == 2
    assert any("anchor_node_ids" in error for error in need_review["validation_errors"])
    assert any("impact" in error for error in need_review["validation_errors"])


def test_nonblocking_objections_allow_direction_ok_marker_and_round_record(tmp_path, monkeypatch):
    _init_repo(tmp_path)
    reference_calls: list[str] = []
    _patch_reference(monkeypatch, reference_calls)
    nonblocking = _objection(
        "maintenance",
        objection_type="nonblocking_suggestion",
        claim="The wording could mention ownership more directly.",
        impact="",
        action="re_explain",
    )

    def handler(repo, prompt, output_schema):
        kind = _schema_kind(output_schema)
        if kind == "aufheben":
            return _aufheben_payload("direction-ok", [nonblocking]), 0
        axis = next(dimension.key for dimension in review.DIMENSIONS if f"axis: {dimension.key}" in prompt)
        return _axis_payload(axis, [nonblocking] if axis == "maintenance" else []), 0

    _install_codex_fake(monkeypatch, handler)

    result = review.run_rfc_review(tmp_path, RFC_ID)

    assert result.status == "direction-ok"
    assert "rfc: direction-ok" in _latest_commit_message(tmp_path)
    record = json.loads(Path(result.round_record_path).read_text(encoding="utf-8"))
    assert record["verdict"] == "direction-ok"
    assert record["objections"][0]["type"] == "nonblocking_suggestion"
    assert reference_calls
    assert record["axis_reviews"][0]["reference_consultations"]
    assert _git(tmp_path, "ls-files", result.round_record_path).stdout == ""


def test_blocking_objections_dedupe_to_needs_revision_marker(tmp_path, monkeypatch):
    _init_repo(tmp_path)
    _patch_reference(monkeypatch)
    blocking = _objection("approach")
    duplicate = {**blocking, "objection_id": "approach:duplicate"}

    def handler(repo, prompt, output_schema):
        kind = _schema_kind(output_schema)
        if kind == "aufheben":
            return _aufheben_payload("needs_revision", [blocking, duplicate]), 0
        axis = next(dimension.key for dimension in review.DIMENSIONS if f"axis: {dimension.key}" in prompt)
        return _axis_payload(axis, [blocking, duplicate] if axis == "approach" else []), 0

    _install_codex_fake(monkeypatch, handler)

    result = review.run_rfc_review(tmp_path, RFC_ID)

    assert result.status == "needs_revision"
    assert [objection.axis for objection in result.unresolved] == ["approach"]
    assert "rfc: needs-revision round 1" in _latest_commit_message(tmp_path)
    record = json.loads(Path(result.round_record_path).read_text(encoding="utf-8"))
    assert record["verdict"] == "needs_revision"
    assert len(record["objections"]) == 1


def test_nak_path_is_evidenced_decision_record(tmp_path, monkeypatch):
    _init_repo(tmp_path)
    _patch_reference(monkeypatch)
    blocking = _objection("compat", claim="The direction breaks the RFC branch contract.")
    reason = "The direction is fundamentally incompatible with the RFC branch contract."

    def handler(repo, prompt, output_schema):
        kind = _schema_kind(output_schema)
        if kind == "aufheben":
            return _aufheben_payload("nak", [blocking], nak_reason=reason), 0
        axis = next(dimension.key for dimension in review.DIMENSIONS if f"axis: {dimension.key}" in prompt)
        return _axis_payload(axis, [blocking] if axis == "compat" else []), 0

    _install_codex_fake(monkeypatch, handler)

    result = review.run_rfc_review(tmp_path, RFC_ID)

    assert result.status == "nak"
    assert result.escalation_reason == reason
    assert "rfc: nak" in _latest_commit_message(tmp_path)
    record = json.loads(Path(result.round_record_path).read_text(encoding="utf-8"))
    assert record["consolidation"]["nak_reason"] == reason
    assert record["consolidation"]["evidence"]


def test_aufheben_schema_no_longer_returns_revised_rfc():
    serialized = json.dumps(review.AUFHEBEN_SCHEMA)
    assert "revised_rfc" not in serialized
    assert review.AUFHEBEN_SCHEMA["additionalProperties"] is False
    assert sorted(review.AUFHEBEN_SCHEMA["required"]) == sorted(review.AUFHEBEN_SCHEMA["properties"])


def test_review_schemas_codex_safe_required_properties():
    for schema in (
        review.EVIDENCE_SCHEMA,
        review.OBJECTION_ITEM_SCHEMA,
        review.OBJECTION_SCHEMA,
        review.CONTRADICTION_SCHEMA,
        review.AUFHEBEN_SCHEMA,
    ):
        serialized = json.dumps(schema)
        assert "allOf" not in serialized
        assert "anyOf" not in serialized
        assert "oneOf" not in serialized
        _assert_required_is_all_properties(schema)


def _assert_required_is_all_properties(schema: dict[str, object]) -> None:
    if schema.get("type") == "object":
        assert schema["additionalProperties"] is False
        assert sorted(schema["required"]) == sorted(schema["properties"])
        for child in schema["properties"].values():
            if isinstance(child, dict):
                _assert_required_is_all_properties(child)
    if schema.get("type") == "array":
        _assert_required_is_all_properties(schema["items"])
