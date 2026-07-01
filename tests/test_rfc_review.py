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
        "proposal_hint": "Run five dimension reviewers and consolidate objections.",
        "alternatives_considered": ["Keep loading RFCs directly."],
    }


def _revised_rfc(suffix: str = "") -> dict[str, object]:
    revised = _rfc_view()
    revised.update(
        {
            "working_title": f"Revised Manual RFC{suffix}",
            "problem_or_motivation": f"The RFC review workflow needs structured convergence{suffix}.",
            "intended_users_or_jobs": f"Contributors and maintainers{suffix}.",
            "desired_outcomes_success": f"Review state stays schema-backed{suffix}.",
            "background_facts": f"Keep all codex calls read-only and schema-backed{suffix}.",
            "proposal_hint": f"Run five reviewers, then synthesize into a revised RFC{suffix}.",
            "alternatives_considered": [f"Skip consolidation and accept reviewer drift{suffix}."],
        }
    )
    return revised


def _aufheben_response(verdict: str, revised_rfc: dict[str, object], **extra) -> str:
    payload = {
        "verdict": verdict,
        "revised_rfc": revised_rfc,
        "situation_read": "Synthesized reviewer objections into one RFC direction.",
        **extra,
    }
    return json.dumps(payload)


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _init_repo(repo: Path, rfc: dict[str, object] | None = None) -> None:
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True, text=True)
    _git(repo, "config", "user.name", "RFC Test")
    _git(repo, "config", "user.email", "rfc-test@example.invalid")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "base")
    _git(repo, "branch", "-M", "main")
    _git(repo, "checkout", "-B", RFC_BRANCH, "main")
    (repo / "rfc.json").write_text(json.dumps(rfc or _rfc_view()) + "\n", encoding="utf-8")
    _git(repo, "add", "rfc.json")
    _git(repo, "commit", "-m", "initial rfc")
    _git(repo, "checkout", "main")


def _latest_commit_message(repo: Path) -> str:
    return _git(repo, "log", "-1", "--pretty=%B").stdout


def _commit_count(repo: Path, ref: str = "HEAD") -> int:
    return int(_git(repo, "rev-list", "--count", ref).stdout.strip())


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
            out_file.write_text(payload, encoding="utf-8")
            return subprocess.CompletedProcess(cmd, returncode, stdout="", stderr="")
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(review.subprocess, "run", fake_run)


def test_all_reviewers_clear_direction_ok_in_one_round(tmp_path, monkeypatch):
    _init_repo(tmp_path)
    calls = []

    def handler(repo, prompt, output_schema):
        assert _schema_kind(output_schema) == "reviewer"
        calls.append({"repo": repo, "prompt": prompt, "output_schema": output_schema})
        return json.dumps({"has_objection": False, "detail": "No objection."}), 0

    _install_codex_fake(monkeypatch, handler)

    before = _commit_count(tmp_path, RFC_BRANCH)
    result = review.run_rfc_review(tmp_path, RFC_ID)

    assert result.status == "direction-ok"
    assert result.rounds == 1
    assert result.final_view == _rfc_view()
    assert result.resolved == [dim.key for dim in review.DIMENSIONS]
    assert result.unresolved == []
    assert len(calls) == 5
    assert all(call["repo"] == tmp_path for call in calls)
    assert _commit_count(tmp_path, RFC_BRANCH) == before + 1
    assert _latest_commit_message(tmp_path).startswith("rfc: direction-ok (1 rounds)")


def test_aufheben_proceed_revised_rfc_feeds_next_review_round(tmp_path, monkeypatch):
    _init_repo(tmp_path)
    revised = _revised_rfc()
    reviewer_prompts = []
    reviewer_calls = 0
    aufheben_calls = 0

    def handler(repo, prompt, output_schema):
        nonlocal reviewer_calls, aufheben_calls
        kind = _schema_kind(output_schema)
        if kind == "aufheben":
            aufheben_calls += 1
            return _aufheben_response("proceed", revised), 0

        reviewer_prompts.append(prompt)
        dim = review.DIMENSIONS[reviewer_calls % len(review.DIMENSIONS)].key
        round_index = reviewer_calls // len(review.DIMENSIONS)
        reviewer_calls += 1
        has_objection = round_index == 0 and dim == "approach"
        return (
            json.dumps(
                {
                    "has_objection": has_objection,
                    "detail": f"{dim} {'objects' if has_objection else 'is clear'}",
                }
            ),
            0,
        )

    _install_codex_fake(monkeypatch, handler)

    result = review.run_rfc_review(tmp_path, RFC_ID)

    assert result.status == "direction-ok"
    assert result.rounds == 2
    assert result.final_view == revised
    assert json.loads(_git(tmp_path, "show", f"{RFC_BRANCH}:rfc.json").stdout) == revised
    assert _latest_commit_message(tmp_path).startswith("rfc: direction-ok (2 rounds)")
    assert aufheben_calls == 1
    assert reviewer_calls == 2 * len(review.DIMENSIONS)
    second_round_prompts = reviewer_prompts[len(review.DIMENSIONS):]
    assert len(second_round_prompts) == len(review.DIMENSIONS)
    for prompt in second_round_prompts:
        assert "Current structured revised RFC to re-critique" in prompt
        for field, value in revised.items():
            if isinstance(value, list):
                for item in value:
                    assert item in prompt
            elif isinstance(value, dict):
                assert json.dumps(value, sort_keys=True, ensure_ascii=True) in prompt
            else:
                assert value in prompt
    assert result.history[0]["aufheben"]["verdict"] == "proceed"
    assert result.history[0]["aufheben"]["situation_read"]


def test_aufheben_escalate_naks_immediately(tmp_path, monkeypatch):
    _init_repo(tmp_path)
    reviewer_calls = 0
    aufheben_calls = 0
    reason = "need and compatibility objections cannot both be satisfied"

    def handler(repo, prompt, output_schema):
        nonlocal reviewer_calls, aufheben_calls
        kind = _schema_kind(output_schema)
        if kind == "aufheben":
            aufheben_calls += 1
            return (
                _aufheben_response(
                    "escalate",
                    _revised_rfc(" escalation"),
                    escalation_reason=reason,
                ),
                0,
            )

        dim = review.DIMENSIONS[reviewer_calls % len(review.DIMENSIONS)].key
        reviewer_calls += 1
        return json.dumps({"has_objection": dim == "compat", "detail": f"{dim} review detail"}), 0

    _install_codex_fake(monkeypatch, handler)

    result = review.run_rfc_review(tmp_path, RFC_ID)

    assert result.status == "nak"
    assert result.rounds == 1
    assert result.rounds < review.CAP
    assert result.escalation_reason == reason
    assert [objection.dimension for objection in result.unresolved] == ["compat"]
    assert aufheben_calls == 1
    assert reviewer_calls == len(review.DIMENSIONS)
    assert result.history[0]["aufheben"]["verdict"] == "escalate"
    message = _latest_commit_message(tmp_path)
    assert message.startswith("rfc: nak (1 rounds)")
    assert "unresolved: compat" in message


def test_garbled_aufheben_json_fail_closed_nak(tmp_path, monkeypatch):
    _init_repo(tmp_path)
    reviewer_calls = 0

    def handler(repo, prompt, output_schema):
        nonlocal reviewer_calls
        kind = _schema_kind(output_schema)
        if kind == "aufheben":
            return "not json", 0

        dim = review.DIMENSIONS[reviewer_calls % len(review.DIMENSIONS)].key
        reviewer_calls += 1
        return json.dumps({"has_objection": dim == "need", "detail": f"{dim} review detail"}), 0

    _install_codex_fake(monkeypatch, handler)

    result = review.run_rfc_review(tmp_path, RFC_ID)

    assert result.status == "nak"
    assert result.rounds == 1
    assert "Aufheben returned invalid JSON" in result.escalation_reason
    assert [objection.dimension for objection in result.unresolved] == ["need"]
    assert result.history[0]["aufheben"]["verdict"] == "escalate"
    message = _latest_commit_message(tmp_path)
    assert message.startswith("rfc: nak (1 rounds)")
    assert "unresolved: need" in message


def test_persistent_objection_naks_after_cap_and_consolidates_each_round(tmp_path, monkeypatch):
    _init_repo(tmp_path)
    reviewer_calls = 0
    aufheben_calls = 0

    def handler(repo, prompt, output_schema):
        nonlocal reviewer_calls, aufheben_calls
        kind = _schema_kind(output_schema)
        if kind == "aufheben":
            aufheben_calls += 1
            return _aufheben_response("proceed", _revised_rfc(f" {aufheben_calls}")), 0

        dim = review.DIMENSIONS[reviewer_calls % len(review.DIMENSIONS)].key
        reviewer_calls += 1
        has_objection = dim == "approach"
        return (
            json.dumps(
                {
                    "has_objection": has_objection,
                    "detail": f"{dim} {'still objects' if has_objection else 'is resolved'}",
                }
            ),
            0,
        )

    _install_codex_fake(monkeypatch, handler)

    result = review.run_rfc_review(tmp_path, RFC_ID)

    assert result.status == "nak"
    assert result.rounds == review.CAP
    assert result.final_view == _revised_rfc(f" {review.CAP}")
    assert result.resolved == ["need", "compat", "scope", "maintenance"]
    assert [objection.dimension for objection in result.unresolved] == ["approach"]
    assert aufheben_calls == review.CAP
    assert reviewer_calls == review.CAP * len(review.DIMENSIONS)
    message = _latest_commit_message(tmp_path)
    assert message.startswith(f"rfc: nak ({review.CAP} rounds)")
    assert "unresolved: approach" in message


@pytest.mark.parametrize(
    ("payload", "returncode", "expected_detail"),
    [
        ("process failed", 1, "Codex review failed for need"),
        ("not json", 0, "returned invalid JSON"),
    ],
)
def test_failed_or_garbled_reviewer_output_is_an_objection(
    tmp_path,
    monkeypatch,
    payload,
    returncode,
    expected_detail,
):
    _init_repo(tmp_path)

    def handler(repo, prompt, output_schema):
        return payload, returncode

    _install_codex_fake(monkeypatch, handler)

    objection = review._review_one(review.DIMENSIONS[0], _rfc_view(), tmp_path, None)

    assert objection.dimension == "need"
    assert objection.has_objection is True
    assert expected_detail in objection.detail


def test_reviewers_use_read_only_sandbox_and_output_schema(tmp_path, monkeypatch):
    _init_repo(tmp_path)
    calls = []

    def handler(repo, prompt, output_schema):
        calls.append({"repo": repo, "output_schema": output_schema, "prompt": prompt})
        return json.dumps({"has_objection": False, "detail": ""}), 0

    _install_codex_fake(monkeypatch, handler)

    review._review_one(review.DIMENSIONS[2], _rfc_view(), tmp_path, _revised_rfc(" current"))

    assert len(calls) == 1
    assert calls[0]["repo"] == tmp_path
    assert calls[0]["output_schema"] is not None
    assert "compat" in calls[0]["prompt"]
    assert "Revised Manual RFC current" in calls[0]["prompt"]


def test_rfc_schemas_use_registry_and_codex_valid_required_properties():
    schema = review.AUFHEBEN_SCHEMA
    serialized = json.dumps(schema)
    assert "allOf" not in serialized
    assert "anyOf" not in serialized
    assert "oneOf" not in serialized
    assert schema["additionalProperties"] is False
    assert sorted(schema["required"]) == sorted(schema["properties"])

    schema_rfc = schema["properties"]["revised_rfc"]
    assert schema_rfc["additionalProperties"] is False
    assert tuple(schema_rfc["required"]) == review.RFC_VIEW_FIELDS
    assert sorted(schema_rfc["required"]) == sorted(schema_rfc["properties"])
    assert schema_rfc["properties"]["tech_stack"]["type"] == "object"
    # Registry semantics reach the codex schema as a STRING description; the structured dict
    # form is prompt-only (codex/OpenAI reject a non-string `description` with HTTP 400).
    provenance_desc = schema_rfc["properties"]["grounding_provenance"]["description"]
    assert isinstance(provenance_desc, str)
    assert "must_not=content consumed downstream as product requirement nouns" in provenance_desc


def test_missing_rfc_on_rfc_branch_fail_closed_nak(tmp_path):
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True, text=True)
    _git(tmp_path, "config", "user.name", "RFC Test")
    _git(tmp_path, "config", "user.email", "rfc-test@example.invalid")
    (tmp_path / "README.md").write_text("empty\n", encoding="utf-8")
    _git(tmp_path, "add", "README.md")
    _git(tmp_path, "commit", "-m", "initial")
    _git(tmp_path, "branch", "-M", "main")
    _git(tmp_path, "checkout", "-B", RFC_BRANCH, "main")
    _git(tmp_path, "checkout", "main")

    result = review.run_rfc_review(tmp_path, RFC_ID)

    assert result.status == "nak"
    assert result.rounds == 0
    assert [objection.dimension for objection in result.unresolved] == ["rfc-read"]
    message = _latest_commit_message(tmp_path)
    assert message.startswith("rfc: nak (0 rounds)")
    assert "unresolved: rfc-read" in message
