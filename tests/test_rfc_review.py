from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from ai_org.rfc import review


RFC_ID = "manual-rfc"
RFC_BRANCH = f"ai-org/rfc/{RFC_ID}"
ORIGINAL_GROUND_REQUEST = review._ground_request


def _rfc_view() -> dict[str, object]:
    return {
        "title": "Manual RFC",
        "problem": "The workflow needs real RFC review.",
        "proposal": "Run five dimension reviewers and consolidate objections.",
        "alternatives": ["Keep loading RFCs directly."],
        "intended_users": "Contributors taking RFC work.",
        "affected_area": "ai_org.rfc",
        "impact": "RFC direction converges before patch work starts.",
        "context": "Keep the target repo read-only during review.",
    }


def _revised_rfc(suffix: str = "") -> dict[str, object]:
    return {
        "title": f"Revised Manual RFC{suffix}",
        "problem": f"The RFC review workflow needs structured convergence{suffix}.",
        "proposal": f"Run five reviewers, then synthesize into a revised RFC{suffix}.",
        "alternatives": [f"Skip consolidation and accept reviewer drift{suffix}."],
        "intended_users": f"Contributors and maintainers{suffix}.",
        "affected_area": "ai_org.rfc",
        "impact": f"Review state stays schema-backed{suffix}.",
        "context": f"Keep all codex calls read-only and schema-backed{suffix}.",
    }


def _grounded_rfc() -> dict[str, object]:
    return {
        "title": "Auto-Battle Party Dungeon RPG",
        "problem": "A rough request for a game like kumo needs the correct auto-battle dungeon RPG grounding.",
        "proposal": "Build an auto-battle party dungeon RPG loop with party setup, dungeon runs, loot, and progression.",
        "alternatives": ["Build a maze arcade game, but that is the wrong genre for the reference."],
        "intended_users": "Players who want idle party-building dungeon RPG play.",
        "affected_area": "game",
        "impact": "The RFC targets the correct genre and mechanics before implementation starts.",
        "context": "Grounding corrected kumo from a maze arcade assumption to an auto-battle dungeon RPG reference.",
    }


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
    if schema == review.GROUNDING_SCHEMA:
        return "grounding"
    raise AssertionError(f"unexpected schema: {schema}")


@pytest.fixture(autouse=True)
def _identity_grounding(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        review,
        "_ground_request",
        lambda repo, rfc_view: review.GroundingResult(rfc_view, ""),
    )


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
            if field == "alternatives":
                assert value[0] in prompt
            else:
                assert value in prompt
    assert result.history[0]["aufheben"]["verdict"] == "proceed"
    assert result.history[0]["aufheben"]["situation_read"]


def test_grounded_request_feeds_review_loop_and_direction_ok_commit(tmp_path, monkeypatch):
    _init_repo(
        tmp_path,
        {
            **_rfc_view(),
            "title": "Game Like Kumo",
            "problem": "Make a maze arcade game like kumo.",
            "proposal": "Build a spider labyrinth.",
        },
    )
    grounded = _grounded_rfc()
    notes = "Found kumo is an auto-battle party dungeon RPG; corrected wrong maze-arcade framing."
    reviewer_prompts = []

    def fake_ground_request(repo, rfc_view):
        assert repo == tmp_path
        assert rfc_view["title"] == "Game Like Kumo"
        return review.GroundingResult(grounded, notes)

    def handler(repo, prompt, output_schema):
        assert _schema_kind(output_schema) == "reviewer"
        reviewer_prompts.append(prompt)
        return json.dumps({"has_objection": False, "detail": "Grounded RFC is coherent."}), 0

    monkeypatch.setattr(review, "_ground_request", fake_ground_request)
    _install_codex_fake(monkeypatch, handler)

    result = review.run_rfc_review(tmp_path, RFC_ID)

    assert result.status == "direction-ok"
    assert result.final_view == grounded
    assert result.grounding_notes == notes
    assert json.loads(_git(tmp_path, "show", f"{RFC_BRANCH}:rfc.json").stdout) == grounded
    assert "auto-battle party dungeon RPG" in _latest_commit_message(tmp_path)
    assert len(reviewer_prompts) == len(review.DIMENSIONS)
    for prompt in reviewer_prompts:
        assert "Auto-Battle Party Dungeon RPG" in prompt
        assert "spider labyrinth" not in prompt


def test_ground_request_uses_read_only_web_search_and_schema(tmp_path, monkeypatch):
    grounded = _grounded_rfc()
    notes = "Grounded with web research."

    def fake_run(cmd, *args, **kwargs):
        assert cmd[:4] == ["codex", "exec", "--sandbox", "read-only"]
        assert cmd[cmd.index("-C") + 1] == str(tmp_path)
        assert cmd[cmd.index("--enable") + 1] == "web_search"
        assert _schema_kind(cmd[cmd.index("--output-schema") + 1]) == "grounding"
        out_file = Path(cmd[cmd.index("-o") + 1])
        out_file.write_text(
            json.dumps({"grounded_rfc": grounded, "grounding_notes": notes}),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(review, "_ground_request", ORIGINAL_GROUND_REQUEST)
    monkeypatch.setattr(review.subprocess, "run", fake_run)

    result = review._ground_request(tmp_path, _rfc_view())

    assert result.rfc_view == grounded
    assert result.grounding_notes == notes


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


@pytest.mark.parametrize(
    ("schema_name", "schema"),
    [
        ("aufheben", review.AUFHEBEN_SCHEMA),
        ("grounding", review.GROUNDING_SCHEMA),
    ],
)
def test_rfc_schemas_use_common_8_and_codex_valid_required_properties(schema_name, schema):
    serialized = json.dumps(schema)
    assert "allOf" not in serialized
    assert "anyOf" not in serialized
    assert "oneOf" not in serialized
    assert schema["additionalProperties"] is False
    assert sorted(schema["required"]) == sorted(schema["properties"])

    rfc_key = "revised_rfc" if schema_name == "aufheben" else "grounded_rfc"
    schema_rfc = schema["properties"][rfc_key]
    assert schema_rfc["additionalProperties"] is False
    assert tuple(schema_rfc["required"]) == review.RFC_VIEW_FIELDS
    assert sorted(schema_rfc["required"]) == sorted(schema_rfc["properties"])
    assert schema_rfc["properties"]["alternatives"]["type"] == "array"


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
