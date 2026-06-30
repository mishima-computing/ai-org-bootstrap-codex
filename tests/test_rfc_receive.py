from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from ai_org.rfc import receive as receive_module
from ai_org.rfc.receive import (
    COMMON_8_FIELDS,
    GROUNDING_SCHEMA,
    REQUEST_SCHEMA,
    GroundingResult,
    intake,
    produce_rfc,
    receive,
)


def test_receive_validates_full_common_8_request_from_dict():
    request = {
        "title": "Manual intake",
        "problem": "Requests need a real entrance form.",
        "proposal": "Validate common-8 data before RFC formation.",
        "alternatives": ["Keep loading RFCs directly."],
        "intended_users": "Contributors opening a request.",
        "affected_area": "ai_org.rfc",
        "impact": "RFC formation starts from request data.",
        "context": "See the receive gate comments.",
    }

    assert receive(request) == request
    assert tuple(REQUEST_SCHEMA["recognized_fields"]) == COMMON_8_FIELDS
    assert REQUEST_SCHEMA["required"] == ["title", "problem"]


def test_receive_validates_request_from_json_file(tmp_path):
    path = tmp_path / "request.json"
    request = {
        "title": "JSON intake",
        "problem": "The raw request is stored on disk.",
        "proposal": "Load JSON into a validated request dict.",
        "alternatives": [],
        "intended_users": "Request authors.",
        "affected_area": "receive",
        "impact": "Callers get plain data.",
        "context": "request.json",
    }
    path.write_text(
        json.dumps(request),
        encoding="utf-8",
    )

    assert receive(path) == request


@pytest.mark.parametrize(
    ("request_data", "missing_field"),
    [
        ({"problem": "A title is required."}, "title"),
        ({"title": "No problem"}, "problem"),
        ({"title": "", "problem": "A title is required."}, "title"),
        ({"title": "No problem", "problem": "   "}, "problem"),
    ],
)
def test_receive_missing_title_or_problem_raises_clear_error(request_data, missing_field):
    with pytest.raises(ValueError, match=f"{missing_field!r} is required"):
        receive(request_data)


def test_receive_defaults_optional_fields_sanely():
    assert receive({"title": "Minimal", "problem": "Required fields only."}) == {
        "title": "Minimal",
        "problem": "Required fields only.",
        "proposal": "",
        "alternatives": [],
        "intended_users": "",
        "affected_area": "",
        "impact": "",
        "context": "",
    }


def test_receive_preserves_extra_keys():
    assert receive(
        {
            "title": "Extra data",
            "problem": "Unknown keys should not be rejected.",
            "custom_priority": "high",
        }
    )["custom_priority"] == "high"


def test_produce_rfc_writes_common_8_to_rfc_branch_from_default_branch(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    request = receive(
        {
            "title": "Manual Intake",
            "problem": "Requests need a real entrance form.",
            "proposal": "Commit the validated COMMON-8 as rfc.json.",
            "alternatives": ["Keep loading RFCs directly."],
            "intended_users": "Contributors opening a request.",
            "affected_area": "ai_org.rfc",
            "impact": "RFC formation starts from request data.",
            "context": "request.json",
            "custom_priority": "high",
        }
    )

    monkeypatch.setattr(receive_module, "_ground_request", lambda repo, rfc: GroundingResult(rfc, "identity"))

    result = produce_rfc(request, repo)

    assert result["ok"] is True
    assert result["status"] == "promoted"
    assert result["id"] == "manual-intake"
    assert result["branch"] == "ai-org/rfc/manual-intake"
    assert result["commit"] == _git(repo, "rev-parse", "refs/heads/ai-org/rfc/manual-intake")
    assert _git(repo, "rev-parse", "HEAD") == _git(repo, "rev-parse", "refs/heads/main")
    assert _git(repo, "show", "main:README.md") == "base"
    produced = json.loads(_git(repo, "show", "ai-org/rfc/manual-intake:rfc.json"))
    assert produced == {field: request[field] for field in COMMON_8_FIELDS}
    assert "custom_priority" not in produced


def test_intake_grounding_confident_writes_grounded_branch(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    request = receive(
        {
            "title": "Game Like Kumo",
            "problem": "Make a maze arcade game like kumo.",
            "proposal": "Build a spider labyrinth.",
        }
    )
    grounded = {
        "title": "Auto-Battle Party Dungeon RPG",
        "problem": "A rough request for a game like kumo needs the correct auto-battle dungeon RPG grounding.",
        "proposal": "Build an auto-battle party dungeon RPG loop with party setup, dungeon runs, loot, and progression.",
        "alternatives": ["Build a maze arcade game, but that is the wrong genre for the reference."],
        "intended_users": "Players who want idle party-building dungeon RPG play.",
        "affected_area": "game",
        "impact": "The RFC targets the correct genre and mechanics before implementation starts.",
        "context": "Grounding corrected kumo from a maze arcade assumption to an auto-battle dungeon RPG reference.",
    }
    notes = "Found kumo is an auto-battle party dungeon RPG; corrected wrong maze-arcade framing."

    def handler(cmd):
        assert cmd[:4] == ["codex", "exec", "--sandbox", "read-only"]
        assert cmd[cmd.index("-C") + 1] == str(repo.resolve())
        assert cmd[cmd.index("--enable") + 1] == "web_search"
        assert _schema_kind(cmd[cmd.index("--output-schema") + 1]) == "grounding"
        _assert_prompt_preserves_named_thing_specificity(cmd[-1])
        return {
            "confident": True,
            "proposed_rfc": grounded,
            "assumptions": [],
            "questions": [],
            "grounding_notes": notes,
        }

    _install_codex_fake(monkeypatch, handler)

    result = intake(request, repo)

    assert result["status"] == "promoted"
    assert result["id"] == "auto-battle-party-dungeon-rpg"
    assert result["branch"] == "ai-org/rfc/auto-battle-party-dungeon-rpg"
    assert result["grounding_notes"] == notes
    assert json.loads(_git(repo, "show", "ai-org/rfc/auto-battle-party-dungeon-rpg:rfc.json")) == grounded
    assert _git(repo, "rev-parse", "HEAD") == _git(repo, "rev-parse", "refs/heads/main")


def test_intake_grounding_not_confident_returns_proposed_rfc_without_branch(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    request = receive(
        {
            "title": "Rough Game",
            "problem": "Make it like that thing we discussed.",
        }
    )
    proposed_rfc = {
        "title": "Conversation-Inferred Dungeon Automation Game",
        "problem": "The requester likely wants the previously discussed automation game, but the exact reference is not fully recoverable from the request alone.",
        "proposal": "Build a small dungeon automation loop with party setup, automated runs, rewards, and progression.",
        "alternatives": ["Wait for a named reference before shaping the RFC."],
        "intended_users": "Players who want a lightweight automated dungeon progression game.",
        "affected_area": "game",
        "impact": "The RFC gives the requester a concrete interpretation to confirm or correct before branch creation.",
        "context": "Grounding inferred a likely game request from the available wording and repository game context.",
    }
    assumptions = [
        "I assumed 'that thing we discussed' refers to a dungeon automation game because the repository context points at game work and the request asks for a rough game.",
        "I assumed the first RFC should cover core loop and progression rather than art polish because the problem does not name a visual style.",
    ]
    questions = ["Can you name the exact prior reference if this inferred game is wrong?"]

    def handler(cmd):
        assert cmd[:4] == ["codex", "exec", "--sandbox", "read-only"]
        return {
            "confident": False,
            "proposed_rfc": proposed_rfc,
            "assumptions": assumptions,
            "questions": questions,
            "grounding_notes": "The reference is ambiguous, but grounding inferred a likely RFC from repo context.",
        }

    _install_codex_fake(monkeypatch, handler)

    result = intake(request, repo)

    assert result["status"] == "needs_confirmation"
    assert result["proposed_rfc"] == proposed_rfc
    assert result["assumptions"] == assumptions
    assert result["questions"] == questions
    assert "branch" not in result
    missing_branch = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--verify", "refs/heads/ai-org/rfc/rough-game"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert missing_branch.returncode != 0
    assert _git(repo, "rev-parse", "HEAD") == _git(repo, "rev-parse", "refs/heads/main")


def test_grounding_schema_is_codex_valid_common_8():
    serialized = json.dumps(GROUNDING_SCHEMA)
    assert "allOf" not in serialized
    assert "anyOf" not in serialized
    assert "oneOf" not in serialized
    assert GROUNDING_SCHEMA["additionalProperties"] is False
    assert sorted(GROUNDING_SCHEMA["required"]) == sorted(GROUNDING_SCHEMA["properties"])

    schema_rfc = GROUNDING_SCHEMA["properties"]["proposed_rfc"]
    assert schema_rfc["additionalProperties"] is False
    assert tuple(schema_rfc["required"]) == COMMON_8_FIELDS
    assert sorted(schema_rfc["required"]) == sorted(schema_rfc["properties"])


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "base")
    _git(repo, "branch", "-M", "main")
    return repo


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return result.stdout.strip()


def _install_codex_fake(monkeypatch: pytest.MonkeyPatch, handler) -> None:
    real_run = receive_module.subprocess.run

    def fake_run(cmd, *args, **kwargs):
        if cmd and cmd[0] == "codex":
            out_file = Path(cmd[cmd.index("-o") + 1])
            payload = handler(cmd)
            out_file.write_text(json.dumps(payload), encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(receive_module.subprocess, "run", fake_run)


def _schema_kind(output_schema: str | Path) -> str:
    schema = json.loads(Path(output_schema).read_text(encoding="utf-8"))
    if schema == GROUNDING_SCHEMA:
        return "grounding"
    raise AssertionError(f"unexpected schema: {schema}")


def _assert_prompt_preserves_named_thing_specificity(prompt: str) -> None:
    assert "Faithfully render the request's specific identity" in prompt
    assert "concrete defining signatures" in prompt
    assert "ground down to that named thing" in prompt
    assert "never generalize up to a broad category" in prompt
    assert "faithfully reproduce <the specific named thing>" in prompt
    assert "generic genre entry" in prompt
    assert "Preserve the request's full scope" in prompt
    assert "vertical slice" in prompt
    assert "prototype, MVP, first iteration" in prompt
    assert "complete requested deliverable" in prompt
    assert "Grounding is not legal review" in prompt
    assert "Do not perform IP, trademark, copyright, or licensing risk analysis" in prompt
    assert "do not add legal disclaimers" in prompt
    assert "Do not avoid perceived IP risk by renaming, generalizing, or shrinking" in prompt
