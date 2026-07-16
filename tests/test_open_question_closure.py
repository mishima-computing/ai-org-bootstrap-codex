"""Brief 28: open-question closure — closure is judgment, retirement is code.

Canon (open_question_lifecycle_canon 2026-07-05): W3C formal-address/transition
gate, Rust tracking-issue closure, and the mature-doc-drops-the-question rule.
Live evidence: three consecutive round-6 NAKs because problem.open_questions was
append-only — the author DECIDED skip/reset while the open question 「両方入れる
か」 could never retire, so the reviewer correctly flagged the normative-vs-open
contradiction every round until cap-NAK.
"""
from __future__ import annotations

import json
from typing import Any

from ai_org.patchwork_queue import patch_series_gate
from ai_org.patchwork_queue import receive as receive_module
from ai_org.patchwork_queue import review
from ai_org.patchwork_queue.receive import GroundingResult
from ai_org import contributor_handoff, review_bodies

import test_patch_series_receive as receive_harness
import test_patch_series_review as review_side_harness

THIRD_RUN_QUESTION = "Should the first public CLI contract include both skip and reset commands?"


def _plan_with_question() -> dict[str, Any]:
    return {
        "step": "constraints",
        "target_ids": ["constraint:hard:1"],
        "open_questions": [THIRD_RUN_QUESTION, "Is a config file needed at all?"],
        "objections": [
            {
                "objection_id": "approach:contradiction",
                "claim": "The decided interface conflicts with the standing open question.",
                "requested_author_action": "revise_subtree",
                "anchor_node_ids": ["constraint:hard:1"],
                "axis": "approach",
                "resolution_authority": "author",
            }
        ],
        "neighborhood_ids": [],
        "manifest_ids": [],
        "target_bodies": {},
        "neighborhood_bodies": {},
        "out_of_step_anchor_bodies": {},
    }


def _closure(kind: str = "settled_on_paper", check: str = "") -> dict[str, str]:
    return {
        "question": THIRD_RUN_QUESTION,
        "resolution": "Decided: the interface requires both skip and reset; the question is settled.",
        "closure_kind": kind,
        "executable_check": check,
    }


def test_closed_questions_schema_is_enum_closed_over_the_open_questions():
    schema = receive_module._targeted_revision_schema("constraints", _plan_with_question())
    closures = schema["properties"]["closed_questions"]["items"]
    assert set(closures["required"]) == {"question", "resolution", "closure_kind", "executable_check"}
    assert closures["properties"]["closure_kind"]["enum"] == ["settled_on_paper", "routed_to_patch"]
    assert THIRD_RUN_QUESTION in closures["properties"]["question"]["enum"]
    assert "closed_questions" in schema["required"]
    prompt = receive_module._targeted_revision_prompt("constraints", _plan_with_question())
    assert "closed_questions" in prompt
    assert THIRD_RUN_QUESTION in prompt
    assert "contradiction reviewers must flag" in prompt


def test_closure_parsing_byte_matches_and_cross_checks_routing():
    plan = _plan_with_question()
    assert receive_module._parse_closed_questions([_closure()], plan) == [_closure()]
    routed = _closure("routed_to_patch", "sh probe_skip_reset.sh")
    assert receive_module._parse_closed_questions([routed], plan) == [routed]

    # Unknown/paraphrased string: typed reject; feedback names candidates verbatim.
    paraphrased = {**_closure(), "question": "Should skip and reset both exist?"}
    rejected = receive_module._parse_closed_questions([paraphrased], plan)
    assert rejected["ok"] is False
    assert THIRD_RUN_QUESTION in rejected["error"]
    assert "Is a config file needed at all?" in rejected["error"]

    # Cross-checks both ways.
    no_check = receive_module._parse_closed_questions([_closure("routed_to_patch", "")], plan)
    assert no_check["ok"] is False and "requires a non-empty executable_check" in no_check["error"]
    stray_check = receive_module._parse_closed_questions([_closure("settled_on_paper", "sh x.sh")], plan)
    assert stray_check["ok"] is False and "must not carry an executable_check" in stray_check["error"]
    duplicate = receive_module._parse_closed_questions([_closure(), _closure()], plan)
    assert duplicate["ok"] is False
    missing_resolution = receive_module._parse_closed_questions([{**_closure(), "resolution": " "}], plan)
    assert missing_resolution["ok"] is False and "missing a resolution" in missing_resolution["error"]


def _closure_repo(tmp_path):
    repo = receive_harness._init_repo(tmp_path)
    series_branch = "ai-org/patch-series/reviewable-patch_series"
    tree = receive_harness._reform_approach_tree("old slice")
    tree["problem"]["open_questions"] = [THIRD_RUN_QUESTION, "Is a config file needed at all?"]
    tree["problem"]["constraints"]["hard"] = [
        {"id": f"constraint:hard:{index}", "statement": statement,
         "derivation": {"from": "repo", "trace": "Repo evidence."},
         "implication": {"must": "Honor.", "must_not": "Ignore."}}
        for index, statement in (
            (1, "The CLI exposes skip and reset commands."),
            (2, "Keep the CLI dependency-free."),
            (3, "Verify headlessly."),
        )
    ]
    view = receive_harness._patch_series_view("Reviewable patch series")
    # Assembly-normalize the committed fixture (the brief 25 lesson): reform
    # round-trips then reproduce untouched nodes byte-identically.
    tree = receive_module._assemble_from_components(receive_module._approach_components(tree))
    receive_harness._git(repo, "checkout", "-B", series_branch, "main")
    (repo / "patch-series-cover-letter.json").write_text(json.dumps(view) + "\n", encoding="utf-8")
    (repo / "technical-approach-plan.json").write_text(json.dumps(tree) + "\n", encoding="utf-8")
    receive_harness._git(repo, "add", "-A")
    receive_harness._git(repo, "commit", "-m", "initial patch_series")
    receive_harness._git(repo, "commit", "--allow-empty", "-m", "patch_series: needs-revision round 1")
    receive_harness._git(repo, "checkout", "main")
    objection = receive_harness._blocking_objection("constraint:hard:1")
    objection["objection_id"] = "approach:contradiction"
    objection["claim"] = "The decided interface conflicts with the standing open question."
    receive_harness._write_review_round(repo, "reviewable-patch_series", objection)
    return repo, series_branch


def test_settled_closure_retires_the_question_and_records_the_disposition(tmp_path, monkeypatch):
    receive_harness._install_historical_reform_preparation(monkeypatch)
    # The exact third-run shape: the interface is decided (constraint states
    # skip AND reset) while the stale open question stands. The closure settles
    # it: the question leaves the living tree, the disposition lives in the
    # ledger, and the contradiction objection class cannot recur.
    repo, series_branch = _closure_repo(tmp_path)
    codex_calls: list[dict[str, Any]] = []

    def fake_run_json(repo_path, **kwargs):
        codex_calls.append(kwargs)
        return {"ok": True, "raw": json.dumps({
            "hard_constraints": [{
                "id": "constraint:hard:1",
                "statement": "The CLI exposes BOTH skip and reset commands (decided; question settled).",
                "derivation": {"from": "repo", "trace": "Interface decision."},
                "implication": {"must": "Ship both commands.", "must_not": "Reopen the question."},
            }],
            "soft_preferences": [],
            "deferral_proposals": [],
            "closed_questions": [_closure()],
        })}

    monkeypatch.setattr(receive_module.codex_exec, "run_json", fake_run_json)

    result = receive_module.reform_patch_series(repo, "reviewable-patch_series")

    assert result["status"] == "reformed"
    revised_tree = json.loads(receive_harness._git(repo, "show", f"{series_branch}:technical-approach-plan.json"))
    # Mature docs DROP the question — no inline resolved-Q&A.
    assert THIRD_RUN_QUESTION not in revised_tree["problem"]["open_questions"]
    assert "Is a config file needed at all?" in revised_tree["problem"]["open_questions"]
    assert THIRD_RUN_QUESTION not in json.dumps(revised_tree)
    # The disposition record is complete in the public ledger.
    ledger = review_bodies.read_record(repo, series_branch, result["revision_delta_path"], review_bodies.REVISION_DELTA_RECIPE)
    [record] = ledger["closed_questions"]
    assert record == {
        "question": THIRD_RUN_QUESTION,
        "resolution": "Decided: the interface requires both skip and reset; the question is settled.",
        "closure_kind": "settled_on_paper",
        "executable_check": "",
        "step": "constraints",
        "decided_after_review_round": 1,
    }
    # Next round's delta scope carries the changed node; the reviewer can object
    # to a bad closure like any other change (group ratification, no new judge).
    scope = review._delta_review_scope(revised_tree, ledger)
    assert "constraint:hard:1" in scope["changed_nodes"]


def test_routed_to_patch_closure_lands_in_the_obligation_artifact(tmp_path, monkeypatch):
    receive_harness._install_historical_reform_preparation(monkeypatch)
    repo, series_branch = _closure_repo(tmp_path)
    codex_calls: list[dict[str, Any]] = []

    def fake_run_json(repo_path, **kwargs):
        codex_calls.append(kwargs)
        return {"ok": True, "raw": json.dumps({
            "hard_constraints": [{
                "id": "constraint:hard:1",
                "statement": "The CLI ships skip and reset behind the probe's verdict.",
                "derivation": {"from": "repo", "trace": "Routed to the patch."},
                "implication": {"must": "Run the probe.", "must_not": "Decide blind."},
            }],
            "soft_preferences": [],
            "deferral_proposals": [],
            "closed_questions": [_closure("routed_to_patch", "sh probe_skip_reset.sh")],
        })}

    monkeypatch.setattr(receive_module.codex_exec, "run_json", fake_run_json)

    result = receive_module.reform_patch_series(repo, "reviewable-patch_series")

    assert result["status"] == "reformed"
    revised_tree = json.loads(receive_harness._git(repo, "show", f"{series_branch}:technical-approach-plan.json"))
    assert THIRD_RUN_QUESTION not in json.dumps(revised_tree["problem"]["open_questions"])
    # The question-shaped must-answer record reuses the brief 27 artifact,
    # obligation-form, executable_check required.
    artifact = contributor_handoff.parse_questions(
        receive_harness._git(repo, "show", f"{series_branch}:{patch_series_gate.PATCH_MUST_ANSWER_QUESTIONS_PATH}") + "\n"
    )
    [entry] = artifact["questions_the_patch_must_answer"]
    assert entry["objection_id"].startswith("open-question:")
    assert entry["status"] == "must_resolve_in_patch"
    assert entry["executable_check"] == "sh probe_skip_reset.sh"
    assert entry["claim"] == THIRD_RUN_QUESTION
    assert "defer" not in json.dumps(sorted(entry.keys()))
    ledger = review_bodies.read_record(repo, series_branch, result["revision_delta_path"], review_bodies.REVISION_DELTA_RECIPE)
    assert ledger["closed_questions"][0]["closure_kind"] == "routed_to_patch"


def test_closure_only_revision_keeps_the_disposition_visible_next_round(tmp_path, monkeypatch):
    receive_harness._install_historical_reform_preparation(monkeypatch)
    # A closure with no node fragments: the problem node's own field changed, so
    # it keeps its delta-scope seat (ancestor-drop applies only when a changed
    # DESCENDANT carries the delta) and the reviewer sees the retirement.
    repo, series_branch = _closure_repo(tmp_path)

    def fake_run_json(repo_path, **kwargs):
        return {"ok": True, "raw": json.dumps({
            "hard_constraints": [],
            "soft_preferences": [],
            "deferral_proposals": [],
            "closed_questions": [_closure()],
        })}

    monkeypatch.setattr(receive_module.codex_exec, "run_json", fake_run_json)

    result = receive_module.reform_patch_series(repo, "reviewable-patch_series")

    assert result["status"] == "reformed"
    revised_tree = json.loads(receive_harness._git(repo, "show", f"{series_branch}:technical-approach-plan.json"))
    ledger = review_bodies.read_record(repo, series_branch, result["revision_delta_path"], review_bodies.REVISION_DELTA_RECIPE)
    assert ledger["changed_node_ids"] == ["problem"]
    scope = review._delta_review_scope(revised_tree, ledger)
    assert "problem" in scope["changed_nodes"]
    assert THIRD_RUN_QUESTION not in json.dumps(scope["changed_nodes"])


def test_grounding_intake_append_side_is_untouched():
    # The autonomy canon stands at intake: non-blocking recording appends.
    grounding = GroundingResult(
        {"open_questions": ["Existing question."], "constraints_assumptions": []},
        "notes",
        False,
        ["I assumed X."],
        ["New uncertainty?"],
    )
    patched = receive_module._patch_series_with_non_blocking_grounding_uncertainty(grounding)
    assert patched["open_questions"] == ["Existing question.", "New uncertainty?"]
