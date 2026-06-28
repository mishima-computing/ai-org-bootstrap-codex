from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from ai_org.rfc import review


def _rfc_view() -> dict[str, str]:
    return {
        "title": "Manual RFC",
        "problem": "The workflow needs real RFC review.",
        "proposed_change": "Run five dimension reviewers and consolidate objections.",
        "interface_sketch": "run_rfc_review(repo)",
        "notes": "Keep the target repo read-only during review.",
    }


def _revised_rfc(suffix: str = "") -> dict[str, str]:
    return {
        "title": f"Revised Manual RFC{suffix}",
        "problem": f"The RFC review workflow needs structured convergence{suffix}.",
        "proposed_change": f"Run five reviewers, then synthesize into a revised RFC{suffix}.",
        "interface_sketch": f"run_rfc_review(repo) -> ReviewResult{suffix}",
        "notes": f"Keep all codex calls read-only and schema-backed{suffix}.",
    }


def _aufheben_response(verdict: str, revised_rfc: dict[str, str], **extra) -> str:
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


def _init_repo(repo: Path, rfc: dict[str, str] | None = None) -> None:
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True, text=True)
    _git(repo, "config", "user.name", "RFC Test")
    _git(repo, "config", "user.email", "rfc-test@example.invalid")
    (repo / "rfc.json").write_text(json.dumps(rfc or _rfc_view()) + "\n", encoding="utf-8")
    _git(repo, "add", "rfc.json")
    _git(repo, "commit", "-m", "initial rfc")


def _latest_commit_message(repo: Path) -> str:
    return _git(repo, "log", "-1", "--pretty=%B").stdout


def _commit_count(repo: Path) -> int:
    return int(_git(repo, "rev-list", "--count", "HEAD").stdout.strip())


def _schema_kind(output_schema: str | Path) -> str:
    schema = json.loads(Path(output_schema).read_text(encoding="utf-8"))
    if schema["required"] == ["has_objection", "detail"]:
        return "reviewer"
    if schema["required"] == ["verdict", "revised_rfc", "situation_read"]:
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

    before = _commit_count(tmp_path)
    result = review.run_rfc_review(tmp_path)

    assert result.status == "direction-ok"
    assert result.rounds == 1
    assert result.final_view == _rfc_view()
    assert result.resolved == [dim.key for dim in review.DIMENSIONS]
    assert result.unresolved == []
    assert len(calls) == 5
    assert all(call["repo"] == tmp_path for call in calls)
    assert _commit_count(tmp_path) == before + 1
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

    result = review.run_rfc_review(tmp_path)

    assert result.status == "direction-ok"
    assert result.rounds == 2
    assert result.final_view == revised
    assert json.loads((tmp_path / "rfc.json").read_text(encoding="utf-8")) == revised
    assert _latest_commit_message(tmp_path).startswith("rfc: direction-ok (2 rounds)")
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

    result = review.run_rfc_review(tmp_path)

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

    result = review.run_rfc_review(tmp_path)

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

    result = review.run_rfc_review(tmp_path)

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


def test_missing_rfc_at_head_fail_closed_nak(tmp_path):
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True, text=True)
    _git(tmp_path, "config", "user.name", "RFC Test")
    _git(tmp_path, "config", "user.email", "rfc-test@example.invalid")
    (tmp_path / "README.md").write_text("empty\n", encoding="utf-8")
    _git(tmp_path, "add", "README.md")
    _git(tmp_path, "commit", "-m", "initial")

    result = review.run_rfc_review(tmp_path)

    assert result.status == "nak"
    assert result.rounds == 0
    assert [objection.dimension for objection in result.unresolved] == ["rfc-read"]
    message = _latest_commit_message(tmp_path)
    assert message.startswith("rfc: nak (0 rounds)")
    assert "unresolved: rfc-read" in message
