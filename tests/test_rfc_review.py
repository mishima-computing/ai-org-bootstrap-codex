from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_org.rfc import review
from ai_org.rfc.receive import RFC


def _rfc() -> RFC:
    return RFC(
        title="Manual RFC",
        problem="The workflow needs real RFC review.",
        proposed_change="Run five dimension reviewers and consolidate objections.",
        interface_sketch="run_rfc_review(rfc, repo)",
        notes="Keep the target repo read-only during review.",
    )


def _revised_rfc(suffix: str = "") -> dict[str, str]:
    return {
        "title": f"Revised Manual RFC{suffix}",
        "problem": f"The RFC review workflow needs structured convergence{suffix}.",
        "proposed_change": f"Run five reviewers, then synthesize into a revised RFC{suffix}.",
        "interface_sketch": f"run_rfc_review(rfc, repo) -> ReviewResult{suffix}",
        "notes": f"Keep all carrier calls read-only and schema-backed{suffix}.",
    }


def _aufheben_response(verdict: str, revised_rfc: dict[str, str], **extra) -> str:
    payload = {
        "verdict": verdict,
        "revised_rfc": revised_rfc,
        "situation_read": "Synthesized reviewer objections into one RFC direction.",
        **extra,
    }
    return json.dumps(payload)


def _schema_kind(output_schema: str | Path) -> str:
    schema = json.loads(Path(output_schema).read_text(encoding="utf-8"))
    if schema["required"] == ["has_objection", "detail"]:
        return "reviewer"
    if schema["required"] == ["verdict", "revised_rfc", "situation_read"]:
        return "aufheben"
    raise AssertionError(f"unexpected schema: {schema}")


def test_all_reviewers_clear_direction_ok_in_one_round(tmp_path, monkeypatch):
    calls = []

    def fake_run_codex(repo, prompt, sandbox, *, out_file, output_schema=None, **kwargs):
        assert output_schema is not None
        assert _schema_kind(output_schema) == "reviewer"
        calls.append(
            {
                "repo": Path(repo),
                "prompt": prompt,
                "sandbox": sandbox,
                "out_file": Path(out_file),
                "output_schema": None if output_schema is None else Path(output_schema),
            }
        )
        return {
            "ok": True,
            "session_id": "reviewer",
            "last_message": json.dumps({"has_objection": False, "detail": "No objection."}),
            "events": 1,
        }

    monkeypatch.setattr(review.carrier, "run_codex", fake_run_codex)

    result = review.run_rfc_review(_rfc(), tmp_path)

    assert result.status == "direction-ok"
    assert result.rounds == 1
    assert result.final_view == ""
    assert result.resolved == [dim.key for dim in review.DIMENSIONS]
    assert result.unresolved == []
    assert len(calls) == 5
    assert all(call["sandbox"] == "read-only" for call in calls)
    assert all(call["repo"] == tmp_path for call in calls)
    assert all(call["output_schema"] is not None for call in calls)


def test_aufheben_proceed_revised_rfc_feeds_next_review_round(tmp_path, monkeypatch):
    revised = _revised_rfc()
    reviewer_prompts = []
    reviewer_calls = 0
    aufheben_calls = 0

    def fake_run_codex(repo, prompt, sandbox, *, out_file, output_schema=None, **kwargs):
        nonlocal reviewer_calls, aufheben_calls
        assert sandbox == "read-only"
        assert output_schema is not None
        kind = _schema_kind(output_schema)
        if kind == "aufheben":
            aufheben_calls += 1
            return {
                "ok": True,
                "session_id": "aufheben",
                "last_message": _aufheben_response("proceed", revised),
                "events": 1,
            }

        reviewer_prompts.append(prompt)
        dim = review.DIMENSIONS[reviewer_calls % len(review.DIMENSIONS)].key
        round_index = reviewer_calls // len(review.DIMENSIONS)
        reviewer_calls += 1
        has_objection = round_index == 0 and dim == "approach"
        return {
            "ok": True,
            "session_id": f"review-{dim}",
            "last_message": json.dumps(
                {
                    "has_objection": has_objection,
                    "detail": f"{dim} {'objects' if has_objection else 'is clear'}",
                }
            ),
            "events": 1,
        }

    monkeypatch.setattr(review.carrier, "run_codex", fake_run_codex)

    result = review.run_rfc_review(_rfc(), tmp_path)

    assert result.status == "direction-ok"
    assert result.rounds == 2
    assert result.final_view == revised
    assert aufheben_calls == 1
    assert reviewer_calls == 2 * len(review.DIMENSIONS)
    second_round_prompts = reviewer_prompts[len(review.DIMENSIONS):]
    assert len(second_round_prompts) == len(review.DIMENSIONS)
    for prompt in second_round_prompts:
        assert "Current structured revised RFC to re-critique" in prompt
        for value in revised.values():
            assert value in prompt
    assert result.history[0]["aufheben"]["verdict"] == "proceed"
    assert result.history[0]["aufheben"]["situation_read"]


def test_aufheben_escalate_naks_immediately(tmp_path, monkeypatch):
    reviewer_calls = 0
    aufheben_calls = 0
    reason = "need and compatibility objections cannot both be satisfied"

    def fake_run_codex(repo, prompt, sandbox, *, out_file, output_schema=None, **kwargs):
        nonlocal reviewer_calls, aufheben_calls
        assert sandbox == "read-only"
        assert output_schema is not None
        kind = _schema_kind(output_schema)
        if kind == "aufheben":
            aufheben_calls += 1
            return {
                "ok": True,
                "session_id": "aufheben",
                "last_message": _aufheben_response(
                    "escalate",
                    _revised_rfc(" escalation"),
                    escalation_reason=reason,
                ),
                "events": 1,
            }

        dim = review.DIMENSIONS[reviewer_calls % len(review.DIMENSIONS)].key
        reviewer_calls += 1
        return {
            "ok": True,
            "session_id": f"review-{dim}",
            "last_message": json.dumps(
                {"has_objection": dim == "compat", "detail": f"{dim} review detail"}
            ),
            "events": 1,
        }

    monkeypatch.setattr(review.carrier, "run_codex", fake_run_codex)

    result = review.run_rfc_review(_rfc(), tmp_path)

    assert result.status == "nak"
    assert result.rounds == 1
    assert result.rounds < review.CAP
    assert result.escalation_reason == reason
    assert [objection.dimension for objection in result.unresolved] == ["compat"]
    assert aufheben_calls == 1
    assert reviewer_calls == len(review.DIMENSIONS)
    assert result.history[0]["aufheben"]["verdict"] == "escalate"


def test_garbled_aufheben_json_fail_closed_nak(tmp_path, monkeypatch):
    reviewer_calls = 0

    def fake_run_codex(repo, prompt, sandbox, *, out_file, output_schema=None, **kwargs):
        nonlocal reviewer_calls
        assert sandbox == "read-only"
        assert output_schema is not None
        kind = _schema_kind(output_schema)
        if kind == "aufheben":
            return {
                "ok": True,
                "session_id": "aufheben",
                "last_message": "not json",
                "events": 1,
            }

        dim = review.DIMENSIONS[reviewer_calls % len(review.DIMENSIONS)].key
        reviewer_calls += 1
        return {
            "ok": True,
            "session_id": f"review-{dim}",
            "last_message": json.dumps(
                {"has_objection": dim == "need", "detail": f"{dim} review detail"}
            ),
            "events": 1,
        }

    monkeypatch.setattr(review.carrier, "run_codex", fake_run_codex)

    result = review.run_rfc_review(_rfc(), tmp_path)

    assert result.status == "nak"
    assert result.rounds == 1
    assert "Aufheben returned invalid JSON" in result.escalation_reason
    assert [objection.dimension for objection in result.unresolved] == ["need"]
    assert result.history[0]["aufheben"]["verdict"] == "escalate"


def test_persistent_objection_naks_after_cap_and_consolidates_each_round(tmp_path, monkeypatch):
    reviewer_calls = 0
    aufheben_calls = 0

    def fake_run_codex(repo, prompt, sandbox, *, out_file, output_schema=None, **kwargs):
        nonlocal reviewer_calls, aufheben_calls
        assert sandbox == "read-only"
        assert output_schema is not None
        kind = _schema_kind(output_schema)
        if kind == "aufheben":
            aufheben_calls += 1
            return {
                "ok": True,
                "session_id": "aufheben",
                "last_message": _aufheben_response("proceed", _revised_rfc(f" {aufheben_calls}")),
                "events": 1,
            }

        dim = review.DIMENSIONS[reviewer_calls % len(review.DIMENSIONS)].key
        reviewer_calls += 1
        has_objection = dim == "approach"
        return {
            "ok": True,
            "session_id": f"review-{dim}",
            "last_message": json.dumps(
                {
                    "has_objection": has_objection,
                    "detail": f"{dim} {'still objects' if has_objection else 'is resolved'}",
                }
            ),
            "events": 1,
        }

    monkeypatch.setattr(review.carrier, "run_codex", fake_run_codex)

    result = review.run_rfc_review(_rfc(), tmp_path)

    assert result.status == "nak"
    assert result.rounds == review.CAP
    assert result.final_view == _revised_rfc(f" {review.CAP}")
    assert result.resolved == ["need", "compat", "scope", "maintenance"]
    assert [objection.dimension for objection in result.unresolved] == ["approach"]
    assert aufheben_calls == review.CAP
    assert reviewer_calls == review.CAP * len(review.DIMENSIONS)


@pytest.mark.parametrize(
    ("carrier_result", "expected_detail"),
    [
        (
            {"ok": False, "session_id": None, "last_message": "process failed", "events": 0},
            "Codex review failed for need",
        ),
        (
            {"ok": True, "session_id": "bad-json", "last_message": "not json", "events": 1},
            "returned invalid JSON",
        ),
    ],
)
def test_failed_or_garbled_reviewer_output_is_an_objection(
    tmp_path,
    monkeypatch,
    carrier_result,
    expected_detail,
):
    def fake_run_codex(repo, prompt, sandbox, *, out_file, output_schema=None, **kwargs):
        return carrier_result

    monkeypatch.setattr(review.carrier, "run_codex", fake_run_codex)

    objection = review._review_one(review.DIMENSIONS[0], _rfc(), tmp_path, None)

    assert objection.dimension == "need"
    assert objection.has_objection is True
    assert expected_detail in objection.detail


def test_reviewers_use_read_only_sandbox_and_output_schema(tmp_path, monkeypatch):
    calls = []

    def fake_run_codex(repo, prompt, sandbox, *, out_file, output_schema=None, **kwargs):
        calls.append({"sandbox": sandbox, "output_schema": output_schema, "prompt": prompt})
        return {
            "ok": True,
            "session_id": "reviewer",
            "last_message": json.dumps({"has_objection": False, "detail": ""}),
            "events": 1,
        }

    monkeypatch.setattr(review.carrier, "run_codex", fake_run_codex)

    review._review_one(review.DIMENSIONS[2], _rfc(), tmp_path, _revised_rfc(" current"))

    assert len(calls) == 1
    assert calls[0]["sandbox"] == "read-only"
    assert calls[0]["output_schema"] is not None
    assert "compat" in calls[0]["prompt"]
    assert "Revised Manual RFC current" in calls[0]["prompt"]
