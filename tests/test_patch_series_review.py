from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from ai_org.patchwork_queue import review
from ai_org import review_bodies
import ai_org.patchwork_queue.requester_assumptions as requester_assumptions
from ai_org.patchwork_queue.field_registry import empty_user_experience_requirements


RFC_ID = "manual-patch_series"
RFC_BRANCH = f"ai-org/patch-series/{RFC_ID}"


def _patch_series_view() -> dict[str, object]:
    return {
        "raw_request": "Run real patch series review.",
        "working_title": "Manual patch series",
        "request_type": "feature",
        "problem_or_motivation": "The workflow needs real patch series review.",
        "intended_users_or_jobs": "Contributors taking patch series work.",
        "desired_outcomes_success": "patch series direction converges before patch work starts.",
        "affected_area_platform": "ai_org.patchwork_queue",
        "tech_stack": {
            "build_strategy": "framework_based",
            "engine": "",
            "framework": "repo-native Python modules",
            "language": "Python",
            "platform": "CLI",
            "rationale": "Use the repository's existing Python modules.",
            "provenance": "requester_specified",
        },
        "user_experience_requirements": empty_user_experience_requirements(),
        "background_facts": "The target repo is read-only during review.",
        "constraints_assumptions": [],
        "references": [],
        "grounding_provenance": "Test fixture grounding.",
        "open_questions": [],
        "non_goals_out_of_scope": [],
        "proposal_hint": "Run dimension reviewers and consolidate objections.",
        "alternatives_considered": ["Keep loading patch seriess directly."],
    }


def _approach_tree() -> dict[str, object]:
    return {
        "problem": {
            "id": "problem",
            "summary": "patch series review needs anchored direction critique.",
            "constraints": {
                "hard": [{"id": "constraint:hard:1", "text": "Review never authors fixes."}],
                "soft": [{"id": "constraint:soft:1", "text": "Keep output schema safe."}],
            },
            "prior_art": [{"id": "prior_art:lkml", "summary": "LKML patch series threads use anchored replies."}],
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


def _evidence(term: str = "Manual patch series") -> list[dict[str, object]]:
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
    resolution_authority: str = "author",
    reviewer_confidence: str = "high",
    reviewed_scope: str = "The anchored nodes and their 1-hop neighborhood.",
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
        "resolution_authority": resolution_authority,
        "status": "open",
        # Brief 33: reviewer self-declaration fields (informational, never a gate).
        "reviewer_confidence": reviewer_confidence,
        "reviewed_scope": reviewed_scope,
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
            "summary": "Consolidated the patch series direction-review round.",
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
    _git(repo, "config", "user.name", "patch series Test")
    _git(repo, "config", "user.email", "patch_series-test@example.invalid")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "base")
    _git(repo, "branch", "-M", "main")
    _git(repo, "checkout", "-B", RFC_BRANCH, "main")
    (repo / "patch-series-cover-letter.json").write_text(json.dumps(_patch_series_view()) + "\n", encoding="utf-8")
    _git(repo, "add", "patch-series-cover-letter.json")
    if include_approach:
        (repo / "technical-approach-plan.json").write_text(json.dumps(_approach_tree()) + "\n", encoding="utf-8")
        _git(repo, "add", "technical-approach-plan.json")
    _git(repo, "commit", "-m", "initial patch_series")
    _git(repo, "checkout", "main")


def _latest_commit_message(repo: Path) -> str:
    return _git(repo, "log", "-1", "--pretty=%B", RFC_BRANCH).stdout


def _committed_round_record(repo: Path, result: review.ReviewResult) -> dict[str, object]:
    return review_bodies.read_record(repo, RFC_BRANCH, result.round_record_path, review_bodies.CONSOLIDATION_RECIPE)


def _schema_kind(output_schema: str | Path) -> str:
    schema = output_schema if isinstance(output_schema, dict) else json.loads(Path(output_schema).read_text(encoding="utf-8"))
    if schema == review.OBJECTION_SCHEMA:
        return "reviewer"
    if schema == review.AUFHEBEN_SCHEMA:
        return "aufheben"
    raise AssertionError(f"unexpected schema: {schema}")


def _install_codex_fake(monkeypatch: pytest.MonkeyPatch, handler):
    def fake_run_json(repo, **kwargs):
        payload, returncode = handler(Path(repo), kwargs["prompt"], kwargs["schema"])
        if returncode != 0:
            return {"ok": False, "error": "codex did not complete successfully"}
        return {"ok": True, "raw": payload or ""}

    monkeypatch.setattr(review.codex_exec, "run_json", fake_run_json)


def _write_round_record(repo: Path, round_number: int, objection: dict[str, object]) -> None:
    directory = repo / ".ai-org" / "review" / RFC_ID
    directory.mkdir(parents=True, exist_ok=True)
    record = {
        "patch_series_id": RFC_ID,
        "branch": RFC_BRANCH,
        "round": round_number,
        "inputs": {"patch_series_path": "patch-series-cover-letter.json", "technical_approach_path": "technical-approach-plan.json", "node_ids": []},
        "axis_reviews": [],
        "objections": [objection],
        "per_axis_verdicts": {},
        "consolidation": {},
        "verdict": "needs_revision",
        "git_result_marker": f"patch_series: needs-revision round {round_number}",
    }
    (directory / f"round-{round_number}.json").write_text(json.dumps(record) + "\n", encoding="utf-8")


def _patch_reference(monkeypatch: pytest.MonkeyPatch, calls: list[str] | None = None) -> None:
    def fake_lookup(term, context=None, kind=None):
        if calls is not None:
            calls.append(term)
        return {"term": term, "candidates": [{"summary": f"Stored guidance for {term}", "source": "Reference"}]}

    monkeypatch.setattr(review.engineering_precedent_store, "lookup", fake_lookup)


def test_missing_technical_approach_fails_closed_without_codex(tmp_path, monkeypatch):
    _init_repo(tmp_path, include_approach=False)

    result = review.run_patch_series_review(tmp_path, RFC_ID)

    assert result.status == "nak"
    assert result.rounds == 0
    assert "technical-approach-plan.json" in result.escalation_reason
    assert "patch_series: nak" in _latest_commit_message(tmp_path)
    assert _committed_round_record(tmp_path, result)["verdict"] == "nak"


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

    result = review.run_patch_series_review(tmp_path, RFC_ID)

    assert result.status == "direction-ok"
    # Brief 20: round 1 is sharded per step subtree; the approach dimension retries
    # once per shard, the other dimensions review each shard once.
    shard_count = len(review._round_one_shards(_approach_tree()))
    assert shard_count > 1
    assert reviewer_calls == shard_count * (len(review.DIMENSIONS) + 1)
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

    result = review.run_patch_series_review(tmp_path, RFC_ID)

    assert result.status == "direction-ok"
    record = _committed_round_record(tmp_path, result)
    need_review = next(axis for axis in record["axis_reviews"] if axis["axis"] == "need")
    # Brief 20: one retry per shard of the need dimension (2 attempts each).
    shard_count = len(review._round_one_shards(_approach_tree()))
    assert need_review["attempts"] == 2 * shard_count
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

    result = review.run_patch_series_review(tmp_path, RFC_ID)

    assert result.status == "direction-ok"
    assert "patch_series: direction-ok" in _latest_commit_message(tmp_path)
    record = _committed_round_record(tmp_path, result)
    assert record["verdict"] == "direction-ok"
    assert record["objections"][0]["type"] == "nonblocking_suggestion"
    assert reference_calls
    assert record["axis_reviews"][0]["reference_consultations"]
    assert _git(tmp_path, "show", f"{RFC_BRANCH}:{result.round_record_path}").stdout


def test_finished_round_posts_review_mail_with_binding_record_refs(tmp_path, monkeypatch):
    _init_repo(tmp_path)
    _patch_reference(monkeypatch)
    blocking = _objection("approach")

    def handler(repo, prompt, output_schema):
        kind = _schema_kind(output_schema)
        if kind == "aufheben":
            return _aufheben_payload("needs_revision", [blocking]), 0
        axis = next(dimension.key for dimension in review.DIMENSIONS if f"axis: {dimension.key}" in prompt)
        return _axis_payload(axis, [blocking] if axis == "approach" else []), 0

    _install_codex_fake(monkeypatch, handler)

    result = review.run_patch_series_review(tmp_path, RFC_ID)

    posts = review.mailing_list.read(tmp_path, kinds={"review"})
    assert len(posts) == 1
    post = posts[0]
    assert post["subject"] == f"{RFC_ID} round 0001 needs_revision"
    assert post["refs"] == {
        "series": RFC_BRANCH,
        "record": result.round_record_path,
        "commit": result.history[0]["git_result_commit"],
    }
    assert post["body"].splitlines()[:3] == [
        "resolved: 0",
        "resolved_by_construction: 0",
        "unresolved: 1",
    ]
    assert f"- {blocking['objection_id']}: {blocking['claim']}" in post["body"]


def test_round_mail_failure_warns_without_failing_round(tmp_path, monkeypatch):
    _init_repo(tmp_path)
    _patch_reference(monkeypatch)
    warnings: list[tuple[str, dict[str, object], str]] = []

    def handler(repo, prompt, output_schema):
        kind = _schema_kind(output_schema)
        if kind == "aufheben":
            return _aufheben_payload("direction-ok"), 0
        axis = next(dimension.key for dimension in review.DIMENSIONS if f"axis: {dimension.key}" in prompt)
        return _axis_payload(axis), 0

    def fail_post(*args, **kwargs):
        raise RuntimeError("list unavailable")

    def capture_warning(event_type, payload, *, ctx, severity="info"):
        warnings.append((event_type, payload, severity))

    _install_codex_fake(monkeypatch, handler)
    monkeypatch.setattr(review.mailing_list, "post", fail_post)
    monkeypatch.setattr(review.org_log, "debug_emit", capture_warning)

    result = review.run_patch_series_review(tmp_path, RFC_ID)

    assert result.status == "direction-ok"
    assert _git(tmp_path, "show", f"{RFC_BRANCH}:{result.round_record_path}").stdout
    assert warnings == [
        (
            "mailing_list.review.failed",
            {
                "series": RFC_BRANCH,
                "record": result.round_record_path,
                "commit": result.history[0]["git_result_commit"],
                "round": 1,
                "status": "direction-ok",
                "error": "list unavailable",
            },
            "warning",
        )
    ]


def test_round_mail_counts_cross_round_construction_resolution_separately():
    record = {
        "resolved_objections": [
            {"objection_id": "approach:resolved", "status": "resolved"},
        ],
        "cross_round_objection_mapping": [
            {
                "prior_objection_id": "compat:vanished",
                "status": "resolved_by_construction",
                "fresh_objection_ids": [],
            },
            {
                "prior_objection_id": "approach:persisting",
                "status": "unresolved",
                "fresh_objection_ids": ["approach:fresh"],
            },
        ],
        "objections": [
            {
                "objection_id": "approach:fresh",
                "type": "blocking",
                "status": "open",
            },
        ],
    }

    assert review._round_mail_counts(record) == (1, 1, 1)


def test_direction_ok_assigns_serial_tag_once(tmp_path, monkeypatch):
    _init_repo(tmp_path)
    _patch_reference(monkeypatch)

    def handler(repo, prompt, output_schema):
        kind = _schema_kind(output_schema)
        if kind == "aufheben":
            return _aufheben_payload("direction-ok"), 0
        axis = next(dimension.key for dimension in review.DIMENSIONS if f"axis: {dimension.key}" in prompt)
        return _axis_payload(axis), 0

    _install_codex_fake(monkeypatch, handler)

    first = review.run_patch_series_review(tmp_path, RFC_ID)
    second = review.run_patch_series_review(tmp_path, RFC_ID)

    assert first.serial == "0001"
    assert second.serial == "0001"
    message = _latest_commit_message(tmp_path)
    for axis in review.AXES:
        assert f"Reviewed-by: AI Org {axis} reviewer <{axis}-reviewer@ai-org.invalid>" in message
    tags = _git(tmp_path, "tag", "--list", "ai-org/serial/*").stdout.splitlines()
    assert tags == ["ai-org/serial/0001"]
    tag_target = _git(tmp_path, "rev-parse", "ai-org/serial/0001").stdout.strip()
    assert tag_target == first.history[0]["git_result_commit"]


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

    result = review.run_patch_series_review(tmp_path, RFC_ID)

    assert result.status == "needs_revision"
    assert [objection.axis for objection in result.unresolved] == ["approach"]
    assert "patch_series: needs-revision round 1" in _latest_commit_message(tmp_path)
    record = _committed_round_record(tmp_path, result)
    assert record["verdict"] == "needs_revision"
    assert len(record["objections"]) == 1


def test_requester_authority_blockers_convert_to_direction_ok_in_same_round(tmp_path, monkeypatch):
    _init_repo(tmp_path)
    _patch_reference(monkeypatch)
    requester_blocker = _objection(
        "scope",
        objection_id="scope:rights-boundary",
        claim="The rights/content-use boundary needs a requester product-policy decision.",
        impact="The author cannot decide whether recognizable product identity is acceptable.",
        action="re_explain",
        resolution_authority="requester",
    )

    def handler(repo, prompt, output_schema):
        kind = _schema_kind(output_schema)
        if kind == "aufheben":
            return _aufheben_payload("needs_revision", [requester_blocker]), 0
        axis = next(dimension.key for dimension in review.DIMENSIONS if f"axis: {dimension.key}" in prompt)
        return _axis_payload(axis, [requester_blocker] if axis == "scope" else []), 0

    _install_codex_fake(monkeypatch, handler)

    result = review.run_patch_series_review(tmp_path, RFC_ID)

    assert result.status == "direction-ok"
    assert result.unresolved == []
    assert "patch_series: direction-ok" in _latest_commit_message(tmp_path)
    cover = json.loads(_git(tmp_path, "show", f"{RFC_BRANCH}:patch-series-cover-letter.json").stdout)
    entries = [json.loads(item) for item in cover["constraints_assumptions"]]
    assert entries == [
        {
            "adopted_default": (
                "continue with the current explanation for selected approach candidate:anchored-review: "
                "The rights/content-use boundary needs a requester product-policy decision. "
                "Monitor requester impact: The author cannot decide whether recognizable product identity is acceptable. "
                "Requester may override this default."
            ),
            "default_provenance": "deterministic",
            "objection_id": "scope:rights-boundary",
            "question": (
                "The rights/content-use boundary needs a requester product-policy decision. "
                "Impact if wrong: The author cannot decide whether recognizable product identity is acceptable."
            ),
            "rationale": (
                "The objection is classified as requester-authority, so the author cannot settle it by technical revision. "
                "Requested author action was re_explain. Recording an explicit default preserves progress while keeping the requester question visible."
            ),
        }
    ]
    assert cover["open_questions"] == [
        (
            "scope:rights-boundary: The rights/content-use boundary needs a requester product-policy decision. "
            "Impact if wrong: The author cannot decide whether recognizable product identity is acceptable."
        )
    ]
    record = _committed_round_record(tmp_path, result)
    assert record["verdict"] == "direction-ok"
    assert record["objections"][0]["status"] == "assumption_recorded"
    assert record["requester_assumption_notes"] == entries


def test_requester_authority_only_nak_is_reevaluated_after_assumption_conversion(tmp_path, monkeypatch):
    _init_repo(tmp_path)
    _patch_reference(monkeypatch)
    requester_blocker = _objection(
        "scope",
        objection_id="scope:rights-boundary",
        claim="The requester must decide whether this product identity is allowed.",
        impact="The author cannot make the policy call.",
        action="re_explain",
        resolution_authority="requester",
    )

    def handler(repo, prompt, output_schema):
        kind = _schema_kind(output_schema)
        if kind == "aufheben":
            return _aufheben_payload("nak", [requester_blocker], nak_reason="Requester policy is unresolved."), 0
        axis = next(dimension.key for dimension in review.DIMENSIONS if f"axis: {dimension.key}" in prompt)
        return _axis_payload(axis, [requester_blocker] if axis == "scope" else []), 0

    _install_codex_fake(monkeypatch, handler)

    result = review.run_patch_series_review(tmp_path, RFC_ID)

    assert result.status == "direction-ok"
    record = _committed_round_record(tmp_path, result)
    assert record["verdict"] == "direction-ok"
    assert record["consolidation"]["verdict"] == "nak"
    assert record["consolidation_raw_verdict"] == "nak"
    assert record["objections"][0]["status"] == "assumption_recorded"
    assert record["requester_assumption_notes"][0]["default_provenance"] == "deterministic"


def test_author_authority_nak_still_terminates(tmp_path, monkeypatch):
    _init_repo(tmp_path)
    _patch_reference(monkeypatch)
    author_blocker = _objection(
        "approach",
        objection_id="approach:fundamental",
        claim="The selected direction cannot preserve the review boundary.",
        impact="The author would have to replace the direction.",
    )

    def handler(repo, prompt, output_schema):
        kind = _schema_kind(output_schema)
        if kind == "aufheben":
            return _aufheben_payload("nak", [author_blocker], nak_reason="Author-grounded direction failure."), 0
        axis = next(dimension.key for dimension in review.DIMENSIONS if f"axis: {dimension.key}" in prompt)
        return _axis_payload(axis, [author_blocker] if axis == "approach" else []), 0

    _install_codex_fake(monkeypatch, handler)

    result = review.run_patch_series_review(tmp_path, RFC_ID)

    assert result.status == "nak"
    record = _committed_round_record(tmp_path, result)
    assert record["verdict"] == "nak"
    assert record["consolidation"]["verdict"] == "nak"
    assert record["consolidation_raw_verdict"] == "nak"


def test_requester_authority_clarifications_and_suggestions_do_not_mutate_cover_letter(tmp_path, monkeypatch):
    _init_repo(tmp_path)
    _patch_reference(monkeypatch)
    clarification = _objection(
        "scope",
        objection_id="scope:clarify-policy",
        objection_type="clarification",
        claim="Clarify the requester policy boundary.",
        impact="The author can proceed while the question remains visible.",
        action="re_explain",
        resolution_authority="requester",
    )
    suggestion = _objection(
        "maintenance",
        objection_id="maintenance:budget-note",
        objection_type="nonblocking_suggestion",
        claim="Requester budget preference could be documented.",
        impact="The direction remains technically reviewable.",
        action="re_explain",
        resolution_authority="requester",
    )

    def handler(repo, prompt, output_schema):
        kind = _schema_kind(output_schema)
        if kind == "aufheben":
            return _aufheben_payload("needs_revision", [clarification, suggestion]), 0
        axis = next(dimension.key for dimension in review.DIMENSIONS if f"axis: {dimension.key}" in prompt)
        if axis == "scope":
            return _axis_payload(axis, [clarification]), 0
        if axis == "maintenance":
            return _axis_payload(axis, [suggestion]), 0
        return _axis_payload(axis, []), 0

    _install_codex_fake(monkeypatch, handler)

    result = review.run_patch_series_review(tmp_path, RFC_ID)

    assert result.status == "direction-ok"
    cover = json.loads(_git(tmp_path, "show", f"{RFC_BRANCH}:patch-series-cover-letter.json").stdout)
    assert cover["constraints_assumptions"] == []
    assert cover["open_questions"] == []
    record = _committed_round_record(tmp_path, result)
    assert record["requester_assumption_notes"] == []
    assert {item["objection_id"]: item["status"] for item in record["objections"]} == {
        "scope:clarify-policy": "open",
        "maintenance:budget-note": "open",
    }


def test_mixed_authority_round_converts_requester_and_keeps_only_author_blockers(tmp_path, monkeypatch):
    _init_repo(tmp_path)
    _patch_reference(monkeypatch)
    requester_blocker = _objection(
        "scope",
        objection_id="scope:rights-boundary",
        claim="The rights/content-use boundary needs a requester product-policy decision.",
        impact="The author cannot decide whether recognizable product identity is acceptable.",
        action="re_explain",
        resolution_authority="requester",
    )
    author_blocker = _objection(
        "approach",
        objection_id="approach:review-boundary",
        claim="The selected approach does not preserve the review boundary.",
        impact="Review can mutate the direction before the author reposts.",
    )

    def handler(repo, prompt, output_schema):
        kind = _schema_kind(output_schema)
        if kind == "aufheben":
            return _aufheben_payload("needs_revision", [requester_blocker, author_blocker]), 0
        axis = next(dimension.key for dimension in review.DIMENSIONS if f"axis: {dimension.key}" in prompt)
        if axis == "scope":
            return _axis_payload(axis, [requester_blocker]), 0
        if axis == "approach":
            return _axis_payload(axis, [author_blocker]), 0
        return _axis_payload(axis, []), 0

    _install_codex_fake(monkeypatch, handler)

    result = review.run_patch_series_review(tmp_path, RFC_ID)

    assert result.status == "needs_revision"
    assert [objection.objection_id for objection in result.unresolved] == ["approach:review-boundary"]
    assert "patch_series: needs-revision round 1" in _latest_commit_message(tmp_path)
    cover = json.loads(_git(tmp_path, "show", f"{RFC_BRANCH}:patch-series-cover-letter.json").stdout)
    entries = [json.loads(item) for item in cover["constraints_assumptions"]]
    assert entries[0]["objection_id"] == "scope:rights-boundary"
    assert cover["open_questions"] == [
        (
            "scope:rights-boundary: The rights/content-use boundary needs a requester product-policy decision. "
            "Impact if wrong: The author cannot decide whether recognizable product identity is acceptable."
        )
    ]
    record = _committed_round_record(tmp_path, result)
    assert record["verdict"] == "needs_revision"
    statuses = {item["objection_id"]: item["status"] for item in record["objections"]}
    assert statuses == {
        "scope:rights-boundary": "assumption_recorded",
        "approach:review-boundary": "open",
    }
    open_blocking = [
        item["objection_id"]
        for item in record["objections"]
        if item["type"] == "blocking" and item["status"] == "open"
    ]
    assert open_blocking == ["approach:review-boundary"]
    assert record["requester_assumption_notes"] == [
        {
            "adopted_default": (
                "continue with the current explanation for selected approach candidate:anchored-review: "
                "The rights/content-use boundary needs a requester product-policy decision. "
                "Monitor requester impact: The author cannot decide whether recognizable product identity is acceptable. "
                "Requester may override this default."
            ),
            "default_provenance": "deterministic",
            "objection_id": "scope:rights-boundary",
            "question": (
                "The rights/content-use boundary needs a requester product-policy decision. "
                "Impact if wrong: The author cannot decide whether recognizable product identity is acceptable."
            ),
            "rationale": (
                "The objection is classified as requester-authority, so the author cannot settle it by technical revision. "
                "Requested author action was re_explain. Recording an explicit default preserves progress while keeping the requester question visible."
            ),
        }
    ]


def test_terminal_nak_does_not_commit_requester_assumption_cover_letter_mutation(tmp_path, monkeypatch):
    _init_repo(tmp_path)
    _patch_reference(monkeypatch)
    requester_blocker = _objection(
        "scope",
        objection_id="scope:rights-boundary",
        claim="The rights/content-use boundary needs requester policy.",
        impact="The author cannot decide the product identity risk tolerance.",
        action="re_explain",
        resolution_authority="requester",
    )
    author_blocker = _objection(
        "compat",
        objection_id="compat:terminal",
        claim="The direction breaks the patch series branch contract.",
        impact="The branch cannot carry a coherent patch series.",
    )

    def handler(repo, prompt, output_schema):
        kind = _schema_kind(output_schema)
        if kind == "aufheben":
            return _aufheben_payload("nak", [requester_blocker, author_blocker], nak_reason="Author-grounded terminal failure."), 0
        axis = next(dimension.key for dimension in review.DIMENSIONS if f"axis: {dimension.key}" in prompt)
        if axis == "scope":
            return _axis_payload(axis, [requester_blocker]), 0
        if axis == "compat":
            return _axis_payload(axis, [author_blocker]), 0
        return _axis_payload(axis, []), 0

    _install_codex_fake(monkeypatch, handler)

    result = review.run_patch_series_review(tmp_path, RFC_ID)

    assert result.status == "nak"
    cover = json.loads(_git(tmp_path, "show", f"{RFC_BRANCH}:patch-series-cover-letter.json").stdout)
    assert cover["constraints_assumptions"] == []
    assert cover["open_questions"] == []
    changed_files = _git(tmp_path, "show", "--name-only", "--pretty=format:", f"{RFC_BRANCH}").stdout.splitlines()
    assert "patch-series-cover-letter.json" not in changed_files


def test_preseeded_requester_assumption_keeps_round_n_reencounter_direction_ok_without_duplicate(tmp_path, monkeypatch):
    _init_repo(tmp_path)
    _patch_reference(monkeypatch)
    requester_blocker = _objection(
        "scope",
        objection_id="scope:rights-boundary",
        claim="The rights/content-use boundary needs requester policy.",
        impact="The author cannot decide the product identity risk tolerance.",
        action="re_explain",
        resolution_authority="requester",
    )
    cover = _patch_series_view()
    cover, entries, changed = requester_assumptions.record_requester_authority_assumptions(
        cover,
        _approach_tree(),
        [requester_blocker],
    )
    assert changed is True
    _git(tmp_path, "checkout", RFC_BRANCH)
    (tmp_path / "patch-series-cover-letter.json").write_text(json.dumps(cover) + "\n", encoding="utf-8")
    _git(tmp_path, "add", "patch-series-cover-letter.json")
    _git(tmp_path, "commit", "-m", "seed requester assumption")
    _git(tmp_path, "checkout", "main")
    _write_round_record(tmp_path, 1, requester_blocker)

    def handler(repo, prompt, output_schema):
        kind = _schema_kind(output_schema)
        if kind == "aufheben":
            return _aufheben_payload("needs_revision", [requester_blocker]), 0
        axis = next(dimension.key for dimension in review.DIMENSIONS if f"axis: {dimension.key}" in prompt)
        return _axis_payload(axis, [requester_blocker] if axis == "scope" else []), 0

    _install_codex_fake(monkeypatch, handler)

    result = review.run_patch_series_review(tmp_path, RFC_ID)

    assert result.status == "direction-ok"
    assert result.rounds == 2
    latest_cover = json.loads(_git(tmp_path, "show", f"{RFC_BRANCH}:patch-series-cover-letter.json").stdout)
    assert [json.loads(item)["objection_id"] for item in latest_cover["constraints_assumptions"]] == [
        "scope:rights-boundary"
    ]
    record = _committed_round_record(tmp_path, result)
    assert record["verdict"] == "direction-ok"
    assert record["objections"][0]["status"] == "assumption_recorded"
    assert record["requester_assumption_notes"] == entries
    changed_files = _git(tmp_path, "show", "--name-only", "--pretty=format:", f"{RFC_BRANCH}").stdout.splitlines()
    assert "patch-series-cover-letter.json" not in changed_files


# --- Brief 32: the bounded-rounds cap is a terminal HEARING, not an instant nak ---
# Live disease (run 4, 2026-07-05): v6's reform had just revised the candidate node
# that round-5's objections anchored on, and the round-6 "review" nak'd in ZERO
# seconds at 19:15:02 with round-5's unresolved list verbatim - the author responded
# and was never heard. Byte-level proof (independent Codex diagnosis): the round-6
# record carried axis_reviews: [] and inputs.node_ids: [], and its objections hashed
# IDENTICAL to round-5's open blocking (33c5f41f5dcb...). The cap bounds rounds; it
# does not replace judgment. Note: the deleted test at this spot
# (test_bounded_rounds_escalates_to_nak_without_codex) monkeypatched _run_codex to
# FAIL if called and expected the instant nak - it pinned the disease as the spec,
# the same pattern brief 26 found. These tests pin the hearing instead.


def test_cap_reached_runs_a_real_terminal_review_and_nak_carries_terminal_judgment(tmp_path, monkeypatch):
    _init_repo(tmp_path)
    _patch_reference(monkeypatch)
    monkeypatch.setenv("AI_ORG_RFC_MAX_REVIEW_ROUNDS", "2")
    stale_blocker = _objection("approach", objection_id="approach:stale-from-round-2")
    _write_round_record(tmp_path, 1, stale_blocker)
    _write_round_record(tmp_path, 2, stale_blocker)
    fresh_blocker = _objection(
        "compat",
        objection_id="compat:terminal-fresh",
        claim="The terminal tree still breaks the branch contract.",
        impact="The series cannot land as directed.",
    )
    codex_calls: list[str] = []

    def handler(repo, prompt, output_schema):
        kind = _schema_kind(output_schema)
        codex_calls.append(kind)
        if kind == "aufheben":
            return _aufheben_payload("needs_revision", [fresh_blocker]), 0
        axis = next(dimension.key for dimension in review.DIMENSIONS if f"axis: {dimension.key}" in prompt)
        return _axis_payload(axis, [fresh_blocker] if axis == "compat" else []), 0

    _install_codex_fake(monkeypatch, handler)

    result = review.run_patch_series_review(tmp_path, RFC_ID)

    # The current tree WAS reviewed (the zero-second stale-list nak is gone).
    assert codex_calls, "terminal round must run a real review"
    assert result.status == "nak"
    assert "bounded-rounds backstop" in result.escalation_reason
    assert "terminal-round hearing" in result.escalation_reason
    record = _committed_round_record(tmp_path, result)
    assert record["round"] == 3
    assert record["bounded_rounds_backstop"]["max_rounds"] == 2
    assert record["bounded_rounds_backstop"]["terminal_round_reviewed"] is True
    # The nak carries the TERMINAL round's actual judgment, not round-2's list.
    objection_ids = [item["objection_id"] for item in record["objections"]]
    assert objection_ids == ["compat:terminal-fresh"]
    assert record["axis_reviews"]  # real axis reviews attached
    # The raw consolidation verdict is preserved: the conversion is the cap's,
    # not a fabricated reviewer nak.
    assert record["consolidation_raw_verdict"] == "needs_revision"
    assert "patch_series: nak" in _latest_commit_message(tmp_path)


def test_terminal_round_direction_ok_converges_at_exactly_the_cap(tmp_path, monkeypatch):
    # E2E of the justice fix: a tree whose author fixed everything converges at
    # the cap instead of dying on the previous round's stale list.
    _init_repo(tmp_path)
    _patch_reference(monkeypatch)
    monkeypatch.setenv("AI_ORG_RFC_MAX_REVIEW_ROUNDS", "2")
    stale_blocker = _objection("approach", objection_id="approach:stale-from-round-2")
    _write_round_record(tmp_path, 1, stale_blocker)
    _write_round_record(tmp_path, 2, stale_blocker)

    def handler(repo, prompt, output_schema):
        kind = _schema_kind(output_schema)
        if kind == "aufheben":
            return _aufheben_payload("direction-ok", []), 0
        axis = next(dimension.key for dimension in review.DIMENSIONS if f"axis: {dimension.key}" in prompt)
        return _axis_payload(axis, []), 0

    _install_codex_fake(monkeypatch, handler)

    result = review.run_patch_series_review(tmp_path, RFC_ID)

    assert result.status == "direction-ok"
    record = _committed_round_record(tmp_path, result)
    assert record["round"] == 3
    assert record["verdict"] == "direction-ok"
    assert "bounded_rounds_backstop" not in record
    assert "patch_series: direction-ok" in _latest_commit_message(tmp_path)
    assert result.serial


def test_no_reform_selectable_after_terminal_review_nak(tmp_path, monkeypatch):
    # Boundedness is structural: the terminal nak writes the terminal marker,
    # the pull selector stops, so at most cap+1 reviews and cap reforms run.
    import ai_org.patchwork_queue as queue_module

    _init_repo(tmp_path)
    _patch_reference(monkeypatch)
    monkeypatch.setenv("AI_ORG_RFC_MAX_REVIEW_ROUNDS", "2")
    blocker = _objection("approach", objection_id="approach:stale-from-round-2")
    _write_round_record(tmp_path, 1, blocker)
    _write_round_record(tmp_path, 2, blocker)

    def handler(repo, prompt, output_schema):
        kind = _schema_kind(output_schema)
        if kind == "aufheben":
            return _aufheben_payload("needs_revision", [blocker]), 0
        axis = next(dimension.key for dimension in review.DIMENSIONS if f"axis: {dimension.key}" in prompt)
        return _axis_payload(axis, [blocker] if axis == "approach" else []), 0

    _install_codex_fake(monkeypatch, handler)

    result = review.run_patch_series_review(tmp_path, RFC_ID)

    assert result.status == "nak"
    assert queue_module._is_terminal_patch_series(tmp_path, RFC_BRANCH) is True


def test_genuine_nak_at_the_cap_keeps_its_own_reason(tmp_path, monkeypatch):
    # The 03a7319 genuine-nak escape stays closed: an evidenced consolidation
    # nak at the terminal round is the reviewer's judgment, not the backstop's.
    _init_repo(tmp_path)
    _patch_reference(monkeypatch)
    monkeypatch.setenv("AI_ORG_RFC_MAX_REVIEW_ROUNDS", "2")
    stale_blocker = _objection("approach", objection_id="approach:stale-from-round-2")
    _write_round_record(tmp_path, 1, stale_blocker)
    _write_round_record(tmp_path, 2, stale_blocker)
    blocking = _objection("compat", objection_id="compat:fundamental", claim="The direction breaks the patch series branch contract.")
    reason = "The direction is fundamentally incompatible with the patch series branch contract."

    def handler(repo, prompt, output_schema):
        kind = _schema_kind(output_schema)
        if kind == "aufheben":
            return _aufheben_payload("nak", [blocking], nak_reason=reason), 0
        axis = next(dimension.key for dimension in review.DIMENSIONS if f"axis: {dimension.key}" in prompt)
        return _axis_payload(axis, [blocking] if axis == "compat" else []), 0

    _install_codex_fake(monkeypatch, handler)

    result = review.run_patch_series_review(tmp_path, RFC_ID)

    assert result.status == "nak"
    assert result.escalation_reason == reason
    record = _committed_round_record(tmp_path, result)
    assert "bounded_rounds_backstop" not in record
    assert "bounded-rounds backstop" not in record["consolidation"]["nak_reason"]


def test_nak_path_is_evidenced_decision_record(tmp_path, monkeypatch):
    _init_repo(tmp_path)
    _patch_reference(monkeypatch)
    blocking = _objection("compat", claim="The direction breaks the patch series branch contract.")
    reason = "The direction is fundamentally incompatible with the patch series branch contract."

    def handler(repo, prompt, output_schema):
        kind = _schema_kind(output_schema)
        if kind == "aufheben":
            return _aufheben_payload("nak", [blocking], nak_reason=reason), 0
        axis = next(dimension.key for dimension in review.DIMENSIONS if f"axis: {dimension.key}" in prompt)
        return _axis_payload(axis, [blocking] if axis == "compat" else []), 0

    _install_codex_fake(monkeypatch, handler)

    result = review.run_patch_series_review(tmp_path, RFC_ID)

    assert result.status == "nak"
    assert result.escalation_reason == reason
    assert "patch_series: nak" in _latest_commit_message(tmp_path)
    record = _committed_round_record(tmp_path, result)
    assert record["consolidation_raw_verdict"] == "nak"
    assert record["consolidation"]["verdict"] == "nak"
    assert record["consolidation"]["nak_reason"] == reason
    assert record["consolidation"]["evidence"]


def test_aufheben_schema_no_longer_returns_revised_patch_series():
    serialized = json.dumps(review.AUFHEBEN_SCHEMA)
    assert "revised_patch_series" not in serialized
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
