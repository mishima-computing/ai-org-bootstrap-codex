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


def test_all_reviewers_clear_direction_ok_in_one_round(tmp_path, monkeypatch):
    calls = []

    def fake_run_codex(repo, prompt, sandbox, *, out_file, output_schema=None, **kwargs):
        calls.append(
            {
                "repo": Path(repo),
                "prompt": prompt,
                "sandbox": sandbox,
                "out_file": Path(out_file),
                "output_schema": None if output_schema is None else Path(output_schema),
            }
        )
        assert output_schema is not None
        schema = json.loads(Path(output_schema).read_text(encoding="utf-8"))
        assert schema["required"] == ["has_objection", "detail"]
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


def test_persistent_objection_naks_after_cap_and_consolidates_each_round(tmp_path, monkeypatch):
    reviewer_calls = 0
    aufheben_calls = 0

    def fake_run_codex(repo, prompt, sandbox, *, out_file, output_schema=None, **kwargs):
        nonlocal reviewer_calls, aufheben_calls
        assert sandbox == "read-only"
        if output_schema is None:
            aufheben_calls += 1
            return {
                "ok": True,
                "session_id": "aufheben",
                "last_message": f"consolidated view {aufheben_calls}",
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
    assert result.final_view == f"consolidated view {review.CAP}"
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

    review._review_one(review.DIMENSIONS[2], _rfc(), tmp_path, "current direction")

    assert len(calls) == 1
    assert calls[0]["sandbox"] == "read-only"
    assert calls[0]["output_schema"] is not None
    assert "compat" in calls[0]["prompt"]
    assert "current direction" in calls[0]["prompt"]
